"""Der einzige Code, der schreibt: Spielzustand und Spielzug-Protokoll.

Die Funktionen laufen in einer Transaktion, die der Aufrufer geöffnet hat:
`turns.submit_turn` für Spielzüge, beim Aufbau einer Kampagne der Aufrufer selbst.
"""

import sqlite3
from collections.abc import Mapping

from valar.content import Content
from valar.db import ATTRIBUTE_COLUMNS
from valar.models import (
    EFFECTS,
    AddItems,
    AddMoney,
    AdjustSkill,
    AdvanceTime,
    ClearOpenRoll,
    Effect,
    EnterLocation,
    GrantAttributePoints,
    MarkSkillScene,
    OpenRoll,
    RemoveItems,
    SetQuestStatus,
    TurnResult,
)
from valar.rules import SKILLS


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
    attributes: Mapping[str, int] | None = None,
    skills: Mapping[str, int] | None = None,
) -> None:
    """Legt eine Spielfigur an. Nicht genannte Attribute stehen auf 10, nicht genannte Skills auf 0.

    Die Figur kommt zur aktuellen Spielzeit an ihrem Ort an; damit beginnt ihre erste Szene.
    """
    unknown = set(attributes or {}) - set(ATTRIBUTE_COLUMNS)
    if unknown:
        raise ValueError(f"Unbekannte Attribute: {sorted(unknown)}")
    unknown = set(skills or {}) - set(SKILLS)
    if unknown:
        raise ValueError(f"Unbekannte Skills: {sorted(unknown)}")
    values = {attribute: 10 for attribute in ATTRIBUTE_COLUMNS} | dict(attributes or {})
    # Spaltennamen kommen nur aus der festen Tabelle ATTRIBUTE_COLUMNS, nie aus Eingaben.
    columns = ", ".join(ATTRIBUTE_COLUMNS.values())
    marks = ", ".join("?" for _ in ATTRIBUTE_COLUMNS)
    conn.execute(
        f"INSERT INTO player (campaign_id, player_id, name, location_id, money_pennies, arrived_at,"
        f" {columns}) VALUES (?, ?, ?, ?, ?,"
        f" (SELECT game_minutes FROM campaign WHERE campaign_id = ?), {marks})",
        (campaign_id, player_id, name, location_id, money_pennies, campaign_id, *values.values()),
    )
    for skill_id, value in (skills or {}).items():
        conn.execute(
            "INSERT INTO player_skill (campaign_id, player_id, skill_id, value) VALUES (?, ?, ?, ?)",
            (campaign_id, player_id, skill_id, value),
        )


def create_campaign_from_content(conn: sqlite3.Connection, campaign_id: str, content: Content) -> str:
    """Neue Kampagne mit der Testfigur aus dem Inhalt, in einer Transaktion. Gibt die player_id zurück."""
    start = content.start_player
    conn.execute("BEGIN IMMEDIATE")
    try:
        create_campaign(conn, campaign_id)
        create_player(
            conn,
            campaign_id,
            start.player_id,
            name=start.name,
            location_id=start.location_id,
            money_pennies=start.money_pennies,
            attributes=start.attributes,
            skills=start.skills,
        )
        effects: list[Effect] = [AddItems(item_id=i, quantity=q) for i, q in start.items.items()]
        effects += [SetQuestStatus(quest_id=q, status="active") for q in start.active_quests]
        apply_effects(conn, campaign_id, start.player_id, effects)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return start.player_id


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
            case EnterLocation():
                _enter_location(conn, campaign_id, player_id, effect)
            case OpenRoll():
                _open_roll(conn, campaign_id, player_id, effect)
            case ClearOpenRoll():
                _clear_open_roll(conn, campaign_id, player_id)
            case AdjustSkill():
                _adjust_skill(conn, campaign_id, player_id, effect)
            case MarkSkillScene():
                _mark_skill_scene(conn, campaign_id, player_id, effect)
            case GrantAttributePoints():
                _grant_attribute_points(conn, campaign_id, player_id, effect)
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
        " reject_code, message, effects_json, check_json, revision_before, revision_after)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            campaign_id,
            result.request_id,
            player_id,
            action_json,
            result.status,
            result.reject_code,
            result.message,
            EFFECTS.dump_json(result.effects).decode(),
            result.check.model_dump_json() if result.check else None,
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


def _enter_location(
    conn: sqlite3.Connection, campaign_id: str, player_id: str, effect: EnterLocation
) -> None:
    cursor = conn.execute(
        "UPDATE player SET location_id = ?, arrived_at = ? WHERE campaign_id = ? AND player_id = ?",
        (effect.location_id, effect.arrived_at, campaign_id, player_id),
    )
    if cursor.rowcount != 1:
        raise ApplyError(f"Spielfigur {player_id!r} existiert nicht")


def _open_roll(conn: sqlite3.Connection, campaign_id: str, player_id: str, effect: OpenRoll) -> None:
    # Ist schon eine Probe offen, verletzt das den Primärschlüssel: IntegrityError, Rollback.
    conn.execute(
        "INSERT INTO open_roll (campaign_id, player_id, skill_id, difficulty, dc, bonus,"
        " ticks_on_success, reason, minutes, scene) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            campaign_id,
            player_id,
            effect.skill_id,
            effect.difficulty,
            effect.dc,
            effect.bonus,
            effect.ticks_on_success,
            effect.reason,
            effect.minutes,
            effect.scene,
        ),
    )


def _clear_open_roll(conn: sqlite3.Connection, campaign_id: str, player_id: str) -> None:
    cursor = conn.execute(
        "DELETE FROM open_roll WHERE campaign_id = ? AND player_id = ?", (campaign_id, player_id)
    )
    if cursor.rowcount != 1:
        raise ApplyError(f"Keine offene Probe von {player_id!r}")


def _adjust_skill(
    conn: sqlite3.Connection, campaign_id: str, player_id: str, effect: AdjustSkill
) -> None:
    # Erst UPDATE, dann INSERT: Ein Upsert prüfte die CHECKs schon am neuen Wert allein,
    # und die Tick-Änderung kann negativ sein.
    cursor = conn.execute(
        "UPDATE player_skill SET value = value + ?, ticks = ticks + ?, ups = ups + ?"
        " WHERE campaign_id = ? AND player_id = ? AND skill_id = ?",
        (effect.points, effect.ticks, effect.points, campaign_id, player_id, effect.skill_id),
    )
    if cursor.rowcount == 0:
        conn.execute(
            "INSERT INTO player_skill (campaign_id, player_id, skill_id, value, ticks, ups)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (campaign_id, player_id, effect.skill_id, effect.points, effect.ticks, effect.points),
        )


def _mark_skill_scene(
    conn: sqlite3.Connection, campaign_id: str, player_id: str, effect: MarkSkillScene
) -> None:
    conn.execute(
        "INSERT INTO player_skill (campaign_id, player_id, skill_id, ticked_scene) VALUES (?, ?, ?, ?)"
        " ON CONFLICT (campaign_id, player_id, skill_id) DO UPDATE SET ticked_scene = excluded.ticked_scene",
        (campaign_id, player_id, effect.skill_id, effect.scene),
    )


def _grant_attribute_points(
    conn: sqlite3.Connection, campaign_id: str, player_id: str, effect: GrantAttributePoints
) -> None:
    cursor = conn.execute(
        "UPDATE player SET attribute_points = attribute_points + ?"
        " WHERE campaign_id = ? AND player_id = ?",
        (effect.points, campaign_id, player_id),
    )
    if cursor.rowcount != 1:
        raise ApplyError(f"Spielfigur {player_id!r} existiert nicht")
