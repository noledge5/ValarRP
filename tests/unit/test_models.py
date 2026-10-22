import pytest
from pydantic import ValidationError

from valar.models import (
    ACTIONS,
    EFFECTS,
    AddItems,
    AddMoney,
    AdjustSkill,
    AdvanceTime,
    AttemptAction,
    CheckResult,
    ClearOpenRoll,
    GrantAttributePoints,
    OpenRoll,
    RemoveItems,
    SetQuestStatus,
    SubmitQuestItems,
    SubmitRoll,
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
        {"type": "open_roll", "skill_id": "SKILL-101", "difficulty": "DIFF-03", "dc": 0,
         "bonus": 5, "ticks_on_success": 2, "reason": "x"},
        {"type": "open_roll", "skill_id": "SKILL-101", "difficulty": "DIFF-09", "dc": 12,
         "bonus": 5, "ticks_on_success": 2, "reason": "x"},
        {"type": "open_roll", "skill_id": "SKILL-101", "difficulty": "DIFF-03", "dc": 12,
         "bonus": 5, "ticks_on_success": -1, "reason": "x"},
        {"type": "adjust_skill", "skill_id": "SKILL-101", "points": -1, "ticks": 0},
        {"type": "adjust_skill", "skill_id": "SKILL-101", "points": 1},
        {"type": "grant_attribute_points", "points": 0},
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
        OpenRoll(
            skill_id="SKILL-101", difficulty="DIFF-03", dc=12, bonus=5, ticks_on_success=2, reason="x"
        ),
        ClearOpenRoll(),
        AdjustSkill(skill_id="SKILL-101", points=1, ticks=-2),  # Ticks dürfen negativ sein
        GrantAttributePoints(points=1),
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


# --- Probe: Aktionen ---


def test_attempt_carries_only_skill_difficulty_and_reason():
    action = AttemptAction.model_validate_json(
        '{"skill_id": "SKILL-101", "difficulty": "DIFF-03", "reason": "Er will durchbrechen"}'
    )
    assert (action.type, action.skill_id, action.difficulty) == ("attempt", "SKILL-101", "DIFF-03")


@pytest.mark.parametrize(
    "extra",
    [
        {"dc": 5},  # den SG legt der Code fest, nie das Modell (Entscheidung 10)
        {"bonus": 20},
        {"ticks": 8},
        {"duration": "hour"},
    ],
)
def test_attempt_does_not_take_numbers_from_the_model(extra):
    data = {"skill_id": "SKILL-101", "difficulty": "DIFF-03", "reason": "x"} | extra
    with pytest.raises(ValidationError):
        AttemptAction.model_validate(data)


@pytest.mark.parametrize(
    "data",
    [
        {"skill_id": "101", "difficulty": "DIFF-03", "reason": "x"},
        {"skill_id": "SKILL-1", "difficulty": "DIFF-03", "reason": "x"},
        {"skill_id": "SKILL-101", "difficulty": "DIFF-08", "reason": "x"},
        {"skill_id": "SKILL-101", "difficulty": "leicht", "reason": "x"},
        {"skill_id": "SKILL-101", "difficulty": "DIFF-03", "reason": ""},
        {"skill_id": "SKILL-101", "difficulty": "DIFF-03", "reason": "x" * 201},
        {"skill_id": "SKILL-101", "difficulty": "DIFF-03"},
    ],
)
def test_attempt_rejects_invalid_input(data):
    with pytest.raises(ValidationError):
        AttemptAction.model_validate(data)


@pytest.mark.parametrize("value", [1, 10, 20])
def test_roll_accepts_a_d20_result(value):
    assert SubmitRoll(value=value).value == value


@pytest.mark.parametrize("value", [0, -1, 21, 100, "7", 7.0, 7.5, True, None])
def test_roll_rejects_everything_but_an_integer_from_1_to_20(value):
    with pytest.raises(ValidationError):
        SubmitRoll.model_validate({"value": value})


def test_actions_are_told_apart_by_their_type():
    assert isinstance(ACTIONS.validate_json('{"type": "submit_roll", "value": 7}'), SubmitRoll)
    assert isinstance(ACTIONS.validate_json('{"type": "submit_quest_items", "quest_id": "q"}'), SubmitQuestItems)
    with pytest.raises(ValidationError):
        ACTIONS.validate_json('{"type": "cast_fireball"}')
    with pytest.raises(ValidationError):
        ACTIONS.validate_json('{"value": 7}')


def test_turn_result_keeps_the_check_through_json():
    check = CheckResult(die=7, bonus=5, total=12, dc=12, outcome="success", ticks=2, skill_points=0)
    result = TurnResult(request_id="r", status="accepted", check=check, revision=2)
    assert TurnResult.model_validate_json(result.model_dump_json()) == result
    awaiting = TurnResult(request_id="r", status="awaiting_roll", revision=1)
    assert awaiting.check is None
    with pytest.raises(ValidationError):
        CheckResult(die=7, bonus=5, total=12, dc=12, outcome="great", ticks=2, skill_points=0)
