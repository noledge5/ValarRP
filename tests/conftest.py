import sqlite3
from pathlib import Path

import pytest

from tests.helpers import CAMPAIGN, PLAYER, START_MONEY
from valar import apply, db
from valar.content import CRYSTAL_SHARD, GUILD_HALL, GUILD_SHARD_ORDER
from valar.models import AddItems, SetQuestStatus


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "valar.db"


@pytest.fixture
def conn(db_path: Path):
    connection = db.connect(db_path)
    db.init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def scenario(conn: sqlite3.Connection) -> None:
    """Ein Schürfer im Zunfthaus mit 5 Kristallsplittern und aktivem Auftrag der Zunft."""
    conn.execute("BEGIN")
    apply.create_campaign(conn, CAMPAIGN)
    apply.create_player(
        conn, CAMPAIGN, PLAYER, name="Schürfer", location_id=GUILD_HALL, money_pennies=START_MONEY
    )
    apply.apply_effects(
        conn,
        CAMPAIGN,
        PLAYER,
        [
            AddItems(item_id=CRYSTAL_SHARD, quantity=5),
            SetQuestStatus(quest_id=GUILD_SHARD_ORDER, status="active"),
        ],
    )
    conn.execute("COMMIT")
