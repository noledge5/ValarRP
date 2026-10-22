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


A = LocationDef(location_id="a", name="A", description="a", x=0, y=0)
B = LocationDef(location_id="b", name="B", description="b", x=100, y=0)


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


# --- Koordinaten und Reisezeit ---


def test_route_time_is_calculated_or_fixed():
    content = content_with(
        routes=(
            Route(a=BOOMTOWN, b=GUILD_HALL),
            Route(a=BOOMTOWN, b=TAVERN, minutes=30),
            Route(a=BOOMTOWN, b=CRYSTAL_FIELD, kind="rough", waypoints=((20_000, 26_000),)),
        )
    )
    assert content.route_minutes(GUILD_HALL, BOOMTOWN) == 5
    assert content.route_minutes(TAVERN, BOOMTOWN) == 30
    route = content.route_between(CRYSTAL_FIELD, BOOMTOWN)  # Reihenfolge egal
    assert content.route_points(route) == [(20_000, 20_000), (20_000, 26_000), (24_500, 26_000)]
    assert (content.route_length(route), content.route_time(route)) == (10_500, 252)
    assert content.route_between(GUILD_HALL, TAVERN) is None


@pytest.mark.parametrize(
    "data",
    [
        {"x": 1_000_001, "y": 0},
        {"x": 0, "y": -1_000_001},
        {"x": 1.5, "y": 0},
        {"x": "zehn", "y": 0},
        {"y": 0},
    ],
)
def test_location_points_are_whole_meters_on_the_map(data):
    with pytest.raises(ValidationError):
        LocationDef(location_id="a", name="A", description="a", **data)


@pytest.mark.parametrize(
    "data",
    [
        {"kind": "flight"},
        {"minutes": 0},
        {"waypoints": ((0,),)},
        {"waypoints": ((2_000_000, 0),)},
    ],
)
def test_invalid_routes_are_refused(data):
    with pytest.raises(ValidationError):
        Route(a="a", b="b", **data)


# --- Testfigur ---


@pytest.mark.parametrize(
    "change",
    [{"location_id": "nowhere"}, {"active_quests": ("nope",)}, {"items": {"crystal_shard": 0}}, {"money_pennies": -1}],
)
def test_invalid_start_player_is_refused(change):
    with pytest.raises(ValidationError):
        content_with(start_player=CONTENT.start_player.model_dump() | change)
