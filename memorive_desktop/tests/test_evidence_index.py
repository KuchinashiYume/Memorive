"""The evidence index (memorive/research/workspace/evidence.py): documents become numbered excerpts,
and an excerpt is read only while the file it came from is unchanged.

Material comes in two ways: a file added from the library (add_library_file, kept as a snapshot
under the workspace) and the files of a project's own folder (refresh). Everything here is written
in a temporary folder.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from memorive.research.workspace.service import ResearchWorkspace
from memorive.research.workspace.store import digest

LINE = "Growth of strain K12 was measured every hour at 25 C, 30 C and 37 C in the same medium."
PAPER = "\n".join(f"{number:03d} {LINE}" for number in range(1, 61))  # about 5,500 characters


@pytest.fixture
def workspace(tmp_path):
    opened = ResearchWorkspace(tmp_path / "workspace", catalog=lambda: [])
    yield opened
    opened.close()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def add_paper(workspace, folder: Path, name="k12.md", text=PAPER, external_id="k12"):
    folder.mkdir(exist_ok=True)
    paper = folder / name
    paper.write_bytes(text.encode("utf-8"))
    return workspace.index.add_library_file(
        "default", path=paper, root=folder, external_id=external_id,
        expected_hash=sha256(paper.read_bytes()), title="K12", material_layer="cleaned")


def excerpts(workspace, artifact_id):
    with workspace.store.tx() as db:
        rows = db.execute("SELECT * FROM chunks WHERE artifact_id=? ORDER BY line_start", (artifact_id,)).fetchall()
    return [dict(row) for row in rows]


def snapshots(workspace):
    return sorted((workspace.store.root / "library_snapshots").iterdir())


def test_a_library_file_becomes_excerpts_of_whole_lines(workspace, tmp_path):
    material = add_paper(workspace, tmp_path / "library")
    rows = excerpts(workspace, material["id"])
    assert material["chunks"] == len(rows) == 3  # an excerpt closes once it reaches 1,800 characters
    lines = PAPER.split("\n")
    assert [(row["line_start"], row["line_end"]) for row in rows] == [(1, 20), (21, 40), (41, 60)]
    for row in rows:
        assert row["text"] == "\n".join(lines[row["line_start"] - 1:row["line_end"]])
        assert row["hash"] == digest(row["text"])  # recorded with the excerpt; nothing checks it on reading
    # The workspace keeps its own copy, named by the content's hash.
    [snapshot] = snapshots(workspace)
    assert snapshot.name == sha256(PAPER.encode("utf-8")) + ".md" and material["path"] == str(snapshot)


def test_an_excerpt_is_read_with_its_place_in_the_document(workspace, tmp_path):
    material = add_paper(workspace, tmp_path / "library")
    first = excerpts(workspace, material["id"])[0]
    excerpt = workspace.index.read(first["id"], "default", expected_hash=material["content_hash"])
    assert (excerpt["artifact_id"], excerpt["line_start"], excerpt["line_end"]) == (material["id"], 1, 20)
    assert excerpt["text"] == first["text"] and excerpt["material_layer"] == "cleaned"
    with pytest.raises(ValueError, match="EVIDENCE_VERSION_CONFLICT"):
        workspace.index.read(first["id"], "default", expected_hash="0" * 64)



def test_the_page_markers_of_a_markdown_file_give_its_excerpts_their_pages(workspace, tmp_path):
    # Document processing writes <!-- memorive:page:N --> ahead of page N's lines. A marker ends an
    # excerpt and stays out of its text; lines are still counted through the file. The excerpts used to
    # be one page-less excerpt with the markers in its text.
    text = "\n".join(["# K12", "<!-- memorive:page:8 -->", "Growth at 25 C.", "Growth at 30 C.",
                      "<!-- memorive:page:9 -->", "Growth at 37 C."])
    material = add_paper(workspace, tmp_path / "library", text=text)
    rows = excerpts(workspace, material["id"])
    assert [(row["page"], row["line_start"], row["line_end"], row["text"]) for row in rows] == [
        (None, 1, 1, "# K12"), (8, 3, 4, "Growth at 25 C.\nGrowth at 30 C."), (9, 6, 6, "Growth at 37 C.")]
    excerpt = workspace.index.read(rows[1]["id"], "default")
    assert (excerpt["page"], excerpt["line_start"], excerpt["line_end"]) == (8, 3, 4)

def test_an_excerpt_of_a_changed_file_is_not_read(workspace, tmp_path):
    material = add_paper(workspace, tmp_path / "library")
    first = excerpts(workspace, material["id"])[0]
    [snapshot] = snapshots(workspace)
    snapshot.write_bytes(b"changed after it was indexed")
    with pytest.raises(ValueError, match="EVIDENCE_STALE"):
        workspace.index.read(first["id"], "default")


def test_adding_the_same_file_again_checks_the_snapshot_already_on_disk(workspace, tmp_path):
    add_paper(workspace, tmp_path / "library")
    add_paper(workspace, tmp_path / "library", external_id="k12-again")
    assert len(snapshots(workspace)) == 1  # one copy per content
    [snapshot] = snapshots(workspace)
    snapshot.write_bytes(b"replaced on disk")
    with pytest.raises(ValueError, match="LIBRARY_SOURCE_HASH_MISMATCH"):
        add_paper(workspace, tmp_path / "library", external_id="k12-third")


def test_a_project_folder_is_indexed_without_private_folders_or_settings(workspace, tmp_path):
    vault = tmp_path / "vault"
    (vault / "notes").mkdir(parents=True)
    (vault / "notes" / "k12.md").write_bytes(PAPER.encode("utf-8"))
    (vault / "notes" / "k12.card.json").write_bytes(b'{"claim": "growth doubles"}')
    (vault / "settings.json").write_bytes(b'{"token": "not for the index"}')
    for private in (".git", "node_modules", ".obsidian"):
        (vault / private).mkdir()
        (vault / private / "inside.md").write_bytes(b"never indexed")
    project = workspace.project_save("Vault", vault=str(vault))["id"]
    result = workspace.index.refresh(project)
    assert (result["status"], result["indexed"], result["failures"]) == ("PASS", 2, [])
    sources = {a["title"]: a for a in workspace.store.list("artifact", project)}
    assert set(sources) == {"k12", "k12.card"}
    rows = excerpts(workspace, sources["k12"]["id"])
    assert [(row["line_start"], row["line_end"]) for row in rows] == [(1, 20), (21, 40), (41, 60)]
    # An edited note is stale until the folder is indexed again.
    (vault / "notes" / "k12.md").write_bytes(b"edited")
    with pytest.raises(ValueError, match="EVIDENCE_STALE"):
        workspace.index.read(rows[0]["id"], project)
    workspace.index.refresh(project)
    [row] = excerpts(workspace, sources["k12"]["id"])
    assert workspace.index.read(row["id"], project)["text"] == "edited"


def _move(tmp_path):
    """The data folder copied to another place as a whole, the old one gone (a restored backup)."""
    shutil.copytree(tmp_path / "old", tmp_path / "moved")
    shutil.rmtree(tmp_path / "old")
    return ResearchWorkspace(tmp_path / "moved" / "workspace", catalog=lambda: [])


def test_an_excerpt_is_read_after_the_data_folder_moved(tmp_path):
    # Records keep absolute paths. The workspace's copy of a library file used to be looked for at the old place,
    # so no excerpt of it could be read after the data folder moved.
    opened = ResearchWorkspace(tmp_path / "old" / "workspace", catalog=lambda: [])
    material = add_paper(opened, tmp_path / "library")
    first = excerpts(opened, material["id"])[0]
    opened.close()
    moved = _move(tmp_path)
    try:
        excerpt = moved.index.read(first["id"], "default", expected_hash=material["content_hash"])
        assert excerpt["text"] == first["text"]
        assert Path(excerpt["path"]).is_relative_to(moved.store.root)
    finally:
        moved.close()
