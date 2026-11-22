"""A settings file imported on another computer or into a new data folder.

Keys are not exported. The import required the key references to be identical to the ones here, times
and status included, so a file only went back into the settings it came from: elsewhere it failed with
SETTINGS_IMPORT_CREDENTIAL_REFERENCE_DRIFT (PARAMETER_INVALID on the page). Now a reference this computer
has keeps its own row and one it lacks comes in as MISSING, for the key to be entered here. What is saved
is checked here; the settings preview that follows is the same as for any save.
"""
from __future__ import annotations

import pytest

from memorive.desktop.settings.capability import CapabilityProjection
from memorive.desktop.settings.contracts import default_settings
from memorive.desktop.settings.controller import SettingsController
from memorive.desktop.settings.credentials import CredentialReferenceManager
from memorive.desktop.settings.directory_policy import DirectoryPolicy
from memorive.desktop.settings.store import SettingsStore
from test_scoped_default_reset import SILICONFLOW_REFERENCE, CredentialBackend, _controller_with_saved_settings


class Saved(Exception):
    pass


def _import_elsewhere(tmp_path, *, key_entered):
    exported = _controller_with_saved_settings(tmp_path)[0].export_redacted()
    workspace, artifacts = tmp_path / "elsewhere" / "workspace", tmp_path / "elsewhere" / "artifacts"
    workspace.mkdir(parents=True)
    artifacts.mkdir()
    store = SettingsStore(tmp_path / "elsewhere" / "settings", default_factory=lambda: default_settings(
        workspace_root=str(workspace), artifact_root=str(artifacts)))
    elsewhere = SettingsController(store=store, credentials=CredentialReferenceManager(backend=CredentialBackend()),
                                   capabilities=CapabilityProjection([], revision="fixture"),
                                   directory_policy=DirectoryPolicy([tmp_path]))
    if key_entered:
        elsewhere.credential_create(provider="siliconflow", logical_key="primary", secret_input="another-key",
                                    expected_revision=store.load()["revision"])
    local = store.load()["settings"]["credential_references"]
    saved = []

    def save(settings, **kwargs):
        saved.append((settings, kwargs))
        raise Saved

    elsewhere._save = save
    with pytest.raises(Saved):
        elsewhere.import_redacted(exported, expected_revision=store.load()["revision"])
    [(settings, kwargs)] = saved
    assert kwargs["references_from_import"] is True
    assert [row["config_id"] for row in settings["model_services"]][-1] == "siliconflow-bge-m3"
    return settings["credential_references"], local


def test_where_the_key_was_entered_again_this_computers_reference_is_kept(tmp_path):
    # As in acceptance testing: the key is entered first (another time stamp), then the file is imported.
    imported, local = _import_elsewhere(tmp_path, key_entered=True)
    assert imported == local


def test_a_key_reference_this_computer_lacks_comes_in_as_missing(tmp_path):
    imported, local = _import_elsewhere(tmp_path, key_entered=False)
    [reference] = [row for row in imported if row["credential_ref"] == SILICONFLOW_REFERENCE]
    assert reference["status"] == "MISSING" and local == []
