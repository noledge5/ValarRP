"""Aktionen (vom Modell angefordert), Effekte (vom Code bestimmt) und Spielzug-Ergebnis."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

_STRICT = ConfigDict(extra="forbid", frozen=True)

DifficultyId = Literal["DIFF-01", "DIFF-02", "DIFF-03", "DIFF-04", "DIFF-05", "DIFF-06", "DIFF-07"]
Outcome = Literal["critical_success", "success", "near_miss", "failure", "fumble"]
# Dauer eines freien Versuchs (Entscheidung 59); die Minuten legt der Code fest.
AttemptDuration = Literal["moment", "hour", "half_day"]


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


class SubmitRoll(BaseModel):
    """Der Spieler tippt das Ergebnis seines analogen W20 ein."""

    model_config = _STRICT

    type: Literal["submit_roll"] = "submit_roll"
    value: int = Field(ge=1, le=20, strict=True)


Action = Annotated[SubmitQuestItems | AttemptAction | SubmitRoll | Move, Field(discriminator="type")]
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


class OpenRoll(BaseModel):
    """Eröffnet die offene Probe. Die Werte gelten für den Wurf, genau wie dem Spieler gezeigt.

    `minutes` werden beim Wurf gutgeschrieben, `scene` ist die Szene beim Öffnen (Entscheidungen 58, 59).
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
    | GrantAttributePoints,
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
