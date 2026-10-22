"""Schritt 1c2: ein Koordinatensystem für alles, Reisezeit aus der Karte, Karte als GeoJSON.

Ausgangslage wie in 1c: die Figur im Zunfthaus, Testgebiet nach Entscheidung 74 mit Punkten in Metern.
"""

import json
from pathlib import Path

import pytest

from tests.helpers import CAMPAIGN, PLAYER, campaign_row, dump_state, move
from valar import geo, queries
from valar.content import BOOMTOWN, CONTENT, CRYSTAL_FIELD, GUILD_HALL, TAVERN, VILLAGE, Content, Route
from valar.models import AdvanceTime

GEOJSON_FILE = Path(__file__).parents[1] / "docs" / "testgebiet.geojson"


def content_with_routes(*routes: Route) -> Content:
    keep = [r for r in CONTENT.routes if frozenset((r.a, r.b)) not in {frozenset((n.a, n.b)) for n in routes}]
    return Content(**(dict(CONTENT) | {"routes": (*keep, *routes)}))


# --- 1: Jeder Ort hat einen Punkt ---


def test_1_every_location_has_its_own_point():
    points = {location_id: location.point for location_id, location in CONTENT.locations.items()}
    assert points[BOOMTOWN] == (20_000, 20_000)
    assert points[GUILD_HALL] != points[BOOMTOWN]  # auch ein Unterort hat einen eigenen Punkt
    assert len(set(points.values())) == len(points)


# --- 2: Reisezeit aus Weglänge und Wegart ---


def test_2_travel_time_comes_from_length_and_kind(conn, roll_scenario):
    route = CONTENT.route_between(BOOMTOWN, CRYSTAL_FIELD)
    assert (route.kind, CONTENT.route_length(route), CONTENT.route_time(route)) == ("road", 7_500, 90)

    move(conn, "req-2a", 0, destination_id=BOOMTOWN)
    result = move(conn, "req-2b", 1, destination_id=CRYSTAL_FIELD)

    assert result.effects[0] == AdvanceTime(minutes=90)
    assert campaign_row(conn)["game_minutes"] == 5 + 90


@pytest.mark.parametrize("kind, minutes", [("road", 90), ("path", 113), ("rough", 180)])
def test_2b_the_kind_of_way_sets_the_pace(conn, roll_scenario, kind, minutes):
    content = content_with_routes(Route(a=BOOMTOWN, b=CRYSTAL_FIELD, kind=kind))
    move(conn, "req-a", 0, destination_id=BOOMTOWN, content=content)

    result = move(conn, "req-b", 1, destination_id=CRYSTAL_FIELD, content=content)

    assert result.effects[0] == AdvanceTime(minutes=minutes)


# --- 3: Feste Reisezeit überschreibt die Rechnung ---


def test_3_fixed_minutes_override_the_calculation(conn, roll_scenario):
    content = content_with_routes(Route(a=BOOMTOWN, b=CRYSTAL_FIELD, minutes=600))  # z. B. gesperrter Pass
    move(conn, "req-a", 0, destination_id=BOOMTOWN, content=content)

    result = move(conn, "req-b", 1, destination_id=CRYSTAL_FIELD, content=content)

    assert result.effects[0] == AdvanceTime(minutes=600)


# --- 4: Zwischenpunkte ---


def test_4_waypoints_make_the_way_longer(conn, roll_scenario):
    # Umweg über (20 000, 26 000): 6 000 m nach Norden, dann 4 500 m nach Osten.
    content = content_with_routes(Route(a=BOOMTOWN, b=CRYSTAL_FIELD, waypoints=((20_000, 26_000),)))
    route = content.route_between(BOOMTOWN, CRYSTAL_FIELD)
    assert content.route_length(route) == 10_500
    move(conn, "req-a", 0, destination_id=BOOMTOWN, content=content)

    result = move(conn, "req-b", 1, destination_id=CRYSTAL_FIELD, content=content)

    assert result.effects[0] == AdvanceTime(minutes=126)  # 10,5 km bei 5 km/h


# --- 5: Umsehen zeigt Richtung und Entfernung ---


def test_5_look_shows_direction_and_distance_without_a_turn(conn, roll_scenario):
    move(conn, "req-a", 0, destination_id=BOOMTOWN)
    before = dump_state(conn)

    seen = queries.look(conn, campaign_id=CAMPAIGN, player_id=PLAYER)

    assert dump_state(conn) == before
    assert (seen.x, seen.y) == (20_000, 20_000)
    assert [(e.location_id, e.direction, e.distance_m, e.minutes) for e in seen.exits] == [
        (GUILD_HALL, "S", 400, 5),
        (TAVERN, "NO", 391, 5),
        (CRYSTAL_FIELD, "NO", 7_500, 90),
        (VILLAGE, "O", 15_962, 240),
    ]


# --- 6: Karte als GeoJSON, hin und zurück ---


def test_6_geojson_round_trip_keeps_the_content():
    data = json.loads(json.dumps(geo.to_geojson()))  # wie aus einer Datei
    assert geo.from_geojson(data) == CONTENT
    kinds = [f["geometry"]["type"] for f in data["features"]]
    assert kinds.count("Point") == len(CONTENT.locations)
    assert kinds.count("LineString") == len(CONTENT.routes)


def test_6b_a_moved_location_changes_the_travel_time():
    data = geo.to_geojson()
    for feature in data["features"]:
        if feature["properties"].get("location_id") == CRYSTAL_FIELD:
            feature["geometry"]["coordinates"] = geo.to_lonlat(20_000, 30_000)  # 10 km nördlich

    updated = geo.from_geojson(data)

    assert updated.locations[CRYSTAL_FIELD].point == (20_000, 30_000)
    assert updated.route_minutes(BOOMTOWN, CRYSTAL_FIELD) == 120
    assert geo.changes(CONTENT, updated)[0].startswith("crystal_field: x=20000, y=30000")


@pytest.mark.parametrize(
    "change",
    [
        lambda f: f["properties"].update(location_id="atlantis") if f["geometry"]["type"] == "Point" else None,
        lambda f: f["properties"].update(b="atlantis") if f["geometry"]["type"] == "LineString" else None,
        lambda f: f["properties"].update(kind="flight") if f["geometry"]["type"] == "LineString" else None,
        lambda f: f["geometry"].update(coordinates=[50.0, 0.0]) if f["geometry"]["type"] == "Point" else None,
    ],
)
def test_6c_invalid_map_data_is_refused(change):
    data = geo.to_geojson()
    for feature in data["features"]:
        change(feature)
    with pytest.raises(ValueError):
        geo.from_geojson(data)


# --- 7: Die Karte im Repo ist aktuell ---


def test_7_docs_map_matches_the_content():
    assert json.loads(GEOJSON_FILE.read_text(encoding="utf-8")) == geo.to_geojson(), (
        "docs/testgebiet.geojson neu erzeugen: python -m valar.geo export > docs/testgebiet.geojson"
    )
