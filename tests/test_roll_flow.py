"""Schritt 1b: Probenablauf (offene Probe, analoger Wurf, Ticks, Skill-Aufstieg).

Ausgangslage (roll_scenario): Bonus +5, also reicht gegen SG 12 (DIFF-03) eine 7.
"""

import pytest

from tests.helpers import (
    CAMPAIGN,
    PLAYER,
    REASON,
    SKILL,
    add_player,
    assert_only_log_changed,
    attempt,
    attribute_points,
    campaign_row,
    dump_state,
    last_turn,
    open_roll_row,
    roll,
    skill_row,
    submit,
    total_ups,
    turn_count,
)
from valar import apply, db, rules
from valar.models import (
    AdjustSkill,
    AdvanceTime,
    CheckResult,
    ClearOpenRoll,
    GrantAttributePoints,
    MarkSkillScene,
    OpenRoll,
)

MOMENT = AdvanceTime(minutes=5)  # Standarddauer der Versuche in den Hilfen: Augenblick


def opened_roll() -> OpenRoll:
    return OpenRoll(
        skill_id=SKILL, difficulty="DIFF-03", dc=12, bonus=5, ticks_on_success=2, reason=REASON,
        minutes=5, scene=0,
    )


def test_1_attempt_opens_a_roll_and_shows_skill_dc_and_reason(conn, roll_scenario):
    result = attempt(conn, "req-1", expected_revision=0)

    assert result.status == "awaiting_roll"
    assert result.check is None
    assert result.revision == 1
    assert result.effects == [opened_roll()]  # Skill, SG, Bonus, mögliche Ticks und Grund
    row = open_roll_row(conn)
    assert (row["player_id"], row["skill_id"], row["dc"], row["bonus"]) == (PLAYER, SKILL, 12, 5)
    assert skill_row(conn) == (30, 0, 0)  # noch keine Wirkung auf den Skill
    assert tuple(campaign_row(conn)) == (1, 0)
    turn = last_turn(conn)
    assert (turn["status"], turn["revision_before"], turn["revision_after"]) == ("awaiting_roll", 0, 1)


def test_2_roll_resolves_the_check_and_stores_it_with_the_turn(conn, roll_scenario):
    attempt(conn, "req-2a", expected_revision=0)

    result = roll(conn, "req-2b", expected_revision=1, value=7)

    expected_check = CheckResult(
        die=7, bonus=5, total=12, dc=12, outcome="success", ticks=2, skill_points=0
    )
    assert result.status == "accepted"
    assert result.check == expected_check
    assert result.effects == [
        ClearOpenRoll(),
        AdjustSkill(skill_id=SKILL, points=0, ticks=2),
        MarkSkillScene(skill_id=SKILL, scene=0),
        MOMENT,
    ]
    assert result.revision == 2
    assert open_roll_row(conn) is None
    assert skill_row(conn) == (30, 2, 0)
    turn = last_turn(conn)
    assert (turn["status"], turn["revision_before"], turn["revision_after"]) == ("accepted", 1, 2)
    assert CheckResult.model_validate_json(turn["check_json"]) == expected_check


@pytest.mark.parametrize(
    "die, outcome, ticks",
    [
        (20, "critical_success", 2),  # natürliche 20: auch ohne Summe ein Erfolg
        (7, "success", 2),  # 7 + 5 = 12 = SG
        (6, "near_miss", 0),  # SG - 1
        (5, "near_miss", 0),  # SG - 2
        (4, "failure", 0),  # SG - 3
        (2, "failure", 0),
        (1, "fumble", 0),  # natürliche 1
    ],
)
def test_3_outcomes_and_ticks_only_on_success(conn, roll_scenario, die, outcome, ticks):
    attempt(conn, "req-3a", expected_revision=0)

    result = roll(conn, "req-3b", expected_revision=1, value=die)

    assert result.check.outcome == outcome
    assert result.check.ticks == ticks
    assert skill_row(conn) == (30, ticks, 0)
    assert open_roll_row(conn) is None  # auch ein Fehlschlag schließt die Probe


def test_4_while_a_roll_is_open_every_other_action_is_rejected(conn, roll_scenario):
    add_player(conn, "player-2")
    attempt(conn, "req-4a", expected_revision=0)
    before = dump_state(conn)

    second_attempt = attempt(conn, "req-4b", expected_revision=1)
    assert (second_attempt.status, second_attempt.reject_code) == ("rejected", "roll_pending")
    assert_only_log_changed(before, dump_state(conn))

    quest = submit(conn, "req-4c", expected_revision=1)  # eine ganz andere Aktion
    assert (quest.status, quest.reject_code) == ("rejected", "roll_pending")

    # Auch eine andere Spielfigur: Es ist höchstens eine Probe je Kampagne offen.
    other_player = attempt(conn, "req-4d", expected_revision=1, player_id="player-2")
    assert other_player.reject_code == "roll_pending"

    assert open_roll_row(conn)["player_id"] == PLAYER
    assert campaign_row(conn)["revision"] == 1


def test_5_roll_without_an_open_roll_is_rejected(conn, roll_scenario):
    before = dump_state(conn)

    result = roll(conn, "req-5", expected_revision=0, value=15)

    assert (result.status, result.reject_code) == ("rejected", "no_open_roll")
    assert_only_log_changed(before, dump_state(conn))


def test_5b_roll_of_another_player_is_rejected(conn, roll_scenario):
    add_player(conn, "player-2")
    attempt(conn, "req-5a", expected_revision=0)

    result = roll(conn, "req-5b", expected_revision=1, value=15, player_id="player-2")

    assert result.reject_code == "no_open_roll"
    assert open_roll_row(conn) is not None


def test_6_dc_and_bonus_stay_as_shown_to_the_player(conn, roll_scenario):
    attempt(conn, "req-6a", expected_revision=0)
    # Zwischen Öffnen und Wurf ändert sich der Skill (hier von außen, z. B. durch einen Admin-Eingriff).
    apply.apply_effects(conn, CAMPAIGN, PLAYER, [AdjustSkill(skill_id=SKILL, points=20, ticks=0)])
    assert skill_row(conn)[0] == 50  # Bonus wäre jetzt +7

    result = roll(conn, "req-6b", expected_revision=1, value=7)

    assert (result.check.bonus, result.check.dc, result.check.total) == (5, 12, 12)
    assert result.check.outcome == "success"


def test_7_ticks_become_a_skill_up_with_carry_over(conn, make_roll_scenario):
    make_roll_scenario(skills={SKILL: 19})  # Bonus +2 (STR 14) +1 (Skill 19) = +3
    attempt(conn, "req-7a", expected_revision=0, difficulty="DIFF-05")  # SG 16, 4 Ticks

    result = roll(conn, "req-7b", expected_revision=1, value=13)  # 13 + 3 = 16

    assert result.check.outcome == "success"
    assert (result.check.ticks, result.check.skill_points) == (4, 1)
    # Skill 20 kostet 3 Ticks; 1 Tick bleibt übrig.
    assert result.effects[:2] == [ClearOpenRoll(), AdjustSkill(skill_id=SKILL, points=1, ticks=1)]
    assert skill_row(conn) == (20, 1, 1)
    assert attribute_points(conn) == 0


@pytest.mark.parametrize("ups_before, level_up", [(8, False), (9, True)])
def test_7b_tenth_skill_up_gives_a_level_and_an_attribute_point(
    conn, make_roll_scenario, ups_before, level_up
):
    make_roll_scenario(skills={SKILL: 19})
    # Frühere Aufstiege in einem anderen Skill.
    apply.apply_effects(
        conn, CAMPAIGN, PLAYER, [AdjustSkill(skill_id="SKILL-102", points=ups_before, ticks=0)]
    )
    attempt(conn, "req-7a", expected_revision=0, difficulty="DIFF-05")

    result = roll(conn, "req-7b", expected_revision=1, value=13)

    assert result.check.skill_points == 1
    assert total_ups(conn) == ups_before + 1
    assert (GrantAttributePoints(points=1) in result.effects) is level_up
    assert attribute_points(conn) == (1 if level_up else 0)
    assert rules.level(total_ups(conn)) == (2 if level_up else 1)


def test_8_routine_gives_no_ticks(conn, roll_scenario):
    # DIFF-01 hat SG 8; mit Bonus +5 reicht eine 3. Das ist Routine.
    opened = attempt(conn, "req-8a", expected_revision=0, difficulty="DIFF-01")
    assert opened.effects[0].ticks_on_success == 0

    result = roll(conn, "req-8b", expected_revision=1, value=10)

    assert result.check.outcome == "success"
    assert result.check.ticks == 0
    assert result.effects == [ClearOpenRoll(), MOMENT]  # Routine verbraucht die Szene nicht
    assert skill_row(conn) == (30, 0, 0)


def test_9_duplicate_roll_replays_the_result_without_effect(conn, roll_scenario):
    attempt(conn, "req-9a", expected_revision=0)
    first = roll(conn, "req-9b", expected_revision=1, value=7)
    after_first = dump_state(conn)

    again = roll(conn, "req-9b", expected_revision=1, value=7)

    assert again.replayed
    assert again.model_copy(update={"replayed": False}) == first
    assert again.check == first.check  # der gespeicherte Wurf kommt zurück
    assert dump_state(conn) == after_first
    assert skill_row(conn) == (30, 2, 0)  # Ticks nur einmal


def test_9b_error_while_applying_a_roll_rolls_everything_back(conn, roll_scenario, monkeypatch):
    attempt(conn, "req-9a", expected_revision=0)
    before = dump_state(conn)
    inside: dict[str, bool] = {}

    def failing_adjust_skill(c, *args, **kwargs):
        # Die Probe ist in dieser Transaktion schon geschlossen, erst jetzt fällt der Fehler.
        inside["roll_closed"] = open_roll_row(c) is None
        raise RuntimeError("Fehler beim Skill")

    monkeypatch.setattr(apply, "_adjust_skill", failing_adjust_skill)

    with pytest.raises(RuntimeError, match="Fehler beim Skill"):
        roll(conn, "req-9b", expected_revision=1, value=7)

    assert inside["roll_closed"]
    assert not conn.in_transaction
    assert dump_state(conn) == before  # Probe wieder offen, kein Tick, kein Protokolleintrag
    assert open_roll_row(conn) is not None

    monkeypatch.undo()
    retry = roll(conn, "req-9b", expected_revision=1, value=7)  # request_id ist noch frei
    assert retry.status == "accepted"
    assert skill_row(conn) == (30, 2, 0)


def test_10_roll_with_a_stale_revision_is_rejected_and_the_roll_stays_open(conn, roll_scenario):
    attempt(conn, "req-10a", expected_revision=0)
    before = dump_state(conn)

    stale = roll(conn, "req-10b", expected_revision=0, value=7)  # der Client kennt Revision 1 nicht

    assert (stale.status, stale.reject_code) == ("rejected", "stale_revision")
    assert_only_log_changed(before, dump_state(conn))
    assert open_roll_row(conn) is not None

    retry = roll(conn, "req-10c", expected_revision=1, value=7)
    assert retry.status == "accepted"


def test_11_open_roll_survives_closing_and_reopening_the_database(conn, roll_scenario, db_path):
    opened = attempt(conn, "req-11a", expected_revision=0)
    saved = dump_state(conn)
    conn.close()

    reopened = db.connect(db_path)
    try:
        db.init_schema(reopened)
        assert dump_state(reopened) == saved
        assert open_roll_row(reopened) is not None

        again = attempt(reopened, "req-11a", expected_revision=0)  # Duplikat-Schutz gilt weiter
        assert again.replayed
        assert again.model_copy(update={"replayed": False}) == opened

        result = roll(reopened, "req-11b", expected_revision=1, value=7)
        assert result.check.outcome == "success"
        assert skill_row(reopened) == (30, 2, 0)
    finally:
        reopened.close()


@pytest.mark.parametrize("skill_id", ["SKILL-999", "SKILL-501"])  # 501 (Ziehen) folgt mit der Begabung
def test_12_unknown_skill_is_rejected_without_opening_a_roll(conn, roll_scenario, skill_id):
    before = dump_state(conn)

    result = attempt(conn, "req-12", expected_revision=0, skill_id=skill_id)

    assert (result.status, result.reject_code) == ("rejected", "unknown_skill")
    assert_only_log_changed(before, dump_state(conn))
    assert open_roll_row(conn) is None
    assert turn_count(conn) == 1
