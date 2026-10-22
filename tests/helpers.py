import sqlite3

from valar import apply
from valar.content import CONTENT, CRYSTAL_SHARD, GUILD_HALL, GUILD_SHARD_ORDER, Content, QuestDef
from valar.models import AddItems, AttemptAction, Examine, Gather, Move, SetQuestStatus, SubmitQuestItems, SubmitRoll
from valar.turns import submit_turn

CAMPAIGN = "campaign-1"
PLAYER = "player-1"
START_MONEY = 30  # Pfennig, bewusst nicht 0: die Belohnung wird addiert, nicht gesetzt
SKILL = "SKILL-101"  # Klingenwaffen, Leit-Attribute STR / GES
REASON = "Der Schürfer schlägt sich zum Ausgang durch"


def shards(conn: sqlite3.Connection, campaign_id: str = CAMPAIGN, player_id: str = PLAYER) -> int:
    row = conn.execute(
        "SELECT quantity FROM inventory WHERE campaign_id = ? AND player_id = ? AND item_id = ?",
        (campaign_id, player_id, CRYSTAL_SHARD),
    ).fetchone()
    return row["quantity"] if row else 0


def money(conn: sqlite3.Connection, campaign_id: str = CAMPAIGN, player_id: str = PLAYER) -> int:
    return conn.execute(
        "SELECT money_pennies FROM player WHERE campaign_id = ? AND player_id = ?",
        (campaign_id, player_id),
    ).fetchone()["money_pennies"]


def quest_status(
    conn: sqlite3.Connection, campaign_id: str = CAMPAIGN, player_id: str = PLAYER
) -> str | None:
    row = conn.execute(
        "SELECT status FROM quest_state WHERE campaign_id = ? AND player_id = ? AND quest_id = ?",
        (campaign_id, player_id, GUILD_SHARD_ORDER),
    ).fetchone()
    return row["status"] if row else None


def location(conn: sqlite3.Connection, player_id: str = PLAYER) -> str:
    return conn.execute(
        "SELECT location_id FROM player WHERE campaign_id = ? AND player_id = ?", (CAMPAIGN, player_id)
    ).fetchone()["location_id"]


def set_game_minutes(conn: sqlite3.Connection, minutes: int, campaign_id: str = CAMPAIGN) -> None:
    """Stellt die Uhr von außen (Testaufbau), ohne Spielzug."""
    conn.execute("UPDATE campaign SET game_minutes = ? WHERE campaign_id = ?", (minutes, campaign_id))


def campaign_row(conn: sqlite3.Connection, campaign_id: str = CAMPAIGN) -> sqlite3.Row:
    return conn.execute(
        "SELECT revision, game_minutes FROM campaign WHERE campaign_id = ?", (campaign_id,)
    ).fetchone()


def turn_count(conn: sqlite3.Connection, campaign_id: str = CAMPAIGN) -> int:
    return conn.execute("SELECT count(*) FROM turn WHERE campaign_id = ?", (campaign_id,)).fetchone()[0]


def last_turn(conn: sqlite3.Connection) -> sqlite3.Row:
    return conn.execute("SELECT * FROM turn ORDER BY turn_id DESC LIMIT 1").fetchone()


def submit(conn: sqlite3.Connection, request_id: str, expected_revision: int, **overrides):
    """Reicht die Abgabe des Standard-Spielers ein; `overrides` ersetzen einzelne Argumente."""
    request = {
        "campaign_id": CAMPAIGN,
        "player_id": PLAYER,
        "request_id": request_id,
        "expected_revision": expected_revision,
        "action": SubmitQuestItems(quest_id=GUILD_SHARD_ORDER),
    } | overrides
    return submit_turn(conn, **request)


def attempt(
    conn: sqlite3.Connection,
    request_id: str,
    expected_revision: int,
    *,
    difficulty: str = "DIFF-03",
    skill_id: str = SKILL,
    duration: str = "moment",
    **overrides,
):
    action = AttemptAction(skill_id=skill_id, difficulty=difficulty, duration=duration, reason=REASON)
    return submit(conn, request_id, expected_revision, action=action, **overrides)


def move(conn: sqlite3.Connection, request_id: str, expected_revision: int, destination_id: str, **overrides):
    return submit(conn, request_id, expected_revision, action=Move(destination_id=destination_id), **overrides)


def examine(conn: sqlite3.Connection, request_id: str, expected_revision: int, page_id: str, **overrides):
    return submit(conn, request_id, expected_revision, action=Examine(page_id=page_id), **overrides)


def gather(conn: sqlite3.Connection, request_id: str, expected_revision: int, page_id: str, **overrides):
    return submit(conn, request_id, expected_revision, action=Gather(page_id=page_id), **overrides)


def roll(conn: sqlite3.Connection, request_id: str, expected_revision: int, value: int, **overrides):
    return submit(conn, request_id, expected_revision, action=SubmitRoll(value=value), **overrides)


def skill_row(
    conn: sqlite3.Connection, skill_id: str = SKILL, campaign_id: str = CAMPAIGN, player_id: str = PLAYER
) -> tuple[int, int, int] | None:
    """(Wert, Ticks, Aufstiege) oder None, wenn es keine Zeile gibt (Skillwert 0)."""
    row = conn.execute(
        "SELECT value, ticks, ups FROM player_skill"
        " WHERE campaign_id = ? AND player_id = ? AND skill_id = ?",
        (campaign_id, player_id, skill_id),
    ).fetchone()
    return tuple(row) if row else None


def attribute_points(conn: sqlite3.Connection, player_id: str = PLAYER) -> int:
    return conn.execute(
        "SELECT attribute_points FROM player WHERE campaign_id = ? AND player_id = ?",
        (CAMPAIGN, player_id),
    ).fetchone()[0]


def total_ups(conn: sqlite3.Connection, player_id: str = PLAYER) -> int:
    return conn.execute(
        "SELECT COALESCE(SUM(ups), 0) FROM player_skill WHERE campaign_id = ? AND player_id = ?",
        (CAMPAIGN, player_id),
    ).fetchone()[0]


def open_roll_row(conn: sqlite3.Connection, campaign_id: str = CAMPAIGN) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM open_roll WHERE campaign_id = ?", (campaign_id,)).fetchone()


def add_campaign(conn: sqlite3.Connection, campaign_id: str, *, seed: int | None = None) -> None:
    apply.create_campaign(conn, campaign_id, seed=seed)


def add_player(
    conn: sqlite3.Connection,
    player_id: str = PLAYER,
    *,
    campaign_id: str = CAMPAIGN,
    location_id: str = GUILD_HALL,
    shards: int = 0,
    quest: str | None = None,
    money_pennies: int = START_MONEY,
    attributes: dict[str, int] | None = None,
    skills: dict[str, int] | None = None,
) -> None:
    """Legt einen Spieler an, `quest` ist None (nie angenommen), "active" oder "completed"."""
    effects = []
    if shards:
        effects.append(AddItems(item_id=CRYSTAL_SHARD, quantity=shards))
    if quest:
        effects.append(SetQuestStatus(quest_id=GUILD_SHARD_ORDER, status=quest))
    conn.execute("BEGIN")
    apply.create_player(
        conn,
        campaign_id,
        player_id,
        name=player_id,
        location_id=location_id,
        money_pennies=money_pennies,
        attributes=attributes,
        skills=skills,
    )
    apply.apply_effects(conn, campaign_id, player_id, effects)
    conn.execute("COMMIT")


def custom_content(*, location=GUILD_HALL, quantity=5, reward=100, minutes=10) -> Content:
    quest = QuestDef(
        quest_id=GUILD_SHARD_ORDER,
        turn_in_location_id=location,
        item_id=CRYSTAL_SHARD,
        quantity=quantity,
        reward_pennies=reward,
    )
    # Über den Konstruktor, damit der Inhalt geprüft wird.
    return Content(**(dict(CONTENT) | {"quests": {GUILD_SHARD_ORDER: quest}, "action_minutes": {"submit_quest_items": minutes}}))


def assert_only_log_changed(before: dict[str, list[tuple]], after: dict[str, list[tuple]]) -> None:
    """Abgelehnt heißt: Zustand, Zeit und Revision unverändert, nur ein Protokolleintrag mehr."""
    state_tables = set(before) - {"turn", "sqlite_sequence"}
    assert {t: after[t] for t in state_tables} == {t: before[t] for t in state_tables}
    assert len(after["turn"]) == len(before["turn"]) + 1
    assert after["turn"][:-1] == before["turn"]  # bisherige Einträge unberührt


def dump_state(conn: sqlite3.Connection) -> dict[str, list[tuple]]:
    """Kompletter Inhalt aller Tabellen, zum Vergleichen vorher/nachher."""
    tables = [
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
    ]
    return {
        table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        for table in tables
    }


# --- Test-Wiki (Schritt 1d2): Testdaten, kein Weltinhalt ---


def page(type_: str, valar: str = "", *, extra: str = "", body: str = "Text.") -> str:
    """Eine Wiki-Seite mit Typ, optionalem Block `valar` (als YAML-Zeilen) und Text."""
    block = "valar:\n" + "".join(f"  {line}\n" for line in valar.strip().splitlines()) if valar else ""
    return f"---\ntype: {type_}\n{extra}{block}---\n{body}\n"


TEST_WIKI = {
    "regionen/oberland.md": page("Region"),
    "regionen/testland.md": page("Region", "parent: oberland"),
    "landschaften/moor.md": page("Landschaft"),
    "landschaften/wald.md": page("Landschaft"),
    "orte/testdorf.md": page(
        "Ort",
        "region: testland\nlandscapes: [moor]\nknowledge:\n"
        "  - {skill: SKILL-405, difficulty: DIFF-03, text: Unter der Brücke liegt ein Versteck.}",
    ),
    "orte/anderswo.md": page("Ort", "region: oberland\nlandscapes: [wald]"),
    "pflanzen/sumpfkraut.md": page(
        "Pflanze",
        "occurs:\n  - {where: testland, frequency: rare}\n  - {where: moor, frequency: common}\n"
        "item: {value_pennies: 3, gather_skill: SKILL-408}\nknowledge:\n"
        "  - {skill: SKILL-408, difficulty: DIFF-02, text: Naturkunde leicht.}\n"
        "  - {skill: SKILL-408, difficulty: DIFF-04, text: Naturkunde schwer.}\n"
        "  - {skill: SKILL-408, difficulty: DIFF-06, text: Naturkunde extrem.}\n"
        "  - {skill: SKILL-407, difficulty: DIFF-07, text: Alchemie legendär.}",
        extra="title: Sumpfkraut\n",
    ),
    "tiere/wolf.md": page("Tier", "occurs:\n  - {where: oberland, frequency: uncommon}"),
    "tiere/dorfkatze.md": page("Tier", "occurs:\n  - {where: testdorf, frequency: very_rare}\n  - {where: moor, frequency: common}"),
    "tiere/auerochse.md": page("Tier", "occurs:\n  - {where: testland, frequency: rare}", extra="status: deprecated\n"),
    "npc-typen/koehler.md": page(
        "NPC-Typ",
        "occurs:\n  - {where: wald, frequency: common}\ntemplate:\n"
        "  attributes: {STR: [9, 13], WEI: [12, 12]}\n  skills: {SKILL-402: [10, 30]}\n  money_pennies: [0, 40]",
        extra="title: Köhler\n",
    ),
    "personen/graf-test.md": page("Person", extra="title: Graf Kuno von Testhausen\n"),
}


def write_wiki(root, pages: dict[str, str]) -> None:
    for rel, text in pages.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def content_pointing_to(**pages: str | None) -> Content:
    """Testinhalt, dessen Orte auf Seiten des Test-Wikis zeigen (z. B. boomtown="testdorf")."""
    locations = {
        key: location.model_copy(update={"wiki": pages.get(key)}) for key, location in CONTENT.locations.items()
    }
    return Content(**(dict(CONTENT) | {"locations": locations}))
