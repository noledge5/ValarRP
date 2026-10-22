"""Modell-Client (Schritt 2a, Entscheidungen 4, 5, 67, 142 und 153 bis 157).

Spricht das OpenAI-Format (Chat Completions) mit einer Kette von Anbietern: Ist das Kontingent eines Anbieters
erschöpft (HTTP 429), kommt der nächste dran. Jede Antwort wird gegen ein Pydantic-Schema geprüft; ist sie
ungültig, gibt es genau einen Wiederholungsversuch mit dem Fehler als Hinweis, dann einen Fehler. Kosten werden
je Aufruf protokolliert; ist das Monatsbudget erreicht, ruft der Client nichts mehr auf.

Der Client fasst den Spielstand nie an. Schlüssel kommen nur aus der Umgebung und stehen nie in Datenbank,
Einstellungen oder Fehlermeldungen. Tests laufen ohne Netz gegen eine Attrappe (`Transport`).
"""

import json
import math
import os
import re
import sqlite3
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, TypeVar
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from valar import apply

T = TypeVar("T")

MAX_RESPONSE_BYTES = 1_000_000
CHARS_PER_TOKEN = 4  # Schätzung, wenn ein Anbieter keine Token-Zahlen meldet (wie im Kontextbauer)
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

OutputMode = Literal["json_schema", "json_object", "none"]
Outcome = Literal["ok", "invalid", "quota", "error"]
ErrorCode = Literal["budget", "no_provider", "invalid_reply", "network", "provider_error"]


# --- Einstellungen (Datei, ohne Schlüssel) ---


class ProviderSettings(BaseModel):
    """Ein Anbieter mit einem Modell. Preise in Euro je Million Tokens; 0 heißt kostenlos."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=40)
    base_url: str
    model: str = Field(min_length=1)
    key_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")  # Name der Umgebungsvariable, nie der Schlüssel selbst
    input_eur_per_million: float = Field(default=0, ge=0)
    output_eur_per_million: float = Field(default=0, ge=0)
    output: OutputMode = "json_schema"  # wie der Anbieter JSON erzwingt
    timeout_s: float = Field(default=30, gt=0, le=300)

    @field_validator("base_url")
    @classmethod
    def _https_only(cls, url: str) -> str:
        """Schlüssel gehen nur verschlüsselt hinaus; unverschlüsselt nur an den eigenen Rechner (Ollama)."""
        parts = urlsplit(url)
        local = parts.scheme == "http" and parts.hostname in LOCAL_HOSTS
        if not (parts.scheme == "https" or local) or not parts.hostname or parts.query or parts.fragment:
            raise ValueError("Adresse muss https:// sein (http:// nur für localhost), ohne ?-Teil")
        return url.rstrip("/")


class BudgetSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    warn_eur: float = Field(default=10, ge=0)
    stop_eur: float = Field(default=20, gt=0)

    @model_validator(mode="after")
    def _order(self) -> "BudgetSettings":
        if self.warn_eur > self.stop_eur:
            raise ValueError("Die Warnung muss vor dem Stopp kommen")
        return self


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    budget: BudgetSettings = BudgetSettings()
    provider: tuple[ProviderSettings, ...] = Field(min_length=1)  # in dieser Reihenfolge versucht

    @model_validator(mode="after")
    def _unique(self) -> "ModelSettings":
        names = [p.name for p in self.provider]
        if len(set(names)) != len(names):
            raise ValueError("Jeder Anbieter braucht einen eigenen Namen")
        return self


def load_settings(path: str | Path) -> ModelSettings:
    """Liest die Einstellungen aus einer TOML-Datei (Vorlage: modelle.example.toml)."""
    with open(path, "rb") as file:
        return ModelSettings.model_validate(tomllib.load(file))


def cost_micro_eur(provider: ProviderSettings, input_tokens: int, output_tokens: int) -> int:
    """Kosten in Millionstel Euro, aufgerundet: Euro je Million Tokens mal Tokens."""
    return math.ceil(input_tokens * provider.input_eur_per_million + output_tokens * provider.output_eur_per_million)


# --- Transport: der einzige Weg ins Netz ---


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes


class TransportError(Exception):
    """Zeitüberschreitung oder Netzfehler, ohne Antwort."""


class Transport(Protocol):
    def post(self, url: str, headers: Mapping[str, str], body: bytes, timeout: float) -> HttpResponse: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Weiterleitungen werden nicht befolgt: Sonst ginge der Schlüssel im Kopf an eine andere Adresse."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class UrllibTransport:
    """HTTP mit der Standardbibliothek. Folgt keiner Weiterleitung, liest höchstens MAX_RESPONSE_BYTES."""

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(_NoRedirect)

    def post(self, url: str, headers: Mapping[str, str], body: bytes, timeout: float) -> HttpResponse:
        request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
        try:
            with self._opener.open(request, timeout=timeout) as response:  # Adresse in ProviderSettings geprüft
                return HttpResponse(response.status, response.read(MAX_RESPONSE_BYTES))
        except urllib.error.HTTPError as error:
            return HttpResponse(error.code, error.read(MAX_RESPONSE_BYTES))
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise TransportError(type(error).__name__) from None


# --- Kostenprotokoll ---


class Ledger:
    """Kosten je Aufruf in der Tabelle `model_call`, jeder Eintrag in eigener Transaktion."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def spent_micro_eur(self, month: str) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(cost_micro_eur), 0) FROM model_call WHERE month = ?", (month,)
        ).fetchone()
        return row[0]

    def record(self, *, month: str, provider: ProviderSettings, input_tokens: int, output_tokens: int, outcome: Outcome) -> int:
        cost = cost_micro_eur(provider, input_tokens, output_tokens)
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            apply.record_model_call(
                self.conn,
                month=month,
                provider=provider.name,
                model=provider.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_micro_eur=cost,
                outcome=outcome,
            )
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        return cost


# --- Client ---


class ModelError(RuntimeError):
    """Kein gültiges Ergebnis. Der Spielstand ist unberührt. Die Meldung enthält nie einen Schlüssel."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Reply[V]:
    value: V
    provider: str
    model: str
    attempts: int  # Aufrufe beim antwortenden Anbieter, 1 oder 2
    budget_warning: bool  # Warnschwelle des Monats erreicht


@dataclass(frozen=True)
class _Answer:
    """Eine Antwort des Anbieters, noch ungeprüft."""

    text: str | None
    input_tokens: int
    output_tokens: int


class _Quota(Exception):
    pass


class ModelClient:
    def __init__(
        self,
        settings: ModelSettings,
        ledger: Ledger,
        *,
        transport: Transport | None = None,
        environ: Mapping[str, str] = os.environ,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.settings = settings
        self.ledger = ledger
        self.transport = transport or UrllibTransport()
        self.environ = environ
        self.now = now

    def request(self, messages: list[dict[str, str]], adapter: TypeAdapter[T], *, schema_name: str = "antwort") -> Reply[T]:
        """Fragt die Anbieter der Reihe nach, bis einer eine gültige Antwort liefert oder alle erschöpft sind."""
        month = self.now().strftime("%Y-%m")
        usable = [(p, self.environ.get(p.key_env, "")) for p in self.settings.provider]
        usable = [(p, key) for p, key in usable if key]
        if not usable:
            raise ModelError("no_provider", "Kein Anbieter hat einen Schlüssel in der Umgebung.")
        for provider, key in usable:
            try:
                value, attempts = self._ask(provider, key, month, messages, adapter, schema_name)
            except _Quota:
                continue  # Kontingent erschöpft: der nächste Anbieter
            warn = self.ledger.spent_micro_eur(month) >= self.settings.budget.warn_eur * 1_000_000
            return Reply(value, provider.name, provider.model, attempts, warn)
        raise ModelError("no_provider", "Alle Anbieter melden ein erschöpftes Kontingent.")

    def _ask(
        self,
        provider: ProviderSettings,
        key: str,
        month: str,
        messages: list[dict[str, str]],
        adapter: TypeAdapter[T],
        schema_name: str,
    ) -> tuple[T, int]:
        conversation = list(messages)
        for attempt in (1, 2):
            if self.ledger.spent_micro_eur(month) >= self.settings.budget.stop_eur * 1_000_000:
                raise ModelError("budget", f"Monatsbudget von {self.settings.budget.stop_eur:g} € erreicht.")
            answer = self._call(provider, key, month, conversation, adapter, schema_name)
            try:
                value = adapter.validate_json(_strip_fences(answer.text or ""))
            except ValidationError as error:
                self.ledger.record(
                    month=month,
                    provider=provider,
                    input_tokens=answer.input_tokens,
                    output_tokens=answer.output_tokens,
                    outcome="invalid",
                )
                if attempt == 2:
                    raise ModelError("invalid_reply", f"{provider.name}: auch die Wiederholung passt nicht zum Schema.") from None
                conversation += [
                    {"role": "assistant", "content": answer.text or ""},
                    {"role": "user", "content": _correction(error)},
                ]
                continue
            self.ledger.record(
                month=month,
                provider=provider,
                input_tokens=answer.input_tokens,
                output_tokens=answer.output_tokens,
                outcome="ok",
            )
            return value, attempt
        raise AssertionError("unerreichbar")

    def _call(
        self,
        provider: ProviderSettings,
        key: str,
        month: str,
        messages: list[dict[str, str]],
        adapter: TypeAdapter[T],
        schema_name: str,
    ) -> _Answer:
        body: dict = {"model": provider.model, "messages": messages}
        if provider.output == "json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": provider_schema(adapter.json_schema())},
            }
        elif provider.output == "json_object":
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        estimate = sum(len(m.get("content", "")) for m in messages) // CHARS_PER_TOKEN

        def failed(outcome: Outcome) -> None:
            self.ledger.record(month=month, provider=provider, input_tokens=0, output_tokens=0, outcome=outcome)

        try:
            response = self.transport.post(
                f"{provider.base_url}/chat/completions", headers, json.dumps(body).encode(), provider.timeout_s
            )
        except TransportError as error:
            failed("error")
            raise ModelError("network", f"{provider.name}: keine Antwort ({_redact(str(error), key)}).") from None
        if response.status == 429:
            failed("quota")
            raise _Quota()
        if response.status != 200:
            failed("error")
            raise ModelError("provider_error", f"{provider.name}: Fehler {response.status}.")
        try:
            data = json.loads(response.body)
            text = data["choices"][0]["message"].get("content")
            usage = data.get("usage") or {}
            input_tokens = int(usage.get("prompt_tokens", estimate))
            output_tokens = int(usage.get("completion_tokens", len(text or "") // CHARS_PER_TOKEN))
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            # Kein Chat-Format: wie eine ungültige Antwort, ohne Token-Zahlen; gezählt wird die Schätzung.
            return _Answer(None, estimate, 0)
        return _Answer(text if isinstance(text, str) else None, max(input_tokens, 0), max(output_tokens, 0))


_DROPPED_KEYS = {"$defs", "discriminator", "title", "default"}


def provider_schema(schema: dict) -> dict:
    """Das Schema in der Teilmenge, die alle Anbieter verstehen (Härtetest 2c): Verweise eingesetzt, `anyOf` statt
    `oneOf`, `enum` statt `const`, ohne Titel und Standardwerte, und jedes Feld Pflicht.

    Gemini wirft Unbekanntes sonst weg und liefert in Aufruf A nur {"action": {"type": "…"}}; bei DeepSeek drückte
    das Erzwingen die Antwort in den einfachsten Zweig. Pflicht heißt auch `type` mit Standardwert, damit der Anbieter
    die Aktion nennt; ein freies Feld bleibt über `null` im `anyOf` frei. Geprüft wird danach wie immer mit Pydantic.
    """
    defs = schema.get("$defs", {})

    def walk(node: object, seen: tuple[str, ...]) -> object:
        if isinstance(node, list):
            return [walk(item, seen) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            name = node["$ref"].rsplit("/", 1)[-1]
            if name in seen or name not in defs:
                raise ValueError(f"Schema mit Kreis oder unbekanntem Verweis: {name}")
            return walk(defs[name], seen + (name,))
        flat: dict = {}
        for key, value in node.items():
            if key in _DROPPED_KEYS:
                continue
            if key == "oneOf":
                flat["anyOf"] = walk(value, seen)
            elif key == "const":
                flat["enum"] = [value]
            else:
                flat[key] = walk(value, seen)
        if isinstance(flat.get("properties"), dict):
            flat["required"] = list(flat["properties"])
        return flat

    return walk(schema, ())


def _strip_fences(text: str) -> str:
    """Kleine Modelle setzen JSON gern in ```json … ```; der Inhalt zählt."""
    match = re.fullmatch(r"\s*```(?:json)?\s*(.*?)\s*```\s*", text, flags=re.DOTALL)
    return match.group(1) if match else text


def _correction(error: ValidationError) -> str:
    """Der Hinweis für den einen Wiederholungsversuch: was am JSON nicht passte, ohne die Eingabe zu wiederholen."""
    problems = "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'Antwort'}: {e['msg']}" for e in error.errors()[:5])
    return f"Die Antwort passt nicht zum Schema ({problems}). Antworte nur mit gültigem JSON nach dem Schema."


def _redact(text: str, key: str) -> str:
    return text.replace(key, "***") if key else text
