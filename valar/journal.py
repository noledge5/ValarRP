"""Protokoll und Melden (Entscheidungen 200, 204 und 205 bis 209).

Je Modellaufruf stehen Prompt, Antwort, Dauer und Fehler im Protokoll, nie ein Schlüssel. Dazu kommen Meldungen des
Spielers zu einem Zug (Melde-Knopf) und Fehler, die die Seite selbst aus dem Browser meldet. Alles bleibt 30 Tage.
Eine Sitzung ist Spiel zwischen Pausen über 30 Minuten. Der Export liefert alles seit dem letzten Export als Datei,
gedacht für ein privates zweites Repo; nichts davon gehört ins öffentliche.

Das Protokoll ist kein Spielzustand: Es liegt in einer eigenen Datei (`protokoll.db`) neben dem Spielstand, mit
eigener Schema-Version. Der Spielstand bleibt dabei unberührt (Entscheidung 205). Scheitert das Protokoll, läuft das
Spiel weiter; ein Modellaufruf scheitert nie am Protokoll.
"""

import json
import os
import sqlite3
import sys
import unicodedata
from collections.abc import Callable, Iterable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

JOURNAL_FILE = "protokoll.db"
SCHEMA_VERSION = 1
FORMAT = "valar-protokoll"
KEEP = timedelta(days=30)
SESSION_PAUSE = timedelta(minutes=30)
REPORT_KINDS = {"fehler": "Fehler", "unlogisch": "Unlogisch", "zu_hart": "Zu hart", "zu_leicht": "Zu leicht", "toll": "Toll"}
CLIENT_ERROR_KINDS = {"skript": "Skriptfehler", "verbindung": "Verbindung"}
MAX_NOTE = 300  # ein Satz zur Meldung
MAX_MESSAGE = 500
MAX_SOURCE = 300
MAX_STACK = 2000
MAX_AGENT = 300
MAX_ERROR_BODY = 500  # so viel einer Fehlerantwort des Anbieters bleibt stehen
MAX_CLIENT_ERRORS_PER_HOUR = 100  # mehr nimmt der Server nicht an: Eine Schleife im Skript füllt nicht die Platte

SCHEMA = """
CREATE TABLE IF NOT EXISTS session (
    session_no  INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    last_at     TEXT NOT NULL
) STRICT;
-- Ein Aufruf beim Anbieter. `request_id` ist die des Formulars, also der Zug im Chat; `messages` als JSON.
CREATE TABLE IF NOT EXISTS model_call (
    call_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at             TEXT NOT NULL,
    session_no     INTEGER NOT NULL,
    player_id      TEXT,
    request_id     TEXT,
    purpose        TEXT NOT NULL,
    provider       TEXT NOT NULL,
    model          TEXT NOT NULL,
    attempt        INTEGER NOT NULL,
    duration_ms    INTEGER NOT NULL CHECK (duration_ms >= 0),
    outcome        TEXT NOT NULL CHECK (outcome IN ('ok', 'invalid', 'quota', 'error')),
    http_status    INTEGER,
    error          TEXT,
    input_tokens   INTEGER NOT NULL,
    output_tokens  INTEGER NOT NULL,
    messages       TEXT NOT NULL,
    reply          TEXT
) STRICT;
CREATE INDEX IF NOT EXISTS model_call_at ON model_call (at);
-- Meldung zu einem Zug; Eingabe und Erzählung stehen als Abschrift dabei, damit der Export für sich lesbar ist.
CREATE TABLE IF NOT EXISTS report (
    report_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id    TEXT NOT NULL UNIQUE,
    at            TEXT NOT NULL,
    session_no    INTEGER NOT NULL,
    player_id     TEXT NOT NULL,
    turn          TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('fehler', 'unlogisch', 'zu_hart', 'zu_leicht', 'toll')),
    note          TEXT NOT NULL,
    player_input  TEXT NOT NULL,
    narration     TEXT
) STRICT;
CREATE INDEX IF NOT EXISTS report_turn ON report (player_id, turn);
CREATE TABLE IF NOT EXISTS client_error (
    error_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id  TEXT NOT NULL UNIQUE,
    at          TEXT NOT NULL,
    session_no  INTEGER NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('skript', 'verbindung')),
    message     TEXT NOT NULL,
    page        TEXT NOT NULL,
    source      TEXT NOT NULL,
    line        INTEGER,
    col         INTEGER,
    stack       TEXT NOT NULL,
    agent       TEXT NOT NULL
) STRICT;
CREATE INDEX IF NOT EXISTS client_error_at ON client_error (at);
-- Je Export die Bereiche der Nummern; der nächste beginnt dahinter.
CREATE TABLE IF NOT EXISTS export (
    export_no    INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id   TEXT NOT NULL UNIQUE,
    at           TEXT NOT NULL,
    call_from    INTEGER NOT NULL,
    call_to      INTEGER NOT NULL,
    report_from  INTEGER NOT NULL,
    report_to    INTEGER NOT NULL,
    error_from   INTEGER NOT NULL,
    error_to     INTEGER NOT NULL
) STRICT;
"""


class JournalError(RuntimeError):
    """Das Protokoll ist nicht nutzbar (Datei kaputt, fremde Version, keine Rechte)."""


class DuplicateRequest(ValueError):
    """Dieselbe request_id kam schon mit anderem Inhalt."""


@dataclass(frozen=True)
class Pending:
    """Was seit dem letzten Export dazukam."""

    calls: int
    reports: int
    client_errors: int
    sessions: int
    last_export_no: int | None
    last_export_at: str | None


@dataclass(frozen=True)
class ExportFile:
    export_no: int
    name: str
    body: bytes


class Journal:
    """Das Protokoll in einer eigenen SQLite-Datei. Je Aufruf eine eigene Verbindung, wie im Webserver üblich."""

    def __init__(self, path: str | Path, *, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.path = Path(path)
        self.now = now
        self.problem: str | None = None
        try:
            with closing(self._connect()) as conn:
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, SCHEMA_VERSION):
                    raise JournalError(f"Unbekannte Version {version} in {self.path.name}, erwartet {SCHEMA_VERSION}")
                conn.executescript(f"BEGIN;{SCHEMA}PRAGMA user_version = {SCHEMA_VERSION};COMMIT;")
            os.chmod(self.path, 0o600)  # Prompts und Meldungen: nur für den Server lesbar, wie die Schlüsseldatei
            self.purge()
        except (sqlite3.Error, OSError, JournalError) as error:
            self.problem = f"{self.path.name} ist nicht nutzbar ({type(error).__name__}); gespielt wird ohne Protokoll."
            print(f"Protokoll: {error}", file=sys.stderr)

    @property
    def usable(self) -> bool:
        return self.problem is None

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _stamp(self) -> str:
        return stamp(self.now())

    # --- Sitzungen und Aufbewahrung ---

    def touch(self) -> int | None:
        """Gespielt wird jetzt: die laufende Sitzung, oder nach einer Pause über 30 Minuten eine neue."""
        if not self.usable:
            return None
        try:
            with closing(self._connect()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    number = self._session(conn)
                    conn.execute("COMMIT")
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
        except (sqlite3.Error, OSError) as error:
            print(f"Protokoll: Sitzung nicht vermerkt ({type(error).__name__}: {error})", file=sys.stderr)
            return None
        return number

    def _session(self, conn: sqlite3.Connection) -> int:
        """In einer offenen Transaktion: Nummer der Sitzung zu jetzt, neu nach einer langen Pause."""
        now = self.now()
        row = conn.execute("SELECT session_no, last_at FROM session ORDER BY session_no DESC LIMIT 1").fetchone()
        if row is not None and now - parse(row["last_at"]) <= SESSION_PAUSE:
            conn.execute("UPDATE session SET last_at = MAX(last_at, ?) WHERE session_no = ?", (stamp(now), row["session_no"]))
            return row["session_no"]
        self._purge(conn)  # mit jeder neuen Sitzung wird aufgeräumt, auch wenn der Server wochenlang läuft
        cursor = conn.execute("INSERT INTO session (started_at, last_at) VALUES (?, ?)", (stamp(now), stamp(now)))
        return cursor.lastrowid

    def purge(self) -> None:
        """Löscht, was älter als 30 Tage ist. Die Bereiche früherer Exporte bleiben, sie sind nur Zahlen."""
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._purge(conn)
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

    def _purge(self, conn: sqlite3.Connection) -> None:
        cutoff = stamp(self.now() - KEEP)
        for table in ("model_call", "report", "client_error"):
            conn.execute(f"DELETE FROM {table} WHERE at < ?", (cutoff,))
        conn.execute("DELETE FROM session WHERE last_at < ?", (cutoff,))

    def _insert(self, sql: str, values: tuple) -> bool:
        """Ein Eintrag mit der Sitzung von jetzt; False, wenn es ihn unter dieser request_id schon gibt."""
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                session = self._session(conn)
                cursor = conn.execute(sql, (self._stamp(), session, *values))
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        return cursor.rowcount == 1

    # --- Modellaufrufe ---

    def calls(self, *, player_id: str | None = None, request_id: str | None = None) -> "CallRecorder":
        """Für den Modell-Client: schreibt jeden Aufruf mit Figur und Zug."""
        return CallRecorder(self, player_id, request_id)

    def record_call(self, *, player_id: str | None, request_id: str | None, **call) -> None:
        """Ein Modellaufruf. Scheitert das Schreiben, steht es nur auf stderr; der Aufruf selbst gilt."""
        if not self.usable:
            return
        try:
            self._insert(
                "INSERT INTO model_call (at, session_no, player_id, request_id, purpose, provider, model, attempt,"
                " duration_ms, outcome, http_status, error, input_tokens, output_tokens, messages, reply)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    player_id,
                    request_id,
                    call["purpose"],
                    call["provider"],
                    call["model"],
                    call["attempt"],
                    max(int(call["duration_ms"]), 0),
                    call["outcome"],
                    call.get("http_status"),
                    call.get("error"),
                    call["input_tokens"],
                    call["output_tokens"],
                    call["messages"],
                    call.get("reply"),
                ),
            )
        except (sqlite3.Error, OSError) as error:
            print(f"Protokoll: Modellaufruf nicht gespeichert ({type(error).__name__}: {error})", file=sys.stderr)

    # --- Meldungen ---

    def report(
        self,
        *,
        request_id: str,
        player_id: str,
        turn: str,
        kind: str,
        note: str,
        player_input: str,
        narration: str | None,
    ) -> bool:
        """Eine Meldung zu einem Zug. Dieselbe request_id zählt einmal; mit anderem Inhalt ist sie ein Konflikt."""
        if kind not in REPORT_KINDS:
            raise ValueError(kind)
        if self._insert(
            "INSERT OR IGNORE INTO report (at, session_no, request_id, player_id, turn, kind, note, player_input, narration)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (request_id, player_id, turn, kind, note, player_input, narration),
        ):
            return True
        with closing(self._connect()) as conn:
            stored = conn.execute(
                "SELECT player_id, turn, kind, note FROM report WHERE request_id = ?", (request_id,)
            ).fetchone()
        if tuple(stored) != (player_id, turn, kind, note):
            raise DuplicateRequest(request_id)
        return False

    def reports_for(self, player_id: str, turns: Iterable[str]) -> dict[str, str]:
        """Je Zug die Art der letzten Meldung, für die Anzeige im Verlauf."""
        turns = list(turns)
        if not self.usable or not turns:
            return {}
        marks = ",".join("?" * len(turns))
        try:
            with closing(self._connect()) as conn:
                rows = conn.execute(
                    f"SELECT turn, kind FROM report WHERE player_id = ? AND turn IN ({marks}) ORDER BY report_id",
                    (player_id, *turns),
                ).fetchall()
        except (sqlite3.Error, OSError):
            return {}  # nur Anzeige: ohne Protokoll fehlt die Markierung, gespielt wird trotzdem
        return {row["turn"]: row["kind"] for row in rows}

    # --- Fehler aus dem Browser ---

    def client_error(
        self,
        *,
        request_id: str,
        kind: str,
        message: str,
        page: str,
        source: str,
        line: int | None,
        col: int | None,
        stack: str,
        agent: str,
    ) -> bool:
        """Ein Fehler, den die Seite gemeldet hat. Über der Grenze je Stunde wird nichts mehr angenommen."""
        if kind not in CLIENT_ERROR_KINDS:
            raise ValueError(kind)
        with closing(self._connect()) as conn:
            recent = conn.execute(
                "SELECT COUNT(*) FROM client_error WHERE at >= ?", (stamp(self.now() - timedelta(hours=1)),)
            ).fetchone()[0]
        if recent >= MAX_CLIENT_ERRORS_PER_HOUR:
            return False
        return self._insert(
            "INSERT OR IGNORE INTO client_error (at, session_no, request_id, kind, message, page, source, line, col, stack, agent)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (request_id, kind, message, page, source, line, col, stack, agent),
        )

    # --- Export ---

    def pending(self) -> Pending:
        with closing(self._connect()) as conn:
            last = conn.execute("SELECT * FROM export ORDER BY export_no DESC LIMIT 1").fetchone()
            calls, reports, errors = _upper(last)
            counts = [
                conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {key} > ?", (after,)).fetchone()[0]
                for table, key, after in (("model_call", "call_id", calls), ("report", "report_id", reports), ("client_error", "error_id", errors))
            ]
            sessions = conn.execute(
                "SELECT COUNT(DISTINCT session_no) FROM ("
                " SELECT session_no FROM model_call WHERE call_id > ? UNION ALL"
                " SELECT session_no FROM report WHERE report_id > ? UNION ALL"
                " SELECT session_no FROM client_error WHERE error_id > ?)",
                (calls, reports, errors),
            ).fetchone()[0]
        return Pending(*counts, sessions, last["export_no"] if last else None, last["at"] if last else None)

    def export(self, request_id: str) -> ExportFile:
        """Alles seit dem letzten Export als neuer Export. Dieselbe request_id liefert denselben Export noch einmal."""
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute("SELECT export_no FROM export WHERE request_id = ?", (request_id,)).fetchone()
                if row is None:
                    last = conn.execute("SELECT * FROM export ORDER BY export_no DESC LIMIT 1").fetchone()
                    calls, reports, errors = _upper(last)
                    top = [
                        conn.execute(f"SELECT COALESCE(MAX({key}), 0) FROM {table}").fetchone()[0]
                        for table, key in (("model_call", "call_id"), ("report", "report_id"), ("client_error", "error_id"))
                    ]
                    cursor = conn.execute(
                        "INSERT INTO export (request_id, at, call_from, call_to, report_from, report_to, error_from, error_to)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (request_id, self._stamp(), calls, max(top[0], calls), reports, max(top[1], reports), errors, max(top[2], errors)),
                    )
                    number = cursor.lastrowid
                else:
                    number = row["export_no"]
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        return self.export_again(number)

    def export_again(self, export_no: int) -> ExportFile:
        """Ein früherer Export noch einmal, so weit seine Einträge noch nicht nach 30 Tagen gelöscht sind."""
        with closing(self._connect()) as conn:
            mark = conn.execute("SELECT * FROM export WHERE export_no = ?", (export_no,)).fetchone()
            if mark is None:
                raise KeyError(export_no)
            calls = conn.execute(
                "SELECT * FROM model_call WHERE call_id > ? AND call_id <= ? ORDER BY call_id",
                (mark["call_from"], mark["call_to"]),
            ).fetchall()
            reports = conn.execute(
                "SELECT * FROM report WHERE report_id > ? AND report_id <= ? ORDER BY report_id",
                (mark["report_from"], mark["report_to"]),
            ).fetchall()
            errors = conn.execute(
                "SELECT * FROM client_error WHERE error_id > ? AND error_id <= ? ORDER BY error_id",
                (mark["error_from"], mark["error_to"]),
            ).fetchall()
            used = sorted({row["session_no"] for row in (*calls, *reports, *errors)})
            sessions = conn.execute(
                f"SELECT * FROM session WHERE session_no IN ({','.join('?' * len(used))}) ORDER BY session_no", used
            ).fetchall() if used else []
        data = {
            "format": FORMAT,
            "version": SCHEMA_VERSION,
            "export": mark["export_no"],
            "created_at": mark["at"],
            "keep_days": KEEP.days,
            "session_pause_minutes": int(SESSION_PAUSE.total_seconds() // 60),
            "sessions": [
                {"session": s["session_no"], "started_at": s["started_at"], "last_at": s["last_at"]} for s in sessions
            ],
            "calls": [_call(row) for row in calls],
            "reports": [
                {
                    "id": r["report_id"], "at": r["at"], "session": r["session_no"], "player_id": r["player_id"],
                    "turn": r["turn"], "kind": r["kind"], "note": r["note"], "player_input": r["player_input"],
                    "narration": r["narration"],
                }
                for r in reports
            ],
            "client_errors": [
                {
                    "id": r["error_id"], "at": r["at"], "session": r["session_no"], "kind": r["kind"],
                    "message": r["message"], "page": r["page"], "source": r["source"], "line": r["line"],
                    "col": r["col"], "stack": r["stack"], "agent": r["agent"],
                }
                for r in errors
            ],
        }
        day = mark["at"][:10]
        body = json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
        return ExportFile(mark["export_no"], f"valar-protokoll-{day}-{mark['export_no']}.json", body)


class CallRecorder:
    """Ein Protokoll, an Figur und Zug gebunden; der Modell-Client kennt beides nicht."""

    def __init__(self, journal: Journal, player_id: str | None, request_id: str | None) -> None:
        self.journal = journal
        self.player_id = player_id
        self.request_id = request_id

    def record(self, **call) -> None:
        self.journal.record_call(player_id=self.player_id, request_id=self.request_id, **call)


# --- Hilfen ---


def stamp(moment: datetime) -> str:
    """Zeit als Text in UTC, sekundengenau; so sortiert und vergleicht SQLite richtig."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def clean(text: str, limit: int) -> str:
    """Text aus dem Browser: ohne Steuerzeichen (Zeilenumbrüche bleiben), gekürzt auf die Grenze."""
    kept = "".join(c for c in text if c == "\n" or unicodedata.category(c) not in ("Cc", "Cf"))
    return kept.strip()[:limit]


def _upper(last: sqlite3.Row | None) -> tuple[int, int, int]:
    """Bis wohin der letzte Export reichte; ohne Export ab dem Anfang."""
    if last is None:
        return 0, 0, 0
    return last["call_to"], last["report_to"], last["error_to"]


def _call(row: sqlite3.Row) -> dict:
    try:
        messages = json.loads(row["messages"])
    except ValueError:
        messages = row["messages"]
    return {
        "id": row["call_id"], "at": row["at"], "session": row["session_no"], "player_id": row["player_id"],
        "turn": row["request_id"], "purpose": row["purpose"], "provider": row["provider"], "model": row["model"],
        "attempt": row["attempt"], "duration_ms": row["duration_ms"], "outcome": row["outcome"],
        "http_status": row["http_status"], "error": row["error"], "input_tokens": row["input_tokens"],
        "output_tokens": row["output_tokens"], "messages": messages, "reply": row["reply"],
    }
