"""Schritt 1: Abgabe-Quest. Nummerierung wie im README (Tests 1 bis 8)."""

import sqlite3

import pytest

from tests.helpers import CAMPAIGN, PLAYER, START_MONEY, assert_only_log_changed, dump_state
from valar import apply, db, resolve
from valar.content import CRYSTAL_SHARD, GUILD_SHARD_ORDER
from valar.models import (
    AddMoney,
    AdvanceTime,
    RemoveItems,
    SetQuestStatus,
    SubmitQuestItems,
)
from valar.turns import RequestIdConflict, submit_turn


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


def last_turn(conn: sqlite3.Connection) -> sqlite3.Row:
    return conn.execute("SELECT * FROM turn ORDER BY turn_id DESC LIMIT 1").fetchone()


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


def test_2_second_submit_is_rejected_without_double_reward(conn, scenario):
    first = submit(conn, "req-2a", expected_revision=0)
    assert first.status == "accepted"
    after_first = dump_state(conn)

    second = submit(conn, "req-2b", expected_revision=first.revision)

    assert second.status == "rejected"
    assert second.reject_code == "quest_not_active"
    assert not second.replayed
    assert second.effects == []
    assert second.revision == 1  # unverändert
    assert (shards(conn), money(conn), quest_status(conn)) == (0, START_MONEY + 100, "completed")
    assert tuple(campaign_row(conn)) == (1, 10)
    assert_only_log_changed(after_first, dump_state(conn))
    turn = last_turn(conn)
    assert (turn["status"], turn["reject_code"]) == ("rejected", "quest_not_active")
    assert (turn["revision_before"], turn["revision_after"]) == (1, 1)


def test_3_duplicate_request_id_replays_result_without_effect(conn, scenario):
    first = submit(conn, "req-3", expected_revision=0)
    assert first.status == "accepted"
    after_first = dump_state(conn)

    # Der Client hat die Antwort nicht erhalten und sendet dieselbe Anfrage noch einmal.
    # Die Revision ist inzwischen weitergezählt; trotzdem kommt das gespeicherte Ergebnis.
    again = submit(conn, "req-3", expected_revision=0)

    assert again.replayed
    assert again.model_copy(update={"replayed": False}) == first
    assert dump_state(conn) == after_first  # nichts geschrieben, auch kein zweiter Protokolleintrag
    assert (shards(conn), money(conn), quest_status(conn)) == (0, START_MONEY + 100, "completed")
    assert tuple(campaign_row(conn)) == (1, 10)
    assert turn_count(conn) == 1


def test_4_too_few_items_is_rejected_and_changes_nothing(conn, make_scenario):
    make_scenario(shards=4)
    before = dump_state(conn)

    result = submit(conn, "req-4", expected_revision=0)

    assert result.status == "rejected"
    assert result.reject_code == "not_enough_items"
    assert result.revision == 0
    assert (shards(conn), money(conn), quest_status(conn)) == (4, START_MONEY, "active")
    assert tuple(campaign_row(conn)) == (0, 0)
    assert_only_log_changed(before, dump_state(conn))
    turn = last_turn(conn)
    assert (turn["status"], turn["revision_before"], turn["revision_after"]) == ("rejected", 0, 0)


def test_5_wrong_location_is_rejected(conn, make_scenario):
    make_scenario(location_id="street")  # Testdaten: irgendein Ort außer dem Zunfthaus
    before = dump_state(conn)

    result = submit(conn, "req-5", expected_revision=0)

    assert result.status == "rejected"
    assert result.reject_code == "wrong_location"
    assert result.revision == 0
    assert (shards(conn), money(conn), quest_status(conn)) == (5, START_MONEY, "active")
    assert tuple(campaign_row(conn)) == (0, 0)
    assert_only_log_changed(before, dump_state(conn))


def test_6_stale_revision_is_rejected_and_needs_new_request_id(conn, scenario):
    # Ein anderer Client hat inzwischen einen Zug angenommen: die Revision steht auf 1.
    conn.execute("BEGIN")
    apply.bump_revision(conn, CAMPAIGN)
    conn.execute("COMMIT")
    before = dump_state(conn)

    # Der Zug wäre sonst gültig (Ort, Splitter und Auftrag passen), nur die Revision ist veraltet.
    stale = submit(conn, "req-6a", expected_revision=0)

    assert stale.status == "rejected"
    assert stale.reject_code == "stale_revision"
    assert stale.revision == 1  # der aktuelle Stand, den der Client neu lesen soll
    assert (shards(conn), money(conn), quest_status(conn)) == (5, START_MONEY, "active")
    assert tuple(campaign_row(conn)) == (1, 0)
    assert_only_log_changed(before, dump_state(conn))
    turn = last_turn(conn)
    assert (turn["status"], turn["reject_code"]) == ("rejected", "stale_revision")
    assert (turn["revision_before"], turn["revision_after"]) == (1, 1)

    # Dieselbe request_id liefert die gespeicherte Ablehnung, auch mit neuer Revision.
    # Der Client braucht für den neuen Versuch also eine neue request_id.
    same_id = submit(conn, "req-6a", expected_revision=1)
    assert same_id.replayed
    assert same_id.reject_code == "stale_revision"

    retry = submit(conn, "req-6b", expected_revision=1)
    assert retry.status == "accepted"
    assert (shards(conn), money(conn), quest_status(conn)) == (0, START_MONEY + 100, "completed")
    assert tuple(campaign_row(conn)) == (2, 10)


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


def test_8_state_and_duplicate_protection_survive_reopening(conn, scenario, db_path):
    first = submit(conn, "req-8", expected_revision=0)
    assert first.status == "accepted"
    saved = dump_state(conn)
    conn.close()

    reopened = db.connect(db_path)
    try:
        db.init_schema(reopened)  # so startet die Anwendung; bestehende Daten bleiben unberührt
        assert dump_state(reopened) == saved
        assert (shards(reopened), money(reopened), quest_status(reopened)) == (
            0,
            START_MONEY + 100,
            "completed",
        )
        assert tuple(campaign_row(reopened)) == (1, 10)

        # Duplikat-Schutz: dieselbe request_id liefert das gespeicherte Ergebnis ohne Wirkung.
        again = submit(reopened, "req-8", expected_revision=0)
        assert again.replayed
        assert again.model_copy(update={"replayed": False}) == first
        assert dump_state(reopened) == saved

        # Auch eine neue Abgabe bringt keine zweite Belohnung.
        second = submit(reopened, "req-8b", expected_revision=1)
        assert second.reject_code == "quest_not_active"
        assert money(reopened) == START_MONEY + 100
    finally:
        reopened.close()


# --- Zusätzlich zu den acht Tests aus dem README: Entscheidungen 37 und 39 ---


def test_37_resolver_cannot_write(conn, scenario, monkeypatch):
    original = resolve._resolve_submit_quest_items

    def writing_resolver(c: sqlite3.Connection, *args, **kwargs):
        c.execute("UPDATE player SET money_pennies = 999999")  # ein Resolver darf das nie
        return original(c, *args, **kwargs)

    monkeypatch.setattr(resolve, "_resolve_submit_quest_items", writing_resolver)
    before = dump_state(conn)

    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        submit(conn, "req-37", expected_revision=0)

    assert not conn.in_transaction
    assert dump_state(conn) == before
    assert money(conn) == START_MONEY

    # Der Nur-Lese-Modus ist wieder aus: Mit einem ordentlichen Resolver wird normal geschrieben.
    monkeypatch.undo()
    retry = submit(conn, "req-37", expected_revision=0)
    assert retry.status == "accepted"
    assert money(conn) == START_MONEY + 100


@pytest.mark.parametrize(
    "other",
    [
        {"action": SubmitQuestItems(quest_id="other_quest")},
        {"player_id": "player-2"},
    ],
    ids=["other_action", "other_player"],
)
def test_39_same_request_id_with_different_content_is_a_conflict(conn, scenario, other):
    first = submit(conn, "req-39", expected_revision=0)
    assert first.status == "accepted"
    after_first = dump_state(conn)

    request = {
        "campaign_id": CAMPAIGN,
        "player_id": PLAYER,
        "request_id": "req-39",
        "expected_revision": 0,
        "action": SubmitQuestItems(quest_id=GUILD_SHARD_ORDER),
    } | other

    with pytest.raises(RequestIdConflict):
        submit_turn(conn, **request)

    assert not conn.in_transaction
    assert dump_state(conn) == after_first  # das gespeicherte Ergebnis wurde nicht fremd abgespielt
