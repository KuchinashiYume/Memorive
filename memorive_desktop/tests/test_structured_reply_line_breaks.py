"""A model reply whose JSON strings hold line breaks as they are (models/validation/structured.py).

In acceptance testing four of seven research chat replies from deepseek-flash put a line break straight into
a JSON string. The strict parser refused them (STRUCTURED_CHAT_RESPONSE_INVALID), the question failed after
three paid attempts, though each answer was right.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from memorive.models.validation.api_route import _api_chat_response
from memorive.models.validation.structured import _json_object_from_text

REPLY = '{"answer": "Reordering by revision date brings it back to 78%.\nSee the evaluation table.", "citations": ["E1"]}'


def test_a_line_break_inside_a_json_string_is_read():
    assert "\n" in REPLY and _json_object_from_text(REPLY) == {
        "answer": "Reordering by revision date brings it back to 78%.\nSee the evaluation table.", "citations": ["E1"]}
    assert _json_object_from_text("Here it is:\n```json\n" + REPLY + "\n```")["citations"] == ["E1"]


def test_the_chat_route_returns_such_a_reply():
    body = json.dumps({"model": "deepseek-v4-flash", "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                       "choices": [{"finish_reason": "stop", "message": {"content": REPLY}}]}).encode()
    model, document, _usage = _api_chat_response(SimpleNamespace(provider_family="OPENAI_COMPATIBLE"), body)
    assert model == "deepseek-v4-flash" and document["citations"] == ["E1"]


def test_text_that_is_not_json_is_still_refused():
    assert _json_object_from_text("The answer is 78%.") is None
