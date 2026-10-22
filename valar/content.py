"""Testinhalt, vorerst fest im Code. Reine Testdaten, kein fester Weltinhalt."""

from pydantic import BaseModel, ConfigDict, Field, model_validator

from valar.rules import MINUTES_PER_DAY

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
    """Ein Ort. Ein Unterort nennt seinen Ort als `parent_id` (Entscheidung 11)."""

    model_config = ConfigDict(frozen=True)

    location_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    parent_id: str | None = None


class Route(BaseModel):
    """Verbindung zwischen zwei Orten, in beide Richtungen gleich lang (Entscheidung 55)."""

    model_config = ConfigDict(frozen=True)

    a: str
    b: str
    minutes: int = Field(gt=0)


class Content(BaseModel):
    model_config = ConfigDict(frozen=True)

    quests: dict[str, QuestDef]
    # Dauer jeder Aktion in Spielminuten; der Code legt sie fest, nicht das Modell.
    action_minutes: dict[str, int]
    locations: dict[str, LocationDef]
    routes: tuple[Route, ...]
    # Uhrzeit bei Spielminute 0, in Minuten nach Mitternacht (Entscheidung 57).
    start_minute_of_day: int = Field(ge=0, lt=MINUTES_PER_DAY)

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
        return self

    def route_minutes(self, origin: str, destination: str) -> int | None:
        """Reisezeit zwischen zwei Orten, oder None, wenn sie nicht verbunden sind."""
        pair = frozenset((origin, destination))
        return next((r.minutes for r in self.routes if frozenset((r.a, r.b)) == pair), None)

    def exits(self, origin: str) -> list[tuple[str, int]]:
        """Alle von `origin` aus erreichbaren Orte mit Reisezeit, in der Reihenfolge des Inhalts."""
        result = []
        for route in self.routes:
            if origin == route.a:
                result.append((route.b, route.minutes))
            elif origin == route.b:
                result.append((route.a, route.minutes))
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
            name="Bretterstadt",
            description="Hastig gezimmerte Buden, Schlamm bis an die Knöchel, überall Schürfer.",
        ),
        GUILD_HALL: LocationDef(
            location_id=GUILD_HALL,
            name="Zunfthaus",
            description="Ein Steinbau zwischen den Bretterbuden. Hier kauft die Zunft Kristalle an.",
            parent_id=BOOMTOWN,
        ),
        TAVERN: LocationDef(
            location_id=TAVERN,
            name="Schenke",
            description="Rauch, dünnes Bier und Gerüchte über das Kristallfeld.",
            parent_id=BOOMTOWN,
        ),
        CRYSTAL_FIELD: LocationDef(
            location_id=CRYSTAL_FIELD,
            name="Kristallfeld",
            description="Aufgerissene Erde, glitzernder Splitterstaub, Wachen der Fürsten am Rand.",
        ),
        VILLAGE: LocationDef(
            location_id=VILLAGE,
            name="Dorf",
            description="Alte Höfe, ein Hain mit Opfersteinen. Man misstraut Fremden.",
        ),
    },
    routes=(
        Route(a=BOOMTOWN, b=GUILD_HALL, minutes=5),
        Route(a=BOOMTOWN, b=TAVERN, minutes=5),
        Route(a=BOOMTOWN, b=CRYSTAL_FIELD, minutes=90),
        Route(a=BOOMTOWN, b=VILLAGE, minutes=240),
        Route(a=CRYSTAL_FIELD, b=VILLAGE, minutes=180),
    ),  # Testwerte
    start_minute_of_day=8 * 60,  # Spielbeginn um 8 Uhr
)
