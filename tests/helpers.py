import sqlite3

CAMPAIGN = "campaign-1"
PLAYER = "player-1"
START_MONEY = 30  # Pfennig, bewusst nicht 0: die Belohnung wird addiert, nicht gesetzt


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
