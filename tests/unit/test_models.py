import pytest
from pydantic import ValidationError

from valar.models import (
    EFFECTS,
    AddItems,
    AddMoney,
    AdvanceTime,
    RemoveItems,
    SetQuestStatus,
    SubmitQuestItems,
    TurnResult,
)


def test_action_accepts_only_the_quest_id():
    action = SubmitQuestItems.model_validate_json('{"quest_id": "q1"}')
    assert action.quest_id == "q1"
    assert action.type == "submit_quest_items"


@pytest.mark.parametrize(
    "data",
    [
        {"quest_id": "q1", "reward_pennies": 1000},  # Belohnungen kommen nie aus der Aktion
        {"quest_id": "q1", "quantity": 1},  # Mengen auch nicht
        {"quest_id": "q1", "type": "add_money"},
        {"quest_id": ""},
        {"quest_id": 5},
        {},
    ],
)
def test_action_rejects_invalid_input(data):
    with pytest.raises(ValidationError):
        SubmitQuestItems.model_validate(data)


def test_action_is_immutable():
    action = SubmitQuestItems(quest_id="q1")
    with pytest.raises(ValidationError):
        action.quest_id = "q2"


@pytest.mark.parametrize(
    "data",
    [
        {"type": "remove_items", "item_id": "x", "quantity": 0},
        {"type": "remove_items", "item_id": "x", "quantity": -1},
        {"type": "add_items", "item_id": "x", "quantity": 0},
        {"type": "add_money", "pennies": 0},
        {"type": "add_money", "pennies": -5},
        {"type": "advance_time", "minutes": 0},
        {"type": "remove_items", "item_id": "", "quantity": 1},
        {"type": "set_quest_status", "quest_id": "q", "status": "failed"},
        {"type": "set_quest_status", "quest_id": "", "status": "active"},
        {"type": "add_money", "pennies": 5, "extra": 1},
        {"type": "grant_gold", "pennies": 5},
        {"pennies": 5},
    ],
)
def test_effects_reject_invalid_input(data):
    with pytest.raises(ValidationError):
        EFFECTS.validate_python([data])


def test_effects_survive_a_json_round_trip():
    effects = [
        RemoveItems(item_id="x", quantity=2),
        AddItems(item_id="x", quantity=1),
        AddMoney(pennies=100),
        SetQuestStatus(quest_id="q", status="completed"),
        AdvanceTime(minutes=10),
    ]
    assert EFFECTS.validate_json(EFFECTS.dump_json(effects)) == effects


def test_turn_result_defaults_and_vocabulary():
    result = TurnResult(request_id="r", status="accepted", revision=1)
    assert (result.effects, result.reject_code, result.message, result.replayed) == ([], None, None, False)

    with pytest.raises(ValidationError):
        TurnResult(request_id="r", status="rejected", reject_code="made_up", revision=0)
    with pytest.raises(ValidationError):
        TurnResult(request_id="r", status="maybe", revision=0)
    with pytest.raises(ValidationError):
        result.revision = 5
