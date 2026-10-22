"""Kommandozeile ohne Modell (Schritt 1d, Entscheidung 65): deutsche Befehle, nur Standardbibliothek.

Werkzeug für Autor und Tests. Liest Zeile für Zeile von der Standardeingabe, interaktiv oder per Skript:

    python -m valar.cli --db spiel.db
    python -m valar.cli --db spiel.db < ablauf.txt

Gibt es in der Kampagne noch keine Figur, führt sie durch die Charaktererschaffung (Schritt 1i);
`--testfigur` legt stattdessen die Testfigur aus dem Inhalt an. Die Kommandozeile übersetzt nur Eingaben in
Aktionen und zeigt Ergebnisse; die Regeln bleiben im Kern.

Mit `--modell modelle.toml` spielt das Modell den Spielleiter (Schritt 2b): Freier Text geht an ihn, Befehle
beginnen mit `!`. Ohne `!` bleiben nur `schau`, `status`, `notizen`, `hilfe`, `ende` und `wurf ZAHL` Befehle.
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

from valar import apply, character, context, db, gm, llm, queries, rules
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
    RaiseAttribute,
    RecordFact,
    SubmitQuestItems,
    Sleep,
    SubmitRoll,
    Talk,
    TurnResult,
)
from valar.resolve import campaign_wiki
from valar.turns import submit_turn
from valar.wiki import Wiki, default_wiki, plain_text

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
  steigere ATTRIBUT [PUNKTE]    Attributspunkte aus dem Vorrat verteilen, z. B. steigere Stärke oder steigere GES 2
  knapp DING STUFE DAUER GRUND  erzählte Knappheit am Ort: STUFE knapp oder weg, DAUER tag, woche, monat;
                                z. B. knapp Blutwurz weg woche Die Kräuterfrau hat alles geerntet
  abgeben [AUFTRAG]             Auftrag abgeben; ohne Angabe der einzige aktive
  suche FRAGE                   im Wiki nachschlagen, wenige Abschnitte (kein Spielzug)
  kontext [EINGABE]             zeigen, was das Modell in diesem Zug bekäme (kein Spielzug)
  hilfe                         diese Übersicht
  ende                          beenden; der Spielstand bleibt in der Datei"""

MODEL_HELP = """Mit Spielleiter: Schreib frei, was deine Figur tut oder sagt. Das Modell wählt die Aktion, der Code
bestimmt die Folgen, das Modell erzählt. Proben würfelst du selbst: wurf ZAHL.
Ohne ! bleiben Befehle: schau, status, notizen, hilfe, ende, wurf ZAHL. Jeder andere Befehl mit !, z. B. !gehe Sturmhagen
(ohne Erzählung)."""

# Befehle, die auch mit Spielleiter ohne ! gelten: nur Abfragen und Beenden, damit freier Text frei bleibt.
MODEL_MODE_COMMANDS = {"schau", "umsehen", "status", "zustand", "notizen", "hilfe", "?", "ende", "beenden"}

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
    name: str  # "look", "status", "examine", "facts", "lookup" oder "context"
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


format_money = rules.format_money


def format_distance(meters: int) -> str:
    return f"{meters / 1000:.1f} km".replace(".", ",") if meters >= 1000 else f"{meters} m"


plain = plain_text


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
        case "suche":
            if not args:
                raise InputError("Wonach? Zum Beispiel: suche Was kostet eine Übernachtung")
            return Query("lookup", " ".join(args))
        case "kontext":
            return Query("context", " ".join(args))
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
        case "steigere":
            if not 1 <= len(args) <= 2 or (len(args) == 2 and not args[1].isdigit()):
                raise InputError("Steigern braucht ATTRIBUT und wahlweise PUNKTE, z. B. steigere Stärke 2")
            attribute = _find(args[0], rules.ATTRIBUTE_NAMES, "Attribut")
            return Turn(_build(RaiseAttribute, attribute=attribute, points=int(args[1]) if len(args) == 2 else 1))
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
        player_id: str,
        campaign_id: str = CAMPAIGN,
        content: Content = CONTENT,
        wiki: Wiki | None = None,
        new_request_id: Callable[[], str] = lambda: f"cli-{uuid.uuid4().hex}",
        game_master: gm.GameMaster | None = None,
    ) -> None:
        self.conn = conn
        self.game_master = game_master
        self.out = out
        self.campaign_id = campaign_id
        self.content = content
        self.wiki = wiki
        self.new_request_id = new_request_id
        self.player_id = player_id
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
        if self.game_master is not None:
            stripped = line.strip()
            words = stripped.split()
            roll = re.fullmatch(r"wurf\s+(\d{1,2})", stripped, flags=re.IGNORECASE)
            if stripped.startswith("!"):
                line = stripped[1:]
            elif roll and 1 <= int(roll.group(1)) <= 20:
                self.show_game_master(lambda request_id: self.game_master.roll(request_id, int(roll.group(1))))
                return True
            elif words and words[0].casefold() not in MODEL_MODE_COMMANDS and words[0].casefold() != "wurf":
                self.show_game_master(lambda request_id: self.game_master.play(request_id, stripped))
                return True
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
                if self.game_master is not None:
                    self.say(MODEL_HELP)
            case Quit():
                return False
            case Query("look"):
                self.show_look()
            case Query("examine", page_id):
                self.show_examine(page_id)
            case Query("facts"):
                self.show_facts()
            case Query("lookup", text):
                self.show_lookup(text)
            case Query("context", text):
                self.show_context(text)
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

    def show_game_master(self, ask: Callable[[str], gm.GmResult]) -> None:
        """Ein Zug mit Spielleiter: Erzählung, dann die harten Folgen, wie der Code sie bestimmt hat."""
        try:
            result = ask(self.new_request_id())
        except gm.InputTooLong as error:
            self.say(str(error))
            return
        except llm.ModelError as error:
            self.say(f"Spielleiter nicht erreichbar, nichts ist passiert: {error}")
            return
        if result.narration:
            self.say(result.narration)
            self.say()
        elif result.error is not None:
            self.say(f"(Erzählung fehlt: {result.error})")
        if result.turn is not None:
            self.show_result(result.turn)
        for note in result.notes:
            for effect in note.effects:
                if effect.type == "add_fact":
                    self.say(f"Notiert{' (Gerücht)' if effect.kind == 'rumor' else ''}: {effect.text}")
        if result.budget_warning:
            self.say("Warnung: Die Warnschwelle des Monatsbudgets ist erreicht.")
        self.revision = self.status().revision

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

    def show_lookup(self, text: str) -> None:
        hits = queries.lookup(self.conn, campaign_id=self.campaign_id, query=text, wiki=self.wiki)
        for hit in hits:
            self.say(f"{hit.rank}. {hit.title}" + (f" · {hit.heading}" if hit.heading else ""))
            self.say(f"   {hit.text}")
        if not hits:
            self.say("Nichts gefunden.")

    def show_context(self, text: str) -> None:
        built = context.build_context(
            self.conn,
            campaign_id=self.campaign_id,
            player_id=self.player_id,
            content=self.content,
            wiki=self.wiki,
            player_input=text,
        )
        self.say(built.render())
        dropped = f", weggelassen: {', '.join(k for k, _ in built.dropped)}" if built.dropped else ""
        self.say(f"({built.tokens} von {built.budget} Tokens Weltwissen{dropped})")

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
        if status.keepsake:
            self.say(f"Persönlicher Gegenstand: {status.keepsake}")
        if status.background:
            self.say(f"Hintergrund: {status.background}")
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
                case "adjust_attribute":
                    value = self.status().attributes[effect.attribute]
                    self.say(f"{rules.ATTRIBUTE_NAMES[effect.attribute]} steigt um {effect.points} auf {value}.")
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


# --- Charaktererschaffung (Schritt 1i) ---

Ask = Callable[[str], str | None]  # stellt eine Frage, gibt die Antwortzeile oder None am Ende der Eingabe


def parse_attributes(text: str) -> dict[str, int]:
    """„STR 12 GES 14“ oder „Stärke 12, Geschick 14“ → {"STR": 12, "GES": 14}. Falsche Eingabe: InputError."""
    pairs = re.findall(r"([^\W\d_]+)\s*[:=]?\s*(\d+)", text)
    rest = re.sub(r"([^\W\d_]+)\s*[:=]?\s*(\d+)|[\s,;]", "", text)
    if not pairs or rest:
        raise InputError("Attribute als Kürzel oder Name mit Zahl, z. B. STR 12 GES 14 WEI 12")
    values: dict[str, int] = {}
    for word, number in pairs:
        attribute = _find(word, rules.ATTRIBUTE_NAMES, "Attribut")
        if attribute in values:
            raise InputError(f"{rules.ATTRIBUTE_NAMES[attribute]} steht doppelt da.")
        values[attribute] = int(number)
    return values


def parse_skills(text: str) -> tuple[str, ...]:
    """Skills durch Komma getrennt, als Name oder ID. Auch gesperrte Begabungen (der Kern lehnt sie ab)."""
    names = {key: skill.name for key, skill in rules.SKILLS.items()} | rules.LOCKED_SKILLS
    return tuple(_find(word.strip(), names, "Skill") for word in text.split(",") if word.strip())


def create_character(
    conn: sqlite3.Connection,
    out: TextIO,
    ask: Ask,
    *,
    campaign_id: str = CAMPAIGN,
    content: Content = CONTENT,
    wiki: Wiki | None = None,
) -> str | None:
    """Führt durch die Erschaffung und legt die Figur an. Jede Antwort wird sofort geprüft; bei einem Fehler kommt
    die Frage noch einmal. Gibt die player_id zurück, oder None, wenn die Eingabe vorher endet."""

    def say(text: str = "") -> None:
        print(text, file=out)

    def answer(question: str, check: Callable[[str], object]) -> object:
        while True:
            line = ask(question)
            if line is None:
                return None
            try:
                return check(line.strip())
            except (InputError, character.CharacterError) as error:
                say(str(error))

    def name(line: str) -> str:
        line = character.check_text(line, character.MAX_NAME)
        player_id = character.check_name(line)
        taken = conn.execute(
            "SELECT 1 FROM player WHERE campaign_id = ? AND player_id = ?", (campaign_id, player_id)
        ).fetchone()
        if taken:
            raise character.CharacterError("player_exists", f"Eine Figur mit dem Namen {line!r} gibt es schon.")
        return line

    def attributes(line: str) -> dict[str, int]:
        chosen = parse_attributes(line)
        character.check_attributes(chosen)
        return chosen

    main: tuple[str, ...] = ()

    def main_skills(line: str) -> tuple[str, ...]:
        chosen = parse_skills(line)
        character.check_skill_choice(chosen, rules.MAIN_SKILLS, "Hauptskills")
        return chosen

    def side_skills(line: str) -> tuple[str, ...]:
        chosen = parse_skills(line)
        character.check_skills(main, chosen)
        return chosen

    def kit(line: str) -> str:
        names = {key: k.name for key, k in content.start_kits.items()}
        return character.check_kit(_find(line, names, "Ausrüstung"), content).kit_id

    def text(limit: int, required: bool) -> Callable[[str], str]:
        return lambda line: character.check_text(line, limit, required=required)

    say("Neue Figur erschaffen.")
    chosen_name = answer("Name: ", name)
    if chosen_name is None:
        return None
    say(
        f"Alle Attribute starten bei {rules.START_ATTRIBUTE}. Verteile genau {rules.START_ATTRIBUTE_POINTS} Punkte,"
        f" keines über {rules.MAX_START_ATTRIBUTE}. Nicht genannte bleiben auf {rules.START_ATTRIBUTE}."
    )
    say("  " + ", ".join(f"{a} {n}" for a, n in rules.ATTRIBUTE_NAMES.items()))
    chosen_attributes = answer("Attribute (z. B. STR 12 GES 14 KON 10 WEI 12 INT 10): ", attributes)
    if chosen_attributes is None:
        return None
    say(f"{rules.MAIN_SKILLS} Hauptskills auf {rules.MAIN_SKILL_VALUE}, dann {rules.SIDE_SKILLS} Nebenskills auf"
        f" {rules.SIDE_SKILL_VALUE}; mit Komma getrennt. Ziehen ist noch gesperrt.")
    say("  " + ", ".join(skill.name for skill in rules.SKILLS.values()))
    main = answer("Hauptskills: ", main_skills)
    if main is None:
        return None
    side = answer("Nebenskills: ", side_skills)
    if side is None:
        return None
    say("Ausrüstung zur Wahl:")
    pages = (wiki or default_wiki()).pages
    for k in content.start_kits.values():
        items = ", ".join(f"{q} {pages[i].title if i in pages else i}" for i, q in k.items.items())
        say(f"  {k.name}: {format_money(k.money_pennies)}" + (f", {items}" if items else ""))
    chosen_kit = answer("Ausrüstung: ", kit)
    if chosen_kit is None:
        return None
    keepsake = answer("Persönlicher Gegenstand ohne großen Wert: ", text(character.MAX_KEEPSAKE, True))
    if keepsake is None:
        return None
    background = answer("Hintergrund (Herkunft, Kontakte, Feinde; darf leer bleiben): ", text(character.MAX_BACKGROUND, False))
    if background is None:
        return None

    sheet = character.CharacterSheet(
        name=chosen_name,
        attributes=chosen_attributes,
        main_skills=main,
        side_skills=side,
        kit_id=chosen_kit,
        keepsake=keepsake,
        background=background,
    )
    try:
        player_id = apply.create_character(conn, campaign_id, sheet, content)
    except character.CharacterError as error:  # z. B. derselbe Name, gerade von jemand anderem angelegt
        say(f"Nicht angelegt: {error}")
        return None
    say(f"{sheet.name} ist erschaffen.")
    return player_id


def run(lines: Iterable[str], session: Session, *, echo: bool) -> None:
    """Liest Zeilen bis `ende` oder Dateiende. `echo` zeigt Eingabeaufforderung und Eingabe (für Skripte)."""
    for line in lines:
        if echo:
            session.say(session.prompt() + line.rstrip("\n"))
        if not session.handle(line):
            break


def main(
    argv: list[str] | None = None,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    transport: llm.Transport | None = None,  # Tests: Attrappe statt Netz
) -> int:
    parser = argparse.ArgumentParser(prog="python -m valar.cli", description="ValarRP spielen, ohne Modell oder mit Spielleiter (--modell).")
    parser.add_argument("--db", default="valar.db", help="Spielstand (SQLite-Datei), Standard: valar.db")
    parser.add_argument("--figur", help="mit dieser Figur spielen (Name oder ID), wenn es mehrere gibt")
    parser.add_argument("--neu", action="store_true", help="eine weitere Figur erschaffen")
    parser.add_argument("--testfigur", action="store_true", help="neue Kampagne mit der Testfigur, ohne Erschaffung")
    parser.add_argument("--modell", metavar="TOML", help="mit Spielleiter spielen; Einstellungen wie modelle.example.toml")
    args = parser.parse_args(argv)

    def say(text: str) -> None:
        print(text, file=stdout)

    conn = db.connect(args.db)
    try:
        db.init_schema(conn)
        interactive = stdin.isatty()
        lines = iter(stdin)

        def ask(question: str) -> str | None:
            if interactive:
                try:
                    return input(question)
                except EOFError:
                    return None
            line = next(lines, None)
            if line is not None:
                say(question + line.rstrip("\n"))
            return line

        if not conn.execute("SELECT 1 FROM campaign WHERE campaign_id = ?", (CAMPAIGN,)).fetchone():
            if args.testfigur:
                apply.create_campaign_from_content(conn, CAMPAIGN, CONTENT)
                say("Neue Kampagne mit der Testfigur angelegt.")
            else:
                apply.create_campaign(conn, CAMPAIGN)
                say("Neue Kampagne angelegt.")
        players = {
            row["player_id"]: row["name"]
            for row in conn.execute("SELECT player_id, name FROM player WHERE campaign_id = ? ORDER BY player_id", (CAMPAIGN,))
        }
        if args.figur:
            try:
                player_id = _find(args.figur, players, "Figur")
            except InputError as error:
                say(f"{error} Es gibt: {', '.join(players.values()) or 'noch keine'}.")
                return 2
        elif args.neu or not players:
            player_id = create_character(conn, stdout, ask)
            if player_id is None:
                say("Erschaffung abgebrochen.")
                return 1
        elif len(players) == 1:
            (player_id,) = players
        else:
            say(f"Es gibt mehrere Figuren: {', '.join(players.values())}. Wähle eine mit --figur NAME.")
            return 2

        game_master = None
        if args.modell:
            try:
                settings = llm.load_settings(args.modell)
            except (OSError, ValueError) as error:  # fehlende Datei, falsches TOML oder Feld
                say(f"Einstellungen nicht lesbar: {args.modell} ({type(error).__name__})")
                return 2
            client = llm.ModelClient(settings, llm.Ledger(conn), transport=transport)
            game_master = gm.GameMaster(conn, client, campaign_id=CAMPAIGN, player_id=player_id)
        session = Session(conn, stdout, player_id=player_id, game_master=game_master)
        if interactive:
            session.say("ValarRP mit Spielleiter. 'hilfe' zeigt die Befehle." if game_master else
                        "ValarRP ohne Modell. 'hilfe' zeigt die Befehle.")
            session.show_look()
            while True:
                try:
                    line = input(session.prompt())
                except EOFError:
                    break
                if not session.handle(line):
                    break
        else:
            run(lines, session, echo=True)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
