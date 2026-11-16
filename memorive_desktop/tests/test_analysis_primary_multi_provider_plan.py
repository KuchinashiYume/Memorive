from __future__ import annotations

from pathlib import Path
import sys


ATTEMPT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ATTEMPT_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from memorive_settings.workflow_exam.analysis import QualityAnalysisExamExecutor
from memorive_settings.workflow_exam.card_reviewer import _api_worst_case_request_reserve_cny
from memorive_settings.workflow_exam.common import _sha256


def test_opus5_max_worst_case_reserve_uses_provider_output_ceiling() -> None:
    reserve = _api_worst_case_request_reserve_cny(
        {
            "enforce_per_call_worst_case_reserve": True,
            "pricing_estimate_cny_per_million": {
                "input": 37.5,
                "output": 187.5,
            },
        },
        {"effective_prompt_utf8_bytes": 10_000},
        128_000,
    )

    assert reserve == 24.375


def test_analysis_primary_preload_reuses_only_exact_completed_case_subset() -> None:
    rows = [{"case_id": "case-1", "claim_text": "preserved"}]
    context = {
        "workflow_exam_preloaded_initial_artifacts": {
            "A:claim_map": {
                "sample_slot": 1,
                "requested_model": "claude-opus-5",
                "returned_model": "claude-opus-5",
                "rows": rows,
                "rows_sha256": _sha256(rows),
                "source_calls": [
                    {
                        "call_id": "call-01",
                        "receipt_sha256": "A" * 64,
                        "provider_output_sha256": "B" * 64,
                    }
                ],
            }
        }
    }

    preload = QualityAnalysisExamExecutor._preloaded_initial_artifact_rows(
        context=context,
        target={"model_name": "claude-opus-5"},
        sample_slot=1,
        form_id="A",
        artifact="claim_map",
        target_case_ids=["case-1", "case-2"],
    )

    assert preload["rows"] == rows
    assert preload["reused_case_ids"] == ["case-1"]
    assert preload["missing_case_ids"] == ["case-2"]
    assert preload["provider_call_required_for_reused_rows"] is False
