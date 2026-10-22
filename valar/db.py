"""Schema und Verbindung. Geschrieben wird Spielzustand nur in apply.py."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from valar.rules import ATTRIBUTES

SCHEMA_VERSION = 2

# Spaltenname je Attribut (Attribute stehen als Spalten in `player`, Standardwert 10).
ATTRIBUTE_COLUMNS = {attribute: f"attr_{attribute.lower()}" for attribute in ATTRIBUTES}

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
    attr_str      INTEGER NOT NULL DEFAULT 10 CHECK (attr_str BETWEEN 1 AND 20),
    attr_ges      INTEGER NOT NULL DEFAULT 10 CHECK (attr_ges BETWEEN 1 AND 20),
    attr_kon      INTEGER NOT NULL DEFAULT 10 CHECK (attr_kon BETWEEN 1 AND 20),
    attr_int      INTEGER NOT NULL DEFAULT 10 CHECK (attr_int BETWEEN 1 AND 20),
    attr_wei      INTEGER NOT NULL DEFAULT 10 CHECK (attr_wei BETWEEN 1 AND 20),
    attr_cha      INTEGER NOT NULL DEFAULT 10 CHECK (attr_cha BETWEEN 1 AND 20),
    attribute_points INTEGER NOT NULL DEFAULT 0 CHECK (attribute_points >= 0),
    PRIMARY KEY (campaign_id, player_id)
) STRICT;

-- Fehlende Zeile = Skillwert 0. `ticks` zählen zum nächsten Punkt, `ups` sind die Aufstiege
-- (Level = 1 + Summe der Aufstiege / 10, wird nicht gespeichert).
CREATE TABLE IF NOT EXISTS player_skill (
    campaign_id TEXT NOT NULL,
    player_id   TEXT NOT NULL,
    skill_id    TEXT NOT NULL,
    value       INTEGER NOT NULL DEFAULT 0 CHECK (value BETWEEN 0 AND 100),
    ticks       INTEGER NOT NULL DEFAULT 0 CHECK (ticks >= 0),
    ups         INTEGER NOT NULL DEFAULT 0 CHECK (ups >= 0),
    PRIMARY KEY (campaign_id, player_id, skill_id),
    FOREIGN KEY (campaign_id, player_id) REFERENCES player (campaign_id, player_id)
) STRICT;

-- Die offene Probe. Der Primärschlüssel erzwingt: höchstens eine je Kampagne.
CREATE TABLE IF NOT EXISTS open_roll (
    campaign_id      TEXT NOT NULL PRIMARY KEY REFERENCES campaign (campaign_id),
    player_id        TEXT NOT NULL,
    skill_id         TEXT NOT NULL,
    difficulty       TEXT NOT NULL CHECK (difficulty IN
        ('DIFF-01', 'DIFF-02', 'DIFF-03', 'DIFF-04', 'DIFF-05', 'DIFF-06', 'DIFF-07')),
    dc               INTEGER NOT NULL CHECK (dc > 0),
    bonus            INTEGER NOT NULL,
    ticks_on_success INTEGER NOT NULL CHECK (ticks_on_success >= 0),
    reason           TEXT NOT NULL,
    FOREIGN KEY (campaign_id, player_id) REFERENCES player (campaign_id, player_id)
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
-- `awaiting_roll`: der Versuch hat eine Probe eröffnet (angenommen, Revision +1).
CREATE TABLE IF NOT EXISTS turn (
    turn_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id     TEXT NOT NULL REFERENCES campaign (campaign_id),
    request_id      TEXT NOT NULL,
    player_id       TEXT NOT NULL,
    action_json     TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('accepted', 'awaiting_roll', 'rejected')),
    reject_code     TEXT,
    message         TEXT,
    effects_json    TEXT NOT NULL,
    check_json      TEXT,
    revision_before INTEGER NOT NULL,
    revision_after  INTEGER NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (campaign_id, request_id),
    CHECK ((status = 'rejected') = (reject_code IS NOT NULL)),
    CHECK ((status IN ('accepted', 'awaiting_roll') AND revision_after = revision_before + 1)
        OR (status = 'rejected' AND revision_after = revision_before)),
    CHECK (check_json IS NULL OR status = 'accepted')
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
