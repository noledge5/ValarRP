"""Schritt 2b: Spielleiter-Schleife gegen eine Attrappe, ohne Netz (Entscheidungen 158 bis 166).

Die Nummern folgen dem README. Testdaten: Sturmhagen zeigt auf das Testdorf des Test-Wikis; das Testdorf hat eine
versteckte Wissensstufe (Wahrnehmung, SG 12), die die Figur ohne Wurf nicht sieht. Aufruf A wählt die Aktion,
Aufruf B erzählt das Ergebnis.
"""

import io
import json
import random
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.helpers import (
    CAMPAIGN,
    PLAYER,
    TEST_WIKI,
    FakeTransport,
    add_campaign,
    add_player,
    chat,
    content_pointing_to,
    dump_state,
    location,
    money,
    submit,
    turn_count,
    write_wiki,
)
from valar import cli, context, gm, llm, wiki
from valar.content import BOOMTOWN, GUILD_HALL
from valar.models import Move, RecordFact
from valar.turns import RequestIdConflict

CONTENT = content_pointing_to(boomtown="testdorf", crystal_field="anderswo")
SECRET = "Unter der Brücke liegt ein Versteck."
SETTINGS = llm.ModelSettings(provider=(llm.ProviderSettings(name="frei", base_url="https://frei.example/v1", model="m", key_env="FREI_KEY"),))
TO_TOWN = {"type": "move", "destination_id": BOOMTOWN}
TO_HALL = {"type": "move", "destination_id": GUILD_HALL}


@pytest.fixture
def test_wiki(tmp_path: Path) -> wiki.Wiki:
    write_wiki(tmp_path / "w", TEST_WIKI)
    return wiki.load(tmp_path / "w")


@pytest.fixture
def scene(conn):
    add_campaign(conn, CAMPAIGN)
    add_player(conn, PLAYER, location_id=GUILD_HALL)


def master(conn, test_wiki, transport: FakeTransport) -> gm.GameMaster:
    client = llm.ModelClient(
        SETTINGS, llm.Ledger(conn), transport=transport, environ={"FREI_KEY": "k"}, now=lambda: datetime(2026, 10, 22, tzinfo=UTC)
    )
    return gm.GameMaster(conn, client, campaign_id=CAMPAIGN, player_id=PLAYER, content=CONTENT, wiki=test_wiki)


def decide(action: dict):
    return chat(json.dumps({"action": action}))


def tell(text: str = "Der Schlamm schmatzt unter deinen Stiefeln.", notes: list[dict] = ()):
    return chat(json.dumps({"text": text, "notes": list(notes)}))


def game_state(conn) -> dict:
    return {t: rows for t, rows in dump_state(conn).items() if t not in ("model_call", "sqlite_sequence")}


def exchange(conn, request_id: str):
    return conn.execute("SELECT * FROM exchange WHERE request_id = ?", (request_id,)).fetchone()


def user_message(transport: FakeTransport, call: int) -> str:
    return transport.requests[call]["body"]["messages"][1]["content"]


def action_schema(transport: FakeTransport, call: int) -> dict:
    return transport.requests[call]["body"]["response_format"]["json_schema"]["schema"]


# --- 1: Ein Zug mit der Attrappe: Aktion, Auflösung, Erzählung ---


def test_1_one_turn_action_resolution_narration(conn, scene, test_wiki):
    transport = FakeTransport(decide(TO_TOWN), tell("Du trittst hinaus in den Schlamm."))

    result = master(conn, test_wiki, transport).play("r1", "Ich gehe hinaus nach Sturmhagen.")

    assert result.narration == "Du trittst hinaus in den Schlamm."
    assert (result.turn.request_id, result.turn.status) == ("r1/a", "accepted")
    assert location(conn) == BOOMTOWN
    assert len(transport.requests) == 2
    assert "# Aufgabe A" in user_message(transport, 0) and "# Aufgabe B" in user_message(transport, 1)
    assert "Die Figur ist laut Code jetzt hier: Zunfthaus." in user_message(transport, 0)  # Entscheidung 222
    assert "Gewählte Aktion: gehen nach Sturmhagen\nAngekommen: Sturmhagen\nDauer: 5 Min." in user_message(transport, 1)
    row = exchange(conn, "r1")
    assert (row["player_input"], row["turn_request_id"], row["narration"], row["chronicle"]) == (
        "Ich gehe hinaus nach Sturmhagen.", "r1/a", "Du trittst hinaus in den Schlamm.", "Tag 1, 08:05: gehen nach Sturmhagen"
    )


def test_1_every_call_starts_with_the_frame_prompt(conn, scene, test_wiki):
    transport = FakeTransport(decide(TO_TOWN), tell())
    master(conn, test_wiki, transport).play("r1", "Los.")

    prompt = gm.load_prompt()
    assert context.estimate_tokens(prompt) <= gm.PROMPT_BUDGET
    assert all(r["body"]["messages"][0] == {"role": "system", "content": prompt} for r in transport.requests)
    assert "Gewalt" in prompt and "Sex deutest du nur an" in prompt


def test_1_narrate_needs_one_call_and_its_notes_become_turns(conn, scene, test_wiki):
    note = {"kind": "rumor", "text": "Im Moor soll ein Irrlicht tanzen.", "subject": "Moor"}
    transport = FakeTransport(decide({"type": "narrate", "text": "Der Zunftmeister brummt.", "notes": [note]}))

    result = master(conn, test_wiki, transport).play("r1", "Ich frage den Zunftmeister nach Neuigkeiten.")

    assert (result.narration, result.turn) == ("Der Zunftmeister brummt.", None)
    assert len(transport.requests) == 1
    assert [n.request_id for n in result.notes] == ["r1/n1"]
    assert [tuple(r) for r in conn.execute("SELECT kind, text, subject FROM fact")] == [("rumor", note["text"], "Moor")]
    assert exchange(conn, "r1")["chronicle"] is None


def test_1_a_check_is_announced_and_the_player_rolls(conn, scene, test_wiki):
    attempt = {"type": "attempt", "skill_id": "SKILL-101", "difficulty": "DIFF-03", "duration": "hour", "reason": "Tür aufbrechen"}
    note = {"kind": "fact", "text": "Die Tür ist alt."}
    transport = FakeTransport(
        decide(attempt), tell("Du stemmst dich gegen die Tür.", [note]),  # Probe offen: Notizen warten nicht
        decide({"type": "narrate", "text": "Die Tür knarrt.", "notes": []}),
        tell("Das Holz gibt nach."),
    )
    game_master = master(conn, test_wiki, transport)

    opened = game_master.play("r1", "Ich breche die Tür auf.")
    assert opened.turn.status == "awaiting_roll" and opened.notes == ()
    assert "Probe angekündigt: Klingenwaffen, SG 12, Grund: Tür aufbrechen." in user_message(transport, 1)

    game_master.play("r2", "Ich warte noch.")  # bei offener Probe kann das Modell nur erzählen
    assert action_schema(transport, 2)["properties"]["action"]["properties"]["type"]["enum"] == ["narrate"]

    rolled = game_master.roll("r3", 15)
    assert len(transport.requests) == 4  # der Wurf braucht nur Aufruf B
    assert rolled.turn.check.outcome == "success"
    assert "Wurf: W20 15 +0 = 15 gegen SG 12: Erfolg" in user_message(transport, 3)
    assert exchange(conn, "r3")["chronicle"] == "Tag 1, 09:00: Wurf eingetragen: 15 → Erfolg"
    assert conn.execute("SELECT count(*) FROM fact").fetchone()[0] == 0


def test_1_a_roll_without_open_check_is_rejected_without_narration(conn, scene, test_wiki):
    transport = FakeTransport()
    result = master(conn, test_wiki, transport).roll("r1", 12)
    assert (result.turn.reject_code, result.narration, transport.requests) == ("no_open_roll", None, [])


def test_1_a_rejected_action_is_narrated_and_changes_nothing(conn, scene, test_wiki):
    important = {"type": "meet_npc", "template_id": "koehler", "name": "Graf Kuno von Testhausen", "description": "Ein Graf"}
    transport = FakeTransport(decide(important), tell("Der Mann winkt ab."))
    before = game_state(conn)

    result = master(conn, test_wiki, transport).play("r1", "Ich spreche den Grafen an.")

    assert result.turn.reject_code == "important_figure"
    assert "Abgelehnt:" in user_message(transport, 1)
    after = game_state(conn)
    assert {t: rows for t, rows in after.items() if t not in ("turn", "exchange")} == {
        t: rows for t, rows in before.items() if t not in ("turn", "exchange")
    }
    assert exchange(conn, "r1")["chronicle"] is None


def test_1_stale_revision_is_resubmitted_once_without_the_model(conn, scene, test_wiki):
    add_player(conn, "player-2")

    def other_player_moves_first():
        submit(conn, "x1", 0, player_id="player-2", action=RecordFact(text="Fremde Notiz"), content=CONTENT, wiki=test_wiki)
        return decide(TO_TOWN)

    transport = FakeTransport(other_player_moves_first, tell())
    result = master(conn, test_wiki, transport).play("r1", "Ich gehe.")

    assert result.turn.request_id == "r1/a2" and result.turn.status == "accepted"
    assert conn.execute("SELECT reject_code FROM turn WHERE request_id = 'r1/a'").fetchone()[0] == "stale_revision"
    assert len(transport.requests) == 2 and location(conn) == BOOMTOWN


# --- 2: Das Modell setzt keine harten Werte ---


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "gather", "page_id": "sumpfkraut", "quantity": 99},  # Zusatzfeld
        {"type": "move", "destination_id": "crystal_field"},  # kein Weg dorthin: nicht in der Liste
        {"type": "record_fact", "text": "x"},  # Notizen nur mit der Erzählung
        {"type": "submit_roll", "value": 20},  # würfeln tut der Spieler
        {"type": "meet_npc", "template_id": "graf-test", "name": "X", "description": "Y"},  # keine NPC-Vorlage
    ],
)
def test_2_an_action_outside_the_offer_is_an_invalid_answer(conn, scene, test_wiki, bad):
    transport = FakeTransport(decide(bad), decide({"type": "sleep"}), tell())
    result = master(conn, test_wiki, transport).play("r1", "Ich tu was.")
    assert result.turn.effects[-1].type == "rest" and len(transport.requests) == 3
    assert "passt nicht zum Schema" in transport.requests[1]["body"]["messages"][-1]["content"]


def test_2_the_narration_cannot_carry_hard_values(conn, scene, test_wiki):
    claims = chat(json.dumps({"text": "Du findest 100 Gulden.", "notes": [], "money_pennies": 10000}))
    transport = FakeTransport(decide(TO_TOWN), claims, tell("Du findest 100 Gulden."))

    result = master(conn, test_wiki, transport).play("r1", "Ich suche Gold.")

    assert len(transport.requests) == 3  # Zusatzfeld: ein Wiederholungsversuch
    assert result.narration == "Du findest 100 Gulden." and money(conn) == 30  # nur erzählt, nichts gebucht
    assert "money_pennies" not in gm.NARRATION.json_schema()["properties"]


def test_2_at_most_two_notes(conn, scene, test_wiki):
    three = [{"kind": "fact", "text": f"Notiz {n}"} for n in range(3)]
    transport = FakeTransport(decide(TO_TOWN), tell("A", three), tell("B", three[:2]))
    result = master(conn, test_wiki, transport).play("r1", "Los.")
    assert result.narration == "B" and len(result.notes) == 2


def test_2_a_note_the_core_rejects_falls_away_alone(conn, scene, test_wiki):
    notes = [{"kind": "fact", "text": "Von Unbekannt", "source_npc_id": "niemand"}, {"kind": "fact", "text": "Gilt"}]
    transport = FakeTransport(decide(TO_TOWN), tell("Text", notes))
    result = master(conn, test_wiki, transport).play("r1", "Los.")
    assert [n.request_id for n in result.notes] == ["r1/n2"]
    assert [tuple(r) for r in conn.execute("SELECT text FROM fact")] == [("Gilt",)]


def test_2_a_failed_choice_changes_nothing(conn, scene, test_wiki):
    transport = FakeTransport(chat("Ich gehe nach Sturmhagen."), chat("{}"))
    before = game_state(conn)
    with pytest.raises(llm.ModelError):
        master(conn, test_wiki, transport).play("r1", "Los.")
    assert game_state(conn) == before


def test_2_targets_are_fixed_lists_from_the_state(conn, scene, test_wiki):
    meet = {"type": "meet_npc", "template_id": "koehler", "name": "Hanne Ruß", "description": "Rußige Hände"}
    transport = FakeTransport(decide(meet), tell(), decide({"type": "talk", "npc_id": "hanne-russ"}), tell())
    game_master = master(conn, test_wiki, transport)
    game_master.play("r1", "Ich spreche die Köhlerin an.")

    game_master.play("r2", "Ich rede weiter mit Hanne.")

    # Das Schema geht abgeflacht hinaus (llm.provider_schema): je Aktion ein Zweig in anyOf, Ziele als enum.
    variants = {v["properties"]["type"]["enum"][0]: v for v in action_schema(transport, 2)["properties"]["action"]["anyOf"]}

    def allowed(action: str, field: str) -> list[str]:
        return variants[action]["properties"][field]["enum"]

    assert allowed("talk", "npc_id") == allowed("contest", "npc_id") == ["hanne-russ"]
    assert allowed("move", "destination_id") == [BOOMTOWN]
    assert "SKILL-201" in allowed("contest", "skill_id") and "SKILL-202" not in allowed("contest", "skill_id")
    assert "record_fact" not in variants and "submit_roll" not in variants
    assert "  npc_id: hanne-russ = Hanne Ruß" in user_message(transport, 2)
    assert "NPC-IDs hier: hanne-russ" in user_message(transport, 3)


def test_2_control_characters_from_the_model_are_removed(conn, scene, test_wiki):
    transport = FakeTransport(decide(TO_TOWN), tell("Du gehst.\x1b[2J\nWeiter\u202e.", [{"kind": "fact", "text": "A\x07B"}]))
    result = master(conn, test_wiki, transport).play("r1", "Los.")
    assert result.narration == "Du gehst.[2J\nWeiter." and exchange(conn, "r1")["narration"] == result.narration
    assert [tuple(r) for r in conn.execute("SELECT text FROM fact")] == [("AB",)]


# --- 3: Das Modell bekommt nur Wissen, das die Figur hat ---


def test_3_a_hidden_stage_reaches_the_model_only_once_learned(conn, scene, test_wiki):
    transport = FakeTransport(
        decide(TO_TOWN), tell(),
        decide({"type": "examine", "page_id": "testdorf"}), tell("Du suchst."),
        tell("Da!"),
    )
    game_master = master(conn, test_wiki, transport)
    game_master.play("r1", "Ich gehe hinaus.")
    game_master.play("r2", "Ich suche das Dorf ab.")
    assert all(SECRET not in text for text in transport.prompts())

    game_master.roll("r3", 20)
    assert f"Die Figur erfährt über testdorf: {SECRET}" in user_message(transport, 4)


def test_3_npc_values_stay_hidden(conn, scene, test_wiki):
    meet = {"type": "meet_npc", "template_id": "koehler", "name": "Hanne Ruß", "description": "Rußige Hände"}
    transport = FakeTransport(decide(meet), tell("Eine Frau mit rußigen Händen nickt dir zu."))

    master(conn, test_wiki, transport).play("r1", "Ich spreche die Köhlerin an.")

    result = user_message(transport, 1).split("# Ergebnis des Codes\n")[1].split("\n\n")[0]
    assert result.splitlines() == [
        "Gewählte Aktion: neuen NPC ansprechen: Hanne Ruß",
        "Neu: Hanne Ruß (Köhler), Rußige Hände",
        "Dauer: 10 Min. Jetzt: Tag 1, 08:10",
    ]


# --- 4: Eine wiederholte request_id ruft das Modell nicht noch einmal auf ---


def test_4_a_repeated_request_id_replays_without_the_model(conn, scene, test_wiki):
    note = {"kind": "fact", "text": "Merken."}
    first = master(conn, test_wiki, FakeTransport(decide(TO_TOWN), tell("Hinaus.", [note]))).play("r1", "Ich gehe.")
    before = dump_state(conn)

    again = master(conn, test_wiki, FakeTransport()).play("r1", "Ich gehe.")

    assert again.replayed and (again.narration, again.turn.revision) == (first.narration, first.turn.revision)
    assert [n.request_id for n in again.notes] == ["r1/n1"]
    assert dump_state(conn) == before
    with pytest.raises(RequestIdConflict):
        master(conn, test_wiki, FakeTransport()).play("r1", "Ich gehe woandershin.")


def test_4_a_failed_narration_is_fetched_later_without_repeating_the_action(conn, scene, test_wiki):
    broken = llm.HttpResponse(500, b"")
    result = master(conn, test_wiki, FakeTransport(decide(TO_TOWN), broken)).play("r1", "Ich gehe.")
    assert result.narration is None and result.error.code == "provider_error"
    assert location(conn) == BOOMTOWN and exchange(conn, "r1")["narration"] is None
    turns = turn_count(conn)

    transport = FakeTransport(tell("Nachgeholt."))
    again = master(conn, test_wiki, transport).play("r1", "Ich gehe.")

    assert again.narration == "Nachgeholt." and len(transport.requests) == 1
    assert turn_count(conn) == turns and location(conn) == BOOMTOWN
    assert exchange(conn, "r1")["narration"] == "Nachgeholt."


def test_4_after_a_crash_between_turn_and_exchange_the_action_stands(conn, scene, test_wiki):
    submit(conn, "r1/a", 0, action=Move(destination_id=BOOMTOWN), content=CONTENT, wiki=test_wiki)
    transport = FakeTransport(tell("Weiter."))

    result = master(conn, test_wiki, transport).play("r1", "Ich gehe.")

    assert len(transport.requests) == 1 and "# Aufgabe B" in user_message(transport, 0)
    assert result.turn.replayed and turn_count(conn) == 1


def test_4_a_repeated_roll_replays(conn, scene, test_wiki):
    attempt = {"type": "attempt", "skill_id": "SKILL-101", "difficulty": "DIFF-03", "duration": "moment", "reason": "x"}
    game_master = master(conn, test_wiki, FakeTransport(decide(attempt), tell(), tell("Geschafft.")))
    game_master.play("r1", "Ich versuche es.")
    game_master.roll("r2", 18)
    before = dump_state(conn)
    again = master(conn, test_wiki, FakeTransport()).roll("r2", 18)
    assert again.replayed and again.narration == "Geschafft." and dump_state(conn) == before


# --- Gedächtnis (Entscheidungen 158 und 161) ---


def walk(conn, test_wiki, count: int, narration: str = "Unterwegs.") -> FakeTransport:
    """`count` Züge hin und her zwischen Zunfthaus und Sturmhagen."""
    for n in range(count):
        transport = FakeTransport(decide(TO_TOWN if n % 2 == 0 else TO_HALL), tell(narration))
        master(conn, test_wiki, transport).play(f"w{n}", f"Gang {n}")
    return transport


def test_memory_last_six_exchanges_verbatim_older_ones_as_chronicle(conn, scene, test_wiki):
    walk(conn, test_wiki, 8)
    transport = FakeTransport(decide({"type": "sleep"}), tell())

    master(conn, test_wiki, transport).play("r9", "Ich schlafe.")

    message = user_message(transport, 0)
    assert message.count("Spieler: Gang") == 6 and "Spieler: Gang 2\n" in message and "Gang 1\n" not in message
    chronicle = message.split("# Chronik\n")[1].split("\n\n")[0].splitlines()
    assert chronicle == ["- Tag 1, 08:05: gehen nach Sturmhagen", "- Tag 1, 08:10: gehen nach Zunfthaus"]
    manifest = json.loads(conn.execute("SELECT context_json FROM turn WHERE request_id = 'r9/a'").fetchone()[0])
    assert (manifest["history"], manifest["chronicle"]) == (6, 2)


def test_memory_long_narrations_respect_the_history_budget(conn, scene, test_wiki):
    walk(conn, test_wiki, 4, narration="x" * gm.NARRATION_CHARS)  # je etwa 500 Tokens
    transport = FakeTransport(decide({"type": "sleep"}), tell())

    master(conn, test_wiki, transport).play("r9", "Ich schlafe.")

    message = user_message(transport, 0)
    history = message.split("# Bisher\n")[1].split("\n\n# Zustand")[0]
    assert message.count("Spieler: Gang") == 2 and context.estimate_tokens(history) <= gm.HISTORY_BUDGET
    assert message.count("- Tag 1") == 2


def test_memory_the_chronicle_has_a_budget_newest_kept(conn, scene, test_wiki):
    walk(conn, test_wiki, 40)
    transport = FakeTransport(decide({"type": "sleep"}), tell())

    master(conn, test_wiki, transport).play("r", "Ich schlafe.")

    chronicle = user_message(transport, 0).split("# Chronik\n")[1].split("\n\n")[0]
    assert context.estimate_tokens(chronicle) <= gm.CHRONICLE_BUDGET + 30  # Zeilen ohne „- “ gezählt
    lines = chronicle.splitlines()
    assert lines[-1] == "- Tag 1, 10:50: gehen nach Zunfthaus"  # Gang 33, der neueste vor den sechs wörtlichen
    assert "- Tag 1, 08:05: gehen nach Sturmhagen" not in lines  # die ältesten fallen weg
    assert "Spieler: Gang 34" in user_message(transport, 0) and "Spieler: Gang 33" not in user_message(transport, 0)


def test_memory_long_or_empty_input_is_refused_without_a_call(conn, scene, test_wiki):
    transport = FakeTransport()
    for text in ("x" * (gm.MAX_INPUT_CHARS + 1), "   "):
        with pytest.raises(gm.InputTooLong):
            master(conn, test_wiki, transport).play("r1", text)
    assert transport.requests == [] and exchange(conn, "r1") is None


# --- Kommandozeile mit Spielleiter ---


# --- Spieltest 26.10.2026: neue Personen, Notizen nach Fehlschlag, Einstieg (Entscheidungen 217 bis 219) ---

KOEHLER = {"template_id": "koehler", "name": "Hanne Ruß", "description": "Rußgeschwärzt und misstrauisch."}


def tell_with(text: str, *, notes: list[dict] = (), npcs: list[dict] = ()):
    return chat(json.dumps({"text": text, "notes": list(notes), "npcs": list(npcs)}))


def game_minutes(conn) -> int:
    return conn.execute("SELECT game_minutes FROM campaign").fetchone()[0]


def test_new_people_in_the_narration_become_npcs_without_time(conn, scene, test_wiki):
    note = {"kind": "fact", "text": "Hanne verkauft Holzkohle.", "source_npc_id": "Hanne Ruß"}  # Name statt ID
    narrate = {"type": "narrate", "text": "Eine Köhlerin tritt ein.", "notes": [note], "npcs": [KOEHLER]}
    transport = FakeTransport(decide(narrate))

    result = master(conn, test_wiki, transport).play("r1", "Ich sehe mich um.")

    assert [n.request_id for n in result.npcs] == ["r1/p1"]
    assert tuple(conn.execute("SELECT npc_id, name, location_id FROM npc").fetchone()) == ("hanne-russ", "Hanne Ruß", GUILD_HALL)
    assert [r[0] for r in conn.execute("SELECT source_npc_id FROM fact")] == ["hanne-russ"]
    assert game_minutes(conn) == 0
    replay = master(conn, test_wiki, FakeTransport()).play("r1", "Ich sehe mich um.")
    assert [n.request_id for n in replay.npcs] == ["r1/p1"]


def test_a_person_with_an_unknown_template_falls_away_alone(conn, scene, test_wiki):
    dragon = KOEHLER | {"template_id": "drache"}
    transport = FakeTransport(decide(TO_TOWN), tell_with("Ein Drache?", npcs=[dragon]))

    result = master(conn, test_wiki, transport).play("r1", "Ich gehe hinaus.")

    assert (result.narration, result.npcs, location(conn)) == ("Ein Drache?", (), BOOMTOWN)
    assert "Vorlagen für npcs: koehler = Köhler" in user_message(transport, 1)


def test_no_notes_after_a_failed_check(conn, scene, test_wiki):
    attempt = {"type": "attempt", "skill_id": "SKILL-405", "difficulty": "DIFF-03", "duration": "moment", "reason": "lauschen"}
    rumor = {"kind": "rumor", "text": "Die Wachen schlafen."}
    transport = FakeTransport(decide(attempt), tell("Du lauschst."), tell_with("Nur Gemurmel.", notes=[rumor]))
    game_master = master(conn, test_wiki, transport)

    game_master.play("r1", "Ich lausche.")
    result = game_master.roll("r2", 3)

    assert result.turn.check.outcome == "failure" and result.notes == ()
    assert conn.execute("SELECT count(*) FROM fact").fetchone()[0] == 0
    assert "kein Geräusch, keine neue Information" in user_message(transport, 1)
    assert "Die Figur erfährt und gewinnt dadurch nichts; keine Notizen." in user_message(transport, 2)


def test_the_opening_tells_a_scene_from_what_occurs_here(conn, test_wiki):
    add_campaign(conn, CAMPAIGN)
    add_player(conn, PLAYER, location_id="crystal_field")  # Anderswo liegt im Wald: dort kommt der Köhler vor
    transport = FakeTransport(tell_with("Eine Köhlerin packt dich am Ärmel: „Hilf mir!“", npcs=[KOEHLER]))
    before = game_state(conn)

    result = master(conn, test_wiki, transport).opening("e1", random.Random(1))

    assert (result.narration, result.turn) == ("Eine Köhlerin packt dich am Ärmel: „Hilf mir!“", None)
    assert [n.request_id for n in result.npcs] == ["e1/p1"]
    assert "# Aufgabe Einstieg" in user_message(transport, 0) and "rund um: Köhler" in user_message(transport, 0)
    assert "Die Szene spielt genau hier: Glasgrund, nicht davor" in user_message(transport, 0)
    assert exchange(conn, "e1")["player_input"] == gm.OPENING_INPUT
    assert game_minutes(conn) == 0 and game_state(conn)["npc"] != before["npc"]
    assert master(conn, test_wiki, FakeTransport()).opening("e1").replayed


def test_a_payment_is_a_turn_and_the_narration_hears_the_amount(conn, scene, test_wiki):
    """Spieltest: „Der Wirt nimmt den Groschen“, das Geld blieb gleich (Entscheidung 220)."""
    pay = {"type": "give_money", "pennies": 10, "npc_id": None, "reason": "ein Bett"}
    transport = FakeTransport(decide(pay), tell("Der Wirt nimmt den Groschen."))
    before = money(conn)

    master(conn, test_wiki, transport).play("r1", "Ich zahle einen Groschen fürs Bett.")

    assert money(conn) == before - 10
    assert "Bezahlt: 1 Groschen" in user_message(transport, 1)
    assert "- give_money: Geld geben oder bezahlen" in user_message(transport, 0)


# --- Mehrere Schritte je Zug (Entscheidung 225) ---

TO_FIELD = {"type": "move", "destination_id": "crystal_field"}


def decide_steps(action: dict, *then: dict):
    return chat(json.dumps({"action": action, "then": list(then)}))


def test_several_steps_run_in_order_and_are_narrated_together(conn, scene, test_wiki):
    """Spieltest: „aus der Schenke zum Glasgrund“ führte nur einen Ort weit; jetzt geht es über Sturmhagen weiter."""
    transport = FakeTransport(decide_steps(TO_TOWN, TO_FIELD), tell("Du stapfst hinaus und weiter zum Feld."))

    result = master(conn, test_wiki, transport).play("r1", "Ich gehe über Sturmhagen zum Glasgrund.")

    assert location(conn) == "crystal_field"
    assert (result.turn.request_id, [t.request_id for t in result.more]) == ("r1/a", ["r1/b"])
    told = user_message(transport, 1)
    assert "Gewählte Aktion: gehen nach Sturmhagen\nAngekommen: Sturmhagen\nDanach: gehen nach Glasgrund" in told
    assert "Dauer: 95 Min." in told
    assert exchange(conn, "r1")["chronicle"] == "Tag 1, 09:35: gehen nach Sturmhagen; gehen nach Glasgrund"
    replay = master(conn, test_wiki, FakeTransport()).play("r1", "Ich gehe über Sturmhagen zum Glasgrund.")
    assert [t.request_id for t in replay.more] == ["r1/b"] and location(conn) == "crystal_field"


def test_an_open_check_ends_the_steps(conn, scene, test_wiki):
    attempt = {"type": "attempt", "skill_id": "SKILL-405", "difficulty": "DIFF-03", "duration": "moment", "reason": "lauschen"}
    transport = FakeTransport(decide_steps(attempt, TO_TOWN), tell("Du hältst den Atem an."))

    result = master(conn, test_wiki, transport).play("r1", "Ich lausche und gehe dann hinaus.")

    assert result.turn.status == "awaiting_roll" and result.more == () and location(conn) == GUILD_HALL
    assert "Nicht mehr ausgeführt: gehen nach Sturmhagen" in user_message(transport, 1)


def test_a_rejected_step_ends_the_steps_without_a_clock_line(conn, scene, test_wiki):
    to_tavern = {"type": "move", "destination_id": "tavern"}  # vom Glasgrund aus nicht verbunden
    transport = FakeTransport(decide_steps(TO_TOWN, TO_FIELD, to_tavern), tell("Der Weg zur Schenke führt nicht von hier."))

    result = master(conn, test_wiki, transport).play("r1", "Ich gehe zum Glasgrund und dann in die Schenke.")

    assert [t.status for t in result.more] == ["accepted", "rejected"] and location(conn) == "crystal_field"
    result_part = user_message(transport, 1).split("# Ergebnis des Codes")[1]
    assert "Danach: gehen nach Glasgrund\nAngekommen: Glasgrund\nDanach: gehen nach Schenke\nAbgelehnt:" in result_part
    assert "Jetzt: Tag" not in result_part


def test_narrate_before_steps_gives_way_to_the_steps(conn, scene, test_wiki):
    narrate = {"type": "narrate", "text": "Du gehst.", "notes": [], "npcs": []}
    transport = FakeTransport(decide_steps(narrate, TO_TOWN), tell("Draußen ist Schlamm."))

    result = master(conn, test_wiki, transport).play("r1", "Ich nicke und gehe hinaus.")

    assert result.turn.request_id == "r1/a" and location(conn) == BOOMTOWN and result.narration == "Draußen ist Schlamm."


def test_steps_are_offered_with_every_place_but_not_while_a_check_is_open(conn, scene, test_wiki):
    transport = FakeTransport(decide(TO_TOWN), tell(), decide({"type": "attempt", "skill_id": "SKILL-405",
        "difficulty": "DIFF-03", "duration": "moment", "reason": "x"}), tell(), decide({"type": "narrate", "text": "…"}))
    game_master = master(conn, test_wiki, transport)

    game_master.play("r1", "Los.")
    assert '"then"' in user_message(transport, 0) and "then.move.destination_id: boomtown = Sturmhagen" in user_message(transport, 0)
    game_master.play("r2", "Ich lausche.")
    game_master.play("r3", "Ich warte.")
    assert "then" not in json.dumps(action_schema(transport, 4)) and '"then"' not in user_message(transport, 4)


def test_cli_free_text_goes_to_the_game_master_commands_need_a_bang(tmp_path, monkeypatch):
    monkeypatch.setenv("FREI_KEY", "k")
    settings = tmp_path / "modelle.toml"
    settings.write_text('[[provider]]\nname = "frei"\nbase_url = "https://frei.example/v1"\nmodel = "m"\nkey_env = "FREI_KEY"\n')
    transport = FakeTransport(decide(TO_TOWN), tell("Du stapfst durch den Schlamm nach Sturmhagen."))
    script = "Ich gehe raus, nach Sturmhagen.\n!gehe Zunfthaus\nstatus\nende\n"
    out = io.StringIO()

    code = cli.main(["--db", str(tmp_path / "s.db"), "--testfigur", "--modell", str(settings)], io.StringIO(script), out, transport)

    text = out.getvalue()
    assert code == 0 and len(transport.requests) == 2
    assert "Du stapfst durch den Schlamm nach Sturmhagen.\n\nAngekommen: Sturmhagen. Neue Szene." in text
    assert "Angekommen: Zunfthaus. Neue Szene." in text  # mit ! ohne Modell


def test_cli_a_model_error_is_reported_and_nothing_happens(tmp_path, monkeypatch):
    monkeypatch.delenv("FREI_KEY", raising=False)
    settings = tmp_path / "modelle.toml"
    settings.write_text('[[provider]]\nname = "frei"\nbase_url = "https://frei.example/v1"\nmodel = "m"\nkey_env = "FREI_KEY"\n')
    out = io.StringIO()

    cli.main(["--db", str(tmp_path / "s.db"), "--testfigur", "--modell", str(settings)], io.StringIO("Hallo\n"), out, FakeTransport())

    assert "Spielleiter nicht erreichbar, nichts ist passiert: Kein Anbieter hat einen Schlüssel" in out.getvalue()
