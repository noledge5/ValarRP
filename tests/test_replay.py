"""Protokollierte Aufrufe nachspielen und auszählen (Entscheidung 224). Testdaten, kein Netz."""

import json
from pathlib import Path

from tests.helpers import FakeTransport, chat
from valar import db, journal, replay

KEY = "geheimer-schluessel-123"
SETTINGS = """
[[provider]]
name = "frei"
base_url = "https://openrouter.ai/api/v1"
model = "m"
key_env = "FREI_KEY"
output = "json_schema"
"""
MESSAGES = [
    {"role": "system", "content": "alter Rahmen-Prompt"},
    {"role": "user", "content": "# Zustand\nOrt: Zunfthaus\n\n# Eingabe des Spielers\nIch gehe hinaus.\n\n# Aktionen\n- move"},
]


def setup(data_dir: Path) -> None:
    data_dir.mkdir()
    (data_dir / "modelle.toml").write_text(SETTINGS, encoding="utf-8")
    db.init_schema(db.connect(data_dir / "spiel.db"))
    log = journal.Journal(data_dir / journal.JOURNAL_FILE)
    call = {"provider": "frei", "model": "m", "attempt": 1, "duration_ms": 5, "outcome": "ok", "input_tokens": 1,
            "output_tokens": 1, "messages": json.dumps(MESSAGES, ensure_ascii=False)}
    log.record_call(player_id="figur", request_id="zug-1", purpose="aktion", **call)
    log.record_call(player_id="figur", request_id="zug-2", purpose="aktion", **call)  # nicht gemeldet
    log.report(request_id="meldung-1", player_id="figur", turn="zug-1", kind="unlogisch", note="nur erzählt",
               player_input="Ich gehe hinaus.", narration="…")


def test_reported_calls_are_replayed_per_prompt_and_counted(tmp_path, capsys):
    data_dir = tmp_path / "daten"
    setup(data_dir)
    (tmp_path / "alt.md").write_text("ALT", encoding="utf-8")
    (tmp_path / "neu.md").write_text("NEU", encoding="utf-8")
    narrate = chat(json.dumps({"action": {"type": "narrate", "text": "Du trittst hinaus."}}))
    move = chat(json.dumps({"action": {"type": "move", "destination_id": "boomtown"}}))
    transport = FakeTransport(narrate, narrate, move, chat("```json\n{\"action\": {\"type\": \"move\", \"destination_id\": \"boomtown\"}}\n```"))

    results = replay.replay_calls(
        data_dir, reported=True, prompts=[tmp_path / "alt.md", tmp_path / "neu.md"], rounds=2,
        transport=transport, environ={"FREI_KEY": KEY},
    )

    assert [(r.purpose, r.player_input) for r in results] == [("aktion", "Ich gehe hinaus.")]
    counts = {Path(name).name: dict(c) for name, c in results[0].counts.items()}
    assert counts == {"alt.md": {"narrate": 2}, "neu.md": {"move boomtown": 2}}
    systems = [r["body"]["messages"][0]["content"] for r in transport.requests]
    assert systems == ["ALT", "ALT", "NEU", "NEU"] and transport.requests[0]["body"]["messages"][1] == MESSAGES[1]
    assert transport.requests[0]["body"]["provider"] == {"require_parameters": True}  # wie im Spiel
    with db.connect(data_dir / "spiel.db") as conn:
        assert conn.execute("SELECT count(*) FROM model_call").fetchone()[0] == 4  # zählt ins Budget
    assert KEY not in capsys.readouterr().out
