"""Aktionen (vom Modell angefordert), Effekte (vom Code bestimmt) und Spielzug-Ergebnis."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

_STRICT = ConfigDict(extra="forbid", frozen=True)

DifficultyId = Literal["DIFF-01", "DIFF-02", "DIFF-03", "DIFF-04", "DIFF-05", "DIFF-06", "DIFF-07"]
Outcome = Literal["critical_success", "success", "near_miss", "failure", "fumble"]
# Dauer eines freien Versuchs (Entscheidung 59); die Minuten legt der Code fest.
AttemptDuration = Literal["moment", "hour", "half_day"]
# Wegart einer Verbindung; bestimmt das Reisetempo (Entscheidung 76).
RouteKind = Literal["road", "path", "rough"]
# Wie oft etwas vorkommt (Entscheidung 88), von häufig bis sehr selten. Die Zahlen kommen mit 1e.
Frequency = Literal["common", "uncommon", "rare", "very_rare"]


# --- Aktionen: das Modell fordert sie an, der Code bestimmt die Folgen ---


class SubmitQuestItems(BaseModel):
    """Gegenstände für einen Auftrag abgeben.

    Nennt nur den Auftrag. Gegenstand, Menge, Ort und Belohnung stehen im Inhalt,
    nie in der Aktion.
    """

    model_config = _STRICT

    type: Literal["submit_quest_items"] = "submit_quest_items"
    quest_id: str = Field(min_length=1)


class AttemptAction(BaseModel):
    """Versuch mit Probe. Das Modell schlägt nur Skill, Stufe, Dauer und Grund vor (Entscheidungen 10
    und 59); SG, Bonus, mögliche Ticks und Minuten rechnet der Code. Eröffnet eine offene Probe."""

    model_config = _STRICT

    type: Literal["attempt"] = "attempt"
    skill_id: str = Field(pattern=r"^SKILL-\d{3}$")
    difficulty: DifficultyId
    duration: AttemptDuration
    reason: str = Field(min_length=1, max_length=200)


class Move(BaseModel):
    """Zu einem verbundenen Ort gehen. Nennt nur das Ziel; die Dauer steht im Inhalt (Entscheidung 55)."""

    model_config = _STRICT

    type: Literal["move"] = "move"
    destination_id: str = Field(min_length=1)


class Examine(BaseModel):
    """Eine Wiki-Seite am Ort genauer untersuchen (Entscheidungen 64 und 92). Nennt nur die Seite;
    Skill und Stufe kommen aus der nächsten unbekannten Wissensstufe."""

    model_config = _STRICT

    type: Literal["examine"] = "examine"
    page_id: str = Field(min_length=1)


class Gather(BaseModel):
    """Etwas sammeln, das laut Wiki am Ort vorkommt (Entscheidung 92). Nennt nur die Seite;
    Skill, Stufe und Menge bestimmt der Code."""

    model_config = _STRICT

    type: Literal["gather"] = "gather"
    page_id: str = Field(min_length=1)


class MeetNpc(BaseModel):
    """Einen neuen NPC ansprechen (Entscheidung 11). Das Modell wählt die Vorlage (eine NPC-Typ-Seite im Wiki)
    und nennt Name und Beschreibung; Werte würfelt der Code. Der NPC steht am Ort der Spielfigur."""

    model_config = _STRICT

    type: Literal["meet_npc"] = "meet_npc"
    template_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(min_length=1, max_length=300)


class Talk(BaseModel):
    """Mit einem bekannten NPC am selben Ort reden. Was gesagt wird, erzählt das Modell."""

    model_config = _STRICT

    type: Literal["talk"] = "talk"
    npc_id: str = Field(min_length=1)


class RecordFact(BaseModel):
    """Weicher Fakt der Kampagne (Entscheidungen 8 und 107): was die Spielfigur erfahren hat.
    Ein Gerücht (`rumor`) ist nicht wahr, nur weil es jemand sagt. Harter Zustand bleibt unberührt."""

    model_config = _STRICT

    type: Literal["record_fact"] = "record_fact"
    kind: Literal["fact", "rumor"] = "fact"
    text: str = Field(min_length=1, max_length=300)
    subject: str | None = Field(default=None, min_length=1, max_length=80)
    source_npc_id: str | None = Field(default=None, min_length=1)


class CreatePage(BaseModel):
    """Neue Kampagnen-Seite im Wiki (Entscheidungen 91 und 108). Das Modell wählt eine Seitenvorlage und
    schreibt Titel und Text; harte Felder (Vorkommen, Wert) setzt der Code aus der Vorlage."""

    model_config = _STRICT

    type: Literal["create_page"] = "create_page"
    template: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=1500)


class Contest(BaseModel):
    """Vergleichsprobe gegen einen NPC am Ort (MECH-05, Entscheidung 63). Das Modell nennt Skill, NPC, Dauer
    und Grund; den Gegen-Skill nimmt der Code aus der Paar-Tabelle, und der NPC würfelt beim Öffnen."""

    model_config = _STRICT

    type: Literal["contest"] = "contest"
    skill_id: str = Field(pattern=r"^SKILL-\d{3}$")
    npc_id: str = Field(min_length=1)
    duration: AttemptDuration
    reason: str = Field(min_length=1, max_length=200)


class Sleep(BaseModel):
    """Schlafen bis zum Morgen; die Dauer bestimmt der Code (Entscheidung 12)."""

    model_config = _STRICT

    type: Literal["sleep"] = "sleep"


class MarkScarcity(BaseModel):
    """Erzählte Knappheit (Entscheidung 123): Etwas, das hier vorkommt, ist knapp oder ganz weg, für eine Zeit.
    Das Modell kann damit nur erschweren, nie erleichtern."""

    model_config = _STRICT

    type: Literal["mark_scarcity"] = "mark_scarcity"
    page_id: str = Field(min_length=1)
    level: Literal["scarce", "gone"]
    duration: Literal["day", "week", "month"]
    reason: str = Field(min_length=1, max_length=200)


class SubmitRoll(BaseModel):
    """Der Spieler tippt das Ergebnis seines analogen W20 ein."""

    model_config = _STRICT

    type: Literal["submit_roll"] = "submit_roll"
    value: int = Field(ge=1, le=20, strict=True)


Action = Annotated[
    SubmitQuestItems
    | AttemptAction
    | SubmitRoll
    | Move
    | Examine
    | Gather
    | MeetNpc
    | Talk
    | RecordFact
    | CreatePage
    | Contest
    | Sleep
    | MarkScarcity,
    Field(discriminator="type"),
]
ACTIONS = TypeAdapter(Action)


# --- Effekte: kleinste Änderungen am harten Zustand, nur apply.py wendet sie an ---


class RemoveItems(BaseModel):
    model_config = _STRICT

    type: Literal["remove_items"] = "remove_items"
    item_id: str = Field(min_length=1)
    quantity: int = Field(gt=0)


class AddItems(BaseModel):
    model_config = _STRICT

    type: Literal["add_items"] = "add_items"
    item_id: str = Field(min_length=1)
    quantity: int = Field(gt=0)


class AddMoney(BaseModel):
    model_config = _STRICT

    type: Literal["add_money"] = "add_money"
    pennies: int = Field(gt=0)


class SetQuestStatus(BaseModel):
    model_config = _STRICT

    type: Literal["set_quest_status"] = "set_quest_status"
    quest_id: str = Field(min_length=1)
    status: Literal["active", "completed"]


class AdvanceTime(BaseModel):
    model_config = _STRICT

    type: Literal["advance_time"] = "advance_time"
    minutes: int = Field(gt=0)


class EnterLocation(BaseModel):
    """Die Spielfigur kommt an einem Ort an. `arrived_at` (Spielminute der Ankunft) beginnt eine neue Szene."""

    model_config = _STRICT

    type: Literal["enter_location"] = "enter_location"
    location_id: str = Field(min_length=1)
    arrived_at: int = Field(ge=0)


class LearnKnowledge(BaseModel):
    """Die Spielfigur kennt jetzt diese Wissensstufe der Seite, dauerhaft (Entscheidung 64).
    `dc`: Als Folge einer Probe gilt die Stufe nur, wenn das Gesamt sie erreicht."""

    model_config = _STRICT

    type: Literal["learn_knowledge"] = "learn_knowledge"
    page_id: str = Field(min_length=1)
    skill_id: str = Field(min_length=1)
    difficulty: DifficultyId
    dc: int = Field(gt=0)


class CreateNpc(BaseModel):
    """Ein NPC entsteht, mit den Werten, die der Code aus der Vorlage gewürfelt hat."""

    model_config = _STRICT

    type: Literal["create_npc"] = "create_npc"
    npc_id: str = Field(min_length=1)
    template_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    location_id: str = Field(min_length=1)
    attributes: dict[str, int]
    skills: dict[str, int]
    money_pennies: int = Field(ge=0)


class AddFact(BaseModel):
    """Ein weicher Fakt, den die Spielfigur kennt."""

    model_config = _STRICT

    type: Literal["add_fact"] = "add_fact"
    kind: Literal["fact", "rumor"]
    text: str = Field(min_length=1)
    subject: str | None = None
    source_npc_id: str | None = None


class CreateCampaignPage(BaseModel):
    """Eine Kampagnen-Seite mit den Feldern, die der Code aus der Seitenvorlage gesetzt hat."""

    model_config = _STRICT

    type: Literal["create_campaign_page"] = "create_campaign_page"
    page_id: str = Field(min_length=1)
    template: str = Field(min_length=1)
    page_type: str = Field(min_length=1)
    title: str = Field(min_length=1)
    text: str = Field(min_length=1)
    where: str | None = None  # Seite des Orts, an dem sie vorkommt
    frequency: Frequency | None = None
    value_pennies: int | None = Field(default=None, ge=0)
    gather_skill: str | None = None


class PlayerDay(BaseModel):
    """Was der Tageswechsel für eine Spielfigur bringt: Ration gegessen oder nicht, Hunger danach."""

    model_config = _STRICT

    player_id: str = Field(min_length=1)
    ate: bool
    hunger: int = Field(ge=0, le=4)


class DayChange(BaseModel):
    """Mitternacht (Entscheidungen 57 und 101): neuer Tag, sein Wetter, Ration oder Hunger für jede Figur."""

    model_config = _STRICT

    type: Literal["day_change"] = "day_change"
    day: int = Field(ge=2)
    weather_roll: int = Field(ge=1, le=20)
    weather: str = Field(min_length=1)
    players: tuple[PlayerDay, ...] = ()


class Rest(BaseModel):
    """Nach dem Schlaf: verbleibende Erschöpfungsstufen und Zeitpunkt des Aufwachens."""

    model_config = _STRICT

    type: Literal["rest"] = "rest"
    debt: int = Field(ge=0, le=4)
    awake_since: int = Field(ge=0)


class AddScarcity(BaseModel):
    model_config = _STRICT

    type: Literal["add_scarcity"] = "add_scarcity"
    location_page: str = Field(min_length=1)
    page_id: str = Field(min_length=1)
    level: Literal["scarce", "gone"]
    until: int = Field(ge=0)  # Spielminute, ab der sie nicht mehr gilt
    reason: str = Field(min_length=1)


# Was ein Erfolg bewirkt; beim Öffnen festgehalten, beim Wurf angewandt (Entscheidung 61).
RollConsequence = Annotated[AddItems | LearnKnowledge, Field(discriminator="type")]
CONSEQUENCES = TypeAdapter(tuple[RollConsequence, ...])


class NpcRoll(BaseModel):
    """Der Wurf des NPCs bei einer Vergleichsprobe. Sein Gesamt ist der SG (Entscheidung 63)."""

    model_config = _STRICT

    npc_id: str = Field(min_length=1)
    skill_id: str = Field(min_length=1)
    die: int = Field(ge=1, le=20)
    bonus: int
    total: int


class OpenRoll(BaseModel):
    """Eröffnet die offene Probe. Die Werte gelten für den Wurf, genau wie dem Spieler gezeigt.

    `minutes` werden beim Wurf gutgeschrieben, `scene` ist die Szene beim Öffnen (Entscheidungen 58, 59).
    `on_success` sind die Folgen eines Erfolgs, mit allem, was der Code dafür gewürfelt hat (Entscheidung 61).
    """

    model_config = _STRICT

    type: Literal["open_roll"] = "open_roll"
    skill_id: str = Field(min_length=1)
    difficulty: DifficultyId
    dc: int = Field(gt=0)
    bonus: int
    ticks_on_success: int = Field(ge=0)
    reason: str = Field(min_length=1)
    minutes: int = Field(gt=0)
    scene: int = Field(ge=0)
    on_success: tuple[RollConsequence, ...] = ()
    npc_roll: NpcRoll | None = None  # nur bei der Vergleichsprobe


class ClearOpenRoll(BaseModel):
    model_config = _STRICT

    type: Literal["clear_open_roll"] = "clear_open_roll"


class AdjustSkill(BaseModel):
    """Änderung an einem Skill: `points` Skillpunkte dazu (zählen als Aufstiege),
    `ticks` zu den Ticks addiert (kann negativ sein: beim Aufstieg werden Ticks verbraucht)."""

    model_config = _STRICT

    type: Literal["adjust_skill"] = "adjust_skill"
    skill_id: str = Field(min_length=1)
    points: int = Field(ge=0)
    ticks: int


class MarkSkillScene(BaseModel):
    """Der Skill hat in der Szene `scene` Ticks gebracht; dort gibt es keine weiteren (Entscheidung 53)."""

    model_config = _STRICT

    type: Literal["mark_skill_scene"] = "mark_skill_scene"
    skill_id: str = Field(min_length=1)
    scene: int = Field(ge=0)


class GrantAttributePoints(BaseModel):
    model_config = _STRICT

    type: Literal["grant_attribute_points"] = "grant_attribute_points"
    points: int = Field(gt=0)


Effect = Annotated[
    RemoveItems
    | AddItems
    | AddMoney
    | SetQuestStatus
    | AdvanceTime
    | EnterLocation
    | OpenRoll
    | ClearOpenRoll
    | AdjustSkill
    | MarkSkillScene
    | GrantAttributePoints
    | LearnKnowledge
    | CreateNpc
    | AddFact
    | CreateCampaignPage
    | DayChange
    | Rest
    | AddScarcity,
    Field(discriminator="type"),
]
EFFECTS = TypeAdapter(list[Effect])


# --- Ergebnis eines Spielzugs ---

RejectCode = Literal[
    "stale_revision",
    "unknown_player",
    "unknown_quest",
    "quest_not_active",
    "wrong_location",
    "not_enough_items",
    "unknown_skill",
    "roll_pending",
    "no_open_roll",
    "unknown_location",
    "already_there",
    "not_connected",
    "unknown_page",
    "not_here",
    "nothing_to_learn",
    "not_gatherable",
    "unknown_template",
    "unknown_npc",
    "npc_exists",
    "important_figure",
    "page_exists",
    "no_counter_skill",
    "scarce",
]


class CheckResult(BaseModel):
    """Der gewürfelte Wurf einer Probe samt Ausgang. Wird mit dem Spielzug gespeichert."""

    model_config = ConfigDict(frozen=True)

    die: int
    bonus: int
    total: int
    dc: int
    outcome: Outcome
    ticks: int  # Ticks, die diese Probe eingebracht hat
    skill_points: int  # daraus entstandene Skill-Aufstiege


class TurnResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    status: Literal["accepted", "awaiting_roll", "rejected"]
    reject_code: RejectCode | None = None
    message: str | None = None
    effects: list[Effect] = Field(default_factory=list)
    check: CheckResult | None = None
    # Revision der Kampagne nach diesem Spielzug (bei Ablehnung unverändert).
    revision: int
    # True, wenn das gespeicherte Ergebnis zu einer wiederholten request_id erneut ausgegeben wird.
    replayed: bool = False
