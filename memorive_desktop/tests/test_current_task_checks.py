"""Current task page: choosing another model for one node, and which artifacts are shown.

A node may only switch to a profile that is configured with a stored credential, of the right
kind (embedding for the chunk-embedding node, chat for the others), and with the model the user
picked. Private, restricted, gold and holdout artifacts never reach the page.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from memorive.desktop.current_task.product_controller import CurrentTaskProductController
from memorive.desktop.current_task.projection import CurrentTaskProjector, ProjectionError

CATALOG = [
    {"profile_ref": "api:chat", "model": "chat-model", "credential_reference_stored": True, "capability": "CHAT"},
    {"profile_ref": "api:embed", "model": "embed-model", "credential_reference_stored": True,
     "capability": "EMBEDDING", "embedding_eligible": True},
    {"profile_ref": "api:nokey", "model": "chat-model", "credential_reference_stored": False, "capability": "CHAT"},
    {"profile_ref": "api:chat-only", "model": "chat-model", "credential_reference_stored": True, "capability": "CHAT"},
]


def _override(node_id, profile_ref, model, catalog=CATALOG):
    owner = SimpleNamespace(_workflow_execution_snapshot={"profile_catalog": catalog})
    return CurrentTaskProductController._validate_core_model_override(owner, node_id, profile_ref=profile_ref,
                                                                     model=model)


@pytest.mark.parametrize("node_id, profile_ref, model", [
    ("05_ANALYSIS", "api:chat", "chat-model"),
    ("05_ANALYSIS", " api:chat ", " chat-model "),
    ("02_CHUNK_EMBEDDING", "api:embed", "embed-model"),
])
def test_a_configured_profile_of_the_right_kind_may_be_chosen(node_id, profile_ref, model):
    assert _override(node_id, profile_ref, model) is None


@pytest.mark.parametrize("node_id, profile_ref, model, code", [
    ("05_ANALYSIS", "", "chat-model", "CURRENT_TASK_MODEL_PROFILE_REQUIRED"),
    ("02_CHUNK_EMBEDDING", None, "embed-model", "CURRENT_TASK_EMBEDDING_PROFILE_REQUIRED"),
    ("05_ANALYSIS", "api:chat", " ", "CURRENT_TASK_MODEL_REQUIRED"),
    ("02_CHUNK_EMBEDDING", "api:embed", None, "CURRENT_TASK_EMBEDDING_MODEL_REQUIRED"),
    ("05_ANALYSIS", "api:removed", "chat-model", "CURRENT_TASK_MODEL_PROFILE_UNAVAILABLE"),
    ("05_ANALYSIS", "api:nokey", "chat-model", "CURRENT_TASK_MODEL_PROFILE_UNAVAILABLE"),
    ("02_CHUNK_EMBEDDING", "api:chat-only", "chat-model", "CURRENT_TASK_EMBEDDING_PROFILE_INELIGIBLE"),
    ("05_ANALYSIS", "api:embed", "embed-model", "CURRENT_TASK_CHAT_PROFILE_REQUIRED"),
    ("05_ANALYSIS", "api:chat", "other-model", "CURRENT_TASK_MODEL_MISMATCH"),
    ("02_CHUNK_EMBEDDING", "api:embed", "other-model", "CURRENT_TASK_EMBEDDING_MODEL_MISMATCH"),
])
def test_a_profile_that_cannot_run_the_node_is_refused(node_id, profile_ref, model, code):
    with pytest.raises(ValueError) as caught:
        _override(node_id, profile_ref, model)
    assert str(caught.value) == code


def test_without_a_profile_catalog_nothing_can_be_chosen():
    for catalog in ([], None):
        owner = SimpleNamespace(_workflow_execution_snapshot=catalog)
        with pytest.raises(ValueError, match="CURRENT_TASK_MODEL_PROFILE_UNAVAILABLE"):
            CurrentTaskProductController._validate_core_model_override(owner, "05_ANALYSIS", profile_ref="api:chat",
                                                                      model="chat-model")


@pytest.mark.parametrize("row", [
    {"artifact_id": "a1", "locator": "memorive://artifact/a1"},
    {"artifact_id": "a1", "classification": "public_safe", "locator": "cards/a1.md"},
])
def test_public_artifacts_are_shown(row):
    assert CurrentTaskProjector._validate_artifact(row) is None


@pytest.mark.parametrize("row", [
    {"classification": "PRIVATE"},
    {"classification": "restricted"},
    {"classification": "GOLD"},
    {"classification": "HOLDOUT"},
    {"private": True},
    {"locator": "private:answers.json"},
    {"locator": "runs/LANE_A_PRIVATE/item.json"},
    {"locator": "runs/lane_b_private/item.json"},
    {"locator": "exam/holdout/set.json"},
    {"locator": "exam/private_gold/key.json"},
])
def test_private_artifacts_are_kept_off_the_page(row):
    with pytest.raises(ProjectionError, match="PRIVATE_ARTIFACT_FORBIDDEN"):
        CurrentTaskProjector._validate_artifact(dict({"artifact_id": "a1"}, **row))
