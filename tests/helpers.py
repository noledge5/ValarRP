import sqlite3

CAMPAIGN = "campaign-1"
PLAYER = "player-1"
START_MONEY = 30  # Pfennig, bewusst nicht 0: die Belohnung wird addiert, nicht gesetzt


def assert_only_log_changed(before: dict[str, list[tuple]], after: dict[str, list[tuple]]) -> None:
    """Abgelehnt heißt: Zustand, Zeit und Revision unverändert, nur ein Protokolleintrag mehr."""
    state_tables = set(before) - {"turn", "sqlite_sequence"}
    assert {t: after[t] for t in state_tables} == {t: before[t] for t in state_tables}
    assert len(after["turn"]) == len(before["turn"]) + 1
    assert after["turn"][:-1] == before["turn"]  # bisherige Einträge unberührt


def dump_state(conn: sqlite3.Connection) -> dict[str, list[tuple]]:
    """Kompletter Inhalt aller Tabellen, zum Vergleichen vorher/nachher."""
    tables = [
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
    ]
    return {
        table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        for table in tables
    }
