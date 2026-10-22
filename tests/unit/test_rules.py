from typing import get_args

import pytest

from valar import rules
from valar.models import AttemptDuration, DifficultyId, RouteKind


@pytest.mark.parametrize(
    "value, modifier", [(1, -5), (8, -1), (9, -1), (10, 0), (11, 0), (12, 1), (14, 2), (15, 2), (20, 5)]
)
def test_attribute_modifier_rounds_down(value, modifier):  # MECH-08
    assert rules.attribute_modifier(value) == modifier


@pytest.mark.parametrize("value, bonus", [(0, 0), (9, 0), (10, 1), (18, 1), (20, 2), (30, 3), (100, 10)])
def test_skill_bonus_rounds_down(value, bonus):  # MECH-02
    assert rules.skill_bonus(value) == bonus


def test_check_bonus_uses_the_better_lead_attribute():  # MECH-09
    # Klingenwaffen: STR / GES. Beispiel aus REGELN.md: Skill 30 (+3) und Attribut 14 (+2) = +5.
    assert rules.check_bonus("SKILL-101", 30, {"STR": 14, "GES": 8}) == 5
    assert rules.check_bonus("SKILL-101", 30, {"STR": 8, "GES": 14}) == 5
    assert rules.check_bonus("SKILL-101", 30, {"STR": 8, "GES": 8}) == 2  # beide -1, der höhere zählt
    assert rules.check_bonus("SKILL-102", 0, {"STR": 8, "GES": 18}) == -1  # Wuchtwaffen: nur STR


def test_skills_use_known_attributes_and_skip_the_gift_skill():
    assert len(rules.SKILLS) == 30
    for skill in rules.SKILLS.values():
        assert skill.attributes and set(skill.attributes) <= set(rules.ATTRIBUTES)
    assert "SKILL-501" not in rules.SKILLS


@pytest.mark.parametrize(
    "difficulty, dc, ticks",
    [
        ("DIFF-01", 8, 1),
        ("DIFF-02", 10, 1),
        ("DIFF-03", 12, 2),
        ("DIFF-04", 14, 3),
        ("DIFF-05", 16, 4),
        ("DIFF-06", 20, 6),
        ("DIFF-07", 24, 8),
    ],
)
def test_difficulty_table_matches_the_rules(difficulty, dc, ticks):
    assert rules.DIFFICULTIES[difficulty] == rules.Difficulty(dc, ticks)


def test_difficulty_table_covers_exactly_the_model_literal():
    assert set(rules.DIFFICULTIES) == set(get_args(DifficultyId))


@pytest.mark.parametrize("bonus, ticks", [(0, 2), (6, 2), (7, 0), (8, 0)])
def test_no_ticks_when_at_most_a_5_was_needed(bonus, ticks):
    # DIFF-03 hat SG 12: bei Bonus 7 reicht eine 5 (Routine), bei Bonus 6 braucht es eine 6.
    assert rules.ticks_on_success("DIFF-03", bonus) == ticks


@pytest.mark.parametrize(
    "die, total, dc, expected",
    [
        (20, 5, 12, "critical_success"),  # natürliche 20 geht vor, auch bei kleiner Summe
        (1, 30, 12, "fumble"),  # natürliche 1 geht vor, auch bei großer Summe
        (7, 12, 12, "success"),
        (15, 25, 12, "success"),
        (6, 11, 12, "near_miss"),
        (5, 10, 12, "near_miss"),
        (4, 9, 12, "failure"),
        (2, 3, 12, "failure"),
    ],
)
def test_outcome(die, total, dc, expected):
    assert rules.outcome(die, total, dc) == expected


@pytest.mark.parametrize(
    "target, cost",
    [(1, 3), (20, 3), (21, 5), (40, 5), (41, 8), (60, 8), (61, 12), (80, 12), (81, 20), (100, 20)],
)
def test_ticks_per_point_by_band_of_the_target_value(target, cost):
    assert rules.ticks_per_point(target) == cost


@pytest.mark.parametrize("target", [0, -1, 101])
def test_ticks_per_point_refuses_values_outside_the_scale(target):
    with pytest.raises(ValueError):
        rules.ticks_per_point(target)


def test_from_0_to_100_takes_960_ticks():  # REGELN.md: "rund 960 Ticks"
    assert sum(rules.ticks_per_point(v) for v in range(1, 101)) == 960


@pytest.mark.parametrize(
    "start, gained, expected",
    [
        ((0, 0), 2, (0, 2, 0)),  # noch kein Punkt
        ((0, 2), 1, (1, 0, 1)),  # genau 3 Ticks
        ((19, 0), 4, (20, 1, 1)),  # Rest bleibt
        ((0, 0), 8, (2, 2, 2)),  # zwei Aufstiege auf einmal
        ((20, 0), 8, (21, 3, 1)),  # Skill 21 kostet 5 Ticks (Band 21-40)
        ((99, 15), 8, (100, 0, 1)),  # Skill 100 erreicht, Ticks verfallen
        ((100, 0), 8, (100, 0, 0)),  # am Ende der Skala
        ((10, 1), 0, (10, 1, 0)),
    ],
)
def test_add_ticks(start, gained, expected):
    assert rules.add_ticks(*start, gained) == expected


@pytest.mark.parametrize("ups, level", [(0, 1), (9, 1), (10, 2), (19, 2), (20, 3), (100, 11)])
def test_level_from_skill_ups(ups, level):
    assert rules.level(ups) == level


# --- Zeit und Szene ---


def test_attempt_durations_are_fixed_by_the_code():  # Entscheidung 59
    assert rules.ATTEMPT_MINUTES == {"moment": 5, "hour": 60, "half_day": 360}
    assert set(rules.ATTEMPT_MINUTES) == set(get_args(AttemptDuration))


@pytest.mark.parametrize(
    "game_minutes, start, day, minute",
    [
        (0, 480, 1, 480),  # Spielbeginn 8:00
        (959, 480, 1, 1439),  # 23:59
        (960, 480, 2, 0),  # Mitternacht: Tag 2
        (960 + 1440, 480, 3, 0),
        (0, 0, 1, 0),  # Spielbeginn um Mitternacht
        (1439, 0, 1, 1439),
    ],
)
def test_clock_derives_day_and_time_from_game_minutes(game_minutes, start, day, minute):
    assert rules.clock(game_minutes, start) == (day, minute)


@pytest.mark.parametrize(
    "game_minutes, start, midnight",
    [
        (0, 480, -480),  # vor der ersten Mitternacht: die vor Spielbeginn
        (959, 480, -480),
        (960, 480, 960),  # genau Mitternacht zählt schon
        (2000, 480, 960),
        (2400, 480, 2400),
        (0, 0, 0),
    ],
)
def test_last_midnight(game_minutes, start, midnight):
    assert rules.last_midnight(game_minutes, start) == midnight


@pytest.mark.parametrize(
    "arrived_at, game_minutes, scene",
    [
        (0, 0, 0),
        (0, 959, 0),  # dieselbe Szene bis kurz vor Mitternacht
        (0, 960, 960),  # Mitternacht beginnt eine neue
        (100, 500, 100),  # Ankunft beginnt eine neue
        (1000, 1100, 1000),  # Ankunft nach Mitternacht: die Ankunft gilt
        (900, 1100, 960),  # Ankunft vor Mitternacht: die Mitternacht gilt
        (960, 960, 960),  # beides zugleich: eine Szene
    ],
)
def test_scene_starts_at_the_later_of_arrival_and_midnight(arrived_at, game_minutes, scene):
    assert rules.scene_start(arrived_at, game_minutes, 480) == scene


def test_no_ticks_when_the_skill_already_ticked_in_this_scene():  # Entscheidung 58
    assert rules.ticks_on_success("DIFF-04", 0) == 3
    assert rules.ticks_on_success("DIFF-04", 0, ticked_this_scene=True) == 0
    assert rules.ticks_on_success("DIFF-01", 5, ticked_this_scene=False) == 0  # Routine bleibt Routine


# --- Raum ---


@pytest.mark.parametrize(
    "points, length",
    [([(0, 0), (3, 4)], 5), ([(0, 0), (3, 4), (3, 10)], 11), ([(0, 0), (1, 1)], 1), ([(5, 5), (5, 5)], 0)],
)
def test_path_length_rounds_the_sum_to_meters(points, length):
    assert rules.path_length(points) == length


@pytest.mark.parametrize(
    "length, kind, minutes",
    [
        (5000, "road", 60),
        (5001, "road", 61),  # angefangene Minute zählt
        (4000, "path", 60),
        (2500, "rough", 60),
        (0, "road", 1),  # mindestens eine Minute
        (10, "rough", 1),
    ],
)
def test_travel_minutes_round_up(length, kind, minutes):
    assert rules.travel_minutes(length, kind) == minutes


def test_every_route_kind_has_a_pace():
    assert set(rules.TRAVEL_METERS_PER_HOUR) == set(get_args(RouteKind))


@pytest.mark.parametrize(
    "target, direction",
    [
        ((0, 10), "N"), ((10, 10), "NO"), ((10, 0), "O"), ((10, -10), "SO"),
        ((0, -10), "S"), ((-10, -10), "SW"), ((-10, 0), "W"), ((-10, 10), "NW"),
        ((3, 10), "N"), ((5, 10), "NO"),  # 16,7° und 26,6°: Grenze bei 22,5°
        ((0, 0), None),
    ],
)
def test_compass_uses_eight_directions(target, direction):
    assert rules.compass((0, 0), target) == direction


# --- Zufall, Untersuchen, Sammeln (Schritt 1e) ---


def test_turn_rng_is_fixed_by_seed_and_revision():
    first = [rules.turn_rng(7, 3).randint(1, 100) for _ in range(3)]
    assert first == [rules.turn_rng(7, 3).randint(1, 100) for _ in range(3)]
    assert rules.turn_rng(7, 3).random() != rules.turn_rng(7, 4).random()
    assert rules.turn_rng(7, 3).random() != rules.turn_rng(8, 3).random()


@pytest.mark.parametrize("bonus, dc, seen", [(0, 10, True), (0, 11, False), (4, 14, True), (-1, 10, False)])
def test_passive_knowledge_is_ten_plus_bonus(bonus, dc, seen):
    assert rules.passive_known(bonus, dc) is seen


def test_gathering_gets_harder_and_smaller_with_rarity():
    order = ["common", "uncommon", "rare", "very_rare"]
    dcs = [rules.DIFFICULTIES[rules.GATHER[f][0]].dc for f in order]
    most = [rules.GATHER[f][1] for f in order]
    assert dcs == sorted(dcs) and most == sorted(most, reverse=True) and min(most) >= 1


# --- Charaktererschaffung (CHAR) ---


def test_attribute_points_spent_counts_above_eight():
    assert rules.attribute_points_spent({"STR": 8, "GES": 15, "KON": 9}) == 8


def test_start_skills_main_thirty_side_fifteen():
    assert rules.start_skills(("A", "B"), ("C",)) == {"A": 30, "B": 30, "C": 15}


def test_attribute_names_and_ids_match_the_table():
    from typing import get_args

    from valar.models import AttributeId

    assert tuple(rules.ATTRIBUTE_NAMES) == rules.ATTRIBUTES == get_args(AttributeId)
    assert not set(rules.LOCKED_SKILLS) & set(rules.SKILLS)
