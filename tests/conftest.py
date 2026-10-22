import sqlite3
from pathlib import Path

import pytest

from tests.helpers import CAMPAIGN, PLAYER, SKILL, add_campaign, add_player
from valar import db
from valar.content import GUILD_HALL


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
        add_campaign(conn, CAMPAIGN)
        add_player(conn, PLAYER, location_id=location_id, shards=shards, quest="active")

    return make


@pytest.fixture
def scenario(make_scenario) -> None:
    """Der Schürfer im Zunfthaus mit 5 Kristallsplittern und aktivem Auftrag der Zunft."""
    make_scenario()


@pytest.fixture
def make_roll_scenario(conn: sqlite3.Connection):
    """Eine Spielfigur für Proben. Standard: STR 14 (+2), GES 8 und Klingenwaffen 30 (+3), Bonus +5.

    Gegen SG 12 (DIFF-03) reicht dann eine 7.
    """

    def make(*, skills: dict[str, int] | None = None, attributes: dict[str, int] | None = None) -> None:
        add_campaign(conn, CAMPAIGN)
        add_player(
            conn,
            PLAYER,
            attributes={"STR": 14, "GES": 8} if attributes is None else attributes,
            skills={SKILL: 30} if skills is None else skills,
        )

    return make


@pytest.fixture
def roll_scenario(make_roll_scenario) -> None:
    make_roll_scenario()
