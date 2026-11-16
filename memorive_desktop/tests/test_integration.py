"""sample-only contracts complement the preserved predecessor regressions."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from memorive.exams.score_history import ExamScoreHistory
from memorive.models.validation.runner import LiveModelValidationRunner
from memorive.exams.runner import WorkflowExams
from memorive.platform.json_files import canonical_sha256

ROOT = Path(__file__).resolve().parents[1]
TARGET = {"kind": "API", "model_name": "synthetic-model", "thinking_mode": "high"}
NODE = {"node_id": "analysis"}
RESULT = {"status": "PASS", "score": 87, "executor_ref": "synthetic-exam-v1", "exam_tier": {"tier": "FULL"}}

def test_full_score_survives_restart_and_is_exactly_profile_and_node_bound(tmp_path):
    history = ExamScoreHistory(tmp_path)
    assert history.record(NODE, TARGET, RESULT, {"synthetic-exam-v1"})
    reopened = ExamScoreHistory(tmp_path)
    assert reopened.lookup(NODE, TARGET, {"synthetic-exam-v1"})["score"] == 87
    assert reopened.lookup(NODE, {**TARGET, "thinking_mode": "low"}, {"synthetic-exam-v1"}) is None
    assert reopened.lookup({"node_id":"card_distill"}, TARGET, {"synthetic-exam-v1"}) is None
    assert reopened.lookup(NODE, TARGET, {"synthetic-exam-v2"}) is None
    assert "synthetic-model" in next(tmp_path.glob("*.json")).read_text(encoding="utf-8")

@pytest.mark.parametrize("change", [
    {"exam_tier": {"tier": "LIGHT"}}, {"exam_tier": {"tier": "HALF"}},
    {"status": "NOT_RUN"}, {"score": None}, {"score": True},
    {"stable_score_cache_eligible": False}, {"cache_reuse_status": "DIAGNOSTIC_ONLY"},
])
def test_partial_or_unscored_results_cannot_overwrite_full_cache(tmp_path, change):
    history = ExamScoreHistory(tmp_path)
    assert history.record(NODE, TARGET, RESULT, {"synthetic-exam-v1"})
    assert not history.record(NODE, TARGET, {**RESULT, **change}, {"synthetic-exam-v1"})
    assert history.lookup(NODE, TARGET, {"synthetic-exam-v1"})["score"] == 87

def test_corrupt_local_score_cache_does_not_become_a_grade(tmp_path):
    history = ExamScoreHistory(tmp_path)
    history.record(NODE, TARGET, RESULT, {"synthetic-exam-v1"})
    path = next(tmp_path.glob("*.json"))
    value = json.loads(path.read_text(encoding="utf-8"))
    value["score"] = 99
    path.write_text(json.dumps(value), encoding="utf-8")
    assert history.lookup(NODE, TARGET, {"synthetic-exam-v1"}) is None

def test_explicit_exam_does_not_silently_return_an_old_cached_grade(tmp_path):
    class Executor:
        EXECUTOR_REF = "synthetic-exam-v1"
        def plan(self, node, target, context):
            return {"status":"READY", "mode":"SYNTHETIC", "maximum_model_calls":1}
        def execute(self, node, target, context, **kwargs):
            return {"status":"NOT_RUN", "reason":"AUTHORIZATION_REQUIRED", "provider_calls":0}
    runner = WorkflowExams(runner=LiveModelValidationRunner(credential_resolver=lambda ref:b"",
        preferences_loader=lambda:{}, scratch_root=tmp_path), executors=[Executor()])
    runner._exam_score_history.record(NODE, TARGET, RESULT, {"synthetic-exam-v1"})
    assert runner.cached_workflow_node_score(NODE, TARGET)["score"] == 87
    assert runner.plan_workflow_node_exam(NODE, TARGET)["maximum_model_calls"] == 0
    live = {"workflow_exam_force_live": True}
    assert runner.plan_workflow_node_exam(NODE, TARGET, live)["maximum_model_calls"] == 1
    result = runner.test_workflow_node(NODE, TARGET, context=live)
    assert result["status"] == "NOT_RUN" and result["provider_calls"] == 0

