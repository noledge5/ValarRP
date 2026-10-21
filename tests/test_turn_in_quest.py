"""Schritt 1: Abgabe-Quest. Nummerierung wie im README (Tests 1 und 7)."""

import sqlite3

import pytest

from tests.helpers import CAMPAIGN, PLAYER, START_MONEY, dump_state
from valar import apply
from valar.content import CRYSTAL_SHARD, GUILD_SHARD_ORDER
from valar.models import (
    AddMoney,
    AdvanceTime,
    RemoveItems,
    SetQuestStatus,
    SubmitQuestItems,
)
from valar.turns import submit_turn


def shards(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT quantity FROM inventory WHERE campaign_id = ? AND player_id = ? AND item_id = ?",
        (CAMPAIGN, PLAYER, CRYSTAL_SHARD),
    ).fetchone()
    return row["quantity"] if row else 0


def money(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT money_pennies FROM player WHERE campaign_id = ? AND player_id = ?",
        (CAMPAIGN, PLAYER),
    ).fetchone()["money_pennies"]


def quest_status(conn: sqlite3.Connection) -> str:
    return conn.execute(
        "SELECT status FROM quest_state WHERE campaign_id = ? AND player_id = ? AND quest_id = ?",
        (CAMPAIGN, PLAYER, GUILD_SHARD_ORDER),
    ).fetchone()["status"]


def campaign_row(conn: sqlite3.Connection) -> sqlite3.Row:
    return conn.execute(
        "SELECT revision, game_minutes FROM campaign WHERE campaign_id = ?", (CAMPAIGN,)
    ).fetchone()


def turn_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT count(*) FROM turn WHERE campaign_id = ?", (CAMPAIGN,)).fetchone()[0]


def submit(conn: sqlite3.Connection, request_id: str, expected_revision: int):
    return submit_turn(
        conn,
        campaign_id=CAMPAIGN,
        player_id=PLAYER,
        request_id=request_id,
        expected_revision=expected_revision,
        action=SubmitQuestItems(quest_id=GUILD_SHARD_ORDER),
    )


def test_1_submit_completes_quest_exactly_once(conn, scenario):
    assert (shards(conn), money(conn), quest_status(conn)) == (5, START_MONEY, "active")
    assert tuple(campaign_row(conn)) == (0, 0)

    result = submit(conn, "req-1", expected_revision=0)

    assert result.status == "accepted"
    assert result.reject_code is None
    assert not result.replayed
    assert result.revision == 1
    # Die Belohnung stammt aus dem Inhalt: 100 Pfennig = 1 Gulden.
    assert result.effects == [
        RemoveItems(item_id=CRYSTAL_SHARD, quantity=5),
        AddMoney(pennies=100),
        SetQuestStatus(quest_id=GUILD_SHARD_ORDER, status="completed"),
        AdvanceTime(minutes=10),
    ]

    assert shards(conn) == 0
    assert money(conn) == START_MONEY + 100
    assert quest_status(conn) == "completed"
    assert tuple(campaign_row(conn)) == (1, 10)  # Revision +1, Zeit +10 Minuten
    assert turn_count(conn) == 1
    turn = conn.execute("SELECT status, revision_before, revision_after FROM turn").fetchone()
    assert tuple(turn) == ("accepted", 0, 1)


def test_7_error_while_applying_rolls_everything_back(conn, scenario, monkeypatch):
    before = dump_state(conn)
    inside: dict[str, int] = {}

    def failing_add_money(c: sqlite3.Connection, *args, **kwargs) -> None:
        # Beweis, dass der Test nicht ins Leere läuft: Die Splitter sind in dieser
        # Transaktion schon abgezogen, erst jetzt fällt der Fehler.
        inside["shards"] = shards(c)
        raise RuntimeError("Fehler beim Geld")

    monkeypatch.setattr(apply, "_add_money", failing_add_money)

    with pytest.raises(RuntimeError, match="Fehler beim Geld"):
        submit(conn, "req-7", expected_revision=0)

    assert inside["shards"] == 0
    assert not conn.in_transaction
    assert dump_state(conn) == before  # Zustand, Revision, Zeit und Protokoll: alles wie vorher
    assert (shards(conn), money(conn), quest_status(conn)) == (5, START_MONEY, "active")
    assert turn_count(conn) == 0

    # Es wurde nichts gespeichert, also ist die request_id noch frei.
    monkeypatch.undo()
    retry = submit(conn, "req-7", expected_revision=0)
    assert retry.status == "accepted"
    assert not retry.replayed
    assert (shards(conn), money(conn), quest_status(conn)) == (0, START_MONEY + 100, "completed")
