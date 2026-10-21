"""Schema und Verbindung. Geschrieben wird Spielzustand nur in apply.py."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1

# Alle Zeilen gehören zu einer Kampagne; Spieler haben zusätzlich eine player_id.
# Geld in Pfennig als ganze Zahl (1 Gulden = 100 Pfennig).
SCHEMA = """
CREATE TABLE IF NOT EXISTS campaign (
    campaign_id  TEXT NOT NULL PRIMARY KEY,
    revision     INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    game_minutes INTEGER NOT NULL DEFAULT 0 CHECK (game_minutes >= 0)
) STRICT;

CREATE TABLE IF NOT EXISTS player (
    campaign_id   TEXT NOT NULL REFERENCES campaign (campaign_id),
    player_id     TEXT NOT NULL,
    name          TEXT NOT NULL,
    location_id   TEXT NOT NULL,
    money_pennies INTEGER NOT NULL DEFAULT 0 CHECK (money_pennies >= 0),
    PRIMARY KEY (campaign_id, player_id)
) STRICT;

CREATE TABLE IF NOT EXISTS inventory (
    campaign_id TEXT NOT NULL,
    player_id   TEXT NOT NULL,
    item_id     TEXT NOT NULL,
    quantity    INTEGER NOT NULL CHECK (quantity >= 0),
    PRIMARY KEY (campaign_id, player_id, item_id),
    FOREIGN KEY (campaign_id, player_id) REFERENCES player (campaign_id, player_id)
) STRICT;

CREATE TABLE IF NOT EXISTS quest_state (
    campaign_id TEXT NOT NULL,
    player_id   TEXT NOT NULL,
    quest_id    TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('active', 'completed')),
    PRIMARY KEY (campaign_id, player_id, quest_id),
    FOREIGN KEY (campaign_id, player_id) REFERENCES player (campaign_id, player_id)
) STRICT;

-- Spielzug-Protokoll. Abgelehnte Züge stehen auch hier, ändern aber keine Revision.
CREATE TABLE IF NOT EXISTS turn (
    turn_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id     TEXT NOT NULL REFERENCES campaign (campaign_id),
    request_id      TEXT NOT NULL,
    player_id       TEXT NOT NULL,
    action_json     TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('accepted', 'rejected')),
    reject_code     TEXT,
    message         TEXT,
    effects_json    TEXT NOT NULL,
    revision_before INTEGER NOT NULL,
    revision_after  INTEGER NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (campaign_id, request_id),
    CHECK ((status = 'rejected') = (reject_code IS NOT NULL)),
    CHECK ((status = 'accepted' AND revision_after = revision_before + 1)
        OR (status = 'rejected' AND revision_after = revision_before))
) STRICT;
"""


def connect(path: str | Path) -> sqlite3.Connection:
    """Öffnet die Datenbank. Transaktionen steuert der Code selbst (BEGIN/COMMIT/ROLLBACK)."""
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Legt das Schema an, ganz oder gar nicht. Eine fremde Schema-Version bricht ab."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version not in (0, SCHEMA_VERSION):
        raise RuntimeError(f"Unbekannte Schema-Version {version}, erwartet {SCHEMA_VERSION}")
    conn.executescript(f"BEGIN;{SCHEMA}PRAGMA user_version = {SCHEMA_VERSION};COMMIT;")


@contextmanager
def read_only(conn: sqlite3.Connection) -> Iterator[None]:
    """Jeder Schreibversuch auf dieser Verbindung scheitert. Hier laufen die Resolver."""
    conn.execute("PRAGMA query_only = ON")
    try:
        yield
    finally:
        conn.execute("PRAGMA query_only = OFF")
