from pathlib import Path

import pytest

from tests.helpers import TEST_WIKI, page, write_wiki
from valar import wiki


@pytest.fixture
def root(tmp_path: Path) -> Path:
    base = tmp_path / "welt"
    write_wiki(base, TEST_WIKI)
    wiki.write_indexes(base)
    return base


def messages(problems) -> list[str]:
    return [str(p) for p in problems]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("---\ntype: A\n---\nText\n", ("type: A", "Text\n")),
        ("﻿---\r\ntype: A\r\n---\r\nText\r\n", ("type: A", "Text\n")),
        ("---\ntype: A\n---\n", ("type: A", "")),
        ("---\ntype: A\n---", ("type: A", "")),
        ("type: A\n---\nText\n", None),
        ("---\ntype: A\n", None),
    ],
)
def test_split_frontmatter(text, expected):
    assert wiki.split_frontmatter(text) == expected


def test_title_defaults_to_the_id_and_status_to_stable(root):
    loaded = wiki.load(root).pages["wolf"]
    assert (loaded.title, loaded.status, loaded.description) == ("wolf", "stable", None)


def test_unknown_status_is_a_warning(root):
    write_wiki(root, {"tiere/wolf.md": page("Tier", extra="status: fertig\n")})
    report = wiki.scan(root)
    assert report.errors == [] and report.pages["wolf"].status == "stable"
    assert messages(report.warnings) == ["tiere/wolf.md: status 'fertig' unbekannt, gilt als stable"]


def test_ids_are_unique_across_folders(root):
    write_wiki(root, {"wissen/wolf.md": page("Wissen")})
    assert messages(wiki.scan(root).errors) == ["wissen/wolf.md: ID 'wolf' gibt es schon in tiere/wolf.md"]


@pytest.mark.parametrize("name", ["Wolf.md", "bär.md", "grauer_wolf.md", "-wolf.md"])
def test_file_names_are_plain_ids(root, name):
    write_wiki(root, {f"tiere/{name}": page("Tier")})
    assert any("Dateiname nur aus a-z" in e for e in messages(wiki.scan(root).errors))


def test_regions_must_not_form_a_circle(root):
    write_wiki(root, {"regionen/oberland.md": page("Region", "parent: testland")})
    assert any("Kreis bei den Oberregionen" in e for e in messages(wiki.scan(root).errors))


def test_wealth_and_rule_of_a_place_are_checked(root):
    """Entscheidung 203: Wohlstand 1 bis 5, Herrschaft zeigt auf eine Macht."""
    write_wiki(root, {"orte/anderswo.md": page("Ort", "region: oberland\nwealth: 6\nruled_by: testmacht")})
    assert any("valar.wealth" in e for e in messages(wiki.scan(root).errors))
    write_wiki(root, {"orte/anderswo.md": page("Ort", "region: oberland\nwealth: 3\nruled_by: graf-test")})
    assert messages(wiki.scan(root).errors) == ["orte/anderswo.md: valar.ruled_by: 'graf-test' ist Person, erwartet Macht"]


def test_missing_headings_and_place_fields_are_only_warnings(root):
    write_wiki(root, {"orte/anderswo.md": page("Ort", "region: oberland"), "npc-typen/koehler.md": page("NPC-Typ")})
    report = wiki.scan(root)
    assert report.errors == []
    assert {m for m in messages(report.warnings) if "fehlen" in m} == {
        "orte/anderswo.md: Überschriften fehlen: ## Stimmung, ## Leute, ## Konflikte",
        "orte/anderswo.md: Felder fehlen: valar.wealth, valar.ruled_by",
        "npc-typen/koehler.md: Überschriften fehlen: ## Stimme, ## Will, ## Fürchtet",
    }


def test_location_fields_must_point_to_the_right_types(root):
    write_wiki(root, {"orte/testdorf.md": page("Ort", "region: moor\nlandscapes: [testland]")})
    assert messages(wiki.scan(root).errors) == [
        "orte/testdorf.md: valar.region: 'moor' ist Landschaft, erwartet Region",
        "orte/testdorf.md: valar.landscapes: 'testland' ist Region, erwartet Landschaft",
    ]


def test_big_or_broken_files_are_errors(root):
    (root / "tiere" / "riese.md").write_text(page("Tier", body="x" * wiki.MAX_PAGE_BYTES), encoding="utf-8")
    (root / "tiere" / "kaputt.md").write_bytes(b"---\ntype: Tier\n---\n\xff\xfe\n")
    assert messages(wiki.scan(root).errors) == [
        "tiere/kaputt.md: kein UTF-8",
        "tiere/riese.md: größer als 100 kB",
    ]


def test_symlinks_are_skipped(root, tmp_path):
    outside = tmp_path / "draussen.md"
    outside.write_text(page("Tier"), encoding="utf-8")
    (root / "tiere" / "link.md").symlink_to(outside)
    report = wiki.scan(root)
    assert "link" not in report.pages and "tiere/link.md: Verknüpfung übersprungen" in messages(report.warnings)


def test_log_headings_must_be_dates(root):
    (root / "log.md").write_text("# Log\n\n## 2026-10-22\n* gut\n\n## gestern\n* schlecht\n", encoding="utf-8")
    assert messages(wiki.scan(root).warnings) == ["log.md: Überschrift '## gestern' ist kein Datum JJJJ-MM-TT"]


def test_missing_folder_is_an_error(tmp_path):
    assert messages(wiki.scan(tmp_path / "fehlt").errors) == [f"{tmp_path / 'fehlt'}: Ordner fehlt"]


def test_root_index_declares_the_okf_version(root):
    assert (root / "index.md").read_text(encoding="utf-8").startswith('---\nokf_version: "0.2"\n---\n')


def test_index_groups_pages_by_type(root):
    write_wiki(root, {"tiere/fuchs.md": page("Wissen", extra="title: Fuchsjagd\n")})
    wiki.write_indexes(root)
    text = (root / "tiere" / "index.md").read_text(encoding="utf-8")
    assert text.index("## Tier") < text.index("* [wolf](wolf.md)") < text.index("## Wissen") < text.index("Fuchsjagd")


# --- Übergaben einlesen ---

HANDOFF = """Hier sind die Seiten:

=== welt/pflanzen/moosbeere.md ===
```markdown
---
type: Pflanze
title: Moosbeere
valar:
  occurs:
    - {where: moor, frequency: common}
---
Rote Beere.
```

=== tiere/fuchs.md ===
---
type: Tier
---
Schlau.
"""


def test_import_writes_pages_and_strips_code_fences(root):
    written = wiki.import_handoff(HANDOFF, root)

    assert written == ["pflanzen/moosbeere.md", "tiere/fuchs.md"]
    assert (root / "pflanzen" / "moosbeere.md").read_text(encoding="utf-8").startswith("---\ntype: Pflanze\n")
    assert (root / "pflanzen" / "moosbeere.md").read_text(encoding="utf-8").endswith("Rote Beere.\n")
    assert wiki.load(root).pages["moosbeere"].game.occurs[0].frequency == "common"


@pytest.mark.parametrize(
    "name",
    ["../draussen.md", "/etc/passwd.md", "tiere/../../draussen.md", "tiere/Fuchs.md", "tiere/fuchs.txt",
     "index.md", "tiere/log.md", "tiere//fuchs.md", "tiere/fuchs.md/"],
)
def test_import_refuses_unsafe_paths(root, name):
    with pytest.raises(ValueError, match="nicht erlaubt"):
        wiki.split_handoff(f"=== {name} ===\n{page('Tier')}", root)


def test_import_never_overwrites_and_then_writes_nothing(root):
    text = f"=== tiere/fuchs.md ===\n{page('Tier')}\n=== tiere/wolf.md ===\n{page('Tier')}"
    before = (root / "tiere" / "wolf.md").read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="gibt es schon: tiere/wolf.md"):
        wiki.import_handoff(text, root)

    assert not (root / "tiere" / "fuchs.md").exists()
    assert (root / "tiere" / "wolf.md").read_text(encoding="utf-8") == before


def test_import_refuses_the_same_path_twice_and_empty_text(root):
    with pytest.raises(ValueError, match="nicht erlaubt"):
        wiki.split_handoff(f"=== tiere/fuchs.md ===\n{page('Tier')}=== tiere/fuchs.md ===\n{page('Tier')}", root)
    with pytest.raises(ValueError, match="keine Seite gefunden"):
        wiki.split_handoff("Nur Gerede.", root)


# --- Kommandozeile ---


def test_cli_check_index_and_import(root, tmp_path, capsys):
    assert wiki.main(["check", str(root)]) == 0
    assert "13 Seiten, davon 0 Entwurf; 0 Fehler, 0 Hinweise." in capsys.readouterr().out

    source = tmp_path / "uebergabe.txt"
    source.write_text(HANDOFF, encoding="utf-8")
    assert wiki.main(["import", str(source), str(root)]) == 0
    out = capsys.readouterr().out
    assert "angelegt: pflanzen/moosbeere.md" in out and "0 Fehler, 0 Hinweise." in out

    assert wiki.main(["import", str(source), str(root)]) == 1
    assert "Nichts geschrieben: gibt es schon" in capsys.readouterr().out

    write_wiki(root, {"tiere/kaputt.md": "kein Kopf\n"})
    assert wiki.main(["check", str(root)]) == 1
    assert "Fehler  tiere/kaputt.md: keine Felder" in capsys.readouterr().out

    assert wiki.main(["index", str(root)]) == 1  # Index geschrieben, der Fehler bleibt
    assert wiki.main(["unsinn"]) == 2


def test_obsidian_settings_keep_links_in_okf_form():
    import json

    settings = json.loads((Path(__file__).parents[2] / "welt" / ".obsidian" / "app.json").read_text(encoding="utf-8"))
    assert settings["useMarkdownLinks"] is True and settings["newLinkFormat"] == "absolute"


def test_hidden_obsidian_folder_is_not_read(root):
    (root / ".obsidian").mkdir()
    (root / ".obsidian" / "notiz.md").write_text("kein Kopf\n", encoding="utf-8")
    assert wiki.scan(root).errors == []
