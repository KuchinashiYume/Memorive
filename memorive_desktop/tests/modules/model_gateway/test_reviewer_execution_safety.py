from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")

from model_gateway.reviewer_batch import BatchContractError, build_batch_requests, reconcile_batch_results, stable_custom_id
from model_gateway.reviewer_binding import ModelIdMismatch, ReviewerBinding, ReviewerUnavailable
from model_gateway.reviewer_budget import BudgetBlocked, BudgetLedger, calculate_anthropic_cost
class ReviewerExecutionSafetyTest(unittest.TestCase):
    def test_binding_reads_exact_current_verify_slot(self) -> None:
        binding = ReviewerBinding.from_files(
            REPO_ROOT / "model_gateway" / "card_reviewer.yaml",
            REPO_ROOT / "model_gateway" / "models.yaml",
        )
        self.assertEqual(binding.provider, "anthropic")
        self.assertEqual(binding.exact_model_id, "claude-sonnet-5")
        self.assertFalse(binding.enabled)

    def test_alias_model_is_rejected(self) -> None:
        with self.assertRaises(ModelIdMismatch):
            ReviewerBinding.validate_exact_model("claude-sonnet-5", "claude-sonnet")

    def test_actual_model_mismatch_fails_current_call(self) -> None:
        binding = ReviewerBinding.from_files(
            REPO_ROOT / "model_gateway" / "card_reviewer.yaml",
            REPO_ROOT / "model_gateway" / "models.yaml",
        )
        with self.assertRaises(ModelIdMismatch):
            binding.assert_actual_model("claude-other")

    def test_unavailable_reviewer_keeps_card_pending(self) -> None:
        error = ReviewerUnavailable("permanent model failure")
        self.assertEqual(error.card_status, "pending")
        self.assertFalse(error.allow_fallback)

    def test_full_disabled_is_forbidden_by_binding(self) -> None:
        binding = ReviewerBinding.from_files(
            REPO_ROOT / "model_gateway" / "card_reviewer.yaml",
            REPO_ROOT / "model_gateway" / "models.yaml",
        )
        self.assertEqual(binding.full["thinking"]["type"], "adaptive")

    def test_full_request_uses_native_json_schema_output(self) -> None:
        binding = ReviewerBinding.from_files(
            REPO_ROOT / "model_gateway" / "card_reviewer.yaml",
            REPO_ROOT / "model_gateway" / "models.yaml",
        )
        schema = {
            "type": "object",
            "properties": {"coverage_complete": {"type": "boolean"}},
            "required": ["coverage_complete"],
            "additionalProperties": False,
        }
        payload = binding.build_full_request_payload(
            messages=[{"role": "user", "content": "review"}],
            schema=schema,
            timeout=240,
        )
        self.assertEqual(payload["effort"], "low")
        self.assertEqual(payload["thinking"], {"type": "adaptive"})
        self.assertEqual(payload["max_tokens"], 16384)
        self.assertFalse(payload["stream"])
        self.assertEqual(
            payload["output_config"],
            {
                "effort": "low",
                "format": {"type": "json_schema", "schema": schema},
            },
        )

        payload["output_config"]["format"]["schema"]["type"] = "array"
        self.assertEqual(schema["type"], "object")

    def test_low_risk_disabled_is_candidate_not_enabled(self) -> None:
        binding = ReviewerBinding.from_files(
            REPO_ROOT / "model_gateway" / "card_reviewer.yaml",
            REPO_ROOT / "model_gateway" / "models.yaml",
        )
        self.assertFalse(binding.low_risk_delta["enabled"])
        self.assertEqual(binding.low_risk_delta["thinking"]["type"], "disabled")

    def test_stable_batch_custom_id_is_valid(self) -> None:
        first = stable_custom_id("Paper", "v7", "full")
        second = stable_custom_id("Paper", "v7", "full")
        self.assertEqual(first, second)
        self.assertRegex(first, r"^[A-Za-z0-9_-]{1,64}$")

    def test_entrusted_batch_is_rejected(self) -> None:
        with self.assertRaises(BatchContractError):
            build_batch_requests([{"paper_id": "P", "card_version": "v1", "data_ownership": "entrusted", "params": {}}], task="full")

    def test_duplicate_batch_request_is_rejected(self) -> None:
        item = {"paper_id": "P", "card_version": "v1", "data_ownership": "self", "params": {"model": "m"}}
        with self.assertRaises(BatchContractError):
            build_batch_requests([item, dict(item)], task="full")

    def test_batch_partial_failure_keeps_failed_card_pending(self) -> None:
        requests = build_batch_requests(
            [
                {"paper_id": "CORE", "card_version": "v1", "data_ownership": "self", "params": {"model": "m"}},
                {"paper_id": "P2", "card_version": "v1", "data_ownership": "self", "params": {"model": "m"}},
            ],
            task="full",
        )
        results = [
            {"custom_id": requests[0]["custom_id"], "result": {"type": "succeeded", "message": {"model": "m"}}},
            {"custom_id": requests[1]["custom_id"], "result": {"type": "errored", "error": {"type": "invalid_request_error"}}},
        ]
        receipt = reconcile_batch_results(requests, results)
        self.assertEqual(receipt[requests[0]["custom_id"]]["card_status"], "reviewed_candidate")
        self.assertEqual(receipt[requests[1]["custom_id"]]["card_status"], "pending")

    def test_batch_card_version_mismatch_is_rejected(self) -> None:
        requests = build_batch_requests(
            [{"paper_id": "P", "card_version": "v1", "data_ownership": "self", "params": {"model": "m"}}],
            task="full",
        )
        requests[0]["metadata"]["card_version"] = "v2"
        with self.assertRaises(BatchContractError):
            reconcile_batch_results(requests, [])

    def test_budget_pre_reservation_blocks_before_call(self) -> None:
        ledger = BudgetLedger(limit_cny=5.0, spent_cny=4.5)
        with self.assertRaises(BudgetBlocked):
            ledger.reserve(task_id="t", conservative_upper_cny=0.6)

    def test_budget_reservation_is_released_to_actual(self) -> None:
        ledger = BudgetLedger(limit_cny=5.0, spent_cny=1.0)
        ledger.reserve(task_id="t", conservative_upper_cny=1.0)
        ledger.settle(task_id="t", actual_cny=0.4)
        self.assertAlmostEqual(ledger.spent_cny, 1.4)
        self.assertEqual(ledger.reserved_cny, 0.0)

    def test_reasoning_tokens_are_not_double_billed(self) -> None:
        cost = calculate_anthropic_cost(
            input_tokens=1000,
            output_tokens=500,
            reasoning_tokens=400,
            cache_write_tokens=0,
            cache_read_tokens=0,
            input_per_mtok=2.0,
            output_per_mtok=10.0,
            cache_write_per_mtok=2.5,
            cache_read_per_mtok=0.2,
            cny_per_usd=7.2,
        )
        self.assertAlmostEqual(cost["usd"], 0.007)

    def test_cache_read_and_write_costs_are_separate(self) -> None:
        cost = calculate_anthropic_cost(
            input_tokens=200,
            output_tokens=100,
            reasoning_tokens=50,
            cache_write_tokens=1000,
            cache_read_tokens=500,
            input_per_mtok=2.0,
            output_per_mtok=10.0,
            cache_write_per_mtok=2.5,
            cache_read_per_mtok=0.2,
            cny_per_usd=7.2,
        )
        self.assertAlmostEqual(cost["usd"], 0.004)


if __name__ == "__main__":
    unittest.main(verbosity=2)
