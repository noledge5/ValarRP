"""Schritt 1i: Charaktererschaffung und Attributspunkte verteilen (CHAR, Entscheidungen 30, 31, 33, 49, 148 bis 152).

Die Nummern folgen dem README. Testdaten: eine Testausrüstung mit 3 Gulden, 2 Rationen und einem Seil;
neue Figuren beginnen im Zunfthaus.
"""

import io
import random

import pytest
from pydantic import ValidationError

from tests.helpers import (
    CAMPAIGN,
    PLAYER,
    add_campaign,
    add_player,
    assert_only_log_changed,
    attempt,
    attribute_points,
    campaign_row,
    dump_state,
    submit,
)
from valar import apply, character, cli, context, db, queries, rules
from valar.character import CharacterError, CharacterSheet
from valar.content import CONTENT, GUILD_HALL, RATION, Content, StartKit
from valar.models import ACTIONS, GrantAttributePoints, RaiseAttribute

CONTENT_1I = Content(
    **(
        dict(CONTENT)
        | {
            "start_location_id": GUILD_HALL,
            "start_kits": {
                "testkit": StartKit(kit_id="testkit", name="Testkit", money_pennies=300, items={RATION: 2, "seil": 1}),
                "leer": StartKit(kit_id="leer", name="Leer", money_pennies=0),
            },
        }
    )
)
ATTRIBUTES = {"STR": 14, "GES": 12, "KON": 10, "WEI": 12, "CHA": 10}  # 6 + 4 + 2 + 4 + 2 = 18 Punkte
MAIN = ("SKILL-101", "SKILL-405")
SIDE = ("SKILL-201", "SKILL-402", "SKILL-403", "SKILL-408")


def sheet(**changes) -> CharacterSheet:
    fields = {
        "name": "Alrik Weidner",
        "attributes": ATTRIBUTES,
        "main_skills": MAIN,
        "side_skills": SIDE,
        "kit_id": "testkit",
        "keepsake": "Ein abgegriffener Holzwürfel",
        "background": "Sohn eines Köhlers aus dem Moor, schuldet dem Wirt in Sturmhagen Geld.",
    }
    return CharacterSheet(**(fields | changes))


def create(conn, **changes) -> str:
    return apply.create_character(conn, CAMPAIGN, sheet(**changes), CONTENT_1I)


def rejected(conn, code: str, **changes) -> None:
    """Abweichung: abgelehnt mit `code`, nichts geändert."""
    before = dump_state(conn)
    with pytest.raises(CharacterError) as error:
        create(conn, **changes)
    assert error.value.code == code
    assert dump_state(conn) == before


def status(conn, player_id: str = "alrik-weidner") -> queries.StatusResult:
    return queries.status(conn, campaign_id=CAMPAIGN, player_id=player_id, content=CONTENT_1I)


@pytest.fixture
def campaign(conn):
    add_campaign(conn, CAMPAIGN)


# --- 1: Punktekauf ---


def test_1_point_buy_all_at_eight_plus_eighteen(conn, campaign):
    player_id = create(conn)

    assert player_id == "alrik-weidner"
    assert status(conn).attributes == {"STR": 14, "GES": 12, "KON": 10, "INT": 8, "WEI": 12, "CHA": 10}
    assert rules.attribute_points_spent(status(conn).attributes) == rules.START_ATTRIBUTE_POINTS


@pytest.mark.parametrize(
    "attributes, code",
    [
        ({"STR": 14, "GES": 12, "KON": 10, "WEI": 12, "CHA": 9}, "points_not_spent"),  # 17 Punkte
        ({"STR": 14, "GES": 12, "KON": 10, "WEI": 12, "CHA": 11}, "points_not_spent"),  # 19 Punkte
        ({"STR": 16, "GES": 12, "KON": 10, "WEI": 10, "CHA": 10}, "attribute_range"),  # über 15
        ({"STR": 15, "GES": 15, "KON": 7, "WEI": 13, "CHA": 8}, "attribute_range"),  # unter 8
        ({"STR": 14, "GES": 12, "KON": 10, "WEI": 12, "MAG": 10}, "unknown_attribute"),
        ({}, "points_not_spent"),
    ],
)
def test_1_any_deviation_is_rejected_and_changes_nothing(conn, campaign, attributes, code):
    rejected(conn, code, attributes=attributes)


def test_1_fifteen_is_allowed(conn, campaign):
    create(conn, attributes={"STR": 15, "GES": 15, "KON": 10, "WEI": 10})  # 7 + 7 + 2 + 2 = 18
    assert status(conn).attributes["STR"] == 15


# --- 2: Skills ---


def test_2_two_main_skills_at_thirty_and_four_side_skills_at_fifteen(conn, campaign):
    create(conn)

    values = {skill.skill_id: skill.value for skill in status(conn).skills}
    assert values == {"SKILL-101": 30, "SKILL-405": 30} | {skill: 15 for skill in SIDE}
    # Beispiel aus CHAR: Hauptskill 30 (+3) und STR 14 (+2) ergeben +5.
    attempt(conn, "a1", 0, player_id="alrik-weidner", content=CONTENT_1I)
    assert status(conn).open_roll.bonus == 5


@pytest.mark.parametrize(
    "main, side, code",
    [
        (("SKILL-101",), SIDE, "skill_count"),
        (MAIN, SIDE[:3], "skill_count"),
        (MAIN, (*SIDE, "SKILL-301"), "skill_count"),
        (("SKILL-101", "SKILL-101"), SIDE, "skill_twice"),
        (MAIN, ("SKILL-101", *SIDE[1:]), "skill_twice"),
        (("SKILL-101", "SKILL-999"), SIDE, "unknown_skill"),
        (("SKILL-101", "SKILL-501"), SIDE, "skill_locked"),  # Ziehen: gesperrt, bis Energie Zahlen hat
        (MAIN, ("SKILL-501", *SIDE[1:]), "skill_locked"),
    ],
)
def test_2_wrong_skill_choices_are_rejected(conn, campaign, main, side, code):
    rejected(conn, code, main_skills=main, side_skills=side)


# --- 3: Startbudget, Ausrüstung, Hintergrund ---


def test_3_money_and_items_come_from_the_kit_background_is_soft_state(conn, campaign):
    create(conn)

    shown = status(conn)
    assert shown.money_pennies == 300
    assert shown.inventory == {RATION: 2, "seil": 1}
    assert (shown.location_id, shown.level, shown.attribute_points) == (GUILD_HALL, 1, 0)
    assert shown.keepsake == "Ein abgegriffener Holzwürfel"
    assert shown.background.startswith("Sohn eines Köhlers")
    assert shown.quests == {}


def test_3_the_model_sees_background_and_keepsake(conn, campaign):
    create(conn)
    built = context.build_context(conn, campaign_id=CAMPAIGN, player_id="alrik-weidner", content=CONTENT_1I)

    assert ("figur", "figur", "full") in [(i.section, i.key, i.level) for i in built.items]
    assert "Hintergrund: Sohn eines Köhlers" in built.render()
    assert "Persönlicher Gegenstand: Ein abgegriffener Holzwürfel" in built.render()


def test_3_an_empty_kit_and_no_background_are_fine(conn, campaign):
    create(conn, kit_id="leer", background="")
    assert (status(conn).money_pennies, status(conn).inventory, status(conn).background) == (0, {}, "")


def test_3_unknown_kit_is_rejected(conn, campaign):
    rejected(conn, "unknown_kit", kit_id="koenigsschatz")


def test_3_creation_is_not_a_turn(conn, campaign):
    create(conn)
    assert (campaign_row(conn)["revision"], campaign_row(conn)["game_minutes"]) == (0, 0)
    assert conn.execute("SELECT COUNT(*) FROM turn").fetchone()[0] == 0


def test_3_a_new_figure_arrives_at_the_current_time_rested(conn, campaign):
    conn.execute("UPDATE campaign SET game_minutes = 600")
    create(conn)
    shown = status(conn)
    assert (shown.scene_start, shown.condition.awake_hours) == (600, 0)


def test_3_names_are_unique_per_campaign(conn, campaign):
    create(conn)
    rejected(conn, "player_exists", name="alrik  WEIDNER")  # gleiche ID
    rejected(conn, "invalid_name", name="!!!")


def test_3_unknown_campaign_or_missing_start_location(conn):
    rejected(conn, "unknown_campaign")
    add_campaign(conn, CAMPAIGN)
    before = dump_state(conn)
    with pytest.raises(CharacterError) as error:
        apply.create_character(conn, CAMPAIGN, sheet(), Content(**(dict(CONTENT_1I) | {"start_location_id": None})))
    assert error.value.code == "no_start_location" and dump_state(conn) == before


@pytest.mark.parametrize(
    "changes",
    [
        {"name": ""},
        {"name": "x" * 41},
        {"name": "Alrik\x1b[2J"},  # Steuerzeichen
        {"keepsake": "  "},
        {"background": "x" * 601},
        {"background": "Zeile\x00"},
        {"level": 5},
    ],
)
def test_3_the_sheet_itself_has_limits(changes):
    with pytest.raises(ValidationError):
        sheet(**changes)


def test_3_rollback_on_error(conn, campaign, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("Absturz mitten im Anlegen")

    before = dump_state(conn)
    monkeypatch.setattr(apply, "apply_effects", broken)
    with pytest.raises(RuntimeError):
        create(conn)
    assert dump_state(conn) == before


def test_3_the_figure_survives_reopening(conn, campaign, db_path):
    create(conn)
    conn.close()
    reopened = db.connect(db_path)
    try:
        assert status(reopened).attributes["STR"] == 14
        assert status(reopened).keepsake == "Ein abgegriffener Holzwürfel"
    finally:
        reopened.close()


# --- 4: Attributspunkte verteilen ---


@pytest.fixture
def pool(conn):
    """Die Figur aus den Probe-Tests mit STR 14 und 3 Punkten im Vorrat."""
    add_campaign(conn, CAMPAIGN)
    add_player(conn, PLAYER, attributes={"STR": 14, "GES": 18})
    conn.execute("BEGIN")
    apply.apply_effects(conn, CAMPAIGN, PLAYER, [GrantAttributePoints(points=3)])
    conn.execute("COMMIT")


def raise_attribute(conn, request_id: str, revision: int, attribute: str, points: int = 1):
    return submit(conn, request_id, revision, action=RaiseAttribute(attribute=attribute, points=points))


def attributes(conn) -> dict[str, int]:
    return queries.status(conn, campaign_id=CAMPAIGN, player_id=PLAYER).attributes


def test_4_points_come_from_the_pool(conn, pool):
    result = raise_attribute(conn, "r1", 0, "STR", 2)

    assert result.status == "accepted" and result.revision == 1
    assert (attributes(conn)["STR"], attribute_points(conn)) == (16, 1)
    assert campaign_row(conn)["game_minutes"] == 0  # kostet keine Zeit


def test_4_not_more_than_the_pool(conn, pool):
    before = dump_state(conn)
    assert raise_attribute(conn, "r1", 0, "STR", 4).reject_code == "no_attribute_points"
    assert_only_log_changed(before, dump_state(conn))


def test_4_never_above_twenty(conn, pool):
    raise_attribute(conn, "r1", 0, "GES", 2)
    assert attributes(conn)["GES"] == 20

    before = dump_state(conn)
    assert raise_attribute(conn, "r2", 1, "GES").reject_code == "attribute_max"
    assert_only_log_changed(before, dump_state(conn))


def test_4_without_points_nothing_to_spend(conn, roll_scenario):
    assert raise_attribute(conn, "r1", 0, "STR").reject_code == "no_attribute_points"


def test_4_replay_has_no_new_effect(conn, pool):
    first = raise_attribute(conn, "r1", 0, "STR")
    again = raise_attribute(conn, "r1", 0, "STR")
    assert again.replayed and again.effects == first.effects
    assert (attributes(conn)["STR"], attribute_points(conn)) == (15, 2)


def test_4_not_while_a_roll_is_open(conn, pool):
    attempt(conn, "a1", 0)
    before = dump_state(conn)
    assert raise_attribute(conn, "r1", 1, "STR").reject_code == "roll_pending"
    assert_only_log_changed(before, dump_state(conn))


def test_4_the_raised_attribute_counts_at_the_next_roll(conn, pool):
    raise_attribute(conn, "r1", 0, "STR", 2)  # STR 16: +3 statt +2
    attempt(conn, "a1", 1, skill_id="SKILL-403")  # Athletik (STR / KON), Skill 0
    assert status(conn, PLAYER).open_roll.bonus == 3


def test_4_the_action_names_only_attribute_and_points():
    base = {"type": "raise_attribute", "attribute": "STR"}
    assert ACTIONS.validate_python(base) == RaiseAttribute(attribute="STR", points=1)
    for wrong in ({"attribute": "MAG"}, {"points": 0}, {"value": 20}, {"player_id": "x"}):
        with pytest.raises(ValidationError):
            ACTIONS.validate_python(base | wrong)


def test_4_the_model_is_offered_raising_only_with_points(conn, pool):
    def offered() -> tuple[str, ...]:
        return context.build_context(conn, campaign_id=CAMPAIGN, player_id=PLAYER).actions

    assert "raise_attribute" in offered()
    raise_attribute(conn, "r1", 0, "STR", 3)
    assert "raise_attribute" not in offered()


# --- Kommandozeile ---

SCRIPT = """\
Alrik Weidner
STR 14 GES 12 KON 10 WEI 12 CHA 9
Stärke 14, Geschick 12, Konstitution 10, Weisheit 12, Charisma 10
Klingenwaffen, Ziehen
Klingenwaffen, Wahrnehmung
Schleichen, Überleben, Athletik
Schleichen, Überleben, Athletik, Naturkunde
Söldner
Ein abgegriffener Holzwürfel
Sohn eines Köhlers aus dem Moor.
status
"""


def run_main(db_path, script: str, *args: str) -> tuple[int, str]:
    out = io.StringIO()
    code = cli.main(["--db", str(db_path), *args], stdin=io.StringIO(script), stdout=out)
    return code, out.getvalue()


def test_cli_creation_asks_again_after_each_mistake(db_path):
    code, output = run_main(db_path, SCRIPT)

    assert code == 0
    assert output.startswith("Neue Kampagne angelegt.\nNeue Figur erschaffen.")
    assert "Genau 18 Punkte verteilen, verteilt sind 17." in output
    assert "Ziehen ist noch gesperrt" in output
    assert "Genau 4 Nebenskills wählen, gewählt sind 3." in output
    assert "Alrik Weidner ist erschaffen." in output
    assert "Alrik Weidner · Level 1 · Zunfthaus" in output
    assert "Geld: 1 Gulden, 5 Groschen" in output
    assert "Persönlicher Gegenstand: Ein abgegriffener Holzwürfel" in output
    assert "Klingenwaffen 30 (0 Ticks)" in output and "Naturkunde 15 (0 Ticks)" in output

    code, output = run_main(db_path, "status\n")  # nächster Start: dieselbe Figur, keine neue Erschaffung
    assert code == 0 and "Neue Figur" not in output and "Alrik Weidner · Level 1" in output


def test_cli_end_of_input_during_creation_creates_nothing(db_path):
    code, output = run_main(db_path, "Alrik\nSTR 15\n")

    assert code == 1 and output.rstrip().endswith("Erschaffung abgebrochen.")
    conn = db.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM player").fetchone()[0] == 0
    finally:
        conn.close()


def test_cli_several_figures_need_a_choice(db_path):
    run_main(db_path, SCRIPT)
    second = SCRIPT.replace("Alrik Weidner", "Berta Kranich")
    run_main(db_path, second, "--neu")

    code, output = run_main(db_path, "status\n")
    assert code == 2 and "--figur" in output

    code, output = run_main(db_path, "status\n", "--figur", "berta kranich")
    assert code == 0 and "Berta Kranich · Level 1" in output


def test_cli_raise_attribute(db_path):
    run_main(db_path, SCRIPT)
    conn = db.connect(db_path)
    try:
        conn.execute("UPDATE player SET attribute_points = 2")
    finally:
        conn.close()

    code, output = run_main(db_path, "steigere Stärke\nsteigere GES 5\nsteigere Mut\nstatus\n")

    assert code == 0
    assert "Stärke steigt um 1 auf 15." in output
    assert "Abgelehnt: Im Vorrat sind nur 1 Attributspunkte. (no_attribute_points)" in output
    assert "Attribut 'Mut' kenne ich nicht." in output
    assert "Attributspunkte im Vorrat: 1" in output


def test_cli_asks_again_on_control_characters(db_path):
    script = SCRIPT.replace("Ein abgegriffener Holzwürfel", "Würfel\x1b[2J\nEin abgegriffener Holzwürfel")
    code, output = run_main(db_path, script)
    assert code == 0 and "Steuerzeichen sind nicht erlaubt." in output
    assert "Persönlicher Gegenstand: Ein abgegriffener Holzwürfel" in output


@pytest.mark.parametrize(
    "text, expected",
    [
        ("STR 12 GES 14", {"STR": 12, "GES": 14}),
        ("stärke=12, Weisheit: 9", {"STR": 12, "WEI": 9}),
    ],
)
def test_cli_parse_attributes(text, expected):
    assert cli.parse_attributes(text) == expected


@pytest.mark.parametrize("text", ["", "STR", "STR 12 13", "STR 12 STR 13", "Mut 12", "STR -2"])
def test_cli_parse_attributes_rejects_unclear_input(text):
    with pytest.raises(cli.InputError):
        cli.parse_attributes(text)


def test_a_random_character_always_follows_the_rules():
    for seed in range(300):
        sheet = character.random_sheet(CONTENT_1I, random.Random(seed))
        character.check_sheet(sheet, CONTENT_1I)
