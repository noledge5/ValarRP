"""Regel-Engine: Tabellen und reine Rechenfunktionen aus docs/REGELN.md. Kein Datenbankzugriff.

Die IDs (MECH, DIFF, SKILL, PROG) sind stabil; der Code verweist darauf.
"""

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from valar.models import AttemptDuration, DifficultyId, Frequency, Outcome, RouteKind

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


# --- Vergleichsprobe (MECH-05, Entscheidungen 63 und 100) ---

# Spieler-Skill → Gegen-Skill des NPCs. Ein Skill ohne Paar kann keine Vergleichsprobe sein.
CONTEST_PAIRS: dict[str, str] = {
    "SKILL-201": "SKILL-405",  # Schleichen gegen Wahrnehmung
    "SKILL-203": "SKILL-405",  # Taschendiebstahl gegen Wahrnehmung
    "SKILL-301": "SKILL-305",  # Überzeugen gegen Menschenkenntnis
    "SKILL-302": "SKILL-305",  # Täuschen gegen Menschenkenntnis
    "SKILL-303": "SKILL-305",  # Einschüchtern gegen Menschenkenntnis
    "SKILL-304": "SKILL-304",  # Feilschen gegen Feilschen
    "SKILL-305": "SKILL-302",  # Menschenkenntnis gegen Täuschen
    "SKILL-306": "SKILL-306",  # Anführen gegen Anführen
    "SKILL-307": "SKILL-305",  # Verführen gegen Menschenkenntnis
    "SKILL-403": "SKILL-403",  # Athletik gegen Athletik
    "SKILL-404": "SKILL-404",  # Akrobatik gegen Akrobatik
    "SKILL-405": "SKILL-201",  # Wahrnehmung gegen Schleichen
}


def contest_difficulty(npc_total: int) -> DifficultyId | None:
    """Die höchste DIFF-Stufe, deren SG das Gesamt des NPCs nicht übersteigt; None unter SG 8."""
    reached = [d for d, values in DIFFICULTIES.items() if values.dc <= npc_total]
    return max(reached, key=lambda d: DIFFICULTIES[d].dc) if reached else None


def contest_ticks(npc_total: int, bonus: int, *, ticked_this_scene: bool = False) -> int:
    """Mögliche Ticks einer Vergleichsprobe: nach der Stufe des NPC-Gesamts; Routine (SG − Bonus ≤ 5)
    und Szenenregel gelten mit dem NPC-Gesamt als SG."""
    difficulty = contest_difficulty(npc_total)
    if difficulty is None or ticked_this_scene or npc_total - bonus <= ROUTINE_NEEDED:
        return 0
    return DIFFICULTIES[difficulty].ticks


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


# --- Raum: Koordinaten, Wege, Reisezeit (Entscheidungen 75 bis 78) ---

# Karte: x nach Osten, y nach Norden, in ganzen Metern.
Point = tuple[int, int]

# Reisetempo zu Fuß je Wegart, in Metern pro Stunde. Vorschlag, Testwerte.
TRAVEL_METERS_PER_HOUR: dict[RouteKind, int] = {"road": 5000, "path": 4000, "rough": 2500}

COMPASS = ("N", "NO", "O", "SO", "S", "SW", "W", "NW")


def path_length(points: Sequence[Point]) -> int:
    """Länge eines Linienzugs in ganzen Metern (gerundet)."""
    return round(sum(math.dist(a, b) for a, b in zip(points, points[1:])))


def travel_minutes(length_m: int, kind: RouteKind) -> int:
    """Reisezeit für eine Weglänge, auf ganze Minuten aufgerundet, mindestens 1."""
    return max(1, -(-length_m * 60 // TRAVEL_METERS_PER_HOUR[kind]))


def compass(origin: Point, target: Point) -> str | None:
    """Himmelsrichtung von `origin` nach `target` in acht Richtungen, Luftlinie. None bei gleichem Punkt."""
    dx, dy = target[0] - origin[0], target[1] - origin[1]
    if dx == dy == 0:
        return None
    degrees = math.degrees(math.atan2(dx, dy)) % 360  # 0 = Norden, im Uhrzeigersinn
    return COMPASS[round(degrees / 45) % 8]


# --- Geld (Entscheidung 32) ---


def format_money(pennies: int) -> str:
    """Pfennig als Gulden, Groschen und Pfennig (1 : 10 : 100, Entscheidung 32)."""
    gulden, rest = divmod(pennies, 100)
    groschen, pfennig = divmod(rest, 10)
    parts = [f"{gulden} Gulden"] if gulden else []
    parts += [f"{groschen} Groschen"] if groschen else []
    parts += [f"{pfennig} Pfennig"] if pfennig or not parts else []
    return ", ".join(parts)


# --- Zufall des Codes (Entscheidung 62) ---


def turn_rng(seed: int, revision: int) -> random.Random:
    """Zufall eines Spielzugs: aus dem Seed der Kampagne und der Revision vor dem Zug.
    Gleicher Seed und gleicher Verlauf ergeben dieselben Würfe."""
    return random.Random(f"valar:{seed}:{revision}")


# --- Untersuchen und Sammeln (Entscheidungen 64 und 92) ---

PASSIVE_BASE = 10  # MECH-01 ohne Wurf: 10 + Bonus


def passive_known(bonus: int, dc: int) -> bool:
    """Eine Wissensstufe ist ohne Wurf sichtbar, wenn 10 + Bonus den SG erreicht."""
    return PASSIVE_BASE + bonus >= dc


# Sammeln je Häufigkeit: Stufe der Probe und höchste Menge bei Erfolg (1 bis Höchstmenge, gleich verteilt).
# Vorschlag, Testwerte.
GATHER: dict[Frequency, tuple[DifficultyId, int]] = {
    "common": ("DIFF-02", 6),
    "uncommon": ("DIFF-03", 4),
    "rare": ("DIFF-04", 2),
    "very_rare": ("DIFF-05", 1),
}


# --- Kampagnen-Seiten (Entscheidungen 91 und 117) ---


@dataclass(frozen=True)
class PageTemplate:
    """Was eine Seitenvorlage festlegt. Vorkommen gilt am Ort der Spielfigur; ein Wert macht die Seite
    zum Gegenstand, der sich mit `gather_skill` sammeln lässt."""

    type: str
    frequency: Frequency | None = None
    value_pennies: int | None = None
    gather_skill: str | None = None


# Testwerte. Wichtige Figuren (Person) und Orte legt das Modell nie an (Entscheidung 11).
PAGE_TEMPLATES: dict[str, PageTemplate] = {
    "wissen": PageTemplate("Wissen"),
    "brauch": PageTemplate("Brauch", "common"),
    "ereignis": PageTemplate("Ereignis", "uncommon"),
    "tier": PageTemplate("Tier", "uncommon"),
    "pflanze": PageTemplate("Pflanze", "uncommon"),
    "heilkraut": PageTemplate("Pflanze", "uncommon", 4, "SKILL-408"),
    "seltenes-kraut": PageTemplate("Pflanze", "rare", 12, "SKILL-408"),
}


# --- Tageswechsel mit Folgen (Entscheidungen 12, 57 und 101) ---

RATION = "ration"  # Gegenstand im Gepäck; Wiki-Seite gegenstaende/ration
MAX_STAGE = 4
CONDITION_MALUS = (0, -1, -2, -4, -6)  # je Stufe von Hunger und Erschöpfung
HUNGER_NAMES = ("Satt", "Hungrig", "Mager", "Ausgemergelt", "Auszehrend")
EXHAUSTION_NAMES = ("Ausgeruht", "Müde", "Übermüdet", "Erschöpft", "Gerädert")
EXHAUSTION_HOURS = (18, 24, 36, 48)  # ab so vielen Stunden ohne Schlaf eine Stufe mehr
SLEEP_MINUTES_PER_STAGE = 6 * 60  # je 6 Stunden Schlaf am Stück eine Stufe weniger
WAKE_MINUTE_OF_DAY = 6 * 60  # Testwert: geschlafen wird bis 6 Uhr
MAX_SLEEP_MINUTES = 12 * 60


def hunger_after_midnight(hunger: int, ate: bool) -> int:
    """Mit Ration eine Stufe weniger, ohne eine mehr; höchstens Stufe 4."""
    return max(0, hunger - 1) if ate else min(MAX_STAGE, hunger + 1)


def exhaustion(debt: int, awake_since: int, now: int) -> int:
    """Erschöpfungsstufe: offene Stufen aus dem letzten Schlaf plus die Stufen der wachen Stunden."""
    hours = (now - awake_since) // 60
    return min(MAX_STAGE, debt + sum(1 for limit in EXHAUSTION_HOURS if hours >= limit))


def sleep_minutes(game_minutes: int, start_minute_of_day: int) -> int:
    """Schlafen bis zum nächsten Morgen um 6 Uhr, höchstens 12 Stunden, mindestens 1 Minute."""
    _, minute = clock(game_minutes, start_minute_of_day)
    until_morning = (WAKE_MINUTE_OF_DAY - minute) % MINUTES_PER_DAY or MINUTES_PER_DAY
    return max(1, min(until_morning, MAX_SLEEP_MINUTES))


def debt_after_sleep(stage: int, minutes: int) -> int:
    return max(0, stage - minutes // SLEEP_MINUTES_PER_STAGE)


@dataclass(frozen=True)
class Weather:
    """Wetter eines Tages (Herbsttabelle, Entscheidung 101). Unwetter ist kein Grollwetter."""

    name: str
    perception: int = 0  # Malus auf Wahrnehmung (SKILL-405)
    checks: int = 0  # Malus auf alle Proben
    travel: float = 1.0  # Faktor auf die Reisezeit


# (höchster W20-Wert, Wetter)
WEATHER_TABLE: tuple[tuple[int, Weather], ...] = (
    (4, Weather("Kühler Altweibersommer")),
    (8, Weather("Herbstlicher Frühnebel", perception=-2)),
    (12, Weather("Nasskalter Dauerregen", travel=1.5)),
    (15, Weather("Rauer Schneeregen", checks=-1, travel=1.5)),
    (18, Weather("Heulender Böenwind", perception=-2)),
    (20, Weather("Unwetter", perception=-2, travel=2.0)),
)


def weather_roll(seed: int, day: int) -> int:
    """Der W20 für das Wetter eines Tages: aus Seed und Tag, also für jeden Tag fest (Entscheidung 109)."""
    return random.Random(f"valar:wetter:{seed}:{day}").randint(1, 20)


def weather(roll: int) -> Weather:
    return next(entry for limit, entry in WEATHER_TABLE if roll <= limit)


CALM = WEATHER_TABLE[0][1]


def weather_of_day(seed: int, day: int) -> Weather:
    """Gewürfelt wird um Mitternacht; der erste Tag ist ruhig."""
    return CALM if day <= 1 else weather(weather_roll(seed, day))


def check_malus(skill_id: str, hunger: int, exhaustion_stage: int, today: Weather) -> int:
    """Malus beim Öffnen einer Probe: Hunger und Erschöpfung zählen zusammen, dazu das Wetter."""
    malus = CONDITION_MALUS[hunger] + CONDITION_MALUS[exhaustion_stage] + today.checks
    return malus + (today.perception if skill_id == "SKILL-405" else 0)


def travel_minutes_in(minutes: int, today: Weather) -> int:
    return math.ceil(minutes * today.travel)


# --- Erzählte Knappheit (Entscheidung 123) ---

SCARCITY_LEVELS = ("scarce", "gone")  # knapp: eine Stufe schwerer; weg: Sammeln abgelehnt
SCARCITY_MINUTES = {"day": MINUTES_PER_DAY, "week": 7 * MINUTES_PER_DAY, "month": 30 * MINUTES_PER_DAY}


def harder(difficulty: DifficultyId) -> DifficultyId:
    """Die nächstschwerere Stufe; DIFF-07 bleibt."""
    order = list(DIFFICULTIES)
    return order[min(order.index(difficulty) + 1, len(order) - 1)]
