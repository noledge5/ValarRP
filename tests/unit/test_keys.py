"""Unit-Tests: Schlüssel und Budget-Grenzen im Datenordner (Entscheidungen 168, 176 und 177)."""

import json

import pytest

from valar import keys, llm

KEY = "sk-geheim-12345678"


def test_environment_comes_first_and_empty_variables_count_as_missing(tmp_path):
    store = keys.KeyStore(tmp_path)
    store.set("A_KEY", KEY)
    store.set("B_KEY", "datei-schluessel-2")

    environ = store.environ({"A_KEY": "aus-der-umgebung", "B_KEY": ""})

    assert (environ.get("A_KEY"), environ.get("B_KEY"), environ.get("C_KEY", "")) == ("aus-der-umgebung", "datei-schluessel-2", "")


def test_key_state_shows_only_the_end(tmp_path):
    store = keys.KeyStore(tmp_path)
    store.set("A_KEY", KEY)

    assert keys.key_state("A_KEY", {}, store) == ("file", "5678")
    assert keys.key_state("A_KEY", {"A_KEY": "x"}, store) == ("env", None)
    assert keys.key_state("B_KEY", {}, store) == ("missing", None)


def test_invalid_entries_in_the_file_are_ignored(tmp_path):
    (tmp_path / keys.KEY_FILE).write_text(json.dumps({"A_KEY": KEY, "kleiner_name": KEY, "B_KEY": "kurz", "C_KEY": 5}))

    assert keys.KeyStore(tmp_path).read() == {"A_KEY": KEY}


@pytest.mark.parametrize("name", ["", "a_key", "A-KEY", "1KEY"])
def test_a_wrong_variable_name_is_refused(tmp_path, name):
    with pytest.raises(keys.KeyInputError):
        keys.KeyStore(tmp_path).set(name, KEY)


def test_deleting_a_missing_key_writes_nothing(tmp_path):
    keys.KeyStore(tmp_path).delete("A_KEY")

    assert list(tmp_path.iterdir()) == []


def test_budget_from_the_web_overrides_the_file(tmp_path):
    settings = llm.ModelSettings(
        provider=(llm.ProviderSettings(name="frei", base_url="https://frei.example/v1", model="m", key_env="FREI_KEY"),)
    )
    assert keys.effective_settings(settings, tmp_path) is settings

    keys.save_budget(tmp_path, llm.BudgetSettings(warn_eur=1, stop_eur=2))

    assert keys.effective_settings(settings, tmp_path).budget == llm.BudgetSettings(warn_eur=1, stop_eur=2)
    (tmp_path / keys.BUDGET_FILE).write_text('{"warn_eur": 5, "stop_eur": 1}')  # Unsinn von Hand: gilt nicht
    assert keys.effective_settings(settings, tmp_path).budget == settings.budget
