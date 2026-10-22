"""Schema und Verbindung. Geschrieben wird Spielzustand nur in apply.py."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from valar.rules import ATTRIBUTES

SCHEMA_VERSION = 8

# Spaltenname je Attribut (Attribute stehen als Spalten in `player`, Standardwert 10).
ATTRIBUTE_COLUMNS = {attribute: f"attr_{attribute.lower()}" for attribute in ATTRIBUTES}

# Alle Zeilen gehören zu einer Kampagne; Spieler haben zusätzlich eine player_id.
# Geld in Pfennig als ganze Zahl (1 Gulden = 100 Pfennig).
SCHEMA = """
-- `seed`: Grundlage für den Zufall des Codes (Entscheidung 62), bei der Anlage gesetzt.
CREATE TABLE IF NOT EXISTS campaign (
    campaign_id  TEXT NOT NULL PRIMARY KEY,
    revision     INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    game_minutes INTEGER NOT NULL DEFAULT 0 CHECK (game_minutes >= 0),
    seed         INTEGER NOT NULL
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
    arrived_at    INTEGER NOT NULL DEFAULT 0 CHECK (arrived_at >= 0),
    hunger        INTEGER NOT NULL DEFAULT 0 CHECK (hunger BETWEEN 0 AND 4),
    fatigue_debt  INTEGER NOT NULL DEFAULT 0 CHECK (fatigue_debt BETWEEN 0 AND 4),
    awake_since   INTEGER NOT NULL DEFAULT 0 CHECK (awake_since >= 0),
    PRIMARY KEY (campaign_id, player_id)
) STRICT;

-- `hunger`: Stufe 0 bis 4, ändert sich nur um Mitternacht. Erschöpfung wird nicht gespeichert, sondern aus
-- `fatigue_debt` (offene Stufen nach dem letzten Schlaf) und den Stunden seit `awake_since` gerechnet.

-- Erzählte Knappheit (Entscheidung 123): ein Ding an einem Ort (Ort-Seite) ist bis `until` knapp oder weg.
CREATE TABLE IF NOT EXISTS scarcity (
    scarcity_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id   TEXT NOT NULL REFERENCES campaign (campaign_id),
    location_page TEXT NOT NULL,
    page_id       TEXT NOT NULL,
    level         TEXT NOT NULL CHECK (level IN ('scarce', 'gone')),
    until         INTEGER NOT NULL CHECK (until >= 0),
    reason        TEXT NOT NULL
) STRICT;

-- `arrived_at`: Spielminute der Ankunft am Ort. Daraus und aus der letzten Mitternacht
-- leitet der Code die laufende Szene ab (rules.scene_start); um Mitternacht wird nichts geschrieben.

-- Fehlende Zeile = Skillwert 0. `ticks` zählen zum nächsten Punkt, `ups` sind die Aufstiege
-- (Level = 1 + Summe der Aufstiege / 10, wird nicht gespeichert).
-- `ticked_scene`: Szene (ihr Beginn in Spielminuten), in der der Skill zuletzt Ticks gebracht hat.
CREATE TABLE IF NOT EXISTS player_skill (
    campaign_id TEXT NOT NULL,
    player_id   TEXT NOT NULL,
    skill_id    TEXT NOT NULL,
    value       INTEGER NOT NULL DEFAULT 0 CHECK (value BETWEEN 0 AND 100),
    ticks       INTEGER NOT NULL DEFAULT 0 CHECK (ticks >= 0),
    ups         INTEGER NOT NULL DEFAULT 0 CHECK (ups >= 0),
    ticked_scene INTEGER CHECK (ticked_scene >= 0),
    PRIMARY KEY (campaign_id, player_id, skill_id),
    FOREIGN KEY (campaign_id, player_id) REFERENCES player (campaign_id, player_id)
) STRICT;

-- Die offene Probe. Der Primärschlüssel erzwingt: höchstens eine je Kampagne.
-- `minutes` werden beim Wurf gutgeschrieben, `scene` ist die Szene beim Öffnen.
-- `on_success_json`: Folgen eines Erfolgs, beim Öffnen festgehalten (Entscheidung 61).
-- `npc_roll_json`: bei der Vergleichsprobe der Wurf des NPCs; sein Gesamt ist `dc` (Entscheidung 63).
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
    minutes          INTEGER NOT NULL CHECK (minutes > 0),
    scene            INTEGER NOT NULL CHECK (scene >= 0),
    on_success_json  TEXT NOT NULL DEFAULT '[]',
    npc_roll_json    TEXT,
    FOREIGN KEY (campaign_id, player_id) REFERENCES player (campaign_id, player_id)
) STRICT;

-- Wissensstufen, die eine Spielfigur durch Untersuchen kennt (Entscheidung 64), dauerhaft.
-- Eine Stufe ist auf ihrer Wiki-Seite durch Skill und Schwierigkeit bestimmt.
CREATE TABLE IF NOT EXISTS player_knowledge (
    campaign_id TEXT NOT NULL,
    player_id   TEXT NOT NULL,
    page_id     TEXT NOT NULL,
    skill_id    TEXT NOT NULL,
    difficulty  TEXT NOT NULL,
    PRIMARY KEY (campaign_id, player_id, page_id, skill_id, difficulty),
    FOREIGN KEY (campaign_id, player_id) REFERENCES player (campaign_id, player_id)
) STRICT;

-- NPCs (Entscheidung 11): gespeichert erst, wenn die Spielfigur sie anspricht. Werte aus der Vorlage
-- (NPC-Typ-Seite im Wiki) und dem Zufall des Codes; das Modell setzt keine.
CREATE TABLE IF NOT EXISTS npc (
    campaign_id   TEXT NOT NULL REFERENCES campaign (campaign_id),
    npc_id        TEXT NOT NULL,
    template_id   TEXT NOT NULL,
    name          TEXT NOT NULL,
    description   TEXT NOT NULL,
    location_id   TEXT NOT NULL,
    money_pennies INTEGER NOT NULL DEFAULT 0 CHECK (money_pennies >= 0),
    attr_str      INTEGER NOT NULL DEFAULT 10 CHECK (attr_str BETWEEN 1 AND 20),
    attr_ges      INTEGER NOT NULL DEFAULT 10 CHECK (attr_ges BETWEEN 1 AND 20),
    attr_kon      INTEGER NOT NULL DEFAULT 10 CHECK (attr_kon BETWEEN 1 AND 20),
    attr_int      INTEGER NOT NULL DEFAULT 10 CHECK (attr_int BETWEEN 1 AND 20),
    attr_wei      INTEGER NOT NULL DEFAULT 10 CHECK (attr_wei BETWEEN 1 AND 20),
    attr_cha      INTEGER NOT NULL DEFAULT 10 CHECK (attr_cha BETWEEN 1 AND 20),
    PRIMARY KEY (campaign_id, npc_id)
) STRICT;

CREATE TABLE IF NOT EXISTS npc_skill (
    campaign_id TEXT NOT NULL,
    npc_id      TEXT NOT NULL,
    skill_id    TEXT NOT NULL,
    value       INTEGER NOT NULL CHECK (value BETWEEN 0 AND 100),
    PRIMARY KEY (campaign_id, npc_id, skill_id),
    FOREIGN KEY (campaign_id, npc_id) REFERENCES npc (campaign_id, npc_id)
) STRICT;

-- Weiche Fakten (Entscheidungen 8 und 107): was eine Spielfigur erfahren hat, mit Quelle.
CREATE TABLE IF NOT EXISTS fact (
    fact_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id   TEXT NOT NULL,
    player_id     TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('fact', 'rumor')),
    text          TEXT NOT NULL CHECK (length(text) BETWEEN 1 AND 300),
    subject       TEXT,
    source_npc_id TEXT,
    game_minutes  INTEGER NOT NULL CHECK (game_minutes >= 0),
    FOREIGN KEY (campaign_id, player_id) REFERENCES player (campaign_id, player_id),
    FOREIGN KEY (campaign_id, source_npc_id) REFERENCES npc (campaign_id, npc_id)
) STRICT;

-- Kampagnen-Seiten im Wiki (Entscheidungen 91 und 108): gelten nur in dieser Kampagne.
-- Vorkommen und Wert setzt der Code aus der Seitenvorlage.
CREATE TABLE IF NOT EXISTS campaign_page (
    campaign_id   TEXT NOT NULL REFERENCES campaign (campaign_id),
    page_id       TEXT NOT NULL,
    template      TEXT NOT NULL,
    type          TEXT NOT NULL,
    title         TEXT NOT NULL,
    text          TEXT NOT NULL CHECK (length(text) BETWEEN 1 AND 1500),
    location_page TEXT,
    frequency     TEXT CHECK (frequency IN ('common', 'uncommon', 'rare', 'very_rare')),
    value_pennies INTEGER CHECK (value_pennies >= 0),
    gather_skill  TEXT,
    PRIMARY KEY (campaign_id, page_id)
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
-- `context_json`: welche Seiten in welcher Stufe ins Modell gingen und was wegfiel (Entscheidung 143).
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
    context_json    TEXT,
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


@contextmanager
def snapshot(conn: sqlite3.Connection) -> Iterator[None]:
    """Nur lesen, in einer Transaktion: Alle Abfragen darin sehen denselben Stand."""
    with read_only(conn):
        conn.execute("BEGIN")
        try:
            yield
        finally:
            conn.execute("ROLLBACK")
