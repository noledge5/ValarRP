"""Welt-Wiki im OKF-Format (Entscheidungen 87 bis 93).

Ein Ordner mit Markdown-Seiten (Open Knowledge Format): oben YAML-Felder, darunter freier Text für das Modell.
Die OKF-Felder (`type`, `title`, `description`, `tags`, `status`, `generated`, `verified` …) sind offen.
Was der Code braucht, steht im Block `valar` und wird streng geprüft. Fehler verhindern das Laden, Hinweise
nicht: OKF verlangt Nachsicht bei unbekannten Feldern, unbekannten Typen und Links ins Leere.

Aufruf:
    python -m valar.wiki check [ORDNER]          # prüfen, Fehler und Hinweise auflisten
    python -m valar.wiki index [ORDNER]          # index.md je Ordner neu schreiben
    python -m valar.wiki import DATEI [ORDNER]   # Seiten aus einer Übergabe anlegen (=== pfad.md === je Seite)

ORDNER ist `welt`, wenn nichts angegeben ist.
"""

import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, NamedTuple

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from valar import rules
from valar.content import Content
from valar.models import DifficultyId, Frequency

OKF_VERSION = "0.2"
DEFAULT_ROOT = Path("welt")
RESERVED = ("index.md", "log.md")
MAX_PAGE_BYTES = 100_000
# Dateiname ohne .md ist die Seiten-ID: Kleinbuchstaben, Ziffern, Bindestriche; Umlaute als ae, oe, ue, ss.
PAGE_ID = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
PageId = Annotated[str, Field(pattern=PAGE_ID)]
SkillId = Annotated[str, Field(pattern=r"^SKILL-\d{3}$")]

# Typen, die der Code kennt, und welche Felder im Block `valar` sie haben dürfen. `knowledge` dürfen alle.
TYPES: dict[str, frozenset[str]] = {
    "Region": frozenset({"parent"}),
    "Ort": frozenset({"region", "landscapes"}),
    "Landschaft": frozenset(),
    "Pflanze": frozenset({"occurs", "item"}),
    "Tier": frozenset({"occurs", "item"}),
    "Rohstoff": frozenset({"occurs", "item"}),
    "Gegenstand": frozenset({"item"}),
    "NPC-Typ": frozenset({"occurs"}),
    "Ereignis": frozenset({"occurs"}),
    "Brauch": frozenset({"occurs"}),
    "Person": frozenset(),
    "Macht": frozenset(),
    "Wissen": frozenset(),
}
PLACE_TYPES = ("Region", "Landschaft", "Ort")
STATUSES = ("draft", "stable", "deprecated")
FREQUENCIES: tuple[Frequency, ...] = ("common", "uncommon", "rare", "very_rare")

_STRICT = ConfigDict(extra="forbid", frozen=True)


class Occurrence(BaseModel):
    """Kommt in einer Region, einer Landschaft oder an einem Ort vor, so häufig."""

    model_config = _STRICT

    where: PageId
    frequency: Frequency


class ItemDef(BaseModel):
    """Macht die Seite zum Gegenstand, der ins Gepäck kann (Entscheidung 90)."""

    model_config = _STRICT

    value_pennies: int = Field(ge=0)
    gather_skill: SkillId | None = None


class KnowledgeStage(BaseModel):
    """Wissensstufe nach Entscheidung 64: Text, den nur kennt, wer die Probe schafft."""

    model_config = _STRICT

    skill: SkillId
    difficulty: DifficultyId
    text: str = Field(min_length=1, max_length=1000)


class GameFields(BaseModel):
    """Der Block `valar` einer Seite: alles, worauf der Code sich verlässt."""

    model_config = _STRICT

    parent: PageId | None = None
    region: PageId | None = None
    landscapes: tuple[PageId, ...] = ()
    occurs: tuple[Occurrence, ...] = ()
    item: ItemDef | None = None
    knowledge: tuple[KnowledgeStage, ...] = ()


@dataclass(frozen=True)
class Page:
    page_id: str
    path: str  # relativ zum Wiki, mit /
    type: str
    title: str
    description: str | None
    status: str
    game: GameFields
    body: str
    meta: Mapping[str, Any] = field(default_factory=dict)  # alle Felder, auch die unbekannten


class Problem(NamedTuple):
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


@dataclass
class Report:
    pages: dict[str, Page] = field(default_factory=dict)
    errors: list[Problem] = field(default_factory=list)
    warnings: list[Problem] = field(default_factory=list)


class WikiError(ValueError):
    def __init__(self, problems: list[Problem]):
        self.problems = problems
        super().__init__("\n".join(str(p) for p in problems))


@dataclass(frozen=True)
class Surroundings:
    """Was zu einem Ort gehört: seine Seite, Regionen (nächste zuerst), Landschaften, was dort vorkommt."""

    location: Page | None
    regions: tuple[Page, ...]
    landscapes: tuple[Page, ...]
    occurring: tuple[tuple[Page, Frequency], ...]


@dataclass(frozen=True)
class Wiki:
    pages: Mapping[str, Page]

    def region_chain(self, region_id: str | None) -> list[Page]:
        """Die Region und alle Regionen darüber. Kreise schließt das Laden aus."""
        chain = []
        while region_id is not None:
            page = self.pages[region_id]
            chain.append(page)
            region_id = page.game.parent
        return chain

    def check_content(self, content: Content) -> list[Problem]:
        """Verweise der Orte im Inhalt auf das Wiki: Ziel muss eine Seite vom Typ Ort sein."""
        problems = []
        for location in content.locations.values():
            if location.wiki is None:
                continue
            page = self.pages.get(location.wiki)
            where = f"Inhalt, Ort {location.location_id}"
            if page is None:
                problems.append(Problem(where, f"Wiki-Seite {location.wiki!r} fehlt"))
            elif page.type != "Ort":
                problems.append(Problem(where, f"Wiki-Seite {location.wiki!r} ist kein Ort, sondern {page.type}"))
        return problems

    def surroundings(self, content: Content, location_id: str) -> Surroundings:
        """Seiten zu einem Ort des Inhalts. Ein Unterort ohne eigene Seite nimmt die seines Oberorts.

        Passen mehrere Vorkommen einer Seite, zählt das genaueste: Ort, dann Landschaft, dann die nächste Region.
        """
        page = None
        current: str | None = location_id
        while current is not None and page is None:
            location = content.locations[current]
            if location.wiki is not None:
                page = self.pages[location.wiki]
            current = location.parent_id
        if page is None:
            return Surroundings(None, (), (), ())

        regions = tuple(self.region_chain(page.game.region))
        landscapes = tuple(self.pages[i] for i in page.game.landscapes)
        rank = {page.page_id: 0} | {p.page_id: 1 for p in landscapes}
        rank |= {p.page_id: 2 + depth for depth, p in enumerate(regions)}

        occurring = []
        for other in self.pages.values():
            if other.status == "deprecated":
                continue
            matches = [(rank[o.where], o.frequency) for o in other.game.occurs if o.where in rank]
            if matches:
                occurring.append((other, min(matches)[1]))
        occurring.sort(key=lambda pair: (FREQUENCIES.index(pair[1]), pair[0].title))
        return Surroundings(page, regions, landscapes, tuple(occurring))


# --- Lesen und prüfen ---


def split_frontmatter(text: str) -> tuple[str, str] | None:
    """Teilt in YAML und Text. None, wenn die Seite nicht mit einem Block zwischen `---` beginnt."""
    text = text.lstrip("﻿").replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 3)
    if end == -1:
        if text.rstrip("\n").endswith("\n---"):
            return text[4 : text.rstrip("\n").rfind("\n---")], ""
        return None
    return text[4:end], text[end + 5 :]


def _parse_page(path: Path, rel: str, report: Report) -> Page | None:
    def error(message: str) -> None:
        report.errors.append(Problem(rel, message))

    if path.stat().st_size > MAX_PAGE_BYTES:
        return error(f"größer als {MAX_PAGE_BYTES // 1000} kB")
    try:
        parts = split_frontmatter(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        return error("kein UTF-8")
    if parts is None:
        return error("keine Felder: die Seite muss mit --- beginnen und den Block mit --- schließen")
    try:
        meta = yaml.safe_load(parts[0])
    except yaml.YAMLError as exc:
        return error(f"Felder nicht lesbar: {str(exc).splitlines()[0]}")
    if not isinstance(meta, dict):
        return error("Felder müssen Schlüssel und Werte sein")

    page_id = path.stem
    if not re.match(PAGE_ID, page_id):
        return error("Dateiname nur aus a-z, 0-9 und Bindestrichen (Umlaute als ae, oe, ue, ss)")
    type_ = meta.get("type")
    if not isinstance(type_, str) or not type_.strip():
        return error("Feld type fehlt")
    for key in ("title", "description"):
        if key in meta and not isinstance(meta[key], str):
            return error(f"Feld {key} muss Text sein")
    status = meta.get("status", "stable")
    if status not in STATUSES:
        report.warnings.append(Problem(rel, f"status {status!r} unbekannt, gilt als stable"))
        status = "stable"

    raw = meta.get("valar")
    if type_ not in TYPES:
        if raw is not None:
            return error(f"Typ {type_!r} ist dem Code unbekannt, Block valar nicht erlaubt")
        report.warnings.append(Problem(rel, f"Typ {type_!r} unbekannt, gilt als allgemeine Seite"))
    if raw is None:
        game = GameFields()
    elif not isinstance(raw, dict):
        return error("Block valar muss Schlüssel und Werte haben")
    else:
        try:
            game = GameFields.model_validate(raw)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(x) for x in first["loc"])
            return error(f"valar.{where}: {first['msg']} ({len(exc.errors())} Fehler)")
        allowed = TYPES[type_] | {"knowledge"}
        extra = sorted(game.model_fields_set - allowed)
        if extra:
            return error(f"Typ {type_} erlaubt im Block valar nicht: {', '.join(extra)}")

    return Page(
        page_id=page_id,
        path=rel,
        type=type_,
        title=meta.get("title") or page_id,
        description=meta.get("description"),
        status=status,
        game=game,
        body=parts[1].strip(),
        meta=meta,
    )


def _check_references(report: Report) -> None:
    pages = report.pages

    def need(page: Page, target: str, types: tuple[str, ...], what: str) -> None:
        other = pages.get(target)
        if other is None:
            report.errors.append(Problem(page.path, f"{what}: Seite {target!r} fehlt"))
        elif other.type not in types:
            report.errors.append(Problem(page.path, f"{what}: {target!r} ist {other.type}, erwartet {'/'.join(types)}"))

    for page in pages.values():
        g = page.game
        if g.parent is not None:
            need(page, g.parent, ("Region",), "valar.parent")
        if g.region is not None:
            need(page, g.region, ("Region",), "valar.region")
        for landscape in g.landscapes:
            need(page, landscape, ("Landschaft",), "valar.landscapes")
        seen = set()
        for occurrence in g.occurs:
            need(page, occurrence.where, PLACE_TYPES, "valar.occurs")
            if occurrence.where in seen:
                report.errors.append(Problem(page.path, f"valar.occurs: {occurrence.where!r} doppelt"))
            seen.add(occurrence.where)
        skills = [s.skill for s in g.knowledge] + ([g.item.gather_skill] if g.item and g.item.gather_skill else [])
        for skill in skills:
            if skill not in rules.SKILLS:
                report.errors.append(Problem(page.path, f"Skill {skill!r} unbekannt"))

    for page in pages.values():
        seen = {page.page_id}
        parent = page.game.parent
        while parent in pages and pages[parent].type == "Region":
            if parent in seen:
                report.errors.append(Problem(page.path, "Kreis bei den Oberregionen"))
                break
            seen.add(parent)
            parent = pages[parent].game.parent


_LINK = re.compile(r"\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")


def _check_links(root: Path, report: Report) -> None:
    """Links ins Leere sind nach OKF erlaubt (noch ungeschriebenes Wissen); sie werden nur gemeldet."""
    base = root.resolve()
    for page in report.pages.values():
        for target in _LINK.findall(page.body):
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith("#"):
                continue
            target = target.split("#", 1)[0]
            start = base if target.startswith("/") else (base / page.path).parent
            resolved = (start / target.lstrip("/")).resolve()
            if not resolved.is_relative_to(base):
                report.warnings.append(Problem(page.path, f"Link {target} zeigt aus dem Wiki hinaus"))
            elif not resolved.exists():
                report.warnings.append(Problem(page.path, f"Link {target} führt ins Leere"))


def _walk(root: Path, report: Report) -> tuple[list[tuple[Path, str]], dict[str, list[str]]]:
    """Alle Markdown-Dateien ohne Verknüpfungen und versteckte Ordner; dazu Unterordner je Ordner."""
    files = []
    subdirs: dict[str, list[str]] = {}
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        rel_dir = Path(current).relative_to(root).as_posix()
        subdirs[rel_dir] = list(dirnames)
        for name in sorted(filenames):
            path = Path(current) / name
            rel = path.relative_to(root).as_posix()
            if not name.endswith(".md"):
                continue
            if path.is_symlink():
                report.warnings.append(Problem(rel, "Verknüpfung übersprungen"))
                continue
            files.append((path, rel))
    return files, subdirs


def scan(root: Path) -> Report:
    """Liest und prüft das ganze Wiki. Liefert Seiten, Fehler und Hinweise."""
    report = Report()
    if not root.is_dir():
        report.errors.append(Problem(str(root), "Ordner fehlt"))
        return report
    files, _ = _walk(root, report)
    for path, rel in files:
        if path.name in RESERVED:
            if path.name == "log.md":
                _check_log(path, rel, report)
            continue
        page = _parse_page(path, rel, report)
        if page is None:
            continue
        if page.page_id in report.pages:
            report.errors.append(Problem(rel, f"ID {page.page_id!r} gibt es schon in {report.pages[page.page_id].path}"))
            continue
        report.pages[page.page_id] = page
    _check_references(report)
    _check_links(root, report)
    for rel_dir, expected in index_texts(root, report).items():
        index = root / rel_dir / "index.md"
        rel = (PurePosixPath(rel_dir) / "index.md").as_posix()
        if not index.exists():
            report.warnings.append(Problem(rel, "fehlt (python -m valar.wiki index)"))
        elif index.read_text(encoding="utf-8") != expected:
            report.warnings.append(Problem(rel, "veraltet (python -m valar.wiki index)"))
    return report


def _check_log(path: Path, rel: str, report: Report) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## ") and not re.fullmatch(r"## \d{4}-\d{2}-\d{2}", line.strip()):
            report.warnings.append(Problem(rel, f"Überschrift {line.strip()!r} ist kein Datum JJJJ-MM-TT"))


def load(root: Path = DEFAULT_ROOT) -> Wiki:
    """Lädt das Wiki. Fehler lösen `WikiError` aus; Hinweise stören nicht."""
    report = scan(root)
    if report.errors:
        raise WikiError(report.errors)
    return Wiki(report.pages)


# --- index.md ---


def index_texts(root: Path, report: Report) -> dict[str, str]:
    """Erwarteter Inhalt jeder index.md, je Ordner mit Seiten oder Unterordnern mit Seiten (OKF §8)."""
    by_dir: dict[str, list[Page]] = {}
    for page in report.pages.values():
        by_dir.setdefault(PurePosixPath(page.path).parent.as_posix(), []).append(page)
    dirs = set()
    for rel_dir in by_dir:
        path = PurePosixPath(rel_dir)
        dirs.add(path.as_posix())
        dirs.update(p.as_posix() for p in path.parents)
    texts = {}
    for rel_dir in sorted(dirs):
        lines = [f'---\nokf_version: "{OKF_VERSION}"\n---\n'] if rel_dir == "." else []
        lines.append(f"# {'Welt-Wiki' if rel_dir == '.' else PurePosixPath(rel_dir).name.capitalize()}\n")
        children = sorted(d for d in dirs if d != rel_dir and PurePosixPath(d).parent.as_posix() == rel_dir)
        if children:
            lines.append("## Ordner\n")
            lines += [f"* [{PurePosixPath(d).name.capitalize()}]({PurePosixPath(d).name}/)" for d in children]
            lines.append("")
        pages = sorted(by_dir.get(rel_dir, []), key=lambda p: (p.type, p.title.casefold()))
        for type_ in dict.fromkeys(p.type for p in pages):
            lines.append(f"## {type_}\n")
            for page in (p for p in pages if p.type == type_):
                entry = f"* [{page.title}]({PurePosixPath(page.path).name})"
                lines.append(entry + (f" - {page.description}" if page.description else ""))
            lines.append("")
        texts[rel_dir] = "\n".join(lines).rstrip("\n") + "\n"
    return texts


def write_indexes(root: Path) -> list[str]:
    """Schreibt jede index.md neu, die fehlt oder veraltet ist. Gibt die geänderten Pfade zurück."""
    report = scan(root)
    changed = []
    for rel_dir, text in index_texts(root, report).items():
        index = root / rel_dir / "index.md"
        if not index.exists() or index.read_text(encoding="utf-8") != text:
            index.write_text(text, encoding="utf-8")
            changed.append((PurePosixPath(rel_dir) / "index.md").as_posix())
    return changed


# --- Übergaben einlesen ---

_MARKER = re.compile(r"^===\s*(\S+?)\s*===\s*$", re.MULTILINE)
_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*$")


def split_handoff(text: str, root: Path) -> dict[Path, str]:
    """Zerlegt eine Übergabe in Dateien: `=== pfad.md ===` vor jeder Seite. Prüft jeden Pfad streng.

    Ein Pfad darf mit `welt/` beginnen. Erlaubt sind nur Ordner und Dateinamen aus a-z, 0-9 und
    Bindestrichen, die Endung .md und keine reservierten Namen; alles bleibt im Wiki-Ordner.
    """
    text = text.replace("\r\n", "\n")
    marks = list(_MARKER.finditer(text))
    if not marks:
        raise ValueError("keine Seite gefunden: jede Seite beginnt mit einer Zeile === pfad.md ===")
    base = root.resolve()
    result: dict[Path, str] = {}
    for i, mark in enumerate(marks):
        name = mark.group(1).removeprefix("welt/")
        parts = name.split("/")
        if (
            not name.endswith(".md")
            or parts[-1] in RESERVED
            or not all(re.match(PAGE_ID, p) for p in [*parts[:-1], parts[-1][:-3]])
        ):
            raise ValueError(f"Pfad {mark.group(1)!r} nicht erlaubt")
        target = (base / name).resolve()
        if not target.is_relative_to(base) or target in result:
            raise ValueError(f"Pfad {mark.group(1)!r} nicht erlaubt")
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        lines = text[mark.end() : end].strip("\n").splitlines()
        while lines and (not lines[0].strip() or _FENCE.match(lines[0])):
            lines.pop(0)
        while lines and (not lines[-1].strip() or _FENCE.match(lines[-1])):
            lines.pop()
        result[target] = "\n".join(lines) + "\n"
    return result


def import_handoff(text: str, root: Path) -> list[str]:
    """Legt die Seiten einer Übergabe an. Vorhandene Dateien bleiben unberührt: dann wird nichts geschrieben."""
    files = split_handoff(text, root)
    existing = [p for p in files if p.exists()]
    if existing:
        names = ", ".join(p.relative_to(root.resolve()).as_posix() for p in existing)
        raise ValueError(f"gibt es schon: {names}")
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return [p.relative_to(root.resolve()).as_posix() for p in files]


# --- Kommandozeile ---


def _print_report(report: Report) -> int:
    for problem in report.errors:
        print(f"Fehler  {problem}")
    for problem in report.warnings:
        print(f"Hinweis {problem}")
    drafts = sum(1 for p in report.pages.values() if p.status == "draft")
    print(
        f"{len(report.pages)} Seiten, davon {drafts} Entwurf; "
        f"{len(report.errors)} Fehler, {len(report.warnings)} Hinweise."
    )
    return 1 if report.errors else 0


def main(argv: list[str]) -> int:
    match argv:
        case ["check"] | ["check", _]:
            return _print_report(scan(Path(argv[1]) if len(argv) > 1 else DEFAULT_ROOT))
        case ["index"] | ["index", _]:
            root = Path(argv[1]) if len(argv) > 1 else DEFAULT_ROOT
            changed = write_indexes(root)
            print("\n".join(f"geschrieben: {c}" for c in changed) or "Alle index.md aktuell.")
            return _print_report(scan(root))
        case ["import", source] | ["import", source, _]:
            root = Path(argv[2]) if len(argv) > 2 else DEFAULT_ROOT
            try:
                written = import_handoff(Path(source).read_text(encoding="utf-8"), root)
            except ValueError as exc:
                print(f"Nichts geschrieben: {exc}")
                return 1
            print("\n".join(f"angelegt: {w}" for w in written))
            write_indexes(root)
            return _print_report(scan(root))
        case _:
            print(__doc__, file=sys.stderr)
            return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
