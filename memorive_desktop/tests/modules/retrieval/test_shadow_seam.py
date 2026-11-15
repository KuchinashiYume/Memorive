from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from retrieval.shadow_seam import preserve_baseline_with_shadow_observation
class ShadowSeamTests(unittest.TestCase):
    @staticmethod
    def _projection() -> dict:
        return {
            "candidate_output_ref": "sandbox://candidate/observed.json",
            "candidate_output_sha256": "A" * 64,
            "structural_only": True,
            "semantic_B_eligible": False,
            "field_semantic_retrieval_quality": "NOT_ASSESSED",
            "external_calls": 0,
            "local_model_calls": 0,
            "provider_tokens": 0,
            "cost_cny": 0,
            "production_mutations": 0,
            "git_mutations": 0,
        }

    def test_default_off_returns_same_object_and_never_invokes_observer(self) -> None:
        baseline = {"baseline": [1, 2, 3]}

        def forbidden_observer():
            raise AssertionError("observer must not be called")

        result = preserve_baseline_with_shadow_observation(
            baseline, observer=forbidden_observer
        )
        self.assertIs(result, baseline)

    def test_enabled_observation_returns_same_baseline_and_emits_non_acceptance_receipt(self) -> None:
        baseline = object()
        with tempfile.TemporaryDirectory() as temporary:
            receipt_path = Path(temporary) / "receipt.json"
            result = preserve_baseline_with_shadow_observation(
                baseline,
                shadow_enabled=True,
                observer=self._projection,
                receipt_path=receipt_path,
                allowed_receipt_root=temporary,
            )
            self.assertIs(result, baseline)
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt["verification_result"], "PASS")
            self.assertEqual(receipt["acceptance_verdict"], "NOT_ASSESSED")
            self.assertFalse(receipt["semantic_B_eligible"])

    def test_observer_error_and_sink_error_never_replace_baseline(self) -> None:
        baseline = ["baseline"]

        def observer():
            raise RuntimeError("fixture failure")

        with tempfile.TemporaryDirectory() as temporary:
            receipt_path = Path(temporary) / "receipt.json"
            result = preserve_baseline_with_shadow_observation(
                baseline,
                shadow_enabled=True,
                observer=observer,
                receipt_path=receipt_path,
                allowed_receipt_root=temporary,
            )
            self.assertIs(result, baseline)
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt["verification_result"], "ERROR")
            self.assertNotIn("fixture failure", receipt)

        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as outside:
            result = preserve_baseline_with_shadow_observation(
                baseline,
                shadow_enabled=True,
                observer=self._projection,
                receipt_path=Path(outside) / "receipt.json",
                allowed_receipt_root=temporary,
            )
            self.assertIs(result, baseline)
            self.assertFalse((Path(outside) / "receipt.json").exists())

    def test_projection_rejects_evaluator_data_without_leaking_payload(self) -> None:
        baseline = {"baseline": True}
        projection = self._projection()
        projection["candidate_output_ref"] = "sandbox://candidate/private_gold.json"
        with tempfile.TemporaryDirectory() as temporary:
            receipt_path = Path(temporary) / "receipt.json"
            result = preserve_baseline_with_shadow_observation(
                baseline,
                shadow_enabled=True,
                observer=lambda: projection,
                receipt_path=receipt_path,
                allowed_receipt_root=temporary,
            )
            self.assertIs(result, baseline)
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt["verification_result"], "ERROR")
            self.assertNotIn("observer_projection", receipt)

    def test_empty_or_false_projection_cannot_be_reported_pass(self) -> None:
        baseline = object()
        for projection in ({}, {**self._projection(), "structural_only": False}):
            with tempfile.TemporaryDirectory() as temporary:
                receipt_path = Path(temporary) / "receipt.json"
                result = preserve_baseline_with_shadow_observation(
                    baseline,
                    shadow_enabled=True,
                    observer=lambda projection=projection: projection,
                    receipt_path=receipt_path,
                    allowed_receipt_root=temporary,
                )
                self.assertIs(result, baseline)
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                self.assertEqual(receipt["verification_result"], "ERROR")

    def test_existing_receipt_blocks_observer_before_candidate_work(self) -> None:
        baseline = object()
        calls = 0

        def observer():
            nonlocal calls
            calls += 1
            return self._projection()

        with tempfile.TemporaryDirectory() as temporary:
            receipt_path = Path(temporary) / "receipt.json"
            receipt_path.write_text("existing", encoding="utf-8")
            result = preserve_baseline_with_shadow_observation(
                baseline,
                shadow_enabled=True,
                observer=observer,
                receipt_path=receipt_path,
                allowed_receipt_root=temporary,
            )
            self.assertIs(result, baseline)
            self.assertEqual(calls, 0)
            self.assertEqual(receipt_path.read_text(encoding="utf-8"), "existing")

    def test_non_boolean_flag_is_rejected_before_observation(self) -> None:
        with self.assertRaises(TypeError):
            preserve_baseline_with_shadow_observation(object(), shadow_enabled=1)


if __name__ == "__main__":
    unittest.main()
