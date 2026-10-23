"""Weboberfläche (Schritt 3, Entscheidungen 2, 3, 167 bis 169, 171 bis 177 und 184 bis 187).

Ein kleiner WSGI-Server aus der Standardbibliothek. Seiten kommen fertig vom Server und funktionieren als Formulare
auch ohne JavaScript. Das eigene Skript `static/app.js` macht daraus eine Chat-Oberfläche: Es schickt dieselben
Formulare an `/spiel/ID/zug` und setzt die Teile ein, die der Server dafür baut (Entscheidung 186). Regeln prüft nur
der Server. Läuft im Docker-Container auf der Synology, im Heimnetz und über Tailscale (Entscheidung 183).

    python -m valar.web --daten daten --hosts synology,synology.tail1234.ts.net

Die Seite übersetzt nur Eingaben und zeigt Ergebnisse; Züge laufen über dieselbe Sitzung wie die Kommandozeile
(`cli.Session`), mit Spielleiter, wenn `modelle.toml` im Datenordner liegt. Jedes Formular trägt eine eigene
request_id: Doppelt abgeschickt wirkt es nur einmal (Entscheidung 3).

Schutz (Entscheidung 174): nur bekannte Hostnamen (gegen DNS-Rebinding), POST nur von derselben Herkunft (gegen
fremde Seiten im selben Browser), strenge Sicherheitskopfzeilen, Skripte nur vom eigenen Server, jeder Text maskiert,
Formulare höchstens 64 KB.
"""

import argparse
import html
import io
import json
import math
import os
import re
import sqlite3
import sys
import threading
import traceback
import uuid
from collections.abc import Callable, Iterable, Mapping
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, quote, urlsplit
from wsgiref.simple_server import WSGIServer, make_server

from pydantic import ValidationError

from valar import apply, character, cli, db, gm, keys, llm, queries, rules
from valar.content import CONTENT, Content
from valar.turns import RequestIdConflict
from valar.wiki import Wiki, default_wiki

DB_FILE = "spiel.db"
SETTINGS_FILE = "modelle.toml"
MAX_BODY = 64 * 1024
MAX_FIELDS = 100
STORY_EXCHANGES = 20  # so viele Wechsel zeigt die Spielseite
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
REQUEST_ID = re.compile(r"^web-[0-9a-f]{32}$")
QUERIES = {"status": "Zustand", "schau": "Umsehen", "notizen": "Notizen", "hilfe": "Hilfe"}  # Reiter der Seitenleiste
NOTICES = {
    "schluessel": "Schlüssel gespeichert.",
    "geloescht": "Schlüssel gelöscht.",
    "budget": "Budget gespeichert.",
}
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; connect-src 'self';"
    " form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
)
HEADERS = [
    ("Content-Security-Policy", CSP),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
]
STATIC_DIR = Path(__file__).parent / "static"
# Was der Server ausliefert, fest benannt: kein Pfad aus der Anfrage erreicht die Platte.
STATIC = {
    "/stil.css": ("stil.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
} | {f"/schrift/{p.name}": (f"schrift/{p.name}", "font/woff2") for p in sorted((STATIC_DIR / "schrift").glob("*.woff2"))}
FONT_CACHE = "public, max-age=604800"
JSON_TYPE = "application/json; charset=utf-8"
# Harte Folgen: Zeilen der Sitzung, die so beginnen, bekommen ein Etikett; die Zeile selbst bleibt ganz stehen.
CONSEQUENCE_LABELS = (
    ("Angekommen:", "Ort"),
    ("Jetzt:", "Zeit"),
    ("W20 ", "Probe"),
    ("Probe offen:", "Probe"),
    ("Gegenprobe:", "Probe"),
    ("Keine Ticks:", "Probe"),
    ("Grund:", "Grund"),
    ("Erhalten:", "Geld"),
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
CREATION_STEPS = {"name": "Name", "attribute": "Attribute", "skills": "Skills", "ausruestung": "Ausrüstung", "herkunft": "Herkunft"}
ERROR_STEP = {
    "invalid_name": "name",
    "player_exists": "name",
    "unknown_attribute": "attribute",
    "attribute_range": "attribute",
    "points_not_spent": "attribute",
    "skill_count": "skills",
    "skill_twice": "skills",
    "unknown_skill": "skills",
    "skill_locked": "skills",
    "unknown_kit": "ausruestung",
}

e = html.escape


@dataclass
class Response:
    status: str
    body: str | bytes = ""
    content_type: str = "text/html; charset=utf-8"
    location: str | None = None
    cache: str = "no-store"


class HttpError(Exception):
    def __init__(self, status: str, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class Form:
    fields: dict[str, list[str]] = field(default_factory=dict)

    def get(self, name: str, default: str = "") -> str:
        values = self.fields.get(name)
        return values[0] if values else default

    def all(self, name: str) -> tuple[str, ...]:
        return tuple(self.fields.get(name, ()))


class App:
    """Die Anwendung. Je Anfrage eine eigene Datenbankverbindung; Züge laufen nacheinander (eine Sperre)."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        hosts: Iterable[str] = (),
        settings_path: str | Path | None = None,
        campaign_id: str = cli.CAMPAIGN,
        content: Content = CONTENT,
        wiki: Wiki | None = None,
        transport: llm.Transport | None = None,  # Tests: Attrappe statt Netz
        environ: Mapping[str, str] = os.environ,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.data_dir = Path(data_dir)
        self.db_path = self.data_dir / DB_FILE
        self.settings_path = Path(settings_path) if settings_path else self.data_dir / SETTINGS_FILE
        self.hosts = frozenset(h.strip().lower() for h in (*LOCAL_HOSTS, *hosts) if h.strip())
        self.campaign_id = campaign_id
        self.content = content
        self.wiki = wiki
        self.transport = transport
        self.environ = environ
        self.now = now
        self.keys = keys.KeyStore(self.data_dir)
        self.lock = threading.Lock()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with closing(db.connect(self.db_path)) as conn:
            db.init_schema(conn)
            if not conn.execute("SELECT 1 FROM campaign WHERE campaign_id = ?", (campaign_id,)).fetchone():
                apply.create_campaign(conn, campaign_id)

    # --- WSGI ---

    def __call__(self, environ: dict, start_response: Callable) -> list[bytes]:
        wants_json = (environ.get("PATH_INFO") or "").endswith("/zug")
        try:
            response = self.handle(environ)
        except HttpError as error:
            response = error_response(error.status, str(error), wants_json)
        except Exception:
            traceback.print_exc(file=environ.get("wsgi.errors", sys.stderr))
            message = "Interner Fehler. Bitte die Seite neu laden; der Spielstand bleibt in der Datenbank."
            response = error_response("500 Internal Server Error", message, wants_json)
        headers = [("Content-Type", response.content_type), *HEADERS, ("Cache-Control", response.cache)]
        if response.location:
            headers.append(("Location", response.location))
        body = response.body if isinstance(response.body, bytes) else response.body.encode("utf-8")
        headers.append(("Content-Length", str(len(body))))
        start_response(response.status, headers)
        return [body]

    def handle(self, environ: dict) -> Response:
        if self._host(environ) not in self.hosts:
            raise HttpError("400 Bad Request", "Unbekannter Hostname. Er muss beim Start unter --hosts stehen.")
        method = environ.get("REQUEST_METHOD", "GET")
        path = environ.get("PATH_INFO") or "/"
        query = Form(parse_qs(environ.get("QUERY_STRING", ""), max_num_fields=MAX_FIELDS))
        form = Form()
        if method == "POST":
            if not same_origin(environ):
                raise HttpError("403 Forbidden", "Anfrage von einer fremden Seite abgelehnt.")
            form = read_form(environ)
        elif method != "GET":
            raise HttpError("405 Method Not Allowed", "Nur GET und POST.")

        if path in STATIC and method == "GET":
            name, content_type = STATIC[path]
            cache = FONT_CACHE if content_type == "font/woff2" else "no-store"
            return Response("200 OK", (STATIC_DIR / name).read_bytes(), content_type, cache=cache)
        if path == "/" and method == "GET":
            return self.index()
        if path == "/erschaffen":
            return self.create(form if method == "POST" else None)
        if path == "/einstellungen" and method == "GET":
            return self.settings_page(notice=NOTICES.get(query.get("ok")))
        if path == "/einstellungen/schluessel" and method == "POST":
            return self.set_key(form)
        if path == "/einstellungen/budget" and method == "POST":
            return self.set_budget(form)
        match = re.fullmatch(r"/spiel/([^/]+)(/zug)?", path)
        if match:
            player_id, as_json = match.group(1), bool(match.group(2))
            if method == "POST":
                return self.play(player_id, form, as_json=as_json)
            if as_json:
                raise HttpError("405 Method Not Allowed", "Züge nur mit POST.")
            return self.play_page(player_id, show=query.get("zeige"))
        raise HttpError("404 Not Found", "Diese Seite gibt es nicht.")

    def _host(self, environ: dict) -> str | None:
        try:
            return urlsplit("//" + environ.get("HTTP_HOST", "")).hostname
        except ValueError:
            return None

    def _connect(self) -> sqlite3.Connection:
        return db.connect(self.db_path)

    # --- Figuren ---

    def players(self, conn: sqlite3.Connection) -> dict[str, str]:
        rows = conn.execute(
            "SELECT player_id, name FROM player WHERE campaign_id = ? ORDER BY name", (self.campaign_id,)
        )
        return {row["player_id"]: row["name"] for row in rows}

    def index(self) -> Response:
        with closing(self._connect()) as conn:
            players = self.players(conn)
            if not players:
                return redirect("/erschaffen")
            cards = []
            for player_id, name in players.items():
                status = queries.status(conn, campaign_id=self.campaign_id, player_id=player_id, content=self.content)
                cards.append(
                    f'<li><a class="figur-karte" href="{e(play_url(player_id))}"><span class="figur-name">{e(name)}</span>'
                    f"<span>{e(status.location_name)} · Tag {status.clock.day}, {e(status.clock.time)}"
                    f"{' · Probe offen' if status.open_roll else ''}</span></a></li>"
                )
        body = f"""<div class="blatt schmal"><h1>Figuren</h1><ul class="figuren">{"".join(cards)}</ul>
<p><a class="knopf" href="/erschaffen">Neue Figur erschaffen</a></p></div>"""
        return Response("200 OK", page("Figuren", body))

    def create(self, form: Form | None) -> Response:
        """Charaktererschaffung (Schritt 1i) als ein Formular. Jede Abweichung: Meldung, nichts angelegt."""
        error, step = None, None
        if form is not None:
            try:
                sheet = sheet_from(form)
                with self.lock, closing(self._connect()) as conn:
                    player_id = apply.create_character(conn, self.campaign_id, sheet, self.content)
                return redirect(play_url(player_id))
            except character.CharacterError as problem:
                error, step = str(problem), ERROR_STEP.get(problem.code) or text_step(form)
            except cli.InputError as problem:
                error, step = str(problem), "attribute"
            except ValidationError:
                error, step = "Der Bogen ist unvollständig oder hat falsche Werte.", None
        body = self.create_form(form or Form(), error, step)
        return Response("400 Bad Request" if error else "200 OK", page("Figur erschaffen", body, "seite-erschaffen"))

    def create_form(self, form: Form, error: str | None, step: str | None) -> str:
        """Ohne JavaScript ein langes Formular in Abschnitten; das Skript macht daraus Schritte und zählt mit."""
        wiki = self.wiki or default_wiki()
        values = form_attributes(form)

        attributes = []
        for a, name in rules.ATTRIBUTE_NAMES.items():
            value = form.get("attr_" + a, str(rules.START_ATTRIBUTE))
            mod = signed(rules.attribute_modifier(values[a])) if a in values else ""
            attributes.append(
                f'<div class="attr" data-attr="{a}"><label for="attr_{a}"><b>{e(name)}</b> <small>{a}</small></label>'
                f'<span class="stepper"><button type="button" class="nur-js" data-delta="-1" aria-label="{e(name)} senken">−</button>'
                f'<input id="attr_{a}" type="number" name="attr_{a}" min="{rules.START_ATTRIBUTE}"'
                f' max="{rules.MAX_START_ATTRIBUTE}" value="{e(value)}" inputmode="numeric" required>'
                f'<button type="button" class="nur-js" data-delta="1" aria-label="{e(name)} erhöhen">+</button></span>'
                f'<output class="mod" title="Modifikator">{mod}</output></div>'
            )

        main, side = set(form.all("haupt")), set(form.all("neben"))
        groups: dict[str, list[str]] = {}
        for key, skill in rules.SKILLS.items():
            lead = " / ".join(skill.attributes)
            chosen = " gewaehlt" if key in main | side else ""
            groups.setdefault(skill.group, []).append(
                f'<div class="skill{chosen}" data-attrs="{" ".join(skill.attributes)}">'
                f'<span class="name">{e(skill.name)} <small>{e(lead)}</small></span><output class="bonus"></output>'
                f'<span class="wahl"><label><input type="checkbox" name="haupt" value="{e(key)}"{" checked" if key in main else ""}'
                f' aria-label="{e(skill.name)} als Hauptskill"><span>Haupt</span></label>'
                f'<label><input type="checkbox" name="neben" value="{e(key)}"{" checked" if key in side else ""}'
                f' aria-label="{e(skill.name)} als Nebenskill"><span>Neben</span></label></span></div>'
            )
        skills = "".join(
            f'<fieldset class="gruppe"><legend>{e(group)}</legend><div class="skills">{"".join(rows)}</div></fieldset>'
            for group, rows in groups.items()
        )

        kits = []
        for kit in self.content.start_kits.values():
            items = "".join(
                f"<li><b>{q} {e(wiki.pages[i].title if i in wiki.pages else i)}</b>"
                + (f" – {e(wiki.pages[i].description)}" if i in wiki.pages and wiki.pages[i].description else "")
                + "</li>"
                for i, q in kit.items.items()
            )
            kits.append(
                f'<label class="kit"><input type="radio" name="ausruestung" value="{e(kit.kit_id)}"'
                f'{" checked" if form.get("ausruestung") == kit.kit_id else ""} required>'
                f'<span class="kit-karte"><span class="kit-kopf"><b>{e(kit.name)}</b>'
                f'<span class="geld">{e(rules.format_money(kit.money_pennies))}</span></span>'
                + (f"<span>{e(kit.description)}</span>" if kit.description else "")
                + f"<ul>{items}</ul></span></label>"
            )

        steps = "".join(f'<li><a href="#schritt-{key}" data-zu="{key}"><span>{e(title)}</span></a></li>' for key, title in CREATION_STEPS.items())
        problem = f'<p class="fehler" role="alert">{e(error)}</p>' if error else ""
        spent = sum(values.get(a, rules.START_ATTRIBUTE) - rules.START_ATTRIBUTE for a in rules.ATTRIBUTES)
        return f"""<div class="erschaffen">
<form method="post" action="/erschaffen" class="bogen blatt" id="bogen" data-start="{rules.START_ATTRIBUTE}"
 data-punkte="{rules.START_ATTRIBUTE_POINTS}" data-max="{rules.MAX_START_ATTRIBUTE}" data-haupt="{rules.MAIN_SKILLS}"
 data-neben="{rules.SIDE_SKILLS}" data-haupt-wert="{rules.MAIN_SKILL_VALUE}" data-neben-wert="{rules.SIDE_SKILL_VALUE}"
 data-start-schritt="{e(step or "name")}">
<h1>Figur erschaffen</h1>
<ol class="schritte">{steps}</ol>
{problem}
<section class="schritt" id="schritt-name" data-schritt-name="name"><h2>Name</h2>
<label class="feld">Wie heißt deine Figur?
<input name="name" maxlength="{character.MAX_NAME}" value="{e(form.get("name"))}" autocomplete="off" required></label>
<p class="klein">Ein deutscher Name passt in die Welt: Grete Müller, Konrad vom Hag, Ulla Steinbrech.</p></section>
<section class="schritt" id="schritt-attribute" data-schritt-name="attribute"><h2>Attribute</h2>
<p class="klein">Alle starten bei {rules.START_ATTRIBUTE}. Verteile genau {rules.START_ATTRIBUTE_POINTS} Punkte, keines über
{rules.MAX_START_ATTRIBUTE}. Rechts steht der Modifikator, der zu jeder Probe zählt.</p>
<div class="attribute">{"".join(attributes)}</div></section>
<section class="schritt" id="schritt-skills" data-schritt-name="skills"><h2>Skills</h2>
<p class="klein">{rules.MAIN_SKILLS} Hauptskills auf {rules.MAIN_SKILL_VALUE}, {rules.SIDE_SKILLS} Nebenskills auf
{rules.SIDE_SKILL_VALUE}; jeder Skill zählt nur einmal. Ziehen ist noch gesperrt.</p>{skills}</section>
<section class="schritt" id="schritt-ausruestung" data-schritt-name="ausruestung"><h2>Ausrüstung</h2>
<div class="kits">{"".join(kits)}</div></section>
<section class="schritt" id="schritt-herkunft" data-schritt-name="herkunft"><h2>Herkunft</h2>
<label class="feld">Persönlicher Gegenstand ohne großen Wert
<input name="gegenstand" maxlength="{character.MAX_KEEPSAKE}" value="{e(form.get("gegenstand"))}" required></label>
<label class="feld">Hintergrund: Herkunft, Kontakte, Feinde (darf leer bleiben)
<textarea name="hintergrund" maxlength="{character.MAX_BACKGROUND}" rows="5">{e(form.get("hintergrund"))}</textarea></label>
</section>
<div class="schritt-nav"><button type="button" class="leise nur-js" data-zurueck>Zurück</button>
<button type="button" class="nur-js" data-weiter>Weiter</button><button class="abschicken">Figur erschaffen</button></div>
</form>
<aside class="zusammen blatt" aria-live="polite"><h2 class="zusammen-name">{e(form.get("name") or "Neue Figur")}</h2>
<p class="zaehler gross"><span>Punkte übrig</span><b data-zaehler="punkte">{rules.START_ATTRIBUTE_POINTS - spent}</b></p>
<p class="zaehler"><span>Hauptskills</span><b data-zaehler="haupt">{len(main)} / {rules.MAIN_SKILLS}</b></p>
<p class="zaehler"><span>Nebenskills</span><b data-zaehler="neben">{len(side)} / {rules.SIDE_SKILLS}</b></p>
<p class="zaehler"><span>Ausrüstung</span><b data-zaehler="ausruestung">{e(self.content.start_kits[form.get("ausruestung")].name if form.get("ausruestung") in self.content.start_kits else "–")}</b></p>
<p class="klein" data-zaehler="offen">Geprüft wird beim Erschaffen.</p></aside>
</div>"""

    # --- Spielen ---

    def _player(self, conn: sqlite3.Connection, player_id: str) -> None:
        if player_id not in self.players(conn):
            raise HttpError("404 Not Found", "Diese Figur gibt es nicht.")

    def _game_master(self, conn: sqlite3.Connection, player_id: str) -> tuple[gm.GameMaster | None, str | None]:
        """Der Spielleiter, wenn es Einstellungen gibt; sonst None und, bei kaputter Datei, warum."""
        settings, problem = self.model_settings()
        if settings is None:
            return None, problem
        client = llm.ModelClient(
            settings, llm.Ledger(conn), transport=self.transport, environ=self.keys.environ(self.environ), now=self.now
        )
        master = gm.GameMaster(
            conn, client, campaign_id=self.campaign_id, player_id=player_id, content=self.content, wiki=self.wiki
        )
        return master, None

    def _session(
        self, conn: sqlite3.Connection, player_id: str, out: io.StringIO, request_id: str, *, master: bool = True
    ) -> tuple[cli.Session, str | None]:
        game_master, problem = self._game_master(conn, player_id) if master else (None, None)
        session = cli.Session(
            conn,
            out,
            player_id=player_id,
            campaign_id=self.campaign_id,
            content=self.content,
            wiki=self.wiki,
            new_request_id=request_ids(request_id),
            game_master=game_master,
        )
        return session, problem

    def play(self, player_id: str, form: Form, *, as_json: bool = False) -> Response:
        """Eine Eingabe: freier Text, Befehl oder Wurf. Dieselbe request_id wirkt nur einmal.

        Als Formular kommt die ganze Seite zurück, unter `/zug` dieselben Teile als JSON für das Skript."""
        request_id = form.get("request_id")
        if not REQUEST_ID.match(request_id):
            raise HttpError("400 Bad Request", "Formular ohne gültige request_id. Bitte die Seite neu laden.")
        roll = form.get("wurf").strip()
        line = f"wurf {roll}" if roll else form.get("eingabe").strip()
        if not line:
            with closing(self._connect()) as conn:
                self._player(conn, player_id)
                return self.play_response(conn, player_id, as_json, notice="Leere Eingabe.")
        out = io.StringIO()
        with self.lock, closing(self._connect()) as conn:
            self._player(conn, player_id)
            session, problem = self._session(conn, player_id, out, request_id)
            try:
                session.handle(line)
            except RequestIdConflict:
                problem = "Dieses Formular wurde schon mit anderer Eingabe gesendet. Bitte die Seite neu laden."
            return self.play_response(
                conn, player_id, as_json, output=out.getvalue(), current=request_id, said=line, notice=problem
            )

    def play_page(self, player_id: str, *, show: str | None = None) -> Response:
        with closing(self._connect()) as conn:
            self._player(conn, player_id)
            return self.play_response(conn, player_id, False, show=show, notice=self.model_settings()[1])

    def play_response(self, conn: sqlite3.Connection, player_id: str, as_json: bool, **kwargs) -> Response:
        with db.read_only(conn):  # die Anzeige ändert nichts, auch nicht aus Versehen
            parts, title = self.play_parts(conn, player_id, **kwargs)
        if as_json:
            return Response("200 OK", json.dumps({"titel": title, "teile": parts}, ensure_ascii=False), JSON_TYPE)
        body = f"""<div class="buehne"><main class="chat">
<header class="lage" id="lage" data-teil="lage">{parts["lage"]}</header>
<div class="verlauf" id="verlauf"><div class="verlauf-innen" data-teil="verlauf">{parts["verlauf"]}</div></div>
<div class="unten" id="unten" data-teil="unten">{parts["unten"]}</div>
</main>
<aside class="seite" id="seite" aria-label="Figur und Umgebung" data-teil="seite">{parts["seite"]}</aside></div>"""
        return Response("200 OK", page(title, body, "seite-spiel"))

    def play_parts(
        self,
        conn: sqlite3.Connection,
        player_id: str,
        *,
        output: str | None = None,
        current: str | None = None,
        said: str | None = None,
        notice: str | None = None,
        show: str | None = None,
    ) -> tuple[dict[str, str], str]:
        """Die Teile der Spielseite: Lage, Verlauf, Eingabe unten, Seitenleiste. Jeder Text wird maskiert."""
        session, _ = self._session(conn, player_id, io.StringIO(), "web-unused", master=False)
        status = session.status()
        with_master = self.model_settings()[0] is not None
        url = play_url(player_id)
        new_id = new_request_id()

        rows = conn.execute(
            "SELECT request_id, player_input, narration FROM exchange WHERE campaign_id = ? AND player_id = ?"
            " ORDER BY exchange_id DESC LIMIT ?",
            (self.campaign_id, player_id, STORY_EXCHANGES),
        ).fetchall()
        story, shown = [], False
        for row in reversed(rows):
            consequences = None
            if output is not None and row["request_id"] == current:
                consequences, shown = consequence_lines(output, row["narration"]), True
            story.append(render_exchange(row["player_input"], row["narration"], consequences, newest=row["request_id"] == current))
        if output and not shown:  # ohne Spielleiter, Befehle, Fehler: die Ausgabe der Sitzung als eigener Eintrag
            story.append(render_output(said, output))
        if not story:
            hint = "Schreib, was deine Figur tut oder sagt." if with_master else "Ohne Spielleiter gelten die Befehle, z. B. schau."
            story.append(f'<p class="leer">Noch ist nichts geschehen. {e(hint)}</p>')

        retry = ""
        if with_master and rows and rows[0]["narration"] is None and REQUEST_ID.match(rows[0]["request_id"]):
            last = rows[0]  # dieselbe request_id holt nur die Erzählung nach (Entscheidung 163)
            retry = (
                f'<form method="post" action="{e(url)}" data-zug class="nachholen"><p>Die Erzählung zum letzten Zug fehlt.</p>'
                f'<input type="hidden" name="request_id" value="{e(last["request_id"])}">'
                f'<input type="hidden" name="eingabe" value="{e(last["player_input"])}">'
                "<button>Erzählung nachholen</button></form>"
            )
        roll_form = render_roll(session, status.open_roll, url, new_id) if status.open_roll else ""
        placeholder = "Was tut deine Figur?" if with_master else "Befehl, z. B. gehe Sturmhagen (ohne Spielleiter)"
        limit = f' maxlength="{gm.MAX_INPUT_CHARS}"' if with_master else ""
        unten = f"""{f'<p class="hinweis" role="status">{e(notice)}</p>' if notice else ""}{retry}{roll_form}
<form method="post" action="{e(url)}" data-zug class="eingabe">
<input type="hidden" name="request_id" value="{new_id}">
<textarea name="eingabe" rows="1"{limit} placeholder="{e(placeholder)}" aria-label="Eingabe" enterkeyhint="send"></textarea>
<button>Senden</button>
</form>"""
        parts = {
            "lage": render_situation(status, url),
            "verlauf": "".join(story),
            "unten": unten,
            "seite": self.render_side(session, status, url, show if show in QUERIES else "status", with_master),
        }
        return parts, status.name

    def render_side(self, session: cli.Session, status: queries.StatusResult, url: str, show: str, with_master: bool) -> str:
        """Seitenleiste mit Reitern: Zustand, Umsehen, Notizen, Hilfe. Ohne JavaScript schalten Links um."""
        tabs = "".join(
            f'<a href="{e(url)}?zeige={key}#seite" data-reiter="{key}"{' class="aktiv" aria-current="true"' if key == show else ""}>{label}</a>'
            for key, label in QUERIES.items()
        )
        panels = {
            "status": render_status(session, status),
            "schau": render_look(session, with_master),
            "notizen": render_notes(session),
            "hilfe": render_help(with_master),
        }
        body = "".join(
            f'<section class="tafel" data-tafel="{key}"{"" if key == show else " hidden"}>{html_}</section>'
            for key, html_ in panels.items()
        )
        return f'<nav class="reiter" aria-label="Seitenleiste">{tabs}</nav><button type="button" class="schliessen nur-js" data-seite-zu aria-label="Schließen">×</button>{body}'

    # --- Einstellungen: Schlüssel und Budget ---

    def model_settings(self) -> tuple[llm.ModelSettings | None, str | None]:
        if not self.settings_path.exists():
            return None, None
        try:
            settings = llm.load_settings(self.settings_path)
        except (OSError, ValueError) as error:  # fehlende Rechte, falsches TOML oder Feld
            return None, f"{self.settings_path.name} ist nicht lesbar ({type(error).__name__}); gespielt wird ohne Spielleiter."
        return keys.effective_settings(settings, self.data_dir), None

    def settings_page(self, *, notice: str | None = None, error: str | None = None, status: str = "200 OK") -> Response:
        settings, problem = self.model_settings()
        if settings is None:
            budget = keys.load_budget(self.data_dir) or llm.BudgetSettings()
        else:
            budget = settings.budget
        with closing(self._connect()) as conn:
            spent = llm.Ledger(conn).spent_micro_eur(self.now().strftime("%Y-%m")) / 1_000_000

        if settings is None:
            providers = f"<p>{e(problem or f'Keine Einstellungen: {self.settings_path.name} im Datenordner anlegen (Vorlage modelle.example.toml). Bis dahin gibt es keinen Spielleiter.')}</p>"
        else:
            rows = []
            for provider in settings.provider:
                state, tail = keys.key_state(provider.key_env, self.environ, self.keys)
                shown = {"env": "aus der Umgebung", "file": f"gesetzt, endet auf …{tail}", "missing": "fehlt"}[state]
                delete = (
                    f'<form method="post" action="/einstellungen/schluessel" class="zeile">'
                    f'<input type="hidden" name="key_env" value="{e(provider.key_env)}">'
                    '<input type="hidden" name="aktion" value="loeschen"><button class="leise">Löschen</button></form>'
                    if state == "file" else ""
                )
                rows.append(
                    f'<div class="anbieter"><h3>{e(provider.name)}</h3><p class="klein">{e(provider.model)}</p>'
                    f'<p><span class="schluessel-{state}">{e(provider.key_env)}: {e(shown)}</span></p>'
                    f'<form method="post" action="/einstellungen/schluessel" class="zeile">'
                    f'<input type="hidden" name="key_env" value="{e(provider.key_env)}">'
                    f'<input type="password" name="schluessel" autocomplete="off" maxlength="400" aria-label="Schlüssel für {e(provider.name)}"'
                    f' placeholder="neuer Schlüssel" required><button>Speichern</button></form>{delete}</div>'
                )
            providers = "".join(rows) + '<p class="klein">Ein Schlüssel aus der Umgebung geht dem gespeicherten vor.</p>'

        body = f"""<div class="blatt schmal"><h1>Einstellungen</h1>
{f'<p class="hinweis" role="status">{e(notice)}</p>' if notice else ""}
{f'<p class="fehler" role="alert">{e(error)}</p>' if error else ""}
<section><h2>Schlüssel</h2>{providers}</section>
<section><h2>Budget</h2>
<p>Diesen Monat (UTC): {euro(spent, 2)} · Warnung ab {euro(budget.warn_eur)} · Stopp bei {euro(budget.stop_eur)}</p>
<form method="post" action="/einstellungen/budget" class="zeile">
<label>Warnung ab € <input name="warn_eur" inputmode="decimal" value="{budget.warn_eur:g}" required></label>
<label>Stopp bei € <input name="stop_eur" inputmode="decimal" value="{budget.stop_eur:g}" required></label>
<button>Speichern</button></form></section></div>"""
        return Response(status, page("Einstellungen", body))

    def set_key(self, form: Form) -> Response:
        settings, _ = self.model_settings()
        key_env = form.get("key_env")
        if settings is None or key_env not in {p.key_env for p in settings.provider}:
            return self.settings_page(error="Unbekannter Anbieter.", status="400 Bad Request")
        with self.lock:
            if form.get("aktion") == "loeschen":
                self.keys.delete(key_env)
                return redirect("/einstellungen?ok=geloescht")
            try:
                self.keys.set(key_env, form.get("schluessel"))
            except keys.KeyInputError as error:
                return self.settings_page(error=str(error), status="400 Bad Request")
        return redirect("/einstellungen?ok=schluessel")

    def set_budget(self, form: Form) -> Response:
        try:
            warn, stop = (float(form.get(name).strip().replace(",", ".")) for name in ("warn_eur", "stop_eur"))
            if not (math.isfinite(warn) and math.isfinite(stop)):
                raise ValueError
            budget = llm.BudgetSettings(warn_eur=warn, stop_eur=stop)
        except (ValueError, ValidationError):
            return self.settings_page(
                error="Budget: zwei Zahlen in Euro, Stopp größer als 0, Warnung nicht über dem Stopp.",
                status="400 Bad Request",
            )
        with self.lock:
            keys.save_budget(self.data_dir, budget)
        return redirect("/einstellungen?ok=budget")


# --- Teile der Spielseite ---


def render_situation(status: queries.StatusResult, url: str) -> str:
    """Kopf des Chats: Name und die harten Werte, die sich mit jedem Zug ändern können."""
    c = status.condition
    chips = [
        f"Tag {status.clock.day}, {e(status.clock.time)}",
        e(status.location_name),
        e(rules.format_money(status.money_pennies)),
        e(c.weather),
    ]
    warn = []
    if c.hunger:
        warn.append(e(c.hunger_name))
    if c.exhaustion:
        warn.append(e(c.exhaustion_name))
    if c.malus:
        warn.append(f"Malus {c.malus:+d}")
    if status.open_roll:
        warn.append("Probe offen")
    spans = "".join(f"<span>{chip}</span>" for chip in chips) + "".join(f'<span class="warn">{chip}</span>' for chip in warn)
    return (
        f'<h1>{e(status.name)}</h1><p class="chips">{spans}</p>'
        f'<a class="seite-auf" href="{e(url)}?zeige=status#seite" data-seite-auf>Lage</a>'
    )


def render_exchange(player_input: str, narration: str | None, consequences: list[str] | None, *, newest: bool) -> str:
    """Ein Wechsel im Verlauf: Eingabe, Erzählung des Modells, darunter getrennt die harten Folgen."""
    roll = re.fullmatch(r"wurf (\d+)", player_input)
    said = f"Gewürfelt: {roll.group(1)}" if roll else player_input
    told = (
        '<div class="erzaehlung">' + "".join(f"<p>{e(p)}</p>" for p in paragraphs(narration)) + "</div>"
        if narration else '<p class="klein">(ohne Erzählung)</p>'
    )
    return f'<article class="wechsel{" neu" if newest else ""}"><p class="du">{e(said)}</p>{told}{render_consequences(consequences or [])}</article>'


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


def render_consequences(lines: list[str]) -> str:
    if not lines:
        return ""
    items = []
    for line in lines:
        label = next((label for prefix, label in CONSEQUENCE_LABELS if line.startswith(prefix)), None)
        bad = ' class="schlecht"' if label in BAD_LABELS else ""
        items.append(f"<li{bad}>" + (f"<b>{label}</b> " if label else "") + f"{e(line)}</li>")
    return f'<ul class="folgen" aria-label="Folgen">{"".join(items)}</ul>'


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


def render_status(session: cli.Session, status: queries.StatusResult) -> str:
    c = status.condition
    values = "".join(
        f'<div><span>{a}</span><b>{v}</b><small>{signed(rules.attribute_modifier(v))}</small></div>'
        for a, v in status.attributes.items()
    )
    fields = [
        ("Geld", rules.format_money(status.money_pennies)),
        ("Hunger", c.hunger_name),
        ("Erschöpfung", f"{c.exhaustion_name} ({c.awake_hours} Std. wach)"),
        ("Wetter", c.weather),
    ]
    if c.malus:
        fields.append(("Malus auf Proben", f"{c.malus:+d}"))
    if status.attribute_points:
        fields.append(("Attributspunkte im Vorrat", str(status.attribute_points)))
    skills = "".join(
        f"<li><span>{e(s.name)}{' <small>· Ticks in dieser Szene</small>' if s.ticked_this_scene else ''}</span>"
        f"<span>{s.value} <small>({s.ticks} Ticks)</small></span></li>"
        for s in status.skills
    )
    pack = "".join(f"<li><span>{e(session.title(item))}</span><span>{q}</span></li>" for item, q in status.inventory.items())
    quests = "".join(
        f"<li><span>{e(quest)}</span><span>{'aktiv' if state == 'active' else 'erledigt'}</span></li>"
        for quest, state in status.quests.items()
    )
    return (
        f'<h2>{e(status.name)}</h2><p class="klein">Level {status.level} · {e(status.location_name)}</p>'
        f'<div class="werte">{values}</div>'
        f'<dl class="felder">{"".join(f"<dt>{e(k)}:</dt><dd>{e(v)}</dd>" for k, v in fields)}</dl>'
        + (f'<h3>Skills</h3><ul class="liste">{skills}</ul>' if skills else "")
        + (f'<h3>Gepäck</h3><ul class="liste">{pack}</ul>' if pack else "")
        + (f'<h3>Aufträge</h3><ul class="liste">{quests}</ul>' if quests else "")
        + (f"<h3>Persönlicher Gegenstand</h3><p>{e(status.keepsake)}</p>" if status.keepsake else "")
        + (f"<h3>Hintergrund</h3><p>{e(status.background)}</p>" if status.background else "")
    )


def render_look(session: cli.Session, with_master: bool) -> str:
    seen = session.look()
    parent = session.content.locations.get(seen.parent_id) if seen.parent_id else None
    parts = [f"<h2>{e(seen.name)}</h2>"]
    if parent:
        parts.append(f'<p class="klein">in {e(parent.name)}</p>')
    parts.append(f"<p>{e(seen.description)}</p>")
    if seen.others_here:
        parts.append(f'<p><b class="etikett">Hier:</b> {e(", ".join(seen.others_here))}</p>')
    if seen.npcs:
        people = "".join(f"<li><b>{e(n.name)}</b> ({e(n.template_title)}): {e(n.description)}</li>" for n in seen.npcs)
        parts.append(f'<p class="etikett">Leute:</p><ul class="punkte">{people}</ul>')
    exits = []
    for exit_ in seen.exits:
        direction = f"{exit_.direction}, " if exit_.direction else ""
        prompt = f"Ich gehe nach {exit_.name}." if with_master else f"gehe {exit_.name}"
        exits.append(
            f"<li><span>{e(exit_.name)} <small>({e(direction)}{e(cli.format_distance(exit_.distance_m))},"
            f" {exit_.minutes} Min)</small></span>"
            f'<button type="button" class="leise klein nur-js" data-eingabe="{e(prompt)}">Hin</button></li>'
        )
    parts.append(f'<p class="etikett">Wege:</p><ul class="liste">{"".join(exits) or "<li>keine</li>"}</ul>')
    if seen.regions:
        landscapes = f" · Landschaft: {', '.join(seen.landscapes)}" if seen.landscapes else ""
        parts.append(f'<p class="klein">Region: {e(seen.regions[0])}{e(landscapes)}</p>')
    for text in seen.known_here:
        parts.append(f'<p><b class="etikett">Du weißt:</b> {e(text)}</p>')
    if seen.things:
        things = "".join(
            f"<li><span>{e(t.title)} <small>({e(t.type)}, {cli.FREQUENCY_NAMES[t.frequency]}{', sammelbar' if t.gatherable else ''})</small></span></li>"
            for t in seen.things
        )
        parts.append(f'<p class="etikett">Hier kommt vor:</p><ul class="liste">{things}</ul>')
    return "".join(parts)


def render_notes(session: cli.Session) -> str:
    notes = queries.facts(session.conn, campaign_id=session.campaign_id, player_id=session.player_id)
    items = []
    for note in notes:
        day, minute = rules.clock(note.game_minutes, session.content.start_minute_of_day)
        kind = "Gerücht" if note.kind == "rumor" else "Fakt"
        items.append(f'<li><small>Tag {day}, {minute // 60:02d}:{minute % 60:02d} · {kind}</small><br>{e(note.text)}</li>')
    return "<h2>Notizen</h2>" + (f'<ul class="notizen">{"".join(items)}</ul>' if items else "<p>Noch nichts notiert.</p>")


def render_help(with_master: bool) -> str:
    intro = f"<p>{e(cli.MODEL_HELP)}</p>" if with_master else "<p>Ohne Spielleiter gelten diese Befehle direkt.</p>"
    return f'<h2>Hilfe</h2>{intro}<pre class="hilfe">{e(cli.HELP)}</pre>'


def signed(number: int) -> str:
    """Vorzeichen mit echtem Minus, wie das Skript es zeigt."""
    return f"+{number}" if number >= 0 else f"−{-number}"


def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


# --- Hilfen ---


def sheet_from(form: Form) -> character.CharacterSheet:
    """Der Bogen aus dem Formular; Text wird wie in der Kommandozeile geprüft (Steuerzeichen, Länge)."""
    name = character.check_text(form.get("name"), character.MAX_NAME)
    character.check_name(name)
    attributes = {}
    for attribute, title in rules.ATTRIBUTE_NAMES.items():
        try:
            attributes[attribute] = int(form.get(f"attr_{attribute}", str(rules.START_ATTRIBUTE)))
        except ValueError:
            raise cli.InputError(f"{title}: bitte eine ganze Zahl.") from None
    return character.CharacterSheet(
        name=name,
        attributes=attributes,
        main_skills=form.all("haupt"),
        side_skills=form.all("neben"),
        kit_id=form.get("ausruestung"),
        keepsake=character.check_text(form.get("gegenstand"), character.MAX_KEEPSAKE),
        background=character.check_text(form.get("hintergrund"), character.MAX_BACKGROUND, required=False),
    )


def form_attributes(form: Form) -> dict[str, int]:
    """Die Attribute, die sich als Zahl lesen lassen; nur für die Anzeige, geprüft wird in `character`."""
    values = {}
    for attribute in rules.ATTRIBUTES:
        try:
            values[attribute] = int(form.get(f"attr_{attribute}", str(rules.START_ATTRIBUTE)))
        except ValueError:
            pass
    return values


def text_step(form: Form) -> str:
    """Welcher Schritt einen Textfehler hat: der Name, sonst Gegenstand oder Hintergrund."""
    try:
        character.check_text(form.get("name"), character.MAX_NAME)
    except character.CharacterError:
        return "name"
    return "herkunft"


def same_origin(environ: Mapping[str, str]) -> bool:
    """POST nur von dieser Seite selbst: Origin passt zum Host, sonst muss der Browser same-origin melden.

    Wegen `Referrer-Policy: no-referrer` schicken Browser bei Formularen `Origin: null` (Entscheidung 187); dann
    zählt `Sec-Fetch-Site`, das keine Seite selbst setzen kann."""
    origin = environ.get("HTTP_ORIGIN")
    if origin is not None and origin != "null":
        parts = urlsplit(origin)
        return parts.scheme in ("http", "https") and parts.netloc.lower() == environ.get("HTTP_HOST", "").lower()
    return environ.get("HTTP_SEC_FETCH_SITE") == "same-origin"


def read_form(environ: Mapping) -> Form:
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        raise HttpError("400 Bad Request", "Ungültige Länge.") from None
    if length < 0 or length > MAX_BODY:
        raise HttpError("413 Content Too Large", "Eingabe zu groß.")
    if environ.get("CONTENT_TYPE", "").split(";")[0].strip().lower() != "application/x-www-form-urlencoded":
        raise HttpError("415 Unsupported Media Type", "Nur Formulare.")
    try:
        text = environ["wsgi.input"].read(length).decode("utf-8")
        return Form(parse_qs(text, keep_blank_values=True, max_num_fields=MAX_FIELDS))
    except (UnicodeDecodeError, ValueError):
        raise HttpError("400 Bad Request", "Formular nicht lesbar.") from None


def request_ids(first: str) -> Callable[[], str]:
    """Die request_id aus dem Formular; ein zweiter Versuch derselben Eingabe (veraltete Revision) hängt -2 an."""
    count = 0

    def next_id() -> str:
        nonlocal count
        count += 1
        return first if count == 1 else f"{first}-{count}"

    return next_id


def new_request_id() -> str:
    return f"web-{uuid.uuid4().hex}"


def euro(amount: float, places: int | None = None) -> str:
    text = f"{amount:.{places}f}" if places is not None else f"{amount:g}"
    return text.replace(".", ",") + " €"


def play_url(player_id: str) -> str:
    return "/spiel/" + quote(player_id, safe="")


def redirect(location: str) -> Response:
    return Response("303 See Other", "", location=location)


def error_response(status: str, message: str, as_json: bool) -> Response:
    if as_json:
        return Response(status, json.dumps({"fehler": message}, ensure_ascii=False), JSON_TYPE)
    return Response(status, page("Fehler", f'<div class="blatt schmal"><h1>Fehler</h1><p>{e(message)}</p></div>'))


def page(title: str, body: str, kind: str = "seite-blatt") -> str:
    return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="light"><meta name="theme-color" content="#1d1712">
<title>{e(title)} · ValarRP</title><link rel="stylesheet" href="/stil.css"><script src="/app.js" defer></script></head>
<body class="{kind}"><nav class="kopf"><a href="/" class="marke">ValarRP</a><a href="/">Figuren</a><a href="/einstellungen">Einstellungen</a></nav>
{body}</body></html>"""


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m valar.web", description="ValarRP im Browser.")
    parser.add_argument("--daten", default="daten", help="Datenordner: spiel.db, modelle.toml, Schlüssel (Standard: daten)")
    parser.add_argument("--adresse", default="127.0.0.1", help="Adresse, an der der Server lauscht (Standard: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--hosts",
        default=os.environ.get("VALAR_HOSTS", ""),
        help="erlaubte Hostnamen, mit Komma getrennt, z. B. synology,synology.tail1234.ts.net (auch VALAR_HOSTS)",
    )
    parser.add_argument("--modell", metavar="TOML", help="Modell-Einstellungen (Standard: DATEN/modelle.toml)")
    args = parser.parse_args(argv)
    app = App(args.daten, hosts=args.hosts.split(","), settings_path=args.modell)
    with make_server(args.adresse, args.port, app, server_class=ThreadingWSGIServer) as server:
        print(f"ValarRP läuft auf http://{args.adresse}:{args.port}/ · erlaubte Hosts: {', '.join(sorted(app.hosts))}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
