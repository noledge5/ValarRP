"""Schritt 1h: Tageswechsel mit Folgen und erzählte Knappheit (Entscheidungen 12, 57, 101, 121 bis 123).

Die Nummern folgen dem README. Testdaten: Spielbeginn 8 Uhr, Seed 0. Wetter mit Seed 0: Tag 1 ruhig,
Tag 2 Böenwind (Wahrnehmung −2), Tag 3 Unwetter (Reisezeit × 2).
"""

import math
from pathlib import Path

import pytest

from tests.helpers import (
    CAMPAIGN,
    PLAYER,
    SKILL,
    TEST_WIKI,
    add_campaign,
    add_player,
    assert_only_log_changed,
    attempt,
    campaign_row,
    content_pointing_to,
    dump_state,
    gather,
    move,
    open_roll_row,
    roll,
    set_game_minutes,
    submit,
    write_wiki,
)
from valar import apply, queries, rules, wiki
from valar.content import BOOMTOWN, CONTENT, GUILD_HALL, VILLAGE, Content, Route
from valar.models import AddItems, MarkScarcity, Sleep

DAY = rules.MINUTES_PER_DAY
MIDNIGHT = 16 * 60  # Spielminute der ersten Mitternacht (Start 8 Uhr)


@pytest.fixture
def player(conn):
    """Die Figur aus 1b: Klingenwaffen 30 und STR 14, Bonus +5; ohne Rationen."""
    add_campaign(conn, CAMPAIGN)
    add_player(conn, PLAYER, attributes={"STR": 14, "GES": 8}, skills={SKILL: 30})


def give_rations(conn, count: int, player_id: str = PLAYER) -> None:
    conn.execute("BEGIN")
    apply.apply_effects(conn, CAMPAIGN, player_id, [AddItems(item_id=rules.RATION, quantity=count)])
    conn.execute("COMMIT")


def sleep(conn, request_id: str, revision: int, **overrides):
    return submit(conn, request_id, revision, action=Sleep(), **overrides)


def condition(conn, player_id: str = PLAYER) -> queries.ConditionView:
    return queries.status(conn, campaign_id=CAMPAIGN, player_id=player_id).condition


def rations(conn, player_id: str = PLAYER) -> int:
    row = conn.execute(
        "SELECT quantity FROM inventory WHERE player_id = ? AND item_id = ?", (player_id, rules.RATION)
    ).fetchone()
    return row["quantity"] if row else 0


def day_changes(result) -> list:
    return [effect for effect in result.effects if effect.type == "day_change"]


# --- 1: Schlafen bis zum Morgen ---


def test_1_sleep_lasts_until_six_in_the_morning(conn, player):
    set_game_minutes(conn, 14 * 60)  # 22 Uhr
    give_rations(conn, 1)

    result = sleep(conn, "s1", 0)

    assert result.effects[0].minutes == 8 * 60
    assert campaign_row(conn)["game_minutes"] == 22 * 60  # 6 Uhr an Tag 2
    assert queries.status(conn, campaign_id=CAMPAIGN, player_id=PLAYER).clock.time == "06:00"


def test_1_sleep_lasts_at_most_twelve_hours(conn, player):
    result = sleep(conn, "s1", 0)  # 8 Uhr: bis 6 Uhr wären es 22 Stunden
    assert result.effects[0].minutes == rules.MAX_SLEEP_MINUTES


def test_1_six_hours_of_sleep_remove_one_stage(conn, player):
    give_rations(conn, 3)
    set_game_minutes(conn, 28 * 60)  # 28 Stunden wach: Übermüdet (Stufe 2), 12 Uhr an Tag 2
    assert condition(conn).exhaustion == 2

    sleep(conn, "s1", 0)  # 12 Stunden: zwei Stufen weniger

    shown = condition(conn)
    assert (shown.exhaustion, shown.awake_hours) == (0, 0)


# --- 2: Je Mitternacht genau einmal: Ration oder Hunger, Wetter ---


def test_2_at_midnight_a_ration_is_eaten(conn, player):
    give_rations(conn, 2)
    set_game_minutes(conn, MIDNIGHT - 30)

    result = attempt(conn, "a1", 0, duration="hour")
    result = roll(conn, "a2", 1, value=20)

    (change,) = day_changes(result)
    assert (change.day, change.weather) == (2, "Heulender Böenwind")
    assert change.players[0].model_dump() == {"player_id": PLAYER, "ate": True, "hunger": 0}
    assert rations(conn) == 1
    assert condition(conn).weather == "Heulender Böenwind"


def test_2_without_a_ration_hunger_grows_and_with_one_it_shrinks(conn, player):
    for day in range(3):
        set_game_minutes(conn, MIDNIGHT + day * DAY - 5)
        move(conn, f"m{day}", 2 * day, BOOMTOWN)
        move(conn, f"n{day}", 2 * day + 1, GUILD_HALL)
    assert (condition(conn).hunger, condition(conn).hunger_name) == (3, "Ausgemergelt")

    give_rations(conn, 1)
    set_game_minutes(conn, MIDNIGHT + 3 * DAY - 5)
    move(conn, "m3", 6, BOOMTOWN)
    assert condition(conn).hunger == 2


def test_2_hunger_stops_at_stage_four(conn, player):
    conn.execute("UPDATE player SET hunger = 4")
    set_game_minutes(conn, MIDNIGHT - 5)
    move(conn, "m1", 0, BOOMTOWN)
    assert condition(conn).hunger == 4


def test_2_every_player_of_the_campaign_has_their_midnight(conn, player):
    add_player(conn, "player-2", location_id=BOOMTOWN)
    give_rations(conn, 1, "player-2")
    set_game_minutes(conn, MIDNIGHT - 5)

    result = move(conn, "m1", 0, BOOMTOWN)

    (change,) = day_changes(result)
    assert [(p.player_id, p.ate, p.hunger) for p in change.players] == [(PLAYER, False, 1), ("player-2", True, 0)]


def test_2_no_midnight_no_day_change(conn, player):
    result = move(conn, "m1", 0, BOOMTOWN)
    assert day_changes(result) == []


# --- 3: Zwei Mitternächte in einem Zug ---


def test_3_a_turn_over_two_midnights_triggers_both(conn, player):
    long_road = Content(
        **(dict(CONTENT) | {"routes": (*CONTENT.routes[:3], Route(a=BOOMTOWN, b=VILLAGE, minutes=30 * 60), CONTENT.routes[4])})
    )
    add_player(conn, "wanderer", location_id=BOOMTOWN)
    give_rations(conn, 1, "wanderer")
    set_game_minutes(conn, MIDNIGHT - 60)

    result = move(conn, "m1", 0, VILLAGE, player_id="wanderer", content=long_road)

    assert [c.day for c in day_changes(result)] == [2, 3]
    wanderer = [next(p for p in c.players if p.player_id == "wanderer") for c in day_changes(result)]
    assert [(p.ate, p.hunger) for p in wanderer] == [(True, 0), (False, 1)]
    assert rations(conn, "wanderer") == 0


# --- 4: Erschöpfung ohne Schlaf ---


@pytest.mark.parametrize("hours, stage", [(17, 0), (18, 1), (24, 2), (36, 3), (48, 4), (80, 4)])
def test_4_exhaustion_grows_in_stages_without_sleep(conn, player, hours, stage):
    conn.execute("UPDATE player SET hunger = 0")
    set_game_minutes(conn, hours * 60)
    shown = condition(conn)
    assert (shown.exhaustion, shown.exhaustion_name) == (stage, rules.EXHAUSTION_NAMES[stage])


# --- 5: Malus im Bonus beim Öffnen ---


def test_5_hunger_and_exhaustion_lower_the_bonus_at_opening(conn, player):
    conn.execute("UPDATE player SET hunger = 2")  # Mager: −2
    set_game_minutes(conn, 20 * 60)  # 20 Stunden wach: Müde −1; Tag 2 (Böenwind, kein Malus auf Klingen)

    attempt(conn, "a1", 0)

    assert open_roll_row(conn)["bonus"] == 5 - 2 - 1
    assert condition(conn).malus == -3


def test_5_weather_lowers_perception_and_slows_travel(conn, player):
    set_game_minutes(conn, MIDNIGHT + 60)  # Tag 2: Böenwind, Wahrnehmung −2
    conn.execute("UPDATE player SET awake_since = ?", (MIDNIGHT,))
    attempt(conn, "a1", 0, skill_id="SKILL-405")
    assert open_roll_row(conn)["bonus"] == -2  # Wahrnehmung 0, WEI 10
    roll(conn, "a2", 1, value=1)

    give_rations(conn, 2)
    set_game_minutes(conn, MIDNIGHT + DAY + 60)  # Tag 3: Unwetter, Reisezeit × 2
    result = move(conn, "m1", 2, BOOMTOWN)
    assert result.effects[0].minutes == math.ceil(5 * 2.0)


def test_5_the_first_day_is_calm():
    assert rules.weather_of_day(123, 1) == rules.CALM
    assert all(rules.weather_of_day(7, day) == rules.weather_of_day(7, day) for day in range(2, 9))


# --- 6: Erzählte Knappheit ---

GATHER_CONTENT = content_pointing_to(boomtown="testdorf")


@pytest.fixture
def herbs(conn, tmp_path: Path):
    write_wiki(tmp_path / "w", TEST_WIKI)
    test_wiki = wiki.load(tmp_path / "w")
    add_campaign(conn, CAMPAIGN)
    add_player(conn, PLAYER)

    def play(step, *args, **kwargs):
        return step(conn, *args, content=GATHER_CONTENT, wiki=test_wiki, **kwargs)

    return play


def scarcity(page_id: str = "sumpfkraut", level: str = "scarce", duration: str = "day") -> MarkScarcity:
    return MarkScarcity(page_id=page_id, level=level, duration=duration, reason="Die Wiese ist abgegrast.")


def test_6_scarce_makes_gathering_one_stage_harder(conn, herbs):
    result = herbs(submit, "k1", 0, action=scarcity())
    assert result.status == "accepted" and campaign_row(conn)["game_minutes"] == 0  # kostet keine Zeit

    herbs(gather, "g1", 1, "sumpfkraut")

    assert open_roll_row(conn)["difficulty"] == "DIFF-03"  # häufig wäre DIFF-02


def test_6_gone_rejects_gathering_until_it_expires(conn, herbs):
    herbs(submit, "k1", 0, action=scarcity(level="gone"))
    before = dump_state(conn)

    assert herbs(gather, "g1", 1, "sumpfkraut").reject_code == "scarce"
    assert_only_log_changed(before, dump_state(conn))

    set_game_minutes(conn, DAY)  # ein Tag später: vorbei
    herbs(gather, "g2", 1, "sumpfkraut")
    assert open_roll_row(conn)["difficulty"] == "DIFF-02"


def test_6_the_stronger_scarcity_counts_and_nothing_gets_easier(conn, herbs):
    herbs(submit, "k1", 0, action=scarcity(level="gone", duration="week"))
    herbs(submit, "k2", 1, action=scarcity(level="scarce", duration="month"))
    assert herbs(gather, "g1", 2, "sumpfkraut").reject_code == "scarce"

    assert set(MarkScarcity.model_fields) == {"type", "page_id", "level", "duration", "reason"}
    assert set(MarkScarcity.model_fields["level"].annotation.__args__) == {"scarce", "gone"}  # kein „reichlich“


def test_6_scarcity_only_for_things_here(conn, herbs):
    before = dump_state(conn)
    assert herbs(submit, "k1", 0, action=scarcity(page_id="nirgendwo")).reject_code == "unknown_page"
    assert herbs(submit, "k2", 0, action=scarcity(page_id="koehler")).reject_code == "not_here"
    log = {"turn", "sqlite_sequence"}
    after = dump_state(conn)
    assert {t: v for t, v in after.items() if t not in log} == {t: v for t, v in before.items() if t not in log}
