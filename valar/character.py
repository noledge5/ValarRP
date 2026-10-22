"""Charaktererschaffung (Schritt 1i, CHAR aus REGELN.md, Entscheidungen 30, 31, 33 und 148 bis 152).

Prüft einen Charakterbogen gegen die Regeln. Liest keine Datenbank und schreibt nichts; angelegt wird die Figur
in `apply.create_character`. Jede Abweichung ist ein `CharacterError` mit Code und deutscher Meldung.
"""

import unicodedata
from collections.abc import Mapping, Sequence
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints

from valar import rules
from valar.content import Content, StartKit
from valar.wiki import slug

MAX_NAME = 40
MAX_KEEPSAKE = 80
MAX_BACKGROUND = 600


def _printable(text: str) -> str:
    """Keine Steuerzeichen: Der Text landet später im Terminal und im Kontext des Modells."""
    if any(unicodedata.category(char) == "Cc" for char in text):
        raise ValueError("Steuerzeichen sind nicht erlaubt")
    return text


def _text(min_length: int, max_length: int):
    return Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=min_length, max_length=max_length),
        AfterValidator(_printable),
    ]


class CharacterSheet(BaseModel):
    """Was der Spieler bei der Erschaffung wählt. Nicht genannte Attribute stehen auf dem Startwert 8."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: _text(1, MAX_NAME)
    attributes: dict[str, int] = {}
    main_skills: tuple[str, ...]
    side_skills: tuple[str, ...]
    kit_id: str
    keepsake: _text(1, MAX_KEEPSAKE)  # persönlicher Gegenstand ohne großen Wert, weicher Zustand
    background: _text(0, MAX_BACKGROUND) = ""  # frei erzählt, weicher Zustand

    @property
    def player_id(self) -> str:
        return slug(self.name)


CharacterCode = Literal[
    "invalid_name",
    "invalid_text",
    "unknown_attribute",
    "attribute_range",
    "points_not_spent",
    "skill_count",
    "skill_twice",
    "unknown_skill",
    "skill_locked",
    "unknown_kit",
    "no_start_location",
    "unknown_campaign",
    "player_exists",
]


class CharacterError(ValueError):
    """Der Bogen weicht von den Regeln ab. Es wird nichts angelegt."""

    def __init__(self, code: CharacterCode, message: str) -> None:
        super().__init__(message)
        self.code = code


def check_text(text: str, max_length: int, *, required: bool = True) -> str:
    """Name, Gegenstand oder Hintergrund wie im Bogen: getrimmt, Länge, keine Steuerzeichen."""
    text = text.strip()
    if (required and not text) or len(text) > max_length:
        limit = f"1 bis {max_length}" if required else f"höchstens {max_length}"
        raise CharacterError("invalid_text", f"Bitte {limit} Zeichen.")
    try:
        return _printable(text)
    except ValueError as error:
        raise CharacterError("invalid_text", f"{error}.") from None


def check_name(name: str) -> str:
    """Gibt die player_id zurück, die aus dem Namen entsteht."""
    player_id = slug(name)
    if not player_id:
        raise CharacterError("invalid_name", "Der Name braucht wenigstens einen Buchstaben oder eine Ziffer.")
    return player_id


def check_attributes(attributes: Mapping[str, int]) -> dict[str, int]:
    """Punktekauf: alle starten bei 8, genau 18 Punkte dazu, keines über 15. Gibt alle sechs Werte zurück."""
    unknown = sorted(set(attributes) - set(rules.ATTRIBUTES))
    if unknown:
        raise CharacterError("unknown_attribute", f"Unbekannte Attribute: {', '.join(unknown)}.")
    values = {attribute: rules.START_ATTRIBUTE for attribute in rules.ATTRIBUTES} | dict(attributes)
    wrong = [a for a, v in values.items() if not rules.START_ATTRIBUTE <= v <= rules.MAX_START_ATTRIBUTE]
    if wrong:
        raise CharacterError(
            "attribute_range",
            f"Attribute zum Start zwischen {rules.START_ATTRIBUTE} und {rules.MAX_START_ATTRIBUTE}: {', '.join(wrong)}.",
        )
    spent = rules.attribute_points_spent(values)
    if spent != rules.START_ATTRIBUTE_POINTS:
        raise CharacterError(
            "points_not_spent",
            f"Genau {rules.START_ATTRIBUTE_POINTS} Punkte verteilen, verteilt sind {spent}.",
        )
    return values


def check_skill_choice(chosen: Sequence[str], count: int, what: str) -> None:
    """Eine Auswahl (Haupt- oder Nebenskills): bekannte, nicht gesperrte Skills, genau `count`, keiner doppelt."""
    for skill_id in chosen:
        if skill_id in rules.LOCKED_SKILLS:
            raise CharacterError(
                "skill_locked", f"{rules.LOCKED_SKILLS[skill_id]} ist noch gesperrt, bis es Zahlen für Energie gibt."
            )
        if skill_id not in rules.SKILLS:
            raise CharacterError("unknown_skill", f"Den Skill {skill_id!r} gibt es nicht.")
    if len(chosen) != count:
        raise CharacterError("skill_count", f"Genau {count} {what} wählen, gewählt sind {len(chosen)}.")
    if len(set(chosen)) != len(chosen):
        raise CharacterError("skill_twice", "Jeder Skill zählt nur einmal.")


def check_skills(main: Sequence[str], side: Sequence[str]) -> dict[str, int]:
    """2 Hauptskills auf 30, 4 Nebenskills auf 15, jeder nur einmal. Gibt die Startwerte zurück."""
    check_skill_choice(main, rules.MAIN_SKILLS, "Hauptskills")
    check_skill_choice(side, rules.SIDE_SKILLS, "Nebenskills")
    if set(main) & set(side):
        raise CharacterError("skill_twice", "Jeder Skill zählt nur einmal.")
    return rules.start_skills(main, side)


def check_kit(kit_id: str, content: Content) -> StartKit:
    kit = content.start_kits.get(kit_id)
    if kit is None:
        raise CharacterError("unknown_kit", f"Diese Ausrüstung gibt es nicht. Zur Wahl: {', '.join(content.start_kits)}.")
    return kit


def check_sheet(sheet: CharacterSheet, content: Content) -> None:
    """Prüft den ganzen Bogen gegen Regeln und Inhalt."""
    if content.start_location_id is None:
        raise CharacterError("no_start_location", "Der Inhalt nennt keinen Startort für neue Figuren.")
    check_name(sheet.name)
    check_attributes(sheet.attributes)
    check_skills(sheet.main_skills, sheet.side_skills)
    check_kit(sheet.kit_id, content)
