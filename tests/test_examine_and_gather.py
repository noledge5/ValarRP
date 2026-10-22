"""Schritt 1e: Zufall, Probe mit Folgen, Untersuchen und Sammeln aus dem Wiki (Entscheidungen 61, 62, 64, 92).

Die Nummern folgen dem README. Test-Wiki und Testinhalt sind Testdaten: Sturmhagen mit Zunfthaus und Schenke
zeigt auf das Testdorf (Region Testland in Oberland, Landschaft Moor). Dort kommen Sumpfkraut (häufig, sammelbar),
Wolf (gelegentlich) und Dorfkatze (sehr selten) vor. Die Figur steht im Zunfthaus, alle Attribute 10, ohne Skills:
Naturkunde-Bonus 0, also ist ohne Wurf nur „Naturkunde leicht“ (SG 10) sichtbar.
"""

import json
from pathlib import Path

import pytest

from tests.helpers import (
    CAMPAIGN,
    PLAYER,
    TEST_WIKI,
    add_campaign,
    add_player,
    assert_only_log_changed,
    campaign_row,
    content_pointing_to,
    dump_state,
    examine,
    gather,
    last_turn,
    move,
    open_roll_row,
    page,
    roll,
    skill_row,
    write_wiki,
)
from valar import queries, wiki
from valar.content import CRYSTAL_FIELD, GUILD_HALL

CONTENT = content_pointing_to(boomtown="testdorf", crystal_field="anderswo")
GATHER_MINUTES = CONTENT.action_minutes["gather"]
EXAMINE_MINUTES = CONTENT.action_minutes["examine"]


@pytest.fixture
def test_wiki(tmp_path: Path) -> wiki.Wiki:
    write_wiki(tmp_path, TEST_WIKI)
    return wiki.load(tmp_path)


@pytest.fixture
def make_player(conn):
    def make(*, seed: int = 7, location_id: str = GUILD_HALL, **values) -> None:
        add_campaign(conn, CAMPAIGN, seed=seed)
        add_player(conn, PLAYER, location_id=location_id, **values)

    return make


@pytest.fixture
def play(conn, test_wiki):
    """Spielzüge mit Testinhalt und Test-Wiki: play(examine, "r1", 0, "sumpfkraut")."""

    def run(step, *args, **kwargs):
        return step(conn, *args, content=CONTENT, wiki=test_wiki, **kwargs)

    return run


def items(conn, item_id: str) -> int:
    row = conn.execute(
        "SELECT quantity FROM inventory WHERE campaign_id = ? AND player_id = ? AND item_id = ?",
        (CAMPAIGN, PLAYER, item_id),
    ).fetchone()
    return row["quantity"] if row else 0


def learned(conn) -> set[tuple[str, str, str]]:
    rows = conn.execute("SELECT page_id, skill_id, difficulty FROM player_knowledge").fetchall()
    return {tuple(row) for row in rows}


def known(conn, test_wiki, page_id: str) -> list[str]:
    seen = queries.examine(
        conn, campaign_id=CAMPAIGN, player_id=PLAYER, page_id=page_id, content=CONTENT, wiki=test_wiki
    )
    return seen.known


# --- 1: Zufall aus dem Seed ---


def gathered_with_seed(conn, play, seed: int) -> int:
    conn.execute("DELETE FROM inventory")
    conn.execute("DELETE FROM player_skill")
    conn.execute("DELETE FROM turn")
    conn.execute("DELETE FROM player")
    conn.execute("DELETE FROM campaign")
    add_campaign(conn, CAMPAIGN, seed=seed)
    add_player(conn, PLAYER)
    play(gather, "g1", 0, "sumpfkraut")
    play(roll, "g2", 1, value=20)
    return items(conn, "sumpfkraut")


def test_1_same_seed_same_result_other_seeds_differ(conn, play):
    first = gathered_with_seed(conn, play, 7)

    assert gathered_with_seed(conn, play, 7) == first
    assert 1 <= first <= 6  # häufig: 1 bis 6
    assert len({gathered_with_seed(conn, play, seed) for seed in range(20)}) > 1


def test_1_what_the_code_rolls_is_in_the_turn_and_a_replay_does_not_roll_again(conn, make_player, play):
    make_player()

    opened = play(gather, "g1", 0, "sumpfkraut")
    quantity = opened.effects[0].on_success[0].quantity

    stored = json.loads(last_turn(conn)["effects_json"])
    assert stored[0]["on_success"] == [{"type": "add_items", "item_id": "sumpfkraut", "quantity": quantity}]
    again = play(gather, "g1", 0, "sumpfkraut")
    assert again.replayed and again.effects == opened.effects
    play(roll, "g2", 1, value=15)
    assert items(conn, "sumpfkraut") == quantity


# --- 2: Untersuchen ohne Stufen ---


def test_2_looking_at_a_page_without_stages_is_a_query_and_changes_nothing(conn, make_player, play, test_wiki):
    make_player()
    before = dump_state(conn)

    seen = queries.examine(
        conn, campaign_id=CAMPAIGN, player_id=PLAYER, page_id="dorfkatze", content=CONTENT, wiki=test_wiki
    )

    assert (seen.title, seen.type, seen.text, seen.known) == ("dorfkatze", "Tier", "Text.", [])
    assert dump_state(conn) == before

    result = play(examine, "e1", 0, "dorfkatze")
    assert (result.status, result.reject_code) == ("rejected", "nothing_to_learn")


# --- 3: Wissensstufen ---


def test_3_a_stage_within_ten_plus_bonus_is_known_without_a_roll(conn, make_player, test_wiki):
    make_player()
    assert known(conn, test_wiki, "sumpfkraut") == ["Naturkunde leicht."]

    # Naturkunde 20 und WEI 14: Bonus +4, also 14 ohne Wurf; auch beim Umsehen.
    conn.execute("UPDATE player SET attr_wei = 14")
    conn.execute("INSERT INTO player_skill (campaign_id, player_id, skill_id, value) VALUES (?, ?, 'SKILL-408', 20)",
                 (CAMPAIGN, PLAYER))
    assert known(conn, test_wiki, "sumpfkraut") == ["Naturkunde leicht.", "Naturkunde schwer."]
    seen = queries.look(conn, campaign_id=CAMPAIGN, player_id=PLAYER, content=CONTENT, wiki=test_wiki)
    herb = next(thing for thing in seen.things if thing.page_id == "sumpfkraut")
    assert herb.known == ["Naturkunde leicht.", "Naturkunde schwer."]
    assert learned(conn) == set()  # ohne Wurf wird nichts gespeichert


def test_3_examine_probes_the_next_unknown_stage(conn, make_player, play):
    make_player()

    opened = play(examine, "e1", 0, "sumpfkraut")

    assert opened.status == "awaiting_roll"
    row = open_roll_row(conn)
    assert (row["skill_id"], row["difficulty"], row["dc"], row["minutes"]) == ("SKILL-408", "DIFF-04", 14, EXAMINE_MINUTES)
    assert row["reason"] == "Sumpfkraut untersuchen"


@pytest.mark.parametrize(
    "die, expected",
    [
        (14, ["Naturkunde leicht.", "Naturkunde schwer."]),
        (20, ["Naturkunde leicht.", "Naturkunde schwer.", "Naturkunde extrem."]),
    ],
)
def test_3_the_total_opens_all_stages_of_that_skill_up_to_its_height(conn, make_player, play, test_wiki, die, expected):
    make_player()
    play(examine, "e1", 0, "sumpfkraut")

    result = play(roll, "e2", 1, value=die)

    assert result.check.outcome in ("success", "critical_success")
    assert known(conn, test_wiki, "sumpfkraut") == expected
    assert ("sumpfkraut", "SKILL-407", "DIFF-07") not in learned(conn)  # anderer Skill bleibt zu
    assert campaign_row(conn)["game_minutes"] == EXAMINE_MINUTES


def test_3_learned_stays_learned_and_is_not_probed_again(conn, make_player, play, test_wiki):
    make_player()
    play(examine, "e1", 0, "sumpfkraut")
    play(roll, "e2", 1, value=14)

    opened = play(examine, "e3", 2, "sumpfkraut")  # weiter mit der nächsten unbekannten Stufe

    assert (open_roll_row(conn)["difficulty"], open_roll_row(conn)["skill_id"]) == ("DIFF-06", "SKILL-408")
    play(roll, "e4", 3, value=20)
    play(examine, "e5", 4, "sumpfkraut")
    assert open_roll_row(conn)["skill_id"] == "SKILL-407"
    play(roll, "e6", 5, value=20)  # natürliche 20: Erfolg, auch unter dem SG
    assert known(conn, test_wiki, "sumpfkraut")[-1] == "Alchemie legendär."

    before = dump_state(conn)
    result = play(examine, "e7", 6, "sumpfkraut")
    assert result.reject_code == "nothing_to_learn"
    assert_only_log_changed(before, dump_state(conn))
    assert opened.status == "awaiting_roll"


def test_3_only_pages_at_the_place_can_be_examined(conn, make_player, play):
    make_player()
    before = dump_state(conn)

    assert play(examine, "e1", 0, "nirgendwo").reject_code == "unknown_page"
    assert play(examine, "e2", 0, "auerochse").reject_code == "not_here"  # veraltet, kommt nicht mehr vor
    assert play(examine, "e3", 0, "anderswo").reject_code == "not_here"

    log = {"turn", "sqlite_sequence"}
    state = dump_state(conn)
    assert {t: v for t, v in state.items() if t not in log} == {t: v for t, v in before.items() if t not in log}


# --- 4: Versteckte Dinge ---


def test_4_hidden_things_are_stages_with_perception(conn, make_player, play, test_wiki):
    make_player()
    seen = queries.look(conn, campaign_id=CAMPAIGN, player_id=PLAYER, content=CONTENT, wiki=test_wiki)
    assert seen.known_here == []

    play(examine, "e1", 0, "testdorf")
    assert open_roll_row(conn)["skill_id"] == "SKILL-405"
    play(roll, "e2", 1, value=12)

    seen = queries.look(conn, campaign_id=CAMPAIGN, player_id=PLAYER, content=CONTENT, wiki=test_wiki)
    assert seen.known_here == ["Unter der Brücke liegt ein Versteck."]


def test_4_sharp_eyes_see_hidden_things_without_a_roll(conn, make_player, test_wiki):
    make_player(attributes={"WEI": 14})  # Wahrnehmung 0, WEI +2: 12 ohne Wurf
    seen = queries.look(conn, campaign_id=CAMPAIGN, player_id=PLAYER, content=CONTENT, wiki=test_wiki)
    assert seen.known_here == ["Unter der Brücke liegt ein Versteck."]


# --- 5: Sammeln ---


def test_5_gathering_what_occurs_here_brings_items_and_costs_time(conn, make_player, play):
    make_player()

    opened = play(gather, "g1", 0, "sumpfkraut")
    quantity = opened.effects[0].on_success[0].quantity
    row = open_roll_row(conn)
    # Sumpfkraut ist im Moor häufig: leicht (SG 10), Naturkunde aus dem Feld gather_skill.
    assert (row["skill_id"], row["difficulty"], row["dc"]) == ("SKILL-408", "DIFF-02", 10)
    result = play(roll, "g2", 1, value=12)

    assert result.check.outcome == "success"
    assert items(conn, "sumpfkraut") == quantity
    assert campaign_row(conn)["game_minutes"] == GATHER_MINUTES
    assert skill_row(conn, "SKILL-408") is not None  # Ticks wie bei jeder Probe


def test_5_the_most_specific_occurrence_sets_the_difficulty(conn, make_player, play, tmp_path):
    # Im Moor häufig, im Testdorf selbst aber sehr selten: das Testdorf zählt.
    pages = TEST_WIKI | {
        "pflanzen/sumpfkraut.md": page(
            "Pflanze",
            "occurs:\n  - {where: moor, frequency: common}\n  - {where: testdorf, frequency: very_rare}\n"
            "item: {value_pennies: 3, gather_skill: SKILL-408}",
        )
    }
    write_wiki(tmp_path / "w", pages)
    make_player()

    gather(conn, "g1", 0, "sumpfkraut", content=CONTENT, wiki=wiki.load(tmp_path / "w"))

    assert open_roll_row(conn)["difficulty"] == "DIFF-05"
    assert json.loads(open_roll_row(conn)["on_success_json"])[0]["quantity"] == 1


@pytest.mark.parametrize(
    "page_id, code",
    [("nirgendwo", "unknown_page"), ("wolf", "not_gatherable"), ("testdorf", "not_gatherable")],
)
def test_5_unknown_or_not_gatherable_is_rejected(conn, make_player, play, page_id, code):
    make_player()
    before = dump_state(conn)

    result = play(gather, "g1", 0, page_id)

    assert (result.status, result.reject_code) == ("rejected", code)
    assert_only_log_changed(before, dump_state(conn))


def test_5_what_does_not_occur_here_cannot_be_gathered(conn, make_player, play):
    make_player(location_id=CRYSTAL_FIELD)  # anderswo: Oberland, Wald; Sumpfkraut wächst im Moor
    before = dump_state(conn)

    result = play(gather, "g1", 0, "sumpfkraut")

    assert result.reject_code == "not_here"
    assert_only_log_changed(before, dump_state(conn))


def test_5_an_open_roll_blocks_examine_and_gather(conn, make_player, play):
    make_player()
    play(gather, "g1", 0, "sumpfkraut")
    assert play(examine, "g2", 1, "sumpfkraut").reject_code == "roll_pending"
    assert play(gather, "g3", 1, "sumpfkraut").reject_code == "roll_pending"


# --- 6: Knapp daneben und Patzer ---


@pytest.mark.parametrize("die, outcome", [(8, "near_miss"), (1, "fumble"), (5, "failure")])
def test_6_near_miss_and_fumble_bring_nothing_but_time_passes(conn, make_player, play, die, outcome):
    make_player()
    play(gather, "g1", 0, "sumpfkraut")

    result = play(roll, "g2", 1, value=die)

    assert result.check.outcome == outcome
    assert items(conn, "sumpfkraut") == 0
    assert campaign_row(conn)["game_minutes"] == GATHER_MINUTES


# --- 7: Folgen wie beim Öffnen gespeichert ---


def test_7_the_roll_applies_what_was_stored_even_if_the_wiki_changes(conn, make_player, play, tmp_path):
    make_player()
    opened = play(gather, "g1", 0, "sumpfkraut")
    quantity = opened.effects[0].on_success[0].quantity
    play(examine, "x", 1, "sumpfkraut")  # abgelehnt: Probe offen
    changed = TEST_WIKI | {"pflanzen/sumpfkraut.md": page("Pflanze")}  # kein Gegenstand mehr
    write_wiki(tmp_path / "w", changed)

    roll(conn, "g2", 1, value=15, content=CONTENT, wiki=wiki.load(tmp_path / "w"))

    assert items(conn, "sumpfkraut") == quantity


def test_7_examine_keeps_the_stages_from_opening(conn, make_player, play, tmp_path, test_wiki):
    make_player()
    play(examine, "e1", 0, "sumpfkraut")  # Naturkunde schwer (SG 14) und extrem (SG 20) möglich
    conn.execute("UPDATE player SET attr_int = 20")  # Bonus steigt nach dem Öffnen: zählt nicht

    result = play(roll, "e2", 1, value=15)

    assert result.check.total == 15
    assert ("sumpfkraut", "SKILL-408", "DIFF-06") not in learned(conn)
    assert ("sumpfkraut", "SKILL-408", "DIFF-04") in learned(conn)


def test_moving_away_does_not_forget_what_was_learned(conn, make_player, play, test_wiki):
    make_player()
    play(examine, "e1", 0, "sumpfkraut")
    play(roll, "e2", 1, value=20)
    play(move, "m1", 2, "boomtown")

    assert known(conn, test_wiki, "sumpfkraut")[-1] == "Naturkunde extrem."
