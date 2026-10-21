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
def make_scenario(conn: sqlite3.Connection):
    """Legt einen Schürfer mit aktivem Auftrag der Zunft an. Abweichungen über Argumente."""

    def make(*, shards: int = 5, location_id: str = GUILD_HALL) -> None:
        conn.execute("BEGIN")
        apply.create_campaign(conn, CAMPAIGN)
        apply.create_player(
            conn, CAMPAIGN, PLAYER, name="Schürfer", location_id=location_id, money_pennies=START_MONEY
        )
        apply.apply_effects(
            conn,
            CAMPAIGN,
            PLAYER,
            [
                AddItems(item_id=CRYSTAL_SHARD, quantity=shards),
                SetQuestStatus(quest_id=GUILD_SHARD_ORDER, status="active"),
            ],
        )
        conn.execute("COMMIT")

    return make


@pytest.fixture
def scenario(make_scenario) -> None:
    """Der Schürfer im Zunfthaus mit 5 Kristallsplittern und aktivem Auftrag der Zunft."""
    make_scenario()
