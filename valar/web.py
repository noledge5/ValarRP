"""Weboberfläche (Schritt 3, Entscheidungen 2, 3, 167 bis 169 und 171 bis 177).

Ein kleiner WSGI-Server aus der Standardbibliothek, ohne JavaScript: Seiten kommen fertig vom Server, Eingaben
als Formular. Läuft im Docker-Container auf der Synology und ist nur über Tailscale erreichbar.

    python -m valar.web --daten daten --hosts synology,synology.tail1234.ts.net

Die Seite übersetzt nur Eingaben und zeigt Ergebnisse; Züge laufen über dieselbe Sitzung wie die Kommandozeile
(`cli.Session`), mit Spielleiter, wenn `modelle.toml` im Datenordner liegt. Jedes Formular trägt eine eigene
request_id: Doppelt abgeschickt wirkt es nur einmal (Entscheidung 3).

Schutz (Entscheidung 174): nur bekannte Hostnamen (gegen DNS-Rebinding), POST nur von derselben Herkunft (gegen
fremde Seiten im selben Browser), strenge Sicherheitskopfzeilen, jeder Text maskiert, Formulare höchstens 64 KB.
"""

import argparse
import html
import io
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
QUERIES = {"schau": "Umsehen", "status": "Zustand", "notizen": "Notizen", "hilfe": "Hilfe"}
NOTICES = {
    "schluessel": "Schlüssel gespeichert.",
    "geloescht": "Schlüssel gelöscht.",
    "budget": "Budget gespeichert.",
}
HEADERS = [
    ("Content-Security-Policy", "default-src 'none'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("Cache-Control", "no-store"),
]

e = html.escape


@dataclass
class Response:
    status: str
    body: str = ""
    content_type: str = "text/html; charset=utf-8"
    location: str | None = None


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
        try:
            response = self.handle(environ)
        except HttpError as error:
            response = Response(error.status, page("Fehler", f"<p>{e(str(error))}</p>"))
        except Exception:
            traceback.print_exc(file=environ.get("wsgi.errors", sys.stderr))
            response = Response(
                "500 Internal Server Error",
                page("Fehler", "<p>Interner Fehler. Bitte die Seite neu laden; der Spielstand bleibt in der Datenbank.</p>"),
            )
        headers = [("Content-Type", response.content_type), *HEADERS]
        if response.location:
            headers.append(("Location", response.location))
        body = response.body.encode("utf-8")
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

        if path == "/stil.css" and method == "GET":
            return Response("200 OK", STYLE, "text/css; charset=utf-8")
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
        match = re.fullmatch(r"/spiel/([^/]+)", path)
        if match:
            if method == "POST":
                return self.play(match.group(1), form)
            return self.play_page(match.group(1), show=query.get("zeige"))
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
        items = "".join(f'<li><a href="{e(play_url(p))}">{e(name)}</a></li>' for p, name in players.items())
        body = f'<h1>Figuren</h1><ul class="figuren">{items}</ul><p><a href="/erschaffen">Neue Figur erschaffen</a></p>'
        return Response("200 OK", page("Figuren", body))

    def create(self, form: Form | None) -> Response:
        """Charaktererschaffung (Schritt 1i) als ein Formular. Jede Abweichung: Meldung, nichts angelegt."""
        error = None
        if form is not None:
            try:
                sheet = sheet_from(form)
                with self.lock, closing(self._connect()) as conn:
                    player_id = apply.create_character(conn, self.campaign_id, sheet, self.content)
                return redirect(play_url(player_id))
            except (character.CharacterError, cli.InputError) as problem:
                error = str(problem)
            except ValidationError:
                error = "Der Bogen ist unvollständig oder hat falsche Werte."
        return Response("400 Bad Request" if error else "200 OK", page("Figur erschaffen", self.create_form(form or Form(), error)))

    def create_form(self, form: Form, error: str | None) -> str:
        wiki = self.wiki or default_wiki()
        attributes = "".join(
            f'<label>{e(name)} <input type="number" name="attr_{a}" min="{rules.START_ATTRIBUTE}"'
            f' max="{rules.MAX_START_ATTRIBUTE}" value="{e(form.get("attr_" + a, str(rules.START_ATTRIBUTE)))}" required></label>'
            for a, name in rules.ATTRIBUTE_NAMES.items()
        )

        def skills(name: str) -> str:
            chosen = set(form.all(name))
            return "".join(
                f'<label><input type="checkbox" name="{name}" value="{e(key)}"{" checked" if key in chosen else ""}>'
                f" {e(skill.name)}</label>"
                for key, skill in rules.SKILLS.items()
            )

        kits = "".join(
            f'<label><input type="radio" name="ausruestung" value="{e(k.kit_id)}"'
            f'{" checked" if form.get("ausruestung") == k.kit_id else ""} required> {e(k.name)}: '
            f"{e(rules.format_money(k.money_pennies))}"
            + "".join(f", {q} {e(wiki.pages[i].title if i in wiki.pages else i)}" for i, q in k.items.items())
            + "</label>"
            for k in self.content.start_kits.values()
        )
        problem = f'<p class="fehler" role="alert">{e(error)}</p>' if error else ""
        return f"""<h1>Figur erschaffen</h1>{problem}
<form method="post" action="/erschaffen" class="bogen">
<label>Name <input name="name" maxlength="{character.MAX_NAME}" value="{e(form.get("name"))}" required></label>
<fieldset><legend>Attribute: alle starten bei {rules.START_ATTRIBUTE}, genau {rules.START_ATTRIBUTE_POINTS} Punkte
dazu, keines über {rules.MAX_START_ATTRIBUTE}</legend><div class="raster">{attributes}</div></fieldset>
<fieldset><legend>{rules.MAIN_SKILLS} Hauptskills auf {rules.MAIN_SKILL_VALUE}</legend><div class="raster">{skills("haupt")}</div></fieldset>
<fieldset><legend>{rules.SIDE_SKILLS} Nebenskills auf {rules.SIDE_SKILL_VALUE} (Ziehen ist noch gesperrt)</legend>
<div class="raster">{skills("neben")}</div></fieldset>
<fieldset><legend>Ausrüstung</legend>{kits}</fieldset>
<label>Persönlicher Gegenstand ohne großen Wert
<input name="gegenstand" maxlength="{character.MAX_KEEPSAKE}" value="{e(form.get("gegenstand"))}" required></label>
<label>Hintergrund (Herkunft, Kontakte, Feinde; darf leer bleiben)
<textarea name="hintergrund" maxlength="{character.MAX_BACKGROUND}" rows="4">{e(form.get("hintergrund"))}</textarea></label>
<button>Erschaffen</button>
</form>"""

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

    def _session(self, conn: sqlite3.Connection, player_id: str, out: io.StringIO, request_id: str) -> tuple[cli.Session, str | None]:
        master, problem = self._game_master(conn, player_id)
        session = cli.Session(
            conn,
            out,
            player_id=player_id,
            campaign_id=self.campaign_id,
            content=self.content,
            wiki=self.wiki,
            new_request_id=request_ids(request_id),
            game_master=master,
        )
        return session, problem

    def play(self, player_id: str, form: Form) -> Response:
        """Eine Eingabe: freier Text, Befehl oder Wurf. Dieselbe request_id wirkt nur einmal."""
        request_id = form.get("request_id")
        if not REQUEST_ID.match(request_id):
            raise HttpError("400 Bad Request", "Formular ohne gültige request_id. Bitte die Seite neu laden.")
        roll = form.get("wurf").strip()
        line = f"wurf {roll}" if roll else form.get("eingabe").strip()
        if not line:
            return self.play_page(player_id, notice="Leere Eingabe.")
        out = io.StringIO()
        with self.lock, closing(self._connect()) as conn:
            self._player(conn, player_id)
            session, problem = self._session(conn, player_id, out, request_id)
            try:
                session.handle(line)
            except RequestIdConflict:
                problem = "Dieses Formular wurde schon mit anderer Eingabe gesendet. Bitte die Seite neu laden."
            return Response("200 OK", self.render_play(conn, player_id, out.getvalue(), request_id, problem))

    def play_page(self, player_id: str, *, show: str | None = None, notice: str | None = None) -> Response:
        out = io.StringIO()
        with closing(self._connect()) as conn:
            self._player(conn, player_id)
            session, problem = self._session(conn, player_id, out, "web-unused")
            if show in QUERIES:
                with db.read_only(conn):  # Abfragen ändern nichts, auch nicht aus Versehen
                    session.handle(show)
            return Response("200 OK", self.render_play(conn, player_id, out.getvalue() or None, None, notice or problem))

    def render_play(
        self, conn: sqlite3.Connection, player_id: str, output: str | None, current: str | None, notice: str | None
    ) -> str:
        status = queries.status(conn, campaign_id=self.campaign_id, player_id=player_id, content=self.content)
        with_master = self.model_settings()[0] is not None
        url = play_url(player_id)
        new_id = new_request_id()

        rows = conn.execute(
            "SELECT request_id, player_input, narration FROM exchange WHERE campaign_id = ? AND player_id = ?"
            " ORDER BY exchange_id DESC LIMIT ?",
            (self.campaign_id, player_id, STORY_EXCHANGES),
        ).fetchall()
        story = []
        for row in reversed(rows):
            if row["request_id"] == current:
                continue  # steht vollständig in der Ausgabe darunter
            story.append(f'<p class="du">{e(row["player_input"])}</p>')
            narration = row["narration"]
            story.append(f'<div class="erzaehlung">{e(narration)}</div>' if narration else '<p class="klein">(ohne Erzählung)</p>')
        retry = ""
        if with_master and rows and rows[0]["narration"] is None and REQUEST_ID.match(rows[0]["request_id"]):
            last = rows[0]  # dieselbe request_id holt nur die Erzählung nach (Entscheidung 163)
            retry = (
                f'<form method="post" action="{e(url)}" class="nachholen"><p>Die Erzählung zum letzten Zug fehlt.</p>'
                f'<input type="hidden" name="request_id" value="{e(last["request_id"])}">'
                f'<input type="hidden" name="eingabe" value="{e(last["player_input"])}">'
                "<button>Erzählung nachholen</button></form>"
            )

        roll = status.open_roll
        roll_form = ""
        if roll:
            name = rules.SKILLS[roll.skill_id].name if roll.skill_id in rules.SKILLS else roll.skill_id
            roll_form = (
                f'<form method="post" action="{e(url)}" class="wurf">'
                f"<p><strong>Probe offen:</strong> {e(name)}, SG {roll.dc}, Bonus {roll.bonus:+d},"
                f" nötig {max(roll.dc - roll.bonus, 1)}+. Grund: {e(roll.reason)}</p>"
                f'<input type="hidden" name="request_id" value="{new_id}">'
                '<label>Dein W20 <input type="number" name="wurf" min="1" max="20" required inputmode="numeric"></label>'
                "<button>Wurf eintragen</button></form>"
            )
        placeholder = "Was tut deine Figur?" if with_master else "Befehl, z. B. gehe Sturmhagen (ohne Spielleiter)"
        limit = f' maxlength="{gm.MAX_INPUT_CHARS}"' if with_master else ""
        links = " · ".join(f'<a href="{e(url)}?zeige={key}">{label}</a>' for key, label in QUERIES.items())
        money = rules.format_money(status.money_pennies)
        body = f"""<header class="lage"><h1>{e(status.name)}</h1>
<p>Tag {status.clock.day}, {e(status.clock.time)} · {e(status.location_name)} · {e(money)}</p>
<p class="abfragen">{links}</p></header>
{f'<p class="hinweis" role="status">{e(notice)}</p>' if notice else ""}
<section class="geschichte">{"".join(story)}</section>
{f'<section class="ausgabe" aria-live="polite">{e(output)}</section>' if output else ""}
{retry}{roll_form}
<form method="post" action="{e(url)}" class="eingabe">
<input type="hidden" name="request_id" value="{new_id}">
<textarea name="eingabe" rows="3"{limit} placeholder="{e(placeholder)}" aria-label="Eingabe"></textarea>
<button>Senden</button>
</form>"""
        return page(status.name, body)

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
                    f'<div class="anbieter"><h3>{e(provider.name)}</h3><p>{e(provider.model)} · {e(provider.key_env)}: {e(shown)}</p>'
                    f'<form method="post" action="/einstellungen/schluessel" class="zeile">'
                    f'<input type="hidden" name="key_env" value="{e(provider.key_env)}">'
                    f'<input type="password" name="schluessel" autocomplete="off" maxlength="400" aria-label="Schlüssel für {e(provider.name)}"'
                    f' placeholder="neuer Schlüssel" required><button>Speichern</button></form>{delete}</div>'
                )
            providers = "".join(rows) + "<p class=\"klein\">Ein Schlüssel aus der Umgebung geht dem gespeicherten vor.</p>"

        body = f"""<h1>Einstellungen</h1>
{f'<p class="hinweis" role="status">{e(notice)}</p>' if notice else ""}
{f'<p class="fehler" role="alert">{e(error)}</p>' if error else ""}
<section><h2>Schlüssel</h2>{providers}</section>
<section><h2>Budget</h2>
<p>Diesen Monat (UTC): {euro(spent, 2)} · Warnung ab {euro(budget.warn_eur)} · Stopp bei {euro(budget.stop_eur)}</p>
<form method="post" action="/einstellungen/budget" class="zeile">
<label>Warnung ab € <input name="warn_eur" inputmode="decimal" value="{budget.warn_eur:g}" required></label>
<label>Stopp bei € <input name="stop_eur" inputmode="decimal" value="{budget.stop_eur:g}" required></label>
<button>Speichern</button></form></section>"""
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


def same_origin(environ: Mapping[str, str]) -> bool:
    """POST nur von dieser Seite selbst: Origin passt zum Host, sonst muss der Browser same-origin melden."""
    origin = environ.get("HTTP_ORIGIN")
    if origin is not None:
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


def page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)} · ValarRP</title><link rel="stylesheet" href="/stil.css"></head>
<body><nav><a href="/" class="marke">ValarRP</a> <a href="/">Figuren</a> <a href="/einstellungen">Einstellungen</a></nav>
<main>{body}</main></body></html>"""


STYLE = """:root{--bg:#f4efe6;--fg:#231f1a;--muted:#6b6257;--line:#d6cdbf;--card:#fffaf2;--accent:#7a3e1d;--bad:#9b1c1c}
@media (prefers-color-scheme:dark){:root{--bg:#1a1714;--fg:#ece5da;--muted:#a69b8c;--line:#3a332b;--card:#24201b;--accent:#d99a6c;--bad:#f08a8a}}
*{box-sizing:border-box}html{overflow-wrap:anywhere}body{margin:0;background:var(--bg);color:var(--fg);font:17px/1.55 Georgia,"Iowan Old Style",serif}
nav{display:flex;gap:1rem;padding:.6rem 16px;border-bottom:1px solid var(--line);font-family:system-ui,sans-serif;font-size:.95rem}
nav a{color:var(--muted);text-decoration:none}nav .marke{color:var(--fg);font-weight:600;margin-right:auto}
main{max-width:46rem;margin:0 auto;padding:1rem 16px 3rem}a{color:var(--accent)}
h1{font-size:1.5rem;margin:.2rem 0}h2{font-size:1.15rem;margin-top:2rem}h3{font-size:1rem;margin:.2rem 0}
.lage p{margin:.2rem 0;color:var(--muted);font-family:system-ui,sans-serif;font-size:.95rem}
.du{margin:1.2rem 0 .3rem;color:var(--muted);font-style:italic;white-space:pre-wrap}.du::before{content:"› "}
.erzaehlung,.ausgabe{white-space:pre-wrap}
.ausgabe{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:.8rem 1rem;margin:1rem 0}
.hinweis{border-left:3px solid var(--accent);padding-left:.7rem}.fehler{color:var(--bad);font-weight:600}
form{margin:1rem 0}textarea,input{font:inherit;color:inherit;background:var(--card);border:1px solid var(--line);border-radius:6px;padding:.5rem;max-width:100%}
textarea{width:100%;display:block}input[type=number]{width:5rem}
button{font:600 1rem system-ui,sans-serif;background:var(--accent);color:var(--bg);border:0;border-radius:6px;padding:.6rem 1.1rem;margin-top:.5rem;cursor:pointer}
button.leise{background:transparent;color:var(--accent);border:1px solid var(--line)}
.wurf{background:var(--card);border:1px solid var(--accent);border-radius:6px;padding:.8rem 1rem}
.bogen label{display:block;margin:.6rem 0}.bogen fieldset{border:1px solid var(--line);border-radius:6px;margin:1rem 0}
.raster{display:grid;grid-template-columns:repeat(auto-fill,minmax(12rem,1fr));gap:.2rem .8rem}.raster label{margin:.2rem 0}
.zeile{display:flex;flex-wrap:wrap;gap:.5rem;align-items:end}.zeile input[type=password]{flex:1 1 12rem}
.anbieter{border-top:1px solid var(--line);padding:.6rem 0}.anbieter p,.klein{margin:.2rem 0;color:var(--muted);font-size:.9rem}
.figuren{font-size:1.2rem}
"""


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
