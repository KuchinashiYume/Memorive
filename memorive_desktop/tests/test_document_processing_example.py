"""The document processing example (examples/document_processing.py) turns a PDF into an anchored card.

It runs as its own process, as a developer would run it: the example points the document folders at its
work folder through environment variables, which must not leak into the other tests.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "document_processing.py"


def test_the_example_turns_a_pdf_into_an_anchored_card(tmp_path):
    work = tmp_path / "work"
    done = subprocess.run([sys.executable, str(EXAMPLE), str(work)], capture_output=True, timeout=600,
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    stdout = done.stdout.decode("utf-8")
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")[-2000:]
    card_path = next((work / "library").rglob("[[]Card[]]*.md"))
    kinds = sorted(path.name.split("]")[0] + "]" for path in card_path.parent.iterdir())
    assert kinds == ["[Card]", "[Chunks]", "[CleanMD]", "[PDF]", "[RawMD]"]
    card = yaml.safe_load(card_path.read_text(encoding="utf-8").split("---", 2)[1])
    assert card["key_results"].startswith("Growth at 37 C was twice the growth at 25 C.")
    [anchor] = card["source_anchor"]["by_field"]["key_results"]
    assert anchor["chunk_id"] == "DOC_EXAMPLE0001#c0003"
    assert [(item["value"], item["unit"]) for item in card["key_data"]] == [("2", "fold")]
    assert "model calls: core fields, key data, extra blocks" in stdout
