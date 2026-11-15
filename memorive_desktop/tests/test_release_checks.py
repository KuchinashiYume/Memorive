"""The release checks in tools/ pass on the code as it is.

- check_internal_codes.py: no unregistered internal development code in the published files
  (it reads SOURCE_REVIEW_MANIFEST.json at the repository root, so it needs the whole repository);
- build_pages.py --check: the two UI pages are what their sources under ui/ generate, script hashes included
  (after editing a file under ui/, run the tool without --check); sync_page_hashes.py is its earlier name.
Each tool runs in its own isolated Python, the way a release does it.
"""
import subprocess
import sys
from pathlib import Path

import pytest

DESKTOP = Path(__file__).resolve().parents[1]
TOOLS = DESKTOP / "tools"


def _run(tool, *args):
    path = TOOLS / tool
    if not path.is_file():
        pytest.skip(f"{tool} is not in this version")
    done = subprocess.run([sys.executable, "-I", str(path), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=600)
    return done.returncode, (done.stdout + done.stderr)[-3000:]


def test_no_unregistered_internal_codes():
    if not (DESKTOP.parent / "SOURCE_REVIEW_MANIFEST.json").is_file():
        pytest.skip("needs the repository root (SOURCE_REVIEW_MANIFEST.json)")
    code, output = _run("check_internal_codes.py")
    assert code == 0, output


def test_pages_match_their_sources():
    code, output = _run("build_pages.py", "--check")
    assert code == 0, output


def test_earlier_tool_name_still_checks_the_pages():
    code, output = _run("sync_page_hashes.py", "--check")
    assert code == 0, output
