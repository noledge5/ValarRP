import json

import pytest

from valar import geo
from valar.content import CONTENT, MAP_LIMIT


@pytest.mark.parametrize("x, y", [(0, 0), (1, -1), (20_000, 19_600), (MAP_LIMIT, -MAP_LIMIT), (-123_457, 987_654)])
def test_meters_survive_the_way_through_degrees(x, y):
    assert geo.to_xy(geo.to_lonlat(x, y)) == (x, y)


def test_export_lies_near_the_equator_and_names_everything():
    data = geo.to_geojson()
    for feature in data["features"]:
        coordinates = feature["geometry"]["coordinates"]
        points = [coordinates] if feature["geometry"]["type"] == "Point" else coordinates
        assert all(abs(lon) < 10 and abs(lat) < 10 for lon, lat in points)
    route = next(f for f in data["features"] if f["properties"]["feature"] == "route")
    assert set(route["properties"]) == {"feature", "a", "b", "kind", "length_m", "minutes", "fixed_minutes"}


def test_a_line_drawn_backwards_is_turned_around():
    data = geo.to_geojson()
    for feature in data["features"]:
        if feature["properties"].get("feature") == "route":
            feature["properties"]["a"], feature["properties"]["b"] = feature["properties"]["b"], feature["properties"]["a"]
            feature["geometry"]["coordinates"].reverse()
    assert geo.from_geojson(data) == CONTENT


def test_waypoints_and_fixed_minutes_come_back():
    data = geo.to_geojson()
    route = next(f for f in data["features"] if f["properties"].get("b") == "crystal_field")
    route["geometry"]["coordinates"].insert(1, geo.to_lonlat(20_000, 26_000))
    route["properties"].update(kind="path", fixed_minutes=200)

    updated = geo.from_geojson(data)

    changed = updated.route_between("boomtown", "crystal_field")
    assert (changed.waypoints, changed.kind, changed.minutes) == (((20_000, 26_000),), "path", 200)
    assert len(geo.changes(CONTENT, updated)) == 1


@pytest.mark.parametrize(
    "data",
    [
        {"type": "Feature"},
        {"type": "FeatureCollection", "features": [{"geometry": {"type": "Polygon"}, "properties": {}}]},
        {"type": "FeatureCollection", "features": [
            {"geometry": {"type": "LineString", "coordinates": [[0, 0]]},
             "properties": {"feature": "route", "a": "boomtown", "b": "guild_hall"}}]},
    ],
)
def test_malformed_files_are_refused(data):
    with pytest.raises(ValueError):
        geo.from_geojson(data)


def test_command_line(tmp_path, capsys):
    assert geo.main(["export"]) == 0
    exported = capsys.readouterr().out
    assert json.loads(exported) == geo.to_geojson()

    path = tmp_path / "karte.geojson"
    path.write_text(exported, encoding="utf-8")
    assert geo.main(["check", str(path)]) == 0
    assert capsys.readouterr().out.strip() == "Keine Änderungen."

    assert geo.main([]) == 2
