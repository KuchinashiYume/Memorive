"""Settings after the data folder was restored or moved: the workspace and artifact folders follow it.

After a backup was restored into a new data folder, Settings > Documents still showed the old data folder's
workspace and artifact paths (gone, or still there and opened by the file pickers). The paths recorded at
their default place inside the old data folder now point to the same place inside this one; a folder the user
chose elsewhere is kept.
"""
from __future__ import annotations

from memorive.desktop.settings.contracts import default_settings
from memorive.desktop.settings.store import SettingsStore, follow_moved_data_folder


def test_default_folders_follow_the_data_folder_and_chosen_ones_stay(tmp_path):
    old, new, chosen = tmp_path / "old" / "profile", tmp_path / "new" / "profile", tmp_path / "my-artifacts"
    for folder in (new / "workspace", new / "artifacts", chosen):
        folder.mkdir(parents=True)
    store = SettingsStore(new / "settings", default_factory=lambda: default_settings(
        workspace_root=str(old / "workspace"), artifact_root=str(chosen)))   # as restored from the old folder

    changed = follow_moved_data_folder(store, new, {"workspace_root": new / "workspace",
                                                    "artifact_root": new / "artifacts"})

    directories = store.load()["settings"]["directories"]
    assert changed == ["workspace_root"]
    assert (directories["workspace_root"], directories["artifact_root"]) == (str(new / "workspace"), str(chosen))
    assert follow_moved_data_folder(store, new, {"workspace_root": new / "workspace"}) == []   # nothing left to do
