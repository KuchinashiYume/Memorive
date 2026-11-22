"""A CLI verification that fails because another model answered names the models the CLI reported.

Claude Code accepts an alias such as "haiku"; when the reply came from another model, the verification
failed with CLI_RETURNED_MODEL_MISMATCH and the page said only that the models differ, so the user could
not tell which ID to enter. The reported models now come with the result (and stay a mismatch).
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from memorive.models.validation.runner import LiveModelValidationRunner

REPLY = "\n".join(json.dumps(row) for row in [
    {"type": "system", "subtype": "init", "model": "claude-haiku-5-5"},
    {"type": "result", "status": "success", "result": "PING", "model": "claude-sonnet-5-5"},
]).encode()


class _Transport:
    def run_verification(self, **options):
        return SimpleNamespace(started=True, duration_ms=5, timed_out=False, output_truncated=False,
                               returncode=0, stdout=REPLY, stderr=b""), {}


def test_a_mismatch_names_the_models_the_cli_reported(tmp_path):
    (tmp_path / "claude.exe").write_bytes(b"stand-in")
    runner = LiveModelValidationRunner(
        credential_resolver=lambda _reference: b"",
        preferences_loader=lambda: {"proxy_mode": "NONE", "proxy_address": "", "request_timeout_seconds": 30},
        scratch_root=tmp_path / "transport", process_transport=_Transport(),
        executable_resolver=lambda value: str(tmp_path / "claude.exe"))
    result = runner.verify_cli_model({"adapter_id": "claude_code", "executable": "claude"},
                                     {"model_name": "haiku", "thinking_mode": ""})
    assert (result["status"], result["reason"]) == ("INVALID", "CLI_RETURNED_MODEL_MISMATCH")
    assert result["available_models"] == ["claude-haiku-5-5", "claude-sonnet-5-5"]
