"""Teile der Spielseite für die Weboberfläche (Entscheidungen 186, 190, 191 und 206).

Jede Funktion baut fertiges HTML aus dem Spielstand und maskiert jeden Text. Die Seite und der JSON-Weg für das Skript
nutzen dieselben Funktionen, so gibt es die Anzeige nur einmal. Hier wird nur gelesen: Die Funktionen laufen auf einer
Verbindung im Lesemodus.
"""

import html
import io
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from valar import cli, gm, journal, queries, rules
from valar.models import TurnResult
from valar.turns import stored_result

STORY_EXCHANGES = 20  # so viele Wechsel zeigt die Spielseite auf einmal
MAX_STORY_EXCHANGES = 200  # ohne Skript: so weit reicht „Ältere Züge“
# Reiter der Seitenleiste
TABS = {"status": "Zustand", "schau": "Umsehen", "karte": "Karte", "personen": "Personen", "notizen": "Notizen", "hilfe": "Hilfe"}
# Harte Folgen: Zeilen der Sitzung, die so beginnen, bekommen ein Etikett; die Zeile selbst bleibt ganz stehen.
CONSEQUENCE_LABELS = (
    ("Angekommen:", "Ort"),
    ("Jetzt:", "Zeit"),
    ("W20 ", "Probe"),
    ("Probe offen:", "Probe"),
    ("Probe angesagt:", "Probe"),
    ("Gegenprobe:", "Probe"),
    ("Keine Ticks:", "Probe"),
    ("Grund:", "Grund"),
    ("Erhalten:", "Geld"),
    ("Bezahlt:", "Geld"),
    ("Gesammelt:", "Gepäck"),
    ("Neu erfahren:", "Wissen"),
    ("Neu:", "Neu"),
    ("Neue Kampagnen-Seite:", "Neu"),
    ("Notiert", "Notiz"),
    ("Vermerkt:", "Notiz"),
    ("Mitternacht:", "Tag"),
    ("Geschlafen.", "Schlaf"),
    ("Auftrag erledigt:", "Auftrag"),
    ("Levelaufstieg:", "Level"),
    ("Warnung:", "Budget"),
    ("Abgelehnt:", "Abgelehnt"),
    ("(Erzählung fehlt", "Fehler"),
    ("Spielleiter nicht erreichbar", "Fehler"),
)
BAD_LABELS = {"Abgelehnt", "Fehler", "Budget"}
ROLL_CARD_LINES = ("Probe offen:", "Gegenprobe:", "Grund:", "Jetzt würfeln:")  # stehen schon im Würfelfeld
MAX_SUGGESTED_EXITS = 4
MAX_SUGGESTED_THINGS = 2
SLEEP_FROM_HOUR, SLEEP_UNTIL_HOUR = 20, 6  # ab dann schlägt die Seite Schlafen vor
SPEECH = re.compile(r"Ich sage: „(.*)“", re.S)  # Eingabe im Modus Rede (Entscheidung 192)

e = html.escape


@dataclass(frozen=True)
class Reports:
    """Für den Melde-Knopf an jedem Zug: neue request_ids und je Zug die Art der letzten Meldung."""

    new_request_id: Callable[[], str]
    reported: Callable[[Iterable[str]], dict[str, str]]


# --- Kopf ---


def render_situation(status: queries.StatusResult, url: str) -> str:
    """Kopf des Chats: Name und die harten Werte, die sich mit jedem Zug ändern können."""
    c = status.condition
    chips = [
        (None, f"Tag {status.clock.day}, {e(status.clock.time)}"),
        ("ort", e(status.location_name)),
        ("geld", e(rules.format_money(status.money_pennies))),
        ("wetter", e(c.weather)),
    ]
    warn = []
    if c.hunger:
        warn.append(("hunger", e(c.hunger_name)))
    if c.exhaustion:
        warn.append(("erschoepfung", e(c.exhaustion_name)))
    if c.malus:
        warn.append(("malus", f"Malus {signed(c.malus)}"))
    if status.open_roll:
        warn.append(("probe", "Probe offen"))
    spans = "".join(f"<span{key_attr(k)}>{text}</span>" for k, text in chips)
    spans += "".join(f'<span class="warn"{key_attr(k)}>{text}</span>' for k, text in warn)
    return (
        f'<h1>{e(status.name)}</h1><p class="chips">{spans}</p>'
        f'<a class="seite-auf" href="{e(url)}?zeige=status#seite" data-seite-auf>Lage</a>'
    )


# --- Verlauf ---


def render_story(
    session: cli.Session,
    url: str,
    *,
    output: str | None = None,
    current: str | None = None,
    said: str | None = None,
    before: int | None = None,
    limit: int = STORY_EXCHANGES,
    reports: Reports | None = None,
) -> str:
    """Die Wechsel vor `before` (sonst die neuesten), älteste zuerst, mit harten Folgen und Kapiteln.

    Der Zug `current` zeigt die Ausgabe der Sitzung, wie sie gerade kam; frühere Züge zeigen ihre gespeicherten Folgen.
    Mit `reports` bekommt jeder Zug einen Melde-Knopf (Entscheidung 206)."""
    rows = session.conn.execute(
        "SELECT exchange_id, request_id, player_input, turn_request_id, narration FROM exchange"
        " WHERE campaign_id = ? AND player_id = ? AND (? IS NULL OR exchange_id < ?) ORDER BY exchange_id DESC LIMIT ?",
        (session.campaign_id, session.player_id, before, before, limit + 1),
    ).fetchall()
    more, rows = len(rows) > limit, rows[:limit]
    reported = reports.reported([row["request_id"] for row in rows]) if reports else {}
    story, shown = [], False
    for row in reversed(rows):
        turn = stored_result(session.conn, session.campaign_id, row["turn_request_id"]) if row["turn_request_id"] else None
        if output is not None and row["request_id"] == current:
            consequences, shown = consequence_lines(output, row["narration"]), True
        else:
            consequences = history_lines(session, row["request_id"], turn)
        report = (
            render_report(url, row["request_id"], reported.get(row["request_id"]), reports.new_request_id())
            if reports else ""
        )
        story.append(
            render_exchange(row["player_input"], row["narration"], consequences, newest=row["request_id"] == current, report=report)
        )
        last = (steps_after(session, row["request_id"]) or [turn])[-1] if turn else None  # Kapitel nach dem letzten Schritt
        story.append(render_chapter(session, last))
    if output and not shown:  # ohne Spielleiter, Befehle, Fehler: die Ausgabe der Sitzung als eigener Eintrag
        story.append(render_output(said, output))
    if more:
        oldest = rows[-1]["exchange_id"]
        story.insert(
            0,
            f'<p class="aelter"><a href="{e(url)}?verlauf={min(limit + STORY_EXCHANGES, MAX_STORY_EXCHANGES)}"'
            f' data-aelter="{oldest}">Ältere Züge</a></p>',
        )
    return "".join(story)


def render_exchange(
    player_input: str, narration: str | None, consequences: list[str] | None, *, newest: bool, report: str = ""
) -> str:
    """Ein Wechsel im Verlauf: Eingabe, Erzählung des Modells, darunter getrennt die harten Folgen und der Melde-Knopf."""
    roll = re.fullmatch(r"wurf (\d+)", player_input)
    spoken = SPEECH.fullmatch(player_input)
    said = f"Gewürfelt: {roll.group(1)}" if roll else (f"„{spoken.group(1)}“" if spoken else player_input)
    told = (
        '<div class="erzaehlung">' + "".join(f"<p>{emphasis(e(p))}</p>" for p in paragraphs(narration)) + "</div>"
        if narration else '<p class="klein">(ohne Erzählung)</p>'
    )
    you = "" if player_input == gm.OPENING_INPUT else f'<p class="du{" rede" if spoken else ""}">{e(said)}</p>'
    return f'<article class="wechsel{" neu" if newest else ""}">{you}{told}{render_consequences(consequences or [])}{report}</article>'


def render_report(url: str, turn: str, reported: str | None, request_id: str) -> str:
    """Melde-Knopf an einem Zug: Was war (Fehler, unlogisch, zu hart, zu leicht, toll), dazu ein Satz.

    Ohne Skript ein Formular in einem aufklappbaren Kasten; das Skript schickt es ohne Neuladen. Gemeldet wird ins
    Protokoll, nie in den Spielstand."""
    kinds = "".join(
        f'<label><input type="radio" name="was" value="{key}" required><span>{e(label)}</span></label>'
        for key, label in journal.REPORT_KINDS.items()
    )
    summary = f"Gemeldet: {journal.REPORT_KINDS[reported]}" if reported in journal.REPORT_KINDS else "Melden"
    return (
        f'<details class="melden"><summary{" class=\"gemeldet\"" if reported else ""}>{e(summary)}</summary>'
        f'<form method="post" action="{e(url)}/melden" data-melden>'
        f'<input type="hidden" name="request_id" value="{e(request_id)}"><input type="hidden" name="zug" value="{e(turn)}">'
        f'<fieldset class="melde-art"><legend>Was war an diesem Zug?</legend>{kinds}</fieldset>'
        f'<div class="melde-satz"><input name="satz" maxlength="{journal.MAX_NOTE}" autocomplete="off"'
        ' placeholder="Ein Satz dazu (freiwillig)" aria-label="Ein Satz dazu"><button>Melden</button></div></form></details>'
    )


def render_chapter(session: cli.Session, turn: TurnResult | None) -> str:
    """Kapitel-Trenner nach einem Zug, der einen neuen Tag oder einen neuen Ort bringt."""
    if turn is None or turn.status != "accepted":
        return ""
    parts = []
    days = [effect.day for effect in turn.effects if effect.type == "day_change"]
    if days:
        parts.append(f"Tag {max(days)}")
    places = [effect.location_id for effect in turn.effects if effect.type == "enter_location"]
    if places and places[-1] in session.content.locations:
        parts.append(session.content.locations[places[-1]].name)
    return f'<h2 class="kapitel"><span>{e(" · ".join(parts))}</span></h2>' if parts else ""


def render_output(said: str | None, output: str) -> str:
    """Ausgabe der Sitzung ohne Wechsel des Spielleiters: Befehle, Abfragen, Ablehnung, Fehler."""
    lines = [line for line in output.splitlines() if not line.startswith("Jetzt würfeln:")]
    text = "\n".join(lines).strip("\n")
    said_html = f'<p class="du">{e(said)}</p>' if said else ""
    return f'<article class="wechsel neu system">{said_html}<div class="ausgabe">{e(text)}</div></article>'


def consequence_lines(output: str, narration: str | None) -> list[str]:
    """Die Ausgabe der Sitzung ohne die Erzählung: das, was der Code bestimmt hat."""
    if narration and narration in output:
        output = output.replace(narration, "", 1)
    return [line.strip() for line in output.splitlines() if line.strip() and not line.startswith(ROLL_CARD_LINES)]


def history_lines(session: cli.Session, request_id: str, turn: TurnResult | None) -> list[str]:
    """Die Folgen eines früheren Zugs aus dem gespeicherten Spielzug, mit denselben Zeilen wie beim Spielen.

    Die Uhrzeit danach fehlt (sie stünde nur als heutiger Stand da); die angesagte Probe steht in einer Zeile."""
    lines = []
    for step in [turn, *steps_after(session, request_id)] if turn is not None else []:  # Entscheidung 225
        if step.status == "awaiting_roll":
            roll = next((effect for effect in step.effects if effect.type == "open_roll"), None)
            if roll is not None:
                name = rules.SKILLS[roll.skill_id].name if roll.skill_id in rules.SKILLS else roll.skill_id
                lines.append(f"Probe angesagt: {name}, SG {roll.dc}. Grund: {roll.reason}")
            continue
        out, session.out = session.out, io.StringIO()
        try:
            session.show_result(step)
            text = session.out.getvalue()
        finally:
            session.out = out
        lines += [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("Jetzt:")]
    for number in range(1, gm.MAX_NPCS + 1):
        npc = stored_result(session.conn, session.campaign_id, f"{request_id}/p{number}")
        for effect in npc.effects if npc and npc.status == "accepted" else ():
            if effect.type == "create_npc":
                lines.append(f"Neu: {effect.name} ({session.title(effect.template_id)})")
    for number in range(1, gm.MAX_NOTES + 1):
        note = stored_result(session.conn, session.campaign_id, f"{request_id}/n{number}")
        for effect in note.effects if note and note.status == "accepted" else ():
            if effect.type == "add_fact":
                lines.append(f"Notiert{' (Gerücht)' if effect.kind == 'rumor' else ''}: {effect.text}")
    return lines


def emphasis(escaped: str) -> str:
    """*kursiv* oder **fett** aus der Erzählung als Kursivschrift; der Text ist schon maskiert."""
    return re.sub(r"\*{1,2}([^*\n]+?)\*{1,2}", r"<em>\1</em>", escaped)


def steps_after(session: cli.Session, request_id: str) -> list[TurnResult]:
    """Die gespeicherten weiteren Schritte eines Zugs (Entscheidung 225), in ihrer Reihenfolge."""
    results = (stored_result(session.conn, session.campaign_id, t) for t in gm.follow_up_ids(session.conn, session.campaign_id, request_id))
    return [result for result in results if result is not None]


def render_consequences(lines: list[str]) -> str:
    if not lines:
        return ""
    items = []
    for line in lines:
        label = next((label for prefix, label in CONSEQUENCE_LABELS if line.startswith(prefix)), None)
        bad = ' class="schlecht"' if label in BAD_LABELS else ""
        items.append(f"<li{bad}>" + (f"<b>{label}</b> " if label else "") + f"{e(line)}</li>")
    return f'<ul class="folgen" aria-label="Folgen">{"".join(items)}</ul>'


# --- Eingabe ---


def render_roll(session: cli.Session, roll: queries.OpenRollView, url: str, new_id: str) -> str:
    """Würfelfeld bei offener Probe: Skill, SG und Grund stehen vor dem Wurf da (Entscheidung 10)."""
    name = rules.SKILLS[roll.skill_id].name if roll.skill_id in rules.SKILLS else roll.skill_id
    against = ""
    if roll.npc_roll:
        npc = roll.npc_roll
        who = {n.npc_id: n.name for n in queries.npcs(session.conn, campaign_id=session.campaign_id, wiki=session.wiki)}
        against = (
            f'<p class="klein">Gegenprobe: {e(who.get(npc.npc_id, npc.npc_id))} würfelt {npc.die} {npc.bonus:+d} = {npc.total};'
            " das ist der SG, Gleichstand gewinnst du.</p>"
        )
    ticks = f"{roll.ticks_on_success} Ticks bei Erfolg" if roll.ticks_on_success else "keine Ticks"
    return (
        f'<form method="post" action="{e(url)}" data-zug class="wurf"><div>'
        f"<p class=\"probe-titel\">Probe offen: <strong>{e(name)}</strong>, SG {roll.dc} · Bonus {roll.bonus:+d} ·"
        f" nötig {max(roll.dc - roll.bonus, 1)}+</p>"
        f'<p class="grund">Grund: {e(roll.reason)}</p>{against}<p class="klein">{ticks} · Dauer {roll.minutes} Min</p></div>'
        f'<input type="hidden" name="request_id" value="{new_id}">'
        '<div class="wurf-feld"><label>Dein W20 <input type="number" name="wurf" min="1" max="20" required inputmode="numeric"'
        ' placeholder="1–20"></label><button>Wurf eintragen</button></div></form>'
    )


def render_suggestions(session: cli.Session, status: queries.StatusResult, with_master: bool) -> str:
    """Knöpfe über dem Eingabefeld aus dem Spielstand. Sie füllen nur das Feld; was geschieht, entscheidet der Zug."""
    seen = session.look()
    ideas: list[tuple[str, str]] = [("Umsehen", "Ich sehe mich um." if with_master else "schau")]
    for npc in seen.npcs:
        ideas.append((f"Mit {npc.name} reden", f"Ich spreche mit {npc.name}." if with_master else f"rede {npc.name}"))
    for exit_ in sorted(seen.exits, key=lambda x: x.minutes)[:MAX_SUGGESTED_EXITS]:
        ideas.append((f"→ {exit_.name}", f"Ich gehe nach {exit_.name}." if with_master else f"gehe {exit_.name}"))
    for thing in [t for t in seen.things if t.gatherable][:MAX_SUGGESTED_THINGS]:
        ideas.append((f"{thing.title} sammeln", f"Ich sammle {thing.title}." if with_master else f"sammle {thing.title}"))
    hour = int(status.clock.time[:2])
    if status.condition.exhaustion or hour >= SLEEP_FROM_HOUR or hour < SLEEP_UNTIL_HOUR:
        ideas.append(("Schlafen", "Ich lege mich schlafen." if with_master else "schlafe"))
    buttons = "".join(
        f'<button type="button" class="vorschlag" data-eingabe="{e(text)}">{e(label)}</button>' for label, text in ideas
    )
    return f'<div class="vorschlaege nur-js" aria-label="Vorschläge">{buttons}</div>'


# --- Seitenleiste ---


def render_status(session: cli.Session, status: queries.StatusResult) -> str:
    c = status.condition
    values = "".join(
        f'<div{key_attr("attr-" + a)}><span>{a}</span><b>{v}</b><small>{signed(rules.attribute_modifier(v))}</small></div>'
        for a, v in status.attributes.items()
    )
    fields = [
        ("geld", "Geld", rules.format_money(status.money_pennies)),
        ("hunger", "Hunger", c.hunger_name),
        ("erschoepfung", "Erschöpfung", f"{c.exhaustion_name} ({c.awake_hours} Std. wach)"),
        ("wetter", "Wetter", c.weather),
    ]
    if c.malus:
        fields.append(("malus", "Malus auf Proben", signed(c.malus)))
    if status.attribute_points:
        fields.append(("punkte", "Attributspunkte im Vorrat", str(status.attribute_points)))
    skills = "".join(
        f"<li{key_attr('skill-' + s.skill_id)}><span>{e(s.name)}{' <small>· Ticks in dieser Szene</small>' if s.ticked_this_scene else ''}</span>"
        f"<span>{s.value} <small>({s.ticks} Ticks)</small></span></li>"
        for s in status.skills
    )
    pack = "".join(
        f"<li{key_attr('item-' + item)}><span>{e(session.title(item))}</span><span>{q}</span></li>"
        for item, q in status.inventory.items()
    )
    quests = "".join(
        f"<li{key_attr('quest-' + quest)}><span>{e(session.title(quest))}</span><span>{'aktiv' if state == 'active' else 'erledigt'}</span></li>"
        for quest, state in status.quests.items()
    )
    return (
        f'<h2>{e(status.name)}</h2><p class="klein">Level {status.level} · {e(status.location_name)}</p>'
        f'<div class="werte">{values}</div>'
        f'<dl class="felder">{"".join(f"<dt>{e(label)}:</dt><dd{key_attr(k)}>{e(v)}</dd>" for k, label, v in fields)}</dl>'
        + (f'<h3>Skills</h3><ul class="liste">{skills}</ul>' if skills else "")
        + (f'<h3>Gepäck</h3><ul class="liste">{pack}</ul>' if pack else "")
        + (f'<h3>Aufträge</h3><ul class="liste">{quests}</ul>' if quests else "")
        + (f"<h3>Persönlicher Gegenstand</h3><p>{e(status.keepsake)}</p>" if status.keepsake else "")
        + (f"<h3>Hintergrund</h3><p>{e(status.background)}</p>" if status.background else "")
    )


def render_look(session: cli.Session, with_master: bool) -> str:
    seen = session.look()
    parent = session.content.locations.get(seen.parent_id) if seen.parent_id else None
    parts = [f'<h2{key_attr("ort")}>{e(seen.name)}</h2>']
    if parent:
        parts.append(f'<p class="klein">in {e(parent.name)}</p>')
    parts.append(f"<p>{e(seen.description)}</p>")
    if seen.others_here:
        parts.append(f'<p><b class="etikett">Hier:</b> {e(", ".join(seen.others_here))}</p>')
    if seen.npcs:
        people = "".join(f"<li><b>{e(n.name)}</b> ({e(n.template_title)}): {e(n.description)}</li>" for n in seen.npcs)
        parts.append(f'<p class="etikett">Leute:</p><ul class="punkte">{people}</ul>')
    parts.append(f'<p class="etikett">Wege:</p>{render_exits(seen, with_master)}')
    if seen.regions:
        landscapes = [f"Landschaft: {', '.join(seen.landscapes)}"] if seen.landscapes else []
        parts.append(f'<p class="klein">{e(" · ".join([f"Region: {seen.regions[0]}", *landscapes, *seen.standing]))}</p>')
    for text in seen.known_here:
        parts.append(f'<p><b class="etikett">Du weißt:</b> {e(text)}</p>')
    if seen.things:
        things = "".join(
            f"<li><span>{e(t.title)} <small>({e(t.type)}, {cli.FREQUENCY_NAMES[t.frequency]}{', sammelbar' if t.gatherable else ''})</small></span></li>"
            for t in seen.things
        )
        parts.append(f'<p class="etikett">Hier kommt vor:</p><ul class="liste">{things}</ul>')
    return "".join(parts)


def render_exits(seen: queries.LookResult, with_master: bool) -> str:
    exits = []
    for exit_ in seen.exits:
        direction = f"{exit_.direction}, " if exit_.direction else ""
        prompt = f"Ich gehe nach {exit_.name}." if with_master else f"gehe {exit_.name}"
        exits.append(
            f"<li><span>{e(exit_.name)} <small>({e(direction)}{e(cli.format_distance(exit_.distance_m))},"
            f" {exit_.minutes} Min)</small></span>"
            f'<button type="button" class="leise klein nur-js" data-eingabe="{e(prompt)}">Hin</button></li>'
        )
    return f'<ul class="liste">{"".join(exits) or "<li>keine</li>"}</ul>'


def render_map(session: cli.Session, with_master: bool) -> str:
    """Karte des Gebiets aus dem Inhalt (Entscheidung 191): Orte, Wege, Standort. Unterorte stehen in der Liste darunter.

    Ein Tipp auf einen erreichbaren Ort füllt nur das Eingabefeld."""
    content = session.content
    seen = session.look()
    here = seen.location_id
    while content.locations[here].parent_id:
        here = content.locations[here].parent_id
    reachable = {}
    for exit_ in seen.exits:
        target = exit_.location_id
        if content.locations[target].parent_id is None:
            reachable[target] = f"Ich gehe nach {exit_.name}." if with_master else f"gehe {exit_.name}"
    places = [loc for loc in content.locations.values() if loc.parent_id is None]
    points = [(loc.x, loc.y) for loc in places] + [p for route in content.routes for p in content.route_points(route)]
    min_x, max_x = min(x for x, _ in points), max(x for x, _ in points)
    min_y, max_y = min(y for _, y in points), max(y for _, y in points)
    span = max(max_x - min_x, max_y - min_y, 1000)
    pad_x, pad_y = span // 5, span // 8  # Platz für Namen am Rand
    width, height = max_x - min_x + 2 * pad_x, max_y - min_y + 2 * pad_y
    unit = max(width, height) / 100  # 1 Einheit = 1 % der längeren Seite

    def at(x: int, y: int) -> str:
        return f"{x - min_x + pad_x},{max_y - y + pad_y}"  # Norden oben

    lines = []
    for route in content.routes:
        a, b = content.locations[route.a], content.locations[route.b]
        if a.parent_id or b.parent_id:
            continue
        coords = " ".join(at(x, y) for x, y in content.route_points(route))
        lines.append(f'<polyline class="weg weg-{e(route.kind)}" points="{coords}"/>')
    marks = []
    for loc in places:
        cx, cy = at(loc.x, loc.y).split(",")
        kind = "hier" if loc.location_id == here else ("ziel" if loc.location_id in reachable else "ort")
        fill = f' data-eingabe="{e(reachable[loc.location_id])}" role="button" tabindex="0"' if kind == "ziel" else ""
        label = loc.name + (f" ({seen.name})" if kind == "hier" and seen.location_id != here else "")
        marks.append(
            f'<g class="karte-{kind}"{fill}><circle cx="{cx}" cy="{cy}" r="{unit * (2.2 if kind == "hier" else 1.6):.0f}"/>'
            f'<text x="{cx}" y="{float(cy) - unit * 3.4:.0f}" font-size="{unit * 4.2:.0f}">{e(label)}</text></g>'
        )
    svg = (
        f'<svg class="karte" viewBox="0 0 {width} {height}" role="img" aria-label="Karte: du bist in {e(seen.name)}">'
        f'<g stroke-width="{unit * 0.7:.0f}">{"".join(lines)}</g>{"".join(marks)}</svg>'
    )
    return (
        f'<h2>Karte</h2>{svg}<p class="klein">Norden oben. Rot: du; umrandet: von hier erreichbar.'
        f'{" Tipp auf einen Ort füllt das Eingabefeld." if reachable else ""}</p>'
        f'<p class="etikett">Wege von {e(seen.name)}:</p>{render_exits(seen, with_master)}'
    )


def render_people(session: cli.Session, status: queries.StatusResult) -> str:
    """Alle bekannten NPCs: Wer, wo, wie beschrieben und was über sie notiert ist."""
    people = queries.npcs(session.conn, campaign_id=session.campaign_id, wiki=session.wiki)
    notes = queries.facts(session.conn, campaign_id=session.campaign_id, player_id=session.player_id)
    items = []
    for npc in sorted(people, key=lambda n: (n.location_id != status.location_id, n.name)):
        place = session.content.locations.get(npc.location_id)
        where = "hier" if npc.location_id == status.location_id else (place.name if place else npc.location_id)
        about = [
            n.text for n in notes
            if n.source_npc_id == npc.npc_id or (n.subject and n.subject.casefold() in npc.name.casefold())
        ]
        known = "".join(f"<li>{e(text)}</li>" for text in about)
        items.append(
            f'<li{key_attr("npc-" + npc.npc_id)}><b>{e(npc.name)}</b> <small>{e(npc.template_title)} · {e(where)}</small>'
            f"<br>{e(npc.description)}" + (f'<ul class="punkte">{known}</ul>' if known else "") + "</li>"
        )
    return "<h2>Personen</h2>" + (
        f'<ul class="personen">{"".join(items)}</ul>' if items else "<p>Noch niemanden getroffen.</p>"
    )


def render_notes(session: cli.Session) -> str:
    notes = queries.facts(session.conn, campaign_id=session.campaign_id, player_id=session.player_id)
    items = []
    for number, note in enumerate(notes):
        day, minute = rules.clock(note.game_minutes, session.content.start_minute_of_day)
        kind = "Gerücht" if note.kind == "rumor" else "Fakt"
        items.append(
            f'<li{key_attr(f"note-{number}")}><small>Tag {day}, {minute // 60:02d}:{minute % 60:02d} · {kind}</small>'
            f"<br>{e(note.text)}</li>"
        )
    return "<h2>Notizen</h2>" + (f'<ul class="notizen">{"".join(items)}</ul>' if items else "<p>Noch nichts notiert.</p>")


def render_help(with_master: bool) -> str:
    intro = f"<p>{e(cli.MODEL_HELP)}</p>" if with_master else "<p>Ohne Spielleiter gelten diese Befehle direkt.</p>"
    return f'<h2>Hilfe</h2>{intro}<pre class="hilfe">{e(cli.HELP)}</pre>'


def render_side(session: cli.Session, status: queries.StatusResult, url: str, show: str, with_master: bool) -> str:
    """Seitenleiste mit Reitern. Ohne Skript schalten Links um; das Skript markiert Reiter mit Neuem."""
    tabs = "".join(
        f'<a href="{e(url)}?zeige={key}#seite" data-reiter="{key}"'
        + (' class="aktiv" aria-current="true"' if key == show else "")
        + f">{label}</a>"
        for key, label in TABS.items()
    )
    panels = {
        "status": render_status(session, status),
        "schau": render_look(session, with_master),
        "karte": render_map(session, with_master),
        "personen": render_people(session, status),
        "notizen": render_notes(session),
        "hilfe": render_help(with_master),
    }
    body = "".join(
        f'<section class="tafel" data-tafel="{key}"{"" if key == show else " hidden"}>{panel}</section>'
        for key, panel in panels.items()
    )
    return (
        f'<nav class="reiter" aria-label="Seitenleiste">{tabs}</nav>'
        f'<button type="button" class="schliessen nur-js" data-seite-zu aria-label="Schließen">×</button>{body}'
    )


# --- Hilfen ---


def speech(text: str) -> str:
    """Rede statt Handlung: so steht die Eingabe für das Modell eindeutig als wörtliche Rede da."""
    return f"Ich sage: „{text}“"


def key_attr(key: str | None) -> str:
    """Schlüssel, an dem das Skript nach einem Zug erkennt, was sich geändert hat."""
    return f' data-k="{e(key)}"' if key else ""


def signed(number: int) -> str:
    """Vorzeichen mit echtem Minus, wie das Skript es zeigt."""
    return f"+{number}" if number >= 0 else f"−{-number}"


def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
