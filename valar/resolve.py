"""Resolver: lesen den Zustand und geben Effekte zurück. Sie schreiben nie."""

import sqlite3
from dataclasses import dataclass

from valar.content import Content
from valar.models import (
    Action,
    AddMoney,
    AdvanceTime,
    Effect,
    RejectCode,
    RemoveItems,
    SetQuestStatus,
    SubmitQuestItems,
)


@dataclass(frozen=True)
class Accepted:
    effects: list[Effect]


@dataclass(frozen=True)
class Rejected:
    code: RejectCode
    message: str


Resolution = Accepted | Rejected


def resolve(
    conn: sqlite3.Connection,
    *,
    campaign_id: str,
    player_id: str,
    action: Action,
    content: Content,
) -> Resolution:
    match action:
        case SubmitQuestItems():
            return _resolve_submit_quest_items(conn, campaign_id, player_id, action, content)
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
