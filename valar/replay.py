"""Protokollierte Modellaufrufe erneut schicken und auszählen (Entscheidung 224).

Misst, ob eine Prompt-Änderung wirkt: dieselben Nachrichten wie im Spiel, mit dem Rahmen-Prompt aus einer Datei,
mehrmals; gezählt wird, was zurückkommt (bei Aufruf A die gewählte Aktion). Ersetzt wird nur der Rahmen-Prompt;
was der Code in die Nachricht schrieb (Kontext, Aufgaben, Aktionen), bleibt wie im Protokoll. Eine Änderung am Code
der Nachricht zeigt sich also erst an Aufrufen, die danach protokolliert wurden. Das Schema des Zugs steht nicht im
Protokoll, darum wird nur gültiges JSON verlangt. Jeder Aufruf kostet wie im Spiel und zählt ins Budget; ist der
Stopp erreicht, geht nichts mehr hinaus. Schlüssel kommen wie im Spiel aus Umgebung oder Datenordner, nie in die
Ausgabe.

Aufruf:
    python -m valar.replay [--daten DATEN] [--aufruf ID ...] [--gemeldet] [--prompt DATEI ...] [--runden N]
                           [--anbieter NAME]

Ohne --aufruf und --gemeldet: die letzten 5 Aufrufe A. Ohne --prompt: der Rahmen-Prompt aus valar/spielleiter.md.
Mehrere --prompt vergleichen alt gegen neu, z. B. mit `git show HEAD~1:valar/spielleiter.md > alt.md`.
"""

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from valar import db, gm, journal, keys, llm

DEFAULT_LIMIT = 5
REPLAY_TIMEOUT_S = 60


@dataclass(frozen=True)
class Replayed:
    call_id: int
    purpose: str
    player_input: str
    counts: dict[str, Counter]  # je Prompt-Datei: was wie oft zurückkam


def select_calls(conn: sqlite3.Connection, call_ids: Sequence[int], reported: bool) -> list[sqlite3.Row]:
    """Die gewählten Aufrufe, älteste zuerst: nach ID, alle zu gemeldeten Zügen, sonst die letzten Aufrufe A."""
    if call_ids:
        marks = ",".join("?" * len(call_ids))
        return conn.execute(f"SELECT * FROM model_call WHERE call_id IN ({marks}) ORDER BY call_id", list(call_ids)).fetchall()
    if reported:
        return conn.execute(
            "SELECT m.* FROM model_call m JOIN report r ON r.turn = m.request_id AND r.player_id = m.player_id"
            " WHERE m.purpose IN ('aktion', 'erzaehlung', 'einstieg') ORDER BY m.call_id"
        ).fetchall()
    rows = conn.execute(
        "SELECT * FROM model_call WHERE purpose = 'aktion' ORDER BY call_id DESC LIMIT ?", (DEFAULT_LIMIT,)
    ).fetchall()
    return rows[::-1]


def player_input(messages: list[dict]) -> str:
    """Die Eingabe des Spielers aus der Nachricht, wie der Spielleiter sie gebaut hat."""
    text = messages[-1].get("content", "") if messages else ""
    _, _, rest = text.partition("# Eingabe des Spielers\n")
    return rest.split("\n\n", 1)[0].strip() or "?"


def summarize(text: str | None) -> str:
    """Was zurückkam, kurz: die Aktion (mit Ziel), oder bei einer Erzählung, wie viele Notizen und Personen."""
    try:
        data = json.loads(llm._strip_fences(text or ""))  # wie im Client: ein Codeblock ums JSON wird toleriert
    except ValueError:
        return "kein JSON"
    if not isinstance(data, dict):
        return "kein Objekt"
    action = data.get("action")
    if isinstance(action, dict):
        target = next((action[k] for k in ("destination_id", "npc_id", "page_id", "skill_id") if action.get(k)), None)
        return f"{action.get('type', '?')}{f' {target}' if target else ''}"
    if "text" in data:
        return f"erzählt, {len(data.get('notes') or [])} Notizen, {len(data.get('npcs') or [])} Personen"
    return "unbekannte Form"


def replay_calls(
    data_dir: Path,
    *,
    call_ids: Sequence[int] = (),
    reported: bool = False,
    prompts: Sequence[Path] = (),
    rounds: int = 3,
    provider_name: str | None = None,
    transport: llm.Transport | None = None,
    environ: Mapping[str, str] = os.environ,
    now: datetime | None = None,
) -> list[Replayed]:
    settings = keys.effective_settings(llm.load_settings(data_dir / "modelle.toml"), data_dir)
    env = keys.KeyStore(data_dir).environ(environ)
    providers = [p for p in settings.provider if env.get(p.key_env) and provider_name in (None, p.name)]
    if not providers:
        raise SystemExit("Kein Anbieter mit Schlüssel" + (f" namens {provider_name}" if provider_name else "") + ".")
    provider, key = providers[0], env[providers[0].key_env]
    transport = transport or llm.UrllibTransport()
    variants = {str(p): p.read_text(encoding="utf-8").strip() for p in prompts} or {"spielleiter.md": gm.load_prompt()}
    month = (now or datetime.now(UTC)).strftime("%Y-%m")

    with closing(db.connect(data_dir / journal.JOURNAL_FILE)) as log, closing(db.connect(data_dir / "spiel.db")) as game:
        ledger = llm.Ledger(game)
        results = []
        for row in select_calls(log, call_ids, reported):
            messages = json.loads(row["messages"])
            counts: dict[str, Counter] = {}
            for name, prompt in variants.items():
                counts[name] = Counter()
                for _ in range(rounds):
                    if ledger.spent_micro_eur(month) >= settings.budget.stop_eur * 1_000_000:
                        counts[name]["Budget-Stopp"] += 1
                        continue
                    counts[name][_ask(provider, key, transport, ledger, month, [{"role": "system", "content": prompt}, *messages[1:]])] += 1
            results.append(Replayed(row["call_id"], row["purpose"], player_input(messages), counts))
    return results


def _ask(provider: llm.ProviderSettings, key: str, transport: llm.Transport, ledger: llm.Ledger, month: str, messages: list[dict]) -> str:
    body: dict = {"model": provider.model, "messages": messages, "response_format": {"type": "json_object"}}
    if "openrouter.ai" in provider.base_url:
        body["provider"] = {"require_parameters": True}  # wie im Spiel (Entscheidung 214)
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        answer = transport.post(f"{provider.base_url}/chat/completions", headers, json.dumps(body).encode(), REPLAY_TIMEOUT_S)
    except llm.TransportError:
        ledger.record(month=month, provider=provider, input_tokens=0, output_tokens=0, outcome="error")
        return "keine Antwort"
    if answer.status != 200:
        ledger.record(month=month, provider=provider, input_tokens=0, output_tokens=0, outcome="error")
        return f"HTTP {answer.status}"
    try:
        data = json.loads(answer.body)
        text = data["choices"][0]["message"].get("content")
        usage = data.get("usage") or {}
    except (ValueError, KeyError, IndexError, TypeError):
        text, usage = None, {}
    ledger.record(
        month=month, provider=provider, outcome="ok",
        input_tokens=int(usage.get("prompt_tokens", 0)), output_tokens=int(usage.get("completion_tokens", 0)),
    )
    return summarize(text)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m valar.replay", description=__doc__.splitlines()[0])
    parser.add_argument("--daten", default="daten", type=Path, help="Datenordner mit protokoll.db, spiel.db, modelle.toml")
    parser.add_argument("--aufruf", type=int, nargs="*", default=[], help="IDs aus dem Protokoll (call_id)")
    parser.add_argument("--gemeldet", action="store_true", help="alle Aufrufe zu gemeldeten Zügen")
    parser.add_argument("--prompt", type=Path, action="append", default=[], help="Rahmen-Prompt als Datei; mehrmals für alt gegen neu")
    parser.add_argument("--runden", type=int, default=3, help="Wiederholungen je Aufruf und Prompt (Standard 3)")
    parser.add_argument("--anbieter", help="Name aus modelle.toml; sonst der erste mit Schlüssel")
    args = parser.parse_args(argv)
    results = replay_calls(
        args.daten, call_ids=args.aufruf, reported=args.gemeldet, prompts=args.prompt, rounds=args.runden,
        provider_name=args.anbieter,
    )
    if not results:
        print("Keine passenden Aufrufe im Protokoll.")
    for result in results:
        print(f"Aufruf {result.call_id} ({result.purpose}): {result.player_input}")
        for name, counts in result.counts.items():
            print(f"  {name}: " + ", ".join(f"{what} ×{n}" for what, n in counts.most_common()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
