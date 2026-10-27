"""Einsatz einer Probe nach den Weltgesetzen Quelle und Maßstab (Entscheidungen 196 und 231).

Testdaten: Die Spielfigur steht im Zunfthaus mit Startgeld. Der Händler Udo steht daneben mit 50 Pfennig im Beutel,
der Bauer Bert in Sturmhagen. Im Test-Wiki gibt es die Ware Hornknopf (Wert 4 Pfennig).
"""

import pytest

from tests.helpers import CAMPAIGN, PLAYER, add_campaign, add_player, money, open_roll_row, roll, submit, write_wiki
from valar import apply, queries, wiki
from valar.content import BOOMTOWN, GUILD_HALL
from valar.models import AttemptAction, Contest, CreateNpc, Gain

PURSE = 50
SNEAK = "SKILL-201"


@pytest.fixture
def test_wiki(tmp_path):
    write_wiki(tmp_path, {"gegenstaende/hornknopf.md": (
        "---\ntype: Gegenstand\ntitle: Hornknopf\ndescription: Ein Knopf aus Horn.\nstatus: draft\n"
        "valar:\n  item: {value_pennies: 4}\n---\nEin Knopf aus Horn.\n"
    )})
    return wiki.load(tmp_path)


@pytest.fixture
def scene(conn):
    add_campaign(conn, CAMPAIGN, seed=5)
    add_player(conn, PLAYER, money_pennies=40)
    conn.execute("BEGIN")
    apply.apply_effects(conn, CAMPAIGN, PLAYER, [
        CreateNpc(npc_id="udo", template_id="haendler", name="Udo", description="Ein Händler.", location_id=GUILD_HALL,
                  attributes={}, skills={}, money_pennies=PURSE),
        CreateNpc(npc_id="bert", template_id="bauer", name="Bert", description="Ein Bauer.", location_id=BOOMTOWN,
                  attributes={}, skills={}, money_pennies=PURSE),
    ])
    conn.execute("COMMIT")


def try_for(conn, gain: Gain | None, *, loss: bool = False, difficulty: str = "DIFF-03", test_wiki=None):
    action = AttemptAction(skill_id=SNEAK, difficulty=difficulty, duration="moment", reason="Lohn fürs Schleppen",
                           gain=gain, loss=loss)
    return submit(conn, "s1", 0, action=action, wiki=test_wiki)


def purse(conn, npc_id: str = "udo") -> int:
    return conn.execute("SELECT money_pennies FROM npc WHERE npc_id = ?", (npc_id,)).fetchone()[0]


def stakes(conn) -> tuple[str, ...]:
    return queries.status(conn, campaign_id=CAMPAIGN, player_id=PLAYER).open_roll.stakes


# --- Quelle und Maßstab: gekürzt beim Öffnen, sichtbar vor dem Wurf ---


def test_a_gain_is_cut_to_the_cap_of_the_stage_and_shown_before_the_roll(conn, scene):
    try_for(conn, Gain(source="udo", pennies=500), loss=True)  # SG 12: höchstens 3 Tagelöhne

    assert stakes(conn) == (
        "Bei Erfolg: 3 Groschen von Udo", "Bei kritischem Erfolg dazu: 2 Groschen", "Bei Fehlschlag: du verlierst 1 Groschen, 5 Pfennig an Udo"
    )


def test_an_npc_gives_at_most_what_is_in_his_purse(conn, scene):
    try_for(conn, Gain(source="udo", pennies=500), difficulty="DIFF-05")  # Obergrenze 300, Beutel 50

    assert stakes(conn) == ("Bei Erfolg: 5 Groschen von Udo",)  # kritisch: nichts mehr im Beutel


def test_without_a_number_the_code_takes_what_stage_and_source_allow(conn, scene):
    try_for(conn, Gain(source="udo"))  # das Modell nennt meist keinen Betrag: SG 12 gibt 3 Groschen

    assert stakes(conn)[0] == "Bei Erfolg: 3 Groschen von Udo"


def test_nothing_at_stake_and_no_loss_without_the_model_naming_one(conn, scene):
    try_for(conn, Gain(kind="nichts", source="fund"))

    assert stakes(conn) == ()


@pytest.mark.parametrize("source", ["bert", "niemand"])
def test_a_source_that_is_not_here_gives_nothing(conn, scene, source):
    result = try_for(conn, Gain(source=source, pennies=10))

    assert result.status == "awaiting_roll" and stakes(conn) == ()


# --- Beim Wurf: genau, was angesagt war ---


def test_success_moves_the_money_from_the_purse_to_the_figure(conn, scene):
    try_for(conn, Gain(source="udo", pennies=20))
    roll(conn, "s2", 1, value=15)

    assert (money(conn), purse(conn)) == (60, 30)


def test_a_critical_success_brings_money_twice_never_beyond_the_source(conn, scene):
    try_for(conn, Gain(source="udo", pennies=30))
    roll(conn, "s2", 1, value=20)

    assert (money(conn), purse(conn)) == (90, 0)  # 30 und noch einmal 20, mehr hat Udo nicht


@pytest.mark.parametrize("die", [1, 3])
def test_a_failure_costs_at_most_half_and_goes_to_the_source(conn, scene, die):
    try_for(conn, Gain(source="udo", pennies=20), loss=True)
    roll(conn, "s2", 1, value=die)

    assert (money(conn), purse(conn)) == (30, 60)


def test_a_herrenless_find_needs_no_purse(conn, scene):
    try_for(conn, Gain(source="fund", pennies=10), difficulty="DIFF-01")  # SG 8: höchstens ½ Tagelohn

    assert stakes(conn) == ("Bei Erfolg: 5 Pfennig als Fund", "Bei kritischem Erfolg dazu: 5 Pfennig")


def test_a_contest_loss_goes_to_the_opponent(conn, scene):
    action = Contest(skill_id=SNEAK, npc_id="udo", duration="moment", reason="Beutel stehlen", loss=True,
                     gain=Gain(source="udo", pennies=40))
    submit(conn, "s1", 0, action=action)

    assert stakes(conn)[-1].endswith("an Udo")
    assert open_roll_row(conn) is not None


# --- Waren ---


def test_a_new_ware_becomes_a_page_whose_value_the_code_caps(conn, scene, test_wiki):
    try_for(conn, Gain(kind="ware", source="udo", pennies=900, item="Silberring"), test_wiki=test_wiki)
    assert stakes(conn) == ("Bei Erfolg: Silberring (Wert 3 Groschen) von Udo",)

    roll(conn, "s2", 1, value=20, wiki=test_wiki)  # eine Ware kommt auch kritisch nur einmal

    page = conn.execute("SELECT title, type, value_pennies FROM campaign_page WHERE page_id = 'silberring'").fetchone()
    assert tuple(page) == ("Silberring", "Gegenstand", 30)
    assert conn.execute("SELECT quantity FROM inventory WHERE item_id = 'silberring'").fetchone()[0] == 1
    assert purse(conn) == 20


def test_a_known_ware_keeps_its_value(conn, scene, test_wiki):
    try_for(conn, Gain(kind="ware", source="udo", pennies=30, item="Hornknopf"), test_wiki=test_wiki)
    roll(conn, "s2", 1, value=15, wiki=test_wiki)

    assert conn.execute("SELECT quantity FROM inventory WHERE item_id = 'hornknopf'").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM campaign_page").fetchone()[0] == 0
    assert purse(conn) == PURSE - 4


def test_a_repeated_roll_changes_nothing_more(conn, scene):
    try_for(conn, Gain(source="udo", pennies=20))
    roll(conn, "s2", 1, value=15)
    again = roll(conn, "s2", 1, value=15)

    assert again.replayed and (money(conn), purse(conn)) == (60, 30)
