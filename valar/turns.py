"""submit_turn(): Idempotenz, Revisionsprüfung und eine Transaktion pro Spielzug."""

import sqlite3
from typing import NamedTuple

from valar import apply, db
from valar.content import CONTENT, Content
from valar.models import EFFECTS, Action, TurnResult
from valar.resolve import Accepted, Rejected, resolve


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
) -> TurnResult:
    """Nimmt einen Spielzug entgegen: alles oder nichts.

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
        case Accepted(effects=effects):
            apply.apply_effects(conn, campaign_id, player_id, effects)
            result = TurnResult(
                request_id=request_id,
                status="accepted",
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
    )
    return result


def _load_turn(conn: sqlite3.Connection, campaign_id: str, request_id: str) -> _StoredTurn | None:
    row = conn.execute(
        "SELECT player_id, action_json, status, reject_code, message, effects_json, revision_after"
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
        revision=row["revision_after"],
    )
    return _StoredTurn(row["player_id"], row["action_json"], result)
