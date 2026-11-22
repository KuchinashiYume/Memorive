"""Settings after the data folder was restored or moved: the workspace and artifact folders follow it.

After a backup was restored into a new data folder, Settings > Documents still showed the old data folder's
workspace and artifact paths (gone, or still there and opened by the file pickers). The paths recorded at
their default place inside an earlier data folder now point to the same place inside this one. A folder the
user chose is kept, also one whose name happens to be workspace or artifacts.
"""
from __future__ import annotations

from memorive.desktop.settings.contracts import default_settings
from memorive.desktop.settings.store import SettingsStore, follow_moved_data_folder


def _start(tmp_path, workspace, artifacts):
    """This data folder (new/profile) starting with these recorded folders; returns what changed and the result."""
    new = tmp_path / "new" / "profile"
    for folder in (new / "workspace", new / "artifacts"):
        folder.mkdir(parents=True, exist_ok=True)
    store = SettingsStore(new / "settings", default_factory=lambda: default_settings(
        workspace_root=str(workspace), artifact_root=str(artifacts)))   # as restored from elsewhere
    own = {"workspace_root": new / "workspace", "artifact_root": new / "artifacts"}
    changed = follow_moved_data_folder(store, new, own)
    directories = store.load()["settings"]["directories"]
    return changed, (directories["workspace_root"], directories["artifact_root"]), own


def test_an_old_data_folder_still_there_is_followed_and_a_chosen_folder_stays(tmp_path):
    old, chosen = tmp_path / "old" / "profile", tmp_path / "my-artifacts"
    chosen.mkdir()
    (old / "settings").mkdir(parents=True)
    (old / "settings" / "settings.json").write_text("{}", encoding="utf-8")   # every data folder keeps one here

    changed, directories, own = _start(tmp_path, old / "workspace", chosen)

    assert changed == ["workspace_root"]
    assert directories == (str(own["workspace_root"]), str(chosen))


def test_a_gone_data_folder_is_followed_when_both_folders_were_its_defaults(tmp_path):
    old = tmp_path / "old" / "profile"                                       # restored on another computer

    changed, directories, own = _start(tmp_path, old / "workspace", old / "artifacts")

    assert changed == ["workspace_root", "artifact_root"]
    assert directories == (str(own["workspace_root"]), str(own["artifact_root"]))


def test_a_chosen_folder_named_workspace_stays(tmp_path):
    chosen = tmp_path / "研究" / "workspace"
    chosen.mkdir(parents=True)
    new_artifacts = tmp_path / "new" / "profile" / "artifacts"

    changed, directories, _ = _start(tmp_path, chosen, new_artifacts)

    assert changed == [] and directories == (str(chosen), str(new_artifacts))


def test_chosen_folders_named_like_a_data_folder_stay(tmp_path):
    research = tmp_path / "研究"                                              # no settings file: not a data folder
    for name in ("workspace", "artifacts"):
        (research / name).mkdir(parents=True)

    changed, directories, _ = _start(tmp_path, research / "workspace", research / "artifacts")

    assert changed == [] and directories == (str(research / "workspace"), str(research / "artifacts"))


def test_a_chosen_folder_on_a_missing_drive_stays(tmp_path):
    missing = tmp_path / "unplugged" / "workspace"                         # gone, and artifacts is not beside it
    new_artifacts = tmp_path / "new" / "profile" / "artifacts"

    changed, directories, _ = _start(tmp_path, missing, new_artifacts)

    assert changed == [] and directories == (str(missing), str(new_artifacts))
