import sqlite3

from valar import apply
from valar.content import CRYSTAL_SHARD, GUILD_HALL, GUILD_SHARD_ORDER, Content, QuestDef
from valar.models import AddItems, SetQuestStatus, SubmitQuestItems
from valar.turns import submit_turn

CAMPAIGN = "campaign-1"
PLAYER = "player-1"
START_MONEY = 30  # Pfennig, bewusst nicht 0: die Belohnung wird addiert, nicht gesetzt


def shards(conn: sqlite3.Connection, campaign_id: str = CAMPAIGN, player_id: str = PLAYER) -> int:
    row = conn.execute(
        "SELECT quantity FROM inventory WHERE campaign_id = ? AND player_id = ? AND item_id = ?",
        (campaign_id, player_id, CRYSTAL_SHARD),
    ).fetchone()
    return row["quantity"] if row else 0


def money(conn: sqlite3.Connection, campaign_id: str = CAMPAIGN, player_id: str = PLAYER) -> int:
    return conn.execute(
        "SELECT money_pennies FROM player WHERE campaign_id = ? AND player_id = ?",
        (campaign_id, player_id),
    ).fetchone()["money_pennies"]


def quest_status(
    conn: sqlite3.Connection, campaign_id: str = CAMPAIGN, player_id: str = PLAYER
) -> str | None:
    row = conn.execute(
        "SELECT status FROM quest_state WHERE campaign_id = ? AND player_id = ? AND quest_id = ?",
        (campaign_id, player_id, GUILD_SHARD_ORDER),
    ).fetchone()
    return row["status"] if row else None


def campaign_row(conn: sqlite3.Connection, campaign_id: str = CAMPAIGN) -> sqlite3.Row:
    return conn.execute(
        "SELECT revision, game_minutes FROM campaign WHERE campaign_id = ?", (campaign_id,)
    ).fetchone()


def turn_count(conn: sqlite3.Connection, campaign_id: str = CAMPAIGN) -> int:
    return conn.execute("SELECT count(*) FROM turn WHERE campaign_id = ?", (campaign_id,)).fetchone()[0]


def last_turn(conn: sqlite3.Connection) -> sqlite3.Row:
    return conn.execute("SELECT * FROM turn ORDER BY turn_id DESC LIMIT 1").fetchone()


def submit(conn: sqlite3.Connection, request_id: str, expected_revision: int, **overrides):
    """Reicht die Abgabe des Standard-Spielers ein; `overrides` ersetzen einzelne Argumente."""
    request = {
        "campaign_id": CAMPAIGN,
        "player_id": PLAYER,
        "request_id": request_id,
        "expected_revision": expected_revision,
        "action": SubmitQuestItems(quest_id=GUILD_SHARD_ORDER),
    } | overrides
    return submit_turn(conn, **request)


def add_campaign(conn: sqlite3.Connection, campaign_id: str) -> None:
    apply.create_campaign(conn, campaign_id)


def add_player(
    conn: sqlite3.Connection,
    player_id: str = PLAYER,
    *,
    campaign_id: str = CAMPAIGN,
    location_id: str = GUILD_HALL,
    shards: int = 0,
    quest: str | None = None,
    money_pennies: int = START_MONEY,
) -> None:
    """Legt einen Spieler an, `quest` ist None (nie angenommen), "active" oder "completed"."""
    effects = []
    if shards:
        effects.append(AddItems(item_id=CRYSTAL_SHARD, quantity=shards))
    if quest:
        effects.append(SetQuestStatus(quest_id=GUILD_SHARD_ORDER, status=quest))
    conn.execute("BEGIN")
    apply.create_player(
        conn,
        campaign_id,
        player_id,
        name=player_id,
        location_id=location_id,
        money_pennies=money_pennies,
    )
    apply.apply_effects(conn, campaign_id, player_id, effects)
    conn.execute("COMMIT")


def custom_content(*, location=GUILD_HALL, quantity=5, reward=100, minutes=10) -> Content:
    quest = QuestDef(
        quest_id=GUILD_SHARD_ORDER,
        turn_in_location_id=location,
        item_id=CRYSTAL_SHARD,
        quantity=quantity,
        reward_pennies=reward,
    )
    return Content(quests={GUILD_SHARD_ORDER: quest}, action_minutes={"submit_quest_items": minutes})


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
