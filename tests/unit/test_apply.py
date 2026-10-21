import sqlite3

import pytest

from tests.helpers import (
    CAMPAIGN,
    PLAYER,
    START_MONEY,
    add_campaign,
    add_player,
    assert_only_log_changed,
    campaign_row,
    dump_state,
    last_turn,
    money,
    quest_status,
    shards,
)
from valar import apply
from valar.content import CRYSTAL_SHARD, GUILD_SHARD_ORDER
from valar.models import (
    EFFECTS,
    AddItems,
    AddMoney,
    AdvanceTime,
    RemoveItems,
    SetQuestStatus,
    TurnResult,
)


def do(conn, *effects, campaign_id=CAMPAIGN, player_id=PLAYER):
    apply.apply_effects(conn, campaign_id, player_id, list(effects))


# --- RemoveItems ---


def test_remove_items_reduces_the_quantity(conn, scenario):
    do(conn, RemoveItems(item_id=CRYSTAL_SHARD, quantity=2))
    assert shards(conn) == 3


def test_remove_items_can_take_everything(conn, scenario):
    do(conn, RemoveItems(item_id=CRYSTAL_SHARD, quantity=5))
    assert shards(conn) == 0


@pytest.mark.parametrize(
    "effect, player_id",
    [
        (RemoveItems(item_id=CRYSTAL_SHARD, quantity=6), PLAYER),  # mehr als vorhanden
        (RemoveItems(item_id="unobtainium", quantity=1), PLAYER),  # nie besessen
        (RemoveItems(item_id=CRYSTAL_SHARD, quantity=1), "ghost"),  # Spieler gibt es nicht
    ],
)
def test_remove_items_that_are_not_there_is_an_error_and_changes_nothing(conn, scenario, effect, player_id):
    before = dump_state(conn)
    with pytest.raises(apply.ApplyError):
        do(conn, effect, player_id=player_id)
    assert dump_state(conn) == before


# --- AddItems ---


def test_add_items_creates_and_accumulates(conn, scenario):
    add_player(conn, "player-2")
    do(conn, AddItems(item_id=CRYSTAL_SHARD, quantity=3))
    do(conn, AddItems(item_id=CRYSTAL_SHARD, quantity=3), player_id="player-2")
    do(conn, AddItems(item_id=CRYSTAL_SHARD, quantity=1), player_id="player-2")
    assert shards(conn) == 8
    assert shards(conn, player_id="player-2") == 4


@pytest.mark.parametrize(
    "effect",
    [AddItems(item_id=CRYSTAL_SHARD, quantity=1), SetQuestStatus(quest_id="q", status="active")],
)
def test_effects_for_an_unknown_player_fail(conn, scenario, effect):
    with pytest.raises((apply.ApplyError, sqlite3.IntegrityError)):
        do(conn, effect, player_id="ghost")


# --- AddMoney ---


def test_add_money_adds_to_the_balance(conn, scenario):
    do(conn, AddMoney(pennies=100))
    assert money(conn) == START_MONEY + 100


def test_add_money_for_an_unknown_player_is_an_error(conn, scenario):
    with pytest.raises(apply.ApplyError):
        do(conn, AddMoney(pennies=1), player_id="ghost")


# --- SetQuestStatus ---


def test_set_quest_status_inserts_updates_and_is_repeatable(conn, scenario):
    do(conn, SetQuestStatus(quest_id="other_quest", status="active"))
    do(conn, SetQuestStatus(quest_id=GUILD_SHARD_ORDER, status="completed"))
    do(conn, SetQuestStatus(quest_id=GUILD_SHARD_ORDER, status="completed"))
    assert quest_status(conn) == "completed"
    states = conn.execute("SELECT quest_id, status FROM quest_state ORDER BY quest_id").fetchall()
    assert [tuple(r) for r in states] == [("guild_shard_order", "completed"), ("other_quest", "active")]


# --- AdvanceTime ---


def test_advance_time_accumulates(conn, scenario):
    do(conn, AdvanceTime(minutes=10), AdvanceTime(minutes=5))
    assert campaign_row(conn)["game_minutes"] == 15


def test_advance_time_for_an_unknown_campaign_is_an_error(conn, scenario):
    with pytest.raises(apply.ApplyError):
        do(conn, AdvanceTime(minutes=1), campaign_id="ghost")


# --- apply_effects im Ganzen ---


def test_effects_are_applied_in_the_given_order(conn, scenario):
    add_player(conn, "player-2")  # besitzt keine Splitter
    do(
        conn,
        AddItems(item_id=CRYSTAL_SHARD, quantity=5),
        RemoveItems(item_id=CRYSTAL_SHARD, quantity=5),
        player_id="player-2",
    )
    assert shards(conn, player_id="player-2") == 0

    with pytest.raises(apply.ApplyError):
        do(
            conn,
            RemoveItems(item_id=CRYSTAL_SHARD, quantity=5),  # noch nichts da
            AddItems(item_id=CRYSTAL_SHARD, quantity=5),
            player_id="player-2",
        )


def test_unknown_effect_type_is_an_error(conn, scenario):
    with pytest.raises(TypeError):
        do(conn, object())


def test_effects_stay_within_their_campaign_and_player(conn, scenario):
    add_player(conn, "player-2", shards=5, quest="active")
    add_campaign(conn, "campaign-2")
    add_player(conn, PLAYER, campaign_id="campaign-2", shards=5, quest="active")
    others_before = (
        shards(conn, player_id="player-2"),
        money(conn, player_id="player-2"),
        shards(conn, "campaign-2"),
        money(conn, "campaign-2"),
        tuple(campaign_row(conn, "campaign-2")),
    )

    do(
        conn,
        RemoveItems(item_id=CRYSTAL_SHARD, quantity=5),
        AddMoney(pennies=100),
        SetQuestStatus(quest_id=GUILD_SHARD_ORDER, status="completed"),
        AdvanceTime(minutes=10),
    )
    apply.bump_revision(conn, CAMPAIGN)

    assert (shards(conn), money(conn), quest_status(conn)) == (0, START_MONEY + 100, "completed")
    others_after = (
        shards(conn, player_id="player-2"),
        money(conn, player_id="player-2"),
        shards(conn, "campaign-2"),
        money(conn, "campaign-2"),
        tuple(campaign_row(conn, "campaign-2")),
    )
    assert others_after == others_before
    assert quest_status(conn, player_id="player-2") == quest_status(conn, "campaign-2") == "active"


# --- bump_revision ---


def test_bump_revision_counts_up_by_one_per_campaign(conn, scenario):
    add_campaign(conn, "campaign-2")
    assert apply.bump_revision(conn, CAMPAIGN) == 1
    assert apply.bump_revision(conn, CAMPAIGN) == 2
    assert apply.bump_revision(conn, "campaign-2") == 1
    assert campaign_row(conn)["revision"] == 2


def test_bump_revision_for_an_unknown_campaign_is_an_error(conn, scenario):
    with pytest.raises(apply.ApplyError):
        apply.bump_revision(conn, "ghost")


# --- record_turn ---


def record(conn, result, *, revision_before, request_id="r1"):
    apply.record_turn(
        conn,
        campaign_id=CAMPAIGN,
        player_id=PLAYER,
        action_json='{"type":"x"}',
        revision_before=revision_before,
        result=result.model_copy(update={"request_id": request_id}),
    )


def test_record_turn_stores_an_accepted_result(conn, scenario):
    effects = [AddMoney(pennies=5), AdvanceTime(minutes=2)]
    before = dump_state(conn)

    record(conn, TurnResult(request_id="r1", status="accepted", effects=effects, revision=1), revision_before=0)

    row = last_turn(conn)
    assert (row["request_id"], row["player_id"], row["action_json"]) == ("r1", PLAYER, '{"type":"x"}')
    assert (row["status"], row["reject_code"], row["message"]) == ("accepted", None, None)
    assert (row["revision_before"], row["revision_after"]) == (0, 1)
    assert EFFECTS.validate_json(row["effects_json"]) == effects
    assert row["created_at"]  # setzt die Datenbank
    assert_only_log_changed(before, dump_state(conn))


def test_record_turn_stores_a_rejection(conn, scenario):
    result = TurnResult(
        request_id="r1", status="rejected", reject_code="wrong_location", message="Falscher Ort", revision=4
    )
    record(conn, result, revision_before=4)
    row = last_turn(conn)
    assert (row["status"], row["reject_code"], row["message"]) == ("rejected", "wrong_location", "Falscher Ort")
    assert EFFECTS.validate_json(row["effects_json"]) == []


def test_record_turn_refuses_a_duplicate_request_id_and_an_unknown_campaign(conn, scenario):
    result = TurnResult(request_id="r1", status="accepted", revision=1)
    record(conn, result, revision_before=0)
    with pytest.raises(sqlite3.IntegrityError):
        record(conn, result, revision_before=0)
    with pytest.raises(sqlite3.IntegrityError):
        apply.record_turn(
            conn, campaign_id="ghost", player_id=PLAYER, action_json="{}", revision_before=0, result=result
        )
