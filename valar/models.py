"""Aktionen (vom Modell angefordert), Effekte (vom Code bestimmt) und Spielzug-Ergebnis."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

_STRICT = ConfigDict(extra="forbid", frozen=True)


# --- Aktionen: das Modell fordert sie an, der Code bestimmt die Folgen ---


class SubmitQuestItems(BaseModel):
    """Gegenstände für einen Auftrag abgeben.

    Nennt nur den Auftrag. Gegenstand, Menge, Ort und Belohnung stehen im Inhalt,
    nie in der Aktion.
    """

    model_config = _STRICT

    type: Literal["submit_quest_items"] = "submit_quest_items"
    quest_id: str = Field(min_length=1)


# Später eine Union aller Aktionen (Unterscheidung über `type`).
Action = SubmitQuestItems


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


Effect = Annotated[
    RemoveItems | AddItems | AddMoney | SetQuestStatus | AdvanceTime,
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
]


class TurnResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    status: Literal["accepted", "rejected"]
    reject_code: RejectCode | None = None
    message: str | None = None
    effects: list[Effect] = Field(default_factory=list)
    # Revision der Kampagne nach diesem Spielzug (bei Ablehnung unverändert).
    revision: int
    # True, wenn das gespeicherte Ergebnis zu einer wiederholten request_id erneut ausgegeben wird.
    replayed: bool = False
