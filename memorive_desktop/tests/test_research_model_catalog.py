"""Which model profiles research can choose (memorive_research_runtime/report_models.py).

profile_options() decides it from data: the saved settings and the recognized local models. The
desktop's catalog() and the window-less binding (memorive/research/workspace/headless_models.py) only
supply that data. Here the settings snapshot is replaced by a fixed list of profiles.
"""
from __future__ import annotations

import pytest

from memorive_research_runtime import report_models


def profile(ref, kind, capability="CHAT", status="AVAILABLE", credential=True, runnable=None, exact=None):
    service = {"adapter_id": kind.lower() + "_adapter"}
    if runnable is not None:
        service["execution_eligible"] = runnable
    if exact is not None:
        service["exact_identity_available"] = exact
    return {"profile_ref": ref, "model": ref + " model", "profile_kind": kind, "capability": capability,
            "connection_status": status, "credential_reference_stored": credential,
            "execution_profile": {"model": {"model_name": ref + "-name"}, "service": service}}


PROFILES = [
    profile("api:ok", "API"),
    profile("api:offline", "API", status="UNAVAILABLE"),
    profile("api:no-key", "API", credential=False),
    profile("local:ok", "LOCAL", runnable=True, exact=True),
    profile("local:unverified", "LOCAL", runnable=True, exact=False),
    profile("cli:ok", "CLI"),
    profile("api:embed", "API", capability="EMBEDDING"),
]


@pytest.fixture
def snapshot(monkeypatch):
    import memorive.desktop.current_task.workflow_mapping as mapping
    given = {}

    def build(settings, *, settings_revision, local_model_profiles):
        given.update(settings=settings, revision=settings_revision, local=local_model_profiles)
        return {"profile_catalog": PROFILES}

    monkeypatch.setattr(mapping, "build_core_execution_snapshot", build)
    return given


def test_a_profile_is_eligible_when_connected_with_a_key_and_a_local_one_when_verified(snapshot):
    options = report_models.profile_options({"settings": {"theme": "x"}, "revision": 4}, [{"profile_ref": "local:ok"}])
    assert [(o["profile_ref"], o["eligible"]) for o in options] == [
        ("api:ok", True), ("api:offline", False), ("api:no-key", False),
        ("local:ok", True), ("local:unverified", False), ("cli:ok", True)]
    assert snapshot == {"settings": {"theme": "x"}, "revision": 4, "local": [{"profile_ref": "local:ok"}]}
    assert options[0] == {"profile_ref": "api:ok", "model": "api:ok model", "profile_kind": "API", "model_name": "api:ok-name",
                          "display_name": "api:ok model", "binding_hash": report_models.sha(PROFILES[0]["execution_profile"]),
                          "adapter_id": "api_adapter", "eligible": True}


def test_each_capability_has_its_own_list(snapshot):
    options = report_models.profile_options({"settings": {}, "revision": 0}, [], "EMBEDDING")
    assert [o["profile_ref"] for o in options] == ["api:embed"]
