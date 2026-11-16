"""Regressions for one syntax-only recovery of malformed core JSON."""
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


def _response(text: str, *, truncated: bool = False) -> dict:
    return {
        "text": text,
        "model": "deepseek-v4-pro",
        "raw": {
            "choices": [
                {
                    "finish_reason": "length" if truncated else "stop",
                }
            ]
        },
    }


class CoreJsonRecoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.log_patch = patch.object(distill, "log")
        self.error_log_patch = patch.object(distill, "log_error")
        self.mock_log = self.log_patch.start()
        self.mock_error_log = self.error_log_patch.start()
        self.addCleanup(self.error_log_patch.stop)
        self.addCleanup(self.log_patch.stop)

    def _parse(self, raw: str):
        parser = getattr(distill, "_parse_core_json_with_recovery", None)
        self.assertTrue(
            callable(parser),
            "_parse_core_json_with_recovery must exist",
        )
        return parser(raw, "Zhang2024", "self")

    def test_valid_core_json_does_not_call_recovery(self) -> None:
        valid = {"research_question": {"value": "valid"}}
        with patch.object(distill, "_call_distill") as call:
            parsed = self._parse(json.dumps(valid))

        self.assertEqual(parsed, valid)
        call.assert_not_called()

    def test_malformed_core_json_recovers_exactly_once(self) -> None:
        malformed = (
            '{"research_question":{"value":"A "quoted" process",'
            '"anchors":[]}}'
        )
        repaired = {
            "research_question": {
                "value": 'A "quoted" process',
                "anchors": [],
            }
        }
        with patch.object(
            distill,
            "_call_distill",
            return_value=_response(json.dumps(repaired)),
        ) as call:
            parsed = self._parse(malformed)

        self.assertEqual(parsed, repaired)
        self.assertEqual(call.call_count, 1)
        prompt, max_tokens = call.call_args.args
        self.assertIn(malformed, prompt)
        self.assertIn("Expecting", prompt)
        self.assertIn("只修复 JSON 语法", prompt)
        self.assertEqual(max_tokens, distill.DISTILL_MAX_TOKENS_RETRY)

    def test_second_malformed_response_fails_closed(self) -> None:
        malformed = '{"research_question":{"value":"A "quoted" process"}}'
        with patch.object(
            distill,
            "_call_distill",
            return_value=_response(malformed),
        ) as call:
            with self.assertRaises(errors.CardParseError):
                self._parse(malformed)

        self.assertEqual(call.call_count, 1)
        self.mock_error_log.assert_called_once()

    def test_truncated_recovery_response_fails_closed(self) -> None:
        malformed = '{"research_question":{"value":"A "quoted" process"}}'
        with patch.object(
            distill,
            "_call_distill",
            return_value=_response('{"research_question":', truncated=True),
        ) as call:
            with self.assertRaises(errors.CardParseError):
                self._parse(malformed)

        self.assertEqual(call.call_count, 1)
        self.mock_error_log.assert_called_once()

    def test_empty_or_no_object_response_is_not_recovered(self) -> None:
        for raw in ("", "plain prose without a JSON object"):
            with self.subTest(raw=raw):
                self.mock_error_log.reset_mock()
                with patch.object(distill, "_call_distill") as call:
                    with self.assertRaises(errors.CardParseError):
                        self._parse(raw)
                call.assert_not_called()
                self.mock_error_log.assert_called_once()

    def test_recovery_call_error_is_not_swallowed(self) -> None:
        malformed = '{"research_question":{"value":"A "quoted" process"}}'
        with patch.object(
            distill,
            "_call_distill",
            side_effect=RuntimeError("gateway unavailable"),
        ) as call:
            with self.assertRaisesRegex(RuntimeError, "gateway unavailable"):
                self._parse(malformed)

        self.assertEqual(call.call_count, 1)
        self.mock_error_log.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
