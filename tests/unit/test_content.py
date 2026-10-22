import pytest
from pydantic import ValidationError

from valar.content import CONTENT, QuestDef
from valar.models import SubmitQuestItems

VALID = {
    "quest_id": "q",
    "turn_in_location_id": "somewhere",
    "item_id": "thing",
    "quantity": 1,
    "reward_pennies": 1,
}


@pytest.mark.parametrize("field", ["quantity", "reward_pennies"])
@pytest.mark.parametrize("value", [0, -1])
def test_quest_def_rejects_non_positive_numbers(field, value):
    with pytest.raises(ValidationError):
        QuestDef(**(VALID | {field: value}))


def test_content_keys_match_quest_ids():
    for key, quest in CONTENT.quests.items():
        assert key == quest.quest_id


def test_content_gives_every_action_a_positive_duration():
    action_type = SubmitQuestItems.model_fields["type"].default
    assert CONTENT.action_minutes[action_type] > 0
