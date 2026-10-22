"""Der einzige Code, der schreibt: Spielzustand und Spielzug-Protokoll.

Die Funktionen laufen in einer Transaktion, die der Aufrufer geöffnet hat:
`turns.submit_turn` für Spielzüge, beim Aufbau einer Kampagne der Aufrufer selbst.
"""

import secrets
import sqlite3
from collections.abc import Mapping

from valar import character
from valar.content import Content
from valar.db import ATTRIBUTE_COLUMNS
from valar.models import (
    CONSEQUENCES,
    EFFECTS,
    AddFact,
    AddItems,
    AddScarcity,
    AddMoney,
    AdjustAttribute,
    AdjustSkill,
    AdvanceTime,
    ClearOpenRoll,
    CreateCampaignPage,
    CreateNpc,
    DayChange,
    Effect,
    EnterLocation,
    GrantAttributePoints,
    LearnKnowledge,
    MarkSkillScene,
    OpenRoll,
    RemoveItems,
    Rest,
    SetQuestStatus,
    TurnResult,
)
from valar.rules import MAX_ATTRIBUTE, RATION, SKILLS


class ApplyError(RuntimeError):
    """Ein Effekt passt nicht zum Zustand. Die Transaktion wird zurückgerollt."""


def create_campaign(conn: sqlite3.Connection, campaign_id: str, *, seed: int | None = None) -> None:
    """Legt eine Kampagne an. Ohne `seed` wird er zufällig gezogen; Tests setzen ihn fest."""
    if seed is None:
        seed = secrets.randbits(62)
    conn.execute("INSERT INTO campaign (campaign_id, seed) VALUES (?, ?)", (campaign_id, seed))


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
    background: str = "",
    keepsake: str = "",
) -> None:
    """Legt eine Spielfigur an. Nicht genannte Attribute stehen auf 10, nicht genannte Skills auf 0.

    Die Figur kommt zur aktuellen Spielzeit an ihrem Ort an und ist ausgeruht; damit beginnt ihre erste Szene.
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
        f"INSERT INTO player (campaign_id, player_id, name, location_id, money_pennies, background, keepsake,"
        f" arrived_at, awake_since, {columns}) VALUES (?, ?, ?, ?, ?, ?, ?,"
        f" (SELECT game_minutes FROM campaign WHERE campaign_id = ?),"
        f" (SELECT game_minutes FROM campaign WHERE campaign_id = ?), {marks})",
        (
            campaign_id, player_id, name, location_id, money_pennies, background, keepsake,
            campaign_id, campaign_id, *values.values(),
        ),
    )
    for skill_id, value in (skills or {}).items():
        conn.execute(
            "INSERT INTO player_skill (campaign_id, player_id, skill_id, value) VALUES (?, ?, ?, ?)",
            (campaign_id, player_id, skill_id, value),
        )


def create_campaign_from_content(
    conn: sqlite3.Connection, campaign_id: str, content: Content, *, seed: int | None = None
) -> str:
    """Neue Kampagne mit der Testfigur aus dem Inhalt, in einer Transaktion. Gibt die player_id zurück."""
    start = content.start_player
    conn.execute("BEGIN IMMEDIATE")
    try:
        create_campaign(conn, campaign_id, seed=seed)
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


def create_character(
    conn: sqlite3.Connection, campaign_id: str, sheet: character.CharacterSheet, content: Content
) -> str:
    """Legt eine neu erschaffene Figur in einer Transaktion an (Schritt 1i). Gibt die player_id zurück.

    Erst wird der Bogen geprüft; jede Abweichung ist ein `CharacterError`, und es ändert sich nichts.
    Kein Spielzug: Wie die Anlage einer Kampagne erhöht das weder Zeit noch Revision (Entscheidung 149).
    """
    character.check_sheet(sheet, content)
    kit = content.start_kits[sheet.kit_id]
    player_id = sheet.player_id
    conn.execute("BEGIN IMMEDIATE")
    try:
        if conn.execute("SELECT 1 FROM campaign WHERE campaign_id = ?", (campaign_id,)).fetchone() is None:
            raise character.CharacterError("unknown_campaign", "Diese Kampagne gibt es nicht.")
        taken = conn.execute(
            "SELECT 1 FROM player WHERE campaign_id = ? AND player_id = ?", (campaign_id, player_id)
        ).fetchone()
        if taken:
            raise character.CharacterError("player_exists", f"Eine Figur mit dem Namen {sheet.name!r} gibt es schon.")
        create_player(
            conn,
            campaign_id,
            player_id,
            name=sheet.name,
            location_id=content.start_location_id,
            money_pennies=kit.money_pennies,
            attributes=character.check_attributes(sheet.attributes),
            skills=character.check_skills(sheet.main_skills, sheet.side_skills),
            background=sheet.background,
            keepsake=sheet.keepsake,
        )
        apply_effects(conn, campaign_id, player_id, [AddItems(item_id=i, quantity=q) for i, q in kit.items.items()])
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return player_id


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
            case LearnKnowledge():
                _learn_knowledge(conn, campaign_id, player_id, effect)
            case CreateNpc():
                _create_npc(conn, campaign_id, effect)
            case AddFact():
                _add_fact(conn, campaign_id, player_id, effect)
            case CreateCampaignPage():
                _create_campaign_page(conn, campaign_id, effect)
            case DayChange():
                _day_change(conn, campaign_id, effect)
            case Rest():
                _rest(conn, campaign_id, player_id, effect)
            case AddScarcity():
                _add_scarcity(conn, campaign_id, effect)
            case AdjustAttribute():
                _adjust_attribute(conn, campaign_id, player_id, effect)
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
    context_json: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO turn (campaign_id, request_id, player_id, action_json, status,"
        " reject_code, message, effects_json, check_json, revision_before, revision_after, context_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            context_json,
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
        " ticks_on_success, reason, minutes, scene, on_success_json, npc_roll_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            CONSEQUENCES.dump_json(effect.on_success).decode(),
            effect.npc_roll.model_dump_json() if effect.npc_roll else None,
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


def _learn_knowledge(
    conn: sqlite3.Connection, campaign_id: str, player_id: str, effect: LearnKnowledge
) -> None:
    # Schon Bekanntes bleibt bekannt; zweimal lernen ändert nichts.
    conn.execute(
        "INSERT OR IGNORE INTO player_knowledge (campaign_id, player_id, page_id, skill_id, difficulty)"
        " VALUES (?, ?, ?, ?, ?)",
        (campaign_id, player_id, effect.page_id, effect.skill_id, effect.difficulty),
    )


def _create_npc(conn: sqlite3.Connection, campaign_id: str, effect: CreateNpc) -> None:
    unknown = set(effect.attributes) - set(ATTRIBUTE_COLUMNS)
    if unknown:
        raise ApplyError(f"Unbekannte Attribute: {sorted(unknown)}")
    values = {attribute: 10 for attribute in ATTRIBUTE_COLUMNS} | effect.attributes
    # Spaltennamen kommen nur aus der festen Tabelle ATTRIBUTE_COLUMNS, nie aus Eingaben.
    columns = ", ".join(ATTRIBUTE_COLUMNS.values())
    marks = ", ".join("?" for _ in ATTRIBUTE_COLUMNS)
    conn.execute(
        f"INSERT INTO npc (campaign_id, npc_id, template_id, name, description, location_id, money_pennies,"
        f" {columns}) VALUES (?, ?, ?, ?, ?, ?, ?, {marks})",
        (
            campaign_id,
            effect.npc_id,
            effect.template_id,
            effect.name,
            effect.description,
            effect.location_id,
            effect.money_pennies,
            *(values[a] for a in ATTRIBUTE_COLUMNS),
        ),
    )
    for skill_id, value in effect.skills.items():
        conn.execute(
            "INSERT INTO npc_skill (campaign_id, npc_id, skill_id, value) VALUES (?, ?, ?, ?)",
            (campaign_id, effect.npc_id, skill_id, value),
        )


def _add_fact(conn: sqlite3.Connection, campaign_id: str, player_id: str, effect: AddFact) -> None:
    conn.execute(
        "INSERT INTO fact (campaign_id, player_id, kind, text, subject, source_npc_id, game_minutes)"
        " VALUES (?, ?, ?, ?, ?, ?, (SELECT game_minutes FROM campaign WHERE campaign_id = ?))",
        (campaign_id, player_id, effect.kind, effect.text, effect.subject, effect.source_npc_id, campaign_id),
    )


def _create_campaign_page(conn: sqlite3.Connection, campaign_id: str, effect: CreateCampaignPage) -> None:
    conn.execute(
        "INSERT INTO campaign_page (campaign_id, page_id, template, type, title, text, location_page, frequency,"
        " value_pennies, gather_skill) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            campaign_id,
            effect.page_id,
            effect.template,
            effect.page_type,
            effect.title,
            effect.text,
            effect.where,
            effect.frequency,
            effect.value_pennies,
            effect.gather_skill,
        ),
    )


def _day_change(conn: sqlite3.Connection, campaign_id: str, effect: DayChange) -> None:
    for day in effect.players:
        if day.ate:
            _remove_items(conn, campaign_id, day.player_id, RemoveItems(item_id=RATION, quantity=1))
        cursor = conn.execute(
            "UPDATE player SET hunger = ? WHERE campaign_id = ? AND player_id = ?",
            (day.hunger, campaign_id, day.player_id),
        )
        if cursor.rowcount != 1:
            raise ApplyError(f"Spielfigur {day.player_id!r} existiert nicht")


def _rest(conn: sqlite3.Connection, campaign_id: str, player_id: str, effect: Rest) -> None:
    cursor = conn.execute(
        "UPDATE player SET fatigue_debt = ?, awake_since = ? WHERE campaign_id = ? AND player_id = ?",
        (effect.debt, effect.awake_since, campaign_id, player_id),
    )
    if cursor.rowcount != 1:
        raise ApplyError(f"Spielfigur {player_id!r} existiert nicht")


def _add_scarcity(conn: sqlite3.Connection, campaign_id: str, effect: AddScarcity) -> None:
    conn.execute(
        "INSERT INTO scarcity (campaign_id, location_page, page_id, level, until, reason) VALUES (?, ?, ?, ?, ?, ?)",
        (campaign_id, effect.location_page, effect.page_id, effect.level, effect.until, effect.reason),
    )


def _adjust_attribute(conn: sqlite3.Connection, campaign_id: str, player_id: str, effect: AdjustAttribute) -> None:
    column = ATTRIBUTE_COLUMNS[effect.attribute]  # feste Tabelle, keine Eingabe
    cursor = conn.execute(
        f"UPDATE player SET {column} = {column} + ?, attribute_points = attribute_points - ?"
        f" WHERE campaign_id = ? AND player_id = ? AND attribute_points >= ? AND {column} + ? <= ?",
        (effect.points, effect.points, campaign_id, player_id, effect.points, effect.points, MAX_ATTRIBUTE),
    )
    if cursor.rowcount != 1:
        raise ApplyError(f"Attribut {effect.attribute} lässt sich nicht um {effect.points} heben")


def record_model_call(
    conn: sqlite3.Connection,
    *,
    month: str,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_micro_eur: int,
    outcome: str,
) -> None:
    """Protokolliert einen Aufruf des Modells mit seinen Kosten (Schritt 2a). Kein Spielzustand."""
    conn.execute(
        "INSERT INTO model_call (month, provider, model, input_tokens, output_tokens, cost_micro_eur, outcome)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (month, provider, model, input_tokens, output_tokens, cost_micro_eur, outcome),
    )
