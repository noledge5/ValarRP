"""Schritt 4b: Überlastung, Ping und Version (Entscheidungen 210 bis 213), ohne Netz.

Die Nummern folgen dem README. Das Netz ist eine Attrappe; Testdaten wie in den Tests der Weboberfläche.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from tests.helpers import CAMPAIGN, PLAYER, SKILL, TEST_WIKI, FakeTransport, add_campaign, add_player, chat, write_wiki
from tests.test_web import NOW, Browser, decide, make_app, tell, TO_TOWN
from valar import db, journal, keys, llm, web, wiki
from valar.content import GUILD_HALL

KEY = "sk-geheim-ABCDEFGH-9876"
ROOT = Path(__file__).parents[1]


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


def provider(name: str = "frei", model: str = "m") -> llm.ProviderSettings:
    return llm.ProviderSettings(name=name, base_url=f"https://{name}.example/v1", model=model, key_env=f"{name.upper()}_KEY")


def models(*names: str) -> llm.HttpResponse:
    return llm.HttpResponse(200, json.dumps({"object": "list", "data": [{"id": n} for n in names]}).encode())


def calls(data_dir: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(data_dir / journal.JOURNAL_FILE)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM model_call ORDER BY call_id").fetchall()
    finally:
        conn.close()


class Log:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def record(self, **call) -> None:
        self.calls.append(call)


# --- 1: Überlastung führt zum nächsten Anbieter ---


def two_providers(transport: FakeTransport, log: Log | None = None) -> llm.ModelClient:
    conn = db.connect(":memory:")
    db.init_schema(conn)
    settings = llm.ModelSettings(provider=[provider("frei"), provider("reserve")])
    return llm.ModelClient(
        settings, llm.Ledger(conn), transport=transport, environ={"FREI_KEY": KEY, "RESERVE_KEY": KEY}, call_log=log
    )


def test_1_an_overloaded_provider_hands_over_to_the_next():
    log = Log()
    transport = FakeTransport(llm.HttpResponse(503, b'{"error": {"status": "UNAVAILABLE"}}'), chat('{"ok": true}'))

    reply = two_providers(transport, log).request([{"role": "user", "content": "Hallo"}], llm.TypeAdapter(dict))

    assert (reply.provider, reply.value) == ("reserve", {"ok": True})
    assert [(c["provider"], c["outcome"], c["http_status"]) for c in log.calls] == [("frei", "error", 503), ("reserve", "ok", 200)]
    assert "UNAVAILABLE" in log.calls[0]["error"]  # im Protokoll steht, warum übersprungen wurde


def test_1_no_credit_hands_over_to_the_next():
    log = Log()
    transport = FakeTransport(llm.HttpResponse(402, b'{"error": "billing"}'), chat('{"ok": true}'))

    reply = two_providers(transport, log).request([{"role": "user", "content": "Hallo"}], llm.TypeAdapter(dict))

    assert reply.provider == "reserve" and [(c["provider"], c["http_status"]) for c in log.calls] == [("frei", 402), ("reserve", 200)]


def test_1_all_overloaded_is_an_error():
    transport = FakeTransport(llm.HttpResponse(503, b"{}"), llm.HttpResponse(429, b"{}"))

    with pytest.raises(llm.ModelError) as error:
        two_providers(transport).request([{"role": "user", "content": "Hallo"}], llm.TypeAdapter(dict))

    assert error.value.code == "no_provider" and "Überlastung" in str(error.value)


@pytest.mark.parametrize("reply", [llm.TransportError("TimeoutError"), llm.HttpResponse(502, b"{}"), llm.HttpResponse(504, b"{}")])
def test_1_no_answer_or_a_gateway_error_hands_over_and_rests(reply):
    """Entscheidung 223 (ändert 193 und 210): wer gerade nicht kann, ruht; der nächste ist sofort dran."""
    transport = FakeTransport(reply, chat('{"ok": true}'))

    result = two_providers(transport).request([{"role": "user", "content": "Hallo"}], llm.TypeAdapter(dict))

    assert result.provider == "reserve" and set(llm.RESTING) == {"frei"}


@pytest.mark.parametrize("reply", [llm.HttpResponse(500, b"{}"), llm.HttpResponse(401, b"{}")])
def test_1_a_real_error_still_stops_the_chain(reply):
    transport = FakeTransport(reply, chat('{"ok": true}'))

    with pytest.raises(llm.ModelError):
        two_providers(transport).request([{"role": "user", "content": "Hallo"}], llm.TypeAdapter(dict))

    assert len(transport.requests) == 1 and llm.RESTING == {}


# --- 2: Ping: Modellliste, dann ein Mini-Aufruf ---

OK_CALL = chat("OK", prompt_tokens=7, completion_tokens=1)


@pytest.mark.parametrize(
    ("replies", "ok", "text"),
    [
        ((models("m", "anderes"), OK_CALL), True, "erreichbar, Schlüssel gilt, Modell m antwortet"),
        ((models("models/m"), OK_CALL), True, "Modell m antwortet"),  # Gemini nennt Modelle mit Vorsatz
        ((llm.HttpResponse(200, b"kein JSON"), OK_CALL), True, "Modell m antwortet"),
        ((models("m"), llm.HttpResponse(402, b'{"error": "billing"}')), False, "kein Guthaben (Fehler 402)"),
        ((models("m"), llm.HttpResponse(429, b"{}")), False, "Kontingent ist gerade erschöpft (Fehler 429)"),
        ((models("m"), llm.HttpResponse(503, b"{}")), False, "Modell m ist überlastet (Fehler 503)"),
        ((models("m"), llm.HttpResponse(403, b"{}")), False, "für Aufrufe gesperrt (Fehler 403)"),
        ((models("m"), llm.HttpResponse(500, b"{}")), False, "der Aufruf scheitert (Fehler 500)"),
        ((models("m"), llm.TransportError("TimeoutError")), False, "antwortet nicht in 20 s"),
    ],
)
def test_2_after_the_list_a_tiny_call_shows_whether_the_model_answers(replies, ok, text):
    transport = FakeTransport(*replies)
    log = Log()

    result = llm.ping(provider(), {"FREI_KEY": KEY}, transport=transport, call_log=log)

    assert (result.ok, text in result.text) == (ok, True), result.text
    listing, call = transport.requests
    assert (listing["url"], listing["body"], listing["timeout"]) == ("https://frei.example/v1/models", None, llm.PING_TIMEOUT_S)
    assert call["url"] == "https://frei.example/v1/chat/completions" and call["timeout"] == llm.PING_CALL_TIMEOUT_S
    assert call["body"] == {"model": "m", "messages": llm.PING_MESSAGES, "max_tokens": llm.PING_MAX_TOKENS}
    assert [c["purpose"] for c in log.calls] == ["ping", "ping-aufruf"]
    assert log.calls[1]["outcome"] == ("ok" if ok else ("quota" if "429" in text else "error"))
    assert KEY not in json.dumps(log.calls) and KEY not in result.text


@pytest.mark.parametrize(
    ("reply", "text"),
    [
        (models("anderes"), "kennt der Anbieter nicht"),
        (llm.HttpResponse(401, f'{{"error": "bad key {KEY}"}}'.encode()), "Schlüssel ungültig"),
        (llm.HttpResponse(403, b"{}"), "Schlüssel ungültig oder ohne Rechte (Fehler 403)"),
        (llm.HttpResponse(429, b"{}"), "Kontingent ist gerade erschöpft"),
        (llm.HttpResponse(503, b"{}"), "überlastet (Fehler 503)"),
        (llm.HttpResponse(500, b"{}"), "Fehler 500"),
        (llm.TransportError("TimeoutError"), "nicht erreichbar (TimeoutError)"),
    ],
)
def test_2_when_the_list_already_fails_no_call_follows(reply, text):
    transport = FakeTransport(reply)
    log = Log()

    result = llm.ping(provider(), {"FREI_KEY": KEY}, transport=transport, call_log=log)

    assert not result.ok and text in result.text and len(transport.requests) == 1
    assert [c["purpose"] for c in log.calls] == ["ping"] and KEY not in json.dumps(log.calls)


def test_2_a_green_check_ends_the_rest():
    llm.RESTING.update({"frei": 9e9, "reserve": 9e9})
    llm.ping(provider(), {"FREI_KEY": KEY}, transport=FakeTransport(models("m"), OK_CALL))
    assert llm.RESTING == {"reserve": 9e9}


def test_2_without_a_key_nothing_goes_out():
    transport = FakeTransport()

    result = llm.ping(provider(), {}, transport=transport)

    assert (result.ok, result.text, transport.requests) == (False, "kein Schlüssel", [])


def test_2_the_tiny_call_counts_into_the_budget():
    conn = db.connect(":memory:")
    db.init_schema(conn)
    paid = provider().model_copy(update={"input_eur_per_million": 1_000_000, "output_eur_per_million": 1_000_000})

    llm.ping(paid, {"FREI_KEY": KEY}, transport=FakeTransport(models("m"), OK_CALL), ledger=llm.Ledger(conn),
             budget=llm.BudgetSettings(), now=lambda: NOW)

    assert llm.Ledger(conn).spent_micro_eur("2026-10") == 8_000_000  # 7 + 1 Tokens zu je 1 €


def test_2_at_the_budget_stop_there_is_no_tiny_call():
    conn = db.connect(":memory:")
    db.init_schema(conn)
    paid = provider().model_copy(update={"input_eur_per_million": 1_000_000})
    llm.Ledger(conn).record(month="2026-10", provider=paid, input_tokens=20, output_tokens=0, outcome="ok")
    transport = FakeTransport(models("m"))

    result = llm.ping(paid, {"FREI_KEY": KEY}, transport=transport, ledger=llm.Ledger(conn),
                      budget=llm.BudgetSettings(), now=lambda: NOW)

    assert not result.ok and "Monatsbudget ist erreicht" in result.text and len(transport.requests) == 1


# --- 3: Ping in den Einstellungen ---


def test_3_saving_a_key_pings_the_provider(game_db, data_dir, test_wiki):
    transport = FakeTransport(models("m"), OK_CALL)
    browser = Browser(make_app(data_dir, test_wiki, transport, environ={}))

    page = browser.post("/einstellungen/schluessel", {"key_env": "FREI_KEY", "schluessel": KEY})

    assert page.code == 303 and transport.requests[0]["headers"]["Authorization"] == f"Bearer {KEY}"
    shown = browser.get("/einstellungen", "ok=schluessel")
    assert '<p class="pruefung gut" role="status">Geprüft 00:00 UTC: erreichbar, Schlüssel gilt, Modell m antwortet' in shown.text
    assert [c["purpose"] for c in calls(data_dir)] == ["ping", "ping-aufruf"]
    assert game_db.execute("SELECT COUNT(*) FROM model_call").fetchone()[0] == 1  # der Probeaufruf zählt ins Budget


def test_3_no_credit_shows_up_on_the_page(game_db, data_dir, test_wiki):
    browser = Browser(make_app(data_dir, test_wiki, FakeTransport(models("m"), llm.HttpResponse(402, b"{}")), environ={}))

    browser.post("/einstellungen/schluessel", {"key_env": "FREI_KEY", "schluessel": KEY})

    shown = browser.get("/einstellungen")
    assert '<p class="pruefung schlecht" role="status">' in shown.text and "kein Guthaben (Fehler 402)" in shown.text


def test_3_a_bad_key_is_saved_and_the_page_says_why_it_fails(game_db, data_dir, test_wiki):
    browser = Browser(make_app(data_dir, test_wiki, FakeTransport(llm.HttpResponse(401, b"{}")), environ={}))

    browser.post("/einstellungen/schluessel", {"key_env": "FREI_KEY", "schluessel": KEY})

    shown = browser.get("/einstellungen")
    assert "gesetzt, endet auf …9876" in shown.text
    assert '<p class="pruefung schlecht" role="status">' in shown.text and "Schlüssel ungültig" in shown.text


def test_3_the_check_button_pings_again(game_db, data_dir, test_wiki):
    transport = FakeTransport(models("m"), llm.HttpResponse(503, b"{}"), models("m"), OK_CALL)
    browser = Browser(make_app(data_dir, test_wiki, transport))  # Schlüssel aus der Umgebung
    assert 'action="/einstellungen/pruefen"' in browser.get("/einstellungen").text

    first = browser.post("/einstellungen/pruefen", {"key_env": "FREI_KEY"})
    overloaded = browser.get("/einstellungen", "ok=geprueft").text
    browser.post("/einstellungen/pruefen", {"key_env": "FREI_KEY"})

    assert (first.code, first.headers["Location"]) == (303, "/einstellungen?ok=geprueft")
    assert "Anbieter geprüft." in overloaded and "überlastet (Fehler 503)" in overloaded
    assert "Modell m antwortet" in browser.get("/einstellungen").text


def test_3_no_check_button_without_a_key(game_db, data_dir, test_wiki):
    browser = Browser(make_app(data_dir, test_wiki, environ={}))

    assert 'action="/einstellungen/pruefen"' not in browser.get("/einstellungen").text


@pytest.mark.parametrize(("form", "headers", "code"), [
    ({"key_env": "UNBEKANNT_KEY"}, None, 400),
    ({"key_env": "FREI_KEY"}, {"HTTP_ORIGIN": "https://evil.example"}, 403),
])
def test_3_a_wrong_check_is_refused_and_nothing_goes_out(game_db, data_dir, test_wiki, form, headers, code):
    transport = FakeTransport()
    browser = Browser(make_app(data_dir, test_wiki, transport))

    page = browser.post("/einstellungen/pruefen", form, headers=headers)

    assert page.code == code and transport.requests == []


def test_3_a_broken_check_does_not_lose_the_key(game_db, data_dir, test_wiki):
    browser = Browser(make_app(data_dir, test_wiki, FakeTransport(), environ={}))  # keine Antwort vorbereitet

    page = browser.post("/einstellungen/schluessel", {"key_env": "FREI_KEY", "schluessel": KEY})

    assert page.code == 303 and "Prüfung gescheitert" in browser.get("/einstellungen").text


def test_3_playing_still_works_after_a_check(game_db, data_dir, test_wiki):
    transport = FakeTransport(models("m"), OK_CALL, decide(TO_TOWN), tell("Du gehst."))
    browser = Browser(make_app(data_dir, test_wiki, transport))

    browser.post("/einstellungen/pruefen", {"key_env": "FREI_KEY"})

    assert "Du gehst." in browser.say("Ich gehe.").text


# --- 4: Version in den Einstellungen ---


def test_4_the_settings_show_the_build(game_db, data_dir, test_wiki):
    commit = "253168ad90b14d78e076b0010f4f8cdaa1531928"
    environ = {"FREI_KEY": KEY, "VALAR_VERSION": commit, "VALAR_BUILT": "2026-10-25T09:41:07Z"}
    browser = Browser(make_app(data_dir, test_wiki, environ=environ))

    page = browser.get("/einstellungen")

    assert f'Build <a href="https://github.com/noledge5/ValarRP/commit/{commit}"><code>253168a</code></a> vom 25.10.2026, 09:41 UTC' in page.text


@pytest.mark.parametrize("environ", [{}, {"VALAR_VERSION": '"><script>alert(1)</script>'}, {"VALAR_VERSION": "abc"}])
def test_4_without_a_build_it_says_so(game_db, data_dir, test_wiki, environ):
    browser = Browser(make_app(data_dir, test_wiki, environ=environ))

    page = browser.get("/einstellungen")

    assert "Entwicklung: ohne Build der CI gestartet." in page.text and "<script>alert" not in page.text


def test_4_the_build_writes_commit_and_time_into_the_image():
    dockerfile = (ROOT / "Dockerfile").read_text()
    workflow = (ROOT / ".github/workflows/tests.yml").read_text()

    assert "ENV VALAR_VERSION=$VALAR_VERSION VALAR_BUILT=$VALAR_BUILT" in dockerfile
    assert '--build-arg VALAR_VERSION="$GITHUB_SHA"' in workflow and "--build-arg VALAR_BUILT=" in workflow


def test_2_a_model_named_by_its_alias_counts_as_known():
    listing = llm.HttpResponse(200, json.dumps({"data": [{"id": "mistral-medium-2604", "aliases": ["mistral-medium-latest"]}]}).encode())

    assert llm._list_text(provider(model="mistral-medium-latest"), listing) == (True, "erreichbar, Schlüssel gilt")


def test_the_model_list_keeps_chat_models_with_json_and_their_prices():
    listing = llm.HttpResponse(200, json.dumps({"data": [
        {"id": "models/gemini-x"},
        {"id": "mistral-embed", "capabilities": {"completion_chat": False}},
        {"id": "wechselnd", "supported_parameters": ["response_format"], "pricing": {"prompt": "-1", "completion": "-1"}},
    ]}).encode())

    options = llm.list_models(provider(), {"FREI_KEY": KEY}, FakeTransport(listing))

    assert options == [llm.ModelOption("gemini-x")]
    assert llm.list_models(provider(), {}, FakeTransport()) == []  # ohne Schlüssel geht nichts hinaus


def test_chosen_slots_come_first_and_the_file_stays_the_fallback():
    settings = llm.ModelSettings(provider=(provider("frei", "m"), provider("zweit", "n")))
    chain = [keys.ChainSlot(provider="zweit", model="gross", input_eur_per_million=2), keys.ChainSlot(provider="frei", model="m"),
             keys.ChainSlot(provider="weg", model="x")]

    shown = [(p.name, p.model, p.input_eur_per_million) for p in keys.apply_chain(settings, chain).provider]

    assert shown == [("1. zweit", "gross", 2), ("2. frei", "m", 0), ("zweit", "n", 0)]
