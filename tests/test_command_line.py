"""Schritt 1d: Kommandozeile ohne Modell.

Ausgangslage: neue Kampagne aus dem Testinhalt mit der festen Testfigur im Zunfthaus, Tag 1, 08:00
(`--testfigur`; die Erschaffung prüft test_character_creation.py).
"""

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from valar import apply, cli, db
from valar.content import BOOMTOWN, CONTENT, GUILD_SHARD_ORDER
from valar.models import AttemptAction, Move, SubmitQuestItems, SubmitRoll

ROOT = Path(__file__).parents[1]


class Counter:
    """Vorhersagbare request_ids für Tests."""

    def __init__(self, prefix: str = "t") -> None:
        self.prefix, self.n = prefix, 0

    def __call__(self) -> str:
        self.n += 1
        return f"{self.prefix}-{self.n}"


def open_session(path: Path, prefix: str = "t") -> tuple[cli.Session, io.StringIO]:
    conn = db.connect(path)
    db.init_schema(conn)
    if not conn.execute("SELECT 1 FROM campaign").fetchone():
        apply.create_campaign_from_content(conn, cli.CAMPAIGN, CONTENT)
    out = io.StringIO()
    return cli.Session(conn, out, player_id=CONTENT.start_player.player_id, new_request_id=Counter(prefix)), out


def turns(conn) -> list[tuple]:
    return [tuple(r) for r in conn.execute("SELECT request_id, status, reject_code FROM turn ORDER BY turn_id")]


@pytest.fixture
def session(db_path):
    session, _ = open_session(db_path)
    yield session
    session.conn.close()


def play(session: cli.Session, *lines: str) -> str:
    """Spielt Zeilen und gibt nur die neue Ausgabe zurück."""
    start = len(session.out.getvalue())
    for line in lines:
        session.handle(line)
    return session.out.getvalue()[start:]


# --- 1: Jeder Befehl wird zur richtigen Aktion ---


@pytest.mark.parametrize(
    "line, action",
    [
        ("gehe Sturmhagen", Move(destination_id=BOOMTOWN)),
        ("gehe boomtown", Move(destination_id=BOOMTOWN)),
        ("geh STURMHAGEN", Move(destination_id=BOOMTOWN)),
        ("gehe Atlantis", Move(destination_id="Atlantis")),  # der Kern lehnt ab, als Spielzug
        ("wurf 14", SubmitRoll(value=14)),
        ("abgeben", SubmitQuestItems(quest_id=GUILD_SHARD_ORDER)),
        ("abgeben guild_shard_order", SubmitQuestItems(quest_id=GUILD_SHARD_ORDER)),
        (
            "versuche Klingenwaffen 3 stunde Er bricht die Tür auf",
            AttemptAction(skill_id="SKILL-101", difficulty="DIFF-03", duration="hour", reason="Er bricht die Tür auf"),
        ),
        (
            'versuche "Schlösser knacken" schwer augenblick Das Schloss',
            AttemptAction(skill_id="SKILL-202", difficulty="DIFF-04", duration="moment", reason="Das Schloss"),
        ),
        (
            "versuche SKILL-405 DIFF-07 halbtag Spuren lesen",
            AttemptAction(skill_id="SKILL-405", difficulty="DIFF-07", duration="half_day", reason="Spuren lesen"),
        ),
        (
            "versuche erstehilfe sehr-leicht moment Verband",
            AttemptAction(skill_id="SKILL-401", difficulty="DIFF-01", duration="moment", reason="Verband"),
        ),
    ],
)
def test_1_every_command_becomes_the_right_action(session, line, action):
    assert cli.parse(line, CONTENT, session.status()) == cli.Turn(action)


@pytest.mark.parametrize(
    "line, command",
    [
        ("schau", cli.Query("look")),
        ("status", cli.Query("status")),
        ("hilfe", cli.Help()),
        ("ende", cli.Quit()),
        ("", None),
        ("   # nur ein Kommentar", None),
    ],
)
def test_1b_queries_and_controls(session, line, command):
    assert cli.parse(line, CONTENT, session.status()) == command


def test_1c_a_command_is_played_as_a_turn(session):
    output = play(session, "gehe Sturmhagen")

    assert "Angekommen: Sturmhagen" in output
    assert "Jetzt: Tag 1, 08:05" in output
    assert turns(session.conn) == [("t-1", "accepted", None)]


# --- 2: Falsche Eingabe ---


@pytest.mark.parametrize(
    "line",
    [
        "fliegen",
        "gehe",
        "wurf",
        "wurf 0",
        "wurf 21",
        "wurf sieben",
        "wurf 7 8",
        "versuche Klingenwaffen 3 stunde",  # Grund fehlt
        "versuche Zauberei 3 stunde Feuer",  # unbekannter Skillname
        "versuche Klingenwaffen 9 stunde x",
        "versuche Klingenwaffen 3 woche x",
        "versuche SKILL-1 3 stunde x",
        "versuche Klingenwaffen 3 stunde " + "x" * 201,
        'gehe "Sturmhagen',  # offenes Anführungszeichen
    ],
)
def test_2_wrong_input_shows_help_and_is_no_turn(session, line):
    before = (session.status(), turns(session.conn))

    output = play(session, line)

    assert "Befehle:" in output
    assert (session.status(), turns(session.conn)) == before


def test_2b_rejected_actions_are_turns_not_input_errors(session):
    output = play(session, "gehe Atlantis", "versuche SKILL-999 3 stunde x", "wurf 7")

    assert "Befehle:" not in output
    assert [code for _, _, code in turns(session.conn)] == ["unknown_location", "unknown_skill", "no_open_roll"]


# --- 3: request_id und veraltete Revision ---


def test_3_every_input_gets_a_new_request_id(session):
    play(session, "gehe Sturmhagen", "gehe Sturmhagen", "gehe Zunfthaus")

    assert turns(session.conn) == [("t-1", "accepted", None), ("t-2", "rejected", "already_there"), ("t-3", "accepted", None)]


def test_3b_stale_revision_reads_again_and_retries_with_a_new_request_id(db_path):
    first, _ = open_session(db_path, "a")
    second, out = open_session(db_path, "b")
    try:
        play(first, "gehe Sturmhagen")  # der zweite Client kennt Revision 1 nicht

        output = play(second, "schau", "gehe Zunfthaus")

        assert "neu gelesen" in output
        assert turns(second.conn) == [
            ("a-1", "accepted", None),
            ("b-1", "rejected", "stale_revision"),
            ("b-2", "accepted", None),
        ]
        assert second.revision == 2
    finally:
        first.conn.close()
        second.conn.close()


# --- 4: Ein ganzer Ablauf per Skript über die Standardeingabe ---

SCRIPT = """\
schau
versuche Klingenwaffen 3 stunde Die Tür klemmt
gehe Sturmhagen
wurf 7
gehe Sturmhagen
status
ende
gehe Zunfthaus
"""


def run_cli(db_file: Path, script: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "valar.cli", "--db", str(db_file), "--testfigur"],
        input=script,
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=60,
        check=True,
    )
    assert result.stderr == ""
    return result.stdout


def test_4_a_whole_run_from_standard_input(tmp_path):
    output = run_cli(tmp_path / "spiel.db", SCRIPT)

    assert output.startswith("Neue Kampagne mit der Testfigur angelegt.")
    assert "[Tag 1, 08:00 · Zunfthaus] > schau" in output  # Eingaben stehen im Protokoll
    assert "Probe offen: Klingenwaffen, SG 12 (durchschnitt), Bonus +5" in output
    assert "Abgelehnt: Eine Probe ist offen. Erst würfeln. (roll_pending)" in output
    assert "W20 7 +5 = 12 gegen SG 12: Erfolg, 2 Ticks" in output
    assert "Geld: 3 Groschen" in output
    assert "> gehe Zunfthaus" not in output  # nach `ende` wird nichts mehr gelesen


def test_4b_end_of_input_ends_the_run(tmp_path):
    output = run_cli(tmp_path / "spiel.db", "gehe Sturmhagen\n")
    assert "Angekommen: Sturmhagen" in output


# --- 5: Beenden und neu starten ---


def test_5_state_and_open_roll_survive_a_restart(tmp_path):
    db_file = tmp_path / "spiel.db"
    run_cli(db_file, "gehe Sturmhagen\nversuche Klingenwaffen 3 halbtag Ein Gerangel\nende\n")

    output = run_cli(db_file, "status\nwurf 7\n")

    assert "Neue Kampagne" not in output  # keine zweite Kampagne
    assert "[Tag 1, 08:05 · Sturmhagen · Probe offen] > status" in output
    assert "Probe offen: Klingenwaffen" in output
    assert "W20 7 +5 = 12 gegen SG 12: Erfolg" in output
    assert "Jetzt: Tag 1, 14:05" in output  # halber Tag gutgeschrieben


# --- Anzeige ---


def test_status_shows_money_clock_figure_and_open_roll(session):
    output = play(session, "versuche Klingenwaffen 1 moment Kleinigkeit", "status")

    assert "Testfigur · Level 1 · Zunfthaus" in output
    assert "Tag 1, 08:00 · Revision 1" in output
    assert "Geld: 3 Groschen" in output
    assert "Klingenwaffen 30 (0 Ticks)" in output
    assert "Keine Ticks: Routine." in output
    assert "Jetzt würfeln" in output


def test_second_success_in_the_scene_says_why_there_are_no_ticks(session):
    output = play(session, "versuche Klingenwaffen 3 moment a", "wurf 7", "versuche Klingenwaffen 3 moment b")
    assert "Keine Ticks: Skill hat in dieser Szene schon Ticks gebracht." in output


def test_look_shows_exits_with_direction_distance_and_time(session):
    output = play(session, "gehe Sturmhagen", "schau")
    assert "Sturmhagen · Tag 1, 08:05" in output
    assert "  Glasgrund (NO, 7,5 km, 90 Min)" in output
    assert "  Zunfthaus (S, 400 m, 5 Min)" in output


def test_quest_turn_in_shows_the_reward(session):
    output = play(session, "abgeben")
    assert "Erhalten: 1 Gulden" in output
    assert "Auftrag erledigt: guild_shard_order" in output


def test_queries_change_nothing(session):
    before = json.dumps(session.status().model_dump())
    play(session, "schau", "status", "hilfe")
    assert json.dumps(session.status().model_dump()) == before
    assert turns(session.conn) == []


# --- Schritt 1e: Betrachten, Untersuchen, Sammeln ---


@pytest.fixture
def wiki_session(db_path, tmp_path):
    from tests.helpers import TEST_WIKI, content_pointing_to, write_wiki
    from valar import wiki

    write_wiki(tmp_path / "w", TEST_WIKI)
    conn = db.connect(db_path)
    db.init_schema(conn)
    content = content_pointing_to(boomtown="testdorf")
    apply.create_campaign_from_content(conn, cli.CAMPAIGN, content)
    session = cli.Session(
        conn,
        io.StringIO(),
        player_id=content.start_player.player_id,
        content=content,
        wiki=wiki.load(tmp_path / "w"),
        new_request_id=Counter("w"),
    )
    yield session
    conn.close()


def test_1e_look_lists_what_occurs_here(wiki_session):
    out = play(wiki_session, "schau")
    assert "Region: testland · Landschaft: moor" in out
    assert "  Sumpfkraut (Pflanze, häufig, sammelbar)" in out
    assert "  wolf (Tier, gelegentlich)" in out


def test_1e_examine_and_gather_by_name(wiki_session):
    out = play(wiki_session, "betrachte Sumpfkraut")
    assert "Sumpfkraut (Pflanze)" in out and "Du weißt: Naturkunde leicht." in out
    assert turns(wiki_session.conn) == []  # Betrachten ist kein Spielzug

    out = play(wiki_session, "untersuche sumpfkraut", "wurf 20")
    assert "Grund: Sumpfkraut untersuchen" in out
    assert "Neu erfahren: Naturkunde extrem." in out

    out = play(wiki_session, "sammle Sumpfkraut", "wurf 15")
    assert "Grund: Sumpfkraut sammeln" in out and "Gesammelt: " in out
    assert "Sumpfkraut" in play(wiki_session, "status").split("Gepäck: ")[1]


def test_1e_unknown_things(wiki_session):
    assert play(wiki_session, "betrachte Bär") == "Das gibt es hier nicht.\n"
    assert "(unknown_page)" in play(wiki_session, "sammle Bär")
    assert "(not_gatherable)" in play(wiki_session, "sammle wolf")
    assert "Was?" in play(wiki_session, "untersuche")


# --- Schritt 1f: NPCs, Notizen, Kampagnen-Seiten ---


def test_1f_meet_talk_note_and_page(wiki_session):
    out = play(wiki_session, 'triff köhler "Hanne Ruß" Rußige Hände')
    assert "Neu: Hanne Ruß (Köhler), ID hanne-russ" in out
    assert "Leute: Hanne Ruß (Köhler): Rußige Hände" in play(wiki_session, "schau")
    assert "Du sprichst mit Hanne Ruß (Köhler)." in play(wiki_session, "rede Hanne")

    assert "Notiert (Gerücht)." in play(wiki_session, "notiere gerücht Im Moor spukt es")
    assert "Gerücht: Im Moor spukt es" in play(wiki_session, "notizen")

    out = play(wiki_session, 'seite heilkraut "Graue Minze" Wächst im Schlamm')
    assert "Neue Kampagnen-Seite: Graue Minze (Pflanze), ID graue-minze" in out
    assert "Graue Minze (Pflanze, gelegentlich, sammelbar)" in play(wiki_session, "schau")


def test_1f_wrong_input(wiki_session):
    assert "(unknown_template)" in play(wiki_session, 'triff drache "Bob" groß')
    assert "Treffen braucht" in play(wiki_session, "triff köhler")
    assert "(important_figure)" in play(wiki_session, 'triff köhler "Graf Kuno von Testhausen" Ein Graf')
    assert "(unknown_npc)" in play(wiki_session, "rede niemand")
    assert "Noch nichts notiert." in play(wiki_session, "notizen")


# --- Schritt 1g: Vergleichsprobe ---


def test_1g_contest_shows_the_npc_roll_before_the_players_roll(wiki_session):
    play(wiki_session, 'triff köhler "Hanne Ruß" Rußige Hände')
    out = play(wiki_session, "vergleiche Täuschen Hanne augenblick Sie soll mir glauben")
    assert "Gegenprobe: Hanne Ruß würfelt" in out and "(Menschenkenntnis). Das ist der SG" in out
    assert "Probe offen: Täuschen, SG" in out
    assert "W20 20" in play(wiki_session, "wurf 20")
    assert "(no_counter_skill)" in play(wiki_session, "vergleiche Naturkunde Hanne augenblick x")
    assert "Vergleichsprobe braucht" in play(wiki_session, "vergleiche Täuschen Hanne")


# --- Schritt 1h: Schlafen, Zustand, Knappheit ---


def test_1h_sleep_status_and_scarcity(wiki_session):
    out = play(wiki_session, "status")
    assert "Hunger: Satt · Erschöpfung: Ausgeruht (0 Std. wach) · Wetter: Kühler Altweibersommer" in out
    assert "3 ration" in out  # im Test-Wiki ohne eigene Seite: die ID

    out = play(wiki_session, "schlafe")
    assert "Geschlafen. Ausgeruht." in out and "Jetzt: Tag 1, 20:00" in out  # 8 Uhr: höchstens 12 Stunden
    out = play(wiki_session, "schlafe")
    assert "Mitternacht: Tag 2, Wetter: " in out and "Ration gegessen." in out

    out = play(wiki_session, "knapp Sumpfkraut weg woche Abgegrast")
    assert "Vermerkt: Sumpfkraut ist hier weg." in out
    assert "(scarce)" in play(wiki_session, "sammle Sumpfkraut")
    assert "STUFE: knapp oder weg" in play(wiki_session, "knapp Sumpfkraut viel tag x")


# --- Schritt 1j: Nachschlagen und Kontext ---


def test_1j_lookup_and_context_are_queries(wiki_session):
    out = play(wiki_session, "suche Sumpfkraut")
    assert out.startswith("1. Sumpfkraut")
    assert "Nichts gefunden." in play(wiki_session, "suche xyzzy")
    out = play(wiki_session, "kontext Ich pflücke Sumpfkraut")
    assert out.startswith("# Zustand") and "# Genannt" in out and "Tokens Weltwissen" in out
    assert turns(wiki_session.conn) == []
