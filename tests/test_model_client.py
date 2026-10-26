"""Schritt 2a: Modell-Client gegen eine Attrappe, ohne Netz (Entscheidungen 4, 5, 67, 153 bis 157).

Die Nummern folgen dem README. Testdaten: zwei Anbieter, „frei“ (kostenlos) und „bezahlt“ (1 € je Million Tokens
Eingabe, 2 € Ausgabe), Budget mit Warnung bei 10 € und Stopp bei 20 €. Erwartet wird eine Aktion (`ACTIONS`).
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.helpers import CAMPAIGN, PLAYER, FakeTransport, add_campaign, add_player, chat, dump_state
from valar import llm
from valar.models import ACTIONS, Move, Sleep

KEY = "sk-geheim-1234567890"
OTHER_KEY = "sk-anderer-0987654321"
MESSAGES = [{"role": "system", "content": "Du bist der Spielleiter."}, {"role": "user", "content": "Ich schlafe."}]


def provider(name: str = "frei", **changes) -> llm.ProviderSettings:
    fields = {
        "name": name,
        "base_url": f"https://{name}.example/v1",
        "model": f"{name}-modell",
        "key_env": f"{name.upper()}_KEY",
    }
    return llm.ProviderSettings(**(fields | changes))


SETTINGS = llm.ModelSettings(
    provider=(provider("frei"), provider("bezahlt", input_eur_per_million=1, output_eur_per_million=2)),
)
ENV = {"FREI_KEY": KEY, "BEZAHLT_KEY": OTHER_KEY}


SLEEP = json.dumps({"type": "sleep"})


@pytest.fixture
def game(conn):
    """Ein Spielstand, an dem sich durch den Client nichts ändern darf."""
    add_campaign(conn, CAMPAIGN)
    add_player(conn, PLAYER)


def client(conn, transport, *, settings=SETTINGS, env=ENV, month: int = 10, clock=lambda: 1000.0) -> llm.ModelClient:
    return llm.ModelClient(
        settings, llm.Ledger(conn), transport=transport, environ=env, now=lambda: datetime(2026, month, 22, tzinfo=UTC),
        clock=clock,
    )


def ask(conn, transport, **kwargs):
    return client(conn, transport, **kwargs).request(MESSAGES, ACTIONS)


def game_state(conn) -> dict:
    return {t: rows for t, rows in dump_state(conn).items() if t not in ("model_call", "sqlite_sequence")}


def calls(conn) -> list[tuple]:
    return [
        tuple(row)
        for row in conn.execute(
            "SELECT month, provider, model, input_tokens, output_tokens, cost_micro_eur, outcome FROM model_call"
            " ORDER BY call_id"
        )
    ]


def spend(conn, euros: float, month: str = "2026-10") -> None:
    llm.Ledger(conn).record(
        month=month,
        provider=provider("bezahlt", input_eur_per_million=1),
        input_tokens=int(euros * 1_000_000),
        output_tokens=0,
        outcome="ok",
    )


# --- 1: Gültige Antwort wird zur Aktion, sonst genau ein Wiederholungsversuch ---


def test_1_a_valid_answer_becomes_the_action(conn, game):
    transport = FakeTransport(chat(json.dumps({"type": "move", "destination_id": "boomtown"})))
    before = game_state(conn)

    reply = ask(conn, transport)

    assert reply.value == Move(destination_id="boomtown")
    assert (reply.provider, reply.model, reply.attempts, reply.budget_warning) == ("frei", "frei-modell", 1, False)
    assert game_state(conn) == before


def test_1_the_schema_goes_to_the_provider(conn, game):
    transport = FakeTransport(chat(SLEEP))
    ask(conn, transport)

    sent = transport.requests[0]
    assert sent["url"] == "https://frei.example/v1/chat/completions"
    assert sent["body"]["model"] == "frei-modell" and sent["body"]["messages"] == MESSAGES
    assert sent["body"]["response_format"]["json_schema"]["schema"] == llm.provider_schema(ACTIONS.json_schema())


def test_1_the_schema_is_flattened_for_every_provider():
    """Härtetest 2c: Gemini verstand oneOf, $ref und const nicht und lieferte nur {"type": …}."""
    flat = llm.provider_schema(ACTIONS.json_schema())
    text = json.dumps(flat)
    for key in ('"$ref"', '"$defs"', '"oneOf"', '"const"', '"discriminator"', '"default"'):
        assert key not in text
    move = next(v for v in flat["anyOf"] if v["properties"]["type"] == {"enum": ["move"], "type": "string"})
    assert move["required"] == ["type", "destination_id"]
    # Ein freies Feld bleibt frei: Pflicht, aber null ist erlaubt.
    attempt = next(v for v in flat["anyOf"] if v["properties"]["type"]["enum"] == ["attempt"])
    assert set(attempt["required"]) == set(attempt["properties"])


def test_1_a_reference_cycle_is_refused():
    looped = {"$defs": {"A": {"type": "object", "properties": {"a": {"$ref": "#/$defs/A"}}}}, "$ref": "#/$defs/A"}
    with pytest.raises(ValueError, match="Kreis"):
        llm.provider_schema(looped)


@pytest.mark.parametrize("mode, expected", [("json_object", {"type": "json_object"}), ("none", None)])
def test_1_providers_without_json_schema(conn, game, mode, expected):
    settings = llm.ModelSettings(provider=(provider(output=mode),))
    transport = FakeTransport(chat(SLEEP))
    ask(conn, transport, settings=settings)
    assert transport.requests[0]["body"].get("response_format") == expected


@pytest.mark.parametrize("base_url, expected", [
    ("https://openrouter.ai/api/v1", {"require_parameters": True}),
    ("https://frei.example/v1", None),
])
def test_1_openrouter_only_routes_to_providers_with_the_format(conn, game, base_url, expected):
    settings = llm.ModelSettings(provider=(provider(base_url=base_url),))
    transport = FakeTransport(chat(SLEEP))
    ask(conn, transport, settings=settings)
    assert transport.requests[0]["body"].get("provider") == expected


@pytest.mark.parametrize(
    "bad",
    [
        "Ich schlafe jetzt.",  # kein JSON
        json.dumps({"type": "sleep", "minutes": 600}),  # Zusatzfeld
        json.dumps({"type": "fly"}),  # unbekannte Aktion
        json.dumps({"type": "move"}),  # Pflichtfeld fehlt
    ],
)
def test_1_an_invalid_answer_gets_exactly_one_retry(conn, game, bad):
    transport = FakeTransport(chat(bad), chat(SLEEP))

    reply = ask(conn, transport)

    assert (reply.value, reply.attempts) == (Sleep(), 2)
    retry = transport.requests[1]["body"]["messages"]
    assert retry[:2] == MESSAGES and retry[2] == {"role": "assistant", "content": bad}
    assert retry[3]["role"] == "user" and "passt nicht zum Schema" in retry[3]["content"]
    assert [c[-1] for c in calls(conn)] == ["invalid", "ok"]


def test_1_two_invalid_answers_are_an_error_and_change_nothing(conn, game):
    transport = FakeTransport(chat("Unsinn"), chat(json.dumps({"type": "sleep", "dc": 1})))
    before = game_state(conn)

    with pytest.raises(llm.ModelError) as error:
        ask(conn, transport)

    assert error.value.code == "invalid_reply"
    assert len(transport.requests) == 2  # kein dritter Versuch, kein Wechsel des Anbieters
    assert game_state(conn) == before


def test_1_json_in_code_fences_counts(conn, game):
    assert ask(conn, FakeTransport(chat(f"```json\n{SLEEP}\n```"))).value == Sleep()


@pytest.mark.parametrize("body", [b"kein json", b"{}", b'{"choices": []}', b'{"choices": [{"message": {"content": null}}]}'])
def test_1_a_broken_reply_counts_as_invalid(conn, game, body):
    transport = FakeTransport(llm.HttpResponse(200, body), chat(SLEEP))
    assert ask(conn, transport).attempts == 2


# --- 2: Kosten und Monatsbudget ---


def test_2_costs_are_counted_per_call(conn, game):
    settings = llm.ModelSettings(provider=(SETTINGS.provider[1],))  # nur der bezahlte
    transport = FakeTransport(chat("Unsinn", 1000, 50), chat(SLEEP, 1200, 20))

    ask(conn, transport, settings=settings)

    # 1000 × 1 € + 50 × 2 € je Million Tokens = 1100 Millionstel Euro; dann 1200 + 40.
    assert calls(conn) == [
        ("2026-10", "bezahlt", "bezahlt-modell", 1000, 50, 1100, "invalid"),
        ("2026-10", "bezahlt", "bezahlt-modell", 1200, 20, 1240, "ok"),
    ]


def test_2_free_quota_counts_zero(conn, game):
    ask(conn, FakeTransport(chat(SLEEP, 5000, 500)))
    assert calls(conn)[0][5] == 0


def test_2_without_token_numbers_the_client_estimates(conn, game):
    settings = llm.ModelSettings(provider=(SETTINGS.provider[1],))
    ask(conn, FakeTransport(chat(SLEEP, None)), settings=settings)
    (_, _, _, input_tokens, output_tokens, cost, _), = calls(conn)
    expected_in = sum(len(m["content"]) for m in MESSAGES) // llm.CHARS_PER_TOKEN
    assert (input_tokens, output_tokens) == (expected_in, len(SLEEP) // llm.CHARS_PER_TOKEN) and cost > 0


def test_2_warning_from_ten_euros(conn, game):
    spend(conn, 9.99)
    assert ask(conn, FakeTransport(chat(SLEEP))).budget_warning is False
    spend(conn, 0.01)
    assert ask(conn, FakeTransport(chat(SLEEP))).budget_warning is True


def test_2_at_the_budget_the_game_stops_before_the_next_call(conn, game):
    spend(conn, 20)
    transport = FakeTransport(chat(SLEEP))
    before = game_state(conn)

    with pytest.raises(llm.ModelError) as error:
        ask(conn, transport)

    assert error.value.code == "budget"
    assert transport.requests == []  # gar nicht erst gefragt
    assert game_state(conn) == before


def test_2_the_budget_also_stops_the_retry(conn, game):
    settings = llm.ModelSettings(
        budget=llm.BudgetSettings(warn_eur=0.001, stop_eur=0.002),
        provider=(provider("bezahlt", input_eur_per_million=1),),
    )
    transport = FakeTransport(chat("Unsinn", 2500, 0), chat(SLEEP))

    with pytest.raises(llm.ModelError) as error:
        ask(conn, transport, settings=settings)

    assert error.value.code == "budget" and len(transport.requests) == 1


def test_2_a_new_month_starts_at_zero(conn, game):
    spend(conn, 25, month="2026-09")
    assert ask(conn, FakeTransport(chat(SLEEP)), month=10).value == Sleep()


# --- 3: Schlüssel nur aus der Umgebung, nie in Datenbank, Einstellungen oder Meldungen ---


def test_3_the_key_goes_only_into_the_authorization_header(conn, game, db_path):
    transport = FakeTransport(chat(SLEEP))
    ask(conn, transport)

    assert transport.requests[0]["headers"]["Authorization"] == f"Bearer {KEY}"
    assert KEY not in json.dumps(transport.requests[0]["body"])
    assert KEY.encode() not in Path(db_path).read_bytes()
    assert KEY not in repr(SETTINGS)


def test_3_settings_cannot_hold_a_key(tmp_path):
    path = tmp_path / "modelle.toml"
    path.write_text(
        '[[provider]]\nname = "x"\nbase_url = "https://x.example"\nmodel = "m"\nkey_env = "X_KEY"\napi_key = "sk-1"\n'
    )
    with pytest.raises(ValidationError):
        llm.load_settings(path)


def test_3_a_provider_without_key_is_skipped(conn, game):
    transport = FakeTransport(chat(SLEEP))
    reply = ask(conn, transport, env={"BEZAHLT_KEY": OTHER_KEY})
    assert reply.provider == "bezahlt" and transport.requests[0]["url"].startswith("https://bezahlt")

    with pytest.raises(llm.ModelError) as error:
        ask(conn, FakeTransport(), env={})
    assert error.value.code == "no_provider"


@pytest.mark.parametrize(
    "reply",
    [
        llm.HttpResponse(401, f'{{"error": "invalid key {KEY}"}}'.encode()),
        llm.HttpResponse(500, KEY.encode()),
        llm.TransportError(f"Verbindung mit {KEY} abgebrochen"),
    ],
)
def test_3_no_error_message_contains_the_key(conn, game, reply, db_path):
    with pytest.raises(llm.ModelError) as error:
        ask(conn, FakeTransport(reply), settings=llm.ModelSettings(provider=(provider("frei"),)))
    assert KEY not in str(error.value) and KEY not in repr(error.value)
    assert KEY.encode() not in Path(db_path).read_bytes()


@pytest.mark.parametrize(
    "url",
    ["http://api.example/v1", "ftp://api.example", "https://api.example/v1?key=1", "https:///v1", "api.example"],
)
def test_3_keys_only_go_out_encrypted(url):
    with pytest.raises(ValidationError):
        provider(base_url=url)


def test_3_local_models_may_use_http():
    assert provider(base_url="http://localhost:11434/v1/").base_url == "http://localhost:11434/v1"


# --- 4: Zeitüberschreitung und Netzfehler, erschöpftes Kontingent ---


@pytest.mark.parametrize("reply", [llm.HttpResponse(500, b"{}"), llm.HttpResponse(401, b"{}")])
def test_4_a_provider_error_is_an_error_without_effect(conn, game, reply):
    before = game_state(conn)
    with pytest.raises(llm.ModelError) as error:
        ask(conn, FakeTransport(reply))
    assert error.value.code == "provider_error"
    assert game_state(conn) == before
    assert [c[-1] for c in calls(conn)] == ["error"]
    assert llm.RESTING == {}  # ein echter Fehler ist kein Grund zu ruhen


def test_4_no_answer_moves_on_and_the_provider_rests(conn, game):
    """Entscheidung 223 (ändert 193 und 210): Zeitüberschreitung führt weiter, der Anbieter ruht REST_S Sekunden."""
    transport = FakeTransport(llm.TransportError("TimeoutError"), chat(SLEEP), chat(SLEEP))

    first = ask(conn, transport)
    second = ask(conn, transport, clock=lambda: 1000.0 + llm.REST_S - 1)  # ein neuer Client, derselbe Prozess

    assert (first.provider, second.provider) == ("bezahlt", "bezahlt")
    assert [r["headers"]["Authorization"] for r in transport.requests] == [f"Bearer {KEY}"] + [f"Bearer {OTHER_KEY}"] * 2
    assert llm.RESTING == {"frei": 1000.0 + llm.REST_S}


def test_4_after_the_rest_the_chain_order_returns(conn, game):
    llm.RESTING["frei"] = 1000.0 + llm.REST_S
    transport = FakeTransport(chat(SLEEP))

    reply = ask(conn, transport, clock=lambda: 1000.0 + llm.REST_S)

    assert reply.provider == "frei" and llm.RESTING == {}


def test_4_when_everyone_rests_everyone_is_still_tried_in_order(conn, game):
    llm.RESTING.update({"frei": 2000.0, "bezahlt": 2000.0})
    transport = FakeTransport(llm.HttpResponse(503, b"{}"), chat(SLEEP))

    reply = ask(conn, transport)

    assert reply.provider == "bezahlt" and llm.RESTING == {"frei": 1000.0 + llm.REST_S}


def test_4_the_timeout_comes_from_the_settings(conn, game):
    transport = FakeTransport(chat(SLEEP))
    ask(conn, transport, settings=llm.ModelSettings(provider=(provider(timeout_s=12),)))
    assert transport.requests[0]["timeout"] == 12


def test_4_exhausted_quota_moves_on_to_the_next_provider(conn, game):
    transport = FakeTransport(llm.HttpResponse(429, b"{}"), chat(SLEEP))

    reply = ask(conn, transport)

    assert (reply.provider, reply.value) == ("bezahlt", Sleep())
    assert transport.requests[1]["headers"]["Authorization"] == f"Bearer {OTHER_KEY}"
    assert [(c[1], c[-1]) for c in calls(conn)] == [("frei", "quota"), ("bezahlt", "ok")]


def test_4_all_quotas_exhausted(conn, game):
    with pytest.raises(llm.ModelError) as error:
        ask(conn, FakeTransport(llm.HttpResponse(429, b"{}"), llm.HttpResponse(429, b"{}")))
    assert error.value.code == "no_provider"


# --- Einstellungen ---


def test_the_example_settings_are_valid():
    settings = llm.load_settings(Path(__file__).parents[1] / "modelle.example.toml")
    assert [p.name for p in settings.provider] == ["openrouter", "gemini", "mistral", "gemini-reserve", "gemini-bezahlt"]  # Entscheidungen 181, 210, 214, 215
    assert (settings.budget.warn_eur, settings.budget.stop_eur) == (10, 20)


@pytest.mark.parametrize(
    "data",
    [
        {"provider": []},
        {"provider": [provider().model_dump(), provider().model_dump()]},  # doppelter Name
        {"budget": {"warn_eur": 30, "stop_eur": 20}, "provider": [provider().model_dump()]},
        {"provider": [provider().model_dump() | {"key_env": "sk-abc"}]},  # sieht aus wie ein Schlüssel
    ],
)
def test_wrong_settings_are_rejected(data):
    with pytest.raises(ValidationError):
        llm.ModelSettings.model_validate(data)
