"""Abfragen: Umsehen und Zustand anzeigen. Sie lesen nur und sind keine Spielzüge (Entscheidung 56):
kein Protokoll, keine Revision, keine Zeit."""

import sqlite3

from pydantic import BaseModel, ConfigDict

from valar import db, rules
from valar.content import CONTENT, Content
from valar.db import ATTRIBUTE_COLUMNS
from valar.turns import UnknownCampaign

_FROZEN = ConfigDict(frozen=True)


class UnknownPlayer(LookupError):
    pass


class Clock(BaseModel):
    model_config = _FROZEN

    game_minutes: int
    day: int  # ab 1
    time: str  # "HH:MM"


class Exit(BaseModel):
    model_config = _FROZEN

    location_id: str
    name: str
    minutes: int
    distance_m: int  # Weglänge
    direction: str | None  # Himmelsrichtung in Luftlinie (N, NO, O, …)


class LookResult(BaseModel):
    model_config = _FROZEN

    location_id: str
    name: str
    description: str
    parent_id: str | None
    x: int | None  # Punkt auf der Karte in Metern; None für Orte außerhalb des Inhalts
    y: int | None
    exits: list[Exit]
    # Andere Spielfiguren am selben Ort, nach player_id.
    others_here: list[str]
    clock: Clock


class SkillView(BaseModel):
    model_config = _FROZEN

    skill_id: str
    name: str
    value: int
    ticks: int
    ticked_this_scene: bool


class OpenRollView(BaseModel):
    model_config = _FROZEN

    player_id: str
    skill_id: str
    difficulty: str
    dc: int
    bonus: int
    ticks_on_success: int
    reason: str
    minutes: int


class StatusResult(BaseModel):
    model_config = _FROZEN

    revision: int
    clock: Clock
    player_id: str
    name: str
    location_id: str
    location_name: str
    scene_start: int  # Beginn der laufenden Szene in Spielminuten
    money_pennies: int
    attributes: dict[str, int]
    attribute_points: int
    level: int
    skills: list[SkillView]  # nur Skills mit eigener Zeile, nach skill_id
    inventory: dict[str, int]
    quests: dict[str, str]
    open_roll: OpenRollView | None


def look(
    conn: sqlite3.Connection, *, campaign_id: str, player_id: str, content: Content = CONTENT
) -> LookResult:
    """Was die Spielfigur an ihrem Ort sieht."""
    with db.snapshot(conn):
        game_minutes, _ = _campaign(conn, campaign_id)
        player = _player(conn, campaign_id, player_id)
        location_id = player["location_id"]
        others = conn.execute(
            "SELECT player_id FROM player WHERE campaign_id = ? AND location_id = ? AND player_id != ?"
            " ORDER BY player_id",
            (campaign_id, location_id, player_id),
        ).fetchall()

    location = content.locations.get(location_id)
    return LookResult(
        location_id=location_id,
        name=location.name if location else location_id,
        description=location.description if location else "",
        parent_id=location.parent_id if location else None,
        x=location.x if location else None,
        y=location.y if location else None,
        exits=[_exit(content, location_id, target, minutes) for target, minutes in content.exits(location_id)],
        others_here=[row["player_id"] for row in others],
        clock=_clock(game_minutes, content),
    )


def status(
    conn: sqlite3.Connection, *, campaign_id: str, player_id: str, content: Content = CONTENT
) -> StatusResult:
    """Zustand der Spielfigur samt Uhr, Revision und offener Probe."""
    with db.snapshot(conn):
        game_minutes, revision = _campaign(conn, campaign_id)
        player = _player(conn, campaign_id, player_id)
        skills = conn.execute(
            "SELECT skill_id, value, ticks, ups, ticked_scene FROM player_skill"
            " WHERE campaign_id = ? AND player_id = ? ORDER BY skill_id",
            (campaign_id, player_id),
        ).fetchall()
        inventory = conn.execute(
            "SELECT item_id, quantity FROM inventory"
            " WHERE campaign_id = ? AND player_id = ? AND quantity > 0 ORDER BY item_id",
            (campaign_id, player_id),
        ).fetchall()
        quests = conn.execute(
            "SELECT quest_id, status FROM quest_state WHERE campaign_id = ? AND player_id = ?"
            " ORDER BY quest_id",
            (campaign_id, player_id),
        ).fetchall()
        open_roll = conn.execute(
            "SELECT player_id, skill_id, difficulty, dc, bonus, ticks_on_success, reason, minutes"
            " FROM open_roll WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()

    scene = rules.scene_start(player["arrived_at"], game_minutes, content.start_minute_of_day)
    location = content.locations.get(player["location_id"])
    return StatusResult(
        revision=revision,
        clock=_clock(game_minutes, content),
        player_id=player_id,
        name=player["name"],
        location_id=player["location_id"],
        location_name=location.name if location else player["location_id"],
        scene_start=scene,
        money_pennies=player["money_pennies"],
        attributes={name: player[column] for name, column in ATTRIBUTE_COLUMNS.items()},
        attribute_points=player["attribute_points"],
        level=rules.level(sum(row["ups"] for row in skills)),
        skills=[
            SkillView(
                skill_id=row["skill_id"],
                name=rules.SKILLS[row["skill_id"]].name if row["skill_id"] in rules.SKILLS else row["skill_id"],
                value=row["value"],
                ticks=row["ticks"],
                ticked_this_scene=row["ticked_scene"] == scene,
            )
            for row in skills
        ],
        inventory={row["item_id"]: row["quantity"] for row in inventory},
        quests={row["quest_id"]: row["status"] for row in quests},
        open_roll=OpenRollView(**dict(open_roll)) if open_roll else None,
    )


def _campaign(conn: sqlite3.Connection, campaign_id: str) -> tuple[int, int]:
    row = conn.execute(
        "SELECT game_minutes, revision FROM campaign WHERE campaign_id = ?", (campaign_id,)
    ).fetchone()
    if row is None:
        raise UnknownCampaign(campaign_id)
    return row["game_minutes"], row["revision"]


def _player(conn: sqlite3.Connection, campaign_id: str, player_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM player WHERE campaign_id = ? AND player_id = ?", (campaign_id, player_id)
    ).fetchone()
    if row is None:
        raise UnknownPlayer(player_id)
    return row


def _exit(content: Content, origin: str, target: str, minutes: int) -> Exit:
    route = content.route_between(origin, target)
    return Exit(
        location_id=target,
        name=content.locations[target].name,
        minutes=minutes,
        distance_m=content.route_length(route),
        direction=rules.compass(content.locations[origin].point, content.locations[target].point),
    )


def _clock(game_minutes: int, content: Content) -> Clock:
    day, minute = rules.clock(game_minutes, content.start_minute_of_day)
    return Clock(game_minutes=game_minutes, day=day, time=f"{minute // 60:02d}:{minute % 60:02d}")
