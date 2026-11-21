"""A conversation's material and the files added to it in the window
(memorive/research/workspace/attachments.py). Pictures read by the vision model are covered in
test_workspace_vision_and_handoffs.py; everything here is text and runs in a temporary folder.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from memorive.research.workspace.service import ResearchWorkspace
from memorive.research.workspace.store import digest

NOTE = "\n".join(f"{n:03d} Growth of strain K12 doubled between 25 C and 37 C in every repeat." for n in range(1, 41))


@pytest.fixture
def workspace(tmp_path):
    opened = ResearchWorkspace(tmp_path / "workspace", catalog=lambda: [])
    yield opened
    opened.close()


def finish(workspace):
    for future in list(workspace.futures.values()):
        future.result(timeout=60)


def write(folder: Path, name: str, data: bytes) -> Path:
    folder.mkdir(exist_ok=True)
    path = folder / name
    path.write_bytes(data)
    return path


def attach(workspace, thread, *paths):
    job = workspace.attachments.attach(thread["id"], [str(p) for p in paths])
    finish(workspace)
    return workspace.attachments.status(job["id"])


def test_a_text_file_becomes_material_of_the_conversation(workspace, tmp_path):
    thread = workspace.thread_create()
    note = write(tmp_path / "files", "note.md", NOTE.encode("utf-8"))
    job = attach(workspace, thread, note)
    [item] = job["items"]
    assert (job["status"], item["status"]) == ("COMPLETE", "READY")
    artifact = workspace.store.get("artifact", item["id"])
    assert (artifact["state"], artifact["kind"], artifact["chunks"]) == ("active", "attachment", 2)
    assert artifact["content_hash"] == hashlib.sha256(note.read_bytes()).hexdigest()
    # Saved citations find an excerpt by its id, so the way ids are made must not change.
    first = "\n".join(NOTE.split("\n")[:25])
    expected_id = "ev_" + digest([item["id"], artifact["content_hash"], None, 1, 25, digest(first)])[:32]
    assert workspace.index.read(expected_id, thread["project"])["text"] == first
    current = workspace.store.get("thread", thread["id"])
    assert item["id"] in current["artifact_ids"]
    assert item["id"] in workspace.attachments.scope_ids(current)
    preview = workspace.attachments.preview(thread["project"], item["id"])
    assert preview["evidence"][0]["text"].startswith("001 Growth")


def test_each_file_gets_its_own_result(workspace, tmp_path):
    thread = workspace.thread_create()
    files = tmp_path / "files"
    data = json.dumps({"claim": "growth doubles"}).encode("utf-8")
    job = attach(workspace, thread, write(files, "card.json", data), write(files, "empty.txt", b""),
                 write(files, "slides.pptx", b"x"), tmp_path / "missing.md")
    by_name = {item["name"]: item for item in job["items"]}
    assert by_name["card.json"]["status"] == "READY"
    assert by_name["empty.txt"]["error"] == "ATTACHMENT_EMPTY"
    assert by_name["slides.pptx"]["error"] == "ATTACHMENT_FORMAT_UNSUPPORTED"
    assert by_name["missing.md"]["error"] == "ATTACHMENT_PATH_INVALID"
    assert job["status"] == "PARTIAL"
    with pytest.raises(ValueError, match="ATTACHMENT_FILES_REQUIRED"):
        workspace.attachments.attach(thread["id"], [])


def test_the_same_file_is_copied_once_and_the_copy_must_hold(workspace, tmp_path):
    thread = workspace.thread_create()
    note = write(tmp_path / "files", "note.md", NOTE.encode("utf-8"))
    first = attach(workspace, thread, note)["items"][0]
    again = attach(workspace, thread, note)["items"][0]
    assert again["id"] == first["id"] and again["status"] == "READY"
    copies = [p for p in (workspace.store.root / "attachments").iterdir() if p.suffix == ".md"]
    assert len(copies) == 1
    copies[0].write_bytes(b"changed on disk")
    assert attach(workspace, thread, note)["items"][0]["error"] == "ATTACHMENT_SNAPSHOT_CONFLICT"


def test_the_material_scope_of_a_conversation(workspace, tmp_path):
    thread = workspace.thread_create()
    files = tmp_path / "files"
    a = attach(workspace, thread, write(files, "a.md", b"first note"))["items"][0]["id"]
    b = attach(workspace, thread, write(files, "b.md", b"second note"))["items"][0]["id"]
    other = workspace.project_save("Other")["id"]
    library = write(tmp_path / "library", "other.md", b"other project")
    foreign = workspace.index.add_library_file(other, path=library, root=library.parent, external_id="other",
                                               expected_hash=hashlib.sha256(b"other project").hexdigest(), title="Other")["id"]
    current = workspace.store.get("thread", thread["id"])
    with pytest.raises(ValueError, match="MATERIAL_OUTSIDE_PROJECT"):
        workspace.attachments.configure(thread["id"], current["revision"], {"artifact_ids": [a, foreign]})
    # Choosing only a: b stays out of the conversation even though the project includes it.
    current = workspace.attachments.configure(thread["id"], current["revision"], {"artifact_ids": [a]})
    assert current["excluded_artifact_ids"] == [b]
    assert workspace.attachments.scope_ids(current) == [a]
    current = workspace.attachments.configure(thread["id"], current["revision"], {"include_project": False})
    assert (current["scope"], workspace.attachments.scope_ids(current)) == ("conversation", [a])
    with pytest.raises(ValueError, match="MATERIAL_SELECTION_INVALID"):
        workspace.attachments.remove(thread["id"], current["revision"], [])
    with pytest.raises(ValueError, match="MATERIAL_OUTSIDE_PROJECT"):
        workspace.attachments.remove(thread["id"], current["revision"], [foreign])
    current = workspace.attachments.remove(thread["id"], current["revision"], [a])
    assert workspace.attachments.scope_ids(current) == []
    assert workspace.store.get("artifact", a)["state"] == "active"  # removed from the conversation only
    with pytest.raises(ValueError, match="MATERIAL_OUTSIDE_PROJECT"):
        workspace.attachments.preview(thread["project"], foreign)


def _move(tmp_path):
    """The data folder copied to another place as a whole, the old one gone (a restored backup)."""
    shutil.copytree(tmp_path / "old", tmp_path / "moved")
    shutil.rmtree(tmp_path / "old")
    return ResearchWorkspace(tmp_path / "moved" / "workspace", catalog=lambda: [])


def test_material_is_read_after_the_data_folder_moved(tmp_path):
    # The citation of a file added to a conversation used to fail with EVIDENCE_OUTSIDE_WORKSPACE once the data
    # folder had moved: the recorded copy was outside the workspace's new folder.
    opened = ResearchWorkspace(tmp_path / "old" / "workspace", catalog=lambda: [])
    thread = opened.thread_create()
    note = write(tmp_path / "files", "note.md", NOTE.encode("utf-8"))
    [item] = attach(opened, thread, note)["items"]
    first = opened.index.read(opened.attachments.preview(thread["project"], item["id"])["evidence"][0]["id"],
                              thread["project"])
    opened.close()
    moved = _move(tmp_path)
    try:
        assert moved.index.read(first["id"], thread["project"])["text"] == first["text"]
    finally:
        moved.close()
