from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
SMOKE = ATTEMPT_ROOT / "tests" / "browser_companion_dom_smoke.cjs"


def test_browser_companion_v3_indexes_then_captures_each_provider(tmp_path: Path) -> None:
    node = shutil.which("node")
    node_path = os.environ.get("NODE_PATH", "")
    if not node or not node_path:
        pytest.skip("Node.js Playwright runtime is not bound")

    receipt_path = tmp_path / "browser-companion-v3-dom-smoke.json"
    environment = {
        **os.environ,
        "Desktop_DOM_SMOKE_RECEIPT": str(receipt_path),
    }
    result = subprocess.run(
        [node, str(SMOKE)],
        cwd=ATTEMPT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["schema_version"] == "DesktopBrowserCompanionDomSmoke-v3"
    assert receipt["status"] == "PASS"
    assert {row["provider"] for row in receipt["receipts"]} == {
        "deepseek",
        "gemini",
        "kimi",
    }
    assert all(row["indexed_count"] == 2 for row in receipt["receipts"])
    assert all(row["captured_count"] == 2 for row in receipt["receipts"])
    assert all(row["message_count"] == 4 for row in receipt["receipts"])
    assert all(row["current_page_fallback"] is False for row in receipt["receipts"])
