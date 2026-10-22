"""Testgebiet, vorerst fest im Code.

Die Namen stammen aus der Startregion Rauhenau (Entscheidung 85). Lage, Wege, Zahlen, Testfigur und
Auftrag sind Testwerte, kein fester Weltinhalt.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from valar import rules
from valar.models import RouteKind
from valar.rules import MINUTES_PER_DAY, Point

# Koordinaten in Metern liegen in diesem Rahmen um den Nullpunkt der Karte (Entscheidung 75).
MAP_LIMIT = 1_000_000
Coordinate = Annotated[int, Field(ge=-MAP_LIMIT, le=MAP_LIMIT)]

CRYSTAL_SHARD = "crystal_shard"
GUILD_SHARD_ORDER = "guild_shard_order"

BOOMTOWN = "boomtown"
GUILD_HALL = "guild_hall"
TAVERN = "tavern"
CRYSTAL_FIELD = "crystal_field"
VILLAGE = "village"


class QuestDef(BaseModel):
    """Auftrag zum Abgeben von Gegenständen. Die Belohnung kommt von hier, nie aus der Aktion."""

    model_config = ConfigDict(frozen=True)

    quest_id: str
    turn_in_location_id: str
    item_id: str
    quantity: int = Field(gt=0)
    reward_pennies: int = Field(gt=0)


class LocationDef(BaseModel):
    """Ein Ort mit seinem Punkt auf der Karte (x nach Osten, y nach Norden, in Metern).

    Ein Unterort nennt seinen Ort als `parent_id` (Entscheidung 11) und hat einen eigenen Punkt.
    """

    model_config = ConfigDict(frozen=True)

    location_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    x: Coordinate
    y: Coordinate
    parent_id: str | None = None

    @property
    def point(self) -> Point:
        return (self.x, self.y)


class Route(BaseModel):
    """Verbindung zwischen zwei Orten, in beide Richtungen gleich lang (Entscheidungen 55 und 76).

    Der Weg führt von `a` über die Zwischenpunkte nach `b`. Die Reisezeit rechnet der Code aus
    Länge und Wegart; `minutes` setzt sie fest, wo die Rechnung nicht passt (Fähre, Pass).
    """

    model_config = ConfigDict(frozen=True)

    a: str
    b: str
    kind: RouteKind = "road"
    waypoints: tuple[tuple[Coordinate, Coordinate], ...] = ()
    minutes: int | None = Field(default=None, gt=0)


class PlayerTemplate(BaseModel):
    """Feste Testfigur für eine neue Kampagne (Schritt 1d), bis es die Charaktererschaffung gibt (1i)."""

    model_config = ConfigDict(frozen=True)

    player_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    location_id: str
    money_pennies: int = Field(ge=0)
    attributes: dict[str, int] = {}
    skills: dict[str, int] = {}
    items: dict[str, int] = {}
    active_quests: tuple[str, ...] = ()


class Content(BaseModel):
    model_config = ConfigDict(frozen=True)

    quests: dict[str, QuestDef]
    # Dauer jeder Aktion in Spielminuten; der Code legt sie fest, nicht das Modell.
    action_minutes: dict[str, int]
    locations: dict[str, LocationDef]
    routes: tuple[Route, ...]
    # Uhrzeit bei Spielminute 0, in Minuten nach Mitternacht (Entscheidung 57).
    start_minute_of_day: int = Field(ge=0, lt=MINUTES_PER_DAY)
    start_player: PlayerTemplate

    @model_validator(mode="after")
    def _check_references(self) -> "Content":
        for key, location in self.locations.items():
            if key != location.location_id:
                raise ValueError(f"Schlüssel {key!r} passt nicht zu {location.location_id!r}")
            seen = {key}
            parent = location.parent_id
            while parent is not None:
                if parent not in self.locations:
                    raise ValueError(f"Unbekannter Ort {parent!r} über {key!r}")
                if parent in seen:
                    raise ValueError(f"Kreis bei den Unterorten von {key!r}")
                seen.add(parent)
                parent = self.locations[parent].parent_id

        pairs: set[frozenset[str]] = set()
        for route in self.routes:
            if route.a == route.b:
                raise ValueError(f"Verbindung von {route.a!r} zu sich selbst")
            for end in (route.a, route.b):
                if end not in self.locations:
                    raise ValueError(f"Verbindung zu unbekanntem Ort {end!r}")
            pair = frozenset((route.a, route.b))
            if pair in pairs:
                raise ValueError(f"Doppelte Verbindung {route.a!r} – {route.b!r}")
            pairs.add(pair)

        for location in self.locations.values():
            if location.parent_id and frozenset((location.location_id, location.parent_id)) not in pairs:
                raise ValueError(f"Unterort {location.location_id!r} ohne Verbindung zu seinem Ort")

        for key, quest in self.quests.items():
            if key != quest.quest_id:
                raise ValueError(f"Schlüssel {key!r} passt nicht zu {quest.quest_id!r}")
            if quest.turn_in_location_id not in self.locations:
                raise ValueError(f"Auftrag {key!r} an unbekanntem Ort {quest.turn_in_location_id!r}")

        start = self.start_player
        if start.location_id not in self.locations:
            raise ValueError(f"Testfigur an unbekanntem Ort {start.location_id!r}")
        unknown = set(start.active_quests) - set(self.quests)
        if unknown:
            raise ValueError(f"Testfigur mit unbekannten Aufträgen {sorted(unknown)}")
        if any(quantity <= 0 for quantity in start.items.values()):
            raise ValueError("Testfigur mit Menge ≤ 0")
        return self

    def route_points(self, route: Route) -> list[Point]:
        """Der Weg als Linienzug: Punkt von `a`, Zwischenpunkte, Punkt von `b`."""
        return [self.locations[route.a].point, *route.waypoints, self.locations[route.b].point]

    def route_length(self, route: Route) -> int:
        """Weglänge in Metern."""
        return rules.path_length(self.route_points(route))

    def route_time(self, route: Route) -> int:
        """Reisezeit in Spielminuten: fest gesetzt oder aus Länge und Wegart gerechnet."""
        if route.minutes is not None:
            return route.minutes
        return rules.travel_minutes(self.route_length(route), route.kind)

    def route_between(self, origin: str, destination: str) -> Route | None:
        """Die direkte Verbindung zweier Orte, oder None."""
        pair = frozenset((origin, destination))
        return next((r for r in self.routes if frozenset((r.a, r.b)) == pair), None)

    def route_minutes(self, origin: str, destination: str) -> int | None:
        """Reisezeit zwischen zwei Orten, oder None, wenn sie nicht verbunden sind."""
        route = self.route_between(origin, destination)
        return None if route is None else self.route_time(route)

    def exits(self, origin: str) -> list[tuple[str, int]]:
        """Alle von `origin` aus erreichbaren Orte mit Reisezeit, in der Reihenfolge des Inhalts."""
        result = []
        for route in self.routes:
            if origin == route.a:
                result.append((route.b, self.route_time(route)))
            elif origin == route.b:
                result.append((route.a, self.route_time(route)))
        return result


CONTENT = Content(
    quests={
        GUILD_SHARD_ORDER: QuestDef(
            quest_id=GUILD_SHARD_ORDER,
            turn_in_location_id=GUILD_HALL,
            item_id=CRYSTAL_SHARD,
            quantity=5,
            reward_pennies=100,  # 1 Gulden
        ),
    },
    action_minutes={"submit_quest_items": 10},  # Testwert
    locations={
        BOOMTOWN: LocationDef(
            location_id=BOOMTOWN,
            name="Sturmhagen",
            description="Hastig gezimmerte Buden, Schlamm bis an die Knöchel, überall Schürfer."
            " Die Leute nennen den Ort nur Guldenbretter.",
            x=20_000,
            y=20_000,
        ),
        GUILD_HALL: LocationDef(
            location_id=GUILD_HALL,
            name="Zunfthaus",
            description="Ein Steinbau zwischen den Bretterbuden. Hier kauft die Zunft Kristalle an.",
            x=20_000,
            y=19_600,
            parent_id=BOOMTOWN,
        ),
        TAVERN: LocationDef(
            location_id=TAVERN,
            name="Schenke",
            description="Rauch, dünnes Bier und Gerüchte über den Glasgrund.",
            x=20_300,
            y=20_250,
            parent_id=BOOMTOWN,
        ),
        CRYSTAL_FIELD: LocationDef(
            location_id=CRYSTAL_FIELD,
            name="Glasgrund",
            description="Aufgerissene Erde, glitzernder Splitterstaub, Wachen der Fürsten am Rand.",
            x=24_500,
            y=26_000,
        ),
        VILLAGE: LocationDef(
            location_id=VILLAGE,
            name="Lindenheim",
            description="Alte Höfe um eine mächtige Linde, dahinter ein Hain mit Opfersteinen."
            " Man misstraut Fremden.",
            x=35_400,
            y=15_800,
        ),
    },
    # Testwerte. Gerechnete Reisezeiten: 5, 5, 90, 240 und 180 Minuten.
    routes=(
        Route(a=BOOMTOWN, b=GUILD_HALL),  # 400 m Straße
        Route(a=BOOMTOWN, b=TAVERN),  # 391 m Straße
        Route(a=BOOMTOWN, b=CRYSTAL_FIELD),  # 7,5 km Straße
        Route(a=BOOMTOWN, b=VILLAGE, kind="path"),  # 16 km Pfad
        Route(a=CRYSTAL_FIELD, b=VILLAGE),  # 14,9 km Straße
    ),
    start_minute_of_day=8 * 60,  # Spielbeginn um 8 Uhr
    # Die Figur aus den Testfällen 1 und 1b: Klingenwaffen 30 und STR 14 ergeben Bonus +5.
    start_player=PlayerTemplate(
        player_id="testfigur",
        name="Testfigur",
        location_id=GUILD_HALL,
        money_pennies=30,
        attributes={"STR": 14, "GES": 8},
        skills={"SKILL-101": 30, "SKILL-405": 10},
        items={CRYSTAL_SHARD: 5},
        active_quests=(GUILD_SHARD_ORDER,),
    ),
)
