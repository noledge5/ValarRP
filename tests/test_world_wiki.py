"""Schritt 1d2: Welt-Wiki im OKF-Format (Entscheidungen 87 bis 93). Die Nummern folgen dem README."""

from pathlib import Path

import pytest

from tests.helpers import TEST_WIKI, content_pointing_to, page, write_wiki
from valar import wiki
from valar.content import BOOMTOWN, CONTENT, CRYSTAL_FIELD, GUILD_HALL, TAVERN, VILLAGE

WORLD = Path(__file__).parents[1] / "welt"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    write_wiki(tmp_path, TEST_WIKI)
    wiki.write_indexes(tmp_path)
    return tmp_path


def errors(root: Path) -> list[str]:
    return [str(p) for p in wiki.scan(root).errors]


def warnings(root: Path) -> list[str]:
    return [str(p) for p in wiki.scan(root).warnings]


# --- 1: Felder und Text; Fehler nennen Datei und Grund ---


def test_1_the_test_wiki_loads_without_problems(root):
    assert wiki.scan(root).errors == []
    assert wiki.scan(root).warnings == []
    loaded = wiki.load(root)
    assert loaded.pages["sumpfkraut"].title == "Sumpfkraut"
    assert loaded.pages["sumpfkraut"].game.item.value_pennies == 3
    assert loaded.pages["sumpfkraut"].body == "Text."


@pytest.mark.parametrize(
    "text, reason",
    [
        ("Nur Text.\n", "keine Felder"),
        ("---\ntype: Pflanze\nText ohne Ende.\n", "keine Felder"),
        ("---\ntitle: Ohne Typ\n---\nText.\n", "Feld type fehlt"),
        ("---\ntype: ''\n---\nText.\n", "Feld type fehlt"),
        ("---\ntype: [Pflanze\n---\nText.\n", "Felder nicht lesbar"),
        ("---\n- eine Liste\n---\nText.\n", "Schlüssel und Werte"),
        ("---\ntype: Pflanze\ntitle: [kein, text]\n---\nText.\n", "title muss Text sein"),
    ],
)
def test_1_a_page_without_readable_fields_or_type_fails(root, text, reason):
    write_wiki(root, {"pflanzen/kaputt.md": text})

    found = errors(root)

    assert len(found) == 1 and found[0].startswith("pflanzen/kaputt.md: ") and reason in found[0]
    with pytest.raises(wiki.WikiError, match="pflanzen/kaputt.md"):
        wiki.load(root)


def test_1_yaml_cannot_run_code(root):
    write_wiki(root, {"wissen/boese.md": "---\ntype: !!python/object/apply:os.system ['echo x']\n---\nText.\n"})
    assert any("wissen/boese.md: Felder nicht lesbar" in e for e in errors(root))


def test_1_free_okf_fields_are_kept(root):
    extra = "tags: [moor]\nresource: https://example.org\ngenerated: {by: gemini/test, at: 2026-10-22}\nfarbe: grün\n"
    write_wiki(root, {"pflanzen/moosbeere.md": page("Pflanze", extra=extra)})

    loaded = wiki.load(root)

    assert loaded.pages["moosbeere"].meta["farbe"] == "grün"
    assert loaded.pages["moosbeere"].meta["tags"] == ["moor"]


@pytest.mark.parametrize(
    "valar, reason",
    [
        ("farbe: grün", "valar.farbe"),
        ("occurs:\n  - {where: moor, frequency: oft}", "valar.occurs.0.frequency"),
        ("occurs:\n  - {where: Moor, frequency: common}", "valar.occurs.0.where"),
        ("item: {value_pennies: -1}", "valar.item.value_pennies"),
        ("item: {value_pennies: 1, gather_skill: Naturkunde}", "valar.item.gather_skill"),
        ("knowledge:\n  - {skill: SKILL-408, difficulty: DIFF-09, text: Giftig}", "valar.knowledge.0.difficulty"),
        ("region: testland", "erlaubt im Block valar nicht: region"),
        ("occurs:\n  - {where: sumpfland, frequency: common}", "Seite 'sumpfland' fehlt"),
        ("occurs:\n  - {where: wolf, frequency: common}", "'wolf' ist Tier"),
        ("occurs:\n  - {where: moor, frequency: common}\n  - {where: moor, frequency: rare}", "'moor' doppelt"),
        ("item: {value_pennies: 1, gather_skill: SKILL-999}", "Skill 'SKILL-999' unbekannt"),
    ],
)
def test_1_the_valar_block_is_checked_strictly(root, valar, reason):
    write_wiki(root, {"pflanzen/neu.md": page("Pflanze", valar)})

    found = errors(root)

    assert len(found) == 1 and found[0].startswith("pflanzen/neu.md: ") and reason in found[0], found


def test_1_an_unknown_type_is_a_general_page_but_takes_no_valar_block(root):
    write_wiki(root, {"wissen/lied.md": page("Lied")})
    assert errors(root) == [] and any("Typ 'Lied' unbekannt" in w for w in warnings(root))

    write_wiki(root, {"wissen/lied.md": page("Lied", "occurs:\n  - {where: moor, frequency: common}")})
    assert any("Block valar nicht erlaubt" in e for e in errors(root))


# --- 2: Links und index.md ---


def test_2_a_link_to_a_missing_page_is_reported_not_fatal(root):
    body = "[Wolf](/tiere/wolf.md), [Dorf](../orte/testdorf.md), [Fehlt](/tiere/baer.md), [Web](https://x.org)"
    write_wiki(root, {"tiere/fuchs.md": page("Tier", body=body)})
    wiki.write_indexes(root)

    report = wiki.scan(root)

    assert report.errors == []
    assert [str(w) for w in report.warnings] == ["tiere/fuchs.md: Link /tiere/baer.md führt ins Leere"]


def test_2_a_link_out_of_the_wiki_is_reported(root):
    write_wiki(root, {"tiere/fuchs.md": page("Tier", body="[raus](../../geheim.md)")})
    assert any("zeigt aus dem Wiki hinaus" in w for w in warnings(root))


def test_2_index_files_list_the_pages_and_are_kept_current(root):
    assert "* [Sumpfkraut](sumpfkraut.md)" in (root / "pflanzen" / "index.md").read_text(encoding="utf-8")
    assert "* [Pflanzen](pflanzen/)" in (root / "index.md").read_text(encoding="utf-8")

    write_wiki(root, {"pflanzen/moosbeere.md": page("Pflanze", extra="description: Rote Beere im Moor.\n")})
    assert warnings(root) == ["pflanzen/index.md: veraltet (python -m valar.wiki index)"]

    assert wiki.write_indexes(root) == ["pflanzen/index.md"]
    assert warnings(root) == []
    assert "* [moosbeere](moosbeere.md) - Rote Beere im Moor." in (root / "pflanzen/index.md").read_text(
        encoding="utf-8"
    )


# --- 3: Orte im Inhalt zeigen auf Wiki-Seiten ---


def test_3_content_locations_point_to_location_pages(root):
    loaded = wiki.load(root)

    assert loaded.check_content(content_pointing_to(boomtown="testdorf")) == []
    assert [str(p) for p in loaded.check_content(content_pointing_to(boomtown="nirgendwo", village="wolf"))] == [
        "Inhalt, Ort boomtown: Wiki-Seite 'nirgendwo' fehlt",
        "Inhalt, Ort village: Wiki-Seite 'wolf' ist kein Ort, sondern Tier",
    ]


# --- 4: Welche Seiten gehören zu einem Ort ---


def test_4_surroundings_name_place_regions_landscapes_and_what_occurs(root):
    loaded = wiki.load(root)
    content = content_pointing_to(boomtown="testdorf", crystal_field="anderswo")

    here = loaded.surroundings(content, BOOMTOWN)

    assert here.location.page_id == "testdorf"
    assert [p.page_id for p in here.regions] == ["testland", "oberland"]  # nächste zuerst
    assert [p.page_id for p in here.landscapes] == ["moor"]
    # Das genaueste Vorkommen zählt: Ort vor Landschaft vor Region. Veraltete Seiten fehlen.
    assert [(p.page_id, f) for p, f in here.occurring] == [
        ("sumpfkraut", "common"),
        ("wolf", "uncommon"),
        ("dorfkatze", "very_rare"),
    ]

    there = loaded.surroundings(content, CRYSTAL_FIELD)
    assert [(p.page_id, f) for p, f in there.occurring] == [("wolf", "uncommon")]


def test_4_a_sublocation_without_page_takes_its_parents(root):
    loaded = wiki.load(root)
    content = content_pointing_to(boomtown="testdorf")

    assert loaded.surroundings(content, TAVERN) == loaded.surroundings(content, BOOMTOWN)
    assert loaded.surroundings(content, GUILD_HALL).location.page_id == "testdorf"


def test_4_a_location_without_any_page_has_empty_surroundings(root):
    empty = wiki.load(root).surroundings(content_pointing_to(), VILLAGE)
    assert empty == wiki.Surroundings(None, (), (), ())


# --- 5: Das echte Welt-Wiki ---


def test_5_the_world_wiki_loads_cleanly_and_fits_the_content():
    report = wiki.scan(WORLD)

    assert report.errors == [], "\n".join(map(str, report.errors))
    assert report.warnings == [], "Hinweise beheben, index.md mit python -m valar.wiki index"
    assert wiki.Wiki(report.pages).check_content(CONTENT) == []


def test_5_the_first_canon_pages_exist():
    pages = wiki.load(WORLD).pages
    for page_id, type_ in [
        ("rauhenau", "Region"),
        ("sturmhagen", "Ort"),
        ("glasgrund", "Ort"),
        ("lindenheim", "Ort"),
        ("kirche-des-ersten-sturms", "Macht"),
        ("kristallmeisterzunft", "Macht"),
        ("bund-der-salz-und-kristallfahrer", "Macht"),
        ("fuersten", "Macht"),
        ("grollwetter", "Wissen"),
    ]:
        assert pages[page_id].type == type_
    surroundings = wiki.load(WORLD).surroundings(CONTENT, GUILD_HALL)
    assert surroundings.location.title == "Sturmhagen" and surroundings.regions[0].title == "Rauhenau"


def test_2_obsidian_links_from_the_wiki_root_are_found(root):
    # Obsidian schreibt Links mit „absolutem Pfad im Tresor“ ohne führenden Schrägstrich (Entscheidung 105).
    body = "[Wolf](tiere/wolf.md), [Dorf](orte/testdorf.md#lage), [Nachbar](dorfkatze.md), [Fehlt](tiere/baer.md)"
    write_wiki(root, {"tiere/fuchs.md": page("Tier", body=body)})
    wiki.write_indexes(root)

    assert warnings(root) == ["tiere/fuchs.md: Link tiere/baer.md führt ins Leere"]


def test_2_wikilinks_are_reported(root):
    write_wiki(root, {"tiere/fuchs.md": page("Tier", body="Wie der [[wolf]].")})
    wiki.write_indexes(root)

    assert warnings(root) == ["tiere/fuchs.md: Wikilink [[wolf]]: bitte als Markdown-Link schreiben"]
