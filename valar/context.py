"""Kontextbauer (Schritt 1j, Entscheidungen 137 bis 144): was das Modell in einem Zug über die Welt bekommt.

Klein genug für kleine Modelle: ein fester Zustandsblock, dazu Weltwissen in fester Rangfolge bis zu einem
Budget. Gleicher Spielstand und gleiche Eingabe ergeben denselben Kontext. Wissensstufen, die die Figur nicht
kennt, stehen nie darin. Der Kontextbauer liest nur.
"""

import math
import re
import sqlite3
from dataclasses import dataclass
from typing import Literal

from valar import queries, rules
from valar.content import CONTENT, Content
from valar.resolve import campaign_wiki
from valar.wiki import Page, Wiki, default_wiki, flat_text, linked_pages

WORLD_BUDGET = 2000  # Tokens für Weltwissen je Zug (Entscheidung 138, Vorschlag)
MAX_OCCURRING = 6  # Dinge am Ort, häufige zuerst
MAX_FACTS = 5
SUMMARY_CHARS = 240  # Ersatz, wenn eine Seite keine `description` hat

Level = Literal["full", "summary"]

# Die Reihenfolge, in der Aktionen angeboten werden (Entscheidung 142).
ACTION_ORDER = (
    "narrate",
    "move",
    "examine",
    "gather",
    "meet_npc",
    "talk",
    "contest",
    "attempt",
    "submit_quest_items",
    "record_fact",
    "mark_scarcity",
    "create_page",
    "sleep",
    "raise_attribute",
)


def estimate_tokens(text: str) -> int:
    """Grobe Schätzung ohne Tokenizer: vier Zeichen je Token, aufgerundet. Genügt für das Budget."""
    return math.ceil(len(text) / 4)


@dataclass(frozen=True)
class Item:
    """Ein Eintrag Weltwissen im Kontext."""

    section: str  # ort, figur, leute, notizen, genannt, gepaeck, vorkommen, verweis
    key: str  # Seiten-ID, oder figur, npc:ID bzw. fakt:N
    level: Level
    text: str

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


@dataclass(frozen=True)
class TurnContext:
    state: str  # Zustand der Figur, immer dabei, zählt nicht zum Budget
    items: tuple[Item, ...]
    dropped: tuple[tuple[str, str], ...]  # (Schlüssel, Grund)
    actions: tuple[str, ...]
    budget: int

    @property
    def tokens(self) -> int:
        return sum(item.tokens for item in self.items)

    def manifest(self) -> dict:
        """Was ins Modell ging, für den Spielzug (Entscheidung 143)."""
        return {
            "pages": [[item.key, item.level] for item in self.items],
            "dropped": [list(entry) for entry in self.dropped],
            "tokens": self.tokens,
            "budget": self.budget,
        }

    def render(self) -> str:
        titles = {
            "ort": "Ort",
            "figur": "Die Figur",
            "leute": "Leute hier",
            "notizen": "Notizen der Figur",
            "genannt": "Genannt",
            "gepaeck": "Gepäck",
            "vorkommen": "Hier kommt vor",
            "verweis": "Verweise",
        }
        lines = ["# Zustand", self.state]
        section = None
        for item in self.items:
            if item.section != section:
                section = item.section
                lines += ["", f"# {titles[section]}"]
            lines.append(item.text if item.level == "full" else f"- {item.text}")
        lines += ["", "# Mögliche Aktionen", ", ".join(self.actions)]
        return "\n".join(lines)


def build_context(
    conn: sqlite3.Connection,
    *,
    campaign_id: str,
    player_id: str,
    content: Content = CONTENT,
    wiki: Wiki | None = None,
    player_input: str = "",
    narration: str = "",
    budget: int = WORLD_BUDGET,
) -> TurnContext:
    """Baut den Kontext eines Zugs. `player_input` und `narration` (die letzte Erzählung) lösen genannte Seiten aus."""
    world = campaign_wiki(conn, campaign_id, wiki or default_wiki())
    status = queries.status(conn, campaign_id=campaign_id, player_id=player_id, content=content)
    seen = queries.look(conn, campaign_id=campaign_id, player_id=player_id, content=content, wiki=wiki)
    notes = queries.facts(conn, campaign_id=campaign_id, player_id=player_id)
    player = conn.execute(
        "SELECT * FROM player WHERE campaign_id = ? AND player_id = ?", (campaign_id, player_id)
    ).fetchone()

    def known(page: Page) -> list[str]:
        return queries.known_texts(conn, campaign_id, player, page)  # nur gelernte oder ohne Wurf sichtbare Stufen

    candidates: list[tuple[str, str, list[tuple[Level, str]]]] = []

    # 1. Ort: die Seite des Orts ganz, mit dem, was die Figur über ihn weiß.
    place = _place_page(world, content, seen.location_id)
    if place is not None:
        candidates.append(("ort", place.page_id, _levels(place, known(place), full=True)))

    # Hintergrund und persönlicher Gegenstand der Figur (weicher Zustand aus der Erschaffung, Entscheidung 151).
    if status.background or status.keepsake:
        parts = [f"Hintergrund: {status.background}"] if status.background else []
        parts += [f"Persönlicher Gegenstand: {status.keepsake}"] if status.keepsake else []
        candidates.append(("figur", "figur", [("full", " ".join(parts))]))

    # 2. Anwesende NPCs, mit dem Satz ihrer Vorlage.
    present = {npc.npc_id: npc for npc in seen.npcs}
    for npc in seen.npcs:
        template = world.pages.get(npc.template_id)
        extra = f" {template.description}" if template and template.description else ""
        candidates.append(("leute", f"npc:{npc.npc_id}", [("summary", f"{npc.name} ({npc.template_title}): {npc.description}.{extra}")]))

    # 3. Notizen: zuerst die zum Ort, zu anwesenden NPCs und zu Genanntem, dann die neuesten.
    mentioned = _mentioned(world, f"{player_input}\n{narration}")
    relevant_keys = {place.page_id if place else None, *present, *(p.page_id for p in mentioned)}
    ordered = sorted(
        enumerate(notes),
        key=lambda pair: (pair[1].subject not in relevant_keys and pair[1].source_npc_id not in present, -pair[0]),
    )
    for index, note in ordered[:MAX_FACTS]:
        kind = "Gerücht" if note.kind == "rumor" else "Fakt"
        source = f" (von {present[note.source_npc_id].name})" if note.source_npc_id in present else ""
        candidates.append(("notizen", f"fakt:{index + 1}", [("summary", f"{kind}: {note.text}{source}")]))

    # 4. Genannte Seiten: ganz, wenn Platz ist.
    for page in mentioned:
        candidates.append(("genannt", page.page_id, _levels(page, known(page), full=True)))

    # 5. Gepäck mit eigener Seite.
    for item_id in status.inventory:
        if item_id in world.pages:
            candidates.append(("gepaeck", item_id, _levels(world.pages[item_id], known(world.pages[item_id]))))

    # 6. Was am Ort vorkommt: häufige zuerst, höchstens einige.
    for thing in seen.things[:MAX_OCCURRING]:
        page = world.pages[thing.page_id]
        candidates.append(("vorkommen", page.page_id, _levels(page, thing.known)))

    # 7. Verweise eine Stufe weit, vom Ort und von genannten Seiten.
    for source in ([place] if place else []) + mentioned:
        for page_id in linked_pages(source, world):
            candidates.append(("verweis", page_id, _levels(world.pages[page_id], known(world.pages[page_id]))))

    items, dropped, used, remaining = [], [], set(), budget
    for section, key, levels in candidates:
        if key in used:
            continue
        for level, text in levels:
            if estimate_tokens(text) <= remaining:
                items.append(Item(section, key, level, text))
                used.add(key)
                remaining -= estimate_tokens(text)
                break
        else:
            dropped.append((key, "budget"))
            used.add(key)

    return TurnContext(
        state=_state(status, seen),
        items=tuple(items),
        dropped=tuple(dropped),
        actions=_actions(status, seen),
        budget=budget,
    )


def _place_page(world: Wiki, content: Content, location_id: str) -> Page | None:
    if location_id not in content.locations:
        return None
    return world.surroundings(content, location_id).location


def _summary(page: Page) -> str:
    if page.description:
        return f"{page.title}: {page.description}"
    text = " ".join(flat_text(page.body).split())
    return f"{page.title}: {text[:SUMMARY_CHARS].rstrip()}{'…' if len(text) > SUMMARY_CHARS else ''}"


def _levels(page: Page, known: list[str], *, full: bool = False) -> list[tuple[Level, str]]:
    """Die möglichen Stufen eines Eintrags, die ausführlichste zuerst. Gewusstes hängt immer an."""
    knowledge = "".join(f" Die Figur weiß: {text}" for text in known)
    levels: list[tuple[Level, str]] = []
    if full:
        levels.append(("full", f"{page.title}: {flat_text(page.body)}{knowledge}"))
    levels.append(("summary", _summary(page) + knowledge))
    return levels


def _mentioned(world: Wiki, text: str) -> list[Page]:
    """Seiten, deren Titel, ID oder Alias als ganzes Wort im Text steht, in der Reihenfolge ihres Vorkommens."""
    if not text.strip():
        return []
    lowered = text.casefold()
    found: list[tuple[int, str, Page]] = []
    for page in world.pages.values():
        if page.status == "deprecated" or page.type in ("Region", "Landschaft"):
            continue
        for name in (page.title, page.page_id.replace("-", " "), *page.game.aliases):
            match = re.search(rf"(?<!\w){re.escape(name.casefold())}(?!\w)", lowered)
            if match:
                found.append((match.start(), page.page_id, page))
                break
    return [page for _, _, page in sorted(found)]


def _state(status: queries.StatusResult, seen: queries.LookResult) -> str:
    """Der Zustand der Figur als kurzer, fester Block (Entscheidung 141)."""
    c = status.condition
    lines = [
        f"Ort: {seen.name} · Tag {status.clock.day}, {status.clock.time} · Wetter: {c.weather}",
        f"Figur: {status.name} · Hunger: {c.hunger_name} · Erschöpfung: {c.exhaustion_name}"
        f" · Geld: {rules.format_money(status.money_pennies)}",
    ]
    if status.inventory:
        lines.append("Gepäck: " + ", ".join(f"{q} {item}" for item, q in status.inventory.items()))
    active = [quest for quest, state in status.quests.items() if state == "active"]
    if active:
        lines.append("Aufträge: " + ", ".join(active))
    if seen.exits:
        lines.append("Wege: " + ", ".join(f"{e.name} ({e.minutes} Min)" for e in seen.exits))
    if status.open_roll:
        roll = status.open_roll
        lines.append(
            f"Offene Probe: {rules.SKILLS[roll.skill_id].name if roll.skill_id in rules.SKILLS else roll.skill_id},"
            f" SG {roll.dc}, Bonus {roll.bonus:+d}, Grund: {roll.reason}. Der Spieler würfelt."
        )
    return "\n".join(lines)


def _actions(status: queries.StatusResult, seen: queries.LookResult) -> tuple[str, ...]:
    """Nur was gerade erlaubt ist (Entscheidung 142). Bei offener Probe würfelt der Spieler; das Modell erzählt."""
    if status.open_roll:
        return ("narrate",)
    allowed = {"narrate", "attempt", "record_fact", "sleep", "meet_npc", "create_page"}
    if seen.exits:
        allowed.add("move")
    if seen.things or seen.known_here or seen.regions:
        allowed.update({"examine", "mark_scarcity"})
    if any(thing.gatherable for thing in seen.things):
        allowed.add("gather")
    if seen.npcs:
        allowed.update({"talk", "contest"})
    if any(state == "active" for state in status.quests.values()):
        allowed.add("submit_quest_items")
    if status.attribute_points:
        allowed.add("raise_attribute")
    return tuple(action for action in ACTION_ORDER if action in allowed)
