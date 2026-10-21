"""Testinhalt, vorerst fest im Code. Reine Testdaten, kein fester Weltinhalt."""

from pydantic import BaseModel, ConfigDict, Field

CRYSTAL_SHARD = "crystal_shard"
GUILD_HALL = "guild_hall"
GUILD_SHARD_ORDER = "guild_shard_order"


class QuestDef(BaseModel):
    """Auftrag zum Abgeben von Gegenständen. Die Belohnung kommt von hier, nie aus der Aktion."""

    model_config = ConfigDict(frozen=True)

    quest_id: str
    turn_in_location_id: str
    item_id: str
    quantity: int = Field(gt=0)
    reward_pennies: int = Field(gt=0)


class Content(BaseModel):
    model_config = ConfigDict(frozen=True)

    quests: dict[str, QuestDef]
    # Dauer jeder Aktion in Spielminuten; der Code legt sie fest, nicht das Modell.
    action_minutes: dict[str, int]


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
)
