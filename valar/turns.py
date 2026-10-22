"""submit_turn(): Idempotenz, Revisionsprüfung und eine Transaktion pro Spielzug."""

import json
import sqlite3
from typing import NamedTuple

from valar import apply, db
from valar.content import CONTENT, Content
from valar.models import ACTIONS, EFFECTS, Action, CheckResult, TurnResult
from valar.resolve import Accepted, AwaitingRoll, Rejected, resolve
from valar.wiki import Wiki


class UnknownCampaign(LookupError):
    pass


class RequestIdConflict(Exception):
    """Dieselbe request_id kam mit anderer Aktion oder anderem Spieler wieder."""


class _StoredTurn(NamedTuple):
    player_id: str
    action_json: str
    result: TurnResult


def submit_turn(
    conn: sqlite3.Connection,
    *,
    campaign_id: str,
    player_id: str,
    request_id: str,
    expected_revision: int,
    action: Action,
    content: Content = CONTENT,
    wiki: Wiki | None = None,
    context: dict | None = None,
) -> TurnResult:
    """Nimmt einen Spielzug entgegen: alles oder nichts.

    `context` ist das Manifest des Kontextbauers (welche Seiten ins Modell gingen); es wird mitgespeichert.

    - Bekannte request_id: gespeichertes Ergebnis, keine erneute Wirkung.
    - Veraltete Revision oder verletzte Regel: Ablehnung wird gespeichert, Zustand,
      Zeit und Revision bleiben unverändert.
    - Angenommen: Effekte, Revision und Protokoll in einer Transaktion.
    - Fehler beim Anwenden: Rollback, nichts gespeichert, Fehler geht nach oben.
      Die request_id bleibt damit frei für einen neuen Versuch.
    """
    if not request_id:
        raise ValueError("request_id darf nicht leer sein")
    action_json = action.model_dump_json()

    conn.execute("BEGIN IMMEDIATE")  # Schreibsperre gleich zu Beginn: Prüfen und Anwenden sind unteilbar
    try:
        result = _submit(
            conn,
            campaign_id=campaign_id,
            player_id=player_id,
            request_id=request_id,
            expected_revision=expected_revision,
            action=action,
            action_json=action_json,
            content=content,
            wiki=wiki,
            context=context,
        )
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return result


def _submit(
    conn: sqlite3.Connection,
    *,
    campaign_id: str,
    player_id: str,
    request_id: str,
    expected_revision: int,
    action: Action,
    action_json: str,
    content: Content,
    wiki: Wiki | None,
    context: dict | None,
) -> TurnResult:
    stored = _load_turn(conn, campaign_id, request_id)
    if stored is not None:
        if stored.player_id != player_id or stored.action_json != action_json:
            raise RequestIdConflict(request_id)
        return stored.result.model_copy(update={"replayed": True})

    row = conn.execute(
        "SELECT revision FROM campaign WHERE campaign_id = ?", (campaign_id,)
    ).fetchone()
    if row is None:
        raise UnknownCampaign(campaign_id)
    revision = row["revision"]

    if expected_revision != revision:
        resolution = Rejected(
            "stale_revision", "Der Spielstand hat sich geändert. Bitte neu laden."
        )
    else:
        with db.read_only(conn):
            resolution = resolve(
                conn,
                campaign_id=campaign_id,
                player_id=player_id,
                action=action,
                content=content,
                wiki=wiki,
            )

    match resolution:
        case Rejected(code=code, message=message):
            result = TurnResult(
                request_id=request_id,
                status="rejected",
                reject_code=code,
                message=message,
                revision=revision,
            )
        case Accepted(effects=effects, check=check):
            apply.apply_effects(conn, campaign_id, player_id, effects)
            result = TurnResult(
                request_id=request_id,
                status="accepted",
                effects=effects,
                check=check,
                revision=apply.bump_revision(conn, campaign_id),
            )
        case AwaitingRoll(effects=effects):
            apply.apply_effects(conn, campaign_id, player_id, effects)
            result = TurnResult(
                request_id=request_id,
                status="awaiting_roll",
                effects=effects,
                revision=apply.bump_revision(conn, campaign_id),
            )

    apply.record_turn(
        conn,
        campaign_id=campaign_id,
        player_id=player_id,
        action_json=action_json,
        revision_before=revision,
        result=result,
        context_json=json.dumps(context, ensure_ascii=False, sort_keys=True) if context is not None else None,
    )
    return result


def stored_action(conn: sqlite3.Connection, campaign_id: str, request_id: str) -> Action | None:
    """Die Aktion eines schon gespeicherten Spielzugs, oder None. Liest nur."""
    row = conn.execute(
        "SELECT action_json FROM turn WHERE campaign_id = ? AND request_id = ?", (campaign_id, request_id)
    ).fetchone()
    return ACTIONS.validate_json(row["action_json"]) if row else None


def _load_turn(conn: sqlite3.Connection, campaign_id: str, request_id: str) -> _StoredTurn | None:
    row = conn.execute(
        "SELECT player_id, action_json, status, reject_code, message, effects_json, check_json,"
        " revision_after"
        " FROM turn WHERE campaign_id = ? AND request_id = ?",
        (campaign_id, request_id),
    ).fetchone()
    if row is None:
        return None
    result = TurnResult(
        request_id=request_id,
        status=row["status"],
        reject_code=row["reject_code"],
        message=row["message"],
        effects=EFFECTS.validate_json(row["effects_json"]),
        check=CheckResult.model_validate_json(row["check_json"]) if row["check_json"] else None,
        revision=row["revision_after"],
    )
    return _StoredTurn(row["player_id"], row["action_json"], result)
