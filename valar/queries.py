"""Abfragen: Umsehen, Zustand anzeigen, Betrachten. Sie lesen nur und sind keine Spielzüge
(Entscheidung 56): kein Protokoll, keine Revision, keine Zeit."""

import sqlite3

from pydantic import BaseModel, ConfigDict

from valar import db, rules, search
from valar.content import CONTENT, Content
from valar.db import ATTRIBUTE_COLUMNS
from valar.models import CONSEQUENCES, Frequency, NpcRoll
from valar.resolve import campaign_wiki, pages_here, stages_for
from valar.turns import UnknownCampaign
from valar.wiki import Page, Wiki, default_wiki

_FROZEN = ConfigDict(frozen=True)


class UnknownPlayer(LookupError):
    pass


class UnknownPage(LookupError):
    """Die Seite gibt es nicht, oder sie gehört nicht zum Ort der Spielfigur."""


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


class Thing(BaseModel):
    """Etwas, das laut Wiki am Ort vorkommt, mit dem, was die Figur darüber weiß."""

    model_config = _FROZEN

    page_id: str
    title: str
    type: str
    frequency: Frequency
    gatherable: bool
    known: list[str]  # Texte der Wissensstufen, die die Figur kennt oder ohne Wurf sieht


class NpcView(BaseModel):
    """Ein NPC, wie die Spielfigur ihn sieht; seine Werte bleiben verborgen."""

    model_config = _FROZEN

    npc_id: str
    name: str
    template_id: str
    template_title: str
    description: str
    location_id: str
    wants: str = ""  # Entscheidung 197: beim ersten Treffen fest
    fears: str = ""
    attitude: int = 0  # Haltung zur abfragenden Figur, -2 bis 2; 0 auch ohne Figur
    memories: list[str] = []  # was zwischen beiden geschah, älteste zuerst, höchstens MAX_NPC_MEMORY_SHOWN
    secret: str = ""  # nur für den Spielleiter, nie für die Seite (Entscheidung 239)


class FactView(BaseModel):
    model_config = _FROZEN

    kind: str  # "fact" oder "rumor"
    text: str
    subject: str | None
    source_npc_id: str | None
    game_minutes: int


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
    # Aus dem Wiki (Entscheidung 106, Stufe 1): Region (nächste zuerst), Landschaften, was die Figur über den
    # Ort selbst weiß (versteckte Dinge sind Stufen mit Wahrnehmung), und was hier vorkommt.
    regions: list[str] = []
    landscapes: list[str] = []
    wealth: int | None = None  # Wohlstand 1 bis 5 und Herrschaft (Titel der Macht), aus der Seite des Orts
    ruled_by: str | None = None
    known_here: list[str] = []
    things: list[Thing] = []
    npcs: list[NpcView] = []  # NPCs am selben Ort, nach Name
    place_memory: list[str] = []  # Orts-Gedächtnis, älteste zuerst, höchstens MAX_PLACE_MEMORY_SHOWN (Entscheidung 198)

    @property
    def standing(self) -> list[str]:
        """Wohlstand und Herrschaft in Worten, z. B. ["Wohlstand: arm", "Herrschaft: Die Fürsten"]."""
        return ([f"Wohlstand: {rules.WEALTH_NAMES[self.wealth]}"] if self.wealth else []) + (
            [f"Herrschaft: {self.ruled_by}"] if self.ruled_by else []
        )


class ExamineResult(BaseModel):
    """Eine Seite, wie die Figur sie kennt: Text für alle und die Wissensstufen, die sie kennt."""

    model_config = _FROZEN

    page_id: str
    title: str
    type: str
    description: str | None
    text: str
    known: list[str]


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
    npc_roll: NpcRoll | None = None  # Vergleichsprobe: Wurf des NPCs, sein Gesamt ist der SG
    stakes: tuple[str, ...] = ()  # der Einsatz in Worten, schon gekürzt (Entscheidung 231)


class ConditionView(BaseModel):
    """Hunger, Erschöpfung und Wetter (Entscheidung 101), mit dem Malus, den sie beim Öffnen einer Probe geben."""

    model_config = _FROZEN

    hunger: int
    hunger_name: str
    exhaustion: int
    exhaustion_name: str
    awake_hours: int
    weather: str
    malus: int  # Summe aus Hunger, Erschöpfung und Wetter auf alle Proben (Wahrnehmung kann mehr verlieren)


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
    background: str  # weicher Zustand aus der Erschaffung
    keepsake: str
    skills: list[SkillView]  # nur Skills mit eigener Zeile, nach skill_id
    inventory: dict[str, int]
    quests: dict[str, str]
    open_roll: OpenRollView | None
    condition: ConditionView


def look(
    conn: sqlite3.Connection,
    *,
    campaign_id: str,
    player_id: str,
    content: Content = CONTENT,
    wiki: Wiki | None = None,
) -> LookResult:
    """Was die Spielfigur an ihrem Ort sieht. Ohne `wiki` gilt das Welt-Wiki."""
    base = wiki or default_wiki()
    with db.snapshot(conn):
        wiki = campaign_wiki(conn, campaign_id, base)
        game_minutes, _ = _campaign(conn, campaign_id)
        player = _player(conn, campaign_id, player_id)
        location_id = player["location_id"]
        others = conn.execute(
            "SELECT player_id FROM player WHERE campaign_id = ? AND location_id = ? AND player_id != ?"
            " ORDER BY player_id",
            (campaign_id, location_id, player_id),
        ).fetchall()
        around = wiki.surroundings(content, location_id) if location_id in content.locations else None
        indoors = location_id in content.locations and content.locations[location_id].indoors
        known_here = known_texts(conn, campaign_id, player, around.location) if around and around.location else []
        things = [
            Thing(
                page_id=page.page_id,
                title=page.title,
                type=page.type,
                frequency=frequency,
                gatherable=not indoors and page.game.item is not None and page.game.item.gather_skill is not None,
                known=known_texts(conn, campaign_id, player, page),
            )
            for page, frequency in (around.occurring if around else ())
        ]
        npcs = conn.execute(
            "SELECT npc_id, name, template_id, description, location_id, wants, fears, secret FROM npc"
            " WHERE campaign_id = ? AND location_id = ?"
            " ORDER BY name, npc_id",
            (campaign_id, location_id),
        ).fetchall()
        bonds = _bonds(conn, campaign_id, player_id)
        place_memory = [row["text"] for row in conn.execute(
            "SELECT text FROM place_memory WHERE campaign_id = ? AND location_id = ? ORDER BY memory_id DESC LIMIT ?",
            (campaign_id, location_id, rules.MAX_PLACE_MEMORY_SHOWN),
        )][::-1]

    location = content.locations.get(location_id)
    place = around.location if around else None
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
        regions=[page.title for page in around.regions] if around else [],
        landscapes=[page.title for page in around.landscapes] if around else [],
        wealth=place.game.wealth if place else None,
        ruled_by=(wiki.pages[place.game.ruled_by].title if place.game.ruled_by in wiki.pages else place.game.ruled_by)
        if place and place.game.ruled_by else None,
        known_here=known_here,
        things=things,
        npcs=[_npc_view(row, wiki, bonds) for row in npcs],
        place_memory=place_memory,
    )


def examine(
    conn: sqlite3.Connection,
    *,
    campaign_id: str,
    player_id: str,
    page_id: str,
    content: Content = CONTENT,
    wiki: Wiki | None = None,
) -> ExamineResult:
    """Betrachten ohne Probe: was jeder sieht und was die Figur schon weiß (Entscheidung 64).
    Nur Seiten, die zum Ort der Figur gehören. Mehr erfährt sie über die Aktion `examine`."""
    base = wiki or default_wiki()
    with db.snapshot(conn):
        _campaign(conn, campaign_id)
        wiki = campaign_wiki(conn, campaign_id, base)
        player = _player(conn, campaign_id, player_id)
        page = pages_here(wiki, content, player["location_id"]).get(page_id)
        if page is None:
            raise UnknownPage(page_id)
        known = known_texts(conn, campaign_id, player, page)
    return ExamineResult(
        page_id=page.page_id,
        title=page.title,
        type=page.type,
        description=page.description,
        text=page.body,
        known=known,
    )


def npcs(
    conn: sqlite3.Connection, *, campaign_id: str, wiki: Wiki | None = None, player_id: str | None = None
) -> list[NpcView]:
    """Alle NPCs der Kampagne, nach Name. Wo sie stehen, zeigt `location_id`; mit `player_id` auch Haltung und
    Gedächtnis zu dieser Figur."""
    base = wiki or default_wiki()
    with db.snapshot(conn):
        _campaign(conn, campaign_id)
        rows = conn.execute(
            "SELECT npc_id, name, template_id, description, location_id, wants, fears FROM npc WHERE campaign_id = ?"
            " ORDER BY name, npc_id",
            (campaign_id,),
        ).fetchall()
        bonds = _bonds(conn, campaign_id, player_id) if player_id else ({}, {})
    return [_npc_view(row, base, bonds) for row in rows]


def _bonds(conn: sqlite3.Connection, campaign_id: str, player_id: str) -> tuple[dict[str, int], dict[str, list[str]]]:
    """Haltung und die letzten Zeilen Gedächtnis aller NPCs zu einer Figur (Entscheidung 197)."""
    attitudes = {row["npc_id"]: row["attitude"] for row in conn.execute(
        "SELECT npc_id, attitude FROM npc_bond WHERE campaign_id = ? AND player_id = ?", (campaign_id, player_id)
    )}
    memories: dict[str, list[str]] = {}
    for row in conn.execute(
        "SELECT npc_id, text FROM npc_memory WHERE campaign_id = ? AND player_id = ? ORDER BY memory_id",
        (campaign_id, player_id),
    ):
        memories.setdefault(row["npc_id"], []).append(row["text"])
    return attitudes, {npc: lines[-rules.MAX_NPC_MEMORY_SHOWN:] for npc, lines in memories.items()}


def _npc_view(row: sqlite3.Row, wiki: Wiki, bonds: tuple[dict, dict] = ({}, {})) -> NpcView:
    template = wiki.pages.get(row["template_id"])
    attitudes, memories = bonds
    return NpcView(
        npc_id=row["npc_id"],
        name=row["name"],
        template_id=row["template_id"],
        template_title=template.title if template else row["template_id"],
        description=row["description"],
        location_id=row["location_id"],
        wants=row["wants"],
        fears=row["fears"],
        secret=row["secret"] if "secret" in row.keys() else "",
        attitude=attitudes.get(row["npc_id"], 0),
        memories=memories.get(row["npc_id"], []),
    )


def lookup(
    conn: sqlite3.Connection,
    *,
    campaign_id: str,
    query: str,
    wiki: Wiki | None = None,
    limit: int = search.SEARCH_LIMIT,
) -> list[search.Hit]:
    """Nachschlagen (Entscheidung 140): wenige Abschnitte aus Kanon und Kampagnen-Seiten, kein Spielzug.
    Wissensstufen stehen nicht im Index."""
    with db.snapshot(conn):
        _campaign(conn, campaign_id)
        world = campaign_wiki(conn, campaign_id, wiki or default_wiki())
    return search.FullTextRetriever(world).search(query, limit)


def facts(conn: sqlite3.Connection, *, campaign_id: str, player_id: str) -> list[FactView]:
    """Was die Spielfigur notiert hat, in der Reihenfolge des Erfahrens."""
    with db.snapshot(conn):
        _campaign(conn, campaign_id)
        _player(conn, campaign_id, player_id)
        rows = conn.execute(
            "SELECT kind, text, subject, source_npc_id, game_minutes FROM fact"
            " WHERE campaign_id = ? AND player_id = ? ORDER BY fact_id",
            (campaign_id, player_id),
        ).fetchall()
    return [FactView(**dict(row)) for row in rows]


def known_texts(conn: sqlite3.Connection, campaign_id: str, player: sqlite3.Row, page: Page) -> list[str]:
    return [stage.text for stage in stages_for(conn, campaign_id, player, page) if stage.known]


def status(
    conn: sqlite3.Connection, *, campaign_id: str, player_id: str, content: Content = CONTENT
) -> StatusResult:
    """Zustand der Spielfigur samt Uhr, Revision und offener Probe."""
    with db.snapshot(conn):
        game_minutes, revision = _campaign(conn, campaign_id)
        seed = conn.execute("SELECT seed FROM campaign WHERE campaign_id = ?", (campaign_id,)).fetchone()["seed"]
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
            "SELECT player_id, skill_id, difficulty, dc, bonus, ticks_on_success, reason, minutes, npc_roll_json,"
            " on_success_json FROM open_roll WHERE campaign_id = ?",
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
        background=player["background"],
        keepsake=player["keepsake"],
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
        open_roll=_open_roll_view(open_roll) if open_roll else None,
        condition=_condition(player, game_minutes, seed, content),
    )


def _condition(player: sqlite3.Row, game_minutes: int, seed: int, content: Content) -> ConditionView:
    day, _ = rules.clock(game_minutes, content.start_minute_of_day)
    today = rules.weather_of_day(seed, day)
    stage = rules.exhaustion(player["fatigue_debt"], player["awake_since"], game_minutes)
    return ConditionView(
        hunger=player["hunger"],
        hunger_name=rules.HUNGER_NAMES[player["hunger"]],
        exhaustion=stage,
        exhaustion_name=rules.EXHAUSTION_NAMES[stage],
        awake_hours=(game_minutes - player["awake_since"]) // 60,
        weather=today.name,
        malus=rules.check_malus("", player["hunger"], stage, today),
    )


def _open_roll_view(row: sqlite3.Row) -> OpenRollView:
    values = {key: row[key] for key in row.keys() if key not in ("npc_roll_json", "on_success_json")}
    npc_roll = NpcRoll.model_validate_json(row["npc_roll_json"]) if row["npc_roll_json"] else None
    stakes = tuple(c.line() for c in CONSEQUENCES.validate_json(row["on_success_json"]) if c.type == "stake")
    return OpenRollView(**values, npc_roll=npc_roll, stakes=stakes)


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
