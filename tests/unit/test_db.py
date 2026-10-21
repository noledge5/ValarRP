import sqlite3

import pytest

from tests.helpers import CAMPAIGN, PLAYER, START_MONEY, add_campaign, dump_state, money
from valar import db

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


def test_init_schema_refuses_an_unknown_version_and_creates_nothing(db_path):
    connection = db.connect(db_path)
    try:
        connection.execute("PRAGMA user_version = 99")
        with pytest.raises(RuntimeError, match="99"):
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
    ],
)
def test_database_refuses_invalid_state(conn, scenario, sql):
    before = dump_state(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql)
    assert dump_state(conn) == before


@pytest.mark.parametrize(
    "status, code, before, after",
    [("accepted", None, 0, 1), ("accepted", None, 7, 8), ("rejected", "stale_revision", 3, 3)],
)
def test_turn_log_accepts_consistent_rows(conn, scenario, status, code, before, after):
    conn.execute(TURN_INSERT, (CAMPAIGN, "r", status, code, before, after))


@pytest.mark.parametrize(
    "status, code, before, after",
    [
        ("accepted", None, 0, 0),  # angenommen, aber Revision nicht erhöht
        ("accepted", None, 0, 2),  # Revision um mehr als 1 erhöht
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
