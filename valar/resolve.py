"""Resolver: lesen den Zustand und geben Effekte zurück. Sie schreiben nie."""

import sqlite3
from dataclasses import dataclass

from valar import rules
from valar.content import Content
from valar.db import ATTRIBUTE_COLUMNS
from valar.models import (
    Action,
    AddMoney,
    AdjustSkill,
    AdvanceTime,
    AttemptAction,
    CheckResult,
    ClearOpenRoll,
    Effect,
    EnterLocation,
    GrantAttributePoints,
    MarkSkillScene,
    Move,
    OpenRoll,
    RejectCode,
    RemoveItems,
    SetQuestStatus,
    SubmitQuestItems,
    SubmitRoll,
)


@dataclass(frozen=True)
class Accepted:
    effects: list[Effect]
    check: CheckResult | None = None


@dataclass(frozen=True)
class AwaitingRoll:
    """Der Versuch ist angenommen und hat eine offene Probe eröffnet. Die Handlung ist angehalten."""

    effects: list[Effect]


@dataclass(frozen=True)
class Rejected:
    code: RejectCode
    message: str


Resolution = Accepted | AwaitingRoll | Rejected


def resolve(
    conn: sqlite3.Connection,
    *,
    campaign_id: str,
    player_id: str,
    action: Action,
    content: Content,
) -> Resolution:
    open_roll = conn.execute(
        "SELECT player_id, skill_id, dc, bonus, ticks_on_success, minutes, scene"
        " FROM open_roll WHERE campaign_id = ?",
        (campaign_id,),
    ).fetchone()
    match action:
        case SubmitRoll():
            return _resolve_submit_roll(conn, campaign_id, player_id, action, open_roll)
        case SubmitQuestItems() | AttemptAction() | Move() if open_roll is not None:
            return Rejected("roll_pending", "Eine Probe ist offen. Erst würfeln.")
        case SubmitQuestItems():
            return _resolve_submit_quest_items(conn, campaign_id, player_id, action, content)
        case AttemptAction():
            return _resolve_attempt(conn, campaign_id, player_id, action, content)
        case Move():
            return _resolve_move(conn, campaign_id, player_id, action, content)
        case _:
            raise TypeError(f"Keine Auflösung für Aktion {type(action).__name__}")


def _resolve_submit_quest_items(
    conn: sqlite3.Connection,
    campaign_id: str,
    player_id: str,
    action: SubmitQuestItems,
    content: Content,
) -> Resolution:
    quest = content.quests.get(action.quest_id)
    if quest is None:
        return Rejected("unknown_quest", "Diesen Auftrag gibt es nicht.")

    player = conn.execute(
        "SELECT location_id FROM player WHERE campaign_id = ? AND player_id = ?",
        (campaign_id, player_id),
    ).fetchone()
    if player is None:
        return Rejected("unknown_player", "Diese Spielfigur gibt es nicht.")

    state = conn.execute(
        "SELECT status FROM quest_state WHERE campaign_id = ? AND player_id = ? AND quest_id = ?",
        (campaign_id, player_id, quest.quest_id),
    ).fetchone()
    if state is None or state["status"] != "active":
        return Rejected("quest_not_active", "Dieser Auftrag ist nicht aktiv.")

    if player["location_id"] != quest.turn_in_location_id:
        return Rejected("wrong_location", "Hier kann der Auftrag nicht abgegeben werden.")

    owned = conn.execute(
        "SELECT quantity FROM inventory WHERE campaign_id = ? AND player_id = ? AND item_id = ?",
        (campaign_id, player_id, quest.item_id),
    ).fetchone()
    if owned is None or owned["quantity"] < quest.quantity:
        return Rejected("not_enough_items", "Es fehlen Gegenstände für den Auftrag.")

    return Accepted(
        [
            RemoveItems(item_id=quest.item_id, quantity=quest.quantity),
            AddMoney(pennies=quest.reward_pennies),
            SetQuestStatus(quest_id=quest.quest_id, status="completed"),
            AdvanceTime(minutes=content.action_minutes[action.type]),
        ]
    )


def _game_minutes(conn: sqlite3.Connection, campaign_id: str) -> int:
    return conn.execute(
        "SELECT game_minutes FROM campaign WHERE campaign_id = ?", (campaign_id,)
    ).fetchone()["game_minutes"]


def _resolve_move(
    conn: sqlite3.Connection, campaign_id: str, player_id: str, action: Move, content: Content
) -> Resolution:
    player = conn.execute(
        "SELECT location_id FROM player WHERE campaign_id = ? AND player_id = ?",
        (campaign_id, player_id),
    ).fetchone()
    if player is None:
        return Rejected("unknown_player", "Diese Spielfigur gibt es nicht.")
    if action.destination_id not in content.locations:
        return Rejected("unknown_location", "Diesen Ort gibt es nicht.")
    if action.destination_id == player["location_id"]:
        return Rejected("already_there", "Die Figur ist schon dort.")
    minutes = content.route_minutes(player["location_id"], action.destination_id)
    if minutes is None:
        return Rejected("not_connected", "Von hier führt kein Weg direkt dorthin.")

    # Die Ankunft beginnt eine neue Szene (Entscheidung 53).
    arrived_at = _game_minutes(conn, campaign_id) + minutes
    return Accepted(
        [
            AdvanceTime(minutes=minutes),
            EnterLocation(location_id=action.destination_id, arrived_at=arrived_at),
        ]
    )


def _resolve_attempt(
    conn: sqlite3.Connection,
    campaign_id: str,
    player_id: str,
    action: AttemptAction,
    content: Content,
) -> Resolution:
    if action.skill_id not in rules.SKILLS:
        return Rejected("unknown_skill", "Diesen Skill gibt es nicht.")

    columns = ", ".join(ATTRIBUTE_COLUMNS.values())  # feste Tabelle, keine Eingabe
    player = conn.execute(
        f"SELECT arrived_at, {columns} FROM player WHERE campaign_id = ? AND player_id = ?",
        (campaign_id, player_id),
    ).fetchone()
    if player is None:
        return Rejected("unknown_player", "Diese Spielfigur gibt es nicht.")

    skill = conn.execute(
        "SELECT value, ticked_scene FROM player_skill"
        " WHERE campaign_id = ? AND player_id = ? AND skill_id = ?",
        (campaign_id, player_id, action.skill_id),
    ).fetchone()
    attributes = {name: player[column] for name, column in ATTRIBUTE_COLUMNS.items()}
    bonus = rules.check_bonus(action.skill_id, skill["value"] if skill else 0, attributes)
    # Es zählt die Szene beim Öffnen (Entscheidung 58).
    scene = rules.scene_start(
        player["arrived_at"], _game_minutes(conn, campaign_id), content.start_minute_of_day
    )
    ticked_this_scene = skill is not None and skill["ticked_scene"] == scene

    return AwaitingRoll(
        [
            OpenRoll(
                skill_id=action.skill_id,
                difficulty=action.difficulty,
                dc=rules.DIFFICULTIES[action.difficulty].dc,
                bonus=bonus,
                ticks_on_success=rules.ticks_on_success(
                    action.difficulty, bonus, ticked_this_scene=ticked_this_scene
                ),
                reason=action.reason,
                minutes=rules.ATTEMPT_MINUTES[action.duration],
                scene=scene,
            )
        ]
    )


def _resolve_submit_roll(
    conn: sqlite3.Connection,
    campaign_id: str,
    player_id: str,
    action: SubmitRoll,
    open_roll: sqlite3.Row | None,
) -> Resolution:
    if open_roll is None or open_roll["player_id"] != player_id:
        return Rejected("no_open_roll", "Es ist keine Probe offen.")

    # SG und Bonus gelten so, wie sie dem Spieler beim Öffnen gezeigt wurden.
    total = action.value + open_roll["bonus"]
    result = rules.outcome(action.value, total, open_roll["dc"])
    gained = open_roll["ticks_on_success"] if result in ("success", "critical_success") else 0

    effects: list[Effect] = [ClearOpenRoll()]
    points = 0
    if gained:
        skill_id = open_roll["skill_id"]
        skill = conn.execute(
            "SELECT value, ticks FROM player_skill"
            " WHERE campaign_id = ? AND player_id = ? AND skill_id = ?",
            (campaign_id, player_id, skill_id),
        ).fetchone()
        value, ticks = (skill["value"], skill["ticks"]) if skill else (0, 0)
        _, new_ticks, points = rules.add_ticks(value, ticks, gained)
        if points or new_ticks != ticks:
            effects.append(AdjustSkill(skill_id=skill_id, points=points, ticks=new_ticks - ticks))
        if points:
            ups_before = conn.execute(
                "SELECT COALESCE(SUM(ups), 0) FROM player_skill WHERE campaign_id = ? AND player_id = ?",
                (campaign_id, player_id),
            ).fetchone()[0]
            levels = rules.level(ups_before + points) - rules.level(ups_before)
            if levels:
                effects.append(GrantAttributePoints(points=levels))  # 1 Attributspunkt je Level
        # Nur vergebene Ticks verbrauchen die Szene, ein Fehlschlag nicht (Entscheidung 58).
        effects.append(MarkSkillScene(skill_id=skill_id, scene=open_roll["scene"]))
    # Die Zeit vergeht bei jedem Ausgang (Entscheidungen 59, 60).
    effects.append(AdvanceTime(minutes=open_roll["minutes"]))

    check = CheckResult(
        die=action.value,
        bonus=open_roll["bonus"],
        total=total,
        dc=open_roll["dc"],
        outcome=result,
        ticks=gained,
        skill_points=points,
    )
    return Accepted(effects, check)
