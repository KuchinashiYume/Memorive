"""sample score-persistence and progress feedback regressions; no model calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from memorive_settings.contracts import canonical_sha256
from memorive_settings.exam_score_history import ExamScoreHistory
from memorive_settings.tiered_exam import project_tier_result
from memorive.models.validation.runner import LiveModelValidationRunner
from memorive_settings.workflow_exam_runner import WorkflowExams

ROOT=Path(__file__).resolve().parents[1]
REF="Quality_NEW_Quality_EMBEDDING_SUCCESSOR_EXECUTOR_V2"
NODE={"node_id":"chunk_embedding","profile_ref":"local:"+"a"*24}
TARGET=dict(kind="LOCAL",profile_ref=NODE["profile_ref"],config_id=NODE["profile_ref"],
    model_name="bge-m3:latest",model_digest="B"*64,endpoint_kind="ollama",
    embedding_endpoint="http://127.0.0.1:11434/api/embed")
TIER=dict(tier="FULL",precision="FULL_REFERENCE",actual_fraction=1.0,selected_count=10,full_count=10)

def documents():
    plan=dict(status="READY",node_id=NODE["node_id"],profile_ref=NODE["profile_ref"],
        profile_kind="LOCAL",model_name=TARGET["model_name"],model_digest=TARGET["model_digest"],
        executor_ref=REF,exam_tier=deepcopy(TIER),maximum_model_calls=702,
        sample_revision="fixture-revision",exam_category_id="EMBEDDING_FIXTURE")
    plan["plan_sha256"]=canonical_sha256(plan)
    result=dict(status="PASS",execution_outcome="COMPLETED",scorability="SCOREABLE",
        requested_model=TARGET["model_name"],returned_model=TARGET["model_name"],
        run_id="workflow-exam-fixture001",score=81,plan_sha256=plan["plan_sha256"],
        sample_revision=plan["sample_revision"],exam_category_id=plan["exam_category_id"],
        provider_received_answer_key=False,provider_received_gold=False,
        request_attempts=702,external_model_calls=702,reference_regression_only=True)
    return plan,result

def save_legacy(root,plan,result):
    folder=root/"workflow_exams"/result["run_id"]
    folder.mkdir(parents=True)
    for name,row in (("plan.json",plan),("exam_result.json",result)):
        (folder/name).write_text(json.dumps(row),encoding="utf-8")
    return folder

def runner(path):
    return WorkflowExams(runner=LiveModelValidationRunner(credential_resolver=lambda _:(_ for _ in ()).throw(AssertionError("NO_KEYS")),
        preferences_loader=lambda:{},scratch_root=path),catalog=None,
        executors=[SimpleNamespace(EXECUTOR_REF=REF, plan=lambda *a:None,
            execute=lambda *a,**k:(_ for _ in ()).throw(AssertionError("NO_INFERENCE")))])

def test_full_projection_carries_existing_executor_binding_into_real_cache(tmp_path):
    plan,result=documents()
    projected=project_tier_result(result,plan)
    history=ExamScoreHistory(tmp_path/"workflow_exam_scores")
    assert history.record(NODE,TARGET,projected,{REF}) is True
    assert history.lookup(NODE,TARGET,{REF})["score"]==81

def test_existing_full_answer_recovers_readonly_without_new_inference(tmp_path):
    plan,result=documents()
    folder=save_legacy(tmp_path,plan,result)
    before={p.name:p.read_bytes() for p in folder.iterdir()}
    live=runner(tmp_path)
    found=live.cached_workflow_node_score(NODE,TARGET)
    assert found and found["score"]==81
    assert live.cached_workflow_node_score(NODE,TARGET)["score"]==81
    assert {p.name:p.read_bytes() for p in folder.iterdir()}==before
    assert len(list((tmp_path/"workflow_exam_scores").glob("*.json")))==1

@pytest.mark.parametrize("case",["digest","profile","model","node","tier","hash","outcome","count","gold","executor"])
def test_legacy_recovery_rejects_unbound_or_incomplete_answers(tmp_path,case):
    plan,result=documents()
    if case=="digest":plan["model_digest"]="C"*64
    elif case=="profile":plan["profile_ref"]="local:"+"c"*24
    elif case=="model":result["returned_model"]="different-model"
    elif case=="node":plan["node_id"]="card_distill"
    elif case=="tier":plan["exam_tier"]["tier"]="LIGHT"
    elif case=="hash":result["plan_sha256"]="0"*64
    elif case=="outcome":result["execution_outcome"]="ERROR"
    elif case=="count":result["external_model_calls"]=1
    elif case=="gold":result["provider_received_answer_key"]=True
    elif case=="executor":plan["executor_ref"]="OTHER_EXECUTOR"
    if case not in {"hash"}:
        plan.pop("plan_sha256",None);plan["plan_sha256"]=canonical_sha256(plan)
        result["plan_sha256"]=plan["plan_sha256"]
    save_legacy(tmp_path,plan,result)
    assert runner(tmp_path).cached_workflow_node_score(NODE,TARGET) is None


def test_mismatching_result_executor_is_not_silently_rebound(tmp_path):
    plan,result=documents();result["executor_ref"]="DIFFERENT_EXECUTOR"
    projected=project_tier_result(result,plan)
    assert projected["executor_ref"]=="DIFFERENT_EXECUTOR"
    assert ExamScoreHistory(tmp_path/"scores").record(NODE,TARGET,projected,{REF}) is False

def test_saved_luna_high_selection_is_not_rejected_as_wrong_cohort():
    from memorive_settings.workflow_exam import QualityNewCardDistillerLanguageBankExamExecutor as Executor
    target=dict(kind='CLI',adapter_id='codex_cli',model_name='gpt-5.6-luna',
        thinking_mode='high',connection_status='AVAILABLE',config_id='codex-cli')
    assert Executor._profile_kind(target)=='CLI'
    target['thinking_mode']='xhigh'
    assert Executor._profile_kind(target)=='CLI'

def test_data_anomaly_scores_saved_by_earlier_versions_are_still_found(tmp_path):
    """v1.02 to v1.03 filed this exam's scores under this name. The name no longer depends on
    the bytes of exam.py, so editing that file does not hide them."""
    from memorive_review.exam import DataAnomalyExamExecutor
    executor=DataAnomalyExamExecutor(tmp_path)
    assert executor.EXECUTOR_REF=="E2_DataAnomaly_v3_8390243B50F5CCB65F5E"
    history=ExamScoreHistory(tmp_path/"workflow_exam_scores")
    node={"node_id":"logic_review"}
    target=dict(kind="API",config_id="api-fixture",model_name="deepseek-v4-flash")
    saved=dict(status="PASS",score=77,executor_ref="E2_DataAnomaly_v3_8390243B50F5CCB65F5E",exam_tier=dict(TIER))
    assert history.record(node,target,saved,{saved["executor_ref"]})
    assert history.lookup(node,target,{executor.EXECUTOR_REF})["score"]==77
