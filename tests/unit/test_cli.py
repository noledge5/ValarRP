import pytest

from valar import cli


@pytest.mark.parametrize(
    "pennies, text",
    [
        (0, "0 Pfennig"),
        (7, "7 Pfennig"),
        (30, "3 Groschen"),
        (100, "1 Gulden"),
        (130, "1 Gulden, 3 Groschen"),
        (1234, "12 Gulden, 3 Groschen, 4 Pfennig"),
        (105, "1 Gulden, 5 Pfennig"),
    ],
)
def test_format_money_in_gulden_groschen_pfennig(pennies, text):  # Entscheidung 32
    assert cli.format_money(pennies) == text


@pytest.mark.parametrize("meters, text", [(0, "0 m"), (999, "999 m"), (1000, "1,0 km"), (15962, "16,0 km")])
def test_format_distance(meters, text):
    assert cli.format_distance(meters) == text


@pytest.mark.parametrize(
    "word, difficulty",
    [("1", "DIFF-01"), ("7", "DIFF-07"), ("diff-03", "DIFF-03"), ("Schwer", "DIFF-04"), ("legendär", "DIFF-07")],
)
def test_difficulty_words(word, difficulty):
    assert cli._difficulty(word) == difficulty


@pytest.mark.parametrize("word", ["0", "8", "DIFF-08", "unmöglich", ""])
def test_unknown_difficulty_is_an_input_error(word):
    with pytest.raises(cli.InputError):
        cli._difficulty(word)


def test_every_difficulty_has_a_name():
    assert set(cli.DIFFICULTY_NAMES) == set(cli.rules.DIFFICULTIES)
    assert set(cli.DIFFICULTY_WORDS.values()) == set(cli.rules.DIFFICULTIES)


def test_find_ignores_case_spaces_and_dashes():
    names = {"SKILL-202": "Schlösser knacken"}
    assert cli._find("schlösser-knacken", names, "Skill") == "SKILL-202"
    assert cli._find("skill-202", names, "Skill") == "SKILL-202"
    with pytest.raises(cli.InputError):
        cli._find("Schloss", names, "Skill")  # kein Raten bei Teilwörtern


def test_plain_turns_markdown_links_into_text():
    assert cli.plain("Am [Glasgrund](/orte/glasgrund.md) und [hier](x.md#a).") == "Am Glasgrund und hier."
