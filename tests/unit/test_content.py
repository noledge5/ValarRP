import pytest
from pydantic import ValidationError

from valar.content import (
    BOOMTOWN,
    CONTENT,
    CRYSTAL_FIELD,
    GUILD_HALL,
    GUILD_SHARD_ORDER,
    TAVERN,
    Content,
    LocationDef,
    QuestDef,
    Route,
)
from valar.models import SubmitQuestItems

VALID = {
    "quest_id": "q",
    "turn_in_location_id": "somewhere",
    "item_id": "thing",
    "quantity": 1,
    "reward_pennies": 1,
}


@pytest.mark.parametrize("field", ["quantity", "reward_pennies"])
@pytest.mark.parametrize("value", [0, -1])
def test_quest_def_rejects_non_positive_numbers(field, value):
    with pytest.raises(ValidationError):
        QuestDef(**(VALID | {field: value}))


def test_content_keys_match_quest_ids():
    for key, quest in CONTENT.quests.items():
        assert key == quest.quest_id


def test_content_gives_every_action_a_positive_duration():
    action_type = SubmitQuestItems.model_fields["type"].default
    assert CONTENT.action_minutes[action_type] > 0


# --- Orte und Verbindungen ---


def content_with(**changes) -> Content:
    return Content(**(dict(CONTENT) | changes))


def test_test_content_is_valid_and_symmetric():
    assert content_with() == CONTENT
    for origin in CONTENT.locations:
        for target, minutes in CONTENT.exits(origin):
            assert CONTENT.route_minutes(target, origin) == minutes  # beide Richtungen gleich lang


def test_route_minutes_and_exits():
    assert CONTENT.route_minutes(BOOMTOWN, CRYSTAL_FIELD) == 90
    assert CONTENT.route_minutes(CRYSTAL_FIELD, BOOMTOWN) == 90
    assert CONTENT.route_minutes(GUILD_HALL, CRYSTAL_FIELD) is None
    assert CONTENT.route_minutes("nowhere", BOOMTOWN) is None
    assert CONTENT.exits(GUILD_HALL) == [(BOOMTOWN, 5)]
    assert CONTENT.exits("nowhere") == []


def test_every_sub_location_has_a_route_to_its_parent():
    for location in CONTENT.locations.values():
        if location.parent_id:
            assert CONTENT.route_minutes(location.location_id, location.parent_id) is not None


A = LocationDef(location_id="a", name="A", description="a")
B = LocationDef(location_id="b", name="B", description="b")


@pytest.mark.parametrize(
    "changes",
    [
        {"routes": (Route(a=BOOMTOWN, b="nowhere", minutes=5),)},
        {"routes": (Route(a=BOOMTOWN, b=BOOMTOWN, minutes=5),)},
        {"routes": CONTENT.routes + (Route(a=GUILD_HALL, b=BOOMTOWN, minutes=7),)},  # doppelt, umgedreht
        {"routes": tuple(r for r in CONTENT.routes if r.b != TAVERN)},  # Unterort ohne Weg zu seinem Ort
        {"locations": CONTENT.locations | {"x": A}},  # Schlüssel passt nicht
        {"locations": CONTENT.locations | {"a": A.model_copy(update={"parent_id": "nowhere"})}},
        {
            "locations": CONTENT.locations
            | {"a": A.model_copy(update={"parent_id": "b"}), "b": B.model_copy(update={"parent_id": "a"})},
            "routes": CONTENT.routes + (Route(a="a", b="b", minutes=1),),
        },  # Kreis
        {"start_minute_of_day": 1440},
        {"start_minute_of_day": -1},
        {"quests": {GUILD_SHARD_ORDER: CONTENT.quests[GUILD_SHARD_ORDER].model_copy(
            update={"turn_in_location_id": "nowhere"})}},
    ],
)
def test_invalid_content_is_refused(changes):
    with pytest.raises(ValidationError):
        content_with(**changes)


@pytest.mark.parametrize("minutes", [0, -5])
def test_routes_need_a_positive_duration(minutes):
    with pytest.raises(ValidationError):
        Route(a="a", b="b", minutes=minutes)
