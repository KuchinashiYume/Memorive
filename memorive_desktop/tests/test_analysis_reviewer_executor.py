from __future__ import annotations

import json
from typing import Any

from memorive_settings.analysis_reviewer_exam import (
    _attach_rejected_observed_values,
)


def test_rejected_observed_value_history_uses_subject_values_only():
    feedback = {
        "case-1": {
            "mismatched_output_fields": ["/issues/0/severity"],
            "reference_values_disclosed": False,
        }
    }
    response = {
        "case_reviews": [
            {
                "case_id": "case-1",
                "review_verdict": "NEEDS_REVISION",
                "issues": [
                    {
                        "issue_family": "scope_extrapolation",
                        "claim_pointer": "/candidate_analysis/claims/0/text",
                        "evidence_refs": ["E01"],
                        "severity": "major",
                        "blocking": True,
                        "action": "narrow_scope",
                        "explanation": "bounded",
                    }
                ],
                "review_summary": "bounded",
            }
        ]
    }
    history: dict[str, dict[str, list[Any]]] = {}
    first = _attach_rejected_observed_values(
        feedback, response=response, history=history
    )
    assert first["case-1"]["rejected_observed_values_by_field"] == {
        "/issues/0/severity": ["major"]
    }
    response["case_reviews"][0]["issues"][0]["severity"] = "minor"
    second = _attach_rejected_observed_values(
        feedback, response=response, history=history
    )
    assert second["case-1"]["rejected_observed_values_by_field"] == {
        "/issues/0/severity": ["major", "minor"]
    }
    serialized = json.dumps(second, sort_keys=True)
    assert '"critical"' not in serialized
