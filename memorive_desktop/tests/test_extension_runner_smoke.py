from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
SMOKE = ATTEMPT_ROOT / "tests" / "browser_companion_extension_runner_smoke.cjs"


def test_extension_runner_syncs_all_three_providers_in_one_persistent_job(
    tmp_path: Path,
) -> None:
    node = shutil.which("node")
    node_path = os.environ.get("NODE_PATH", "")
    if not node or not node_path:
        pytest.skip("Node.js Playwright runtime is not bound")

    receipt_path = tmp_path / "browser-companion-extension-runner-smoke.json"
    result = subprocess.run(
        [node, str(SMOKE)],
        cwd=ATTEMPT_ROOT,
        env={
            **os.environ,
            "Desktop_EXTENSION_RUNNER_RECEIPT": str(receipt_path),
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["schema_version"] == "DesktopBrowserCompanionExtensionRunnerSmoke-v1"
    assert receipt["extension_version"] == "3.1.0"
    assert receipt["provider_count"] == 3
    assert receipt["captured_count"] == 6
    assert receipt["failed_count"] == 0
    assert receipt["current_page_fallback"] is False
    assert receipt["cookie_value_reads"] == 0
    assert receipt["browser_storage_value_reads"] == 0
    assert receipt["hidden_provider_api_calls"] == 0
    assert receipt["status"] == "PASS"
