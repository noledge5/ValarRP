"""Spielleiter-Schleife (Schritt 2b, Entscheidungen 34, 35, 141, 142 und 158 bis 166).

Der Spieler schreibt frei. Aufruf A: Das Modell wählt eine Aktion aus den erlaubten, mit festen Zielen. Der Code
löst sie über `submit_turn` auf. Aufruf B: Das Modell erzählt das Ergebnis des Codes und darf bis zu zwei neue
Personen und zwei Notizen mitgeben, die als eigene Spielzüge `introduce_npc` und `record_fact` eingereicht werden.
„Nur erzählen“ braucht nur Aufruf A. Eine neue Figur bekommt einen Einstieg (Entscheidung 218).

Gedächtnis: der Kontextbauer (Zustand und Weltwissen), die letzten Wechsel wörtlich, ältere als Chronik, deren
Zeilen der Code aus dem Spielzug schreibt. Jeder Wechsel steht in `exchange`. Dieselbe request_id ruft das
Modell nicht noch einmal auf; fehlt nur die Erzählung, holt sie Aufruf B nach. Das Modell setzt nie harte Werte.
"""

import random
import sqlite3
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, create_model

from valar import apply, context, queries, rules
from valar.content import CONTENT, Content
from valar.llm import ModelClient, ModelError
from valar.models import (
    ACTIONS,
    FUND,
    Action,
    AttemptAction,
    Contest,
    CreatePage,
    Examine,
    Gather,
    Gain,
    GiveMoney,
    IntroduceNpc,
    MarkScarcity,
    NotePlace,
    RememberNpc,
    MeetNpc,
    Move,
    RaiseAttribute,
    RecordFact,
    Sleep,
    SubmitQuestItems,
    SubmitRoll,
    Talk,
    TurnResult,
)
from valar.resolve import campaign_wiki, pages_here
from valar.turns import RequestIdConflict, stored_action, submit_turn
from valar.wiki import Wiki, default_wiki, slug

# Zahlen fürs Gedächtnis (Entscheidung 158).
PROMPT_PATH = Path(__file__).with_name("spielleiter.md")
PROMPT_BUDGET = 800  # Tokens für den Rahmen-Prompt
MAX_INPUT_CHARS = 500
NARRATION_CHARS = 2000
MAX_NOTES = 2
MAX_NPCS = 2  # neue Personen je Erzählung (Entscheidung 217)
FOLLOW_UPS = ("b", "c")  # weitere Schritte eines Zugs nach `/a` (Entscheidung 225)
MAX_BONDS = 2  # Gedächtnis und Haltung je Erzählung (Entscheidung 197)
MAX_IDEAS = 3  # Vorschläge als Knöpfe je Erzählung (Entscheidung 201)
SUCCESS = {"success", "critical_success"}
HISTORY_EXCHANGES = 6  # letzte Wechsel wörtlich
HISTORY_BUDGET = 1500
CHRONICLE_BUDGET = 300

_STRICT = ConfigDict(extra="forbid", frozen=True)


class InputTooLong(ValueError):
    """Die Eingabe ist leer oder zu lang. Kein Aufruf, kein Spielzug."""


# --- Was das Modell zurückgibt ---


class Note(BaseModel):
    """Eine Notiz der Erzählung; wird zum Spielzug `record_fact` (Entscheidung 160)."""

    model_config = _STRICT

    kind: Literal["fact", "rumor"]
    text: str = Field(min_length=1, max_length=300)
    subject: str | None = Field(default=None, min_length=1, max_length=80)
    source_npc_id: str | None = Field(default=None, min_length=1)


class NewNpc(BaseModel):
    """Eine Person, die in der Erzählung neu auftritt; wird zum Spielzug `introduce_npc` (Entscheidung 217)."""

    model_config = _STRICT

    template_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(min_length=1, max_length=300)
    wants: str = Field(min_length=1, max_length=200)  # Entscheidung 197: beim ersten Treffen, danach fest
    fears: str = Field(min_length=1, max_length=200)


class Bond(BaseModel):
    """Was zwischen der Figur und einem NPC hier geschah, und wohin sich seine Haltung bewegt (Entscheidung 197).
    Ob „besser“ zählt, entscheidet der Code: nur nach einem Erfolg oder einem Geschenk an ihn."""

    model_config = _STRICT

    npc_id: str = Field(min_length=1)
    memory: str = Field(min_length=1, max_length=200)
    attitude: Literal["besser", "schlechter", "gleich"] = "gleich"


class Idea(BaseModel):
    """Ein Vorschlag für den nächsten Zug, nur als Knopf über dem Eingabefeld (Entscheidung 201)."""

    model_config = _STRICT

    kind: Literal["handlung", "rede"]
    text: str = Field(min_length=1, max_length=120)


class Narration(BaseModel):
    """Antwort auf Aufruf B: nur Text, Notizen und neue Personen, keine harten Werte."""

    model_config = _STRICT

    text: str = Field(min_length=1, max_length=NARRATION_CHARS)
    notes: tuple[Note, ...] = Field(default=(), max_length=MAX_NOTES)
    npcs: tuple[NewNpc, ...] = Field(default=(), max_length=MAX_NPCS)
    bonds: tuple[Bond, ...] = Field(default=(), max_length=MAX_BONDS)
    place: str | None = Field(default=None, max_length=200)  # Orts-Gedächtnis (Entscheidung 198)
    ideas: tuple[Idea, ...] = Field(default=(), max_length=MAX_IDEAS)


class Narrate(BaseModel):
    """„Nur erzählen“ in Aufruf A: ohne Regelwirkung, darum gleich mit Text (Entscheidung 159)."""

    model_config = _STRICT

    type: Literal["narrate"] = "narrate"
    text: str = Field(min_length=1, max_length=NARRATION_CHARS)
    notes: tuple[Note, ...] = Field(default=(), max_length=MAX_NOTES)
    npcs: tuple[NewNpc, ...] = Field(default=(), max_length=MAX_NPCS)
    bonds: tuple[Bond, ...] = Field(default=(), max_length=MAX_BONDS)
    place: str | None = Field(default=None, max_length=200)
    ideas: tuple[Idea, ...] = Field(default=(), max_length=MAX_IDEAS)


NARRATION = TypeAdapter(Narration)

# Aufruf A: Beschreibung und Form je Aktion. `record_fact` fehlt, Notizen kommen mit der Erzählung.
ACTION_HELP: dict[str, tuple[str, str]] = {
    "narrate": (
        "nur erzählen, ohne Regelwirkung (Gespräch, Umsehen, Stimmung)",
        '{"type": "narrate", "text": "…", "notes": [], "npcs": [], "bonds": [], "place": null, "ideas": []}',
    ),
    "move": ("zu einem verbundenen Ort gehen", '{"type": "move", "destination_id": "…"}'),
    "examine": ("etwas hier genauer untersuchen, mit Probe", '{"type": "examine", "page_id": "…"}'),
    "gather": ("etwas sammeln, das hier vorkommt, mit Probe", '{"type": "gather", "page_id": "…"}'),
    "meet_npc": (
        "einen neuen NPC ansprechen; seine Werte würfelt der Code",
        '{"type": "meet_npc", "template_id": "…", "name": "Vorname Nachname", "description": "kurz",'
        ' "wants": "ein Satz", "fears": "ein Satz"}',
    ),
    "talk": ("mit einem NPC hier reden", '{"type": "talk", "npc_id": "…"}'),
    "contest": (
        "Vergleichsprobe gegen einen NPC hier",
        '{"type": "contest", "skill_id": "…", "npc_id": "…", "duration": "…", "reason": "kurz", "gain": {…}, "loss": false}',
    ),
    "attempt": (
        "freier Versuch mit Probe",
        '{"type": "attempt", "skill_id": "…", "difficulty": "…", "duration": "…", "reason": "kurz", "gain": {…},'
        ' "loss": false}',
    ),
    "submit_quest_items": ("einen Auftrag abgeben", '{"type": "submit_quest_items", "quest_id": "…"}'),
    "give_money": (
        "Geld geben oder bezahlen, Betrag in Pfennig (1 Groschen = 10, 1 Gulden = 100); npc_id nur für einen NPC hier",
        '{"type": "give_money", "pennies": 10, "npc_id": "…" oder null, "reason": "kurz"}',
    ),
    "mark_scarcity": (
        "etwas hier ist knapp oder weg (nur erschweren)",
        '{"type": "mark_scarcity", "page_id": "…", "level": "scarce", "duration": "week", "reason": "kurz"}',
    ),
    "create_page": (
        "neues Weltwissen als Seite anlegen",
        '{"type": "create_page", "template": "…", "title": "…", "text": "…"}',
    ),
    "sleep": ("schlafen bis zum Morgen", '{"type": "sleep"}'),
    "raise_attribute": ("Attributspunkte verteilen", '{"type": "raise_attribute", "attribute": "STR", "points": 1}'),
}
NARRATION_FORMAT = (
    '{"text": "…", "notes": [{"kind": "fact" oder "rumor", "text": "ein Satz", "subject": "Thema" oder null,'
    ' "source_npc_id": "NPC-ID oder Name einer neuen Person" oder null}],'
    ' "npcs": [{"template_id": "…", "name": "Vorname Nachname", "description": "ein Satz", "wants": "ein Satz",'
    ' "fears": "ein Satz"}], "bonds": [{"npc_id": "NPC-ID oder Name", "memory": "ein Satz",'
    ' "attitude": "besser" oder "schlechter" oder "gleich"}], "place": "ein Satz" oder null,'
    ' "ideas": [{"kind": "handlung" oder "rede", "text": "kurz"}]}'
)
FAILED = {"near_miss", "failure", "fumble"}
OPENING_INPUT = "(Einstieg)"  # steht als Eingabe im Wechsel; die Seite zeigt ihn nicht als Spielerzeile
OPENING_SEEDS = ("Ereignis", "NPC-Typ", "Brauch")  # woraus ein Einstieg entsteht (Entscheidung 218)
DIFFICULTY_WORDS = ("sehr leicht", "leicht", "durchschnitt", "schwer", "sehr schwer", "extrem", "legendär")
OUTCOME_TEXT = {
    "critical_success": "kritischer Erfolg",
    "success": "Erfolg",
    "near_miss": "knapp daneben (zählt als Fehlschlag)",
    "failure": "Fehlschlag",
    "fumble": "Patzer",
}


@dataclass(frozen=True)
class GmResult:
    request_id: str
    narration: str | None  # None: Erzählung gescheitert, `error` sagt warum; dieselbe request_id holt sie nach
    turn: TurnResult | None  # der Spielzug der Aktion; None bei „nur erzählen“
    notes: tuple[TurnResult, ...] = ()
    error: ModelError | None = None
    npcs: tuple[TurnResult, ...] = ()
    more: tuple[TurnResult, ...] = ()  # weitere Schritte desselben Zugs, in ihrer Reihenfolge
    bonds: tuple[TurnResult, ...] = ()  # Gedächtnis und Haltung der NPCs (Entscheidung 197)
    place: tuple[TurnResult, ...] = ()  # Orts-Gedächtnis (Entscheidung 198)
    ideas: tuple[Idea, ...] = ()  # nur Anzeige, nicht gespeichert (Entscheidung 201)
    replayed: bool = False
    budget_warning: bool = False


@dataclass(frozen=True)
class _Choices:
    """Was Aufruf A wählen darf: Modelle mit festen Zielen und die Zeilen, die sie erklären."""

    adapter: TypeAdapter
    lines: tuple[str, ...]
    follow_ups: bool = False  # ob weitere Schritte in `then` möglich sind


@dataclass(frozen=True)
class _Exchange:
    player_id: str
    player_input: str
    turn_request_id: str | None
    narration: str | None
    chronicle: str | None


@dataclass
class _Memory:
    history: list[str] = field(default_factory=list)  # Wechsel wörtlich, älteste zuerst
    chronicle: list[str] = field(default_factory=list)
    last_narration: str = ""


class GameMaster:
    def __init__(
        self,
        conn: sqlite3.Connection,
        client: ModelClient,
        *,
        campaign_id: str,
        player_id: str,
        content: Content = CONTENT,
        wiki: Wiki | None = None,
        prompt: str | None = None,
    ) -> None:
        self.conn = conn
        self.client = client
        self.campaign_id = campaign_id
        self.player_id = player_id
        self.content = content
        self.wiki = wiki
        self.prompt = prompt if prompt is not None else load_prompt()

    # --- Einstieg ---

    def play(self, request_id: str, player_input: str) -> GmResult:
        """Ein freier Zug. Fehler in Aufruf A (ModelError) ändern nichts; scheitert B, steht die Aktion."""
        text = _check_input(player_input)
        stored = self._stored(request_id, text)
        if stored is not None and stored.narration is not None:
            return self._replay(request_id, stored)
        if stored is not None:
            turn = self._replay_turn(stored.turn_request_id)
            return self._narrate(request_id, text, turn, recorded=True, more=self._follow_ups(request_id))

        action, more, skipped = self._earlier_action(request_id), [], []
        if action is None:
            memory = self._memory()
            built = self._context(text, memory.last_narration)
            revision = self._status().revision
            choices = self._choices(built)
            reply = self.client.request(self._messages_a(built, memory, choices, text), choices.adapter, schema_name="aktion")
            choice, then = reply.value.action, list(getattr(reply.value, "then", ()))
            if isinstance(choice, Narrate) and then:
                choice, then = then[0], then[1:]  # erzählt wird ohnehin in Aufruf B, die Schritte zählen
            if isinstance(choice, Narrate):
                narration = _clean(choice.text)
                self._record(request_id, text, None, narration, None)
                npcs, names = self._submit_npcs(request_id, choice.npcs)
                notes = self._submit_notes(request_id, choice.notes, names)
                bonds = self._submit_bonds(request_id, choice.bonds, [], names)
                place = self._submit_place(request_id, choice.place)
                return GmResult(
                    request_id, narration, None, notes, budget_warning=reply.budget_warning, npcs=npcs, bonds=bonds,
                    place=place, ideas=_ideas(choice.ideas),
                )
            action = ACTIONS.validate_python(choice.model_dump())
            manifest = built.manifest() | {"history": len(memory.history), "chronicle": len(memory.chronicle)}
            turn = self._submit_step(request_id, "a", action, revision, manifest)
            more, skipped = self._submit_more(request_id, turn, [ACTIONS.validate_python(t.model_dump()) for t in then])
        else:
            # Wiederholung nach Absturz: der erste Schritt steht; weitere, die schon standen, gelten, der Rest entfällt
            turn = self._submit_step(request_id, "a", action, self._status().revision, None)
            more = self._follow_ups(request_id)
        return self._narrate(request_id, text, turn, recorded=False, action=action, more=more, skipped=skipped)

    def roll(self, request_id: str, value: int) -> GmResult:
        """Der Spieler trägt seinen W20 ein; ohne Aufruf A. Ein abgelehnter Wurf wird nicht erzählt."""
        text = f"wurf {value}"
        stored = self._stored(request_id, text)
        if stored is not None and stored.narration is not None:
            return self._replay(request_id, stored)
        if stored is not None:
            return self._narrate(request_id, text, self._replay_turn(stored.turn_request_id), recorded=True)
        action = SubmitRoll(value=value)
        turn = self._submit_step(request_id, "a", action, self._status().revision, None)
        if turn.status == "rejected":
            return GmResult(request_id, None, turn)
        return self._narrate(request_id, text, turn, recorded=False, action=action)

    # --- Aufruf B ---

    def _narrate(
        self,
        request_id: str,
        text: str,
        turn: TurnResult,
        *,
        recorded: bool,
        action: Action | None = None,
        more: list[tuple[Action, TurnResult]] = (),
        skipped: list[Action] = (),
    ) -> GmResult:
        action = action or stored_action(self.conn, self.campaign_id, turn.request_id)
        steps = [(action, turn), *more]
        if not recorded:
            self._record(request_id, text, turn.request_id, None, self._chronicle_line(steps))
        memory = self._memory(exclude=request_id)
        built = self._context(text, memory.last_narration)
        messages = self._messages_b(built, memory, text, self.describe(action, turn, more=more, skipped=skipped))
        later = tuple(result for _, result in more)
        try:
            reply = self.client.request(messages, NARRATION, schema_name="erzaehlung")
        except ModelError as error:
            return GmResult(request_id, None, turn, error=error, more=later)
        narration = _clean(reply.value.text)
        self._write(lambda: apply.set_narration(
            self.conn, campaign_id=self.campaign_id, request_id=request_id, narration=narration
        ))
        npcs, names = self._submit_npcs(request_id, reply.value.npcs)
        # Nach einem Fehlschlag erfährt die Figur nichts dazu (Regel 9, Entscheidung 219): keine Notizen.
        failed = any(result.check is not None and result.check.outcome in FAILED for _, result in steps)
        notes = () if failed else self._submit_notes(request_id, reply.value.notes, names)
        bonds = self._submit_bonds(request_id, reply.value.bonds, steps, names)
        place = self._submit_place(request_id, reply.value.place)
        return GmResult(
            request_id, narration, turn, notes, budget_warning=reply.budget_warning, npcs=npcs, more=later, bonds=bonds,
            place=place, ideas=_ideas(reply.value.ideas),
        )

    # --- Einstieg ---

    def opening(self, request_id: str, rng: random.Random | None = None) -> GmResult:
        """Einstieg für eine neue Figur (Entscheidung 218): eine Szene aus dem, was hier vorkommt, die gleich eine
        Entscheidung verlangt. Nur Erzählung, Notizen und neue Personen; keine Aktion, keine Zeit."""
        stored = self._stored(request_id, OPENING_INPUT)
        if stored is not None and stored.narration is not None:
            return self._replay(request_id, stored)
        seen = queries.look(self.conn, campaign_id=self.campaign_id, player_id=self.player_id, content=self.content, wiki=self.wiki)
        rng = rng or random.Random()
        seeds = [t.title for t in seen.things if t.type in OPENING_SEEDS]
        seed = ", dazu ".join(rng.sample(seeds, min(2, len(seeds)))) or seen.name
        built = self._context(OPENING_INPUT, "")
        task = [
            "# Aufgabe Einstieg",
            f"Die Figur ist gerade angekommen; das Spiel beginnt. Die Szene spielt genau hier: {seen.name}"
            f"{' (drinnen)' if self.content.locations[seen.location_id].indoors else ''}, nicht davor und nicht"
            f" anderswo. Eröffne rund um: {seed}.",
            "Bring die Figur sofort in eine Lage, die jetzt eine Entscheidung verlangt: jemand bittet, droht, bietet an"
            " oder etwas geschieht vor ihren Augen. Nenne keine Möglichkeiten; ende mit der offenen Lage.",
            "Antworte mit " + NARRATION_FORMAT,
            *self._npc_lines(built),
        ]
        reply = self.client.request(self._messages(built, _Memory(), OPENING_INPUT, task), NARRATION, schema_name="einstieg")
        narration = _clean(reply.value.text)
        self._record(request_id, OPENING_INPUT, None, narration, None)
        npcs, names = self._submit_npcs(request_id, reply.value.npcs)
        notes = self._submit_notes(request_id, reply.value.notes, names)
        return GmResult(
            request_id, narration, None, notes, budget_warning=reply.budget_warning, npcs=npcs, ideas=_ideas(reply.value.ideas)
        )

    # --- Spielzüge ---

    def _submit_step(self, request_id: str, letter: str, action: Action, revision: int, manifest: dict | None) -> TurnResult:
        """Ein Schritt als Spielzug `<id>/a` (`/b`, `/c`). Veraltete Revision: einmal neu als `<id>/a2`, ohne Modell."""
        result = self._submit(f"{request_id}/{letter}", action, revision, manifest)
        if result.reject_code == "stale_revision":
            result = self._submit(f"{request_id}/{letter}2", action, self._status().revision, manifest)
        return result

    def _submit_more(
        self, request_id: str, previous: TurnResult, actions: list[Action]
    ) -> tuple[list[tuple[Action, TurnResult]], list[Action]]:
        """Weitere Schritte nacheinander (Entscheidung 225). Nach einer Ablehnung oder offenen Probe entfällt der Rest;
        gibt die ausgeführten Schritte und die entfallenen Aktionen zurück."""
        steps = []
        for letter, action in zip(FOLLOW_UPS, actions):
            if previous.status != "accepted":
                break
            previous = self._submit_step(request_id, letter, action, self._status().revision, None)
            steps.append((action, previous))
        return steps, actions[len(steps):]

    def _follow_ups(self, request_id: str) -> list[tuple[Action, TurnResult]]:
        """Die weiteren Schritte, die zu diesem Zug schon gespeichert sind, als Wiederholung ohne neue Wirkung."""
        steps = []
        for turn_id in follow_up_ids(self.conn, self.campaign_id, request_id):
            action = stored_action(self.conn, self.campaign_id, turn_id)
            steps.append((action, self._submit(turn_id, action, self._status().revision, None)))
        return steps

    def _submit_npcs(self, request_id: str, npcs: tuple[NewNpc, ...]) -> tuple[tuple[TurnResult, ...], dict[str, str]]:
        """Je neue Person ein Spielzug `introduce_npc` vor den Notizen; eine abgelehnte fällt allein weg.
        Gibt auch zurück, welcher Name zu welcher NPC-ID wurde, damit Notizen sie als Quelle nennen können."""
        results, names = [], {}
        for number, npc in enumerate(npcs, start=1):
            action = IntroduceNpc(
                template_id=npc.template_id, name=_clean(npc.name), description=_clean(npc.description),
                wants=_clean(npc.wants), fears=_clean(npc.fears),
            )
            try:
                result = self._submit(f"{request_id}/p{number}", action, self._status().revision, None)
            except RequestIdConflict:
                continue  # nach einem Absturz kam eine andere Person unter dieser ID; die gespeicherte gilt
            if result.status == "rejected":
                continue
            results.append(result)
            for effect in result.effects:
                if effect.type == "create_npc":
                    names |= {npc.name: effect.npc_id, slug(npc.name): effect.npc_id}
        return tuple(results), names

    def _submit_bonds(
        self, request_id: str, bonds: tuple[Bond, ...], steps: list[tuple[Action, TurnResult]], names: dict[str, str]
    ) -> tuple[TurnResult, ...]:
        """Gedächtnis und Haltung je NPC als Spielzug `<id>/h1` (Entscheidung 197). Besser nur nach einem gelungenen
        Wurf in diesem Zug oder einem Geschenk an genau diesen NPC; schlechter geht immer; höchstens eine Stufe."""
        accepted = [result for _, result in steps if result.status == "accepted"]
        succeeded = any(result.check is not None and result.check.outcome in SUCCESS for result in accepted)
        results = []
        for number, bond in enumerate(bonds, start=1):
            npc_id = self._npc_id(bond.npc_id, names)
            gift = any(e.type == "pay_money" and e.npc_id == npc_id for result in accepted for e in result.effects)
            step = {"besser": 1 if succeeded or gift else 0, "schlechter": -1}.get(bond.attitude, 0)
            action = RememberNpc(npc_id=npc_id, text=_clean(bond.memory)[:200], step=step)
            try:
                result = self._submit(f"{request_id}/h{number}", action, self._status().revision, None)
            except RequestIdConflict:
                continue
            if result.status != "rejected":
                results.append(result)
        return tuple(results)

    def _submit_place(self, request_id: str, text: str | None) -> tuple[TurnResult, ...]:
        """Ein Satz ins Orts-Gedächtnis als `<id>/o1` (Entscheidung 198). Auch nach „nur erzählen“: ein umgetretener
        Tisch braucht keinen Wurf. Bei offener Probe lehnt der Code ab, das Ergebnis steht noch aus."""
        if not text or not text.strip():
            return ()
        try:
            result = self._submit(f"{request_id}/o1", NotePlace(text=_clean(text)[:200]), self._status().revision, None)
        except RequestIdConflict:
            return ()
        return () if result.status == "rejected" else (result,)

    def _submit_notes(
        self, request_id: str, notes: tuple[Note, ...], names: dict[str, str] | None = None
    ) -> tuple[TurnResult, ...]:
        """Je Notiz ein eigener Spielzug nach dem Hauptzug; eine abgelehnte fällt allein weg."""
        results = []
        for number, note in enumerate(notes, start=1):
            if self._status().open_roll:
                break  # bei offener Probe würfelt der Spieler, Notizen warten nicht
            subject = _clean(note.subject) if note.subject else None
            source = self._npc_id(note.source_npc_id, names or {}) if note.source_npc_id else None
            action = RecordFact(kind=note.kind, text=_clean(note.text), subject=subject or None, source_npc_id=source)
            try:
                results.append(self._submit(f"{request_id}/n{number}", action, self._status().revision, None))
            except RequestIdConflict:
                continue  # nach einem Absturz kam eine andere Notiz unter dieser ID; die gespeicherte gilt
        return tuple(r for r in results if r.status != "rejected")

    def _submit(self, request_id: str, action: Action, revision: int, manifest: dict | None) -> TurnResult:
        return submit_turn(
            self.conn,
            campaign_id=self.campaign_id,
            player_id=self.player_id,
            request_id=request_id,
            expected_revision=revision,
            action=action,
            content=self.content,
            wiki=self.wiki,
            context=manifest,
        )

    def _earlier_action(self, request_id: str) -> Action | None:
        """Steht der Spielzug zu dieser request_id schon (Absturz vor dem Wechsel), gilt seine Aktion, ohne Modell."""
        for suffix in ("/a2", "/a"):
            action = stored_action(self.conn, self.campaign_id, request_id + suffix)
            if action is not None:
                return action
        return None

    def _replay_turn(self, turn_request_id: str | None) -> TurnResult:
        action = stored_action(self.conn, self.campaign_id, turn_request_id or "")
        if action is None:
            raise RuntimeError(f"Spielzug {turn_request_id!r} fehlt")
        return self._submit(turn_request_id, action, self._status().revision, None)

    def _replay(self, request_id: str, stored: _Exchange) -> GmResult:
        turn = self._replay_turn(stored.turn_request_id) if stored.turn_request_id else None
        notes = []
        for number in range(1, MAX_NOTES + 1):
            note = stored_action(self.conn, self.campaign_id, f"{request_id}/n{number}")
            if note is not None:
                notes.append(self._submit(f"{request_id}/n{number}", note, 0, None))
        accepted = tuple(n for n in notes if n.status != "rejected")
        npcs = []
        for number in range(1, MAX_NPCS + 1):
            npc = stored_action(self.conn, self.campaign_id, f"{request_id}/p{number}")
            if npc is not None:
                npcs.append(self._submit(f"{request_id}/p{number}", npc, 0, None))
        known = tuple(n for n in npcs if n.status != "rejected")
        later = tuple(result for _, result in self._follow_ups(request_id))
        extra = {}
        for field_name, ids in (("bonds", [f"h{n}" for n in range(1, MAX_BONDS + 1)]), ("place", ["o1"])):
            found = []
            for suffix in ids:
                stored_step = stored_action(self.conn, self.campaign_id, f"{request_id}/{suffix}")
                if stored_step is not None:
                    found.append(self._submit(f"{request_id}/{suffix}", stored_step, 0, None))
            extra[field_name] = tuple(r for r in found if r.status != "rejected")
        return GmResult(request_id, stored.narration, turn, accepted, replayed=True, npcs=known, more=later, **extra)

    # --- Gedächtnis ---

    def _stored(self, request_id: str, text: str) -> _Exchange | None:
        row = self.conn.execute(
            "SELECT player_id, player_input, turn_request_id, narration, chronicle FROM exchange"
            " WHERE campaign_id = ? AND request_id = ?",
            (self.campaign_id, request_id),
        ).fetchone()
        if row is None:
            return None
        stored = _Exchange(*row)
        if stored.player_id != self.player_id or stored.player_input != text:
            raise RequestIdConflict(request_id)
        return stored

    def _record(self, request_id: str, text: str, turn_request_id: str | None, narration: str | None, chronicle: str | None) -> None:
        self._write(lambda: apply.record_exchange(
            self.conn,
            campaign_id=self.campaign_id,
            request_id=request_id,
            player_id=self.player_id,
            player_input=text,
            turn_request_id=turn_request_id,
            narration=narration,
            chronicle=chronicle,
        ))

    def _write(self, write: Callable[[], None]) -> None:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            write()
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    def _memory(self, exclude: str | None = None) -> _Memory:
        """Die letzten Wechsel wörtlich (höchstens HISTORY_EXCHANGES und HISTORY_BUDGET), davor die Chronik."""
        rows = self.conn.execute(
            "SELECT request_id, player_input, narration, chronicle FROM exchange"
            " WHERE campaign_id = ? AND player_id = ? ORDER BY exchange_id DESC",
            (self.campaign_id, self.player_id),
        ).fetchall()
        rows = [row for row in rows if row["request_id"] != exclude]
        memory = _Memory()
        remaining, verbatim = HISTORY_BUDGET, 0
        older = []
        for index, row in enumerate(rows):
            if row["narration"] is None:
                older.append(row)
                continue
            entry = f"Spieler: {row['player_input']}\nSpielleiter: {row['narration']}"
            if verbatim < HISTORY_EXCHANGES and context.estimate_tokens(entry) <= remaining:
                memory.history.insert(0, entry)
                remaining -= context.estimate_tokens(entry)
                verbatim += 1
                if not memory.last_narration:
                    memory.last_narration = row["narration"]
            else:
                older += rows[index:]
                break
        remaining = CHRONICLE_BUDGET
        for row in older:
            line = row["chronicle"]
            if not line:
                continue
            if context.estimate_tokens(line) > remaining:
                break
            memory.chronicle.insert(0, line)
            remaining -= context.estimate_tokens(line)
        return memory

    # --- Kontext und Nachrichten ---

    def _status(self) -> queries.StatusResult:
        return queries.status(self.conn, campaign_id=self.campaign_id, player_id=self.player_id, content=self.content)

    def _context(self, text: str, narration: str) -> context.TurnContext:
        return context.build_context(
            self.conn,
            campaign_id=self.campaign_id,
            player_id=self.player_id,
            content=self.content,
            wiki=self.wiki,
            player_input=text,
            narration=narration,
        )

    def _messages_a(self, built: context.TurnContext, memory: _Memory, choices: _Choices, text: str) -> list[dict[str, str]]:
        # Spieltest: Erzählte der Verlauf die Figur schon draußen, wurde „gehen“ nur erzählt (Entscheidung 222).
        here = self._status().location_name
        task = [
            "# Aufgabe A",
            f"Die Figur ist laut Code jetzt hier: {here}. Was die Erzählung bisher sagt, gilt nur, wenn es zum Code"
            " passt. Will sie an einen anderen Ort, wähle move.",
            "Wähle genau eine Aktion. Antworte mit {\"action\": …}, das Innere in der Form aus „Aktionen“.",
        ]
        if choices.follow_ups:
            task.append(
                f"Hat die Eingabe mehrere Schritte (erst gehen, dann reden), stehen die weiteren in \"then\", höchstens"
                f" {len(FOLLOW_UPS)}, in ihrer Reihenfolge und in derselben Form; sonst \"then\": []."
            )
        return self._messages(built, memory, text, ["# Aktionen", *choices.lines, "", *task])

    def _messages_b(self, built: context.TurnContext, memory: _Memory, text: str, result: list[str]) -> list[dict[str, str]]:
        task = [
            "# Aufgabe B",
            "Erzähle das Ergebnis. Antworte mit " + NARRATION_FORMAT,
            *self._npc_lines(built),
        ]
        return self._messages(built, memory, text, ["# Ergebnis des Codes", *result, "", *task])

    def _npc_lines(self, built: context.TurnContext) -> list[str]:
        """Bekannte NPCs hier und die Vorlagen für neue Personen (Entscheidung 217)."""
        npcs = [item.key.removeprefix("npc:") for item in built.items if item.section == "leute"]
        world = campaign_wiki(self.conn, self.campaign_id, self.wiki or default_wiki())
        templates = [
            f"{p.page_id} = {p.title}" for p in world.pages.values() if p.game.template is not None and p.status != "deprecated"
        ]
        lines = ["NPC-IDs hier: " + ", ".join(npcs)] if npcs else []
        return lines + ["Vorlagen für npcs: " + ", ".join(templates)]

    def _messages(self, built: context.TurnContext, memory: _Memory, text: str, tail: list[str]) -> list[dict[str, str]]:
        parts = []
        if memory.chronicle:
            parts += ["# Chronik", *(f"- {line}" for line in memory.chronicle), ""]
        if memory.history:
            parts += ["# Bisher", "\n\n".join(memory.history), ""]
        parts += [built.render(actions=False), "", "# Eingabe des Spielers", text, "", *tail]
        return [{"role": "system", "content": self.prompt}, {"role": "user", "content": "\n".join(parts)}]

    def _choices(self, built: context.TurnContext) -> _Choices:
        """Die erlaubten Aktionen mit festen Zielen (Entscheidung 142): ein falsches Ziel ist eine ungültige Antwort."""
        status = self._status()
        seen = queries.look(self.conn, campaign_id=self.campaign_id, player_id=self.player_id, content=self.content, wiki=self.wiki)
        world = campaign_wiki(self.conn, self.campaign_id, self.wiki or default_wiki())
        exits = {e.location_id: e.name for e in seen.exits}
        npcs = {n.npc_id: n.name for n in seen.npcs}
        here = {page_id: page.title for page_id, page in pages_here(world, self.content, seen.location_id).items()}
        gatherable = {t.page_id: t.title for t in seen.things if t.gatherable}
        templates = {p.page_id: p.title for p in world.pages.values() if p.game.template is not None and p.status != "deprecated"}
        skills = {key: skill.name for key, skill in rules.SKILLS.items()}
        countered = {key: skills[key] for key in rules.CONTEST_PAIRS if key in skills}
        quests = {q: q for q, state in status.quests.items() if state == "active"}
        page_templates = {key: key for key in rules.PAGE_TEMPLATES}

        plans: dict[str, tuple[type[BaseModel], dict[str, dict[str, str]]]] = {
            "narrate": (Narrate, {}),
            "move": (Move, {"destination_id": exits}),
            "examine": (Examine, {"page_id": here}),
            "gather": (Gather, {"page_id": gatherable}),
            "meet_npc": (MeetNpc, {"template_id": templates}),
            "talk": (Talk, {"npc_id": npcs}),
            "contest": (Contest, {"skill_id": countered, "npc_id": npcs}),
            "attempt": (AttemptAction, {"skill_id": skills}),
            "submit_quest_items": (SubmitQuestItems, {"quest_id": quests}),
            "mark_scarcity": (MarkScarcity, {"page_id": gatherable}),  # Knappheit wirkt nur aufs Sammeln
            "create_page": (CreatePage, {"template": page_templates}),
            "sleep": (Sleep, {}),
            "raise_attribute": (RaiseAttribute, {}),
            "give_money": (GiveMoney, {}),
        }
        models, lines = [], []
        for name in built.actions:
            if name not in plans:
                continue  # record_fact: Notizen kommen mit der Erzählung
            model, targets = plans[name]
            if any(not values for values in targets.values()):
                continue
            if name == "give_money":  # der NPC ist freiwillig: nur einer von hier oder keiner
                model = create_model(model.__name__, __base__=model, npc_id=(Literal[tuple(npcs)] | None if npcs else None, None))
            if name in ("attempt", "contest"):  # Einsatz (Entscheidung 231): Quelle nur ein NPC hier oder ein Fund
                gain = create_model("Gain", __base__=Gain, source=(Literal[(*npcs, FUND)], ...))
                # im Schema Pflicht und ohne null: mit null wählte das Modell nie einen Gewinn (gemessen, Entscheidung 231)
                model = create_model(model.__name__, __base__=model, gain=(gain, None))
            models.append(_narrow(model, {key: tuple(values) for key, values in targets.items()}))
            text, form = ACTION_HELP[name]
            lines.append(f"- {name}: {text}. {form}")
            if name == "give_money" and npcs:
                lines.append("  npc_id: " + ", ".join(f"{k} = {v}" for k, v in npcs.items()))
            for key, values in targets.items():
                lines.append(f"  {key}: " + ", ".join(v if k == v else f"{k} = {v}" for k, v in values.items()))
        used = {m.model_fields["type"].default for m in models}
        if used & {"attempt", "contest"}:
            lines += [
                "Werte:", "  duration: moment (Augenblick), hour (Stunde), half_day (halber Tag)",
                '  gain: was Erfolg bringt, {"kind": "nichts", "geld" oder "ware", "source": "NPC-ID hier" oder "fund"'
                ' (herrenlos), "item": null oder "Ware", "pennies": 0}. Den Betrag bestimmt der Code nach Stufe und'
                " Quelle; pennies ist nur eine Obergrenze. loss: true, wenn ein Fehlschlag Geld kostet.",
            ]
        if "attempt" in used:
            lines.append("  difficulty: " + ", ".join(
                f"{key} {word} (SG {d.dc})" for (key, d), word in zip(rules.DIFFICULTIES.items(), DIFFICULTY_WORDS)
            ))
        union = models[0] if len(models) == 1 else Annotated[Union[tuple(models)], Field(discriminator="type")]
        # Weitere Schritte (Entscheidung 225): dieselben Aktionen ohne narrate; gehen darf jeden Ort des Inhalts nennen,
        # ob er dann verbunden ist, prüft der Code. ponytail: alle Orte im Schema, bei vielen Orten nur die bekannten.
        places = {key: location.name for key, location in self.content.locations.items()}
        later = [
            _narrow(Move, {"destination_id": tuple(places)}) if m.model_fields["type"].default == "move" else m
            for m in models if m.model_fields["type"].default != "narrate"
        ]
        fields: dict = {"action": (union, ...)}
        if later:
            step = later[0] if len(later) == 1 else Annotated[Union[tuple(later)], Field(discriminator="type")]
            fields["then"] = (list[step], Field(default_factory=list, max_length=len(FOLLOW_UPS)))
            if "move" in used:
                lines.append("  then.move.destination_id: " + ", ".join(f"{k} = {v}" for k, v in places.items()))
        decision = create_model("Entscheidung", __config__=_STRICT, **fields)
        return _Choices(TypeAdapter(decision), tuple(lines), follow_ups=bool(later))

    # --- Ergebnis in Worten ---

    def describe(
        self,
        action: Action | None,
        turn: TurnResult,
        *,
        more: list[tuple[Action, TurnResult]] = (),
        skipped: list[Action] = (),
    ) -> list[str]:
        """Das Ergebnis des Codes für Aufruf B, Schritt für Schritt. Nur, was die Figur sieht: keine Werte von NPCs,
        keine Wissensstufen, die sie nicht gelernt hat."""
        world = campaign_wiki(self.conn, self.campaign_id, self.wiki or default_wiki())

        def title(page_id: str) -> str:
            return world.pages[page_id].title if page_id in world.pages else self.content.titles.get(page_id, page_id)

        lines: list[str] = []
        minutes = 0
        for number, (step_action, step) in enumerate([(action, turn), *more]):
            label = "Gewählte Aktion" if number == 0 else "Danach"
            lines += [f"{label}: {self._action_text(step_action, title)}"] if step_action else []
            if step.status == "rejected":
                lines.append(f"Abgelehnt: {step.message}")
                break
            lines += self._effect_lines(step, title, world)
            minutes += sum(e.minutes for e in step.effects if e.type == "advance_time")
        lines += [f"Nicht mehr ausgeführt: {self._action_text(a, title)}" for a in skipped]
        if any(result.status == "rejected" for result in [turn, *(r for _, r in more)]):
            return lines  # wie bisher: nach einer Ablehnung keine Zeitzeile
        clock = self._status().clock
        lines.append((f"Dauer: {minutes} Min. " if minutes else "") + f"Jetzt: Tag {clock.day}, {clock.time}")
        return lines

    def _effect_lines(self, turn: TurnResult, title: Callable[[str], str], world: Wiki) -> list[str]:
        """Wurf und Folgen eines angenommenen Spielzugs in Worten."""
        lines: list[str] = []
        if turn.check:
            c = turn.check
            lines.append(f"Wurf: W20 {c.die} {c.bonus:+d} = {c.total} gegen SG {c.dc}: {OUTCOME_TEXT[c.outcome]}")
            if c.outcome in FAILED:
                lines.append("Die Figur erfährt und gewinnt dadurch nichts; keine Notizen.")
        for effect in turn.effects:
            match effect.type:
                case "enter_location":
                    lines.append(f"Angekommen: {self.content.locations[effect.location_id].name}")
                case "add_items":
                    lines.append(f"Dazu: {effect.quantity} {title(effect.item_id)}")
                case "remove_items":
                    lines.append(f"Abgegeben: {effect.quantity} {title(effect.item_id)}")
                case "add_money" if not any(line.startswith("Erhalten: ") for line in lines):
                    # ein kritischer Erfolg bringt Geld in zwei Teilen; erzählt wird die Summe (Entscheidung 231)
                    total = sum(e.pennies for e in turn.effects if e.type == "add_money")
                    lines.append(f"Erhalten: {rules.format_money(total)}")
                case "pay_money":
                    to = self._npc_names().get(effect.npc_id or "")
                    lines.append(f"Bezahlt: {rules.format_money(effect.pennies)}" + (f" an {to}" if to else ""))
                case "take_from_npc" if not any(line.startswith("Das kommt von") for line in lines):
                    lines.append(f"Das kommt von {self._npc_names().get(effect.npc_id, effect.npc_id)}.")
                case "set_quest_status" if effect.status == "completed":
                    lines.append(f"Auftrag erledigt: {effect.quest_id}")
                case "learn_knowledge":
                    page = world.pages.get(effect.page_id)
                    stage = next((s.text for s in (page.game.knowledge if page else ())
                                  if (s.skill, s.difficulty) == (effect.skill_id, effect.difficulty)), None)
                    if stage:
                        lines.append(f"Die Figur erfährt über {title(effect.page_id)}: {stage}")
                case "create_npc":
                    lines.append(f"Neu: {effect.name} ({title(effect.template_id)}), {effect.description}")
                case "open_roll":
                    skill = rules.SKILLS[effect.skill_id].name if effect.skill_id in rules.SKILLS else effect.skill_id
                    if effect.npc_roll:
                        lines.append(f"Gegenwurf: {effect.npc_roll.die} {effect.npc_roll.bonus:+d} = {effect.npc_roll.total}")
                    lines += [f"Einsatz laut Code: {s.line()}" for s in effect.on_success if s.type == "stake"]
                    lines.append(
                        f"Probe angekündigt: {skill}, SG {effect.dc}, Grund: {effect.reason}."
                        " Der Spieler würfelt jetzt. Erzähle nur den Anlauf bis kurz vor der Entscheidung und halte"
                        " dort an: noch keine Reaktion anderer, kein Geräusch, keine neue Information, nichts über"
                        " Gelingen oder Scheitern. Keine Notizen."
                    )
                case "day_change":
                    mine = next((p for p in effect.players if p.player_id == self.player_id), None)
                    food = "" if mine is None else (
                        " Ration gegessen." if mine.ate else f" Keine Ration: {rules.HUNGER_NAMES[mine.hunger]}."
                    )
                    lines.append(f"Mitternacht: Tag {effect.day}, Wetter: {effect.weather}.{food}")
                case "rest":
                    lines.append("Geschlafen." + (f" Noch {rules.EXHAUSTION_NAMES[effect.debt]}." if effect.debt else " Ausgeruht."))
                case "add_scarcity":
                    lines.append(f"{title(effect.page_id)} ist hier {'knapp' if effect.level == 'scarce' else 'weg'}.")
                case "create_campaign_page" if effect.template != rules.WARE_TEMPLATE:
                    lines.append(f"Neues Wissen: {effect.title}")
                case "adjust_skill" if effect.points:
                    lines.append(f"{rules.SKILLS[effect.skill_id].name} steigt um {effect.points}.")
                case "grant_attribute_points":
                    lines.append(f"Levelaufstieg: +{effect.points} Attributspunkt")
                case "adjust_attribute":
                    lines.append(f"{rules.ATTRIBUTE_NAMES[effect.attribute]} steigt um {effect.points}.")
        return lines

    def _npc_id(self, given: str, names: dict[str, str]) -> str:
        """Eine NPC-ID aus dem, was das Modell nennt: die ID selbst oder der Name einer neuen oder bekannten Person."""
        known = {key: npc_id for npc_id, name in self._npc_names().items() for key in (name, slug(name))}
        return (known | names).get(given, given)

    def _npc_names(self) -> dict[str, str]:
        return {n.npc_id: n.name for n in queries.npcs(self.conn, campaign_id=self.campaign_id, wiki=self.wiki)}

    def _action_text(self, action: Action, title: Callable[[str], str]) -> str:
        npcs = self._npc_names()
        match action:
            case Move():
                location = self.content.locations.get(action.destination_id)
                return f"gehen nach {location.name if location else action.destination_id}"
            case Examine() | Gather():
                return f"{'untersuchen' if isinstance(action, Examine) else 'sammeln'}: {title(action.page_id)}"
            case MeetNpc():
                return f"neuen NPC ansprechen: {action.name}"
            case Talk():
                return f"reden mit {npcs.get(action.npc_id, action.npc_id)}"
            case Contest() | AttemptAction():
                skill = rules.SKILLS[action.skill_id].name if action.skill_id in rules.SKILLS else action.skill_id
                target = f" gegen {npcs.get(action.npc_id, action.npc_id)}" if isinstance(action, Contest) else ""
                return f"Probe {skill}{target}: {action.reason}"
            case SubmitRoll():
                return f"Wurf eingetragen: {action.value}"
            case SubmitQuestItems():
                return f"Auftrag abgeben: {action.quest_id}"
            case MarkScarcity():
                return f"{title(action.page_id)} als {'knapp' if action.level == 'scarce' else 'weg'} vermerken"
            case CreatePage():
                return f"Seite anlegen: {action.title}"
            case Sleep():
                return "schlafen"
            case RaiseAttribute():
                return f"{rules.ATTRIBUTE_NAMES[action.attribute]} steigern"
            case GiveMoney():
                to = f" an {npcs.get(action.npc_id, action.npc_id)}" if action.npc_id else ""
                return f"{rules.format_money(action.pennies)} geben{to}: {action.reason}"
            case _:
                return action.type

    def _chronicle_line(self, steps: list[tuple[Action | None, TurnResult]]) -> str | None:
        """Eine Zeile je Zug, vom Code geschrieben (Entscheidung 161), mit allen Schritten. Abgelehntes kommt nicht
        in die Chronik."""
        world = campaign_wiki(self.conn, self.campaign_id, self.wiki or default_wiki())
        texts = []
        for action, turn in steps:
            if action is None or turn.status == "rejected":
                continue
            text = self._action_text(action, lambda p: world.pages[p].title if p in world.pages else p)
            if turn.check:
                text += f" → {OUTCOME_TEXT[turn.check.outcome]}"
            gained = [f"{e.quantity} {world.pages[e.item_id].title if e.item_id in world.pages else e.item_id}"
                      for e in turn.effects if e.type == "add_items"]
            if gained:
                text += f" (+{', '.join(gained)})"
            texts.append(text)
        if not texts:
            return None
        clock = self._status().clock
        return f"Tag {clock.day}, {clock.time}: {'; '.join(texts)}"


# --- Hilfen ---


def follow_up_ids(conn: sqlite3.Connection, campaign_id: str, request_id: str) -> list[str]:
    """Die Spielzüge der weiteren Schritte eines Zugs (`/b`, `/c`; nach veralteter Revision `/b2`, `/c2`)."""
    ids = []
    for letter in FOLLOW_UPS:
        found = next((f"{request_id}/{letter}{suffix}" for suffix in ("2", "")
                      if stored_action(conn, campaign_id, f"{request_id}/{letter}{suffix}") is not None), None)
        if found is None:
            break
        ids.append(found)
    return ids


def load_prompt(path: Path = PROMPT_PATH) -> str:
    return path.read_text(encoding="utf-8").strip()


def _check_input(text: str) -> str:
    text = " ".join(text.split())
    if not text or len(text) > MAX_INPUT_CHARS:
        raise InputTooLong(f"Eingabe leer oder länger als {MAX_INPUT_CHARS} Zeichen.")
    return text


def _ideas(ideas: tuple[Idea, ...]) -> tuple[Idea, ...]:
    """Vorschläge ohne leere und doppelte; Anführungszeichen um Rede setzt erst die Seite."""
    seen, result = set(), []
    for idea in ideas:
        text = " ".join(_clean(idea.text).split()).strip("„“\"' –")
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            result.append(Idea(kind=idea.kind, text=text))
    return tuple(result)


def _clean(text: str) -> str:
    """Steuerzeichen aus Text des Modells entfernen (außer Zeilenumbrüchen), bevor er gespeichert oder gezeigt wird:
    Sonst könnte eine Antwort z. B. das Terminal umstellen. Bleibt nichts übrig, steht dort ein Strich."""
    kept = "".join(c for c in text if c == "\n" or unicodedata.category(c) not in ("Cc", "Cf"))
    return kept.strip() or "–"


def _narrow(model: type[BaseModel], targets: dict[str, tuple[str, ...]]) -> type[BaseModel]:
    """Dieselbe Aktion, aber Ziele nur aus der Liste (Enum im JSON-Schema)."""
    if not targets:
        return model
    fields = {key: (Literal[values], ...) for key, values in targets.items()}
    return create_model(model.__name__, __base__=model, **fields)
