"""Regressions for quantitative key_results with an empty key_data response."""
from __future__ import annotations

import json
import sys
import unittest
from importlib import import_module
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
sys.path.insert(0, str(REPO_ROOT))

distill = import_module("memorive.documents.card_distillation.distill")
errors = import_module("memorive.documents.card_distillation.errors")


class KeyDataEmptyRecoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.log_patch = patch.object(distill, "log")
        self.error_log_patch = patch.object(distill, "log_error")
        self.mock_log = self.log_patch.start()
        self.mock_error_log = self.error_log_patch.start()
        self.addCleanup(self.error_log_patch.stop)
        self.addCleanup(self.log_patch.stop)

    def test_strong_signals_detect_headline_numbers_not_list_numbers(self) -> None:
        detect = getattr(distill, "_key_result_quantitative_signals", None)
        self.assertTrue(callable(detect))

        signals = detect(
            "有机负荷为 1.3~13 kg COD/(retrieval·d)，COD 去除率为 42.5%~97.0%，"
            "另一试验报告 65.0±1.0%。"
        )
        self.assertGreaterEqual(len(signals), 3)
        self.assertEqual(
            detect("结论包括 1、2、3 三方面；本文发表于 2024 年。"),
            (),
        )

    def test_empty_response_retries_once_with_core_quantitative_claims(self) -> None:
        recovered = {
            "key_data": [
                {
                    "value": "42.5~97.0",
                    "unit": "%",
                    "metric": "COD 去除率",
                    "sample": "样本/对照: 文献汇总 | 阶段/时段: 全文 | 子过程: 未给 | 对照: 未给",
                    "stat": "统计类型: 范围 | n: 未给 | 显著性: 未给",
                    "quote": "COD 去除率为 42.5%~97.0%",
                    "chunk_id": "Zhang2024#c1",
                }
            ]
        }
        responses = [
            {"text": '{"key_data": []}'},
            {"text": json.dumps(recovered, ensure_ascii=False)},
        ]
        with (
            patch.object(distill, "_call_distill", side_effect=responses) as call,
            patch.object(
                distill,
                "_validate_key_data",
                return_value=["validated"],
            ) as validate,
        ):
            result = distill._extract_key_data(
                "[Zhang2024#c1 | 结果 | p1] COD 去除率为 42.5%~97.0%",
                {"Zhang2024#c1": "COD 去除率为 42.5%~97.0%"},
                "Zhang2024",
                "zh",
                "self",
                core_fields={
                    "key_results": "COD 去除率为 42.5%~97.0%。",
                },
            )

        self.assertEqual(result, ["validated"])
        self.assertEqual(call.call_count, 2)
        self.assertEqual(
            call.call_args_list[0].args[1],
            distill.KEY_DATA_MAX_TOKENS,
        )
        self.assertEqual(
            call.call_args_list[1].args[1],
            distill.KEY_DATA_MAX_TOKENS_RETRY,
        )
        retry_prompt = call.call_args_list[1].args[0]
        self.assertIn("上一轮 key_data 返回空列表", retry_prompt)
        self.assertIn("COD 去除率为 42.5%~97.0%", retry_prompt)
        validate.assert_called_once()
        self.assertEqual(validate.call_args.args[0], recovered)


    def test_invalid_contract_recovers_once_then_validates(self) -> None:
        first = {
            "key_data": [
                {
                    "value": "65.0",
                    "unit": "%",
                    "metric": "COD removal",
                    "sample": "stage: steady | comparison: not given",
                    "stat": "stat_type: single value | n: not given",
                    "quote": "Removal was 65.0%.",
                    "chunk_id": "P#c1",
                }
            ]
        }
        recovered = {"key_data": [{"recovered": True}]}
        responses = [
            {"text": json.dumps(first)},
            {"text": json.dumps(recovered)},
        ]
        with (
            patch.object(
                distill,
                "_call_distill",
                side_effect=responses,
            ) as call,
            patch.object(
                distill,
                "_validate_key_data",
                side_effect=[
                    errors.KeyDataInvalidError("sample missing required slot"),
                    ["validated"],
                ],
            ) as validate,
        ):
            result = distill._extract_key_data(
                "[P#c1 | Results | p1] Removal was 65.0%.",
                {"P#c1": "Removal was 65.0%."},
                "P",
                "en",
                "self",
                core_fields={"key_results": "Removal was 65.0%."},
            )

        self.assertEqual(result, ["validated"])
        self.assertEqual(call.call_count, 2)
        self.assertEqual(
            call.call_args_list[0].args[1],
            distill.KEY_DATA_MAX_TOKENS,
        )
        self.assertEqual(
            call.call_args_list[1].args[1],
            distill.KEY_DATA_MAX_TOKENS_RETRY,
        )
        self.assertEqual(validate.call_count, 2)
        recovery_prompt = call.call_args_list[1].args[0]
        self.assertIn("sample missing required slot", recovery_prompt)
        self.assertIn("上一轮 key_data", recovery_prompt)

    def test_quote_contract_uses_the_same_single_recovery(self) -> None:
        responses = [
            {"text": '{"key_data": [{"round": 1}]}'},
            {"text": '{"key_data": [{"round": 2}]}'},
        ]
        with (
            patch.object(
                distill,
                "_call_distill",
                side_effect=responses,
            ) as call,
            patch.object(
                distill,
                "_validate_key_data",
                side_effect=[
                    errors.CardQuoteNotFoundError(
                        "quote is not a literal chunk substring"
                    ),
                    ["validated"],
                ],
            ) as validate,
        ):
            result = distill._extract_key_data(
                "[P#c1 | Results | p1] Removal was 65.0%.",
                {"P#c1": "Removal was 65.0%."},
                "P",
                "en",
                "self",
                core_fields={"key_results": "Removal was 65.0%."},
            )

        self.assertEqual(result, ["validated"])
        self.assertEqual(call.call_count, 2)
        self.assertEqual(validate.call_count, 2)
        self.assertIn(
            "quote is not a literal chunk substring",
            call.call_args_list[1].args[0],
        )


    def test_qualitative_key_results_keep_empty_list_legal(self) -> None:
        with (
            patch.object(
                distill,
                "_call_distill",
                return_value={"text": '{"key_data": []}'},
            ) as call,
        ):
            result = distill._extract_key_data(
                "[P#c1 | Results | p1] Participants described resilience.",
                {"P#c1": "Participants described resilience."},
                "P",
                "en",
                "self",
                core_fields={
                    "key_results": "Participants described resilience.",
                },
            )

        self.assertEqual(result, [])
        self.assertEqual(call.call_count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
