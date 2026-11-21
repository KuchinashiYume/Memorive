"""The release checks in tools/ pass on the code as it is.

- check_internal_codes.py: no unregistered internal development code in the published files
  (it reads SOURCE_REVIEW_MANIFEST.json at the repository root, so it needs the whole repository);
  in a PDF it reads link addresses and document information, also inside compressed objects;
- build_pages.py --check: the two UI pages are what their sources under ui/ generate, script hashes included
  (after editing a file under ui/, run the tool without --check); sync_page_hashes.py is its earlier name.
Each tool runs in its own isolated Python, the way a release does it.
"""
import importlib.util
import subprocess
import sys
import zlib
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


def test_a_pdf_link_to_a_local_file_is_found_inside_a_compressed_object(tmp_path):
    # The design PDFs, printed from a local page, linked their figures to file:///<the machine's folders>/...,
    # and the check did not read PDFs at all. Such strings can also sit in a compressed object stream.
    spec = importlib.util.spec_from_file_location("check_internal_codes", TOOLS / "check_internal_codes.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    hidden = zlib.compress(b"<< /S /URI /URI (file:///C:/Users/someone/help/figure-01.svg) >>")
    title = b"<FEFF" + "设计".encode("utf-16-be").hex().upper().encode() + b">"
    pdf = tmp_path / "design.pdf"
    pdf.write_bytes(b"%PDF-1.4\n1 0 obj << /Title " + title + b" >> endobj\n"
                    b"2 0 obj << /Type /ObjStm /Filter /FlateDecode >>\nstream\n" + hidden + b"\nendstream\nendobj\n")
    lines = module.pdf_strings(pdf)
    assert "/Title 设计" in lines
    assert [line for line in lines if module.LOCAL_LINK.search(line)] == [
        "/URI file:///C:/Users/someone/help/figure-01.svg"]


def test_pages_match_their_sources():
    code, output = _run("build_pages.py", "--check")
    assert code == 0, output


def test_earlier_tool_name_still_checks_the_pages():
    code, output = _run("sync_page_hashes.py", "--check")
    assert code == 0, output


def test_the_native_windows_read_the_type_rule_of_the_built_page():
    # The installer and update window take their faces and sizes from the main window's type rule. When the
    # sizes moved into typography.css, the build of the native engine still looked for the old inline ones
    # and stopped, so no installer could be built.
    spec = importlib.util.spec_from_file_location("build_update_helper", TOOLS / "build_update_helper.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    contract = module.typography_contract(DESKTOP)
    assert contract["sizes"]["page"] > contract["sizes"]["body"] > contract["sizes"]["small"] > 0
    assert set(contract["weights"]) == set(contract["sizes"])
    for language in ("zh-CN", "en-US", "ja-JP"):
        assert contract["stacks"][language]["heading"] and contract["stacks"][language]["body"]
    assert contract["stacks"]["ja-JP"]["body"][1] == "Yu Gothic" and contract["code"]
