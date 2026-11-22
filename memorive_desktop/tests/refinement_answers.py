"""What a stand-in model or channel answers to a session refinement request, for tests.

The model channel (memorive/sessions/refinement/model_channel.py) asks for a conversation brief
(memorive/sessions/refinement/brief.py); these give a small valid one.
"""
from __future__ import annotations

from typing import Any, Mapping

from memorive.sessions.refinement.brief import make_brief

OVERVIEW = "整理了这段会话。"


def answer_for(schema: Mapping[str, Any], text: str = "保留来源。", kind: str = "result") -> dict[str, Any]:
    """A model's answer to the schema the channel sent: an overview and one item citing the first message."""
    refs = schema["properties"]["items"]["items"]["properties"]["message_refs"]["items"]["enum"]
    return {"overview": OVERVIEW, "items": [{"kind": kind, "text": text, "message_refs": [refs[0]]}]}


def brief_for(request: Mapping[str, Any], text: str = "保留来源。", kind: str = "result") -> dict[str, Any]:
    """The brief a channel returns for this request."""
    return make_brief({"overview": OVERVIEW, "items": [{"kind": kind, "text": text, "message_refs": ["m1"]}]},
                      messages=request["messages"], **request["options"])
