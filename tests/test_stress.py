"""Schritt 2c: das Gerüst des Härtetests gegen eine Attrappe, ohne Netz.

Geprüft wird nur das Werkzeug (Ablauf, Prüfungen, Zählung, Bericht), nicht ein Modell.
"""

import json
from pathlib import Path

from tests.helpers import FakeTransport, chat
from valar import llm, stress
from valar.wiki import default_wiki

PROVIDER = llm.ProviderSettings(name="probe", base_url="https://openrouter.ai/api/v1", model="m", key_env="PROBE_KEY")
KEY = "sk-geheim-1234"
SCENES = {scene.scene_id: scene for scene in stress.SCENES}


def decide(action: dict, cost: float = 0.001):
    body = {"choices": [{"message": {"content": json.dumps({"action": action})}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10, "cost": cost}}
    return llm.HttpResponse(200, json.dumps(body).encode())


def narrate(text: str = "Rauch hängt unter der Decke."):
    return decide({"type": "narrate", "text": text, "notes": []})


def tell(text: str = "Ein Gulden landet vor dir."):
    return chat(json.dumps({"text": text, "notes": []}))


def play(tmp_path: Path, scene_id: str, *replies) -> tuple[list[stress.StepResult], FakeTransport]:
    transport = FakeTransport(*replies)
    results = stress.run_scene(
        PROVIDER, SCENES[scene_id], 1, db_path=tmp_path / "probe.db", wiki=default_wiki(), budget=stress.Budget(1.0),
        transport=transport, environ={"PROBE_KEY": KEY},
    )
    return results, transport


def test_scene_counts_actions_checks_and_cost(tmp_path):
    results, transport = play(
        tmp_path, "zunfthaus",
        narrate(),
        decide({"type": "submit_quest_items", "quest_id": "guild_shard_order"}), tell(),
        narrate("Die Prüferin lacht nur."),
        decide({"type": "sleep"}), tell("Du schläfst."),
    )
    assert [r.action for r in results] == ["narrate", "submit_quest_items", "narrate", "sleep"]
    assert [r.action_ok for r in results] == [True, True, True, False]
    assert [r.check_ok for r in results] == [True, True, None, True]  # Schlafen ändert das Geld nicht
    assert results[1].code_result[-1].startswith("Dauer: 10 Min.")
    summary = stress.summarize("probe", results)
    assert (summary.steps, summary.calls, summary.action_ok, summary.check_ok) == (4, 6, 3, 3)
    assert summary.cost_usd == 0.004  # nur Aufrufe, die Kosten melden
    # OpenRouter muss ein Schema können und meldet die Kosten; der Schlüssel steht nur im Kopf.
    body = transport.requests[0]["body"]
    assert body["provider"] == {"require_parameters": True} and body["usage"] == {"include": True}
    assert transport.requests[0]["headers"]["Authorization"] == f"Bearer {KEY}"
    assert KEY not in stress.transcript("probe", results)


def test_roll_step_is_skipped_without_open_roll(tmp_path):
    results, _ = play(
        tmp_path, "glasgrund",
        narrate("Die Wachen sehen dich nicht einmal an."),  # keine Probe: der Wurf danach entfällt
        narrate("Staub, sonst nichts."),
        narrate("Du findest keine Sturmkappe."),
    )
    assert [r.skipped for r in results] == [False, True, False, True, False, True]
    assert results[0].check_error == "keine Probe offen"
    assert "übersprungen" in stress.transcript("probe", results)


def test_invalid_reply_counts_even_when_the_retry_saves_it(tmp_path):
    results, _ = play(
        tmp_path, "zunfthaus",
        chat("kein JSON"), narrate(),
        narrate(), narrate(), narrate(),
    )
    assert results[0].invalid == 1 and results[0].action_ok
    assert stress.summarize("probe", results).invalid_calls == 1
    assert "| probe |" in stress.report({"probe": results})


def test_model_error_leaves_state_and_is_reported(tmp_path):
    results, _ = play(
        tmp_path, "zunfthaus",
        chat("kein JSON"), chat("wieder nicht"),
        narrate(), narrate(), narrate(),
    )
    assert results[0].model_error.startswith("invalid_reply") and results[0].action is None
    assert results[1].action == "narrate" and results[1].check_error.startswith("Geld 30 statt 130 Pfennig")
    assert stress.summarize("probe", results).model_errors == 1


def test_results_are_saved_after_every_scene(tmp_path):
    transport = FakeTransport(narrate(), narrate(), narrate(), narrate())
    scene = SCENES["zunfthaus"]
    stress.run_model(PROVIDER, (scene,), 1, tmp_path, stress.Budget(1.0), transport=transport, environ={"PROBE_KEY": KEY})
    saved = json.loads((tmp_path / "ergebnis-probe.json").read_text(encoding="utf-8"))
    assert [r["action"] for r in saved] == ["narrate"] * 4
    assert (tmp_path / "verlauf-probe.md").read_text(encoding="utf-8").startswith("# Verlauf: probe")
