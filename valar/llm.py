"""Modell-Client (Schritt 2a, Entscheidungen 4, 5, 67, 142 und 153 bis 157).

Spricht das OpenAI-Format (Chat Completions) mit einer Kette von Anbietern: Ist das Kontingent eines Anbieters
erschöpft (HTTP 429) oder ist er überlastet (HTTP 503, Entscheidung 210), kommt der nächste dran. Jede Antwort wird gegen ein Pydantic-Schema geprüft; ist sie
ungültig, gibt es genau einen Wiederholungsversuch mit dem Fehler als Hinweis, dann einen Fehler. Kosten werden
je Aufruf protokolliert; ist das Monatsbudget erreicht, ruft der Client nichts mehr auf. Ein Protokoll (`CallLog`,
Entscheidung 200) bekommt je Aufruf Prompt, Antwort, Dauer und Fehler, ohne Schlüssel.

Der Client fasst den Spielstand nie an. Schlüssel kommen nur aus der Umgebung und stehen nie in Datenbank,
Einstellungen oder Fehlermeldungen. Tests laufen ohne Netz gegen eine Attrappe (`Transport`).
"""

import json
import math
import os
import re
import sqlite3
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
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
MAX_LOGGED_ERROR = 500  # so viel einer Fehlerantwort steht im Protokoll
MAX_LOGGED_RAW = 2000  # so viel einer Antwort ohne Chat-Format
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
    timeout_s: float = Field(default=60, gt=0, le=300)  # Gemini Flash brauchte im Härtetest bis 32 s je Aufruf

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

    def get(self, url: str, headers: Mapping[str, str], timeout: float) -> HttpResponse: ...


class CallLog(Protocol):
    """Nimmt je Aufruf auf, was geschah (`journal.CallRecorder`). Darf nie einen Schlüssel bekommen."""

    def record(self, **call) -> None: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Weiterleitungen werden nicht befolgt: Sonst ginge der Schlüssel im Kopf an eine andere Adresse."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class UrllibTransport:
    """HTTP mit der Standardbibliothek. Folgt keiner Weiterleitung, liest höchstens MAX_RESPONSE_BYTES.

    `timeout` gilt für den ganzen Aufruf, nicht nur je Lesen: OpenRouter schickt beim Warten Leerzeichen, dann griffe
    die Zeitgrenze von urllib nie (im Härtetest hing ein Aufruf über 15 Minuten). Nach Ablauf der Frist endet der
    Aufruf beim nächsten Lesen; ein einzelnes Lesen wartet höchstens `timeout`."""

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(_NoRedirect)

    def post(self, url: str, headers: Mapping[str, str], body: bytes, timeout: float) -> HttpResponse:
        return self._send(urllib.request.Request(url, data=body, headers=dict(headers), method="POST"), timeout)

    def get(self, url: str, headers: Mapping[str, str], timeout: float) -> HttpResponse:
        return self._send(urllib.request.Request(url, headers=dict(headers), method="GET"), timeout)

    def _send(self, request: urllib.request.Request, timeout: float) -> HttpResponse:
        deadline = time.monotonic() + timeout
        try:
            with self._opener.open(request, timeout=timeout) as response:  # Adresse in ProviderSettings geprüft
                return HttpResponse(response.status, _read_until(response, deadline))
        except urllib.error.HTTPError as error:
            return HttpResponse(error.code, error.read(MAX_RESPONSE_BYTES))
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise TransportError(type(error).__name__) from None


def _read_until(response, deadline: float) -> bytes:
    """Liest, was ankommt, bis zum Ende, zu MAX_RESPONSE_BYTES oder zur Frist; nach der Frist ein Fehler."""
    chunks: list[bytes] = []
    size = 0
    while size < MAX_RESPONSE_BYTES:
        if time.monotonic() > deadline:
            raise TimeoutError("Gesamtzeit überschritten")
        chunk = response.read1(min(65_536, MAX_RESPONSE_BYTES - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks)


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
    """Eine Antwort des Anbieters, noch ungeprüft. `log` schreibt sie ins Protokoll, sobald das Ergebnis feststeht."""

    text: str | None
    input_tokens: int
    output_tokens: int
    log: Callable[..., None]


class _Quota(Exception):
    """Dieser Anbieter kann gerade nicht: Kontingent erschöpft (429) oder überlastet (503). Der nächste ist dran."""


class ModelClient:
    def __init__(
        self,
        settings: ModelSettings,
        ledger: Ledger,
        *,
        transport: Transport | None = None,
        environ: Mapping[str, str] = os.environ,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        call_log: CallLog | None = None,
    ) -> None:
        self.settings = settings
        self.ledger = ledger
        self.transport = transport or UrllibTransport()
        self.environ = environ
        self.now = now
        self.call_log = call_log

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
                continue  # Kontingent erschöpft oder überlastet: der nächste Anbieter
            warn = self.ledger.spent_micro_eur(month) >= self.settings.budget.warn_eur * 1_000_000
            return Reply(value, provider.name, provider.model, attempts, warn)
        raise ModelError("no_provider", "Alle Anbieter melden ein erschöpftes Kontingent oder sind überlastet.")

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
            answer = self._call(provider, key, month, conversation, adapter, schema_name, attempt)
            try:
                value = adapter.validate_json(_strip_fences(answer.text or ""))
            except ValidationError as error:
                answer.log("invalid", error=_redact(_problems(error), key))
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
            answer.log("ok")
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
        attempt: int = 1,
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
        started = time.monotonic()

        def log(outcome: Outcome, *, status: int | None = None, error: str | None = None, reply: str | None = None,
                input_tokens: int = 0, output_tokens: int = 0) -> None:
            """Ins Protokoll, nie mit Schlüssel: Text wird bereinigt, von Fehlern der Anmeldung bleibt nur der Code."""
            if self.call_log is None:
                return
            self.call_log.record(
                purpose=schema_name,
                provider=provider.name,
                model=provider.model,
                attempt=attempt,
                duration_ms=round((time.monotonic() - started) * 1000),
                outcome=outcome,
                http_status=status,
                error=_redact(error, key) if error else None,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                messages=_redact(json.dumps(messages, ensure_ascii=False), key),
                reply=_redact(reply, key) if reply is not None else None,
            )

        def failed(outcome: Outcome, **details) -> None:
            log(outcome, **details)
            self.ledger.record(month=month, provider=provider, input_tokens=0, output_tokens=0, outcome=outcome)

        try:
            response = self.transport.post(
                f"{provider.base_url}/chat/completions", headers, json.dumps(body).encode(), provider.timeout_s
            )
        except TransportError as error:
            failed("error", error=f"keine Antwort ({error})")
            raise ModelError("network", f"{provider.name}: keine Antwort ({_redact(str(error), key)}).") from None
        if response.status == 429:
            failed("quota", status=429, error=_error_body(response))
            raise _Quota()
        if response.status == 503:  # überlastet; anders als die Zeitgrenze (Entscheidung 193) schnell erkannt
            failed("error", status=503, error=_error_body(response))
            raise _Quota()
        if response.status != 200:
            failed("error", status=response.status, error=_error_body(response))
            raise ModelError("provider_error", f"{provider.name}: Fehler {response.status}.")
        raw = response.body.decode("utf-8", errors="replace")
        try:
            data = json.loads(response.body)
            text = data["choices"][0]["message"].get("content")
            usage = data.get("usage") or {}
            input_tokens = int(usage.get("prompt_tokens", estimate))
            output_tokens = int(usage.get("completion_tokens", len(text or "") // CHARS_PER_TOKEN))
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            # Kein Chat-Format: wie eine ungültige Antwort, ohne Token-Zahlen; gezählt wird die Schätzung.
            return _Answer(None, estimate, 0, partial(log, status=200, reply=raw[:MAX_LOGGED_RAW], input_tokens=estimate))
        text = text if isinstance(text, str) else None
        input_tokens, output_tokens = max(input_tokens, 0), max(output_tokens, 0)
        return _Answer(
            text, input_tokens, output_tokens,
            partial(log, status=200, reply=text, input_tokens=input_tokens, output_tokens=output_tokens),
        )


# --- Ping: Ist der Anbieter erreichbar, gilt der Schlüssel, kennt er das Modell? (Entscheidung 212) ---

PING_TIMEOUT_S = 10


@dataclass(frozen=True)
class PingResult:
    ok: bool  # erreichbar, Schlüssel gilt, Modell in der Liste
    text: str  # für die Seite, ohne Antworttext des Anbieters
    status: int | None
    duration_ms: int


def ping(
    provider: ProviderSettings,
    environ: Mapping[str, str],
    *,
    transport: Transport | None = None,
    call_log: CallLog | None = None,
) -> PingResult:
    """Fragt die Modellliste ab (`GET …/models`): kostet nichts, verbraucht kein Kontingent, prüft Schlüssel und Modell.
    Eine Überlastung des Modells zeigt sie nicht; die steht beim Spielen im Protokoll."""
    key = environ.get(provider.key_env, "")
    if not key:
        return PingResult(False, "kein Schlüssel", None, 0)
    transport = transport or UrllibTransport()
    started = time.monotonic()
    status: int | None = None
    try:
        response = transport.get(f"{provider.base_url}/models", {"Authorization": f"Bearer {key}"}, PING_TIMEOUT_S)
        status = response.status
        result = _ping_text(provider, response)
        error = None if response.status == 200 else _error_body(response)
    except TransportError as problem:
        result, error = (False, f"nicht erreichbar ({problem})"), f"keine Antwort ({problem})"
    duration = round((time.monotonic() - started) * 1000)
    if call_log is not None:
        call_log.record(
            purpose="ping",
            provider=provider.name,
            model=provider.model,
            attempt=1,
            duration_ms=duration,
            outcome="ok" if result[0] else ("quota" if status == 429 else "error"),
            http_status=status,
            error=_redact(error, key) if error else (None if result[0] else result[1]),
            input_tokens=0,
            output_tokens=0,
            messages="[]",
            reply=None,
        )
    return PingResult(result[0], _redact(result[1], key), status, duration)


def _ping_text(provider: ProviderSettings, response: HttpResponse) -> tuple[bool, str]:
    if response.status in (401, 403):
        return False, f"Schlüssel ungültig oder ohne Rechte (Fehler {response.status})"
    if response.status == 429:
        return False, "erreichbar, Schlüssel gilt, aber das Kontingent ist gerade erschöpft (Fehler 429)"
    if response.status == 503:
        return False, "erreichbar, aber überlastet (Fehler 503)"
    if response.status != 200:
        return False, f"Fehler {response.status}"
    try:
        names = {str(m["id"]) for m in json.loads(response.body)["data"]}
    except (ValueError, KeyError, TypeError):
        return True, "erreichbar, Schlüssel gilt; die Modellliste ist nicht lesbar"
    if provider.model in names or f"models/{provider.model}" in names:
        return True, f"erreichbar, Schlüssel gilt, Modell {provider.model} gefunden"
    return False, f"erreichbar, Schlüssel gilt, aber das Modell {provider.model} kennt der Anbieter nicht"


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


def _problems(error: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'Antwort'}: {e['msg']}" for e in error.errors()[:5])


def _correction(error: ValidationError) -> str:
    """Der Hinweis für den einen Wiederholungsversuch: was am JSON nicht passte, ohne die Eingabe zu wiederholen."""
    return f"Die Antwort passt nicht zum Schema ({_problems(error)}). Antworte nur mit gültigem JSON nach dem Schema."


def _error_body(response: HttpResponse) -> str | None:
    """Der Anfang einer Fehlerantwort fürs Protokoll. Bei Fehlern der Anmeldung (401, 403) nichts: Dort wiederholen
    Anbieter gern Teile des Schlüssels."""
    if response.status in (401, 403):
        return None
    return response.body[:MAX_LOGGED_ERROR].decode("utf-8", errors="replace") or None


def _redact(text: str, key: str) -> str:
    return text.replace(key, "***") if key else text
