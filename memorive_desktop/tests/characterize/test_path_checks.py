"""Which file paths the research evidence reader and the inbox import accept.

Pins today's behaviour of memorive_research_workspace.evidence.safe_path and
memorive.documents.inbox.path_policy.PathSecurityPolicy.inspect, including folder junctions, before
any change to how links are handled. The folder layout is built in tmp_path; two junctions
point one folder inside and one outside the allowed root (Windows only: mklink /J needs no
administrator rights). Paths are shown relative to tmp_path.

safe_path reads each level's own attributes, so a file reached through a junction is refused
wherever the junction points. The inbox check still looks for reparse points on the resolved
\\?\ form of the path (windows_io_path), where Windows does not report a folder junction, so it
accepts a file reached through a junction whose real location is inside the allowed root.

load_bundle resolves the folder of a memo-source manifest before safe_path compares it with the
resolved root, so a bundle in a folder reached through a junction or named by a short (8.3)
path loads.
"""
import ctypes
import json
import os
import subprocess
from pathlib import Path

import pytest

from memorive.documents.inbox.path_policy import PathSecurityPolicy
from memorive_research_workspace.evidence import safe_path
from memorive_research_workspace.skill_bridge import load_bundle
from memorive_research_workspace.store import digest
from tests.characterize.golden import check, outcome

HERE = Path(__file__).parent
pytestmark = pytest.mark.skipif(os.name != "nt", reason="folder junctions are a Windows feature")


def _junction(link, target):
    done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    assert done.returncode == 0, done.stderr


@pytest.fixture
def tree(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    for folder in (root / "docs", root / ".git", root / "node_modules", outside):
        folder.mkdir(parents=True)
    (root / "docs" / "a.md").write_text("alpha", encoding="utf8")
    (root / "docs" / "CON.md").write_text("reserved", encoding="utf8")
    (root / ".git" / "b.md").write_text("private", encoding="utf8")
    (root / "node_modules" / "c.md").write_text("vendored", encoding="utf8")
    (root / "empty.md").write_bytes(b"")
    (root / "tool.exe").write_bytes(b"MZ")
    (outside / "d.md").write_text("delta", encoding="utf8")
    _junction(root / "linked_out", outside)
    _junction(root / "linked_in", root / "docs")
    return tmp_path


def _show(value, base):
    text = str(value).replace(str(base.resolve()), "<tmp>").replace(str(base), "<tmp>")
    return text.replace("\\", "/")


def _clean(result, base):
    if isinstance(result, dict):
        if result.get("error") in ("FileNotFoundError", "OSError", "PermissionError"):
            return {"error": result["error"]}  # the message is in the system language
        return {k: _clean(v, base) for k, v in sorted(result.items()) if k not in ("free_bytes", "mtime_ns")}
    if isinstance(result, list):
        return [_clean(v, base) for v in result]
    if isinstance(result, (str, Path)):
        return _show(result, base)
    return result


PATHS = {
    "plain_file": "root/docs/a.md",
    "private_git_folder": "root/.git/b.md",
    "node_modules": "root/node_modules/c.md",
    "outside_root": "outside/d.md",
    "through_junction_to_outside": "root/linked_out/d.md",
    "through_junction_inside": "root/linked_in/a.md",
    "junction_itself": "root/linked_in",
    "missing_file": "root/docs/missing.md",
    "empty_file": "root/empty.md",
    "unsupported_type": "root/tool.exe",
    "reserved_name": "root/docs/CON.md",
    "dot_dot": "root/docs/../docs/a.md",
}


def test_research_evidence_safe_path(tree):
    root = tree / "root"
    cases = {name: _clean(outcome(safe_path, root, tree / rel if ".." not in rel else str(tree) + "/" + rel), tree)
             for name, rel in PATHS.items()}
    cases["root_missing"] = _clean(outcome(safe_path, tree / "nowhere", tree / "root/docs/a.md"), tree)
    check("path_checks_research_evidence", cases, HERE)


def test_inbox_import_inspect(tree):
    policy = PathSecurityPolicy([tree / "root"], tree / "destination")
    cases = {name: _clean(outcome(policy.inspect, str(tree / rel) if ".." not in rel else str(tree) + "/" + rel), tree)
             for name, rel in PATHS.items()}
    cases["device_path"] = _clean(outcome(policy.inspect, "\\\\.\\C:\\x.md"), tree)
    cases["allowlist_includes_outside"] = _clean(outcome(
        PathSecurityPolicy([tree / "root", tree / "outside"], tree / "destination").inspect, str(tree / "root/linked_out/d.md")), tree)
    check("path_checks_inbox_import", cases, HERE)


def _bundle(folder):
    """A memo-source bundle of one Markdown file; returns its manifest."""
    folder.mkdir(parents=True)
    text = b"# Title\nOne line of text.\n"
    (folder / "paper.md").write_bytes(text)
    (folder / "paper.raw.md").write_bytes(text)
    sha = digest(text)
    manifest = {"schema": "memo-source/1", "source_sha256": sha,
                "original": {"path": "paper.md", "sha256": sha}, "raw": {"path": "paper.raw.md", "sha256": sha},
                "converter": {"name": "fixture"}, "warnings": [], "pages": [{"page": None, "line_start": 1, "line_end": 2}]}
    (folder / "memo-source.json").write_text(json.dumps(manifest), encoding="utf8")
    return folder / "memo-source.json"


def test_a_bundle_in_a_folder_reached_through_a_junction_loads(tmp_path):
    manifest = _bundle(tmp_path / "real" / "bundle")
    _junction(tmp_path / "alias", tmp_path / "real")
    _value, files, _lines = load_bundle(tmp_path / "alias" / "bundle" / "memo-source.json")
    assert files["raw"][0] == manifest.parent.resolve() / "paper.raw.md"


def test_a_bundle_named_by_a_short_path_loads(tmp_path):
    folder = tmp_path / "a folder with a long name"
    manifest = _bundle(folder / "bundle")
    buffer = ctypes.create_unicode_buffer(1024)
    ctypes.windll.kernel32.GetShortPathNameW(str(folder), buffer, len(buffer))
    if not buffer.value or Path(buffer.value).name == folder.name:
        pytest.skip("this volume makes no 8.3 names")
    _value, files, _lines = load_bundle(Path(buffer.value) / "bundle" / "memo-source.json")
    assert files["original"][0] == manifest.parent.resolve() / "paper.md"
