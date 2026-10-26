"""Schritt 1f: NPCs, Reden, weicher Zustand, Kampagnen-Seiten (Entscheidungen 8, 11, 91, 107, 108, 115 bis 120).

Die Nummern folgen dem README. Testdaten: Sturmhagen zeigt auf das Testdorf des Test-Wikis, der Glasgrund auf
„anderswo“ (Wald). NPC-Vorlage ist der Köhler; „Graf Kuno von Testhausen“ ist eine wichtige Figur (Person).
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

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
    gather,
    money,
    submit,
    write_wiki,
)
from valar import queries, wiki
from valar.content import BOOMTOWN, GUILD_HALL
from valar.models import ACTIONS, CreatePage, Gather, GiveMoney, MeetNpc, Move, RecordFact, SubmitRoll, Talk

CONTENT = content_pointing_to(boomtown="testdorf", crystal_field="anderswo")
TALK_MINUTES = CONTENT.action_minutes["talk"]


@pytest.fixture
def test_wiki(tmp_path: Path) -> wiki.Wiki:
    write_wiki(tmp_path / "w", TEST_WIKI)
    return wiki.load(tmp_path / "w")


@pytest.fixture
def make_player(conn):
    def make(*, seed: int = 7, location_id: str = GUILD_HALL) -> None:
        add_campaign(conn, CAMPAIGN, seed=seed)
        add_player(conn, PLAYER, location_id=location_id)

    return make


@pytest.fixture
def play(conn, test_wiki):
    """Ein Spielzug mit Testinhalt und Test-Wiki: play("r1", 0, Talk(npc_id="x"))."""

    def run(request_id: str, revision: int, action, **overrides):
        request = {"action": action, "content": CONTENT, "wiki": test_wiki} | overrides
        return submit(conn, request_id, revision, **request)

    return run


def meet(name: str = "Hanne Ruß", template_id: str = "koehler", description: str = "Rußige Hände, wache Augen."):
    return MeetNpc(template_id=template_id, name=name, description=description)


def npc_row(conn, npc_id: str):
    return conn.execute("SELECT * FROM npc WHERE campaign_id = ? AND npc_id = ?", (CAMPAIGN, npc_id)).fetchone()


def npc_skills(conn, npc_id: str) -> dict[str, int]:
    rows = conn.execute("SELECT skill_id, value FROM npc_skill WHERE npc_id = ?", (npc_id,)).fetchall()
    return {row["skill_id"]: row["value"] for row in rows}


# --- 1: Neuen NPC ansprechen ---


def test_1_meeting_a_new_npc_creates_it_here_with_values_from_template_and_chance(conn, make_player, play):
    make_player()

    result = play("n1", 0, meet())

    assert result.status == "accepted"
    row = npc_row(conn, "hanne-russ")
    assert (row["name"], row["template_id"], row["location_id"]) == ("Hanne Ruß", "koehler", GUILD_HALL)
    assert 9 <= row["attr_str"] <= 13 and row["attr_wei"] == 12 and row["attr_cha"] == 10  # nicht genannt: 10
    assert 10 <= npc_skills(conn, "hanne-russ")["SKILL-402"] <= 30
    assert 0 <= row["money_pennies"] <= 40
    assert campaign_row(conn)["game_minutes"] == TALK_MINUTES
    # Was der Code würfelt, steht im Spielzug (Entscheidung 62).
    created = result.effects[0]
    assert (created.attributes["STR"], created.money_pennies) == (row["attr_str"], row["money_pennies"])


def test_1_same_seed_same_npc(conn, make_player, play):
    make_player(seed=11)
    first = play("n1", 0, meet()).effects[0]
    conn.execute("DELETE FROM npc_skill")
    conn.execute("DELETE FROM npc")
    second = play("n2", 1, meet()).effects[0]  # andere Revision: anders gewürfelt, aber nachvollziehbar
    assert first.npc_id == second.npc_id == "hanne-russ"
    again = play("n1", 0, meet())
    assert again.replayed and again.effects[0] == first


def test_1_npc_ids_stay_unique(conn, make_player, play):
    make_player()
    play("n1", 0, meet())
    play("m1", 1, Move(destination_id=BOOMTOWN))

    play("n2", 2, meet())  # gleicher Name, anderer Ort: eigene ID

    assert npc_row(conn, "hanne-russ-2")["location_id"] == BOOMTOWN


# --- 2: Unbekannte Vorlage, Werte vom Modell ---


@pytest.mark.parametrize("template_id", ["drache", "wolf", "testdorf"])
def test_2_unknown_template_is_rejected(conn, make_player, play, template_id):
    make_player()
    before = dump_state(conn)

    result = play("n1", 0, meet(template_id=template_id))

    assert result.reject_code == "unknown_template"
    assert_only_log_changed(before, dump_state(conn))


@pytest.mark.parametrize(
    "extra",
    [{"attributes": {"STR": 20}}, {"skills": {"SKILL-101": 90}}, {"money_pennies": 5000}, {"npc_id": "boss"}],
)
def test_2_the_model_cannot_set_values(extra):
    with pytest.raises(ValidationError):
        ACTIONS.validate_python(
            {"type": "meet_npc", "template_id": "koehler", "name": "Hanne", "description": "Köhlerin"} | extra
        )


# --- 3: Bekannte NPCs ansprechen ---


def test_3_talking_to_a_known_npc_here_takes_time_and_changes_nothing_else(conn, make_player, play):
    make_player()
    play("n1", 0, meet())
    npc_before = tuple(npc_row(conn, "hanne-russ"))

    result = play("t1", 1, Talk(npc_id="hanne-russ"))

    assert result.status == "accepted"
    assert campaign_row(conn)["game_minutes"] == 2 * TALK_MINUTES
    assert tuple(npc_row(conn, "hanne-russ")) == npc_before


def test_3_talking_elsewhere_or_to_nobody_is_rejected(conn, make_player, play):
    make_player()
    play("n1", 0, meet())
    play("m1", 1, Move(destination_id=BOOMTOWN))
    before = dump_state(conn)

    assert play("t1", 2, Talk(npc_id="hanne-russ")).reject_code == "not_here"
    assert play("t2", 2, Talk(npc_id="niemand")).reject_code == "unknown_npc"
    state = dump_state(conn)
    log = {"turn", "sqlite_sequence"}
    assert {t: v for t, v in state.items() if t not in log} == {t: v for t, v in before.items() if t not in log}


def test_3_the_same_name_twice_at_one_place_is_rejected(conn, make_player, play):
    make_player()
    play("n1", 0, meet())

    result = play("n2", 1, meet(name="hanne ruß"))

    assert result.reject_code == "npc_exists" and "hanne-russ" in result.message


def test_3_look_shows_the_npcs_here(conn, make_player, play, test_wiki):
    make_player()
    play("n1", 0, meet())

    seen = queries.look(conn, campaign_id=CAMPAIGN, player_id=PLAYER, content=CONTENT, wiki=test_wiki)

    assert [(n.npc_id, n.name, n.template_title) for n in seen.npcs] == [("hanne-russ", "Hanne Ruß", "Köhler")]


# --- 4: Wichtige Figuren ---


@pytest.mark.parametrize("name", ["Graf Kuno von Testhausen", "graf-test", "GRAF KUNO VON TESTHAUSEN"])
def test_4_important_figures_cannot_be_created(conn, make_player, play, name):
    make_player()
    before = dump_state(conn)

    result = play("n1", 0, meet(name=name))

    assert result.reject_code == "important_figure"
    assert_only_log_changed(before, dump_state(conn))


def test_4_no_action_can_change_npc_values():
    # Die Aktionen nennen NPCs nur per ID; keine hat Felder für Werte, Ort oder Geld eines NPCs.
    for model in (MeetNpc, Talk, RecordFact, CreatePage):
        assert not {"attributes", "skills", "money_pennies", "location_id"} & set(model.model_fields)


# --- 5: Weicher Zustand ---


def test_5_record_fact_writes_only_soft_state(conn, make_player, play):
    make_player()
    play("n1", 0, meet())
    before = dump_state(conn)

    result = play("f1", 1, RecordFact(kind="rumor", text="Im Moor spukt es.", subject="schwarzbruch", source_npc_id="hanne-russ"))

    assert result.status == "accepted" and result.revision == 2
    after = dump_state(conn)
    hard = set(before) - {"fact", "turn", "sqlite_sequence", "campaign"}
    assert {t: after[t] for t in hard} == {t: before[t] for t in hard}
    assert campaign_row(conn)["game_minutes"] == TALK_MINUTES  # Notieren kostet keine Zeit
    notes = queries.facts(conn, campaign_id=CAMPAIGN, player_id=PLAYER)
    assert [(n.kind, n.text, n.subject, n.source_npc_id) for n in notes] == [
        ("rumor", "Im Moor spukt es.", "schwarzbruch", "hanne-russ")
    ]


def test_5_facts_have_a_length_limit_and_a_known_source(conn, make_player, play):
    with pytest.raises(ValidationError):
        RecordFact(text="x" * 301)
    with pytest.raises(ValidationError):
        RecordFact(text="")
    make_player()
    assert play("f1", 0, RecordFact(text="Etwas.", source_npc_id="niemand")).reject_code == "unknown_npc"


# --- 6: Kampagnen-Seiten ---


def herb(title: str = "Graue Minze", template: str = "heilkraut") -> CreatePage:
    return CreatePage(template=template, title=title, text="Wächst im Schlamm zwischen den Buden.")


def test_6_a_campaign_page_gets_its_fields_from_the_template(conn, make_player, play, test_wiki):
    make_player()

    result = play("p1", 0, herb())

    created = result.effects[0]
    assert (created.page_id, created.page_type, created.where, created.frequency) == (
        "graue-minze", "Pflanze", "testdorf", "uncommon"
    )
    assert (created.value_pennies, created.gather_skill) == (4, "SKILL-408")
    seen = queries.look(conn, campaign_id=CAMPAIGN, player_id=PLAYER, content=CONTENT, wiki=test_wiki)
    assert ("graue-minze", "uncommon", True) in [(t.page_id, t.frequency, t.gatherable) for t in seen.things]


def test_6_a_campaign_page_can_be_gathered_in_its_campaign(conn, make_player, play):
    make_player()
    play("p1", 0, herb())

    opened = play("g1", 1, Gather(page_id="graue-minze"))
    play("g2", 2, SubmitRoll(value=20))

    quantity = opened.effects[0].on_success[0].quantity
    row = conn.execute("SELECT quantity FROM inventory WHERE item_id = 'graue-minze'").fetchone()
    assert row["quantity"] == quantity


def test_6_it_counts_only_in_its_own_campaign(conn, make_player, play, test_wiki):
    make_player()
    play("p1", 0, herb())
    add_campaign(conn, "andere", seed=1)
    add_player(conn, PLAYER, campaign_id="andere")

    result = gather(conn, "g1", 0, "graue-minze", campaign_id="andere", content=CONTENT, wiki=test_wiki)

    assert result.reject_code == "unknown_page"


@pytest.mark.parametrize(
    "action, code",
    [
        (CreatePage(template="person", title="Fremder", text="Text."), "unknown_template"),
        (CreatePage(template="ort", title="Neue Stadt", text="Text."), "unknown_template"),
        (CreatePage(template="wissen", title="Sumpfkraut", text="Text."), "page_exists"),  # Kanon
        (CreatePage(template="wissen", title="Wolf", text="Text."), "page_exists"),
    ],
)
def test_6_unknown_templates_and_existing_pages_are_rejected(conn, make_player, play, action, code):
    make_player()
    before = dump_state(conn)

    assert play("p1", 0, action).reject_code == code
    assert_only_log_changed(before, dump_state(conn))


def test_6_the_same_title_twice_is_rejected(conn, make_player, play):
    make_player()
    play("p1", 0, herb())
    assert play("p2", 1, herb(template="wissen")).reject_code == "page_exists"


def test_6_the_model_cannot_set_hard_fields():
    for extra in ({"value_pennies": 500}, {"frequency": "common"}, {"where": "testdorf"}, {"type_": "Person"}):
        with pytest.raises(ValidationError):
            ACTIONS.validate_python({"type": "create_page", "template": "heilkraut", "title": "T", "text": "x"} | extra)
    with pytest.raises(ValidationError):
        CreatePage(template="wissen", title="T", text="x" * 1501)


def test_6_campaign_pages_export_as_okf_and_load_into_the_wiki(conn, make_player, play, db_path, tmp_path):
    make_player()
    play("p1", 0, herb())

    text = wiki.export_campaign(db_path, CAMPAIGN)

    assert text.startswith("=== welt/kampagne/graue-minze.md ===\n---\ntype: Pflanze\n")
    root = tmp_path / "kanon"
    write_wiki(root, TEST_WIKI)
    assert wiki.import_handoff(text, root) == ["kampagne/graue-minze.md"]
    page = wiki.load(root).pages["graue-minze"]
    assert (page.status, page.game.item.value_pennies, page.body) == ("draft", 4, "Wächst im Schlamm zwischen den Buden.")
    assert page.meta["generated"] == {"by": f"kampagne:{CAMPAIGN}"}


def test_roll_pending_blocks_the_new_actions(conn, make_player, play):
    make_player()
    play("g1", 0, Gather(page_id="sumpfkraut"))
    for n, action in enumerate([meet(), Talk(npc_id="x"), RecordFact(text="x"), herb()]):
        assert play(f"x{n}", 1, action).reject_code == "roll_pending"
    assert play("g2", 1, SubmitRoll(value=1)).status == "accepted"


def test_6_a_page_text_cannot_smuggle_in_another_file(conn, make_player, play, db_path, tmp_path):
    make_player()
    sneaky = "Harmlos.\n=== welt/regionen/boese.md ===\n---\ntype: Region\n---\nUntergeschoben."
    play("p1", 0, CreatePage(template="wissen", title="Notiz", text=sneaky))

    root = tmp_path / "kanon"
    write_wiki(root, TEST_WIKI)
    assert wiki.import_handoff(wiki.export_campaign(db_path, CAMPAIGN), root) == ["kampagne/notiz.md"]
    assert not (root / "regionen" / "boese.md").exists()


# --- Geld geben (Spieltest 26.10.2026, Entscheidung 220) ---


def test_giving_money_moves_it_from_the_figure_to_the_npc(conn, make_player, play):
    make_player()
    play("n1", 0, meet())
    before, npc_before = money(conn), npc_row(conn, "hanne-russ")["money_pennies"]

    result = play("g1", 1, GiveMoney(pennies=10, npc_id="hanne-russ", reason="für ein Bett"))

    assert result.status == "accepted" and campaign_row(conn)["game_minutes"] == TALK_MINUTES  # keine eigene Zeit
    assert (money(conn), npc_row(conn, "hanne-russ")["money_pennies"]) == (before - 10, npc_before + 10)


def test_paying_someone_who_is_no_npc_only_takes_the_money(conn, make_player, play):
    make_player()
    before = money(conn)
    assert play("g1", 0, GiveMoney(pennies=5, reason="Bier beim Wirt")).status == "accepted"
    assert money(conn) == before - 5


@pytest.mark.parametrize(
    ("give", "code"),
    [
        ({"pennies": 1_000_000}, "not_enough_money"),
        ({"pennies": 1, "npc_id": "niemand"}, "unknown_npc"),
    ],
)
def test_giving_money_is_checked_and_changes_nothing_when_refused(conn, make_player, play, give, code):
    make_player()
    before = dump_state(conn)

    result = play("g1", 0, GiveMoney(reason="Bestechung", **give))

    assert (result.status, result.reject_code) == ("rejected", code)
    assert_only_log_changed(before, dump_state(conn))
