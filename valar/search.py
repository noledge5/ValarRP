"""Nachschlagen im Wiki (Entscheidungen 140 und 144): Volltextsuche in SQLite (FTS5).

Jede Seite wird an ihren Überschriften in Abschnitte geteilt; lange Abschnitte zusätzlich an Absätzen, sodass
ein Treffer nie eine lange Seite ganz liefert. Wissensstufen stehen nicht im Index: Gesucht wird nur in dem,
was jeder weiß.

Die Suche hängt an der Schnittstelle `Retriever`. Eine Vektorsuche kann später als zweiter Retriever dazukommen
und mit `fuse` (Reciprocal Rank Fusion) verbunden werden, ohne dass sich der Kontextbauer ändert.
"""

import re
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from valar.wiki import Page, Wiki, flat_text, plain_text

CHUNK_CHARS = 800  # Höchstlänge eines Treffers in Zeichen
SEARCH_LIMIT = 3
MIN_TERM = 3  # Trigramme brauchen mindestens drei Zeichen
# Häufige Wörter, die nichts unterscheiden.
STOPWORDS = frozenset(
    "aber alle als auch auf aus bei bin bis das dass dem den der des die ein eine einem einen einer eines"
    " für gibt hat ich ihr ist mit nach nicht noch nur oder sich sie sind und uns von was welche wer wie"
    " wir wird woher wohin zum zur über etwas gegen hier dort kann".split()
)
# Endungen, die für die Suche abgeschnitten werden („Blutungen“ → „blut“). Bewusst einfach.
SUFFIXES = ("ungen", "ung", "en", "er", "es", "e", "n", "s")


@dataclass(frozen=True)
class Hit:
    page_id: str
    title: str
    heading: str | None  # Überschrift des Abschnitts, None für den Anfang der Seite
    text: str
    rank: int  # 1 = bester Treffer


class Retriever(Protocol):
    def search(self, query: str, limit: int = SEARCH_LIMIT) -> list[Hit]: ...


@dataclass(frozen=True)
class Chunk:
    page_id: str
    title: str
    aliases: str
    heading: str | None
    text: str


def chunks(page: Page) -> list[Chunk]:
    """Abschnitte einer Seite: Text vor der ersten Überschrift, dann je Überschrift; lange Teile an Absätzen."""
    aliases = " ".join(page.game.aliases)
    parts: list[tuple[str | None, str]] = []
    heading, lines = None, []
    for line in page.body.splitlines():
        match = re.match(r"^#{1,6}\s+(.*)", line)
        if match:
            parts.append((heading, "\n".join(lines)))
            heading, lines = plain_text(match.group(1)).strip(), []
        else:
            lines.append(line)
    parts.append((heading, "\n".join(lines)))

    result = []
    for heading, text in parts:
        text = flat_text(text)
        if heading is None and page.description:
            text = f"{page.description}\n{text}".strip()
        for piece in _split(text):
            result.append(Chunk(page.page_id, page.title, aliases, heading, piece))
    return [c for c in result if c.text or c.heading]


def _split(text: str) -> list[str]:
    """Teilt an Absätzen, notfalls an Zeilen und Sätzen, bis jedes Stück höchstens CHUNK_CHARS lang ist."""
    if len(text) <= CHUNK_CHARS:
        return [text]
    pieces, current = [], ""
    for unit in re.split(r"\n\s*\n|\n|(?<=[.!?])\s+", text):
        unit = unit.strip()
        if not unit:
            continue
        while len(unit) > CHUNK_CHARS:  # ein einzelner Riesensatz
            pieces.append(unit[:CHUNK_CHARS])
            unit = unit[CHUNK_CHARS:]
        if current and len(current) + 1 + len(unit) > CHUNK_CHARS:
            pieces.append(current)
            current = unit
        else:
            current = f"{current}\n{unit}" if current else unit
    if current:
        pieces.append(current)
    return pieces


def terms(query: str) -> list[str]:
    """Suchbegriffe einer freien Frage: Wörter ohne Füllwörter, dazu ihr Stamm ohne häufige Endungen."""
    found: list[str] = []
    for word in re.findall(r"\w+", query.casefold()):
        if len(word) < MIN_TERM or word in STOPWORDS or word.isdigit():
            continue
        for candidate in (word, _stem(word)):
            if len(candidate) >= MIN_TERM and candidate not in found:
                found.append(candidate)
    return found


def _stem(word: str) -> str:
    for suffix in SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


class FullTextRetriever:
    """Volltextsuche über alle Abschnitte eines Wikis, in einer SQLite-Datenbank im Speicher.

    Trigramme finden auch Wortteile („kraut“ in „Sumpfkraut“). Titel und Aliase wiegen mehr als der Text.
    Die Abfrage wird aus einzelnen, in Anführungszeichen gesetzten Wörtern gebaut; FTS5-Syntax aus der Eingabe
    wirkt dadurch nie als Befehl.
    """

    def __init__(self, wiki: Wiki) -> None:
        self._chunks = [chunk for page in _visible(wiki.pages.values()) for chunk in chunks(page)]
        self._db = sqlite3.connect(":memory:")
        self._db.execute(
            "CREATE VIRTUAL TABLE chunk USING fts5(title, aliases, heading, text, tokenize = 'trigram')"
        )
        self._db.executemany(
            "INSERT INTO chunk (rowid, title, aliases, heading, text) VALUES (?, ?, ?, ?, ?)",
            [(i, c.title, c.aliases, c.heading or "", c.text) for i, c in enumerate(self._chunks)],
        )

    def search(self, query: str, limit: int = SEARCH_LIMIT) -> list[Hit]:
        words = terms(query)
        if not words or limit <= 0:
            return []
        match = " OR ".join('"' + w.replace('"', "") + '"' for w in words)
        rows = self._db.execute(
            "SELECT rowid FROM chunk WHERE chunk MATCH ? ORDER BY bm25(chunk, 8.0, 8.0, 3.0, 1.0), rowid LIMIT ?",
            (match, limit),
        ).fetchall()
        return [_hit(self._chunks[rowid], rank) for rank, (rowid,) in enumerate(rows, start=1)]


def _visible(pages: Iterable[Page]) -> list[Page]:
    return sorted((p for p in pages if p.status != "deprecated"), key=lambda p: p.page_id)


def _hit(chunk: Chunk, rank: int) -> Hit:
    return Hit(chunk.page_id, chunk.title, chunk.heading, chunk.text, rank)


def fuse(rankings: Sequence[Sequence[Hit]], *, limit: int = SEARCH_LIMIT, k: int = 60) -> list[Hit]:
    """Reciprocal Rank Fusion: verbindet die Trefferlisten mehrerer Retriever über ihre Ränge (1 / (k + Rang)).
    Für den Tag, an dem eine Vektorsuche dazukommt; heute gibt es nur einen Retriever."""
    scores: dict[tuple[str, str | None, str], float] = {}
    first: dict[tuple[str, str | None, str], Hit] = {}
    for ranking in rankings:
        for hit in ranking:
            key = (hit.page_id, hit.heading, hit.text)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + hit.rank)
            first.setdefault(key, hit)
    ordered = sorted(scores, key=lambda key: (-scores[key], key[0], key[1] or "", key[2]))
    return [
        Hit(first[key].page_id, first[key].title, first[key].heading, first[key].text, rank)
        for rank, key in enumerate(ordered[:limit], start=1)
    ]
