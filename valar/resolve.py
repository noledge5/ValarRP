"""Resolver: lesen den Zustand und geben Effekte zurück. Sie schreiben nie."""

import sqlite3
from dataclasses import dataclass

from valar import rules
from valar.content import Content
from valar.db import ATTRIBUTE_COLUMNS
from valar.models import (
    CONSEQUENCES,
    DifficultyId,
    Action,
    AddFact,
    AddItems,
    AddMoney,
    AdjustSkill,
    AdvanceTime,
    AttemptAction,
    CheckResult,
    ClearOpenRoll,
    Contest,
    CreateCampaignPage,
    CreateNpc,
    CreatePage,
    Effect,
    EnterLocation,
    Examine,
    Gather,
    GrantAttributePoints,
    LearnKnowledge,
    MarkSkillScene,
    MeetNpc,
    Move,
    NpcRoll,
    OpenRoll,
    RecordFact,
    RollConsequence,
    RejectCode,
    RemoveItems,
    SetQuestStatus,
    SubmitQuestItems,
    SubmitRoll,
    Talk,
)
from valar.wiki import GameFields, ItemDef, Occurrence, Page, Wiki, default_wiki, slug, with_pages


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
    wiki: Wiki | None = None,
) -> Resolution:
    """`wiki` fehlt nur, wo das Welt-Wiki gilt; es wird erst geladen, wenn eine Aktion es braucht."""
    open_roll = conn.execute(
        "SELECT player_id, skill_id, dc, bonus, ticks_on_success, minutes, scene, on_success_json"
        " FROM open_roll WHERE campaign_id = ?",
        (campaign_id,),
    ).fetchone()
    if isinstance(action, SubmitRoll):
        return _resolve_submit_roll(conn, campaign_id, player_id, action, open_roll)
    if open_roll is not None:
        return Rejected("roll_pending", "Eine Probe ist offen. Erst würfeln.")
    match action:
        case SubmitQuestItems():
            return _resolve_submit_quest_items(conn, campaign_id, player_id, action, content)
        case AttemptAction():
            return _resolve_attempt(conn, campaign_id, player_id, action, content)
        case Move():
            return _resolve_move(conn, campaign_id, player_id, action, content)
        case Examine():
            world = campaign_wiki(conn, campaign_id, wiki or default_wiki())
            return _resolve_examine(conn, campaign_id, player_id, action, content, world)
        case Gather():
            world = campaign_wiki(conn, campaign_id, wiki or default_wiki())
            return _resolve_gather(conn, campaign_id, player_id, action, content, world)
        case MeetNpc():
            return _resolve_meet_npc(conn, campaign_id, player_id, action, content, wiki or default_wiki())
        case Talk():
            return _resolve_talk(conn, campaign_id, player_id, action, content)
        case Contest():
            return _resolve_contest(conn, campaign_id, player_id, action, content)
        case RecordFact():
            return _resolve_record_fact(conn, campaign_id, player_id, action)
        case CreatePage():
            world = campaign_wiki(conn, campaign_id, wiki or default_wiki())
            return _resolve_create_page(conn, campaign_id, player_id, action, content, world)
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
    player = _check_player(conn, campaign_id, player_id)
    if player is None:
        return Rejected("unknown_player", "Diese Spielfigur gibt es nicht.")
    return AwaitingRoll(
        [
            _open_roll(
                conn,
                campaign_id,
                player,
                content,
                skill_id=action.skill_id,
                difficulty=action.difficulty,
                reason=action.reason,
                minutes=rules.ATTEMPT_MINUTES[action.duration],
            )
        ]
    )


def _check_player(conn: sqlite3.Connection, campaign_id: str, player_id: str) -> sqlite3.Row | None:
    """Was eine Probe über die Spielfigur braucht: Ort, Ankunft, Attribute."""
    columns = ", ".join(ATTRIBUTE_COLUMNS.values())  # feste Tabelle, keine Eingabe
    return conn.execute(
        f"SELECT player_id, location_id, arrived_at, {columns} FROM player"
        " WHERE campaign_id = ? AND player_id = ?",
        (campaign_id, player_id),
    ).fetchone()


def player_bonus(conn: sqlite3.Connection, campaign_id: str, player: sqlite3.Row, skill_id: str) -> int:
    """Bonus der Spielfigur in einem Skill (MECH-01 ohne Wurf), mit Skillwert 0 ohne eigene Zeile."""
    row = conn.execute(
        "SELECT value FROM player_skill WHERE campaign_id = ? AND player_id = ? AND skill_id = ?",
        (campaign_id, player["player_id"], skill_id),
    ).fetchone()
    attributes = {name: player[column] for name, column in ATTRIBUTE_COLUMNS.items()}
    return rules.check_bonus(skill_id, row["value"] if row else 0, attributes)


def _open_roll(
    conn: sqlite3.Connection,
    campaign_id: str,
    player: sqlite3.Row,
    content: Content,
    *,
    skill_id: str,
    difficulty: DifficultyId,
    reason: str,
    minutes: int,
    on_success: tuple[RollConsequence, ...] = (),
    npc_roll: NpcRoll | None = None,
) -> OpenRoll:
    """Die offene Probe mit allem, was der Spieler vorher sieht (Entscheidung 44).

    Bei der Vergleichsprobe ist das Gesamt des NPCs der SG; Stufe und Ticks folgen daraus (Entscheidung 63).
    """
    bonus = player_bonus(conn, campaign_id, player, skill_id)
    # Es zählt die Szene beim Öffnen (Entscheidung 58).
    scene = rules.scene_start(
        player["arrived_at"], _game_minutes(conn, campaign_id), content.start_minute_of_day
    )
    ticked = conn.execute(
        "SELECT ticked_scene FROM player_skill WHERE campaign_id = ? AND player_id = ? AND skill_id = ?",
        (campaign_id, player["player_id"], skill_id),
    ).fetchone()
    ticked_this_scene = ticked is not None and ticked["ticked_scene"] == scene
    if npc_roll is None:
        dc = rules.DIFFICULTIES[difficulty].dc
        ticks = rules.ticks_on_success(difficulty, bonus, ticked_this_scene=ticked_this_scene)
    else:
        dc = max(npc_roll.total, 1)
        ticks = rules.contest_ticks(npc_roll.total, bonus, ticked_this_scene=ticked_this_scene)
    return OpenRoll(
        skill_id=skill_id,
        difficulty=difficulty,
        dc=dc,
        bonus=bonus,
        ticks_on_success=ticks,
        reason=reason,
        minutes=minutes,
        scene=scene,
        on_success=on_success,
        npc_roll=npc_roll,
    )


# --- Untersuchen und Sammeln (Entscheidungen 64 und 92) ---


@dataclass(frozen=True)
class StageView:
    """Eine Wissensstufe aus Sicht der Spielfigur."""

    skill_id: str
    difficulty: DifficultyId
    dc: int
    text: str
    learned: bool  # durch Untersuchen gelernt, dauerhaft
    passive: bool  # ohne Wurf sichtbar: 10 + Bonus erreicht den SG

    @property
    def known(self) -> bool:
        return self.learned or self.passive


def stages_for(conn: sqlite3.Connection, campaign_id: str, player: sqlite3.Row, page: Page) -> list[StageView]:
    """Alle Wissensstufen einer Seite, nach SG, bei Gleichstand in der Reihenfolge der Seite."""
    learned = {
        (row["skill_id"], row["difficulty"])
        for row in conn.execute(
            "SELECT skill_id, difficulty FROM player_knowledge"
            " WHERE campaign_id = ? AND player_id = ? AND page_id = ?",
            (campaign_id, player["player_id"], page.page_id),
        )
    }
    stages = []
    for stage in page.game.knowledge:
        dc = rules.DIFFICULTIES[stage.difficulty].dc
        bonus = player_bonus(conn, campaign_id, player, stage.skill)
        stages.append(
            StageView(
                skill_id=stage.skill,
                difficulty=stage.difficulty,
                dc=dc,
                text=stage.text,
                learned=(stage.skill, stage.difficulty) in learned,
                passive=rules.passive_known(bonus, dc),
            )
        )
    return sorted(stages, key=lambda s: s.dc)


def pages_here(wiki: Wiki, content: Content, location_id: str) -> dict[str, Page]:
    """Alle Seiten, die zum Ort gehören: Ort, Regionen, Landschaften und was dort vorkommt."""
    if location_id not in content.locations:
        return {}
    around = wiki.surroundings(content, location_id)
    pages = [*([around.location] if around.location else []), *around.regions, *around.landscapes]
    pages += [page for page, _ in around.occurring]
    return {page.page_id: page for page in pages}


def _page_here(
    wiki: Wiki, content: Content, player: sqlite3.Row, page_id: str
) -> Page | Rejected:
    if page_id not in wiki.pages:
        return Rejected("unknown_page", "Davon weiß die Welt nichts.")
    page = pages_here(wiki, content, player["location_id"]).get(page_id)
    if page is None:
        return Rejected("not_here", "Das gibt es hier nicht.")
    return page


def _resolve_examine(
    conn: sqlite3.Connection,
    campaign_id: str,
    player_id: str,
    action: Examine,
    content: Content,
    wiki: Wiki,
) -> Resolution:
    player = _check_player(conn, campaign_id, player_id)
    if player is None:
        return Rejected("unknown_player", "Diese Spielfigur gibt es nicht.")
    page = _page_here(wiki, content, player, action.page_id)
    if isinstance(page, Rejected):
        return page
    stages = stages_for(conn, campaign_id, player, page)
    unknown = [stage for stage in stages if not stage.known]
    if not unknown:
        return Rejected("nothing_to_learn", "Hier gibt es nichts Neues zu erfahren.")
    # Probe im Skill der nächsten unbekannten Stufe. Ein Erfolg kann alle noch nicht gelernten Stufen
    # dieses Skills öffnen, bis zur Höhe des Gesamts (Entscheidung 64).
    target = unknown[0]
    consequences = tuple(
        LearnKnowledge(page_id=page.page_id, skill_id=s.skill_id, difficulty=s.difficulty, dc=s.dc)
        for s in stages
        if s.skill_id == target.skill_id and not s.learned
    )
    return AwaitingRoll(
        [
            _open_roll(
                conn,
                campaign_id,
                player,
                content,
                skill_id=target.skill_id,
                difficulty=target.difficulty,
                reason=f"{page.title} untersuchen",
                minutes=content.action_minutes[action.type],
                on_success=consequences,
            )
        ]
    )


def _resolve_gather(
    conn: sqlite3.Connection,
    campaign_id: str,
    player_id: str,
    action: Gather,
    content: Content,
    wiki: Wiki,
) -> Resolution:
    player = _check_player(conn, campaign_id, player_id)
    if player is None:
        return Rejected("unknown_player", "Diese Spielfigur gibt es nicht.")
    page = wiki.pages.get(action.page_id)
    if page is None:
        return Rejected("unknown_page", "Davon weiß die Welt nichts.")
    if page.game.item is None or page.game.item.gather_skill is None:
        return Rejected("not_gatherable", "Das lässt sich nicht sammeln.")
    occurring = {} if player["location_id"] not in content.locations else {
        p.page_id: frequency for p, frequency in wiki.surroundings(content, player["location_id"]).occurring
    }
    frequency = occurring.get(page.page_id)
    if frequency is None:
        return Rejected("not_here", "Das gibt es hier nicht.")

    difficulty, most = rules.GATHER[frequency]
    # Die Menge würfelt der Code schon beim Öffnen; sie steht in der offenen Probe und im Spielzug.
    campaign = conn.execute(
        "SELECT seed, revision FROM campaign WHERE campaign_id = ?", (campaign_id,)
    ).fetchone()
    quantity = rules.turn_rng(campaign["seed"], campaign["revision"]).randint(1, most)
    return AwaitingRoll(
        [
            _open_roll(
                conn,
                campaign_id,
                player,
                content,
                skill_id=page.game.item.gather_skill,
                difficulty=difficulty,
                reason=f"{page.title} sammeln",
                minutes=content.action_minutes[action.type],
                on_success=(AddItems(item_id=page.page_id, quantity=quantity),),
            )
        ]
    )


# --- NPCs, weicher Zustand, Kampagnen-Seiten (Schritt 1f) ---


def _resolve_meet_npc(
    conn: sqlite3.Connection,
    campaign_id: str,
    player_id: str,
    action: MeetNpc,
    content: Content,
    wiki: Wiki,
) -> Resolution:
    player = _check_player(conn, campaign_id, player_id)
    if player is None:
        return Rejected("unknown_player", "Diese Spielfigur gibt es nicht.")
    template_page = wiki.pages.get(action.template_id)
    if template_page is None or template_page.game.template is None:
        return Rejected("unknown_template", "Diese NPC-Vorlage gibt es nicht.")
    # Wichtige Figuren schreibt der Autor (Entscheidung 11): kein NPC mit ihrem Namen.
    wanted = slug(action.name)
    if any(p.type == "Person" and wanted in (p.page_id, slug(p.title)) for p in wiki.pages.values()):
        return Rejected("important_figure", "Diese Figur legt nur der Autor an.")
    same = conn.execute(
        "SELECT npc_id FROM npc WHERE campaign_id = ? AND location_id = ? AND name = ? COLLATE NOCASE",
        (campaign_id, player["location_id"], action.name),
    ).fetchone()
    if same is not None:
        return Rejected("npc_exists", f"Diesen NPC gibt es hier schon: {same['npc_id']}.")

    taken = {row["npc_id"] for row in conn.execute("SELECT npc_id FROM npc WHERE campaign_id = ?", (campaign_id,))}
    base = npc_id = wanted or "npc"
    number = 1
    while npc_id in taken:
        number += 1
        npc_id = f"{base}-{number}"

    # Werte würfelt der Code in den Spannen der Vorlage; sie stehen im Spielzug (Entscheidung 62).
    template = template_page.game.template
    campaign = conn.execute("SELECT seed, revision FROM campaign WHERE campaign_id = ?", (campaign_id,)).fetchone()
    rng = rules.turn_rng(campaign["seed"], campaign["revision"])
    attributes = {name: rng.randint(*span) for name, span in template.attributes.items()}
    skills = {skill_id: rng.randint(*span) for skill_id, span in template.skills.items()}
    return Accepted(
        [
            CreateNpc(
                npc_id=npc_id,
                template_id=template_page.page_id,
                name=action.name,
                description=action.description,
                location_id=player["location_id"],
                attributes=attributes,
                skills=skills,
                money_pennies=rng.randint(*template.money_pennies),
            ),
            AdvanceTime(minutes=content.action_minutes["talk"]),
        ]
    )


def _resolve_talk(
    conn: sqlite3.Connection, campaign_id: str, player_id: str, action: Talk, content: Content
) -> Resolution:
    player = _check_player(conn, campaign_id, player_id)
    if player is None:
        return Rejected("unknown_player", "Diese Spielfigur gibt es nicht.")
    npc = conn.execute(
        "SELECT location_id FROM npc WHERE campaign_id = ? AND npc_id = ?", (campaign_id, action.npc_id)
    ).fetchone()
    if npc is None:
        return Rejected("unknown_npc", "Diesen NPC gibt es nicht.")
    if npc["location_id"] != player["location_id"]:
        return Rejected("not_here", "Dieser NPC ist nicht hier.")
    return Accepted([AdvanceTime(minutes=content.action_minutes["talk"])])


def _resolve_contest(
    conn: sqlite3.Connection, campaign_id: str, player_id: str, action: Contest, content: Content
) -> Resolution:
    if action.skill_id not in rules.SKILLS:
        return Rejected("unknown_skill", "Diesen Skill gibt es nicht.")
    against = rules.CONTEST_PAIRS.get(action.skill_id)
    if against is None:
        return Rejected("no_counter_skill", "Gegen diesen Skill würfelt niemand; das ist ein Versuch.")
    player = _check_player(conn, campaign_id, player_id)
    if player is None:
        return Rejected("unknown_player", "Diese Spielfigur gibt es nicht.")
    columns = ", ".join(ATTRIBUTE_COLUMNS.values())  # feste Tabelle, keine Eingabe
    npc = conn.execute(
        f"SELECT location_id, {columns} FROM npc WHERE campaign_id = ? AND npc_id = ?",
        (campaign_id, action.npc_id),
    ).fetchone()
    if npc is None:
        return Rejected("unknown_npc", "Diesen NPC gibt es nicht.")
    if npc["location_id"] != player["location_id"]:
        return Rejected("not_here", "Dieser NPC ist nicht hier.")

    # Der NPC würfelt beim Öffnen mit dem Zufall des Codes; nur die Summe zählt (Entscheidung 63).
    skill = conn.execute(
        "SELECT value FROM npc_skill WHERE campaign_id = ? AND npc_id = ? AND skill_id = ?",
        (campaign_id, action.npc_id, against),
    ).fetchone()
    attributes = {name: npc[column] for name, column in ATTRIBUTE_COLUMNS.items()}
    npc_bonus = rules.check_bonus(against, skill["value"] if skill else 0, attributes)
    campaign = conn.execute("SELECT seed, revision FROM campaign WHERE campaign_id = ?", (campaign_id,)).fetchone()
    die = rules.turn_rng(campaign["seed"], campaign["revision"]).randint(1, 20)
    npc_roll = NpcRoll(npc_id=action.npc_id, skill_id=against, die=die, bonus=npc_bonus, total=die + npc_bonus)
    return AwaitingRoll(
        [
            _open_roll(
                conn,
                campaign_id,
                player,
                content,
                skill_id=action.skill_id,
                difficulty=rules.contest_difficulty(npc_roll.total) or "DIFF-01",
                reason=action.reason,
                minutes=rules.ATTEMPT_MINUTES[action.duration],
                npc_roll=npc_roll,
            )
        ]
    )


def _resolve_record_fact(
    conn: sqlite3.Connection, campaign_id: str, player_id: str, action: RecordFact
) -> Resolution:
    if _check_player(conn, campaign_id, player_id) is None:
        return Rejected("unknown_player", "Diese Spielfigur gibt es nicht.")
    if action.source_npc_id is not None:
        known = conn.execute(
            "SELECT 1 FROM npc WHERE campaign_id = ? AND npc_id = ?", (campaign_id, action.source_npc_id)
        ).fetchone()
        if known is None:
            return Rejected("unknown_npc", "Diesen NPC gibt es nicht.")
    # Nur weicher Zustand, keine Zeit: Notieren ist Buchführung, keine Handlung.
    return Accepted(
        [AddFact(kind=action.kind, text=action.text, subject=action.subject, source_npc_id=action.source_npc_id)]
    )


def _resolve_create_page(
    conn: sqlite3.Connection,
    campaign_id: str,
    player_id: str,
    action: CreatePage,
    content: Content,
    wiki: Wiki,
) -> Resolution:
    player = _check_player(conn, campaign_id, player_id)
    if player is None:
        return Rejected("unknown_player", "Diese Spielfigur gibt es nicht.")
    template = rules.PAGE_TEMPLATES.get(action.template)
    if template is None:
        return Rejected("unknown_template", "Diese Seitenvorlage gibt es nicht.")
    page_id = slug(action.title) or "seite"
    if page_id in wiki.pages:
        return Rejected("page_exists", f"Die Seite {page_id!r} gibt es schon.")
    where = None
    if template.frequency is not None:
        around = (
            wiki.surroundings(content, player["location_id"]) if player["location_id"] in content.locations else None
        )
        if around is None or around.location is None:
            return Rejected("not_here", "Hier gibt es keinen Ort im Wiki, an dem sie vorkommen könnte.")
        where = around.location.page_id
    return Accepted(
        [
            CreateCampaignPage(
                page_id=page_id,
                template=action.template,
                page_type=template.type,
                title=action.title,
                text=action.text,
                where=where,
                frequency=template.frequency,
                value_pennies=template.value_pennies,
                gather_skill=template.gather_skill,
            )
        ]
    )


def campaign_wiki(conn: sqlite3.Connection, campaign_id: str, base: Wiki) -> Wiki:
    """Das Wiki, wie es in dieser Kampagne gilt: Kanon samt Kampagnen-Seiten (Entscheidung 108)."""
    rows = conn.execute(
        "SELECT * FROM campaign_page WHERE campaign_id = ? ORDER BY page_id", (campaign_id,)
    ).fetchall()
    return with_pages(base, [campaign_page(campaign_id, row) for row in rows])


def campaign_page(campaign_id: str, row: sqlite3.Row) -> Page:
    occurs = (Occurrence(where=row["location_page"], frequency=row["frequency"]),) if row["frequency"] else ()
    item = (
        ItemDef(value_pennies=row["value_pennies"], gather_skill=row["gather_skill"])
        if row["value_pennies"] is not None
        else None
    )
    meta = {
        "type": row["type"],
        "title": row["title"],
        "status": "draft",
        "generated": {"by": f"kampagne:{campaign_id}"},
    }
    return Page(
        page_id=row["page_id"],
        path=f"kampagne/{row['page_id']}.md",
        type=row["type"],
        title=row["title"],
        description=None,
        status="draft",
        game=GameFields(occurs=occurs, item=item),
        body=row["text"],
        meta=meta,
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
    if result in ("success", "critical_success"):
        # Die Folgen, wie beim Öffnen festgehalten. Eine Wissensstufe gilt, wenn das Gesamt sie erreicht;
        # die Stufe der Probe gilt bei jedem Erfolg, auch bei der natürlichen 20.
        reach = max(total, open_roll["dc"])
        for consequence in CONSEQUENCES.validate_json(open_roll["on_success_json"]):
            if not isinstance(consequence, LearnKnowledge) or consequence.dc <= reach:
                effects.append(consequence)
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
