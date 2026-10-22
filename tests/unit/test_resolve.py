import pytest

from tests.helpers import (
    CAMPAIGN,
    PLAYER,
    REASON,
    SKILL,
    add_campaign,
    add_player,
    custom_content,
    dump_state,
)
from valar import apply
from valar.content import CONTENT, CRYSTAL_SHARD, GUILD_SHARD_ORDER
from valar.models import (
    AddMoney,
    AdjustSkill,
    AdvanceTime,
    AttemptAction,
    CheckResult,
    ClearOpenRoll,
    GrantAttributePoints,
    OpenRoll,
    RemoveItems,
    SetQuestStatus,
    SubmitQuestItems,
    SubmitRoll,
)
from valar.resolve import Accepted, AwaitingRoll, Rejected, resolve


def run(conn, *, quest_id=GUILD_SHARD_ORDER, campaign_id=CAMPAIGN, player_id=PLAYER, content=CONTENT):
    return resolve(
        conn,
        campaign_id=campaign_id,
        player_id=player_id,
        action=SubmitQuestItems(quest_id=quest_id),
        content=content,
    )


def test_accepts_with_effects_from_the_content(conn, scenario):
    assert run(conn) == Accepted(
        [
            RemoveItems(item_id=CRYSTAL_SHARD, quantity=5),
            AddMoney(pennies=100),
            SetQuestStatus(quest_id=GUILD_SHARD_ORDER, status="completed"),
            AdvanceTime(minutes=10),
        ]
    )


def test_changing_the_content_changes_the_effects(conn, make_scenario):
    make_scenario(shards=2)
    content = custom_content(quantity=2, reward=250, minutes=3)
    assert run(conn, content=content) == Accepted(
        [
            RemoveItems(item_id=CRYSTAL_SHARD, quantity=2),
            AddMoney(pennies=250),
            SetQuestStatus(quest_id=GUILD_SHARD_ORDER, status="completed"),
            AdvanceTime(minutes=3),
        ]
    )


@pytest.mark.parametrize("owned", [5, 7])
def test_enough_items_are_accepted_and_only_the_required_amount_is_taken(conn, make_scenario, owned):
    make_scenario(shards=owned)
    result = run(conn)
    assert isinstance(result, Accepted)
    assert result.effects[0] == RemoveItems(item_id=CRYSTAL_SHARD, quantity=5)


def test_turn_in_location_comes_from_the_content(conn, scenario):
    result = run(conn, content=custom_content(location="market"))
    assert result == Rejected("wrong_location", result.message)


def test_never_writes(conn, scenario):
    add_player(conn, "player-2", location_id="street", shards=1, quest="active")
    before = dump_state(conn)
    run(conn)  # angenommen
    run(conn, player_id="player-2")  # abgelehnt
    run(conn, quest_id="nope")  # abgelehnt
    assert dump_state(conn) == before


@pytest.mark.parametrize(
    "code, kwargs, player",
    [
        ("unknown_quest", {"quest_id": "nope"}, {}),
        ("unknown_player", {"player_id": "ghost"}, {}),
        ("quest_not_active", {}, {"quest": None, "shards": 5}),  # nie angenommen
        ("quest_not_active", {}, {"quest": "completed", "shards": 5}),  # schon erledigt
        ("wrong_location", {}, {"quest": "active", "shards": 5, "location_id": "street"}),
        ("not_enough_items", {}, {"quest": "active", "shards": 4}),
        ("not_enough_items", {}, {"quest": "active", "shards": 0}),  # gar keine Splitter
    ],
)
def test_rejections(conn, code, kwargs, player):
    add_campaign(conn, CAMPAIGN)
    add_player(conn, PLAYER, **player)
    result = run(conn, **kwargs)
    assert isinstance(result, Rejected)
    assert result.code == code
    assert result.message


# Jeder fremde Datensatz hat genau einen Mangel. Der Standard-Spieler (campaign-1/player-1)
# hat keinen und darf abgeben. Sähe ein Resolver fremde Zeilen, ginge ein Mangel durch.
DEFECTS = {
    "quest_not_active": {"shards": 5, "quest": None},
    "not_enough_items": {"shards": 0, "quest": "active"},
    "wrong_location": {"shards": 5, "quest": "active", "location_id": "street"},
}


@pytest.mark.parametrize("code", DEFECTS)
def test_state_of_other_players_and_campaigns_does_not_leak(conn, scenario, code):
    add_player(conn, "other-player", **DEFECTS[code])  # gleiche Kampagne, anderer Spieler
    add_campaign(conn, "campaign-2")
    add_player(conn, PLAYER, campaign_id="campaign-2", **DEFECTS[code])  # andere Kampagne, gleiche player_id

    assert run(conn, player_id="other-player").code == code
    assert run(conn, campaign_id="campaign-2").code == code
    assert isinstance(run(conn), Accepted)


def test_unknown_action_type_is_an_error(conn, scenario):
    with pytest.raises(TypeError):
        resolve(conn, campaign_id=CAMPAIGN, player_id=PLAYER, action=object(), content=CONTENT)


# --- Probe ---


def run_action(conn, action, *, player_id=PLAYER, campaign_id=CAMPAIGN):
    return resolve(conn, campaign_id=campaign_id, player_id=player_id, action=action, content=CONTENT)


def an_attempt(skill_id=SKILL, difficulty="DIFF-03") -> AttemptAction:
    return AttemptAction(skill_id=skill_id, difficulty=difficulty, reason=REASON)


def open_the_roll(conn, **kwargs):
    """Eröffnet die Probe wirklich (über apply), damit der Resolver sie sieht."""
    resolution = run_action(conn, an_attempt(**kwargs))
    assert isinstance(resolution, AwaitingRoll)
    apply.apply_effects(conn, CAMPAIGN, PLAYER, resolution.effects)


def test_attempt_awaits_a_roll_with_dc_bonus_and_ticks(conn, roll_scenario):
    assert run_action(conn, an_attempt()) == AwaitingRoll(
        [OpenRoll(skill_id=SKILL, difficulty="DIFF-03", dc=12, bonus=5, ticks_on_success=2, reason=REASON)]
    )


@pytest.mark.parametrize(
    "skills, attributes, difficulty, bonus, dc, ticks",
    [
        ({SKILL: 0}, {}, "DIFF-03", 0, 12, 2),  # alles Durchschnitt
        ({}, {}, "DIFF-03", 0, 12, 2),  # Skill ohne Eintrag zählt 0
        ({SKILL: 18}, {"STR": 12}, "DIFF-04", 2, 14, 3),  # +1 (Skill) +1 (STR 12)
        ({SKILL: 30}, {"STR": 8, "GES": 14}, "DIFF-06", 5, 20, 6),  # GES ist besser
        ({SKILL: 30}, {"STR": 14}, "DIFF-02", 5, 10, 0),  # nur eine 5 nötig: Routine
        ({SKILL: 30}, {"STR": 8, "GES": 8}, "DIFF-07", 2, 24, 8),
    ],
)
def test_attempt_calculates_from_the_players_values(
    conn, make_roll_scenario, skills, attributes, difficulty, bonus, dc, ticks
):
    make_roll_scenario(skills=skills, attributes=attributes)
    opened = run_action(conn, an_attempt(difficulty=difficulty)).effects[0]
    assert (opened.bonus, opened.dc, opened.ticks_on_success) == (bonus, dc, ticks)


@pytest.mark.parametrize(
    "action, player_id, code",
    [
        (an_attempt(skill_id="SKILL-999"), PLAYER, "unknown_skill"),
        (an_attempt(skill_id="SKILL-501"), PLAYER, "unknown_skill"),
        (an_attempt(), "ghost", "unknown_player"),
        (SubmitRoll(value=10), PLAYER, "no_open_roll"),
        (SubmitRoll(value=10), "ghost", "no_open_roll"),
    ],
)
def test_attempt_and_roll_rejections(conn, roll_scenario, action, player_id, code):
    result = run_action(conn, action, player_id=player_id)
    assert isinstance(result, Rejected)
    assert result.code == code


def test_while_a_roll_is_open_other_actions_are_rejected_but_the_roll_is_not(conn, scenario):
    # `scenario` hat einen aktiven Auftrag; die Probe sperrt ihn trotzdem.
    add_player(conn, "player-2", skills={SKILL: 10})
    open_the_roll(conn)
    for action, player_id in [
        (an_attempt(), PLAYER),
        (an_attempt(), "player-2"),
        (SubmitQuestItems(quest_id=GUILD_SHARD_ORDER), PLAYER),
        (SubmitQuestItems(quest_id="nope"), PLAYER),  # die Sperre kommt vor jeder Regelprüfung
    ]:
        assert run_action(conn, action, player_id=player_id).code == "roll_pending"
    assert isinstance(run_action(conn, SubmitRoll(value=10)), Accepted)
    assert run_action(conn, SubmitRoll(value=10), player_id="player-2").code == "no_open_roll"


def test_roll_resolves_with_the_values_stored_at_opening(conn, roll_scenario):
    open_the_roll(conn)
    result = run_action(conn, SubmitRoll(value=7))
    assert result == Accepted(
        [ClearOpenRoll(), AdjustSkill(skill_id=SKILL, points=0, ticks=2)],
        CheckResult(die=7, bonus=5, total=12, dc=12, outcome="success", ticks=2, skill_points=0),
    )


@pytest.mark.parametrize("die", [1, 4, 5, 6])
def test_roll_without_success_only_closes_the_roll(conn, roll_scenario, die):
    open_the_roll(conn)
    result = run_action(conn, SubmitRoll(value=die))
    assert result.effects == [ClearOpenRoll()]
    assert result.check.ticks == 0


def test_roll_at_the_end_of_the_scale_changes_no_skill(conn, make_roll_scenario):
    make_roll_scenario(skills={SKILL: 100})
    open_the_roll(conn, difficulty="DIFF-07")  # SG 24, Bonus +12: nur eine 12 nötig
    result = run_action(conn, SubmitRoll(value=15))
    assert result.check.outcome == "success"
    assert result.effects == [ClearOpenRoll()]  # Ticks verfallen bei Skill 100, nichts zu ändern


def test_several_skill_ups_at_once_and_levels_are_counted_in_total(conn, make_roll_scenario):
    make_roll_scenario(skills={SKILL: 0}, attributes={"STR": 20})  # Bonus +5
    apply.apply_effects(conn, CAMPAIGN, PLAYER, [AdjustSkill(skill_id="SKILL-102", points=9, ticks=0)])
    open_the_roll(conn, difficulty="DIFF-07")  # SG 24, braucht 19; 8 Ticks = zwei Punkte (3 + 3)
    result = run_action(conn, SubmitRoll(value=19))
    assert result.check.skill_points == 2
    assert result.effects == [
        ClearOpenRoll(),
        AdjustSkill(skill_id=SKILL, points=2, ticks=2),  # 8 Ticks: 6 für zwei Punkte, 2 bleiben
        GrantAttributePoints(points=1),  # 9 + 2 = 11 Aufstiege: Level 2
    ]


def test_roll_resolution_never_writes(conn, roll_scenario):
    open_the_roll(conn)
    before = dump_state(conn)
    run_action(conn, SubmitRoll(value=7))
    run_action(conn, an_attempt())
    assert dump_state(conn) == before
