"""Regression tests for the EVIDENCE_REVIEW fine-verification model profile."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
sys.path.insert(0, str(REPO_ROOT))

from evidence_review import transcription
from memorive.models.gateway.config import GatewayConfig


class VerifyModelProfileTest(unittest.TestCase):
    def test_verify_slot_uses_sonnet_two_pass_batch_profile(self) -> None:
        config = GatewayConfig(REPO_ROOT / "memorive" / "models" / "gateway" / "models.yaml")
        slot = config.slot("verify")

        self.assertTrue(slot.enabled)
        self.assertEqual(slot.provider, "anthropic")
        self.assertEqual(slot.model_id, "claude-sonnet-5")
        self.assertEqual(slot.thinking_type, "adaptive")
        self.assertEqual(slot.effort, "low")
        self.assertGreaterEqual(slot.max_tokens or 0, 16384)

    def test_default_verify_call_uses_fine_verification_budget(self) -> None:
        with patch("memorive.models.gateway.call") as mocked_call:
            mocked_call.return_value = {"text": '{"violations":[]}'}
            result = transcription._default_call_verify("verify prompt")

        self.assertEqual(result, '{"violations":[]}')
        task_type, payload = mocked_call.call_args.args
        self.assertEqual(task_type, "verify")
        self.assertGreaterEqual(payload["max_tokens"], 16384)
        self.assertGreaterEqual(payload["timeout"], 120)


if __name__ == "__main__":
    unittest.main(verbosity=2)
