"""Regression tests for the frozen RESEARCH_ANALYSIS Sonnet analysis profile."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
sys.path.insert(0, str(REPO_ROOT))

from research_analysis import analysis
from model_gateway.config import GatewayConfig


class AnalysisModelProfileTest(unittest.TestCase):
    def test_analysis_slot_uses_sonnet_fine_analysis_profile(self) -> None:
        config = GatewayConfig(REPO_ROOT / "model_gateway" / "models.yaml")
        slot = config.slot("analysis")

        self.assertTrue(slot.enabled)
        self.assertEqual(slot.provider, "anthropic")
        self.assertEqual(slot.model_id, "claude-sonnet-5")
        self.assertEqual(slot.api_key_env, "MEMORIVE_ANTHROPIC_KEY")
        self.assertEqual(slot.thinking_type, "adaptive")
        self.assertEqual(slot.effort, "xhigh")
        self.assertEqual(slot.max_tokens, 65536)

    def test_m4_payload_uses_slot_budget_and_keeps_timeout(self) -> None:
        payload = analysis.build_payload(
            type("Pack", (), {"blocks": ()})(),
            "test question",
        )

        self.assertNotIn("max_tokens", payload)
        self.assertEqual(payload["timeout"], 180)


if __name__ == "__main__":
    unittest.main(verbosity=2)
