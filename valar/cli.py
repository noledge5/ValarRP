"""Kommandozeile ohne Modell (Schritt 1d, Entscheidung 65): deutsche Befehle, nur Standardbibliothek.

Werkzeug für Autor und Tests. Liest Zeile für Zeile von der Standardeingabe, interaktiv oder per Skript:

    python -m valar.cli --db spiel.db
    python -m valar.cli --db spiel.db < ablauf.txt

Gibt es die Kampagne in der Datei noch nicht, legt der Code sie mit der Testfigur aus dem Inhalt an.
Die Kommandozeile übersetzt nur Eingaben in Aktionen und zeigt Ergebnisse; die Regeln bleiben im Kern.
"""

import argparse
import re
import shlex
import sqlite3
import sys
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TextIO

from valar import apply, db, queries, rules
from valar.content import CONTENT, Content
from valar.models import (
    Action,
    AttemptAction,
    AttemptDuration,
    Contest,
    CreatePage,
    DifficultyId,
    MarkScarcity,
    Examine,
    Gather,
    MeetNpc,
    Move,
    RecordFact,
    SubmitQuestItems,
    Sleep,
    SubmitRoll,
    Talk,
    TurnResult,
)
from valar.resolve import campaign_wiki
from valar.turns import submit_turn
from valar.wiki import Wiki, default_wiki

CAMPAIGN = "kampagne-1"

HELP = """Befehle:
  schau                         umsehen (kein Spielzug)
  status                        Figur, Zeit und offene Probe (kein Spielzug)
  gehe ZIEL                     zu einem verbundenen Ort, z. B. gehe Sturmhagen
  betrachte DING                was die Figur über etwas am Ort weiß (kein Spielzug)
  untersuche DING               Probe, um mehr zu erfahren, z. B. untersuche Glasgrund
  sammle DING                   Probe, um etwas zu sammeln, z. B. sammle Sturmkappe
  triff VORLAGE NAME BESCHREIBUNG
                                neuen NPC ansprechen, z. B. triff Bauer "Grete Müller" Alte Bäuerin mit Kiepe
  rede NPC                      mit einem NPC am Ort reden, z. B. rede Grete
  notiere [gerücht] TEXT        weichen Fakt notieren, z. B. notiere gerücht Im Moor spukt es
  notizen                       notierte Fakten und Gerüchte (kein Spielzug)
  seite VORLAGE TITEL TEXT      neue Kampagnen-Seite, VORLAGE: wissen, brauch, ereignis, tier, pflanze,
                                heilkraut, seltenes-kraut; z. B. seite heilkraut "Graue Minze" Text …
  versuche SKILL STUFE DAUER GRUND
                                Probe öffnen, z. B. versuche Klingenwaffen 3 stunde Er bricht die Tür auf
                                SKILL: Name oder SKILL-101; Namen mit Leerzeichen in Anführungszeichen
                                STUFE: 1 bis 7, DIFF-03 oder leicht, durchschnitt, schwer, extrem …
                                DAUER: augenblick, stunde, halbtag
  vergleiche SKILL NPC DAUER GRUND
                                Vergleichsprobe gegen einen NPC am Ort, z. B. vergleiche Schleichen Jost augenblick
                                An ihm vorbei; der NPC würfelt, sein Gesamt ist der SG
  wurf ZAHL                     eigenen W20-Wurf eintragen, 1 bis 20
  schlafe                       schlafen bis 6 Uhr, höchstens 12 Stunden
  knapp DING STUFE DAUER GRUND  erzählte Knappheit am Ort: STUFE knapp oder weg, DAUER tag, woche, monat;
                                z. B. knapp Blutwurz weg woche Die Kräuterfrau hat alles geerntet
  abgeben [AUFTRAG]             Auftrag abgeben; ohne Angabe der einzige aktive
  hilfe                         diese Übersicht
  ende                          beenden; der Spielstand bleibt in der Datei"""

DIFFICULTY_WORDS: dict[str, DifficultyId] = {
    "sehr-leicht": "DIFF-01",
    "leicht": "DIFF-02",
    "durchschnitt": "DIFF-03",
    "mittel": "DIFF-03",
    "schwer": "DIFF-04",
    "sehr-schwer": "DIFF-05",
    "extrem": "DIFF-06",
    "legendär": "DIFF-07",
}
DIFFICULTY_NAMES = {
    "DIFF-01": "sehr leicht",
    "DIFF-02": "leicht",
    "DIFF-03": "durchschnitt",
    "DIFF-04": "schwer",
    "DIFF-05": "sehr schwer",
    "DIFF-06": "extrem",
    "DIFF-07": "legendär",
}
DURATION_WORDS: dict[str, AttemptDuration] = {
    "augenblick": "moment",
    "moment": "moment",
    "stunde": "hour",
    "halbtag": "half_day",
    "halber-tag": "half_day",
}
DURATION_NAMES = {"moment": "Augenblick", "hour": "Stunde", "half_day": "halber Tag"}
FREQUENCY_NAMES = {"common": "häufig", "uncommon": "gelegentlich", "rare": "selten", "very_rare": "sehr selten"}
OUTCOME_TEXT = {
    "critical_success": "kritischer Erfolg",
    "success": "Erfolg",
    "near_miss": "knapp daneben",
    "failure": "Fehlschlag",
    "fumble": "Patzer",
}


class InputError(ValueError):
    """Falsche Eingabe: Hilfetext, kein Spielzug."""


@dataclass(frozen=True)
class Query:
    name: str  # "look", "status", "examine" oder "facts"
    arg: str | None = None  # bei "examine": die Seite


@dataclass(frozen=True)
class Turn:
    action: Action


@dataclass(frozen=True)
class Help:
    pass


@dataclass(frozen=True)
class Quit:
    pass


Command = Query | Turn | Help | Quit


def format_money(pennies: int) -> str:
    """Pfennig als Gulden, Groschen und Pfennig (1 : 10 : 100, Entscheidung 32)."""
    gulden, rest = divmod(pennies, 100)
    groschen, pfennig = divmod(rest, 10)
    parts = [f"{gulden} Gulden"] if gulden else []
    parts += [f"{groschen} Groschen"] if groschen else []
    parts += [f"{pfennig} Pfennig"] if pfennig or not parts else []
    return ", ".join(parts)


def format_distance(meters: int) -> str:
    return f"{meters / 1000:.1f} km".replace(".", ",") if meters >= 1000 else f"{meters} m"


def plain(text: str) -> str:
    """Markdown-Links als bloßer Text: [Glasgrund](/orte/glasgrund.md) → Glasgrund."""
    return re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)


def _key(text: str) -> str:
    return text.casefold().replace(" ", "").replace("-", "").replace("_", "")


def _find(word: str, names: dict[str, str], what: str) -> str:
    """ID zu einer Eingabe: die ID selbst oder der Name, ohne Groß- und Kleinschreibung und Leerzeichen."""
    wanted = _key(word)
    for item_id, name in names.items():
        if wanted in (_key(item_id), _key(name)):
            return item_id
    raise InputError(f"{what} {word!r} kenne ich nicht.")


@dataclass(frozen=True)
class Names:
    """Was sich per Name statt ID nennen lässt (ID → Name)."""

    pages: dict[str, str]  # Seiten am Ort
    npcs: dict[str, str]  # NPCs am Ort
    templates: dict[str, str]  # NPC-Vorlagen


def parse(line: str, content: Content, status: queries.StatusResult, names: Names | None = None) -> Command | None:
    """Übersetzt eine Eingabezeile. None bei leerer Zeile. Falsche Eingabe: InputError."""
    names = names or Names({}, {}, {})
    try:
        words = shlex.split(line, comments=True)
    except ValueError as error:  # z. B. offenes Anführungszeichen
        raise InputError(f"Eingabe nicht lesbar: {error}") from None
    if not words:
        return None
    command, args = words[0].casefold(), words[1:]

    match command:
        case "hilfe" | "?":
            return Help()
        case "ende" | "beenden":
            return Quit()
        case "schau" | "umsehen":
            return Query("look")
        case "status" | "zustand":
            return Query("status")
        case "gehe" | "geh":
            if not args:
                raise InputError("Wohin? Zum Beispiel: gehe Sturmhagen")
            target = " ".join(args)
            names = {key: location.name for key, location in content.locations.items()}
            try:
                destination = _find(target, names, "Ort")
            except InputError:
                destination = target  # der Kern lehnt den unbekannten Ort ab (Spielzug)
            return Turn(Move(destination_id=destination))
        case "betrachte" | "untersuche" | "sammle":
            if not args:
                raise InputError(f"Was? Zum Beispiel: {command} Glasgrund")
            wanted = " ".join(args)
            try:
                page_id = _find(wanted, names.pages, "Ding")
            except InputError:
                page_id = wanted  # der Kern entscheidet, ob es das hier gibt
            if command == "betrachte":
                return Query("examine", page_id)
            return Turn(_build(Examine if command == "untersuche" else Gather, page_id=page_id))
        case "triff":
            if len(args) < 3:
                raise InputError('Treffen braucht VORLAGE NAME BESCHREIBUNG, z. B. triff Bauer "Grete Müller" Alte Bäuerin')
            try:
                template_id = _find(args[0], names.templates, "Vorlage")
            except InputError:
                template_id = args[0]  # der Kern lehnt die unbekannte Vorlage ab
            return Turn(_build(MeetNpc, template_id=template_id, name=args[1], description=" ".join(args[2:])))
        case "rede":
            if not args:
                raise InputError("Mit wem? Zum Beispiel: rede Grete")
            wanted = " ".join(args)
            found = [i for i, n in names.npcs.items() if _key(wanted) in (_key(i), _key(n), _key(n.split()[0]))]
            return Turn(Talk(npc_id=found[0] if len(found) == 1 else wanted))
        case "notiere":
            rumor = bool(args) and args[0].casefold() in ("gerücht", "geruecht")
            text = " ".join(args[1:] if rumor else args)
            if not text:
                raise InputError("Was? Zum Beispiel: notiere gerücht Im Moor spukt es")
            return Turn(_build(RecordFact, kind="rumor" if rumor else "fact", text=text))
        case "notizen":
            return Query("facts")
        case "seite":
            if len(args) < 3:
                raise InputError('Seite braucht VORLAGE TITEL TEXT, z. B. seite heilkraut "Graue Minze" Wächst am Bach')
            return Turn(_build(CreatePage, template=args[0].casefold(), title=args[1], text=" ".join(args[2:])))
        case "versuche":
            if len(args) < 4:
                raise InputError("Versuch braucht SKILL STUFE DAUER GRUND.")
            names = {key: skill.name for key, skill in rules.SKILLS.items()}
            if args[0].upper().startswith("SKILL-"):
                skill_id = args[0].upper()  # der Kern lehnt unbekannte Skills ab (Spielzug)
            else:
                skill_id = _find(args[0], names, "Skill")
            difficulty = _difficulty(args[1])
            duration = DURATION_WORDS.get(args[2].casefold())
            if duration is None:
                raise InputError(f"Dauer {args[2]!r} kenne ich nicht: augenblick, stunde oder halbtag.")
            reason = " ".join(args[3:])
            return Turn(_build(AttemptAction, skill_id=skill_id, difficulty=difficulty, duration=duration, reason=reason))
        case "vergleiche":
            if len(args) < 4:
                raise InputError("Vergleichsprobe braucht SKILL NPC DAUER GRUND.")
            skill_names = {key: skill.name for key, skill in rules.SKILLS.items()}
            skill_id = args[0].upper() if args[0].upper().startswith("SKILL-") else _find(args[0], skill_names, "Skill")
            found = [i for i, n in names.npcs.items() if _key(args[1]) in (_key(i), _key(n), _key(n.split()[0]))]
            duration = DURATION_WORDS.get(args[2].casefold())
            if duration is None:
                raise InputError(f"Dauer {args[2]!r} kenne ich nicht: augenblick, stunde oder halbtag.")
            npc_id = found[0] if len(found) == 1 else args[1]
            return Turn(
                _build(Contest, skill_id=skill_id, npc_id=npc_id, duration=duration, reason=" ".join(args[3:]))
            )
        case "schlafe" | "schlafen":
            return Turn(Sleep())
        case "knapp":
            if len(args) < 4:
                raise InputError("Knappheit braucht DING STUFE DAUER GRUND.")
            try:
                page_id = _find(args[0], names.pages, "Ding")
            except InputError:
                page_id = args[0]
            level = {"knapp": "scarce", "weg": "gone"}.get(args[1].casefold())
            duration = {"tag": "day", "woche": "week", "monat": "month"}.get(args[2].casefold())
            if level is None or duration is None:
                raise InputError("STUFE: knapp oder weg; DAUER: tag, woche oder monat.")
            return Turn(_build(MarkScarcity, page_id=page_id, level=level, duration=duration, reason=" ".join(args[3:])))
        case "wurf":
            if len(args) != 1 or not args[0].isdigit() or not 1 <= int(args[0]) <= 20:
                raise InputError("Wurf: eine ganze Zahl von 1 bis 20, z. B. wurf 14")
            return Turn(SubmitRoll(value=int(args[0])))
        case "abgeben":
            if args:
                return Turn(_build(SubmitQuestItems, quest_id=args[0]))
            active = [quest for quest, state in status.quests.items() if state == "active"]
            if len(active) != 1:
                raise InputError("Welcher Auftrag? Zum Beispiel: abgeben guild_shard_order")
            return Turn(SubmitQuestItems(quest_id=active[0]))
        case _:
            raise InputError(f"Befehl {words[0]!r} kenne ich nicht.")


def _difficulty(word: str) -> DifficultyId:
    upper = word.upper()
    if upper in rules.DIFFICULTIES:
        return upper
    if word.isdigit() and 1 <= int(word) <= len(rules.DIFFICULTIES):
        return f"DIFF-{int(word):02d}"
    found = DIFFICULTY_WORDS.get(word.casefold())
    if found is None:
        raise InputError(f"Stufe {word!r} kenne ich nicht: 1 bis 7, DIFF-03 oder leicht, schwer …")
    return found


def _build(model: Callable[..., Action], **fields) -> Action:
    """Baut die Aktion; was das Schema ablehnt (z. B. zu langer Grund), ist eine falsche Eingabe."""
    try:
        return model(**fields)
    except ValueError as error:
        raise InputError(str(error).splitlines()[-1].strip()) from None


class Session:
    """Eine Spielsitzung an einer Datenbank. Merkt sich die Revision wie ein echter Client."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        out: TextIO,
        *,
        campaign_id: str = CAMPAIGN,
        content: Content = CONTENT,
        wiki: Wiki | None = None,
        new_request_id: Callable[[], str] = lambda: f"cli-{uuid.uuid4().hex}",
    ) -> None:
        self.conn = conn
        self.out = out
        self.campaign_id = campaign_id
        self.content = content
        self.wiki = wiki
        self.new_request_id = new_request_id
        exists = conn.execute("SELECT 1 FROM campaign WHERE campaign_id = ?", (campaign_id,)).fetchone()
        if not exists:
            apply.create_campaign_from_content(conn, campaign_id, content)
            self.say("Neue Kampagne mit der Testfigur angelegt.")
        self.player_id = content.start_player.player_id
        self.revision = self.status().revision

    def say(self, text: str = "") -> None:
        print(text, file=self.out)

    def status(self) -> queries.StatusResult:
        return queries.status(self.conn, campaign_id=self.campaign_id, player_id=self.player_id, content=self.content)

    def look(self) -> queries.LookResult:
        return queries.look(
            self.conn, campaign_id=self.campaign_id, player_id=self.player_id, content=self.content, wiki=self.wiki
        )

    def names(self) -> Names:
        """Was man am Ort per Name nennen kann: Ort, Dinge, NPCs; dazu die NPC-Vorlagen."""
        seen = self.look()
        pages = {thing.page_id: thing.title for thing in seen.things}
        location = self.content.locations.get(seen.location_id)
        while location is not None and location.wiki is None and location.parent_id:
            location = self.content.locations[location.parent_id]
        if location is not None and location.wiki:
            pages[location.wiki] = location.name
        wiki = self.wiki or default_wiki()
        templates = {p.page_id: p.title for p in wiki.pages.values() if p.game.template is not None}
        everyone = queries.npcs(self.conn, campaign_id=self.campaign_id, wiki=self.wiki)
        return Names(pages, {npc.npc_id: npc.name for npc in everyone}, templates)

    def title(self, page_id: str) -> str:
        """Titel einer Seite, auch einer Kampagnen-Seite; sonst die ID."""
        world = campaign_wiki(self.conn, self.campaign_id, self.wiki or default_wiki())
        page = world.pages.get(page_id)
        return page.title if page else page_id

    def prompt(self) -> str:
        status = self.status()
        roll = " · Probe offen" if status.open_roll else ""
        return f"[Tag {status.clock.day}, {status.clock.time} · {status.location_name}{roll}] > "

    def handle(self, line: str) -> bool:
        """Verarbeitet eine Eingabezeile. False heißt beenden."""
        try:
            command = parse(line, self.content, self.status(), self.names())
        except InputError as error:
            self.say(f"{error}\n{HELP}")
            return True
        match command:
            case None:
                pass
            case Help():
                self.say(HELP)
            case Quit():
                return False
            case Query("look"):
                self.show_look()
            case Query("examine", page_id):
                self.show_examine(page_id)
            case Query("facts"):
                self.show_facts()
            case Query():
                self.show_status()
            case Turn(action=action):
                result = self.play(action)
                if isinstance(action, Talk) and result.status == "accepted":
                    everyone = queries.npcs(self.conn, campaign_id=self.campaign_id, wiki=self.wiki)
                    npc = next(n for n in everyone if n.npc_id == action.npc_id)
                    self.say(f"Du sprichst mit {npc.name} ({npc.template_title}). {npc.description}")
        return True

    def play(self, action: Action) -> TurnResult:
        """Reicht die Aktion ein. Veraltete Revision: Stand neu lesen, einmal mit neuer request_id erneut."""
        result = self._submit(action)
        if result.reject_code == "stale_revision":
            self.revision = self.status().revision
            self.say("(Spielstand war veraltet, neu gelesen.)")
            result = self._submit(action)
        if result.status != "rejected":
            self.revision = result.revision
        self.show_result(result)
        return result

    def _submit(self, action: Action) -> TurnResult:
        return submit_turn(
            self.conn,
            campaign_id=self.campaign_id,
            player_id=self.player_id,
            request_id=self.new_request_id(),
            expected_revision=self.revision,
            action=action,
            content=self.content,
            wiki=self.wiki,
        )

    # --- Anzeige ---

    def show_look(self) -> None:
        seen = self.look()
        parent = self.content.locations.get(seen.parent_id) if seen.parent_id else None
        self.say(f"{seen.name}" + (f" (in {parent.name})" if parent else "") + f" · Tag {seen.clock.day}, {seen.clock.time}")
        self.say(seen.description)
        if seen.others_here:
            self.say("Hier: " + ", ".join(seen.others_here))
        for npc in seen.npcs:
            self.say(f"Leute: {npc.name} ({npc.template_title}): {npc.description}")
        self.say("Wege:")
        for exit_ in seen.exits:
            direction = f"{exit_.direction}, " if exit_.direction else ""
            self.say(f"  {exit_.name} ({direction}{format_distance(exit_.distance_m)}, {exit_.minutes} Min)")
        if not seen.exits:
            self.say("  keine")
        if seen.regions:
            self.say(f"Region: {seen.regions[0]}" + (f" · Landschaft: {', '.join(seen.landscapes)}" if seen.landscapes else ""))
        for text in seen.known_here:
            self.say(f"  Du weißt: {text}")
        if seen.things:
            self.say("Hier kommt vor:")
        for thing in seen.things:
            mark = ", sammelbar" if thing.gatherable else ""
            self.say(f"  {thing.title} ({thing.type}, {FREQUENCY_NAMES[thing.frequency]}{mark})")

    def show_facts(self) -> None:
        notes = queries.facts(self.conn, campaign_id=self.campaign_id, player_id=self.player_id)
        for note in notes:
            day, minute = rules.clock(note.game_minutes, self.content.start_minute_of_day)
            kind = "Gerücht" if note.kind == "rumor" else "Fakt"
            self.say(f"  Tag {day}, {minute // 60:02d}:{minute % 60:02d} · {kind}: {note.text}")
        if not notes:
            self.say("Noch nichts notiert.")

    def show_examine(self, page_id: str) -> None:
        try:
            seen = queries.examine(
                self.conn,
                campaign_id=self.campaign_id,
                player_id=self.player_id,
                page_id=page_id,
                content=self.content,
                wiki=self.wiki,
            )
        except queries.UnknownPage:
            self.say("Das gibt es hier nicht.")
            return
        self.say(f"{seen.title} ({seen.type})")
        self.say(plain(seen.text))
        for text in seen.known:
            self.say(f"  Du weißt: {text}")

    def show_status(self) -> None:
        status = self.status()
        self.say(f"{status.name} · Level {status.level} · {status.location_name}")
        self.say(f"Tag {status.clock.day}, {status.clock.time} · Revision {status.revision}")
        self.say(f"Geld: {format_money(status.money_pennies)}")
        self.say("Attribute: " + ", ".join(f"{k} {v}" for k, v in status.attributes.items()))
        c = status.condition
        self.say(
            f"Hunger: {c.hunger_name} · Erschöpfung: {c.exhaustion_name} ({c.awake_hours} Std. wach)"
            f" · Wetter: {c.weather}" + (f" · Malus {c.malus:+d} auf Proben" if c.malus else "")
        )
        if status.attribute_points:
            self.say(f"Attributspunkte im Vorrat: {status.attribute_points}")
        for skill in status.skills:
            used = " · Ticks in dieser Szene vergeben" if skill.ticked_this_scene else ""
            self.say(f"  {skill.name} {skill.value} ({skill.ticks} Ticks){used}")
        if status.inventory:
            self.say("Gepäck: " + ", ".join(f"{q} {self.title(item)}" for item, q in status.inventory.items()))
        for quest, state in status.quests.items():
            self.say(f"Auftrag {quest}: {'aktiv' if state == 'active' else 'erledigt'}")
        if status.open_roll:
            self.show_open_roll(status.open_roll)

    def show_open_roll(self, roll: queries.OpenRollView) -> None:
        name = rules.SKILLS[roll.skill_id].name if roll.skill_id in rules.SKILLS else roll.skill_id
        if roll.npc_roll:
            npc = roll.npc_roll
            who = {n.npc_id: n.name for n in queries.npcs(self.conn, campaign_id=self.campaign_id, wiki=self.wiki)}
            self.say(
                f"Gegenprobe: {who.get(npc.npc_id, npc.npc_id)} würfelt {npc.die} {npc.bonus:+d} = {npc.total}"
                f" ({rules.SKILLS[npc.skill_id].name}). Das ist der SG; Gleichstand gewinnst du."
            )
            self.say(
                f"Probe offen: {name}, SG {roll.dc}, Bonus {roll.bonus:+d},"
                f" nötig {max(roll.dc - roll.bonus, 1)}+, Ticks bei Erfolg {roll.ticks_on_success}, Dauer {roll.minutes} Min"
            )
        else:
            self.say(
                f"Probe offen: {name}, SG {roll.dc} ({DIFFICULTY_NAMES[roll.difficulty]}), Bonus {roll.bonus:+d},"
                f" nötig {max(roll.dc - roll.bonus, 1)}+, Ticks bei Erfolg {roll.ticks_on_success}, Dauer {roll.minutes} Min"
            )
        if roll.ticks_on_success == 0 and rules.DIFFICULTIES[roll.difficulty].ticks and roll.dc >= 8:
            routine = roll.dc - roll.bonus <= rules.ROUTINE_NEEDED
            self.say("Keine Ticks: " + ("Routine." if routine else "Skill hat in dieser Szene schon Ticks gebracht."))
        self.say(f"Grund: {roll.reason}")
        self.say("Jetzt würfeln: wurf ZAHL")

    def show_result(self, result: TurnResult) -> None:
        if result.status == "rejected":
            self.say(f"Abgelehnt: {result.message} ({result.reject_code})")
            return
        if result.status == "awaiting_roll":
            status = self.status()
            if status.open_roll:
                self.show_open_roll(status.open_roll)
            return
        if result.check:
            check = result.check
            ups = f", +{check.skill_points} Skillpunkt" if check.skill_points else ""
            self.say(
                f"W20 {check.die} {check.bonus:+d} = {check.total} gegen SG {check.dc}:"
                f" {OUTCOME_TEXT[check.outcome]}, {check.ticks} Ticks{ups}"
            )
        for effect in result.effects:
            match effect.type:
                case "enter_location":
                    self.say(f"Angekommen: {self.content.locations[effect.location_id].name}. Neue Szene.")
                case "add_money":
                    self.say(f"Erhalten: {format_money(effect.pennies)}")
                case "set_quest_status" if effect.status == "completed":
                    self.say(f"Auftrag erledigt: {effect.quest_id}")
                case "grant_attribute_points":
                    self.say(f"Levelaufstieg: +{effect.points} Attributspunkt")
                case "add_items":
                    self.say(f"Gesammelt: {effect.quantity} {self.title(effect.item_id)}")
                case "learn_knowledge":
                    self.say(f"Neu erfahren: {self._stage_text(effect)}")
                case "create_npc":
                    self.say(f"Neu: {effect.name} ({self.title(effect.template_id)}), ID {effect.npc_id}")
                case "add_fact":
                    self.say("Notiert" + (" (Gerücht)." if effect.kind == "rumor" else "."))
                case "day_change":
                    mine = next((p for p in effect.players if p.player_id == self.player_id), None)
                    food = "" if mine is None else (
                        " Ration gegessen." if mine.ate else f" Keine Ration: {rules.HUNGER_NAMES[mine.hunger]}."
                    )
                    self.say(f"Mitternacht: Tag {effect.day}, Wetter: {effect.weather}.{food}")
                case "rest":
                    left = f" Noch {rules.EXHAUSTION_NAMES[effect.debt]}." if effect.debt else " Ausgeruht."
                    self.say("Geschlafen." + left)
                case "add_scarcity":
                    self.say(f"Vermerkt: {self.title(effect.page_id)} ist hier {'knapp' if effect.level == 'scarce' else 'weg'}.")
                case "create_campaign_page":
                    self.say(f"Neue Kampagnen-Seite: {effect.title} ({effect.page_type}), ID {effect.page_id}")
        status = self.status()
        self.say(f"Jetzt: Tag {status.clock.day}, {status.clock.time}")


    def _stage_text(self, effect) -> str:
        wiki = self.wiki or default_wiki()
        page = wiki.pages.get(effect.page_id)
        stages = page.game.knowledge if page else ()
        return next(
            (s.text for s in stages if (s.skill, s.difficulty) == (effect.skill_id, effect.difficulty)),
            effect.page_id,
        )


def run(lines: Iterable[str], session: Session, *, echo: bool) -> None:
    """Liest Zeilen bis `ende` oder Dateiende. `echo` zeigt Eingabeaufforderung und Eingabe (für Skripte)."""
    for line in lines:
        if echo:
            session.say(session.prompt() + line.rstrip("\n"))
        if not session.handle(line):
            break


def main(argv: list[str] | None = None, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> int:
    parser = argparse.ArgumentParser(prog="python -m valar.cli", description="ValarRP ohne Modell spielen.")
    parser.add_argument("--db", default="valar.db", help="Spielstand (SQLite-Datei), Standard: valar.db")
    args = parser.parse_args(argv)

    conn = db.connect(args.db)
    try:
        db.init_schema(conn)
        session = Session(conn, stdout)
        interactive = stdin.isatty()
        if interactive:
            session.say("ValarRP ohne Modell. 'hilfe' zeigt die Befehle.")
            session.show_look()
            while True:
                try:
                    line = input(session.prompt())
                except EOFError:
                    break
                if not session.handle(line):
                    break
        else:
            run(stdin, session, echo=True)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
