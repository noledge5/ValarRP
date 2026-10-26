"""Schritt 1g: Vergleichsprobe mit NPC (MECH-05, Entscheidungen 63 und 100). Die Nummern folgen dem README.

Testdaten: Die Spielfigur steht im Zunfthaus, GES 14 (+2) und Schleichen 30 (+3): Bonus +5.
Die Wache Ulf steht daneben, WEI 12 (+1) und Wahrnehmung 20 (+2): Bonus +3 auf Wahrnehmung.
"""

import pytest

from tests.helpers import (
    CAMPAIGN,
    PLAYER,
    add_campaign,
    add_player,
    assert_only_log_changed,
    campaign_row,
    dump_state,
    open_roll_row,
    roll,
    submit,
)
from valar import apply, queries, rules
from valar.content import BOOMTOWN, GUILD_HALL
from valar.models import Contest, CreateNpc

SEED = 5
SNEAK, PERCEPTION = "SKILL-201", "SKILL-405"


@pytest.fixture
def scene(conn):
    add_campaign(conn, CAMPAIGN, seed=SEED)
    add_player(conn, PLAYER, attributes={"GES": 14}, skills={SNEAK: 30})
    put_npc(conn, "ulf", GUILD_HALL)


def put_npc(conn, npc_id: str, location_id: str, *, wei: int = 12, perception: int = 20) -> None:
    """Testaufbau ohne Spielzug: ein NPC mit festen Werten."""
    conn.execute("BEGIN")
    apply.apply_effects(
        conn,
        CAMPAIGN,
        PLAYER,
        [
            CreateNpc(
                npc_id=npc_id,
                template_id="wache",
                name=npc_id.capitalize(),
                description="Eine Wache.",
                location_id=location_id,
                attributes={"WEI": wei},
                skills={PERCEPTION: perception},
                money_pennies=0,
            )
        ],
    )
    conn.execute("COMMIT")


def contest(conn, request_id: str, revision: int, *, skill_id: str = SNEAK, npc_id: str = "ulf", duration: str = "moment"):
    action = Contest(skill_id=skill_id, npc_id=npc_id, duration=duration, reason="An der Wache vorbei")
    return submit(conn, request_id, revision, action=action)


def npc_die(revision: int) -> int:
    """Der Wurf, den der Code dem NPC bei dieser Revision gibt."""
    return rules.turn_rng(SEED, revision).randint(1, 20)


# --- 1: Der NPC würfelt beim Öffnen, sein Gesamt ist der SG ---


def test_1_the_npc_rolls_on_opening_and_its_total_is_the_dc(conn, scene):
    result = contest(conn, "c1", 0)

    assert result.status == "awaiting_roll"
    npc = result.effects[0].npc_roll
    assert (npc.npc_id, npc.skill_id, npc.die, npc.bonus) == ("ulf", PERCEPTION, npc_die(0), 3)
    assert npc.total == npc.die + 3
    row = open_roll_row(conn)
    assert (row["skill_id"], row["dc"], row["bonus"]) == (SNEAK, npc.total, 5)
    # Vor dem Wurf sichtbar: Zustand zeigt SG und NPC-Wurf.
    shown = queries.status(conn, campaign_id=CAMPAIGN, player_id=PLAYER).open_roll
    assert (shown.dc, shown.npc_roll) == (npc.total, npc)


# --- 2: Paar-Tabelle, NPC am Ort ---


@pytest.mark.parametrize("skill_id, code", [("SKILL-408", "no_counter_skill"), ("SKILL-101", "no_counter_skill"), ("SKILL-999", "unknown_skill")])
def test_2_a_skill_without_pair_is_rejected(conn, scene, skill_id, code):
    before = dump_state(conn)
    assert contest(conn, "c1", 0, skill_id=skill_id).reject_code == code
    assert_only_log_changed(before, dump_state(conn))


def test_2_the_counter_skill_comes_from_the_pair_table(conn, scene):
    result = contest(conn, "c1", 0, skill_id="SKILL-302")  # Täuschen gegen Menschenkenntnis
    assert result.effects[0].npc_roll.skill_id == "SKILL-305"
    assert result.effects[0].npc_roll.bonus == 1  # Menschenkenntnis 0, WEI 12: +1


def test_2_the_npc_must_be_here(conn, scene):
    put_npc(conn, "berta", BOOMTOWN)
    before = dump_state(conn)

    assert contest(conn, "c1", 0, npc_id="berta").reject_code == "not_here"
    assert contest(conn, "c2", 0, npc_id="niemand").reject_code == "unknown_npc"
    log = {"turn", "sqlite_sequence"}
    after = dump_state(conn)
    assert {t: v for t, v in after.items() if t not in log} == {t: v for t, v in before.items() if t not in log}


# --- 3: Gleichstand gewinnt der Spieler ---


def test_3_a_tie_goes_to_the_player(conn, scene):
    dc = contest(conn, "c1", 0).effects[0].npc_roll.total
    needed = dc - 5
    assert 2 <= needed <= 19, "Testaufbau: Seed so wählen, dass der Gleichstand würfelbar ist"

    result = roll(conn, "c2", 1, value=needed)

    assert (result.check.total, result.check.dc, result.check.outcome) == (dc, dc, "success")


# --- 4: Ticks nach der Stufe des NPC-Gesamts ---


@pytest.mark.parametrize(
    "npc_total, bonus, ticks",
    [(7, 0, 0), (8, 0, 1), (12, 0, 2), (15, 0, 3), (19, 0, 4), (20, 0, 6), (30, 0, 8), (15, 10, 0), (16, 10, 4)],
)
def test_4_ticks_follow_the_highest_stage_the_npc_total_reaches(npc_total, bonus, ticks):
    assert rules.contest_ticks(npc_total, bonus) == ticks


def test_4_ticks_on_opening_follow_the_npc_total(conn, scene):
    opened = contest(conn, "c1", 0)
    total = opened.effects[0].npc_roll.total
    assert opened.effects[0].ticks_on_success == rules.contest_ticks(total, 5) > 0


def test_4_the_scene_rule_applies(conn, scene):
    # Schleichen hat in dieser Szene schon Ticks gebracht: keine weiteren (Entscheidung 58).
    shown = queries.status(conn, campaign_id=CAMPAIGN, player_id=PLAYER)
    conn.execute("UPDATE player_skill SET ticked_scene = ? WHERE skill_id = ?", (shown.scene_start, SNEAK))

    assert contest(conn, "c1", 0).effects[0].ticks_on_success == 0


# --- 5: Der NPC-Wurf bleibt ---


def test_5_the_npc_roll_stays_on_replay_and_after_npc_values_change(conn, scene, db_path):
    from valar import db

    first = contest(conn, "c1", 0)
    again = contest(conn, "c1", 0)
    assert again.replayed and again.effects == first.effects

    conn.execute("UPDATE npc_skill SET value = 100 WHERE npc_id = 'ulf'")
    conn.execute("UPDATE npc SET attr_wei = 20 WHERE npc_id = 'ulf'")
    conn.close()
    reopened = db.connect(db_path)
    try:
        assert open_roll_row(reopened)["dc"] == first.effects[0].npc_roll.total
        result = roll(reopened, "c2", 1, value=10)
        assert result.check.dc == first.effects[0].npc_roll.total
    finally:
        reopened.close()


def test_time_passes_by_duration_at_the_roll(conn, scene):
    contest(conn, "c1", 0, duration="hour")
    roll(conn, "c2", 1, value=1)  # Patzer: Fehlschlag, Zeit vergeht trotzdem
    assert campaign_row(conn)["game_minutes"] == 60


def test_the_model_cannot_set_dc_or_npc_roll():
    from pydantic import ValidationError

    from valar.models import ACTIONS

    base = {"type": "contest", "skill_id": SNEAK, "npc_id": "ulf", "duration": "moment", "reason": "x"}
    for extra in ({"dc": 5}, {"against": "SKILL-101"}, {"npc_die": 1}, {"difficulty": "DIFF-01"}):
        with pytest.raises(ValidationError):
            ACTIONS.validate_python(base | extra)


# --- Haltung des NPCs zählt als Bonus (Entscheidung 197) ---


@pytest.mark.parametrize("attitude, bonus", [(-2, 3), (0, 5), (2, 7)])
def test_the_attitude_of_the_npc_moves_the_players_bonus(conn, scene, attitude, bonus):
    conn.execute("INSERT INTO npc_bond (campaign_id, npc_id, player_id, attitude) VALUES (?, 'ulf', ?, ?)",
                 (CAMPAIGN, PLAYER, attitude))

    contest(conn, "c1", 0)

    assert open_roll_row(conn)["bonus"] == bonus
