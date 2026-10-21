import sqlite3

import pytest

from tests.helpers import (
    CAMPAIGN,
    START_MONEY,
    add_player,
    assert_only_log_changed,
    campaign_row,
    custom_content,
    dump_state,
    last_turn,
    money,
    shards,
    submit,
    turn_count,
)
from valar import apply, db
from valar.models import AddMoney
from valar.turns import UnknownCampaign


def test_empty_request_id_is_refused_before_anything_happens(conn, scenario):
    before = dump_state(conn)
    with pytest.raises(ValueError):
        submit(conn, "", expected_revision=0)
    assert not conn.in_transaction
    assert dump_state(conn) == before


def test_unknown_campaign_is_an_error_and_writes_nothing(conn):
    with pytest.raises(UnknownCampaign):
        submit(conn, "r1", expected_revision=0)
    assert not conn.in_transaction
    assert turn_count(conn) == 0


def test_unknown_player_is_rejected_and_logged(conn, scenario):
    before = dump_state(conn)
    result = submit(conn, "r1", expected_revision=0, player_id="ghost")
    assert (result.status, result.reject_code, result.revision) == ("rejected", "unknown_player", 0)
    assert_only_log_changed(before, dump_state(conn))
    assert last_turn(conn)["player_id"] == "ghost"


def test_stale_revision_is_reported_before_any_rule_violation(conn, make_scenario):
    make_scenario(location_id="street")  # der Zug verletzt auch die Ortsregel
    conn.execute("BEGIN")
    apply.bump_revision(conn, CAMPAIGN)
    conn.execute("COMMIT")
    assert submit(conn, "r1", expected_revision=0).reject_code == "stale_revision"


def test_reward_and_duration_come_from_the_content(conn, make_scenario):
    make_scenario(shards=2)
    result = submit(conn, "r1", 0, content=custom_content(quantity=2, reward=250, minutes=3))
    assert result.status == "accepted"
    assert AddMoney(pennies=250) in result.effects
    assert (shards(conn), money(conn)) == (0, START_MONEY + 250)
    assert tuple(campaign_row(conn)) == (1, 3)


def test_replay_returns_the_stored_revision_not_the_current_one(conn, scenario):
    first = submit(conn, "r1", expected_revision=0)
    conn.execute("BEGIN")
    apply.bump_revision(conn, CAMPAIGN)  # die Kampagne ist inzwischen weitergezählt
    conn.execute("COMMIT")

    again = submit(conn, "r1", expected_revision=0)

    assert again.replayed
    assert again.revision == first.revision == 1
    assert campaign_row(conn)["revision"] == 2


def test_a_turn_takes_the_write_lock_first_and_commits_once(conn, scenario):
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    submit(conn, "r1", expected_revision=0)
    conn.set_trace_callback(None)

    # Die Schreibsperre kommt vor dem Lesen der Revision: Prüfen und Anwenden sind unteilbar.
    assert statements[0] == "BEGIN IMMEDIATE"
    assert statements[-1] == "COMMIT"
    assert statements.count("COMMIT") == 1


def test_submitting_inside_a_foreign_transaction_fails_and_leaves_it_alone(conn, scenario):
    conn.execute("BEGIN")
    with pytest.raises(sqlite3.OperationalError):
        submit(conn, "r1", expected_revision=0)
    assert conn.in_transaction  # die Transaktion des Aufrufers wurde nicht zurückgerollt
    conn.execute("ROLLBACK")
    assert turn_count(conn) == 0


def test_two_clients_with_the_same_old_revision_the_second_is_rejected(conn, scenario, db_path):
    add_player(conn, "player-2", shards=5, quest="active")
    other = db.connect(db_path)
    try:
        first = submit(conn, "a-1", expected_revision=0)
        second = submit(other, "b-1", expected_revision=0, player_id="player-2")
    finally:
        other.close()

    assert first.status == "accepted"
    assert (second.status, second.reject_code) == ("rejected", "stale_revision")
    assert shards(conn, player_id="player-2") == 5  # der zweite Zug hatte keine Wirkung
    assert campaign_row(conn)["revision"] == 1


def test_a_second_writer_fails_cleanly_while_the_write_lock_is_held(conn, scenario, db_path):
    other = db.connect(db_path)
    try:
        other.execute("PRAGMA busy_timeout = 50")
        conn.execute("BEGIN IMMEDIATE")  # hält die Schreibsperre wie ein laufender Spielzug
        # Erwartet: Fehler, kein Protokolleintrag, keine offene Transaktion.

        with pytest.raises(sqlite3.OperationalError, match="locked"):
            submit(other, "r1", expected_revision=0)
        assert not other.in_transaction

        conn.execute("ROLLBACK")
        assert submit(other, "r1", expected_revision=0).status == "accepted"
    finally:
        other.close()
