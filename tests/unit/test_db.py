import sqlite3

import pytest

from tests.helpers import (
    CAMPAIGN,
    PLAYER,
    SKILL,
    START_MONEY,
    add_campaign,
    add_player,
    dump_state,
    money,
)
from valar import apply, db
from valar.models import AdjustSkill

TURN_INSERT = (
    "INSERT INTO turn (campaign_id, request_id, player_id, action_json, status, reject_code,"
    " effects_json, revision_before, revision_after) VALUES (?, ?, 'p', '{}', ?, ?, '[]', ?, ?)"
)


def test_init_schema_creates_all_tables_and_sets_version(conn):
    names = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"campaign", "player", "inventory", "quest_state", "turn"} <= names
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION


def test_init_schema_is_idempotent_and_keeps_data(conn, scenario):
    before = dump_state(conn)
    db.init_schema(conn)
    assert dump_state(conn) == before


@pytest.mark.parametrize("version", [99, 1, 2, 3])  # 1 bis 3: ältere Schritte, ohne Migration
def test_init_schema_refuses_an_unknown_version_and_creates_nothing(db_path, version):
    connection = db.connect(db_path)
    try:
        connection.execute(f"PRAGMA user_version = {version}")
        with pytest.raises(RuntimeError, match=str(version)):
            db.init_schema(connection)
        assert connection.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0
    finally:
        connection.close()


def test_connect_settings(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.isolation_level is None  # Transaktionen steuert der Code selbst
    assert isinstance(conn.execute("SELECT 1 AS one").fetchone(), sqlite3.Row)


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE player SET money_pennies = -1",
        "UPDATE player SET money_pennies = 'viel'",  # STRICT: kein Text in Zahlenspalte
        "UPDATE inventory SET quantity = -1",
        "UPDATE quest_state SET status = 'done'",
        "UPDATE campaign SET revision = -1",
        "UPDATE campaign SET game_minutes = -1",
        f"INSERT INTO player (campaign_id, player_id, name, location_id)"
        f" VALUES ('{CAMPAIGN}', '{PLAYER}', 'doppelt', 'x')",
        "INSERT INTO player (campaign_id, player_id, name, location_id) VALUES ('nope', 'p', 'n', 'x')",
        f"INSERT INTO inventory VALUES ('{CAMPAIGN}', 'nobody', 'x', 1)",
        f"INSERT INTO quest_state VALUES ('{CAMPAIGN}', 'nobody', 'q', 'active')",
        f"DELETE FROM campaign WHERE campaign_id = '{CAMPAIGN}'",  # Spieler hängen daran
        "UPDATE player SET attr_str = 0",  # Attribute: 1 bis 20
        "UPDATE player SET attr_cha = 21",
        "UPDATE player SET attribute_points = -1",
        f"UPDATE player_skill SET value = 101 WHERE skill_id = '{SKILL}'",
        f"UPDATE player_skill SET value = -1 WHERE skill_id = '{SKILL}'",
        f"UPDATE player_skill SET ticks = -1 WHERE skill_id = '{SKILL}'",
        f"UPDATE player_skill SET ups = -1 WHERE skill_id = '{SKILL}'",
        f"INSERT INTO player_skill (campaign_id, player_id, skill_id) VALUES ('{CAMPAIGN}', 'nobody', 'x')",
        "UPDATE player SET arrived_at = -1",
        "UPDATE player SET arrived_at = NULL",
        f"UPDATE player_skill SET ticked_scene = -1 WHERE skill_id = '{SKILL}'",
        f"UPDATE player_skill SET ticked_scene = 'heute' WHERE skill_id = '{SKILL}'",
    ],
)
def test_database_refuses_invalid_state(conn, scenario, sql):
    apply.apply_effects(conn, CAMPAIGN, PLAYER, [AdjustSkill(skill_id=SKILL, points=10, ticks=1)])
    before = dump_state(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql)
    assert dump_state(conn) == before


@pytest.mark.parametrize(
    "status, code, before, after",
    [
        ("accepted", None, 0, 1),
        ("accepted", None, 7, 8),
        ("awaiting_roll", None, 0, 1),
        ("rejected", "stale_revision", 3, 3),
    ],
)
def test_turn_log_accepts_consistent_rows(conn, scenario, status, code, before, after):
    conn.execute(TURN_INSERT, (CAMPAIGN, "r", status, code, before, after))


@pytest.mark.parametrize(
    "status, code, before, after",
    [
        ("accepted", None, 0, 0),  # angenommen, aber Revision nicht erhöht
        ("accepted", None, 0, 2),  # Revision um mehr als 1 erhöht
        ("awaiting_roll", None, 0, 0),  # Probe eröffnet, aber Revision nicht erhöht
        ("awaiting_roll", "stale_revision", 0, 1),
        ("accepted", "stale_revision", 0, 1),  # angenommen mit Ablehnungsgrund
        ("rejected", "stale_revision", 0, 1),  # abgelehnt, aber Revision erhöht
        ("rejected", None, 0, 0),  # abgelehnt ohne Grund
        ("maybe", None, 0, 0),
    ],
)
def test_turn_log_refuses_inconsistent_rows(conn, scenario, status, code, before, after):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(TURN_INSERT, (CAMPAIGN, "r", status, code, before, after))


def test_request_id_is_unique_per_campaign_only(conn, scenario):
    add_campaign(conn, "campaign-2")
    conn.execute(TURN_INSERT, (CAMPAIGN, "same", "accepted", None, 0, 1))
    conn.execute(TURN_INSERT, ("campaign-2", "same", "accepted", None, 0, 1))  # andere Kampagne: erlaubt
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(TURN_INSERT, (CAMPAIGN, "same", "accepted", None, 1, 2))


def test_read_only_blocks_writes_but_allows_reads(conn, scenario):
    with db.read_only(conn):
        assert money(conn) == START_MONEY
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("UPDATE player SET money_pennies = 1")
    conn.execute("UPDATE player SET money_pennies = 1")  # danach wieder erlaubt
    assert money(conn) == 1


def test_read_only_is_switched_off_after_an_exception(conn, scenario):
    with pytest.raises(ValueError):
        with db.read_only(conn):
            raise ValueError
    conn.execute("UPDATE player SET money_pennies = 1")


OPEN_ROLL_INSERT = (
    "INSERT INTO open_roll (campaign_id, player_id, skill_id, difficulty, dc, bonus,"
    " ticks_on_success, reason, minutes, scene) VALUES (?, ?, 'SKILL-101', ?, ?, 5, ?, 'x', 5, 0)"
)


def test_only_one_roll_can_be_open_per_campaign(conn, roll_scenario):
    add_player(conn, "player-2")
    add_campaign(conn, "campaign-2")
    add_player(conn, PLAYER, campaign_id="campaign-2")

    conn.execute(OPEN_ROLL_INSERT, (CAMPAIGN, PLAYER, "DIFF-03", 12, 2))
    with pytest.raises(sqlite3.IntegrityError):  # auch nicht für eine andere Spielfigur
        conn.execute(OPEN_ROLL_INSERT, (CAMPAIGN, "player-2", "DIFF-03", 12, 2))
    conn.execute(OPEN_ROLL_INSERT, ("campaign-2", PLAYER, "DIFF-03", 12, 2))  # andere Kampagne: erlaubt


@pytest.mark.parametrize(
    "player_id, difficulty, dc, ticks",
    [
        ("nobody", "DIFF-03", 12, 2),  # Spielfigur gibt es nicht
        (PLAYER, "DIFF-08", 12, 2),
        (PLAYER, "DIFF-03", 0, 2),
        (PLAYER, "DIFF-03", 12, -1),
    ],
)
def test_open_roll_refuses_invalid_rows(conn, roll_scenario, player_id, difficulty, dc, ticks):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(OPEN_ROLL_INSERT, (CAMPAIGN, player_id, difficulty, dc, ticks))


@pytest.mark.parametrize("minutes, scene", [(0, 0), (-5, 0), (5, -1), (None, 0), (5, None)])
def test_open_roll_needs_minutes_and_a_scene(conn, roll_scenario, minutes, scene):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO open_roll (campaign_id, player_id, skill_id, difficulty, dc, bonus,"
            " ticks_on_success, reason, minutes, scene) VALUES (?, ?, 'SKILL-101', 'DIFF-03', 12, 5, 2, 'x', ?, ?)",
            (CAMPAIGN, PLAYER, minutes, scene),
        )


def test_check_is_only_stored_for_accepted_turns(conn, roll_scenario):
    sql = (
        "INSERT INTO turn (campaign_id, request_id, player_id, action_json, status, reject_code,"
        " effects_json, check_json, revision_before, revision_after)"
        " VALUES (?, ?, 'p', '{}', ?, ?, '[]', '{}', ?, ?)"
    )
    conn.execute(sql, (CAMPAIGN, "ok", "accepted", None, 0, 1))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql, (CAMPAIGN, "bad-1", "awaiting_roll", None, 1, 2))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql, (CAMPAIGN, "bad-2", "rejected", "stale_revision", 2, 2))


def test_new_players_start_with_attribute_10_and_no_points(conn):
    add_campaign(conn, CAMPAIGN)
    add_player(conn, PLAYER)
    row = conn.execute("SELECT * FROM player").fetchone()
    assert [row[f"attr_{a}"] for a in ("str", "ges", "kon", "int", "wei", "cha")] == [10] * 6
    assert row["attribute_points"] == 0
