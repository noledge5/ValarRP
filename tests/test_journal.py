"""Schritt 4a: Protokoll und Melde-Knopf, ohne Netz und ohne Browser (Entscheidungen 200, 204 und 205 bis 209).

Die Nummern folgen dem README. Die Anwendung wird direkt als WSGI aufgerufen; das Modell ist eine Attrappe.
Testdaten wie in den Tests der Weboberfläche.
"""

import json
import sqlite3
import stat
from datetime import timedelta
from pathlib import Path

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
    dump_state,
    write_wiki,
)
from tests.test_web import CONTENT, NOW, SETTINGS, TO_TOWN, Browser, decide, make_app, tell
from valar import apply, db, journal, llm, web, wiki
from valar.content import GUILD_HALL

KEY = "sk-geheim-ABCDEFGH-9876"
LATER = NOW + timedelta(minutes=10)


@pytest.fixture
def test_wiki(tmp_path: Path) -> wiki.Wiki:
    write_wiki(tmp_path / "w", TEST_WIKI)
    return wiki.load(tmp_path / "w")


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "daten"


@pytest.fixture
def game_db(data_dir: Path):
    data_dir.mkdir()
    conn = db.connect(data_dir / web.DB_FILE)
    db.init_schema(conn)
    add_campaign(conn, CAMPAIGN)
    add_player(conn, PLAYER, location_id=GUILD_HALL, attributes={"STR": 14, "GES": 8}, skills={SKILL: 30})
    yield conn
    conn.close()


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self):
        return self.now


def clocked_app(data_dir: Path, test_wiki: wiki.Wiki, transport: FakeTransport, clock: Clock) -> web.App:
    data_dir.mkdir(exist_ok=True)
    (data_dir / web.SETTINGS_FILE).write_text(SETTINGS, encoding="utf-8")
    return web.App(
        data_dir,
        campaign_id=CAMPAIGN,
        content=CONTENT,
        wiki=test_wiki,
        transport=transport,
        environ={"FREI_KEY": KEY},
        now=clock,
    )


def rows(data_dir: Path, table: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(data_dir / journal.JOURNAL_FILE)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
    finally:
        conn.close()


def exchange_ids(conn: sqlite3.Connection) -> list[str]:
    return [row[0] for row in conn.execute("SELECT request_id FROM exchange ORDER BY exchange_id")]


def report(browser: Browser, turn: str, kind: str = "unlogisch", note: str = "Die Wache war eben noch tot.", **kwargs):
    form = {"request_id": web.new_request_id(), "zug": turn, "was": kind, "satz": note}
    return browser.post(f"/spiel/{PLAYER}/melden", form, **kwargs)


def client_error(browser: Browser, **changes):
    form = {
        "request_id": web.new_request_id(),
        "art": "skript",
        "meldung": "ReferenceError: x is not defined",
        "seite": f"/spiel/{PLAYER}",
        "quelle": "http://localhost:8000/app.js?v=1#oben",
        "zeile": "12",
        "spalte": "5",
        "stapel": "initPlay@http://localhost:8000/app.js:12:5",
    } | changes
    return browser.post("/fehler", form, headers={"HTTP_USER_AGENT": "Mozilla/5.0 (iPhone) Safari/605.1"})


def export(browser: Browser, request_id: str | None = None):
    return browser.post("/einstellungen/export", {"request_id": request_id or web.new_request_id()})


def client(tmp_path: Path, *replies, providers: int = 1) -> tuple[llm.ModelClient, journal.Journal, sqlite3.Connection]:
    """Ein Modell-Client mit Protokoll, ohne Webseite."""
    conn = db.connect(tmp_path / "spiel.db")
    db.init_schema(conn)
    log = journal.Journal(tmp_path / journal.JOURNAL_FILE, now=lambda: NOW)
    settings = llm.ModelSettings(provider=[
        llm.ProviderSettings(name=f"p{n}", base_url=f"https://p{n}.example/v1", model=f"m{n}", key_env=f"P{n}_KEY")
        for n in range(providers)
    ])
    environ = {f"P{n}_KEY": KEY for n in range(providers)}
    model = llm.ModelClient(
        settings, llm.Ledger(conn), transport=FakeTransport(*replies), environ=environ, now=lambda: NOW,
        call_log=log.calls(player_id=PLAYER, request_id="web-zug"),
    )
    return model, log, conn



# --- 1: Je Modellaufruf ein Eintrag ---


def test_1_each_model_call_is_logged_with_turn_prompt_reply_and_duration(game_db, data_dir, test_wiki):
    transport = FakeTransport(decide(TO_TOWN), tell("Du trittst hinaus in den Schlamm."))
    browser = Browser(make_app(data_dir, test_wiki, transport))
    form = browser.get(f"/spiel/{PLAYER}")

    browser.say("Ich gehe hinaus.", form)

    calls = rows(data_dir, "model_call")
    assert [c["purpose"] for c in calls] == ["aktion", "erzaehlung"]
    for call in calls:
        assert (call["player_id"], call["request_id"]) == (PLAYER, form.request_id())
        assert (call["provider"], call["model"], call["attempt"], call["outcome"], call["http_status"]) == ("frei", "m", 1, "ok", 200)
        assert call["duration_ms"] >= 0 and call["error"] is None
        assert (call["input_tokens"], call["output_tokens"]) == (100, 10)
    sent = json.loads(calls[0]["messages"])
    assert sent == transport.requests[0]["body"]["messages"]  # der ganze Prompt, wie er hinausging
    assert "Ich gehe hinaus." in sent[-1]["content"]
    assert json.loads(calls[0]["reply"]) == {"action": TO_TOWN}
    assert "Du trittst hinaus in den Schlamm." in calls[1]["reply"]


def test_1_an_invalid_reply_and_its_retry_are_two_entries(tmp_path):
    model, _, _ = client(tmp_path, chat("kein JSON"), chat('{"type": "sleep"}'))

    model.request([{"role": "user", "content": "Ich schlafe."}], llm.TypeAdapter(dict), schema_name="aktion")

    first, second = rows(tmp_path, "model_call")
    assert (first["attempt"], first["outcome"], first["reply"]) == (1, "invalid", "kein JSON")
    assert first["error"] and "Antwort" in first["error"]  # was am Schema nicht passte
    assert (second["attempt"], second["outcome"]) == (2, "ok")
    assert len(json.loads(second["messages"])) == 3  # mit der Korrektur


@pytest.mark.parametrize(
    ("reply", "outcome", "status", "error"),
    [
        (llm.TransportError("TimeoutError"), "error", None, "keine Antwort (TimeoutError)"),
        (llm.HttpResponse(500, b'{"error": "upstream kaputt"}'), "error", 500, '{"error": "upstream kaputt"}'),
        (llm.HttpResponse(400, b"x" * 2000), "error", 400, "x" * llm.MAX_LOGGED_ERROR),
    ],
)
def test_1_failures_are_logged_with_the_error(tmp_path, reply, outcome, status, error):
    model, _, _ = client(tmp_path, reply)

    with pytest.raises(llm.ModelError):
        model.request([{"role": "user", "content": "Hallo"}], llm.TypeAdapter(dict))

    (call,) = rows(tmp_path, "model_call")
    assert (call["outcome"], call["http_status"], call["error"], call["reply"]) == (outcome, status, error, None)


def test_1_exhausted_quota_is_logged_before_the_next_provider(tmp_path):
    model, _, _ = client(tmp_path, llm.HttpResponse(429, b"slow down"), chat("{}"), providers=2)

    model.request([{"role": "user", "content": "Hallo"}], llm.TypeAdapter(dict))

    calls = rows(tmp_path, "model_call")
    assert [(c["provider"], c["outcome"], c["http_status"]) for c in calls] == [("p0", "quota", 429), ("p1", "ok", 200)]


# --- 2: Nie ein Schlüssel ---


def test_2_no_key_in_the_journal_or_the_export(tmp_path):
    echoes = [
        llm.TransportError(f"Verbindung mit {KEY} abgebrochen"),
        llm.HttpResponse(400, f'{{"error": "bad request for key {KEY}"}}'.encode()),
        llm.HttpResponse(401, f'{{"error": "Incorrect API key provided: {KEY}"}}'.encode()),
        chat(f"Mein Schlüssel ist {KEY}"),
        chat(f'{{"echo": "{KEY}"}}'),
    ]
    model, log, _ = client(tmp_path, *echoes)
    for _ in range(4):
        try:
            model.request([{"role": "user", "content": f"Hallo {KEY}"}], llm.TypeAdapter(dict))
        except llm.ModelError:
            pass

    calls = rows(tmp_path, "model_call")
    assert [c["outcome"] for c in calls] == ["error", "error", "error", "invalid", "ok"]
    assert calls[2]["http_status"] == 401 and calls[2]["error"] is None  # bei 401/403 bleibt nur der Code
    assert KEY.encode() not in (tmp_path / journal.JOURNAL_FILE).read_bytes()
    assert KEY.encode() not in log.export("web-" + "e" * 32).body
    assert "***" in calls[1]["error"] and "***" in calls[3]["reply"]


def test_2_the_journal_never_sees_the_authorization_header(game_db, data_dir, test_wiki):
    transport = FakeTransport(decide(TO_TOWN), tell())
    browser = Browser(clocked_app(data_dir, test_wiki, transport, Clock()))

    browser.say("Ich gehe hinaus.")

    assert transport.requests[0]["headers"]["Authorization"] == f"Bearer {KEY}"
    assert KEY.encode() not in (data_dir / journal.JOURNAL_FILE).read_bytes()


# --- 3: 30 Tage, Sitzungen ---


def test_3_entries_older_than_30_days_are_deleted(tmp_path):
    clock = Clock()
    log = journal.Journal(tmp_path / journal.JOURNAL_FILE, now=clock)
    call = dict(purpose="aktion", provider="p", model="m", attempt=1, duration_ms=5, outcome="ok", input_tokens=1, output_tokens=1, messages="[]")
    log.record_call(player_id=PLAYER, request_id="web-alt", **call)
    clock.now = NOW + timedelta(days=29)
    log.record_call(player_id=PLAYER, request_id="web-neu", **call)
    log.client_error(request_id="web-" + "a" * 32, kind="skript", message="m", page="/", source="", line=None, col=None, stack="", agent="")

    clock.now = NOW + journal.KEEP + timedelta(seconds=1)
    journal.Journal(tmp_path / journal.JOURNAL_FILE, now=clock)  # beim Start wird aufgeräumt

    assert [c["request_id"] for c in rows(tmp_path, "model_call")] == ["web-neu"]
    assert len(rows(tmp_path, "client_error")) == 1
    assert len(rows(tmp_path, "session")) == 1  # die alte Sitzung ist mit weg


def test_3_a_new_session_also_clears_old_entries(tmp_path):
    clock = Clock()
    log = journal.Journal(tmp_path / journal.JOURNAL_FILE, now=clock)
    log.client_error(request_id="web-" + "a" * 32, kind="skript", message="m", page="/", source="", line=None, col=None, stack="", agent="")

    clock.now += journal.KEEP + timedelta(hours=1)
    log.touch()  # der Server lief durch, ohne Neustart

    assert rows(tmp_path, "client_error") == [] and [s["session_no"] for s in rows(tmp_path, "session")] == [2]


def test_3_only_the_server_may_read_the_journal(tmp_path):
    journal.Journal(tmp_path / journal.JOURNAL_FILE)

    assert stat.S_IMODE((tmp_path / journal.JOURNAL_FILE).stat().st_mode) == 0o600


def test_3_a_pause_over_30_minutes_starts_a_new_session(tmp_path):
    clock = Clock()
    log = journal.Journal(tmp_path / journal.JOURNAL_FILE, now=clock)

    first = log.touch()
    clock.now += timedelta(minutes=30)
    same = log.touch()
    clock.now += timedelta(minutes=30, seconds=1)
    new = log.touch()

    assert (first, same, new) == (1, 1, 2)
    started, ended = rows(tmp_path, "session")[0]["started_at"], rows(tmp_path, "session")[0]["last_at"]
    assert (started, ended) == ("2026-10-22T00:00:00Z", "2026-10-22T00:30:00Z")


def test_3_playing_in_the_browser_counts_for_the_session(game_db, data_dir, test_wiki):
    clock = Clock()
    narrate = {"type": "narrate", "text": "Es regnet.", "notes": []}
    transport = FakeTransport(decide(narrate), decide(narrate), decide(narrate))
    browser = Browser(clocked_app(data_dir, test_wiki, transport, clock))

    browser.say("Ich warte.")
    clock.now += timedelta(minutes=25)
    browser.say("Ich warte weiter.")
    clock.now += timedelta(minutes=45)
    browser.say("Ich warte noch immer.")

    assert [c["session_no"] for c in rows(data_dir, "model_call")] == [1, 1, 2]


# --- 4: Melde-Knopf an jedem Zug ---


def test_4_every_turn_has_a_report_button(game_db, data_dir, test_wiki):
    narrate = {"type": "narrate", "text": "Es regnet.", "notes": []}
    browser = Browser(make_app(data_dir, test_wiki, FakeTransport(decide(narrate), decide(TO_TOWN), tell())))
    browser.say("Ich warte.")
    browser.say("Ich gehe.")

    page = browser.get(f"/spiel/{PLAYER}")

    assert page.text.count('<details class="melden">') == 2
    for turn in exchange_ids(game_db):
        assert f'name="zug" value="{turn}"' in page.text
    ids = [line for line in page.text.split('name="request_id" value="')[1:]]
    assert len({i[:36] for i in ids}) == len(ids)  # jedes Formular eine eigene request_id
    for kind, label in journal.REPORT_KINDS.items():
        assert f'name="was" value="{kind}"' in page.text and label in page.text


def test_4_a_report_stores_kind_sentence_and_a_copy_of_the_turn(game_db, data_dir, test_wiki):
    browser = Browser(make_app(data_dir, test_wiki, FakeTransport(decide(TO_TOWN), tell("Du trittst hinaus."))))
    browser.say("Ich gehe hinaus.")
    (turn,) = exchange_ids(game_db)
    before = dump_state(game_db)

    page = report(browser, turn, "unlogisch", "Die Wache war\x1b[31m eben noch tot.")

    assert (page.code, page.headers["Location"]) == (303, f"/spiel/{PLAYER}?ok=gemeldet")
    (row,) = rows(data_dir, "report")
    assert (row["player_id"], row["turn"], row["kind"], row["note"]) == (PLAYER, turn, "unlogisch", "Die Wache war[31m eben noch tot.")
    assert (row["player_input"], row["narration"]) == ("Ich gehe hinaus.", "Du trittst hinaus.")
    assert row["session_no"] == 1
    shown = browser.get(f"/spiel/{PLAYER}", "ok=gemeldet")
    assert "Danke, gemeldet." in shown.text and '<summary class="gemeldet">Gemeldet: Unlogisch</summary>' in shown.text
    assert dump_state(game_db) == before  # Melden ändert keinen Spielstand


def test_4_with_the_script_without_reloading(game_db, data_dir, test_wiki):
    browser = Browser(make_app(data_dir, test_wiki, FakeTransport(decide(TO_TOWN), tell())))
    browser.say("Ich gehe.")
    (turn,) = exchange_ids(game_db)

    page = report(browser, turn, "toll", "", headers={"HTTP_ACCEPT": "application/json"})

    answer = json.loads(page.text)
    assert page.code == 200 and answer["hinweis"] == "Danke, gemeldet."
    assert "Gemeldet: Toll" in answer["melden"] and f'value="{turn}"' in answer["melden"]
    assert rows(data_dir, "report")[0]["note"] == ""  # der Satz ist freiwillig


def test_4_older_turns_also_have_the_button(game_db, data_dir, test_wiki):
    narrate = {"type": "narrate", "text": "Es regnet.", "notes": []}
    browser = Browser(make_app(data_dir, test_wiki, FakeTransport(*[decide(narrate)] * 22)))
    for number in range(22):
        browser.say(f"Ich warte {number}.")
    oldest = game_db.execute("SELECT MIN(exchange_id) + 2 FROM exchange").fetchone()[0]

    page = browser.get(f"/spiel/{PLAYER}/verlauf", f"vor={oldest}")

    assert json.loads(page.text)["verlauf"].count('class="melden"') == 2


def test_4_the_same_form_twice_counts_once_other_content_is_refused(game_db, data_dir, test_wiki):
    browser = Browser(make_app(data_dir, test_wiki, FakeTransport(decide(TO_TOWN), tell())))
    browser.say("Ich gehe.")
    (turn,) = exchange_ids(game_db)
    form = {"request_id": web.new_request_id(), "zug": turn, "was": "zu_hart", "satz": "Zu teuer."}

    first = browser.post(f"/spiel/{PLAYER}/melden", form)
    again = browser.post(f"/spiel/{PLAYER}/melden", form)
    other = browser.post(f"/spiel/{PLAYER}/melden", form | {"was": "zu_leicht"})

    assert (first.code, again.code, other.code) == (303, 303, 409)
    assert [r["kind"] for r in rows(data_dir, "report")] == ["zu_hart"]


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"request_id": ""}, 400),
        ({"request_id": "web-kurz"}, 400),
        ({"was": "super"}, 400),
        ({"was": ""}, 400),
        ({"satz": "x" * (journal.MAX_NOTE + 1)}, 400),
        ({"zug": "web-" + "0" * 32}, 404),
    ],
)
def test_4_a_wrong_report_is_refused_and_nothing_is_stored(game_db, data_dir, test_wiki, changes, code):
    browser = Browser(make_app(data_dir, test_wiki, FakeTransport(decide(TO_TOWN), tell())))
    browser.say("Ich gehe.")
    (turn,) = exchange_ids(game_db)
    form = {"request_id": web.new_request_id(), "zug": turn, "was": "fehler", "satz": "Kaputt."} | changes

    page = browser.post(f"/spiel/{PLAYER}/melden", form)

    assert page.code == code and rows(data_dir, "report") == []


def test_4_a_turn_of_another_character_cannot_be_reported(game_db, data_dir, test_wiki):
    add_player(game_db, "andere", location_id=GUILD_HALL)
    browser = Browser(make_app(data_dir, test_wiki, FakeTransport(decide(TO_TOWN), tell())))
    browser.say("Ich gehe.")
    (turn,) = exchange_ids(game_db)

    page = browser.post("/spiel/andere/melden", {"request_id": web.new_request_id(), "zug": turn, "was": "fehler", "satz": ""})

    assert page.code == 404 and rows(data_dir, "report") == []


# --- 5: Fehler aus dem Browser ---


def test_5_the_page_reports_a_script_error(game_db, data_dir, test_wiki):
    browser = Browser(make_app(data_dir, test_wiki))

    page = client_error(browser)

    assert (page.code, json.loads(page.text)) == (200, {"ok": True, "neu": True})
    (row,) = rows(data_dir, "client_error")
    assert (row["kind"], row["message"], row["page"]) == ("skript", "ReferenceError: x is not defined", f"/spiel/{PLAYER}")
    assert (row["source"], row["line"], row["col"]) == ("http://localhost:8000/app.js", 12, 5)  # ohne ?-Teil
    assert row["agent"] == "Mozilla/5.0 (iPhone) Safari/605.1" and "initPlay" in row["stack"]


def test_5_a_connection_loss_sent_twice_counts_once(game_db, data_dir, test_wiki):
    browser = Browser(make_app(data_dir, test_wiki))
    form = {"request_id": web.new_request_id(), "art": "verbindung", "meldung": "Keine Verbindung zum Server"}

    first = browser.post("/fehler", form)
    again = browser.post("/fehler", form)

    assert (json.loads(first.text)["neu"], json.loads(again.text)["neu"]) == (True, False)
    assert [r["kind"] for r in rows(data_dir, "client_error")] == ["verbindung"]


def test_5_text_is_cut_and_cleaned(game_db, data_dir, test_wiki):
    browser = Browser(make_app(data_dir, test_wiki))

    client_error(browser, meldung="\x1b[2J" + "m" * 900, stapel="s" * 5000, zeile="999999999999", quelle="q" * 900)

    (row,) = rows(data_dir, "client_error")
    assert row["message"] == "[2J" + "m" * (journal.MAX_MESSAGE - 3)
    assert len(row["stack"]) == journal.MAX_STACK and len(row["source"]) == journal.MAX_SOURCE
    assert row["line"] is None


@pytest.mark.parametrize("changes", [{"art": "boese"}, {"request_id": "web-kurz"}, {"request_id": ""}])
def test_5_a_wrong_error_report_is_refused(game_db, data_dir, test_wiki, changes):
    browser = Browser(make_app(data_dir, test_wiki))

    page = client_error(browser, **changes)

    assert page.code == 400 and "fehler" in json.loads(page.text) and rows(data_dir, "client_error") == []


def test_5_at_most_100_errors_an_hour(tmp_path):
    clock = Clock()
    log = journal.Journal(tmp_path / journal.JOURNAL_FILE, now=clock)

    def send(number: int) -> bool:
        return log.client_error(
            request_id=f"web-{number:032x}", kind="skript", message="m", page="/", source="", line=None, col=None, stack="", agent=""
        )

    taken = [send(n) for n in range(journal.MAX_CLIENT_ERRORS_PER_HOUR + 5)]
    clock.now += timedelta(hours=1, seconds=1)

    assert taken.count(True) == journal.MAX_CLIENT_ERRORS_PER_HOUR and send(1000)


# --- 6: Export ---


def test_6_the_export_brings_everything_since_the_last_one(game_db, data_dir, test_wiki):
    narrate = {"type": "narrate", "text": "Es regnet.", "notes": []}
    browser = Browser(make_app(data_dir, test_wiki, FakeTransport(decide(TO_TOWN), tell("Schlamm."), decide(narrate))))
    browser.say("Ich gehe.")
    (turn,) = exchange_ids(game_db)
    report(browser, turn, "zu_leicht", "Zu einfach.")
    client_error(browser)
    settings = browser.get("/einstellungen")
    assert "2 Modellaufrufe, 1 Meldung, 1 Fehler im Browser, aus 1 Sitzung" in settings.text and "Noch kein Export." in settings.text

    page = export(browser)

    assert page.code == 200 and page.headers["Content-Type"].startswith("application/json")
    assert page.headers["Content-Disposition"] == 'attachment; filename="valar-protokoll-2026-10-22-1.json"'
    data = json.loads(page.text)
    assert (data["format"], data["version"], data["export"], data["keep_days"]) == ("valar-protokoll", 1, 1, 30)
    assert [c["purpose"] for c in data["calls"]] == ["aktion", "erzaehlung"]
    assert data["calls"][0]["turn"] == turn and isinstance(data["calls"][0]["messages"], list)
    assert [(r["kind"], r["note"], r["player_input"], r["narration"]) for r in data["reports"]] == [
        ("zu_leicht", "Zu einfach.", "Ich gehe.", "Schlamm.")
    ]
    assert [e["kind"] for e in data["client_errors"]] == ["skript"]
    assert [s["session"] for s in data["sessions"]] == [1]

    browser.say("Ich warte.")
    later = json.loads(export(browser).text)

    assert later["export"] == 2 and len(later["calls"]) == 1 and later["reports"] == later["client_errors"] == []
    assert "0 Modellaufrufe, 0 Meldungen" in browser.get("/einstellungen").text  # nach dem Export steht wieder 0 da
    assert "Letzter Export: Nr. 2" in browser.get("/einstellungen").text


def test_6_the_same_form_brings_the_same_file_and_an_old_export_comes_again(game_db, data_dir, test_wiki):
    browser = Browser(make_app(data_dir, test_wiki, FakeTransport(decide(TO_TOWN), tell())))
    browser.say("Ich gehe.")
    form_id = web.new_request_id()

    first = export(browser, form_id)
    twice = export(browser, form_id)
    again = browser.get("/einstellungen/export/1")

    assert first.text == twice.text == again.text and json.loads(first.text)["export"] == 1
    assert len(rows(data_dir, "export")) == 1
    assert browser.get("/einstellungen/export/7").code == 404


def test_6_an_export_needs_a_valid_form(game_db, data_dir, test_wiki):
    browser = Browser(make_app(data_dir, test_wiki))

    page = browser.post("/einstellungen/export", {"request_id": "web-kurz"})

    assert page.code == 400 and "request_id" in page.text and rows(data_dir, "export") == []


# --- 7: Schutz wie bisher ---


@pytest.mark.parametrize("path", ["/fehler", f"/spiel/{PLAYER}/melden", "/einstellungen/export"])
def test_7_foreign_origin_too_big_or_no_form_is_refused(game_db, data_dir, test_wiki, path):
    browser = Browser(make_app(data_dir, test_wiki))
    form = {"request_id": web.new_request_id(), "art": "skript", "was": "fehler", "zug": "x", "meldung": "m"}

    foreign = browser.post(path, form, headers={"HTTP_ORIGIN": "https://evil.example"})
    big = browser.post(path, form | {"meldung": "x" * web.MAX_BODY})
    as_json = browser.post(path, form, headers={"CONTENT_TYPE": "application/json"})
    stranger = Browser(browser.app, host="evil.example").post(path, form)

    assert (foreign.code, big.code, as_json.code, stranger.code) == (403, 413, 415, 400)
    assert rows(data_dir, "client_error") == rows(data_dir, "report") == rows(data_dir, "export") == []


def test_7_every_text_in_the_button_is_escaped(game_db, data_dir, test_wiki):
    nasty = 'cli-"><script>alert(1)</script>'
    game_db.execute("BEGIN")
    apply.record_exchange(
        game_db, campaign_id=CAMPAIGN, request_id=nasty, player_id=PLAYER, player_input="Hallo", turn_request_id=None,
        narration="Hallo zurück.", chronicle=None,
    )
    game_db.execute("COMMIT")
    browser = Browser(make_app(data_dir, test_wiki))

    page = browser.get(f"/spiel/{PLAYER}")

    assert "<script>alert" not in page.text and 'value="cli-&quot;&gt;&lt;script&gt;' in page.text
    assert report(browser, nasty).code == 303  # auch ein solcher Zug lässt sich melden


def test_7_the_export_is_a_file_not_a_page(game_db, data_dir, test_wiki):
    browser = Browser(make_app(data_dir, test_wiki, FakeTransport(decide(TO_TOWN), tell("<img src=x onerror=alert(1)>"))))
    browser.say("Ich gehe.")

    page = export(browser)

    assert page.headers["Content-Disposition"].startswith("attachment;")
    assert page.headers["X-Content-Type-Options"] == "nosniff" and "script-src 'self'" in page.headers["Content-Security-Policy"]


# --- 8: Das Protokoll ist kein Spielstand ---


def test_8_the_journal_is_its_own_file_and_the_game_keeps_its_schema(game_db, data_dir, test_wiki):
    fresh = db.connect(":memory:")
    db.init_schema(fresh)
    tables = {r[0] for r in fresh.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    browser = Browser(make_app(data_dir, test_wiki, FakeTransport(decide(TO_TOWN), tell())))

    browser.say("Ich gehe.")
    report(browser, exchange_ids(game_db)[0])
    client_error(browser)

    assert game_db.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 12  # Paket 3 ohne Migration (Entscheidung 227)
    assert {r[0] for r in game_db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")} == tables
    with sqlite3.connect(data_dir / journal.JOURNAL_FILE) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == journal.SCHEMA_VERSION


def test_8_a_broken_journal_does_not_stop_the_game(game_db, data_dir, test_wiki):
    (data_dir / journal.JOURNAL_FILE).write_bytes(b"kein SQLite " * 100)
    browser = Browser(make_app(data_dir, test_wiki, FakeTransport(decide(TO_TOWN), tell("Du gehst."))))

    played = browser.say("Ich gehe.")

    assert played.code == 200 and "Du gehst." in played.text
    assert "protokoll.db ist nicht nutzbar" in browser.get("/einstellungen").text
    assert report(browser, exchange_ids(game_db)[0]).code == 503
    assert client_error(browser).code == 503
    assert export(browser).code == 503


def test_8_a_failing_journal_write_does_not_break_the_turn(game_db, data_dir, test_wiki, monkeypatch):
    app = make_app(data_dir, test_wiki, FakeTransport(decide(TO_TOWN), tell("Du gehst.")))

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(app.journal, "_insert", broken)
    monkeypatch.setattr(app.journal, "_connect", broken)

    page = Browser(app).say("Ich gehe.")

    assert page.code == 200 and "Du gehst." in page.text
