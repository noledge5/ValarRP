import pytest

from tests.helpers import CAMPAIGN, PLAYER, add_campaign, add_player, custom_content, dump_state
from valar.content import CONTENT, CRYSTAL_SHARD, GUILD_SHARD_ORDER
from valar.models import (
    AddMoney,
    AdvanceTime,
    RemoveItems,
    SetQuestStatus,
    SubmitQuestItems,
)
from valar.resolve import Accepted, Rejected, resolve


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
