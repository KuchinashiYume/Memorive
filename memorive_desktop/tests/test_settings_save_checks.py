"""Saving settings from the window (memorive/desktop/settings/controller.py): preview, then save.

preview() checks settings without writing them: the settings themselves, the folders, and the
risks a person must confirm (a retry count of 0-1 or above 5 for a model step). save() writes
only settings whose preview passes, keeps the stored credential references, and needs the
revision the window started from. Everything here is written in a temporary folder.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from memorive.desktop.settings.capability import CapabilityProjection
from memorive.desktop.settings.contracts import default_settings, risk_ids, validate_settings
from memorive.desktop.settings.controller import SettingsController
from memorive.desktop.settings.credentials import CredentialReferenceManager
from memorive.desktop.settings.directory_policy import DirectoryPolicy
from memorive.desktop.settings.store import SettingsStore


@pytest.fixture
def controller(tmp_path):
    workspace, artifacts = tmp_path / "workspace", tmp_path / "artifacts"
    workspace.mkdir()
    artifacts.mkdir()
    store = SettingsStore(tmp_path / "profile", default_factory=lambda: default_settings(
        workspace_root=str(workspace), artifact_root=str(artifacts)))
    return SettingsController(store=store, credentials=CredentialReferenceManager(),
                              capabilities=CapabilityProjection([], revision="test"),
                              directory_policy=DirectoryPolicy([tmp_path]))


def current(controller):
    return controller.store.load()


STEP = "ingest"  # a model step; its default retry count is 2


def model_step(settings):
    return next(n for n in settings["workflow"]["nodes"] if n["node_id"] == STEP)


def with_retry(settings, count):
    changed = deepcopy(settings)
    model_step(changed)["retry_count"] = count
    return changed


# Preview

def test_the_stored_settings_preview_cleanly(controller):
    preview = controller.preview(current(controller)["settings"])
    assert preview["status"] == "PASS" and preview["errors"] == [] and preview["pending_risks"] == []
    assert preview["directory_preflight"]["unchanged_directories"] is True
    assert preview["network_requests"] == 0 and preview["credential_value_reads"] == 0


@pytest.mark.parametrize("count, risk", [(0, "RETRY_LOW"), (1, "RETRY_LOW"), (6, "RETRY_HIGH"), (10, "RETRY_HIGH")])
def test_a_risky_retry_count_must_be_confirmed(controller, count, risk):
    settings = with_retry(current(controller)["settings"], count)
    expected = f"{risk}:{STEP}:{count}"
    preview = controller.preview(settings)
    assert preview["status"] == "BLOCKED" and preview["pending_risks"] == [expected] and preview["errors"] == []
    assert controller.preview(settings, [expected])["status"] == "PASS"
    assert risk_ids(validate_settings(settings)) == [expected]


@pytest.mark.parametrize("count", [2, 3, 5])
def test_a_usual_retry_count_is_no_risk(controller, count):
    assert controller.preview(with_retry(current(controller)["settings"], count))["status"] == "PASS"


@pytest.mark.parametrize("change", [
    lambda s: s.update(mode="EXPERT"),
    lambda s: s.pop("preferences"),
    lambda s: model_step(s).update(retry_count=11),
    lambda s: s.update(credential_references="none"),
])
def test_settings_that_do_not_fit_are_blocked_with_their_error(controller, change):
    settings = deepcopy(current(controller)["settings"])
    change(settings)
    preview = controller.preview(settings)
    assert preview["status"] == "BLOCKED" and len(preview["errors"]) == 1 and preview["errors"][0]
    assert preview["pending_risks"] == [] and "settings_sha256" not in preview


def test_preview_blocks_a_folder_outside_the_allowed_places(controller, tmp_path_factory):
    settings = deepcopy(current(controller)["settings"])
    settings["directories"]["workspace_root"] = str(tmp_path_factory.mktemp("elsewhere"))
    preview = controller.preview(settings)
    assert preview["status"] == "BLOCKED" and preview["errors"]


# Saving

def test_saving_writes_the_settings_and_returns_a_receipt(controller):
    before = current(controller)
    settings = with_retry(before["settings"], 3)
    receipt = controller.save(settings, expected_revision=before["revision"])
    after = current(controller)
    assert after["revision"] == before["revision"] + 1
    assert model_step(after["settings"])["retry_count"] == 3
    assert receipt["restart_required"] is False and receipt["embedding_binding_changed"] is False


def test_a_risk_left_unconfirmed_blocks_the_save(controller):
    before = current(controller)
    settings = with_retry(before["settings"], 1)
    with pytest.raises(ValueError, match="SETTINGS_PREVIEW_BLOCKED:RETRY_LOW"):
        controller.save(settings, expected_revision=before["revision"])
    assert current(controller)["revision"] == before["revision"]
    risk = risk_ids(validate_settings(settings))
    controller.save(settings, expected_revision=before["revision"], confirmed_risk_ids=risk)
    assert model_step(current(controller)["settings"])["retry_count"] == 1


def test_a_save_must_start_from_the_stored_revision(controller):
    before = current(controller)
    with pytest.raises(ValueError, match="SETTINGS_REVISION_CONFLICT"):
        controller.save(with_retry(before["settings"], 3), expected_revision=before["revision"] + 1)
    assert current(controller)["revision"] == before["revision"]


def test_a_save_cannot_change_credential_references(controller):
    before = current(controller)
    settings = deepcopy(before["settings"])
    settings["credential_references"] = [{
        "credential_ref": "REF:windows:Memorive/deepseek/primary", "provider": "DEEPSEEK_API_KEY",
        "logical_key": "primary", "status": "STORED_UNVERIFIED",
        "created_at": "2026-11-16T00:00:00Z", "updated_at": "2026-11-16T00:00:00Z"}]
    with pytest.raises(ValueError, match="CREDENTIAL_REFERENCES_MUTATION_REQUIRES_DEDICATED_FLOW"):
        controller.save(settings, expected_revision=before["revision"])
    assert current(controller) == before


def test_settings_that_do_not_fit_are_not_saved(controller):
    before = current(controller)
    settings = deepcopy(before["settings"])
    settings["mode"] = "EXPERT"
    with pytest.raises(ValueError):
        controller.save(settings, expected_revision=before["revision"])
    assert current(controller) == before
