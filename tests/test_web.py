"""Schritt 3: Weboberfläche, ohne Netz und ohne Browser (Entscheidungen 167 bis 169 und 171 bis 177).

Die Nummern folgen dem README. Die Anwendung wird direkt als WSGI aufgerufen; das Modell ist eine Attrappe.
Testdaten wie in Schritt 2b: Sturmhagen zeigt auf das Testdorf des Test-Wikis.
"""

import io
import json
import re
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode
from wsgiref.util import setup_testing_defaults

import pytest

from tests.helpers import (
    CAMPAIGN,
    PLAYER,
    SKILL,
    TEST_WIKI,
    FakeTransport,
    add_campaign,
    add_player,
    chat,
    content_pointing_to,
    dump_state,
    location,
    turn_count,
    write_wiki,
)
from valar import apply, db, keys, llm, web, wiki
from valar.content import BOOMTOWN, GUILD_HALL

CONTENT = content_pointing_to(boomtown="testdorf", crystal_field="anderswo")
NOW = datetime(2026, 10, 22, tzinfo=UTC)
SETTINGS = """
[budget]
warn_eur = 10
stop_eur = 20

[[provider]]
name = "frei"
base_url = "https://frei.example/v1"
model = "m"
key_env = "FREI_KEY"
"""
TO_TOWN = {"type": "move", "destination_id": BOOMTOWN}
ATTEMPT = {"type": "attempt", "skill_id": SKILL, "difficulty": "DIFF-03", "duration": "moment", "reason": "Tür aufbrechen"}
SECRET_KEY = "sk-geheim-ABCDEFGH-9876"
SHEET = {
    "name": "Grete Müller",
    "attr_STR": "14", "attr_GES": "14", "attr_KON": "12", "attr_INT": "8", "attr_WEI": "10", "attr_CHA": "8",
    "haupt": ["SKILL-101", "SKILL-102"],
    "neben": ["SKILL-201", "SKILL-202", "SKILL-203", "SKILL-204"],
    "ausruestung": "wanderer",
    "gegenstand": "Ein Knopf aus Horn",
    "hintergrund": "",
}


def game_state(state: dict) -> dict:
    """Spielstand ohne das Kostenprotokoll: Auch ein gescheiterter Aufruf wird gezählt."""
    return {t: rows for t, rows in state.items() if t not in ("model_call", "sqlite_sequence")}


def decide(action: dict):
    return chat(json.dumps({"action": action}))


def tell(text: str = "Der Schlamm schmatzt unter deinen Stiefeln.", notes: list[dict] = ()):
    return chat(json.dumps({"text": text, "notes": list(notes)}))


@dataclass
class Page:
    status: str
    headers: dict[str, str]
    text: str

    @property
    def code(self) -> int:
        return int(self.status.split()[0])

    def request_id(self) -> str:
        return re.search(r'name="request_id" value="(web-[0-9a-f]{32})"', self.text).group(1)


class Browser:
    def __init__(self, app: web.App, host: str = "localhost:8000") -> None:
        self.app = app
        self.host = host

    def call(self, method: str, path: str, data: dict | None = None, *, query: str = "", headers: dict | None = None) -> Page:
        environ: dict = {}
        setup_testing_defaults(environ)
        body = urlencode(data or {}, doseq=True).encode()
        environ.update(
            REQUEST_METHOD=method,
            PATH_INFO=path,
            QUERY_STRING=query,
            HTTP_HOST=self.host,
            CONTENT_LENGTH=str(len(body)),
            CONTENT_TYPE="application/x-www-form-urlencoded",
        )
        if method == "POST":
            environ["HTTP_ORIGIN"] = f"http://{self.host}"
        for name, value in (headers or {}).items():
            if value is None:
                environ.pop(name, None)
            else:
                environ[name] = value
        environ["wsgi.input"] = io.BytesIO(body)
        environ["wsgi.errors"] = io.StringIO()
        seen = {}

        def start_response(status, response_headers):
            seen["status"], seen["headers"] = status, dict(response_headers)

        text = b"".join(self.app(environ, start_response)).decode()
        return Page(seen["status"], seen["headers"], text)

    def get(self, path: str, query: str = "", **kwargs) -> Page:
        return self.call("GET", path, query=query, **kwargs)

    def post(self, path: str, data: dict, **kwargs) -> Page:
        return self.call("POST", path, data, **kwargs)

    def say(self, text: str, page: Page | None = None) -> Page:
        """Eine Eingabe auf der Spielseite, mit der request_id des zuletzt gezeigten Formulars."""
        page = page or self.get(f"/spiel/{PLAYER}")
        return self.post(f"/spiel/{PLAYER}", {"request_id": page.request_id(), "eingabe": text})


@pytest.fixture
def test_wiki(tmp_path: Path) -> wiki.Wiki:
    write_wiki(tmp_path / "w", TEST_WIKI)
    return wiki.load(tmp_path / "w")


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "daten"


@pytest.fixture
def game_db(data_dir: Path):
    """Der Spielstand im Datenordner, mit der Testfigur im Zunfthaus (Bonus +5 auf Klingenwaffen)."""
    data_dir.mkdir()
    conn = db.connect(data_dir / web.DB_FILE)
    db.init_schema(conn)
    add_campaign(conn, CAMPAIGN)
    add_player(conn, PLAYER, location_id=GUILD_HALL, attributes={"STR": 14, "GES": 8}, skills={SKILL: 30})
    yield conn
    conn.close()


def make_app(data_dir: Path, test_wiki: wiki.Wiki, transport: FakeTransport | None = None, *, settings: bool = True, environ=None, hosts=()) -> web.App:
    if settings:
        data_dir.mkdir(exist_ok=True)
        (data_dir / web.SETTINGS_FILE).write_text(SETTINGS, encoding="utf-8")
    return web.App(
        data_dir,
        hosts=hosts,
        campaign_id=CAMPAIGN,
        content=CONTENT,
        wiki=test_wiki,
        transport=transport or FakeTransport(),
        environ={"FREI_KEY": "k-aus-der-umgebung"} if environ is None else environ,
        now=lambda: NOW,
    )


# --- 1: Figur erschaffen ---


def test_1_without_a_character_the_start_page_leads_to_creation(data_dir, test_wiki):
    browser = Browser(make_app(data_dir, test_wiki))

    page = browser.get("/")

    assert (page.code, page.headers["Location"]) == (303, "/erschaffen")
    form = browser.get("/erschaffen")
    assert form.code == 200 and 'name="attr_STR"' in form.text and 'value="SKILL-101"' in form.text


def test_1_the_form_creates_the_character(data_dir, test_wiki):
    app = make_app(data_dir, test_wiki)
    browser = Browser(app)

    page = browser.post("/erschaffen", SHEET)

    assert (page.code, page.headers["Location"]) == (303, "/spiel/grete-mueller")
    play = browser.get("/spiel/grete-mueller")
    assert play.code == 200 and "Grete Müller" in play.text
    with db.connect(data_dir / web.DB_FILE) as conn:
        row = conn.execute("SELECT name, money_pennies FROM player WHERE player_id = 'grete-mueller'").fetchone()
    assert (row["name"], row["money_pennies"]) == ("Grete Müller", 250)
    assert "Grete Müller" in browser.get("/").text


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"attr_STR": "15"}, "Genau 18 Punkte"),
        ({"attr_STR": "viel"}, "ganze Zahl"),
        ({"haupt": ["SKILL-101"]}, "Hauptskills"),
        ({"neben": ["SKILL-101", "SKILL-202", "SKILL-203", "SKILL-204"]}, ""),
        ({"ausruestung": "schatztruhe"}, ""),
        ({"name": "Grete\x1b[31m"}, ""),
        ({"gegenstand": ""}, ""),
    ],
)
def test_1_a_wrong_sheet_shows_why_and_creates_nothing(data_dir, test_wiki, changes, message):
    browser = Browser(make_app(data_dir, test_wiki))
    with db.connect(data_dir / web.DB_FILE) as conn:
        before = dump_state(conn)

    page = browser.post("/erschaffen", SHEET | changes)

    assert page.code == 400 and 'class="fehler"' in page.text and message in page.text
    assert 'value="Ein Knopf aus Horn"' in page.text or "gegenstand" in changes  # Eingaben bleiben stehen
    with db.connect(data_dir / web.DB_FILE) as conn:
        assert dump_state(conn) == before


def test_1_the_same_name_twice_is_rejected(data_dir, test_wiki):
    browser = Browser(make_app(data_dir, test_wiki))
    browser.post("/erschaffen", SHEET)

    page = browser.post("/erschaffen", SHEET)

    assert page.code == 400 and "gibt es schon" in page.text


# --- 2: Spielen mit Spielleiter ---


def test_2_free_text_goes_to_the_game_master(game_db, data_dir, test_wiki):
    transport = FakeTransport(decide(TO_TOWN), tell("Du trittst hinaus in den Schlamm."))
    browser = Browser(make_app(data_dir, test_wiki, transport))

    page = browser.say("Ich gehe hinaus nach Sturmhagen.")

    assert page.code == 200
    assert "Du trittst hinaus in den Schlamm." in page.text
    assert "Angekommen: Sturmhagen. Neue Szene." in page.text  # harte Folgen, wie der Code sie bestimmt hat
    assert location(game_db) == BOOMTOWN
    later = browser.get(f"/spiel/{PLAYER}")
    assert "Ich gehe hinaus nach Sturmhagen." in later.text and "Du trittst hinaus in den Schlamm." in later.text
    assert "Sturmhagen" in later.text.split("</header>")[0]  # Kopf zeigt den neuen Ort


def test_2_an_open_roll_shows_a_die_field_and_the_roll_is_narrated(game_db, data_dir, test_wiki):
    transport = FakeTransport(decide(ATTEMPT), tell("Du stemmst dich gegen die Tür."), tell("Die Tür gibt nach."))
    browser = Browser(make_app(data_dir, test_wiki, transport))

    page = browser.say("Ich breche die Tür auf.")

    assert 'name="wurf"' in page.text and "Probe offen:" in page.text and "SG 12" in page.text
    rolled = browser.post(f"/spiel/{PLAYER}", {"request_id": page.request_id(), "wurf": "15"})
    assert "Die Tür gibt nach." in rolled.text and "W20 15 +5 = 20 gegen SG 12" in rolled.text
    assert 'name="wurf"' not in rolled.text


def test_2_a_model_error_changes_nothing_and_is_shown(game_db, data_dir, test_wiki):
    transport = FakeTransport(llm.HttpResponse(500, b"kaputt"))
    browser = Browser(make_app(data_dir, test_wiki, transport))
    before = dump_state(game_db)

    page = browser.say("Ich gehe nach Sturmhagen.")

    assert page.code == 200 and "Spielleiter nicht erreichbar, nichts ist passiert" in page.text
    assert game_state(dump_state(game_db)) == game_state(before)


# --- 3: request_id je Formular ---


def test_3_a_form_sent_twice_acts_once(game_db, data_dir, test_wiki):
    transport = FakeTransport(decide(TO_TOWN), tell("Du trittst hinaus."))  # nur zwei Antworten: kein dritter Aufruf
    browser = Browser(make_app(data_dir, test_wiki, transport))
    form = browser.get(f"/spiel/{PLAYER}")

    first = browser.say("Ich gehe nach Sturmhagen.", form)
    second = browser.say("Ich gehe nach Sturmhagen.", form)

    assert len(transport.requests) == 2 and turn_count(game_db) == 1
    assert "Du trittst hinaus." in second.text and "Angekommen: Sturmhagen" in second.text
    assert first.request_id() != second.request_id() != form.request_id()  # jede Seite bringt ein neues Formular


def test_3_the_same_form_with_other_text_is_refused(game_db, data_dir, test_wiki):
    transport = FakeTransport(decide(TO_TOWN), tell())
    browser = Browser(make_app(data_dir, test_wiki, transport))
    form = browser.get(f"/spiel/{PLAYER}")
    browser.say("Ich gehe nach Sturmhagen.", form)

    page = browser.say("Ich bleibe hier.", form)

    assert page.code == 200 and "schon mit anderer Eingabe gesendet" in page.text
    assert len(transport.requests) == 2 and turn_count(game_db) == 1


@pytest.mark.parametrize("request_id", ["", "abc", "cli-" + "0" * 32, "web-" + "Z" * 32])
def test_3_without_a_valid_request_id_nothing_happens(game_db, data_dir, test_wiki, request_id):
    transport = FakeTransport()
    browser = Browser(make_app(data_dir, test_wiki, transport))

    page = browser.post(f"/spiel/{PLAYER}", {"request_id": request_id, "eingabe": "Ich gehe."})

    assert page.code == 400 and transport.requests == [] and turn_count(game_db) == 0


def test_3_a_missing_narration_is_fetched_with_the_same_request_id(game_db, data_dir, test_wiki):
    transport = FakeTransport(decide(TO_TOWN), llm.HttpResponse(500, b""), tell("Endlich draußen."))
    browser = Browser(make_app(data_dir, test_wiki, transport))
    page = browser.say("Ich gehe nach Sturmhagen.")
    assert "Erzählung fehlt" in page.text and "Erzählung nachholen" in page.text

    retry = re.search(r'class="nachholen">.*?name="request_id" value="([^"]+)".*?name="eingabe" value="([^"]+)"', page.text, re.S)
    again = browser.post(f"/spiel/{PLAYER}", {"request_id": retry.group(1), "eingabe": retry.group(2)})

    assert "Endlich draußen." in again.text and "Erzählung nachholen" not in again.text
    assert turn_count(game_db) == 1 and len(transport.requests) == 3


# --- 4: Abfragen und Spielen ohne Modell ---


@pytest.mark.parametrize(("show", "expected"), [("schau", "Wege:"), ("status", "Geld:"), ("notizen", "Noch nichts"), ("hilfe", "Befehle:")])
def test_4_queries_are_no_turns(game_db, data_dir, test_wiki, show, expected):
    browser = Browser(make_app(data_dir, test_wiki))
    before = dump_state(game_db)

    page = browser.get(f"/spiel/{PLAYER}", f"zeige={show}")

    assert page.code == 200 and expected in page.text
    assert dump_state(game_db) == before


def test_4_without_model_settings_the_commands_of_the_command_line_apply(game_db, data_dir, test_wiki):
    transport = FakeTransport()
    browser = Browser(make_app(data_dir, test_wiki, transport, settings=False))
    form = browser.get(f"/spiel/{PLAYER}")
    assert "ohne Spielleiter" in form.text

    page = browser.say("gehe Sturmhagen", form)

    assert "Angekommen: Sturmhagen" in page.text and location(game_db) == BOOMTOWN and transport.requests == []
    assert "Befehle:" in browser.say("tanze wild").text  # falsche Eingabe: Hilfetext, kein Spielzug
    assert turn_count(game_db) == 1


def test_4_unknown_player_or_page(game_db, data_dir, test_wiki):
    browser = Browser(make_app(data_dir, test_wiki))

    assert browser.get("/spiel/niemand").code == 404
    assert browser.get("/gibtsnicht").code == 404
    assert browser.call("PUT", "/").code == 405


# --- 5: Schlüssel ---


def test_5_a_key_is_stored_and_shown_only_by_its_end(game_db, data_dir, test_wiki):
    browser = Browser(make_app(data_dir, test_wiki, environ={}))
    assert "FREI_KEY: fehlt" in browser.get("/einstellungen").text

    page = browser.post("/einstellungen/schluessel", {"key_env": "FREI_KEY", "schluessel": SECRET_KEY})

    assert (page.code, page.headers["Location"]) == (303, "/einstellungen?ok=schluessel")
    shown = browser.get("/einstellungen", "ok=schluessel")
    assert "gesetzt, endet auf …9876" in shown.text and "Schlüssel gespeichert." in shown.text
    assert SECRET_KEY not in shown.text and "ABCDEFGH" not in shown.text
    path = data_dir / keys.KEY_FILE
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text()) == {"FREI_KEY": SECRET_KEY}
    assert SECRET_KEY not in json.dumps(dump_state(game_db), default=str)


def test_5_the_client_uses_the_stored_key_and_the_environment_comes_first(game_db, data_dir, test_wiki):
    transport = FakeTransport(decide(TO_TOWN), tell(), decide(TO_TOWN), tell())
    keys.KeyStore(data_dir).set("FREI_KEY", SECRET_KEY)

    Browser(make_app(data_dir, test_wiki, transport, environ={})).say("Los.")
    Browser(make_app(data_dir, test_wiki, transport, environ={"FREI_KEY": "aus-der-umgebung"})).say("Weiter.")

    assert [r["headers"]["Authorization"] for r in transport.requests] == [f"Bearer {SECRET_KEY}"] * 2 + ["Bearer aus-der-umgebung"] * 2
    page = Browser(make_app(data_dir, test_wiki, environ={"FREI_KEY": "aus-der-umgebung"})).get("/einstellungen")
    assert "FREI_KEY: aus der Umgebung" in page.text and "aus-der-umgebung" not in page.text


def test_5_change_and_delete_a_key(game_db, data_dir, test_wiki):
    browser = Browser(make_app(data_dir, test_wiki, environ={}))
    browser.post("/einstellungen/schluessel", {"key_env": "FREI_KEY", "schluessel": SECRET_KEY})
    browser.post("/einstellungen/schluessel", {"key_env": "FREI_KEY", "schluessel": "neuer-schluessel-1234"})
    assert "endet auf …1234" in browser.get("/einstellungen").text

    page = browser.post("/einstellungen/schluessel", {"key_env": "FREI_KEY", "aktion": "loeschen"})

    assert page.headers["Location"] == "/einstellungen?ok=geloescht"
    assert "FREI_KEY: fehlt" in browser.get("/einstellungen").text
    assert keys.KeyStore(data_dir).read() == {}


@pytest.mark.parametrize(
    ("key_env", "key"),
    [("FREI_KEY", "kurz"), ("FREI_KEY", "mit leer zeichen drin"), ("FREI_KEY", "zeile\r\nX-Evil: 1"), ("FREI_KEY", "schlüssel-mit-umlaut"), ("PATH", SECRET_KEY)],
)
def test_5_a_wrong_key_or_name_is_refused_without_echo(game_db, data_dir, test_wiki, key_env, key):
    browser = Browser(make_app(data_dir, test_wiki, environ={}))

    page = browser.post("/einstellungen/schluessel", {"key_env": key_env, "schluessel": key})

    assert page.code == 400 and 'class="fehler"' in page.text and key not in page.text
    assert not (data_dir / keys.KEY_FILE).exists()


def test_5_a_broken_key_file_counts_as_empty(data_dir):
    data_dir.mkdir()
    (data_dir / keys.KEY_FILE).write_text("{kaputt", encoding="utf-8")
    store = keys.KeyStore(data_dir)

    assert store.read() == {}
    store.set("FREI_KEY", SECRET_KEY)
    assert store.read() == {"FREI_KEY": SECRET_KEY}
    assert [p.name for p in data_dir.iterdir()] == [keys.KEY_FILE]  # keine Reste der Zwischendatei


# --- 6: Budget ---


def test_6_budget_shows_the_month_and_can_be_changed(game_db, data_dir, test_wiki):
    apply.record_model_call(
        game_db, month="2026-10", provider="frei", model="m", input_tokens=1, output_tokens=1, cost_micro_eur=1_250_000, outcome="ok"
    )
    browser = Browser(make_app(data_dir, test_wiki))
    assert "Diesen Monat (UTC): 1,25 € · Warnung ab 10 € · Stopp bei 20 €" in browser.get("/einstellungen").text

    page = browser.post("/einstellungen/budget", {"warn_eur": "0,5", "stop_eur": "1"})

    assert page.headers["Location"] == "/einstellungen?ok=budget"
    assert "Warnung ab 0,5 € · Stopp bei 1 €" in browser.get("/einstellungen").text


def test_6_the_new_limit_stops_the_game_master(game_db, data_dir, test_wiki):
    apply.record_model_call(
        game_db, month="2026-10", provider="frei", model="m", input_tokens=1, output_tokens=1, cost_micro_eur=1_250_000, outcome="ok"
    )
    transport = FakeTransport()
    browser = Browser(make_app(data_dir, test_wiki, transport))
    browser.post("/einstellungen/budget", {"warn_eur": "0.5", "stop_eur": "1"})

    page = browser.say("Ich gehe nach Sturmhagen.")

    assert "Monatsbudget von 1 € erreicht" in page.text and transport.requests == [] and turn_count(game_db) == 0


@pytest.mark.parametrize(("warn", "stop"), [("5", "0"), ("30", "20"), ("viel", "20"), ("inf", "inf"), ("nan", "20"), ("-1", "20")])
def test_6_nonsense_limits_are_refused(game_db, data_dir, test_wiki, warn, stop):
    browser = Browser(make_app(data_dir, test_wiki))

    page = browser.post("/einstellungen/budget", {"warn_eur": warn, "stop_eur": stop})

    assert page.code == 400 and 'class="fehler"' in page.text
    assert not (data_dir / keys.BUDGET_FILE).exists()


# --- 7: Schutz ---


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8000", "", "[::1"])
def test_7_unknown_hostnames_are_refused(game_db, data_dir, test_wiki, host):
    browser = Browser(make_app(data_dir, test_wiki), host=host)

    assert browser.get("/").code == 400


def test_7_configured_hostnames_are_allowed(game_db, data_dir, test_wiki):
    app = make_app(data_dir, test_wiki, hosts=["Synology.tail1234.ts.net", "100.64.0.7"])

    assert Browser(app, "synology.tail1234.ts.net").get(f"/spiel/{PLAYER}").code == 200
    assert Browser(app, "100.64.0.7:8000").get(f"/spiel/{PLAYER}").code == 200
    assert Browser(app, "127.0.0.1:8000").get(f"/spiel/{PLAYER}").code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {"HTTP_ORIGIN": "https://evil.example"},
        {"HTTP_ORIGIN": "null"},
        {"HTTP_ORIGIN": "http://localhost:9999"},
        {"HTTP_ORIGIN": None},
        {"HTTP_ORIGIN": None, "HTTP_SEC_FETCH_SITE": "cross-site"},
    ],
)
def test_7_posts_from_other_origins_are_refused(game_db, data_dir, test_wiki, headers):
    transport = FakeTransport()
    browser = Browser(make_app(data_dir, test_wiki, transport, environ={}))
    form = browser.get(f"/spiel/{PLAYER}")

    for path, data in [
        (f"/spiel/{PLAYER}", {"request_id": form.request_id(), "eingabe": "Los."}),
        ("/einstellungen/schluessel", {"key_env": "FREI_KEY", "schluessel": SECRET_KEY}),
        ("/einstellungen/budget", {"warn_eur": "1", "stop_eur": "1000"}),
        ("/erschaffen", SHEET),
    ]:
        assert browser.post(path, data, headers=headers).code == 403
    assert transport.requests == [] and keys.KeyStore(data_dir).read() == {} and turn_count(game_db) == 0


def test_7_same_origin_without_origin_header_is_allowed(game_db, data_dir, test_wiki):
    browser = Browser(make_app(data_dir, test_wiki, settings=False))

    page = browser.post(
        f"/spiel/{PLAYER}",
        {"request_id": browser.get(f"/spiel/{PLAYER}").request_id(), "eingabe": "status"},
        headers={"HTTP_ORIGIN": None, "HTTP_SEC_FETCH_SITE": "same-origin"},
    )

    assert page.code == 200 and "Geld:" in page.text


def test_7_large_or_foreign_bodies_are_refused(game_db, data_dir, test_wiki):
    browser = Browser(make_app(data_dir, test_wiki))
    form = browser.get(f"/spiel/{PLAYER}")

    big = browser.post(f"/spiel/{PLAYER}", {"request_id": form.request_id(), "eingabe": "x" * web.MAX_BODY})
    json_body = browser.post(f"/spiel/{PLAYER}", {"request_id": form.request_id()}, headers={"CONTENT_TYPE": "application/json"})

    assert (big.code, json_body.code) == (413, 415) and turn_count(game_db) == 0


def test_7_model_text_is_escaped_and_headers_are_strict(game_db, data_dir, test_wiki):
    evil = '<script>alert(1)</script><img src=x onerror="x">'
    transport = FakeTransport(decide(TO_TOWN), tell(evil))
    browser = Browser(make_app(data_dir, test_wiki, transport))

    page = browser.say('Ich sage "<b>Hallo</b>".')

    assert "<script>" not in page.text and "<b>Hallo</b>" not in page.text and "<img" not in page.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page.text
    for response in (page, browser.get("/einstellungen"), browser.get("/stil.css"), browser.get("/gibtsnicht")):
        assert "default-src 'none'" in response.headers["Content-Security-Policy"]
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["Cache-Control"] == "no-store"
    assert "<script" not in browser.get("/erschaffen").text  # Seiten ohne JavaScript


# --- 8: Neustart ---


def test_8_the_game_survives_a_restart_of_the_server(game_db, data_dir, test_wiki):
    transport = FakeTransport(decide(TO_TOWN), tell("Du trittst hinaus."), decide(ATTEMPT), tell("Du stemmst dich dagegen."))
    browser = Browser(make_app(data_dir, test_wiki, transport))
    browser.say("Ich gehe nach Sturmhagen.")
    browser.say("Ich breche die Tür auf.")

    restarted = Browser(make_app(data_dir, test_wiki))
    page = restarted.get(f"/spiel/{PLAYER}")

    assert "Du trittst hinaus." in page.text and "Du stemmst dich dagegen." in page.text
    assert 'name="wurf"' in page.text  # die offene Probe ist wieder da
