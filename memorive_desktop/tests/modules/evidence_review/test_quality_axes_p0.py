"""quality-D tests for independent status axes and conservative numeric checks."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
sys.path.insert(0, str(REPO_ROOT))

from memorive.documents.card_review.quality_axes import needs_review
from memorive.documents.card_review.quality_axes import assess_numeric_plausibility, build_quality_axes, compare_numeric_facts
class QualityAxesP0Test(unittest.TestCase):
    def test_skipped_analysis_is_not_a_pipeline_failure(self) -> None:
        status = build_quality_axes(
            pipeline_status="completed",
            artifact_availability="skipped_by_policy",
            validation_status="not_run",
            extraction_coverage="not_assessed",
        )
        self.assertEqual(status["pipeline_status"], "completed")
        self.assertEqual(status["artifact_availability"], "skipped_by_policy")
        self.assertFalse(status["artifact_failed"])

    def test_unknown_axis_value_fails_loud(self) -> None:
        with self.assertRaisesRegex(ValueError, "validation_status"):
            build_quality_axes(
                pipeline_status="completed",
                artifact_availability="generated",
                validation_status="complete",
                extraction_coverage="sufficient",
            )

    def test_needs_review_is_local_to_credibility_or_manual_request(self) -> None:
        self.assertFalse(needs_review("verified"))
        self.assertTrue(needs_review("flagged"))
        self.assertTrue(needs_review("verified", manual_review_requested=True))

    def test_statistical_scope_difference_is_not_a_hard_conflict(self) -> None:
        minimum = {
            "metric": "retention_time",
            "value": 18,
            "unit": "d",
            "stat_type": "minimum",
            "time_window": None,
            "sample_scope": "reactors",
            "comparator": None,
            "condition_signature": {},
        }
        mean = {**minimum, "value": 24, "stat_type": "mean"}

        comparison = compare_numeric_facts(minimum, mean)

        self.assertEqual(comparison["status"], "not_directly_comparable")
        self.assertIn("stat_type", comparison["different_axes"])

    def test_plausibility_is_advisory_and_preserves_source_value(self) -> None:
        fact = {
            "metric": "SCOD",
            "value": 13648,
            "unit": "g/L",
            "source_text_value": "13648 ± 1800",
            "source_text_unit": "g/L",
        }

        result = assess_numeric_plausibility(
            fact,
            source_fidelity_status="matched",
        )

        self.assertEqual(result["source_fidelity_status"], "matched")
        self.assertEqual(result["plausibility_status"], "suspicious")
        self.assertEqual(result["source_text_value"], "13648 ± 1800")
        self.assertEqual(result["source_text_unit"], "g/L")
        self.assertTrue(result["do_not_autocorrect"])
        self.assertIn("EXTREME_MAGNITUDE_OR_UNIT", result["issue_codes"])
        self.assertEqual(result["severity"], "medium")
        self.assertEqual(
            result["triggered_rules"][0]["rule_id"],
            "EXTREME_MAGNITUDE_OR_UNIT",
        )
        self.assertIn("10000", result["triggered_rules"][0]["rule"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
