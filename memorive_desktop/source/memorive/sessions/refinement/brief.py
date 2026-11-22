"""The conversation brief a session refinement makes: topic-organized minutes from one model request.

The model channel (model_channel.py) asks the model for an overview and a list of items, each with a
section (kind), a sentence and the messages it rests on (m1, m2, ... in conversation order). make_brief
checks that answer and applies the local rules:

- sections the user switched off (next steps, risks) are left out, whatever the model sent;
- a decision or next step that cites no user message is the AI's proposal, so it moves to ai_suggestion;
- a number in an item that none of its cited messages contains is flagged for the reviewer;
- keys, tokens, e-mail addresses and cookie values are redacted from everything kept.

The session page, the library's Markdown (documents/library/refinement_markdown.py) and the library
registration read the brief this returns.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from memorive.sessions.refinement.redaction import redact_text

SCHEMA_VERSION = "ConversationBrief-v1"
# Section order on the page and in Markdown.
KINDS = ("decision", "next_step", "result", "open_question", "risk", "ai_suggestion")
USER_BACKED_KINDS = {"decision", "next_step"}
NUMBER_NOT_IN_SOURCE = "NUMBER_NOT_IN_SOURCE"
_MAX_ITEMS = 60
_MAX_TEXT = 2_000
_MAX_OVERVIEW = 4_000
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*%?")


class BriefError(ValueError):
    """The model's answer is not a brief; the code names what is wrong (REFINEMENT_PROPOSAL_*)."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def message_refs(count: int) -> list[str]:
    return [f"m{index}" for index in range(1, count + 1)]


def allowed_kinds(include_tasks: bool, include_risks: bool) -> list[str]:
    return [kind for kind in KINDS
            if (include_tasks or kind != "next_step") and (include_risks or kind != "risk")]


def response_schema(refs: Sequence[str], *, include_tasks: bool, include_risks: bool) -> dict[str, Any]:
    """The JSON schema the model's answer must follow."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["overview", "items"],
        "properties": {
            "overview": {"type": "string", "minLength": 1, "maxLength": _MAX_OVERVIEW},
            "items": {
                "type": "array",
                "maxItems": _MAX_ITEMS,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["kind", "text", "message_refs"],
                    "properties": {
                        "kind": {"enum": allowed_kinds(include_tasks, include_risks)},
                        "text": {"type": "string", "minLength": 1, "maxLength": _MAX_TEXT},
                        "message_refs": {"type": "array", "minItems": 1, "maxItems": 20,
                                         "items": {"enum": list(refs)}},
                    },
                },
            },
        },
    }


def _numbers(text: str) -> set[str]:
    """Numbers worth checking: two or more digits, or a decimal or a percentage. Thousands commas dropped."""
    found = set()
    for token in _NUMBER.findall(text):
        value = token.rstrip("%").replace(",", "")
        if len(re.sub(r"\D", "", value)) >= 2 or "." in value or token.endswith("%"):
            found.add(value)
    return found


def _redacted(text: str) -> str:
    return redact_text(text)["redacted_text"].strip()


def make_brief(answer: Any, *, messages: Sequence[Mapping[str, str]],
               include_tasks: bool, include_risks: bool) -> dict[str, Any]:
    """The brief from the model's answer to the conversation `messages` (role and content, m1 first)."""
    refs = message_refs(len(messages))
    by_ref = dict(zip(refs, messages))
    if not isinstance(answer, Mapping) or set(answer) != {"overview", "items"}:
        raise BriefError("REFINEMENT_PROPOSAL_FIELDS_INVALID")
    overview = answer["overview"]
    if not isinstance(overview, str) or not overview.strip() or len(overview) > _MAX_OVERVIEW:
        raise BriefError("REFINEMENT_PROPOSAL_OVERVIEW_INVALID")
    rows = answer["items"]
    if not isinstance(rows, list) or len(rows) > _MAX_ITEMS:
        raise BriefError("REFINEMENT_PROPOSAL_ITEM_COUNT_INVALID")
    kept = allowed_kinds(include_tasks, include_risks)
    items: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {"kind", "text", "message_refs"}:
            raise BriefError("REFINEMENT_PROPOSAL_ITEM_FIELDS_INVALID")
        kind, text, cited = row["kind"], row["text"], row["message_refs"]
        if kind not in KINDS:
            raise BriefError("REFINEMENT_PROPOSAL_TYPE_INVALID")
        if not isinstance(text, str) or not text.strip() or len(text) > _MAX_TEXT or "\x00" in text:
            raise BriefError("REFINEMENT_PROPOSAL_CANDIDATE_TEXT_INVALID")
        if (not isinstance(cited, list) or not cited
                or any(not isinstance(ref, str) or ref not in by_ref for ref in cited)):
            raise BriefError("REFINEMENT_PROPOSAL_MESSAGE_REFS_INVALID")
        if kind not in kept:
            continue
        cited = sorted(set(cited), key=refs.index)
        item: dict[str, Any] = {"kind": kind, "text": _redacted(text), "message_refs": cited, "flags": []}
        if kind in USER_BACKED_KINDS and not any(by_ref[ref]["role"] == "user" for ref in cited):
            item.update(kind="ai_suggestion", moved_from=kind)
        source = " ".join(by_ref[ref]["content"] for ref in cited).replace(",", "")
        if any(number not in source for number in _numbers(text)):
            item["flags"].append(NUMBER_NOT_IN_SOURCE)
        items.append(item)
    items.sort(key=lambda entry: KINDS.index(entry["kind"]))
    cited_refs = sorted({ref for entry in items for ref in entry["message_refs"]}, key=refs.index)
    return {
        "schema_version": SCHEMA_VERSION,
        "overview": _redacted(overview),
        "items": items,
        "options": {"include_tasks": include_tasks, "include_risks": include_risks},
        # The cited messages as this version read them: the page shows these once the conversation changed.
        "quotes": {ref: {"role": by_ref[ref]["role"], "text": _redacted(by_ref[ref]["content"])}
                   for ref in cited_refs},
    }


def validate_brief(value: Any) -> dict[str, Any]:
    """A saved brief as make_brief made it; the library checks a draft with this before registering it."""
    if (not isinstance(value, Mapping) or value.get("schema_version") != SCHEMA_VERSION
            or set(value) != {"schema_version", "overview", "items", "options", "quotes"}
            or not isinstance(value["overview"], str) or not value["overview"].strip()
            or not isinstance(value["items"], list) or not isinstance(value["quotes"], Mapping)):
        raise BriefError("REFINEMENT_BRIEF_INVALID")
    for item in value["items"]:
        if (not isinstance(item, Mapping) or item.get("kind") not in KINDS
                or not isinstance(item.get("text"), str) or not item["text"]
                or not isinstance(item.get("message_refs"), list) or not item["message_refs"]
                or any(ref not in value["quotes"] for ref in item["message_refs"])):
            raise BriefError("REFINEMENT_BRIEF_INVALID")
    return dict(value)


__all__ = ["BriefError", "KINDS", "NUMBER_NOT_IN_SOURCE", "allowed_kinds", "make_brief", "message_refs",
           "response_schema", "validate_brief"]
