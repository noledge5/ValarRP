"""Schritt 1c: Orte, Bewegen, Szene, Dauer freier Versuche.

Ausgangslage (roll_scenario): Die Figur steht im Zunfthaus, Spielbeginn 8:00 Uhr (Spielminute 0).
Bonus +5, gegen SG 12 (DIFF-03) reicht eine 7 und bringt 2 Ticks. Reine Testdaten.
"""

import pytest

from tests.helpers import (
    CAMPAIGN,
    PLAYER,
    SKILL,
    add_player,
    assert_only_log_changed,
    attempt,
    campaign_row,
    dump_state,
    last_turn,
    location,
    move,
    open_roll_row,
    roll,
    set_game_minutes,
    skill_row,
    turn_count,
)
from valar import apply, db, queries
from valar.content import BOOMTOWN, CONTENT, CRYSTAL_FIELD, GUILD_HALL, TAVERN, VILLAGE
from valar.models import ACTIONS, AdvanceTime, EnterLocation

SUCCESS = 7  # 7 + 5 = 12 = SG von DIFF-03
OTHER_SKILL = "SKILL-405"  # Wahrnehmung


def scene_of(conn) -> int:
    return queries.status(conn, campaign_id=CAMPAIGN, player_id=PLAYER).scene_start


def ticks_shown(result) -> int:
    """Mögliche Ticks, wie sie beim Öffnen gezeigt werden."""
    assert result.status == "awaiting_roll"
    return result.effects[0].ticks_on_success


def success_in(conn, request_id: str, revision: int, *, value: int = SUCCESS, player_id: str = PLAYER, **kwargs):
    """Versuch und gelungener Wurf. Gibt (Öffnen, Wurf) zurück; die Revision steigt um 2."""
    opened = attempt(conn, f"{request_id}-a", revision, player_id=player_id, **kwargs)
    return opened, roll(conn, f"{request_id}-b", revision + 1, value=value, player_id=player_id)


# --- 1: Bewegen zu einem verbundenen Ort ---


def test_1_moving_to_a_connected_location(conn, roll_scenario):
    result = move(conn, "req-1", expected_revision=0, destination_id=BOOMTOWN)

    assert result.status == "accepted"
    assert result.revision == 1
    assert result.effects == [AdvanceTime(minutes=5), EnterLocation(location_id=BOOMTOWN, arrived_at=5)]
    assert location(conn) == BOOMTOWN
    assert tuple(campaign_row(conn)) == (1, 5)

    longer = move(conn, "req-1b", expected_revision=1, destination_id=CRYSTAL_FIELD)
    assert longer.status == "accepted"
    assert tuple(campaign_row(conn)) == (2, 5 + 90)  # Reisezeit aus dem Inhalt


# --- 2: Ablehnungen ---


@pytest.mark.parametrize(
    "destination, code",
    [
        ("atlantis", "unknown_location"),
        (CRYSTAL_FIELD, "not_connected"),  # vom Zunfthaus nur über die Bretterstadt
        (TAVERN, "not_connected"),  # Nachbar-Unterort, aber keine direkte Verbindung
        (GUILD_HALL, "already_there"),
    ],
)
def test_2_unknown_unconnected_or_own_location_is_rejected(conn, roll_scenario, destination, code):
    before = dump_state(conn)

    result = move(conn, "req-2", expected_revision=0, destination_id=destination)

    assert (result.status, result.reject_code, result.revision) == ("rejected", code, 0)
    assert_only_log_changed(before, dump_state(conn))


# --- 3: Ort und Unterort ---


def test_3_location_and_sub_location_work_like_any_connection(conn, roll_scenario):
    assert CONTENT.locations[GUILD_HALL].parent_id == BOOMTOWN

    out = move(conn, "req-3a", expected_revision=0, destination_id=BOOMTOWN)
    into = move(conn, "req-3b", expected_revision=1, destination_id=TAVERN)

    assert (out.status, into.status) == ("accepted", "accepted")
    assert location(conn) == TAVERN
    assert campaign_row(conn)["game_minutes"] == 5 + 5  # beide Dauern aus dem Inhalt


# --- 4: Neue Szene bei Ortswechsel und um Mitternacht ---


def test_4_moving_starts_a_new_scene(conn, roll_scenario):
    first = scene_of(conn)
    move(conn, "req-4a", expected_revision=0, destination_id=BOOMTOWN)
    assert scene_of(conn) == 5 != first

    move(conn, "req-4b", expected_revision=1, destination_id=GUILD_HALL)  # zurück an denselben Ort
    assert scene_of(conn) == 10  # trotzdem eine neue Szene


def test_4b_midnight_starts_a_new_scene_without_moving(conn, roll_scenario):
    set_game_minutes(conn, 16 * 60 - 1)  # 23:59 an Tag 1
    before_midnight = scene_of(conn)
    set_game_minutes(conn, 16 * 60)  # 00:00 an Tag 2

    assert scene_of(conn) == 16 * 60 != before_midnight
    assert queries.status(conn, campaign_id=CAMPAIGN, player_id=PLAYER).clock.day == 2


def test_4c_a_journey_over_midnight_starts_its_scene_at_the_arrival(conn, roll_scenario):
    move(conn, "req-4a", expected_revision=0, destination_id=BOOMTOWN)
    set_game_minutes(conn, 16 * 60 - 60)  # 23:00
    move(conn, "req-4b", expected_revision=1, destination_id=VILLAGE)  # 4 Stunden, Ankunft 3:00

    status = queries.status(conn, campaign_id=CAMPAIGN, player_id=PLAYER)
    assert (status.clock.day, status.clock.time) == (2, "03:00")
    assert status.scene_start == 16 * 60 - 60 + 240


# --- 5: Höchstens einmal Ticks je Skill und Szene ---


def test_5_second_success_in_the_same_skill_and_scene_gives_no_ticks(conn, roll_scenario):
    opened, first = success_in(conn, "req-5a", 0)
    assert (ticks_shown(opened), first.check.ticks) == (2, 2)

    opened, second = success_in(conn, "req-5b", 2)

    assert ticks_shown(opened) == 0  # schon beim Öffnen so gezeigt
    assert second.check.outcome == "success"
    assert second.check.ticks == 0
    assert skill_row(conn)[:2] == (30, 2)


def test_5b_another_skill_still_gives_ticks(conn, roll_scenario):
    success_in(conn, "req-5a", 0)

    # Wahrnehmung 0 und WEI 10: Bonus 0, gegen SG 12 also eine 12.
    opened, result = success_in(conn, "req-5b", 2, skill_id=OTHER_SKILL, value=12)

    assert result.check.outcome == "success"
    assert (ticks_shown(opened), result.check.ticks) == (2, 2)


def test_5c_a_new_scene_gives_ticks_again(conn, roll_scenario):
    success_in(conn, "req-5a", 0)
    move(conn, "req-5m", expected_revision=2, destination_id=BOOMTOWN)

    opened, result = success_in(conn, "req-5b", 3)

    assert (ticks_shown(opened), result.check.ticks) == (2, 2)
    assert skill_row(conn)[:2] == (30, 4)


def test_5d_midnight_gives_ticks_again(conn, roll_scenario):
    set_game_minutes(conn, 16 * 60 - 30)  # 23:30
    success_in(conn, "req-5a", 0)
    set_game_minutes(conn, 16 * 60)  # Mitternacht, ohne Ortswechsel

    opened, result = success_in(conn, "req-5b", 2)

    assert result.check.ticks == ticks_shown(opened) == 2


@pytest.mark.parametrize("die", [1, 4, 6])  # Patzer, Fehlschlag, knapp daneben
def test_5e_a_failure_does_not_use_up_the_scene(conn, roll_scenario, die):
    attempt(conn, "req-5a", 0)
    failed = roll(conn, "req-5b", 1, value=die)
    assert failed.check.ticks == 0

    opened, result = success_in(conn, "req-5c", 2)

    assert (ticks_shown(opened), result.check.ticks) == (2, 2)


def test_5f_routine_does_not_use_up_the_scene(conn, roll_scenario):
    success_in(conn, "req-5a", 0, difficulty="DIFF-01")  # Routine: keine Ticks

    opened, result = success_in(conn, "req-5b", 2)

    assert (ticks_shown(opened), result.check.ticks) == (2, 2)


def test_5g_the_scene_at_opening_counts(conn, roll_scenario):
    """Geöffnet vor Mitternacht, gewürfelt danach: Die Ticks zählen für die alte Szene."""
    set_game_minutes(conn, 16 * 60 - 1)  # 23:59
    opened = attempt(conn, "req-5a", 0, duration="hour")
    assert ticks_shown(opened) == 2
    roll(conn, "req-5b", 1, value=SUCCESS)  # 1 Stunde später: 0:59 an Tag 2

    # Die neue Szene ist noch unverbraucht.
    opened, result = success_in(conn, "req-5c", 2)
    assert (ticks_shown(opened), result.check.ticks) == (2, 2)


def test_5h_scene_rule_is_per_player(conn, roll_scenario):
    add_player(conn, "player-2", location_id=GUILD_HALL, attributes={"STR": 14}, skills={SKILL: 30})
    success_in(conn, "req-5a", 0)

    opened, result = success_in(conn, "req-5b", 2, player_id="player-2")

    assert (ticks_shown(opened), result.check.ticks) == (2, 2)


# --- 6: Dauer freier Versuche ---


@pytest.mark.parametrize("duration, minutes", [("moment", 5), ("hour", 60), ("half_day", 360)])
@pytest.mark.parametrize("die", [SUCCESS, 1])  # die Zeit vergeht auch bei einem Patzer
def test_6_the_roll_credits_the_duration_of_the_attempt(conn, roll_scenario, duration, minutes, die):
    opened = attempt(conn, "req-6a", 0, duration=duration)
    assert opened.effects[0].minutes == minutes  # beim Öffnen gespeichert und gezeigt
    assert campaign_row(conn)["game_minutes"] == 0  # das Öffnen kostet keine Zeit

    result = roll(conn, "req-6b", 1, value=die)

    assert AdvanceTime(minutes=minutes) in result.effects
    assert campaign_row(conn)["game_minutes"] == minutes


def test_6b_an_attempt_over_midnight_ends_the_scene_afterwards(conn, roll_scenario):
    set_game_minutes(conn, 16 * 60 - 120)  # 22:00
    attempt(conn, "req-6a", 0, duration="half_day")
    scene_before = scene_of(conn)

    roll(conn, "req-6b", 1, value=SUCCESS)

    status = queries.status(conn, campaign_id=CAMPAIGN, player_id=PLAYER)
    assert (status.clock.day, status.clock.time) == (2, "04:00")
    assert status.scene_start == 16 * 60 != scene_before


# --- 7: Szene oder Minuten in der Aktion ---


@pytest.mark.parametrize(
    "raw",
    [
        '{"type": "move", "destination_id": "boomtown", "minutes": 1}',
        '{"type": "move", "destination_id": "boomtown", "scene": 0}',
        '{"type": "attempt", "skill_id": "SKILL-101", "difficulty": "DIFF-03", "duration": "hour",'
        ' "reason": "x", "minutes": 1}',
        '{"type": "attempt", "skill_id": "SKILL-101", "difficulty": "DIFF-03", "duration": "hour",'
        ' "reason": "x", "scene": 0}',
        '{"type": "attempt", "skill_id": "SKILL-101", "difficulty": "DIFF-03", "duration": 1,'
        ' "reason": "x"}',
    ],
)
def test_7_scene_or_minutes_in_the_action_are_refused(raw):
    with pytest.raises(ValueError):  # ValidationError, bevor ein Spielzug entsteht
        ACTIONS.validate_json(raw)


# --- 8: Umsehen und Zustand sind Abfragen ---


def test_8_look_and_status_are_queries_not_turns(conn, roll_scenario):
    move(conn, "req-8", expected_revision=0, destination_id=BOOMTOWN)
    before = dump_state(conn)

    seen = queries.look(conn, campaign_id=CAMPAIGN, player_id=PLAYER)
    status = queries.status(conn, campaign_id=CAMPAIGN, player_id=PLAYER)

    assert dump_state(conn) == before  # kein Protokoll, keine Revision, keine Zeit
    assert (seen.location_id, seen.name) == (BOOMTOWN, "Bretterstadt")
    assert [(e.location_id, e.minutes) for e in seen.exits] == [
        (GUILD_HALL, 5),
        (TAVERN, 5),
        (CRYSTAL_FIELD, 90),
        (VILLAGE, 240),
    ]
    assert (status.revision, status.clock.time, status.location_id) == (1, "08:05", BOOMTOWN)
    assert not conn.in_transaction


# --- immer mitgeprüft ---


def test_while_a_roll_is_open_moving_is_rejected(conn, roll_scenario):
    attempt(conn, "req-a", 0)
    before = dump_state(conn)

    result = move(conn, "req-m", 1, destination_id=BOOMTOWN)

    assert (result.status, result.reject_code) == ("rejected", "roll_pending")
    assert_only_log_changed(before, dump_state(conn))
    assert location(conn) == GUILD_HALL


def test_duplicate_move_replays_without_moving_again(conn, roll_scenario):
    first = move(conn, "req-d", 0, destination_id=BOOMTOWN)
    after_first = dump_state(conn)

    again = move(conn, "req-d", 0, destination_id=BOOMTOWN)

    assert again.replayed
    assert again.model_copy(update={"replayed": False}) == first
    assert dump_state(conn) == after_first
    assert campaign_row(conn)["game_minutes"] == 5  # die Zeit vergeht nur einmal


def test_stale_revision_on_a_move_is_rejected(conn, roll_scenario):
    move(conn, "req-s1", 0, destination_id=BOOMTOWN)
    before = dump_state(conn)

    stale = move(conn, "req-s2", 0, destination_id=GUILD_HALL)

    assert stale.reject_code == "stale_revision"
    assert_only_log_changed(before, dump_state(conn))


def test_error_while_moving_rolls_everything_back(conn, roll_scenario, monkeypatch):
    before = dump_state(conn)

    def failing_enter(*args, **kwargs):
        raise RuntimeError("Fehler beim Ankommen")

    monkeypatch.setattr(apply, "_enter_location", failing_enter)
    with pytest.raises(RuntimeError, match="Ankommen"):
        move(conn, "req-e", 0, destination_id=BOOMTOWN)

    assert dump_state(conn) == before  # auch die schon vorgerückte Zeit ist zurückgerollt
    assert turn_count(conn) == 0

    monkeypatch.undo()
    assert move(conn, "req-e", 0, destination_id=BOOMTOWN).status == "accepted"


def test_error_while_marking_the_scene_rolls_the_roll_back(conn, roll_scenario, monkeypatch):
    attempt(conn, "req-a", 0)
    before = dump_state(conn)

    def failing_mark(*args, **kwargs):
        raise RuntimeError("Fehler bei der Szene")

    monkeypatch.setattr(apply, "_mark_skill_scene", failing_mark)
    with pytest.raises(RuntimeError, match="Szene"):
        roll(conn, "req-b", 1, value=SUCCESS)

    assert dump_state(conn) == before
    assert open_roll_row(conn) is not None


def test_location_scene_and_used_ticks_survive_reopening_the_database(conn, roll_scenario, db_path):
    move(conn, "req-r1", 0, destination_id=BOOMTOWN)
    success_in(conn, "req-r2", 1)
    attempt(conn, "req-r3", 3, duration="hour")  # offene Probe, Szene schon verbraucht
    saved = dump_state(conn)
    conn.close()

    reopened = db.connect(db_path)
    try:
        db.init_schema(reopened)
        assert dump_state(reopened) == saved
        assert location(reopened) == BOOMTOWN
        assert open_roll_row(reopened)["minutes"] == 60

        result = roll(reopened, "req-r4", 4, value=SUCCESS)
        assert result.check.ticks == 0  # dieselbe Szene, wie beim Öffnen gezeigt
        assert campaign_row(reopened)["game_minutes"] == 5 + 5 + 60
        assert last_turn(reopened)["status"] == "accepted"
    finally:
        reopened.close()
