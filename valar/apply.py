"""Der einzige Code, der schreibt: Spielzustand und Spielzug-Protokoll.

Die Funktionen laufen in einer Transaktion, die der Aufrufer geöffnet hat:
`turns.submit_turn` für Spielzüge, beim Aufbau einer Kampagne der Aufrufer selbst.
"""

import sqlite3

from valar.models import (
    EFFECTS,
    AddItems,
    AddMoney,
    AdvanceTime,
    Effect,
    RemoveItems,
    SetQuestStatus,
    TurnResult,
)


class ApplyError(RuntimeError):
    """Ein Effekt passt nicht zum Zustand. Die Transaktion wird zurückgerollt."""


def create_campaign(conn: sqlite3.Connection, campaign_id: str) -> None:
    conn.execute("INSERT INTO campaign (campaign_id) VALUES (?)", (campaign_id,))


def create_player(
    conn: sqlite3.Connection,
    campaign_id: str,
    player_id: str,
    *,
    name: str,
    location_id: str,
    money_pennies: int = 0,
) -> None:
    conn.execute(
        "INSERT INTO player (campaign_id, player_id, name, location_id, money_pennies)"
        " VALUES (?, ?, ?, ?, ?)",
        (campaign_id, player_id, name, location_id, money_pennies),
    )


def apply_effects(
    conn: sqlite3.Connection, campaign_id: str, player_id: str, effects: list[Effect]
) -> None:
    for effect in effects:
        match effect:
            case RemoveItems():
                _remove_items(conn, campaign_id, player_id, effect)
            case AddItems():
                _add_items(conn, campaign_id, player_id, effect)
            case AddMoney():
                _add_money(conn, campaign_id, player_id, effect)
            case SetQuestStatus():
                _set_quest_status(conn, campaign_id, player_id, effect)
            case AdvanceTime():
                _advance_time(conn, campaign_id, effect)
            case _:
                raise TypeError(f"Kein Anwender für Effekt {type(effect).__name__}")


def bump_revision(conn: sqlite3.Connection, campaign_id: str) -> int:
    """Erhöht die Revision um 1 und gibt den neuen Wert zurück."""
    row = conn.execute(
        "UPDATE campaign SET revision = revision + 1 WHERE campaign_id = ? RETURNING revision",
        (campaign_id,),
    ).fetchone()
    if row is None:
        raise ApplyError(f"Kampagne {campaign_id!r} existiert nicht")
    return row["revision"]


def record_turn(
    conn: sqlite3.Connection,
    *,
    campaign_id: str,
    player_id: str,
    action_json: str,
    revision_before: int,
    result: TurnResult,
) -> None:
    conn.execute(
        "INSERT INTO turn (campaign_id, request_id, player_id, action_json, status,"
        " reject_code, message, effects_json, revision_before, revision_after)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            campaign_id,
            result.request_id,
            player_id,
            action_json,
            result.status,
            result.reject_code,
            result.message,
            EFFECTS.dump_json(result.effects).decode(),
            revision_before,
            result.revision,
        ),
    )


# --- einzelne Effekte; jeder prüft per Zeilenzahl, dass er wirklich getroffen hat ---


def _remove_items(
    conn: sqlite3.Connection, campaign_id: str, player_id: str, effect: RemoveItems
) -> None:
    cursor = conn.execute(
        "UPDATE inventory SET quantity = quantity - ?"
        " WHERE campaign_id = ? AND player_id = ? AND item_id = ? AND quantity >= ?",
        (effect.quantity, campaign_id, player_id, effect.item_id, effect.quantity),
    )
    if cursor.rowcount != 1:
        raise ApplyError(f"Zu wenige {effect.item_id!r} zum Entfernen")


def _add_items(
    conn: sqlite3.Connection, campaign_id: str, player_id: str, effect: AddItems
) -> None:
    conn.execute(
        "INSERT INTO inventory (campaign_id, player_id, item_id, quantity) VALUES (?, ?, ?, ?)"
        " ON CONFLICT (campaign_id, player_id, item_id)"
        " DO UPDATE SET quantity = quantity + excluded.quantity",
        (campaign_id, player_id, effect.item_id, effect.quantity),
    )


def _add_money(
    conn: sqlite3.Connection, campaign_id: str, player_id: str, effect: AddMoney
) -> None:
    cursor = conn.execute(
        "UPDATE player SET money_pennies = money_pennies + ?"
        " WHERE campaign_id = ? AND player_id = ?",
        (effect.pennies, campaign_id, player_id),
    )
    if cursor.rowcount != 1:
        raise ApplyError(f"Spielfigur {player_id!r} existiert nicht")


def _set_quest_status(
    conn: sqlite3.Connection, campaign_id: str, player_id: str, effect: SetQuestStatus
) -> None:
    conn.execute(
        "INSERT INTO quest_state (campaign_id, player_id, quest_id, status) VALUES (?, ?, ?, ?)"
        " ON CONFLICT (campaign_id, player_id, quest_id) DO UPDATE SET status = excluded.status",
        (campaign_id, player_id, effect.quest_id, effect.status),
    )


def _advance_time(conn: sqlite3.Connection, campaign_id: str, effect: AdvanceTime) -> None:
    cursor = conn.execute(
        "UPDATE campaign SET game_minutes = game_minutes + ? WHERE campaign_id = ?",
        (effect.minutes, campaign_id),
    )
    if cursor.rowcount != 1:
        raise ApplyError(f"Kampagne {campaign_id!r} existiert nicht")
