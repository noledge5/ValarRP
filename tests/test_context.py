"""Schritt 1j: Kontextbauer und Nachschlagen (Entscheidungen 137 bis 144). Die Nummern folgen dem README.

Testdaten: Sturmhagen zeigt auf das Testdorf des Test-Wikis (Region Testland, Landschaft Moor). Dort kommen
Sumpfkraut, Wolf und Dorfkatze vor; Sumpfkraut hat Wissensstufen, das Testdorf eine versteckte (Wahrnehmung).
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
    content_pointing_to,
    dump_state,
    last_turn,
    page,
    submit,
    write_wiki,
)
from valar import context, queries, search, wiki
from valar.content import GUILD_HALL
from valar.models import AttemptAction, Examine, MeetNpc, RecordFact, SubmitRoll

CONTENT = content_pointing_to(boomtown="testdorf", crystal_field="anderswo")
SECRET = "Unter der Brücke liegt ein Versteck."


@pytest.fixture
def test_wiki(tmp_path: Path) -> wiki.Wiki:
    pages = TEST_WIKI | {
        "orte/testdorf.md": page(
            "Ort",
            "region: testland\nlandscapes: [moor]\naliases: [Schlammnest]\nknowledge:\n"
            f"  - {{skill: SKILL-405, difficulty: DIFF-03, text: {SECRET}}}",
            extra="description: Ein Dorf im Moor.\n",
            body="Das Testdorf liegt im [Moor](/landschaften/moor.md). Man kennt hier den [Wolf](/tiere/wolf.md).",
        ),
        "wissen/lange-chronik.md": page(
            "Wissen",
            extra="title: Lange Chronik\ndescription: Die Geschichte des Testlands in vielen Kapiteln.\n",
            body="\n\n".join(
                f"## Kapitel {n}\n" + f"Im Jahr der {event} geschah im Testland etwas Wichtiges. " * 30
                for n, event in enumerate(["Flut", "Dürre", "Seuche", "Fehde", "Ernte"], start=1)
            ),
        ),
    }
    write_wiki(tmp_path / "w", pages)
    return wiki.load(tmp_path / "w")


@pytest.fixture
def scene(conn):
    add_campaign(conn, CAMPAIGN)
    add_player(conn, PLAYER, location_id=GUILD_HALL)


def build(conn, test_wiki, **kwargs) -> context.TurnContext:
    return context.build_context(
        conn, campaign_id=CAMPAIGN, player_id=PLAYER, content=CONTENT, wiki=test_wiki, **kwargs
    )


def play(conn, test_wiki, request_id: str, revision: int, action, **kwargs):
    return submit(conn, request_id, revision, action=action, content=CONTENT, wiki=test_wiki, **kwargs)


def keys(built: context.TurnContext) -> list[tuple[str, str, str]]:
    return [(item.section, item.key, item.level) for item in built.items]


# --- 1: Gleicher Stand, gleicher Kontext; Rangfolge ---


def test_1_same_state_same_context_in_fixed_order(conn, scene, test_wiki):
    first = build(conn, test_wiki, player_input="Ich sehe mich nach dem Sumpfkraut um.")
    again = build(conn, test_wiki, player_input="Ich sehe mich nach dem Sumpfkraut um.")

    assert first == again
    assert keys(first) == [
        ("ort", "testdorf", "full"),
        ("genannt", "sumpfkraut", "full"),
        ("vorkommen", "wolf", "summary"),
        ("vorkommen", "dorfkatze", "summary"),
        ("verweis", "moor", "summary"),
    ]
    assert first.render().startswith("# Zustand\nOrt: Zunfthaus · Tag 1, 08:00")


def test_1_building_the_context_changes_nothing(conn, scene, test_wiki):
    before = dump_state(conn)
    build(conn, test_wiki, player_input="Sumpfkraut")
    assert dump_state(conn) == before


# --- 2: Budget ---


def test_2_the_budget_is_never_exceeded_and_dropped_pages_are_listed(conn, scene, test_wiki):
    full = build(conn, test_wiki, player_input="Sumpfkraut und die Lange Chronik")
    small = build(conn, test_wiki, player_input="Sumpfkraut und die Lange Chronik", budget=30)

    assert full.tokens <= full.budget and small.tokens <= 30
    assert small.dropped and all(reason == "budget" for _, reason in small.dropped)
    shown = {item.key for item in small.items} | {key for key, _ in small.dropped}
    assert shown == {item.key for item in full.items} | {key for key, _ in full.dropped}


def test_2_a_page_that_does_not_fit_whole_comes_as_one_sentence(conn, scene, test_wiki):
    roomy = build(conn, test_wiki, player_input="Erzähl die Lange Chronik.", budget=5000)
    chronicle = next(item for item in roomy.items if item.key == "lange-chronik")
    assert chronicle.level == "full"

    tight = build(conn, test_wiki, player_input="Erzähl die Lange Chronik.")  # über 2000 Tokens lang
    chronicle = next(item for item in tight.items if item.key == "lange-chronik")
    assert (chronicle.level, chronicle.text) == (
        "summary", "Lange Chronik: Die Geschichte des Testlands in vielen Kapiteln."
    )


def test_2_the_manifest_is_stored_with_the_turn(conn, scene, test_wiki):
    built = build(conn, test_wiki, player_input="Sumpfkraut", budget=30)

    play(conn, test_wiki, "a1", 0, AttemptAction(skill_id="SKILL-408", difficulty="DIFF-02", duration="moment", reason="x"),
         context=built.manifest())

    stored = json.loads(last_turn(conn)["context_json"])
    assert stored == json.loads(json.dumps(built.manifest(), sort_keys=True))
    assert stored["dropped"] and stored["tokens"] <= 30


# --- 3: Zustand, NPCs, Gepäck und Genanntes lösen aus ---


def test_3_npcs_facts_and_items_come_in(conn, scene, test_wiki):
    play(conn, test_wiki, "n1", 0, MeetNpc(template_id="koehler", name="Hanne Ruß", description="Rußige Hände"))
    play(conn, test_wiki, "f1", 1, RecordFact(kind="rumor", text="Im Moor spukt es.", source_npc_id="hanne-russ"))
    conn.execute("INSERT INTO inventory VALUES (?, ?, 'sumpfkraut', 2)", (CAMPAIGN, PLAYER))

    built = build(conn, test_wiki)

    texts = {item.key: item.text for item in built.items}
    assert texts["npc:hanne-russ"] == "Hanne Ruß (Köhler): Rußige Hände."
    assert texts["fakt:1"] == "Gerücht: Im Moor spukt es. (von Hanne Ruß)"
    assert ("gepaeck", "sumpfkraut", "summary") in keys(built)


def test_3_an_alias_in_the_input_counts_like_the_title(conn, scene, test_wiki):
    built = build(conn, test_wiki, player_input="Was ist im Schlammnest los?")
    assert ("ort", "testdorf", "full") in keys(built)  # schon Ort; nicht doppelt
    assert [item.key for item in built.items].count("testdorf") == 1

    elsewhere = build(conn, test_wiki, player_input="Erzähl vom Graf Kuno von Testhausen")
    assert ("genannt", "graf-test", "full") in keys(elsewhere)


def test_3_words_only_count_whole(conn, scene, test_wiki):
    built = build(conn, test_wiki, player_input="Die Wolfsmilch blüht.")  # „Wolf“ nur als Wortteil
    assert ("genannt", "wolf", "full") not in keys(built)


# --- 4: Detailstufen ---


def test_4_one_sentence_is_the_default_full_text_only_for_place_and_topic(conn, scene, test_wiki):
    built = build(conn, test_wiki)
    levels = {item.key: item.level for item in built.items}
    assert levels["testdorf"] == "full"
    assert {level for key, level in levels.items() if key != "testdorf"} == {"summary"}


# --- 5: Nachschlagen ---


def test_5_lookup_finds_sections_by_title_alias_and_text(conn, scene, test_wiki):
    def found(text):
        return [(h.page_id, h.heading) for h in queries.lookup(conn, campaign_id=CAMPAIGN, query=text, wiki=test_wiki)]

    assert found("Schlammnest")[0] == ("testdorf", None)
    assert found("Was geschah bei der Seuche?")[0] == ("lange-chronik", "Kapitel 3")
    assert found("xyzzy") == []


def test_5_a_long_page_never_comes_whole(test_wiki):
    retriever = search.FullTextRetriever(test_wiki)
    hits = retriever.search("Testland Kapitel Geschichte", limit=10)
    assert hits and all(len(hit.text) <= search.CHUNK_CHARS for hit in hits)


@pytest.mark.parametrize("query", ['" OR 1', "NEAR(a b)", "title:x", "*", '"""', "AND OR NOT"])
def test_5_search_syntax_in_the_input_is_never_a_command(test_wiki, query):
    search.FullTextRetriever(test_wiki).search(query)  # kein Fehler, kein Befehl


# --- 6: Was die Figur nicht weiß, steht nie im Kontext ---


def test_6_unknown_stages_are_never_in_context_or_search(conn, scene, test_wiki):
    rendered = build(conn, test_wiki, player_input="Testdorf Sumpfkraut").render()
    hits = queries.lookup(conn, campaign_id=CAMPAIGN, query="Brücke Versteck", wiki=test_wiki)

    assert SECRET not in rendered
    assert "Naturkunde extrem." not in rendered
    assert all(SECRET not in hit.text for hit in hits)
    assert "Naturkunde leicht." in rendered  # ohne Wurf sichtbar (10 + Bonus 0)


def test_6_learned_stages_come_in(conn, scene, test_wiki):
    play(conn, test_wiki, "e1", 0, Examine(page_id="testdorf"))
    play(conn, test_wiki, "e2", 1, SubmitRoll(value=20))
    assert SECRET in build(conn, test_wiki).render()


# --- 7: Nur erlaubte Aktionen ---


def test_7_only_allowed_actions_are_offered(conn, scene, test_wiki):
    assert build(conn, test_wiki).actions == (
        "narrate", "move", "examine", "gather", "meet_npc", "attempt", "record_fact", "mark_scarcity",
        "create_page", "sleep",
    )
    play(conn, test_wiki, "n1", 0, MeetNpc(template_id="koehler", name="Hanne", description="Köhlerin"))
    assert {"talk", "contest"} <= set(build(conn, test_wiki).actions)

    play(conn, test_wiki, "e1", 1, Examine(page_id="testdorf"))
    assert build(conn, test_wiki).actions == ("narrate",)  # Probe offen: der Spieler würfelt


# --- Suchqualität im echten Welt-Wiki (Maßstab für eine spätere Vektorsuche, Entscheidung 144) ---

QUESTIONS = json.loads((Path(__file__).parent / "data" / "suchfragen.json").read_text(encoding="utf-8"))


def test_search_quality_in_the_world_wiki():
    retriever = search.FullTextRetriever(wiki.default_wiki())
    misses = []
    for question in QUESTIONS:
        wanted = question["erwartet"] if isinstance(question["erwartet"], list) else [question["erwartet"]]
        got = [hit.page_id for hit in retriever.search(question["frage"])]
        if not set(wanted) & set(got):
            misses.append((question["frage"], got))
    recall = 1 - len(misses) / len(QUESTIONS)
    assert recall >= 0.9, f"Trefferquote {recall:.0%}, verfehlt: {misses}"
