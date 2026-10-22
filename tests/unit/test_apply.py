import sqlite3

import pytest

from tests.helpers import (
    CAMPAIGN,
    PLAYER,
    SKILL,
    START_MONEY,
    add_campaign,
    add_player,
    assert_only_log_changed,
    attribute_points,
    campaign_row,
    dump_state,
    last_turn,
    location,
    money,
    open_roll_row,
    quest_status,
    set_game_minutes,
    shards,
    skill_row,
)
from valar import apply
from valar.content import CRYSTAL_SHARD, GUILD_HALL, GUILD_SHARD_ORDER
from valar.models import (
    EFFECTS,
    AddItems,
    AddMoney,
    AdjustSkill,
    AdvanceTime,
    CheckResult,
    ClearOpenRoll,
    EnterLocation,
    GrantAttributePoints,
    LearnKnowledge,
    MarkSkillScene,
    OpenRoll,
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


# --- Spielfigur anlegen ---


def test_create_player_stores_attributes_and_skills(conn):
    add_campaign(conn, CAMPAIGN)
    add_player(conn, PLAYER, attributes={"STR": 14, "CHA": 7}, skills={SKILL: 30, "SKILL-401": 15})
    row = conn.execute("SELECT attr_str, attr_ges, attr_cha FROM player").fetchone()
    assert tuple(row) == (14, 10, 7)  # nicht genannt: 10
    assert skill_row(conn) == (30, 0, 0)  # gesetzte Startwerte zählen nicht als Aufstieg
    assert skill_row(conn, "SKILL-401") == (15, 0, 0)
    assert skill_row(conn, "SKILL-201") is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"attributes": {"XXX": 10}},
        {"attributes": {"str": 10}},  # Schreibweise zählt
        {"attributes": {"STR); DROP TABLE player; --": 10}},
        {"skills": {"SKILL-999": 10}},
        {"skills": {"SKILL-501": 10}},
    ],
)
def test_create_player_refuses_unknown_names_before_touching_the_database(conn, kwargs):
    add_campaign(conn, CAMPAIGN)
    before = dump_state(conn)
    with pytest.raises(ValueError):
        apply.create_player(conn, CAMPAIGN, PLAYER, name="x", location_id="y", **kwargs)
    assert dump_state(conn) == before  # auch die Tabelle `player` ist unversehrt


# --- Probe: OpenRoll, ClearOpenRoll ---


def an_open_roll() -> OpenRoll:
    return OpenRoll(
        skill_id=SKILL, difficulty="DIFF-03", dc=12, bonus=5, ticks_on_success=2, reason="Grund",
        minutes=60, scene=480,
    )


def test_open_roll_is_stored_and_a_second_one_fails(conn, roll_scenario):
    do(conn, an_open_roll())
    row = open_roll_row(conn)
    assert tuple(row) == (CAMPAIGN, PLAYER, SKILL, "DIFF-03", 12, 5, 2, "Grund", 60, 480, "[]")

    before = dump_state(conn)
    with pytest.raises(sqlite3.IntegrityError):
        do(conn, an_open_roll())
    assert dump_state(conn) == before


def test_clear_open_roll_removes_it_and_needs_one(conn, roll_scenario):
    add_player(conn, "player-2")
    with pytest.raises(apply.ApplyError):
        do(conn, ClearOpenRoll())  # nichts offen

    do(conn, an_open_roll())
    with pytest.raises(apply.ApplyError):
        do(conn, ClearOpenRoll(), player_id="player-2")  # die Probe gehört dem anderen
    assert open_roll_row(conn) is not None

    do(conn, ClearOpenRoll())
    assert open_roll_row(conn) is None


# --- Probe: AdjustSkill, GrantAttributePoints ---


def test_adjust_skill_creates_the_row_and_accumulates(conn, roll_scenario):
    do(conn, AdjustSkill(skill_id="SKILL-201", points=0, ticks=2))  # neue Zeile
    do(conn, AdjustSkill(skill_id="SKILL-201", points=1, ticks=-1))  # Aufstieg, Ticks verbraucht
    assert skill_row(conn, "SKILL-201") == (1, 1, 1)
    do(conn, AdjustSkill(skill_id=SKILL, points=2, ticks=0))  # vorhandener Startwert
    assert skill_row(conn) == (32, 0, 2)


@pytest.mark.parametrize(
    "effect",
    [
        AdjustSkill(skill_id="SKILL-201", points=0, ticks=-1),  # neue Zeile mit negativen Ticks
        AdjustSkill(skill_id=SKILL, points=71, ticks=0),  # über 100
        AdjustSkill(skill_id=SKILL, points=0, ticks=-1),  # unter 0 Ticks
    ],
)
def test_adjust_skill_that_leaves_the_scale_fails_and_changes_nothing(conn, roll_scenario, effect):
    before = dump_state(conn)
    with pytest.raises(sqlite3.IntegrityError):
        do(conn, effect)
    assert dump_state(conn) == before


def test_adjust_skill_for_an_unknown_player_fails(conn, roll_scenario):
    with pytest.raises(sqlite3.IntegrityError):
        do(conn, AdjustSkill(skill_id=SKILL, points=1, ticks=0), player_id="ghost")


def test_grant_attribute_points_adds_to_the_pool(conn, roll_scenario):
    do(conn, GrantAttributePoints(points=1))
    do(conn, GrantAttributePoints(points=2))
    assert attribute_points(conn) == 3
    with pytest.raises(apply.ApplyError):
        do(conn, GrantAttributePoints(points=1), player_id="ghost")


def test_record_turn_stores_a_check_and_the_awaiting_status(conn, roll_scenario):
    check = CheckResult(die=7, bonus=5, total=12, dc=12, outcome="success", ticks=2, skill_points=0)
    record(
        conn,
        TurnResult(request_id="r1", status="accepted", check=check, revision=1),
        revision_before=0,
    )
    assert CheckResult.model_validate_json(last_turn(conn)["check_json"]) == check

    record(
        conn,
        TurnResult(request_id="r2", status="awaiting_roll", effects=[an_open_roll()], revision=2),
        revision_before=1,
        request_id="r2",
    )
    row = last_turn(conn)
    assert (row["status"], row["check_json"]) == ("awaiting_roll", None)


# --- Ort und Szene: EnterLocation, MarkSkillScene ---


def arrived_at(conn, player_id=PLAYER) -> int:
    return conn.execute(
        "SELECT arrived_at FROM player WHERE campaign_id = ? AND player_id = ?", (CAMPAIGN, player_id)
    ).fetchone()[0]


def ticked_scene(conn, skill_id=SKILL):
    row = conn.execute(
        "SELECT ticked_scene FROM player_skill WHERE campaign_id = ? AND player_id = ? AND skill_id = ?",
        (CAMPAIGN, PLAYER, skill_id),
    ).fetchone()
    return row[0] if row else "keine Zeile"


def test_enter_location_sets_place_and_arrival(conn, roll_scenario):
    add_player(conn, "player-2")
    do(conn, EnterLocation(location_id="tavern", arrived_at=95))
    assert (location(conn), arrived_at(conn)) == ("tavern", 95)
    assert (location(conn, "player-2"), arrived_at(conn, "player-2")) == (GUILD_HALL, 0)  # unberührt
    with pytest.raises(apply.ApplyError):
        do(conn, EnterLocation(location_id="tavern", arrived_at=1), player_id="ghost")


def test_new_players_arrive_at_the_current_game_time(conn):
    add_campaign(conn, CAMPAIGN)
    set_game_minutes(conn, 300)
    add_player(conn, PLAYER)
    assert arrived_at(conn) == 300


def test_mark_skill_scene_updates_or_creates_the_row(conn, roll_scenario):
    assert ticked_scene(conn) is None  # Startwert ohne Szene
    do(conn, MarkSkillScene(skill_id=SKILL, scene=480))
    assert ticked_scene(conn) == 480
    assert skill_row(conn) == (30, 0, 0)  # Wert, Ticks und Aufstiege unberührt
    do(conn, MarkSkillScene(skill_id="SKILL-201", scene=5))
    assert (ticked_scene(conn, "SKILL-201"), skill_row(conn, "SKILL-201")) == (5, (0, 0, 0))


def test_mark_skill_scene_for_an_unknown_player_fails(conn, roll_scenario):
    with pytest.raises(sqlite3.IntegrityError):
        do(conn, MarkSkillScene(skill_id=SKILL, scene=0), player_id="ghost")


# --- Kampagne aus dem Inhalt ---


def test_create_campaign_from_content_sets_up_the_test_figure(conn):
    from valar.content import CONTENT

    player_id = apply.create_campaign_from_content(conn, "neu", CONTENT)

    assert player_id == CONTENT.start_player.player_id
    row = conn.execute("SELECT name, location_id, money_pennies, attr_str FROM player WHERE campaign_id = 'neu'").fetchone()
    assert tuple(row) == ("Testfigur", GUILD_HALL, 30, 14)
    assert shards(conn, "neu", player_id) == 5
    assert quest_status(conn, "neu", player_id) == "active"
    assert not conn.in_transaction


def test_create_campaign_from_content_is_all_or_nothing(conn):
    from valar.content import CONTENT

    apply.create_campaign_from_content(conn, "neu", CONTENT)
    before = dump_state(conn)
    with pytest.raises(sqlite3.IntegrityError):
        apply.create_campaign_from_content(conn, "neu", CONTENT)  # gibt es schon
    assert dump_state(conn) == before
    assert not conn.in_transaction


def test_learn_knowledge_twice_changes_nothing(conn, scenario):
    effect = LearnKnowledge(page_id="sumpfkraut", skill_id="SKILL-408", difficulty="DIFF-04", dc=14)
    do(conn, effect)
    before = dump_state(conn)
    do(conn, effect)
    assert dump_state(conn) == before
    assert [tuple(r) for r in conn.execute("SELECT page_id, skill_id, difficulty FROM player_knowledge")] == [
        ("sumpfkraut", "SKILL-408", "DIFF-04")
    ]


def test_campaign_seed_is_set_or_drawn(conn):
    apply.create_campaign(conn, "fest", seed=42)
    apply.create_campaign(conn, "zufall")
    seeds = dict(conn.execute("SELECT campaign_id, seed FROM campaign").fetchall())
    assert seeds["fest"] == 42 and isinstance(seeds["zufall"], int)
