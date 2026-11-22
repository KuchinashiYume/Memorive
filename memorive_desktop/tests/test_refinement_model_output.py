"""Session refinement: what the model's answer must look like, and the brief made from it.

The model channel (memorive/sessions/refinement/model_channel.py) numbers the session's messages m1, m2, ...,
asks a model for an overview and topic-organized items citing those numbers, and checks the answer
(memorive/sessions/refinement/brief.py): switched-off sections are dropped, a decision or next step that cites
no user message moves to the AI's suggestions, a number its cited messages do not hold is flagged, and secrets
are redacted. The model is replaced here by a local adapter that returns a prepared answer.
"""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from memorive.documents.library.refinement_markdown import render
from memorive.sessions.refinement.brief import BriefError, validate_brief
from memorive.sessions.refinement.model_channel import (
    ConversationRefinementBridgeError,
    ExistingConversationRefinementChannel,
)

PROFILE = "local:0123456789abcdef01234567"
MESSAGES = [
    {"role": "user", "content": "DECISION: keep the source of every claim. Budget is 1,200 tokens."},
    {"role": "assistant", "content": "Then build the candidate items from the kept sources; it took 35 seconds."},
    {"role": "user", "content": "Why did the first run fail?"},
    {"role": "user", "content": "Next: rerun with the small model tomorrow."},
]
ANSWER = {"overview": "Sources are kept; one question is open.", "items": [
    {"kind": "ai_suggestion", "text": "Build candidates from kept sources.", "message_refs": ["m2"]},
    {"kind": "decision", "text": "Keep the source of every claim; budget 1200 tokens.", "message_refs": ["m1"]},
    {"kind": "next_step", "text": "Rerun with the small model tomorrow.", "message_refs": ["m4"]},
    {"kind": "result", "text": "Building took 35 seconds.", "message_refs": ["m2"]},
    {"kind": "open_question", "text": "Why did the first run fail?", "message_refs": ["m3"]},
    {"kind": "risk", "text": "Kept sources may be incomplete.", "message_refs": ["m2", "m1"]},
]}


def request(messages=MESSAGES, include_tasks=True, include_risks=True):
    return {
        "task_id": "refinement-test-002", "logical_slot": "conversation_refinement", "profile_ref": PROFILE,
        "explicit_user_action": True, "source_snapshot_sha256": "a" * 64,
        "source": {"local_projection_id": "session-002", "provider_id": "deepseek"},
        "messages": deepcopy(messages), "review_required": True, "auto_ingest": False,
        "options": {"include_tasks": include_tasks, "include_risks": include_risks},
    }


class Registry:
    def call(self, method, params):
        return {"recognized_models": [{
            "profile_ref": PROFILE, "chat_endpoint": "http://127.0.0.1:11434/api/chat", "endpoint_kind": "ollama",
            "model_name": "qwen-test", "model_digest": "D" * 64, "exact_identity_available": True,
            "structured_chat_adapter": "EvaluationAssets_LOCAL_STRUCTURED_CHAT_V1", "execution_eligible": True,
            "connection_status": "AVAILABLE"}]}


class Adapter:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        model = kwargs["binding"]["requested_model"]
        return SimpleNamespace(success=True, error_code=None, response=deepcopy(self.answer), receipt={
            "schema_version": "EvaluationAssetsLocalStructuredChatReceipt-v1", "status": "PASS",
            "requested_model": model, "returned_model": model, "model_digest": "D" * 64,
            "local_model_request_count": 1, "external_request_count": 0, "cloud_fallback_count": 0, "api_cost_cny": 0.0})


def run(answer=ANSWER, messages=MESSAGES, **options):
    adapter = Adapter(answer)
    channel = ExistingConversationRefinementChannel(local_model_registry=Registry(), local_adapter=adapter)
    return channel.execute(request(messages, **options)), adapter


def test_the_answer_becomes_a_brief_in_section_order_with_cited_messages():
    result, adapter = run()
    brief = result["brief"]
    assert brief["overview"] == ANSWER["overview"]
    assert [item["kind"] for item in brief["items"]] == [
        "decision", "next_step", "result", "open_question", "risk", "ai_suggestion"]
    assert brief["items"][4]["message_refs"] == ["m1", "m2"]          # in conversation order
    assert sorted(brief["quotes"]) == ["m1", "m2", "m3", "m4"]
    assert brief["quotes"]["m3"] == {"role": "user", "text": MESSAGES[2]["content"]}
    assert all(item["flags"] == [] for item in brief["items"])          # 1200 and 35 are in their messages
    assert validate_brief(brief) == brief
    assert len(adapter.calls) == 1


@pytest.mark.parametrize("include_tasks", [True, False])
@pytest.mark.parametrize("include_risks", [True, False])
def test_switched_off_sections_are_neither_asked_for_nor_kept(include_tasks, include_risks):
    result, adapter = run(include_tasks=include_tasks, include_risks=include_risks)
    asked = adapter.calls[0]["schema"]["properties"]["items"]["items"]["properties"]["kind"]["enum"]
    kept = {item["kind"] for item in result["brief"]["items"]}
    for kind, on in (("next_step", include_tasks), ("risk", include_risks)):
        assert (kind in asked, kind in kept) == (on, on)
    assert {"decision", "result", "open_question", "ai_suggestion"} <= kept
    assert result["brief"]["options"] == {"include_tasks": include_tasks, "include_risks": include_risks}


def test_a_decision_citing_only_the_ai_is_kept_as_an_ai_suggestion():
    answer = {"overview": "o", "items": [
        {"kind": "decision", "text": "Build candidates from kept sources.", "message_refs": ["m2"]},
        {"kind": "next_step", "text": "Rerun tomorrow.", "message_refs": ["m2", "m4"]}]}
    items = run(answer)[0]["brief"]["items"]
    assert [(item["kind"], item.get("moved_from")) for item in items] == [
        ("next_step", None), ("ai_suggestion", "decision")]


def test_a_number_its_cited_messages_do_not_hold_is_flagged():
    answer = {"overview": "o", "items": [
        {"kind": "result", "text": "Building took 45 seconds.", "message_refs": ["m2"]},
        {"kind": "result", "text": "Building took 35 seconds.", "message_refs": ["m2"]},
        {"kind": "decision", "text": "Budget 1,200 tokens.", "message_refs": ["m1"]}]}
    flags = [(item["text"], item["flags"]) for item in run(answer)[0]["brief"]["items"]]
    assert flags == [("Budget 1,200 tokens.", []), ("Building took 45 seconds.", ["NUMBER_NOT_IN_SOURCE"]),
                     ("Building took 35 seconds.", [])]


def test_the_prompt_carries_every_message_whole_with_its_number():
    long_text = "x" * 150_000
    messages = MESSAGES[:2] + [{"role": "user", "content": long_text}]
    answer = {"overview": "o", "items": [{"kind": "result", "text": "t", "message_refs": ["m3"]}]}
    _, adapter = run(answer, messages)
    prompt = adapter.calls[0]["prompt"]
    assert long_text in prompt and MESSAGES[0]["content"] in prompt and '"message_ref":"m3"' in prompt
    assert '"options":{"include_risks":true,"include_tasks":true}' in prompt
    refs = adapter.calls[0]["schema"]["properties"]["items"]["items"]["properties"]["message_refs"]["items"]["enum"]
    assert refs == ["m1", "m2", "m3"]


def test_secrets_in_an_answer_and_its_quotes_are_redacted():
    messages = [{"role": "user", "content": "Mail the summary to someone@example.org."}]
    answer = {"overview": "Write to someone@example.org.",
              "items": [{"kind": "next_step", "text": "Mail someone@example.org.", "message_refs": ["m1"]}]}
    brief = run(answer, messages)[0]["brief"]
    assert "someone@example.org" not in repr(brief)
    assert brief["items"][0]["text"] == "Mail [REDACTED:EMAIL]."


def test_a_greeting_gives_an_overview_alone():
    brief = run({"overview": "Only greetings.", "items": []}, MESSAGES[:1])[0]["brief"]
    assert (brief["overview"], brief["items"], brief["quotes"]) == ("Only greetings.", [], {})


@pytest.mark.parametrize("answer, code", [
    ({"items": []}, "REFINEMENT_PROPOSAL_FIELDS_INVALID"),
    ({"overview": " ", "items": []}, "REFINEMENT_PROPOSAL_OVERVIEW_INVALID"),
    ({"overview": "o", "items": [{"kind": "summary", "text": "t", "message_refs": ["m1"]}]},
     "REFINEMENT_PROPOSAL_TYPE_INVALID"),
    ({"overview": "o", "items": [{"kind": "result", "text": "t", "message_refs": ["m9"]}]},
     "REFINEMENT_PROPOSAL_MESSAGE_REFS_INVALID"),
    ({"overview": "o", "items": [{"kind": "result", "text": "t", "message_refs": []}]},
     "REFINEMENT_PROPOSAL_MESSAGE_REFS_INVALID"),
])
def test_an_answer_that_is_not_a_brief_is_refused(answer, code):
    with pytest.raises(BriefError, match=code):
        run(answer)


def test_options_must_be_given():
    channel = ExistingConversationRefinementChannel(local_model_registry=Registry(), local_adapter=Adapter(ANSWER))
    value = request()
    del value["options"]
    with pytest.raises(ConversationRefinementBridgeError, match="REFINEMENT_REQUEST_FIELDS_INVALID"):
        channel.execute(value)


def test_the_brief_reads_as_markdown_by_section():
    brief = run()[0]["brief"]
    brief["items"][2]["flags"] = ["NUMBER_NOT_IN_SOURCE"]
    draft = {"schema_version": "DesktopSessionRefinementDraft-v4", "display_name": "精炼对话 · Planning",
             "language_context": None, "brief": brief, "local_projection_id": "session-002",
             "created_at": "2026-11-22T00:00:00Z", "source_snapshot_sha256": "A" * 64}
    text = render(draft, "B" * 64)
    assert text.startswith("# Planning\n")
    assert text.index("## Decisions") < text.index("## Next steps") < text.index("## AI suggestions · not adopted")
    assert "- [ ] Rerun with the small model tomorrow. (#4)" in text
    assert "- Kept sources may be incomplete. (#1, #2)" in text
    assert "  - Number not found in the cited messages" in text
