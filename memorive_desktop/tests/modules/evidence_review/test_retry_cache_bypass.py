"""Regression tests for fresh EVIDENCE_REVIEW disaster retries."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
sys.path.insert(0, str(REPO_ROOT))

from knowledge_admission import DecisionError
from evidence_review import grading, transcription


class RetryCacheBypassTest(unittest.TestCase):

    def test_grade_and_admit_marks_only_rerun_as_cache_bypass(self) -> None:
        bypass_values = []

        def fail_verify(
            card_path,
            *,
            call_verify=None,
            region_check=None,
            bypass_cache=False,
        ):
            bypass_values.append(bypass_cache)
            raise DecisionError("malformed verify response")

        with (
            patch.object(grading, "transcription_verify", side_effect=fail_verify),
            patch.object(grading, "log_reason"),
        ):
            result = grading.grade_and_admit("fixture.md", escalate_after=2)

        self.assertEqual(result.outcome, "escalated")
        self.assertEqual(result.attempts, 2)
        self.assertEqual(bypass_values, [False, True])

    def test_default_verify_adapter_forwards_cache_bypass_to_m9(self) -> None:
        calls = []

        def fake_call(task_type: str, payload: dict, *, bypass_cache=False) -> dict:
            calls.append((task_type, payload, bypass_cache))
            return {"text": '{"violations":[]}'}

        with patch("memorive.models.gateway.call", side_effect=fake_call):
            result = transcription._default_call_verify("same prompt", bypass_cache=True)

        self.assertEqual(result, '{"violations":[]}')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "verify")
        self.assertEqual(calls[0][1]["messages"][0]["content"], "same prompt")
        self.assertTrue(calls[0][2])


if __name__ == "__main__":
    unittest.main(verbosity=2)
