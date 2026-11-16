"""Running a configured model for a feature: freeze the chosen profile, then send the request once.

Research Q&A, reports, the AI briefing, retrieval embeddings and picture transcription all go
through the settings service in two calls: *_freeze copies the model profile chosen in Settings
into a snapshot file, *_execute sends the request for that snapshot. These tests use a local
model profile and a stand-in request sender (the model validation runner), so nothing leaves the
computer.
"""
from __future__ import annotations

import base64
from pathlib import Path

import pytest

from memorive_settings.capability import CapabilityProjection
from memorive_settings.contracts import default_settings
from memorive_settings.controller import SettingsController
from memorive_settings.credentials import CredentialReferenceManager
from memorive_settings.directory_policy import DirectoryPolicy
from memorive_settings.store import SettingsStore

RUN_ID = "research-" + "a" * 24
CHAT = {"profile_ref": "local:chat", "execution_eligible": True, "exact_identity_available": True, "capability": "CHAT",
        "connection_status": "AVAILABLE", "model_name": "local-chat", "display_name": "Local chat"}
EMBEDDING = dict(CHAT, profile_ref="local:embed", capability="EMBEDDING", model_name="local-embed", display_name="Local embed")
SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
PASS = {"status": "PASS", "reason": "OK", "execution_receipt": {"status": "PASS"}}


def transient_failure():
    return {"status": "FAILED", "reason": "HTTP_503", "execution_receipt": {"transport_diagnostic": {"http_status": 503}}}


class Runner:
    """Stands in for the model validation runner, which sends the actual requests."""

    def __init__(self, *results):
        self.results = list(results) or [dict(PASS)]
        self.calls = []

    def _next(self, kind, request):
        self.calls.append((kind, request))
        return dict(self.results.pop(0) if len(self.results) > 1 else self.results[0])

    def execute_structured_chat(self, **request):
        return self._next("chat", request)

    def execute_embeddings(self, **request):
        return self._next("embeddings", request)

    def execute_ocr_image(self, **request):
        return self._next("image", request)


@pytest.fixture
def waits(monkeypatch):
    """Record the waits between tries (retry.wait_before_retry) instead of sleeping."""
    from memorive_model_execution import retry
    seen = []
    monkeypatch.setattr(retry, "wait_before_retry", seen.append)
    return seen


def make_controller(root: Path, runner: Runner) -> SettingsController:
    store = SettingsStore(root / "profile", default_factory=lambda: default_settings(workspace_root=str(root), artifact_root=str(root)))
    controller = SettingsController(
        store=store, credentials=CredentialReferenceManager(), capabilities=CapabilityProjection([], revision="test"),
        directory_policy=DirectoryPolicy([root]), model_validation_runner=runner,
        local_profile_resolver=lambda ref: {"local:chat": dict(CHAT), "local:embed": dict(EMBEDDING)}.get(ref))
    return controller


def test_a_frozen_research_question_is_sent_once(tmp_path):
    runner = Runner()
    controller = make_controller(tmp_path, runner)
    frozen = controller.research_chat_freeze(run_id=RUN_ID, profile_ref="local:chat")
    assert (frozen["profile_kind"], frozen["model_name"]) == ("LOCAL", "local-chat")
    result = controller.research_chat_execute(snapshot_id=frozen["snapshot_id"], prompt="What changed?", response_schema=SCHEMA)
    assert result["status"] == "PASS" and result["report_snapshot_id"] == frozen["snapshot_id"]
    [(kind, request)] = runner.calls
    assert kind == "chat" and request["purpose"] == "research_chat" and request["profile_kind"] == "LOCAL"
    folder = tmp_path / "profile" / "research_chat_profiles"
    assert (folder / (frozen["snapshot_id"] + ".result.json")).is_file()
    # The claim file keeps a second send of the same snapshot from happening, even after a crash.
    with pytest.raises(ValueError, match="REPORT_MODEL_ALREADY_SENT"):
        controller.research_chat_execute(snapshot_id=frozen["snapshot_id"], prompt="What changed?", response_schema=SCHEMA)
    assert len(runner.calls) == 1


def test_the_kept_result_of_a_research_question_is_read_back_without_sending(tmp_path):
    """After a restart the research workspace asks for the result a snapshot already has
    (settings.research_chat_result); nothing is sent again."""
    runner = Runner()
    controller = make_controller(tmp_path, runner)
    frozen = controller.research_chat_freeze(run_id=RUN_ID, profile_ref="local:chat")
    assert controller.research_chat_result(snapshot_id=frozen["snapshot_id"]) is None
    sent = controller.research_chat_execute(snapshot_id=frozen["snapshot_id"], prompt="What changed?", response_schema=SCHEMA)
    assert controller.research_chat_result(snapshot_id=frozen["snapshot_id"]) == sent
    assert len(runner.calls) == 1


def test_a_research_question_is_tried_again_after_a_temporary_failure(tmp_path, waits):
    runner = Runner(transient_failure(), transient_failure(), dict(PASS))
    controller = make_controller(tmp_path, runner)
    frozen = controller.research_chat_freeze(run_id=RUN_ID, profile_ref="local:chat")
    result = controller.research_chat_execute(snapshot_id=frozen["snapshot_id"], prompt="What changed?", response_schema=SCHEMA)
    assert result["status"] == "PASS" and len(runner.calls) == 3
    assert [a["status"] for a in result["recovery_attempts"]] == ["FAILED", "FAILED", "PASS"]
    assert waits == [0, 1]  # 1 s before the second try, 2 s before the third


def test_a_research_question_is_not_tried_again_after_a_lasting_failure(tmp_path, waits):
    runner = Runner({"status": "FAILED", "reason": "API_AUTHENTICATION_FAILED", "execution_receipt": {}})
    controller = make_controller(tmp_path, runner)
    frozen = controller.research_chat_freeze(run_id=RUN_ID, profile_ref="local:chat")
    result = controller.research_chat_execute(snapshot_id=frozen["snapshot_id"], prompt="What changed?", response_schema=SCHEMA)
    assert result["status"] == "FAILED" and len(runner.calls) == 1 and waits == []


def test_a_profile_that_is_not_available_is_refused(tmp_path):
    controller = make_controller(tmp_path, Runner())
    with pytest.raises(ValueError, match="REPORT_MODEL_UNAVAILABLE"):
        controller.research_chat_freeze(run_id=RUN_ID, profile_ref="local:missing")
    with pytest.raises(ValueError, match="REPORT_MODEL_UNAVAILABLE"):  # an embedding model cannot answer
        controller.research_chat_freeze(run_id=RUN_ID, profile_ref="local:embed")


def test_a_report_is_sent_once_with_its_period(tmp_path):
    runner = Runner()
    controller = make_controller(tmp_path, runner)
    frozen = controller.report_profile_freeze(run_id=RUN_ID, period="weekly", profile_ref="local:chat")
    controller.report_profile_execute(snapshot_id=frozen["snapshot_id"], prompt="Summarise the week.", response_schema=SCHEMA)
    [(_, request)] = runner.calls
    assert request["purpose"] == "report_weekly"


def test_a_cancelled_ai_briefing_is_not_sent(tmp_path):
    runner = Runner()
    controller = make_controller(tmp_path, runner)
    frozen = controller.ai_briefing_freeze(run_id=RUN_ID, profile_ref="local:chat")
    assert controller.ai_briefing_cancel(snapshot_id=frozen["snapshot_id"]) == {"status": "CANCELLING"}
    result = controller.ai_briefing_execute(snapshot_id=frozen["snapshot_id"], prompt="Brief me.", response_schema=SCHEMA)
    assert (result["status"], result["reason"]) == ("CANCELLED", "AI_MODEL_CANCELLED")
    assert runner.calls == []


def test_retrieval_embeddings_use_an_embedding_profile(tmp_path):
    runner = Runner({"status": "PASS", "vectors": [[0.0, 1.0]]})
    controller = make_controller(tmp_path, runner)
    frozen = controller.research_embedding_freeze(run_id=RUN_ID, profile_ref="local:embed")
    result = controller.research_embedding_execute(snapshot_id=frozen["snapshot_id"], inputs=["growth at 37 C"])
    [(kind, request)] = runner.calls
    assert kind == "embeddings" and request["inputs"] == ["growth at 37 C"] and request["purpose"] == "research_retrieval"
    assert result["snapshot_id"] == frozen["snapshot_id"]


def test_a_picture_is_transcribed_with_the_chat_profile(tmp_path):
    runner = Runner({"status": "PASS", "text": "Figure 1"})
    controller = make_controller(tmp_path, runner)
    frozen = controller.research_chat_freeze(run_id=RUN_ID, profile_ref="local:chat")
    image = base64.b64encode(b"\x89PNG not really").decode("ascii")
    result = controller.research_image_execute(snapshot_id=frozen["snapshot_id"], image_base64=image, mime_type="image/png")
    [(kind, request)] = runner.calls
    assert kind == "image" and request["purpose"] == "research_attachment" and request["mime_type"] == "image/png"
    assert result["snapshot_id"] == frozen["snapshot_id"]
