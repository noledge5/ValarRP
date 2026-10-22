"""Regel-Engine: Tabellen und reine Rechenfunktionen aus docs/REGELN.md. Kein Datenbankzugriff.

Die IDs (MECH, DIFF, SKILL, PROG) sind stabil; der Code verweist darauf.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from valar.models import AttemptDuration, DifficultyId, Outcome

# --- Attribute (ATTR-01 bis ATTR-06) ---

ATTRIBUTES = ("STR", "GES", "KON", "INT", "WEI", "CHA")


def attribute_modifier(value: int) -> int:
    """MECH-08: abrunden((Wert - 10) / 2). 9 -> -1, 10 und 11 -> 0, 12 -> +1."""
    return (value - 10) // 2


# --- Skills (SKILL) ---


@dataclass(frozen=True)
class Skill:
    name: str
    attributes: tuple[str, ...]  # Leit-Attribute; MECH-09: der höhere Modifikator zählt


# SKILL-501 (Ziehen) fehlt, bis es die Begabung gibt.
SKILLS: dict[str, Skill] = {
    "SKILL-101": Skill("Klingenwaffen", ("STR", "GES")),
    "SKILL-102": Skill("Wuchtwaffen", ("STR",)),
    "SKILL-103": Skill("Stangenwaffen", ("STR", "GES")),
    "SKILL-104": Skill("Waffenloser Kampf", ("STR", "GES")),
    "SKILL-105": Skill("Bogen", ("GES",)),
    "SKILL-106": Skill("Armbrust", ("GES",)),
    "SKILL-107": Skill("Wurfwaffen", ("STR", "GES")),
    "SKILL-108": Skill("Parade / Schild", ("STR", "KON")),
    "SKILL-109": Skill("Rüstungsgewöhnung", ("KON", "STR")),
    "SKILL-110": Skill("Kriegskunst", ("INT",)),
    "SKILL-201": Skill("Schleichen", ("GES",)),
    "SKILL-202": Skill("Schlösser knacken", ("GES",)),
    "SKILL-203": Skill("Taschendiebstahl", ("GES",)),
    "SKILL-204": Skill("Fallenkunde", ("INT", "GES")),
    "SKILL-301": Skill("Überzeugen", ("CHA",)),
    "SKILL-302": Skill("Täuschen", ("CHA",)),
    "SKILL-303": Skill("Einschüchtern", ("STR", "CHA")),
    "SKILL-304": Skill("Feilschen", ("CHA", "INT")),
    "SKILL-305": Skill("Menschenkenntnis", ("WEI",)),
    "SKILL-306": Skill("Anführen", ("CHA",)),
    "SKILL-307": Skill("Verführen", ("CHA",)),
    "SKILL-401": Skill("Erste Hilfe", ("INT", "WEI")),
    "SKILL-402": Skill("Überleben", ("WEI",)),
    "SKILL-403": Skill("Athletik", ("STR", "KON")),
    "SKILL-404": Skill("Akrobatik", ("GES",)),
    "SKILL-405": Skill("Wahrnehmung", ("WEI",)),
    "SKILL-406": Skill("Handwerk (Allgemein)", ("INT", "GES")),
    "SKILL-407": Skill("Alchemie", ("INT",)),
    "SKILL-408": Skill("Naturkunde", ("INT", "WEI")),
    "SKILL-409": Skill("Monsterkunde", ("INT", "WEI")),
}

MAX_SKILL = 100


def skill_bonus(skill_value: int) -> int:
    """MECH-02: abrunden(SKILL / 10). Skill 18 -> +1, Skill 20 -> +2."""
    return skill_value // 10


def check_bonus(skill_id: str, skill_value: int, attributes: Mapping[str, int]) -> int:
    """Bonus auf den W20 (MECH-01): bester Leit-Attribut-Modifikator plus Skill-Bonus."""
    lead = max(attribute_modifier(attributes[a]) for a in SKILLS[skill_id].attributes)
    return lead + skill_bonus(skill_value)


# --- Schwierigkeit (DIFF) ---


@dataclass(frozen=True)
class Difficulty:
    dc: int  # Schwierigkeitsgrad (SG)
    ticks: int  # Ticks bei Erfolg


DIFFICULTIES: dict[DifficultyId, Difficulty] = {
    "DIFF-01": Difficulty(8, 1),
    "DIFF-02": Difficulty(10, 1),
    "DIFF-03": Difficulty(12, 2),
    "DIFF-04": Difficulty(14, 3),
    "DIFF-05": Difficulty(16, 4),
    "DIFF-06": Difficulty(20, 6),
    "DIFF-07": Difficulty(24, 8),  # Vorschlag, noch offen
}

ROUTINE_NEEDED = 5  # Gegen Grinden: war höchstens eine 5 nötig, gibt es keine Ticks


def ticks_on_success(difficulty: DifficultyId, bonus: int, *, ticked_this_scene: bool = False) -> int:
    """Mögliche Ticks, beim Öffnen gerechnet. Keine bei Routine und keine, wenn derselbe Skill
    in dieser Szene schon Ticks gebracht hat (Entscheidungen 50 und 58)."""
    d = DIFFICULTIES[difficulty]
    if ticked_this_scene or d.dc - bonus <= ROUTINE_NEEDED:
        return 0
    return d.ticks


# --- Ausgang einer Probe (MECH-03, -04, -07) ---


def outcome(die: int, total: int, dc: int) -> Outcome:
    """Natürliche 20 und 1 gehen vor. Knapp daneben: Gesamt = SG - 1 oder SG - 2."""
    if die == 20:
        return "critical_success"
    if die == 1:
        return "fumble"
    if total >= dc:
        return "success"
    if total >= dc - 2:
        return "near_miss"
    return "failure"


# --- Fortschritt (PROG) ---

# (höchster Skillwert des Bands, Ticks für +1 Skillpunkt); PROG-01 bis PROG-05
PROGRESS_BANDS = ((20, 3), (40, 5), (60, 8), (80, 12), (100, 20))
SKILL_UPS_PER_LEVEL = 10


def ticks_per_point(target_value: int) -> int:
    """Ticks, um den Skillwert `target_value` zu erreichen. Das Band des Zielwerts gilt."""
    if not 1 <= target_value <= MAX_SKILL:
        raise ValueError(f"Skillwert außerhalb 1..{MAX_SKILL}: {target_value}")
    return next(cost for upper, cost in PROGRESS_BANDS if target_value <= upper)


def add_ticks(value: int, ticks: int, gained: int) -> tuple[int, int, int]:
    """Rechnet Ticks in Skillpunkte um. Gibt (neuer Wert, neue Ticks, gewonnene Punkte) zurück.

    Überschüssige Ticks bleiben für den nächsten Punkt, mehrere Aufstiege auf einmal sind möglich.
    Bei Skill 100 verfallen Ticks.
    """
    points = 0
    ticks += gained
    while value < MAX_SKILL and ticks >= ticks_per_point(value + 1):
        ticks -= ticks_per_point(value + 1)
        value += 1
        points += 1
    if value >= MAX_SKILL:
        ticks = 0
    return value, ticks, points


def level(skill_ups: int) -> int:
    """Startlevel 1; je 10 Skill-Aufstiege (in beliebigen Skills) ein Level mehr."""
    return 1 + skill_ups // SKILL_UPS_PER_LEVEL


# --- Zeit und Szene (Entscheidungen 53, 57, 59) ---

MINUTES_PER_DAY = 24 * 60

# Dauer freier Versuche: Das Modell wählt die Kategorie, der Code die Minuten.
ATTEMPT_MINUTES: dict[AttemptDuration, int] = {"moment": 5, "hour": 60, "half_day": 360}


def clock(game_minutes: int, start_minute_of_day: int) -> tuple[int, int]:
    """(Tag ab 1, Minute des Tages). Der Tag wird aus der Spielzeit abgeleitet."""
    day, minute = divmod(start_minute_of_day + game_minutes, MINUTES_PER_DAY)
    return day + 1, minute


def last_midnight(game_minutes: int, start_minute_of_day: int) -> int:
    """Spielminute der letzten Mitternacht vor oder genau bei `game_minutes`.

    Vor der ersten Mitternacht ist das Ergebnis negativ (die Mitternacht vor Spielbeginn).
    """
    return game_minutes - (start_minute_of_day + game_minutes) % MINUTES_PER_DAY


def scene_start(arrived_at: int, game_minutes: int, start_minute_of_day: int) -> int:
    """Beginn der laufenden Szene einer Spielfigur, in Spielminuten. Er kennzeichnet die Szene.

    Eine Szene endet beim Ortswechsel (Ankunft) und um Mitternacht; es gilt das Spätere.
    """
    return max(arrived_at, last_midnight(game_minutes, start_minute_of_day))
