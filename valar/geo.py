"""Karte als GeoJSON (Entscheidung 77): Export für Kartenwerkzeuge und Rückübernahme bearbeiteter Lagen.

Die Karte ist eben (x nach Osten, y nach Norden, Meter). Für GeoJSON legt der Export sie an den Nullpunkt
der Erde: 1 Grad entspricht 111 319,49 Metern. Nahe dem Äquator ist die Verzerrung vernachlässigbar, und
GitHub, geojson.io, QGIS oder uMap zeigen die Datei ohne Einrichtung.

Aufruf:
    python -m valar.geo export            # GeoJSON des Testinhalts auf die Standardausgabe
    python -m valar.geo check DATEI       # bearbeitete Datei prüfen und Änderungen auflisten
"""

import json
import sys
from typing import Any

from valar.content import CONTENT, Content, LocationDef, Route

METERS_PER_DEGREE = 111_319.49  # Äquatorumfang / 360
DECIMALS = 7  # etwa 1 cm; die Meter überstehen den Weg hin und zurück


def to_lonlat(x: int, y: int) -> list[float]:
    return [round(x / METERS_PER_DEGREE, DECIMALS), round(y / METERS_PER_DEGREE, DECIMALS)]


def to_xy(position: list[float]) -> tuple[int, int]:
    lon, lat = position[0], position[1]
    return round(lon * METERS_PER_DEGREE), round(lat * METERS_PER_DEGREE)


def to_geojson(content: Content = CONTENT) -> dict[str, Any]:
    """Orte als Punkte, Verbindungen als Linien. Die Eigenschaften nennen IDs, Wegart und Reisezeit."""
    features: list[dict[str, Any]] = []
    for location in content.locations.values():
        features.append(
            {
                "type": "Feature",
                "id": location.location_id,
                "geometry": {"type": "Point", "coordinates": to_lonlat(location.x, location.y)},
                "properties": {
                    "feature": "location",
                    "location_id": location.location_id,
                    "name": location.name,
                    "parent_id": location.parent_id,
                    "x": location.x,
                    "y": location.y,
                },
            }
        )
    for route in content.routes:
        features.append(
            {
                "type": "Feature",
                "id": f"{route.a}--{route.b}",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [to_lonlat(x, y) for x, y in content.route_points(route)],
                },
                "properties": {
                    "feature": "route",
                    "a": route.a,
                    "b": route.b,
                    "kind": route.kind,
                    "length_m": content.route_length(route),
                    "minutes": content.route_time(route),
                    "fixed_minutes": route.minutes,
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def from_geojson(data: dict[str, Any], content: Content = CONTENT) -> Content:
    """Übernimmt aus einer bearbeiteten Datei Lage der Orte, Zwischenpunkte, Wegart und feste Reisezeiten.

    Orte und Verbindungen müssen schon im Inhalt stehen; Namen und Beschreibungen bleiben unberührt.
    Das Ergebnis wird wie jeder Inhalt geprüft. Fehler: ValueError.
    """
    if data.get("type") != "FeatureCollection":
        raise ValueError("Erwartet eine FeatureCollection")
    locations = dict(content.locations)
    routes = {frozenset((r.a, r.b)): r for r in content.routes}
    for feature in data.get("features", []):
        properties = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        match properties.get("feature"), geometry.get("type"):
            case "location", "Point":
                location_id = properties.get("location_id")
                if location_id not in locations:
                    raise ValueError(f"Unbekannter Ort {location_id!r}")
                x, y = to_xy(geometry["coordinates"])
                # model_validate statt model_copy: Auch der neue Punkt wird geprüft.
                locations[location_id] = LocationDef.model_validate(
                    locations[location_id].model_dump() | {"x": x, "y": y}
                )
            case "route", "LineString":
                key = frozenset((properties.get("a"), properties.get("b")))
                if key not in routes:
                    raise ValueError(f"Unbekannte Verbindung {properties.get('a')!r} – {properties.get('b')!r}")
                points = [to_xy(p) for p in geometry["coordinates"]]
                if len(points) < 2:
                    raise ValueError("Eine Verbindung braucht mindestens zwei Punkte")
                old = routes[key]
                if properties.get("a") != old.a:
                    points.reverse()  # Linie in Gegenrichtung gezeichnet
                routes[key] = Route(
                    a=old.a,
                    b=old.b,
                    kind=properties.get("kind", old.kind),
                    waypoints=tuple(points[1:-1]),
                    minutes=properties.get("fixed_minutes"),
                )
            case _:
                raise ValueError(f"Unbekanntes Element: {properties.get('feature')!r} als {geometry.get('type')!r}")
    # Über den Konstruktor, damit der Inhalt geprüft wird.
    updated = dict(content) | {"locations": locations, "routes": tuple(routes[frozenset((r.a, r.b))] for r in content.routes)}
    return Content(**updated)


def changes(old: Content, new: Content) -> list[str]:
    """Lesbare Liste der Unterschiede bei Lage und Reisezeit."""
    lines = []
    for location_id, location in new.locations.items():
        before = old.locations[location_id]
        if before.point != location.point:
            lines.append(f"{location_id}: x={location.x}, y={location.y} (vorher {before.x}, {before.y})")
    for before, route in zip(old.routes, new.routes):
        if before != route or old.route_time(before) != new.route_time(route):
            lines.append(
                f"{route.a} – {route.b}: {route.kind}, {new.route_length(route)} m, {new.route_time(route)} Min"
                f" (vorher {before.kind}, {old.route_length(before)} m, {old.route_time(before)} Min)"
            )
    return lines


def main(argv: list[str]) -> int:
    match argv:
        case ["export"]:
            print(json.dumps(to_geojson(), ensure_ascii=False, indent=2))
            return 0
        case ["check", path]:
            with open(path, encoding="utf-8") as file:
                updated = from_geojson(json.load(file))
            print("\n".join(changes(CONTENT, updated)) or "Keine Änderungen.")
            return 0
        case _:
            print(__doc__, file=sys.stderr)
            return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
