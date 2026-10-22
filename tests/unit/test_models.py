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
    EnterLocation,
    GrantAttributePoints,
    MarkSkillScene,
    Move,
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


OPEN_ROLL = {
    "type": "open_roll", "skill_id": "SKILL-101", "difficulty": "DIFF-03", "dc": 12,
    "bonus": 5, "ticks_on_success": 2, "reason": "x", "minutes": 5, "scene": 0,
}


def test_open_roll_example_is_valid():
    assert EFFECTS.validate_python([OPEN_ROLL])[0].minutes == 5


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
        OPEN_ROLL | {"dc": 0},
        OPEN_ROLL | {"difficulty": "DIFF-09"},
        OPEN_ROLL | {"ticks_on_success": -1},
        OPEN_ROLL | {"minutes": 0},
        OPEN_ROLL | {"scene": -1},
        {k: v for k, v in OPEN_ROLL.items() if k != "minutes"},
        {"type": "enter_location", "location_id": "", "arrived_at": 0},
        {"type": "enter_location", "location_id": "x", "arrived_at": -1},
        {"type": "enter_location", "location_id": "x"},
        {"type": "mark_skill_scene", "skill_id": "SKILL-101", "scene": -1},
        {"type": "mark_skill_scene", "skill_id": "SKILL-101"},
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
            skill_id="SKILL-101", difficulty="DIFF-03", dc=12, bonus=5, ticks_on_success=2, reason="x",
            minutes=60, scene=0,
        ),
        ClearOpenRoll(),
        AdjustSkill(skill_id="SKILL-101", points=1, ticks=-2),  # Ticks dürfen negativ sein
        MarkSkillScene(skill_id="SKILL-101", scene=480),
        GrantAttributePoints(points=1),
        EnterLocation(location_id="x", arrived_at=90),
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


def test_attempt_carries_only_skill_difficulty_duration_and_reason():
    action = AttemptAction.model_validate_json(
        '{"skill_id": "SKILL-101", "difficulty": "DIFF-03", "duration": "hour",'
        ' "reason": "Er will durchbrechen"}'
    )
    assert (action.type, action.skill_id, action.difficulty) == ("attempt", "SKILL-101", "DIFF-03")
    assert action.duration == "hour"


@pytest.mark.parametrize(
    "extra",
    [
        {"dc": 5},  # den SG legt der Code fest, nie das Modell (Entscheidung 10)
        {"bonus": 20},
        {"ticks": 8},
        {"minutes": 1},  # die Minuten legt der Code fest (Entscheidung 59)
        {"scene": 0},  # die Szene bestimmt der Code (Entscheidung 53)
        {"new_scene": True},
    ],
)
def test_attempt_does_not_take_numbers_from_the_model(extra):
    data = {"skill_id": "SKILL-101", "difficulty": "DIFF-03", "duration": "hour", "reason": "x"} | extra
    with pytest.raises(ValidationError):
        AttemptAction.model_validate(data)


@pytest.mark.parametrize(
    "data",
    [
        {"skill_id": "101", "difficulty": "DIFF-03", "duration": "hour", "reason": "x"},
        {"skill_id": "SKILL-1", "difficulty": "DIFF-03", "duration": "hour", "reason": "x"},
        {"skill_id": "SKILL-101", "difficulty": "DIFF-08", "duration": "hour", "reason": "x"},
        {"skill_id": "SKILL-101", "difficulty": "leicht", "duration": "hour", "reason": "x"},
        {"skill_id": "SKILL-101", "difficulty": "DIFF-03", "duration": "hour", "reason": ""},
        {"skill_id": "SKILL-101", "difficulty": "DIFF-03", "duration": "hour", "reason": "x" * 201},
        {"skill_id": "SKILL-101", "difficulty": "DIFF-03", "duration": "hour"},
        {"skill_id": "SKILL-101", "difficulty": "DIFF-03", "reason": "x"},  # Dauer fehlt
        {"skill_id": "SKILL-101", "difficulty": "DIFF-03", "duration": "day", "reason": "x"},
        {"skill_id": "SKILL-101", "difficulty": "DIFF-03", "duration": 60, "reason": "x"},
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


# --- Bewegen ---


def test_move_carries_only_the_destination():
    action = ACTIONS.validate_json('{"type": "move", "destination_id": "tavern"}')
    assert isinstance(action, Move)
    assert action.destination_id == "tavern"


@pytest.mark.parametrize(
    "data",
    [
        {"destination_id": ""},
        {},
        {"destination_id": "tavern", "minutes": 1},  # die Reisezeit steht im Inhalt
        {"destination_id": "tavern", "new_scene": False},  # die Szene bestimmt der Code
        {"destination_id": "tavern", "player_id": "other"},
    ],
)
def test_move_rejects_invalid_input(data):
    with pytest.raises(ValidationError):
        Move.model_validate(data)
