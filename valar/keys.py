"""Schlüssel und Budget aus der Weboberfläche (Schritt 3, Entscheidungen 168, 176 und 177).

Ein Schlüssel kann außer aus der Umgebung auch über die Weboberfläche kommen. Er liegt in `schluessel.json` im
Datenordner neben dem Spielstand, nur für den Server lesbar (0600), und gilt nur, wenn die Umgebungsvariable fehlt.
Nie in Datenbank, Repo, Protokoll oder Fehlermeldung; angezeigt werden nur die letzten vier Zeichen.

Die Grenzen des Budgets lassen sich in der Weboberfläche ändern und liegen dann in `budget.json`; sie gehen den
Werten aus `modelle.toml` vor. Ob die Seite Vorschläge des Modells zeigt (Entscheidung 201), steht in `anzeige.json`,
die gewählten Plätze 1 bis 3 der Rotation in `kette.json` (Entscheidung 236).
Alles ist Einstellung, kein Spielzustand.
"""

import json
import os
import re
import tempfile
from collections import ChainMap
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from valar import llm

KEY_FILE = "schluessel.json"
BUDGET_FILE = "budget.json"
DISPLAY_FILE = "anzeige.json"
CHAIN_FILE = "kette.json"
CHAIN_SLOTS = 3  # Plätze der Rotation, die man auf der Seite wählt (Entscheidung 236)
KEY_ENV = re.compile(r"^[A-Z][A-Z0-9_]*$")
KEY = re.compile(r"^[\x21-\x7e]{8,400}$")  # druckbar, ohne Leerzeichen: steht später im Kopf `Authorization`
SHOWN = 4  # so viele Zeichen am Ende zeigt die Seite


class KeyInputError(ValueError):
    """Falscher Name oder falscher Schlüssel. Die Meldung enthält nie den Schlüssel."""


class KeyStore:
    """Schlüssel je Umgebungsvariable in einer eigenen Datei im Datenordner."""

    def __init__(self, data_dir: str | Path) -> None:
        self.path = Path(data_dir) / KEY_FILE

    def read(self) -> dict[str, str]:
        """Alle gültigen Einträge. Fehlt die Datei oder ist sie kaputt, gibt es keine."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            name: key
            for name, key in data.items()
            if isinstance(name, str) and isinstance(key, str) and KEY_ENV.match(name) and KEY.match(key)
        }

    def set(self, key_env: str, key: str) -> None:
        key = key.strip()
        if not KEY_ENV.match(key_env):
            raise KeyInputError("Unbekannte Umgebungsvariable.")
        if not KEY.match(key):
            raise KeyInputError("Ein Schlüssel hat 8 bis 400 Zeichen, ohne Leerzeichen und Umlaute.")
        self._write(self.read() | {key_env: key})

    def delete(self, key_env: str) -> None:
        stored = self.read()
        if stored.pop(key_env, None) is not None:
            self._write(stored)

    def environ(self, environ: Mapping[str, str] = os.environ) -> Mapping[str, str]:
        """Für den Modell-Client: die Umgebung geht vor, die Datei gilt, wenn die Variable fehlt."""
        return ChainMap({k: v for k, v in environ.items() if v}, self.read())

    def _write(self, keys: dict[str, str]) -> None:
        _replace(self.path, json.dumps(keys))

def key_state(key_env: str, environ: Mapping[str, str], store: KeyStore) -> tuple[str, str | None]:
    """Woher der Schlüssel kommt: ("env", None), ("file", letzte Zeichen) oder ("missing", None)."""
    if environ.get(key_env):
        return "env", None
    key = store.read().get(key_env)
    if key:
        return "file", key[-SHOWN:]
    return "missing", None


def load_budget(data_dir: str | Path) -> llm.BudgetSettings | None:
    """Die Grenzen aus der Weboberfläche, oder None, wenn keine gesetzt oder die Datei kaputt ist."""
    try:
        data = json.loads((Path(data_dir) / BUDGET_FILE).read_text(encoding="utf-8"))
        return llm.BudgetSettings.model_validate(data)
    except (OSError, ValueError):
        return None


def save_budget(data_dir: str | Path, budget: llm.BudgetSettings) -> None:
    _replace(Path(data_dir) / BUDGET_FILE, budget.model_dump_json())

def load_show_ideas(data_dir: str | Path) -> bool:
    """Ob die Seite Vorschläge des Modells zeigt; ohne oder mit kaputter Datei ja."""
    try:
        return json.loads((Path(data_dir) / DISPLAY_FILE).read_text(encoding="utf-8")).get("ideas") is not False
    except (OSError, ValueError, AttributeError):
        return True


def save_show_ideas(data_dir: str | Path, show: bool) -> None:
    _replace(Path(data_dir) / DISPLAY_FILE, json.dumps({"ideas": show}))


class ChainSlot(BaseModel):
    """Ein gewählter Platz der Rotation: Anbieter aus `modelle.toml`, Modell aus seiner Liste, Preise von dort."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1, max_length=40)
    model: str = Field(pattern=r"^[\w.:/@+-]{1,120}$")  # geht in jeden Aufruf: keine Leerzeichen, keine Steuerzeichen
    input_eur_per_million: float | None = Field(default=None, ge=0)
    output_eur_per_million: float | None = Field(default=None, ge=0)


def load_chain(data_dir: str | Path) -> list[ChainSlot]:
    """Die gewählten Plätze, oder keine, wenn nichts gewählt oder die Datei kaputt ist."""
    try:
        data = json.loads((Path(data_dir) / CHAIN_FILE).read_text(encoding="utf-8"))
        return [ChainSlot.model_validate(slot) for slot in data][:CHAIN_SLOTS]
    except (OSError, ValueError, TypeError):
        return []


def save_chain(data_dir: str | Path, chain: list[ChainSlot]) -> None:
    _replace(Path(data_dir) / CHAIN_FILE, json.dumps([slot.model_dump() for slot in chain]))


def apply_chain(settings: llm.ModelSettings, chain: list[ChainSlot]) -> llm.ModelSettings:
    """Die gewählten Plätze vorn, je als eigener Anbieter „1. openrouter“ (eigene Ruhe, Entscheidung 223), danach die
    Anbieter aus `modelle.toml` als Rückfall, außer einem, der schon mit demselben Modell vorn steht. Ein Preis, den die
    Liste nicht kennt, kommt vom Anbieter aus der Datei."""
    providers = {p.name: p for p in settings.provider}
    front = []
    for number, slot in enumerate(chain, start=1):
        base = providers.get(slot.provider)
        if base is None:
            continue
        prices = {k: v for k in ("input_eur_per_million", "output_eur_per_million") if (v := getattr(slot, k)) is not None}
        front.append(base.model_copy(update={"name": f"{number}. {base.name}"[:40], "model": slot.model, **prices}))
    if not front:
        return settings
    chosen = {(slot.provider, slot.model) for slot in chain}
    rest = tuple(p for p in settings.provider if (p.name, p.model) not in chosen)
    return settings.model_copy(update={"provider": (*front, *rest)})


def effective_settings(settings: llm.ModelSettings, data_dir: str | Path) -> llm.ModelSettings:
    """Die Einstellungen aus der Datei, mit Budget-Grenzen und gewählter Rotation aus der Weboberfläche, falls gesetzt."""
    budget = load_budget(data_dir)
    settings = settings if budget is None else settings.model_copy(update={"budget": budget})
    return apply_chain(settings, load_chain(data_dir))


def _replace(path: Path, text: str) -> None:
    """Schreibt in eine neue Datei (0600, von `mkstemp`) und tauscht sie dann aus: nie eine halbe Datei."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
