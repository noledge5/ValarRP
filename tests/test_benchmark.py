"""Schritt 2c: Ablauf des Härtetests gegen eine Attrappe, ohne Netz (Entscheidungen 178 bis 182).

Die Nummern folgen dem README. Die Attrappe antwortet je nach verlangtem Schema: auf Aufruf A mit der nächsten
Aktion aus einem Plan, auf Aufruf B mit einer Erzählung. Testdaten wie in Schritt 2b.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.helpers import TEST_WIKI, chat, content_pointing_to, write_wiki
from valar import benchmark, llm, wiki
from valar.content import BOOMTOWN, GUILD_SHARD_ORDER, TAVERN

CONTENT = content_pointing_to(boomtown="testdorf", crystal_field="anderswo")
NOW = datetime(2026, 10, 22, tzinfo=UTC)
KEY = "sk-geheim-im-test-4711"


def narrate(text: str) -> dict:
    return {"type": "narrate", "text": text, "notes": []}


# Eine passende Aktion je Schritt der vier Szenen; Würfe brauchen keinen Aufruf A.
GOOD_PLAN = [
    narrate("Staub tanzt im Licht über dem Zähltisch."),
    {"type": "submit_quest_items", "quest_id": GUILD_SHARD_ORDER},
    {"type": "move", "destination_id": BOOMTOWN},
    {"type": "move", "destination_id": TAVERN},
    {"type": "attempt", "skill_id": "SKILL-101", "difficulty": "DIFF-03", "duration": "moment", "reason": "Tür"},
    narrate("Im Lagerraum stapeln sich leere Kisten."),
    {"type": "meet_npc", "template_id": "koehler", "name": "Berta Krumm", "description": "Alte Wirtin"},
    {"type": "talk", "npc_id": "berta-krumm"},
    {"type": "contest", "skill_id": "SKILL-302", "npc_id": "berta-krumm", "duration": "moment", "reason": "Würfel"},
    narrate("Unter dem Tisch liegt nur Dreck."),
    narrate("Eine Kaiserburg kennt hier niemand."),
    narrate("Der Zunftmeister lacht dich aus."),
    {"type": "sleep"},
]


class ScriptedModel:
    """Antwortet auf Aufruf A mit der nächsten Aktion, auf B mit einer Erzählung. Einträge können auch fertige
    Antworten sein (z. B. ein Fehler), dann gehen sie unverändert hinaus."""

    def __init__(self, plan: list, *, narration: str = "Du hörst den Regen auf dem Dach.") -> None:
        self.plan = list(plan)
        self.narration = narration
        self.requests: list[dict] = []

    def post(self, url, headers, body, timeout):
        request = json.loads(body)
        self.requests.append({"url": url, "headers": dict(headers), "body": request})
        if request["response_format"]["json_schema"]["name"] == "erzaehlung":
            return chat(json.dumps({"text": self.narration, "notes": []}))
        reply = self.plan.pop(0)
        return reply if isinstance(reply, llm.HttpResponse) else chat(json.dumps({"action": reply}))


@pytest.fixture
def test_wiki(tmp_path: Path) -> wiki.Wiki:
    write_wiki(tmp_path / "w", TEST_WIKI)
    return wiki.load(tmp_path / "w")


def provider(name: str = "frei", key_env: str = "FREI_KEY", **prices) -> llm.ProviderSettings:
    return llm.ProviderSettings(name=name, base_url=f"https://{name}.example/v1", model=f"{name}-modell", key_env=key_env, **prices)


def run(test_wiki, transport, *providers, environ=None, budget=llm.BudgetSettings(), scenes=benchmark.SCENES):
    settings = llm.ModelSettings(budget=budget, provider=providers or (provider(),))
    return benchmark.run(
        settings,
        scenes=scenes,
        transport=transport,
        environ={"FREI_KEY": KEY} if environ is None else environ,
        content=CONTENT,
        wiki=test_wiki,
        now=lambda: NOW,
    )


# --- 1: Szenen in frischem Spielstand ---


def test_1_all_scenes_run_and_a_good_model_fits_every_step(test_wiki):
    transport = ScriptedModel(GOOD_PLAN)

    (model,) = run(test_wiki, transport)

    assert [s.scene.scene_id for s in model.scenes] == ["s1", "s2", "s3", "s4"]
    assert all(step.fits for step in model.steps()), [(s.step.text, s.action, s.status) for s in model.steps() if not s.fits]
    assert len(model.steps()) == sum(len(s.steps) for s in benchmark.SCENES)
    assert transport.plan == []
    rolls = [s for s in model.steps() if s.step.roll is not None]
    assert [r.status for r in rolls] == ["accepted", "accepted"] and all(r.narration for r in rolls)


def test_1_every_scene_starts_fresh_with_the_test_figure(test_wiki):
    transport = ScriptedModel(GOOD_PLAN)

    (model,) = run(test_wiki, transport)

    s2 = model.scenes[1]
    assert s2.steps[0].status == "awaiting_roll"  # im Zunfthaus, ohne Folgen aus Szene 1
    first_s3 = next(r for r in transport.requests if "Berta Krumm" in json.dumps(r["body"], ensure_ascii=False))
    assert "Schenke" in first_s3["body"]["messages"][1]["content"]  # Aufbau: in die Schenke gegangen


def test_1_same_replies_give_the_same_report(test_wiki):
    first = benchmark.report(run(test_wiki, ScriptedModel(GOOD_PLAN)), date="2026-10-22")
    second = benchmark.report(run(test_wiki, ScriptedModel(GOOD_PLAN)), date="2026-10-22")

    assert first == second


# --- 2 und 3: Schema-Treue und Regeltreue ---


def test_2_invalid_replies_and_retries_are_counted(test_wiki):
    plan = [chat("kein JSON")] + GOOD_PLAN  # erster Aufruf ungültig, die Wiederholung passt
    (model,) = run(test_wiki, ScriptedModel(plan), scenes=benchmark.SCENES[:1])

    calls = model.calls()
    assert (calls["invalid"], calls["ok"]) == (1, 7)
    assert all(step.fits for step in model.steps())


def test_2_a_failing_model_ends_only_the_step(test_wiki):
    plan = [chat("kein JSON"), chat("wieder nichts"), llm.HttpResponse(500, b"")] + GOOD_PLAN[2:4]
    (model,) = run(test_wiki, ScriptedModel(plan), scenes=benchmark.SCENES[:1])

    steps = model.steps()
    assert [s.action for s in steps] == [None, None, "move", "move"]
    assert steps[0].error.startswith("invalid_reply") and steps[1].error.startswith("provider_error")
    assert [s.fits for s in steps] == [False, False, True, True]
    assert (model.calls()["invalid"], model.calls()["error"]) == (2, 1)


def test_3_a_wrong_choice_does_not_fit(test_wiki):
    plan = [  # „umsehen“ als Gehen; danach nur Ziele, die von dort aus gültig sind
        {"type": "move", "destination_id": BOOMTOWN},
        {"type": "submit_quest_items", "quest_id": GUILD_SHARD_ORDER},
        {"type": "move", "destination_id": TAVERN},
        {"type": "move", "destination_id": BOOMTOWN},
    ]
    (model,) = run(test_wiki, ScriptedModel(plan), scenes=benchmark.SCENES[:1])

    first = model.steps()[0]
    assert (first.action, first.fits) == ("move", False)


def test_3_rejected_turns_are_recorded(test_wiki):
    plan = [  # Abgabe erst in Sturmhagen: falscher Ort, abgelehnt
        narrate("Ruhe."),
        {"type": "move", "destination_id": BOOMTOWN},
        {"type": "submit_quest_items", "quest_id": GUILD_SHARD_ORDER},
        {"type": "move", "destination_id": TAVERN},
    ]
    (model,) = run(test_wiki, ScriptedModel(plan), scenes=benchmark.SCENES[:1])

    third = model.steps()[2]
    assert (third.action, third.status, third.reject_code, third.fits) == ("submit_quest_items", "rejected", "wrong_location", False)
    assert third.narration  # die Ablehnung wird in der Welt erzählt
    row = next(line for line in benchmark.report([model], date="2026-10-22").splitlines() if line.startswith("| frei-modell"))
    assert "| 2/4 | 1 |" in row


def test_3_an_unexpected_open_roll_is_rolled_and_not_counted(test_wiki):
    attempt = {"type": "attempt", "skill_id": "SKILL-101", "difficulty": "DIFF-03", "duration": "moment", "reason": "Beutel"}
    plan = [attempt] + GOOD_PLAN[10:]
    scene = benchmark.SCENES[3]
    (model,) = run(test_wiki, ScriptedModel(plan), scenes=(scene,))

    steps = model.scenes[0].steps
    assert steps[1].step.auto and steps[1].step.roll == benchmark.AUTO_ROLL and steps[1].status == "accepted"
    assert len(model.steps()) == len(scene.steps)
    assert all(step.fits for step in model.steps())
    assert "vom Ablauf eingefügt, nicht gewertet" in benchmark.report([model], date="2026-10-22")


# --- 4: Bericht ---


def test_4_the_report_has_the_overview_and_every_narration(test_wiki):
    transport = ScriptedModel(GOOD_PLAN, narration="Der Regen trommelt.\n\n„Was willst du?“, fragt sie.")
    report = benchmark.report(run(test_wiki, transport), date="2026-10-22")

    assert report.startswith("# Härtetest 2026-10-22\n")
    assert "| frei-modell | frei | 23/23 | 0 | 0 | 15/15 | 0 | 0.0000 € |" in report
    assert "   > Der Regen trommelt.\n   >\n   > „Was willst du?“, fragt sie." in report
    assert "   > Eine Kaiserburg kennt hier niemand." in report
    assert "### s3: Leute in der Schenke" in report and "erwartet: meet_npc" in report


# --- 5: Kosten, Budget, mehrere Modelle ---


def test_5_each_provider_is_tested_alone(test_wiki):
    transport = ScriptedModel(GOOD_PLAN[:4] + GOOD_PLAN[:4])
    first, second = provider("eins", "EINS_KEY"), provider("zwei", "ZWEI_KEY")

    results = run(test_wiki, transport, first, second, environ={"EINS_KEY": "k1-12345678", "ZWEI_KEY": "k2-12345678"}, scenes=benchmark.SCENES[:1])

    assert [r.provider.name for r in results] == ["eins", "zwei"]
    hosts = [r["url"].split("/")[2] for r in transport.requests]
    assert hosts == ["eins.example"] * 7 + ["zwei.example"] * 7


def test_5_a_provider_without_key_is_skipped(test_wiki):
    transport = ScriptedModel(GOOD_PLAN[:4])

    results = run(
        test_wiki, transport, provider("ohne", "OHNE_KEY"), provider(), scenes=benchmark.SCENES[:1]
    )

    assert results[0].skipped == "kein Schlüssel in OHNE_KEY" and results[0].scenes == []
    assert all(r["url"].startswith("https://frei.example") for r in transport.requests)
    assert "ohne-modell (kein Schlüssel in OHNE_KEY)" in benchmark.report(results, date="2026-10-22")


def test_5_the_budget_holds_over_the_whole_run(test_wiki):
    pricey = provider(input_eur_per_million=10_000)  # 100 Tokens je Aufruf: 1 € je Aufruf
    transport = ScriptedModel(GOOD_PLAN)

    (model,) = run(test_wiki, transport, pricey, budget=llm.BudgetSettings(warn_eur=1, stop_eur=3))

    assert [s.scene.scene_id for s in model.scenes] == ["s1"]
    assert model.skipped == "Budget von 3 € für den Lauf erreicht"
    assert model.cost_micro_eur == 3_000_000  # schon in der Szene stoppt der Client vor dem nächsten Aufruf
    assert [s.error.split(":")[0] for s in model.steps()[2:]] == ["budget", "budget"]


def test_5_a_later_scene_gets_only_the_rest_of_the_budget(test_wiki):
    pricey = provider(input_eur_per_million=10_000)  # 1 € je Aufruf; Szene 1 kostet 7 €
    (model,) = run(test_wiki, ScriptedModel(GOOD_PLAN), pricey, budget=llm.BudgetSettings(warn_eur=1, stop_eur=8.5))

    assert [s.scene.scene_id for s in model.scenes] == ["s1", "s2"]
    assert model.scenes[1].cost_micro_eur == 2_000_000  # 1,5 € Rest: nach zwei Aufrufen ist Schluss
    assert model.cost_micro_eur == 9_000_000


# --- 6: Schlüssel und Kommandozeile ---


def test_6_main_writes_the_report_without_the_key(test_wiki, tmp_path, monkeypatch):
    settings = tmp_path / "haertetest.toml"
    settings.write_text(
        '[[provider]]\nname = "frei"\nbase_url = "https://frei.example/v1"\nmodel = "m"\nkey_env = "FREI_KEY"\n', encoding="utf-8"
    )
    monkeypatch.setattr(benchmark, "CONTENT", CONTENT)
    target = tmp_path / "bericht" / "lauf.md"
    transport = ScriptedModel(GOOD_PLAN[:4])

    code = benchmark.main(
        ["--modelle", str(settings), "--bericht", str(target), "--szene", "s1"], transport=transport, environ={"FREI_KEY": KEY}
    )

    assert code == 0 and target.exists()
    text = target.read_text(encoding="utf-8")
    assert "### s1:" in text and "### s2:" not in text and KEY not in text
    assert all(r["headers"]["Authorization"] == f"Bearer {KEY}" for r in transport.requests)


def test_6_main_refuses_unknown_scenes_and_bad_settings(tmp_path):
    assert benchmark.main(["--modelle", str(tmp_path / "fehlt.toml"), "--bericht", str(tmp_path / "b.md")]) == 2
    assert not (tmp_path / "b.md").exists()


def test_6_the_example_settings_are_valid():
    settings = llm.load_settings(Path(__file__).resolve().parents[1] / "haertetest.example.toml")

    assert [p.name for p in settings.provider] == ["gemini", "mistral", "deepseek"]
