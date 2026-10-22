import pytest

from tests.helpers import (
    CAMPAIGN,
    PLAYER,
    SKILL,
    START_MONEY,
    add_campaign,
    add_player,
    attempt,
    dump_state,
    move,
    roll,
    set_game_minutes,
)
from valar import queries
from valar.content import BOOMTOWN, CRYSTAL_SHARD, GUILD_HALL, GUILD_SHARD_ORDER
from valar.turns import UnknownCampaign


def look(conn, **kwargs):
    return queries.look(conn, campaign_id=CAMPAIGN, player_id=PLAYER, **kwargs)


def status(conn, **kwargs):
    return queries.status(conn, campaign_id=CAMPAIGN, player_id=PLAYER, **kwargs)


def test_look_shows_place_exits_and_clock(conn, scenario):
    seen = look(conn)
    assert (seen.location_id, seen.name, seen.parent_id) == (GUILD_HALL, "Zunfthaus", BOOMTOWN)
    assert seen.description
    assert [(e.location_id, e.name, e.minutes) for e in seen.exits] == [(BOOMTOWN, "Sturmhagen", 5)]
    assert (seen.clock.day, seen.clock.time, seen.clock.game_minutes) == (1, "08:00", 0)
    assert seen.others_here == []


def test_look_lists_other_players_at_the_same_place_only(conn, scenario):
    add_player(conn, "player-3")
    add_player(conn, "player-2")
    add_player(conn, "far-away", location_id=BOOMTOWN)
    assert look(conn).others_here == ["player-2", "player-3"]


def test_look_at_a_location_outside_the_content(conn):
    add_campaign(conn, CAMPAIGN)
    add_player(conn, PLAYER, location_id="street")
    seen = look(conn)
    assert (seen.location_id, seen.name, seen.exits) == ("street", "street", [])


def test_status_shows_the_player(conn, scenario):
    shown = status(conn)
    assert (shown.revision, shown.player_id, shown.location_name) == (0, PLAYER, "Zunfthaus")
    assert shown.money_pennies == START_MONEY
    assert shown.inventory == {CRYSTAL_SHARD: 5}
    assert shown.quests == {GUILD_SHARD_ORDER: "active"}
    assert shown.attributes == {"STR": 10, "GES": 10, "KON": 10, "INT": 10, "WEI": 10, "CHA": 10}
    assert (shown.level, shown.attribute_points, shown.skills, shown.open_roll) == (1, 0, [], None)


def test_status_shows_skills_scene_and_open_roll(conn, roll_scenario):
    attempt(conn, "r1", 0)
    roll(conn, "r2", 1, value=7)  # 2 Ticks, Szene verbraucht
    attempt(conn, "r3", 2, duration="hour")

    shown = status(conn)

    assert shown.revision == 3
    assert [(s.skill_id, s.name, s.value, s.ticks, s.ticked_this_scene) for s in shown.skills] == [
        (SKILL, "Klingenwaffen", 30, 2, True)
    ]
    assert shown.open_roll.model_dump() == {
        "player_id": PLAYER,
        "skill_id": SKILL,
        "difficulty": "DIFF-03",
        "dc": 12,
        "bonus": 5,
        "ticks_on_success": 0,
        "reason": shown.open_roll.reason,
        "minutes": 60,
    }


def test_status_follows_scene_and_clock(conn, roll_scenario):
    move(conn, "r1", 0, destination_id=BOOMTOWN)
    assert (status(conn).scene_start, status(conn).clock.time) == (5, "08:05")
    set_game_minutes(conn, 960 + 1440 + 75)
    shown = status(conn)
    assert (shown.clock.day, shown.clock.time, shown.scene_start) == (3, "01:15", 960 + 1440)


@pytest.mark.parametrize("query", [queries.look, queries.status])
def test_queries_never_write_and_leave_no_transaction(conn, roll_scenario, query):
    before = dump_state(conn)
    query(conn, campaign_id=CAMPAIGN, player_id=PLAYER)
    assert dump_state(conn) == before
    assert not conn.in_transaction
    conn.execute("UPDATE player SET money_pennies = 1")  # Nur-Lese-Modus ist wieder aus


@pytest.mark.parametrize("query", [queries.look, queries.status])
def test_unknown_campaign_or_player_is_an_error(conn, scenario, query):
    with pytest.raises(UnknownCampaign):
        query(conn, campaign_id="ghost", player_id=PLAYER)
    with pytest.raises(queries.UnknownPlayer):
        query(conn, campaign_id=CAMPAIGN, player_id="ghost")
    assert not conn.in_transaction
