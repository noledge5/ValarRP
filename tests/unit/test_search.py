import pytest

from tests.helpers import page
from valar import search, wiki


def a_page(body: str, *, description: str | None = None) -> wiki.Page:
    return wiki.Page(
        page_id="seite",
        path="seite.md",
        type="Wissen",
        title="Seite",
        description=description,
        status="stable",
        game=wiki.GameFields(aliases=("Andername",)),
        body=body,
    )


@pytest.mark.parametrize(
    "query, expected",
    [
        ("Gibt es etwas gegen Blutungen?", ["blutungen", "blut"]),
        ("Was ist der Rabenforst", ["rabenforst"]),
        ("Wer sind die Sturmväter?", ["sturmväter", "sturmvät"]),
        ("ab 12 Uhr", ["uhr"]),
        ("", []),
    ],
)
def test_terms_drop_filler_and_add_stems(query, expected):
    assert search.terms(query) == expected


def test_chunks_split_at_headings_and_keep_description_first():
    chunks = search.chunks(a_page("Anfang.\n\n# Teil A\nText A mit [Link](/x.md).\n\n## Teil B\nText B.", description="Kurz."))
    assert [(c.heading, c.text) for c in chunks] == [(None, "Kurz.\nAnfang."), ("Teil A", "Text A mit Link."), ("Teil B", "Text B.")]
    assert chunks[0].aliases == "Andername"


def test_long_sections_are_split_below_the_limit():
    body = "\n\n".join("Ein Satz über das Moor. " * 20 for _ in range(6))
    chunks = search.chunks(a_page(body))
    assert len(chunks) > 1 and all(len(c.text) <= search.CHUNK_CHARS for c in chunks)


def test_fuse_combines_rankings_by_rank():
    a = [search.Hit("x", "X", None, "x", 1), search.Hit("y", "Y", None, "y", 2)]
    b = [search.Hit("y", "Y", None, "y", 1), search.Hit("z", "Z", None, "z", 2)]
    assert [h.page_id for h in search.fuse([a, b])] == ["y", "x", "z"]
    assert [h.rank for h in search.fuse([a, b], limit=2)] == [1, 2]


def test_deprecated_pages_are_not_searched(tmp_path):
    from tests.helpers import write_wiki

    write_wiki(tmp_path, {"a/alt.md": page("Wissen", extra="status: deprecated\n", body="Moorgeist."), "a/neu.md": page("Wissen", body="Moorgeist.")})
    assert [h.page_id for h in search.FullTextRetriever(wiki.load(tmp_path)).search("Moorgeist")] == ["neu"]
