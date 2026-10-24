"""Weboberfläche (Schritt 3, Entscheidungen 2, 3, 167 bis 169, 171 bis 177, 184 bis 191 und 205 bis 209).

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

Protokoll (Entscheidung 200): Modellaufrufe, Meldungen zu einem Zug (`/spiel/ID/melden`) und Fehler aus dem Browser
(`/fehler`) liegen in `protokoll.db`, nicht im Spielstand. Der Export unter „Einstellungen“ liefert alles seit dem letzten.
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

from valar import apply, character, cli, db, gm, journal, keys, llm, queries, rules, webview
from valar.content import CONTENT, Content
from valar.turns import RequestIdConflict
from valar.wiki import Wiki, default_wiki

DB_FILE = "spiel.db"
SETTINGS_FILE = "modelle.toml"
MAX_BODY = 64 * 1024
MAX_FIELDS = 100
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
REQUEST_ID = re.compile(r"^web-[0-9a-f]{32}$")
NOTICES = {
    "schluessel": "Schlüssel gespeichert.",
    "geloescht": "Schlüssel gelöscht.",
    "budget": "Budget gespeichert.",
}
PLAY_NOTICES = {"gemeldet": "Danke, gemeldet."}
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self'; connect-src 'self';"
    " manifest-src 'self';"
    " form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
)
HEADERS = [
    ("Content-Security-Policy", CSP),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "same-origin"),  # fremde Seiten erfahren nichts; eigene Formulare tragen ihre Herkunft (Entscheidung 189)
]
STATIC_DIR = Path(__file__).parent / "static"
# Was der Server ausliefert, fest benannt: kein Pfad aus der Anfrage erreicht die Platte.
STATIC = {
    "/stil.css": ("stil.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json; charset=utf-8"),
} | {f"/{p.name}": (p.name, "image/png") for p in sorted(STATIC_DIR.glob("symbol-*.png"))} | {f"/schrift/{p.name}": (f"schrift/{p.name}", "font/woff2") for p in sorted((STATIC_DIR / "schrift").glob("*.woff2"))}
FONT_CACHE = "public, max-age=604800"
JSON_TYPE = "application/json; charset=utf-8"
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
    download: str | None = None  # Dateiname: die Antwort ist eine Datei zum Speichern, keine Seite


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
        self.journal = journal.Journal(self.data_dir / journal.JOURNAL_FILE, now=now)  # eigene Datei, kein Spielzustand
        with closing(db.connect(self.db_path)) as conn:
            db.init_schema(conn)
            if not conn.execute("SELECT 1 FROM campaign WHERE campaign_id = ?", (campaign_id,)).fetchone():
                apply.create_campaign(conn, campaign_id)

    # --- WSGI ---

    def __call__(self, environ: dict, start_response: Callable) -> list[bytes]:
        wants_json = (environ.get("PATH_INFO") or "").endswith(("/zug", "/fehler")) or asks_json(environ)
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
        if response.download:
            headers.append(("Content-Disposition", f'attachment; filename="{response.download}"'))
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
            cache = FONT_CACHE if content_type in ("font/woff2", "image/png") else "no-store"
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
        if path == "/einstellungen/export" and method == "POST":
            return self.export(form)
        again = re.fullmatch(r"/einstellungen/export/(\d{1,9})", path)
        if again and method == "GET":
            return self.export_again(int(again.group(1)))
        if path == "/fehler" and method == "POST":
            return self.client_error(form, environ)
        match = re.fullmatch(r"/spiel/([^/]+)(/zug|/verlauf|/melden)?", path)
        if match:
            player_id, sub = match.group(1), match.group(2)
            if sub == "/verlauf" and method == "GET":
                return self.older_story(player_id, query.get("vor"))
            if sub == "/melden" and method == "POST":
                return self.report(player_id, form, as_json=asks_json(environ))
            if method == "POST" and sub in (None, "/zug"):
                return self.play(player_id, form, as_json=sub == "/zug")
            if sub:
                raise HttpError("405 Method Not Allowed", "Züge nur mit POST.")
            notice = PLAY_NOTICES.get(query.get("ok"))
            return self.play_page(
                player_id, show=query.get("zeige"), story_limit=story_limit(query.get("verlauf")), notice=notice
            )
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
            mod = webview.signed(rules.attribute_modifier(values[a])) if a in values else ""
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

    def _game_master(
        self, conn: sqlite3.Connection, player_id: str, request_id: str | None = None
    ) -> tuple[gm.GameMaster | None, str | None]:
        """Der Spielleiter, wenn es Einstellungen gibt; sonst None und, bei kaputter Datei, warum.

        Jeder Modellaufruf kommt mit Figur und Zug ins Protokoll."""
        settings, problem = self.model_settings()
        if settings is None:
            return None, problem
        client = llm.ModelClient(
            settings,
            llm.Ledger(conn),
            transport=self.transport,
            environ=self.keys.environ(self.environ),
            now=self.now,
            call_log=self.journal.calls(player_id=player_id, request_id=request_id),
        )
        master = gm.GameMaster(
            conn, client, campaign_id=self.campaign_id, player_id=player_id, content=self.content, wiki=self.wiki
        )
        return master, None

    def _session(
        self, conn: sqlite3.Connection, player_id: str, out: io.StringIO, request_id: str, *, master: bool = True
    ) -> tuple[cli.Session, str | None]:
        game_master, problem = self._game_master(conn, player_id, request_id) if master else (None, None)
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
        text = form.get("eingabe").strip()
        line = f"wurf {roll}" if roll else text
        if text and not roll and form.get("art") == "rede" and not text.startswith("!") and self.model_settings()[0]:
            line = webview.speech(text)  # Rede statt Handlung (Entscheidung 192)
        if not line:
            with closing(self._connect()) as conn:
                self._player(conn, player_id)
                return self.play_response(conn, player_id, as_json, notice="Leere Eingabe.")
        out = io.StringIO()
        self.journal.touch()  # gespielt wird jetzt: zählt für die Sitzung (Entscheidung 207)
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

    def play_page(
        self,
        player_id: str,
        *,
        show: str | None = None,
        story_limit: int = webview.STORY_EXCHANGES,
        notice: str | None = None,
    ) -> Response:
        with closing(self._connect()) as conn:
            self._player(conn, player_id)
            return self.play_response(
                conn, player_id, False, show=show, notice=self.model_settings()[1] or notice, story_limit=story_limit
            )

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
        story_limit: int = webview.STORY_EXCHANGES,
    ) -> tuple[dict[str, str], str]:
        """Die Teile der Spielseite: Lage, Verlauf, Eingabe unten, Seitenleiste. Jeder Text wird maskiert."""
        session, _ = self._session(conn, player_id, io.StringIO(), "web-unused", master=False)
        status = session.status()
        with_master = self.model_settings()[0] is not None
        url = play_url(player_id)
        new_id = new_request_id()

        story = webview.render_story(
            session, url, output=output, current=current, said=said, limit=story_limit, reports=self._reports(player_id)
        )
        if not story:
            hint = "Schreib, was deine Figur tut oder sagt." if with_master else "Ohne Spielleiter gelten die Befehle, z. B. schau."
            story = f'<p class="leer">Noch ist nichts geschehen. {e(hint)}</p>'

        last = conn.execute(
            "SELECT request_id, player_input, narration FROM exchange WHERE campaign_id = ? AND player_id = ?"
            " ORDER BY exchange_id DESC LIMIT 1",
            (self.campaign_id, player_id),
        ).fetchone()
        retry = ""
        if with_master and last and last["narration"] is None and REQUEST_ID.match(last["request_id"]):
            # dieselbe request_id holt nur die Erzählung nach (Entscheidung 163)
            retry = (
                f'<form method="post" action="{e(url)}" data-zug class="nachholen"><p>Die Erzählung zum letzten Zug fehlt.</p>'
                f'<input type="hidden" name="request_id" value="{e(last["request_id"])}">'
                f'<input type="hidden" name="eingabe" value="{e(last["player_input"])}">'
                "<button>Erzählung nachholen</button></form>"
            )
        if status.open_roll:
            roll_or_ideas = webview.render_roll(session, status.open_roll, url, new_id)
        else:
            roll_or_ideas = webview.render_suggestions(session, status, with_master)
        placeholder = "Was tut deine Figur?" if with_master else "Befehl, z. B. gehe Sturmhagen (ohne Spielleiter)"
        limit = f' maxlength="{gm.MAX_INPUT_CHARS - len(webview.speech(""))}"' if with_master else ""
        kind = (
            '<div class="art" role="radiogroup" aria-label="Art der Eingabe">'
            '<label><input type="radio" name="art" value="handlung" checked data-platzhalter="Was tut deine Figur?">'
            "<span>Handlung</span></label>"
            '<label><input type="radio" name="art" value="rede" data-platzhalter="Was sagt deine Figur?">'
            "<span>Rede</span></label></div>"
            if with_master else ""
        )
        unten = f"""{f'<p class="hinweis" role="status">{e(notice)}</p>' if notice else ""}{retry}{roll_or_ideas}
<form method="post" action="{e(url)}" data-zug class="eingabe">
<input type="hidden" name="request_id" value="{new_id}">{kind}
<textarea name="eingabe" rows="1"{limit} placeholder="{e(placeholder)}" aria-label="Eingabe" enterkeyhint="send"></textarea>
<button>Senden</button>
</form>"""
        parts = {
            "lage": webview.render_situation(status, url),
            "verlauf": story,
            "unten": unten,
            "seite": webview.render_side(session, status, url, show if show in webview.TABS else "status", with_master),
        }
        return parts, status.name

    def older_story(self, player_id: str, before: str) -> Response:
        """Ältere Wechsel für das Skript, als fertiges HTML; nur gelesen."""
        if not before.isdigit():
            raise HttpError("400 Bad Request", "Ungültige Angabe.")
        with closing(self._connect()) as conn:
            self._player(conn, player_id)
            with db.read_only(conn):
                session, _ = self._session(conn, player_id, io.StringIO(), "web-unused", master=False)
                story = webview.render_story(
                    session, play_url(player_id), before=int(before), reports=self._reports(player_id)
                )
        return Response("200 OK", json.dumps({"verlauf": story}, ensure_ascii=False), JSON_TYPE)

    def _reports(self, player_id: str) -> webview.Reports:
        """Für den Melde-Knopf an jedem Zug: frische request_ids und was schon gemeldet ist."""
        return webview.Reports(new_request_id, lambda turns: self.journal.reports_for(player_id, turns))

    # --- Protokoll: Melden, Fehler aus dem Browser, Export ---

    def report(self, player_id: str, form: Form, *, as_json: bool) -> Response:
        """Eine Meldung zu einem Zug (Fehler, unlogisch, zu hart, zu leicht, toll), dazu ein Satz. Ändert keinen
        Spielstand; Eingabe und Erzählung des Zugs kommen als Abschrift ins Protokoll."""
        request_id, turn, kind = form.get("request_id"), form.get("zug"), form.get("was")
        if not REQUEST_ID.match(request_id):
            raise HttpError("400 Bad Request", "Formular ohne gültige request_id. Bitte die Seite neu laden.")
        if kind not in journal.REPORT_KINDS:
            raise HttpError("400 Bad Request", "Bitte wählen, was an dem Zug war.")
        if len(form.get("satz")) > journal.MAX_NOTE:
            raise HttpError("400 Bad Request", f"Höchstens {journal.MAX_NOTE} Zeichen.")
        if not self.journal.usable:
            raise HttpError("503 Service Unavailable", self.journal.problem)
        note = journal.clean(form.get("satz"), journal.MAX_NOTE)
        with closing(self._connect()) as conn:
            self._player(conn, player_id)
            row = conn.execute(
                "SELECT player_input, narration FROM exchange WHERE campaign_id = ? AND player_id = ? AND request_id = ?",
                (self.campaign_id, player_id, turn),
            ).fetchone()
        if row is None:
            raise HttpError("404 Not Found", "Diesen Zug gibt es nicht.")
        try:
            self.journal.report(
                request_id=request_id,
                player_id=player_id,
                turn=turn,
                kind=kind,
                note=note,
                player_input=row["player_input"],
                narration=row["narration"],
            )
        except journal.DuplicateRequest:
            raise HttpError("409 Conflict", "Dieses Formular wurde schon mit anderer Meldung gesendet. Bitte neu laden.") from None
        if not as_json:
            return redirect(play_url(player_id) + "?ok=gemeldet")
        reported = self.journal.reports_for(player_id, [turn]).get(turn, kind)
        button = webview.render_report(play_url(player_id), turn, reported, new_request_id())
        return Response("200 OK", json.dumps({"melden": button, "hinweis": PLAY_NOTICES["gemeldet"]}, ensure_ascii=False), JSON_TYPE)

    def client_error(self, form: Form, environ: Mapping[str, str]) -> Response:
        """Ein Fehler, den die Seite selbst meldet (Skriptfehler, Verbindungsabbruch). Nur fürs Protokoll."""
        request_id, kind = form.get("request_id"), form.get("art")
        if not REQUEST_ID.match(request_id) or kind not in journal.CLIENT_ERROR_KINDS:
            raise HttpError("400 Bad Request", "Ungültige Fehlermeldung.")
        if not self.journal.usable:
            raise HttpError("503 Service Unavailable", self.journal.problem)
        stored = self.journal.client_error(
            request_id=request_id,
            kind=kind,
            message=journal.clean(form.get("meldung"), journal.MAX_MESSAGE) or "–",
            page=address(form.get("seite")),
            source=address(form.get("quelle")),
            line=small_number(form.get("zeile")),
            col=small_number(form.get("spalte")),
            stack=journal.clean(form.get("stapel"), journal.MAX_STACK),
            agent=journal.clean(environ.get("HTTP_USER_AGENT", ""), journal.MAX_AGENT),
        )
        return Response("200 OK", json.dumps({"ok": True, "neu": stored}), JSON_TYPE)

    def export(self, form: Form) -> Response:
        """Alles seit dem letzten Export als Datei (Entscheidung 209). Dieselbe request_id liefert dieselbe Datei."""
        if not REQUEST_ID.match(form.get("request_id")):
            return self.settings_page(error="Formular ohne gültige request_id. Bitte die Seite neu laden.", status="400 Bad Request")
        if not self.journal.usable:
            return self.settings_page(error=self.journal.problem, status="503 Service Unavailable")
        return download(self.journal.export(form.get("request_id")))

    def export_again(self, export_no: int) -> Response:
        if not self.journal.usable:
            return self.settings_page(error=self.journal.problem, status="503 Service Unavailable")
        try:
            return download(self.journal.export_again(export_no))
        except KeyError:
            raise HttpError("404 Not Found", "Diesen Export gibt es nicht.") from None

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
<button>Speichern</button></form></section>
{self.journal_section()}</div>"""
        return Response(status, page("Einstellungen", body))

    def journal_section(self) -> str:
        """Protokoll: was seit dem letzten Export dazukam, Export als Datei."""
        if not self.journal.usable:
            return f'<section><h2>Protokoll</h2><p class="fehler">{e(self.journal.problem)}</p></section>'
        open_ = self.journal.pending()
        last = (
            f"Letzter Export: Nr. {open_.last_export_no}, {e(shown_time(open_.last_export_at))}"
            f' · <a href="/einstellungen/export/{open_.last_export_no}">noch einmal laden</a>'
            if open_.last_export_no else "Noch kein Export."
        )
        return f"""<section><h2>Protokoll</h2>
<p>Seit dem letzten Export: {count(open_.calls, "Modellaufruf", "Modellaufrufe")}, {count(open_.reports, "Meldung", "Meldungen")}, {open_.client_errors} Fehler im Browser, aus {count(open_.sessions, "Sitzung", "Sitzungen")}.</p>
<p class="klein">{last}</p>
<form method="post" action="/einstellungen/export" class="zeile"><input type="hidden" name="request_id" value="{new_request_id()}">
<button>Exportieren</button></form>
<p class="klein">Je Modellaufruf Prompt, Antwort, Dauer und Fehler, nie ein Schlüssel; dazu deine Meldungen und Fehler aus dem
Browser. Alles bleibt {journal.KEEP.days} Tage. Eine Sitzung ist Spiel zwischen Pausen über
{int(journal.SESSION_PAUSE.total_seconds() // 60)} Minuten. Die Datei gehört in dein <b>privates</b> Repo, nie ins öffentliche.</p>
</section>"""

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


def story_limit(text: str) -> int:
    """Wie viele Wechsel die Seite ohne Skript zeigt (`?verlauf=`), begrenzt."""
    count = int(text) if text.isdigit() else webview.STORY_EXCHANGES
    return max(webview.STORY_EXCHANGES, min(count, webview.MAX_STORY_EXCHANGES))


def same_origin(environ: Mapping[str, str]) -> bool:
    """POST nur von dieser Seite selbst: Origin passt zum Host, sonst muss der Browser same-origin melden.

    Kommt doch `Origin: null` (so war es mit `Referrer-Policy: no-referrer`, Entscheidungen 187 und 189), zählt
    `Sec-Fetch-Site`, das keine Seite selbst setzen kann."""
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


def asks_json(environ: Mapping[str, str]) -> bool:
    """Das eigene Skript fragt nach JSON; ein Formular ohne Skript nicht."""
    return environ.get("HTTP_ACCEPT", "").split(",")[0].strip().lower() == "application/json"


def address(text: str) -> str:
    """Adresse aus dem Browser fürs Protokoll: ohne ?-Teil und #, gekürzt."""
    try:
        parts = urlsplit(text.strip())
    except ValueError:
        return ""
    return journal.clean(parts._replace(query="", fragment="").geturl(), journal.MAX_SOURCE)


def small_number(text: str) -> int | None:
    text = text.strip()
    return int(text) if text.isdigit() and len(text) <= 9 else None


def count(number: int, one: str, many: str) -> str:
    return f"{number} {one if number == 1 else many}"


def shown_time(text: str | None) -> str:
    """Zeit aus dem Protokoll für die Seite: 24.10.2026, 14:05 UTC."""
    if not text:
        return "–"
    moment = journal.parse(text)
    return moment.strftime("%d.%m.%Y, %H:%M UTC")


def download(file: journal.ExportFile) -> Response:
    return Response("200 OK", file.body, JSON_TYPE, download=file.name)


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
<link rel="manifest" href="/manifest.webmanifest"><link rel="icon" href="/symbol-192.png" sizes="192x192">
<link rel="apple-touch-icon" href="/symbol-180.png"><meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-title" content="ValarRP">
<meta name="apple-mobile-web-app-status-bar-style" content="black">
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
