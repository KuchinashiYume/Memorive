"""A library root chosen through a folder link (Windows junction), as research evidence reads it
(memorive.research.workspace.evidence.safe_path and the library index).

A file named through the same link as its root is read from the real folder; a file outside the
root and a link inside it are still refused.
"""
from __future__ import annotations

import hashlib
import os
import subprocess

import pytest

from memorive.research.workspace.evidence import safe_path
from memorive.research.workspace.service import ResearchWorkspace

pytestmark = pytest.mark.skipif(os.name != "nt", reason="folder junctions are a Windows feature")


def junction(link, target):
    done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    assert done.returncode == 0, done.stderr


@pytest.fixture
def linked(tmp_path):
    real = tmp_path / "real"
    (real / "papers").mkdir(parents=True)
    (real / "papers" / "a.md").write_text("# Growth\n\nGrowth at 37 C was twice the growth at 25 C.\n", encoding="utf-8")
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "b.md").write_text("elsewhere", encoding="utf-8")
    junction(tmp_path / "alias", real)
    return tmp_path


def test_a_file_named_through_the_same_link_as_its_root_is_read_from_the_real_folder(linked):
    found = safe_path(linked / "alias", linked / "alias" / "papers" / "a.md")
    assert found == (linked / "real" / "papers" / "a.md").resolve()


def test_a_file_outside_a_linked_root_is_still_refused(linked):
    with pytest.raises(ValueError, match="SOURCE_OUTSIDE_VAULT"):
        safe_path(linked / "alias", linked / "outside" / "b.md")


def test_a_link_inside_a_linked_root_is_still_refused(linked):
    junction(linked / "real" / "papers" / "inner", linked / "outside")
    with pytest.raises(ValueError, match="SOURCE_LINK_REJECTED"):
        safe_path(linked / "alias", linked / "alias" / "papers" / "inner" / "b.md")


def test_the_library_index_takes_a_paper_from_a_root_chosen_through_a_link(linked):
    workspace = ResearchWorkspace(linked / "workspace")
    try:
        paper = linked / "alias" / "papers" / "a.md"
        row = workspace.index.add_library_file(
            "default", path=paper, root=linked / "alias", external_id="a",
            expected_hash=hashlib.sha256(paper.read_bytes()).hexdigest(), title="Growth", material_layer="cleaned")
        assert row["id"] and row["title"] == "Growth"
    finally:
        workspace.close()
