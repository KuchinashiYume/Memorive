"""The data anomaly exam counts the cost of a reply its checks refuse.

A reply that came back but fails the exam's checks (here it names another model than the one asked for)
was still sent and paid for. The exam stopped at the check before adding the reply's cost, and its result
said 0.0 yuan with the cost complete. The exam runs as the desktop runs it on its public reference pack;
the model is a stand-in, nothing leaves the computer.
"""
from __future__ import annotations

import pytest

from memorive.exams.data_anomaly import DataAnomalyExamExecutor

TARGET = {"kind": "API", "config_id": "cfg-flash", "provider": "DeepSeek", "model_name": "deepseek-v4-flash",
          "tier": "flash", "thinking": True, "connection_status": "AVAILABLE", "credential_ref": "DEEPSEEK_API_KEY"}
NODE = {"node_id": "logic_review"}


def another_model(**request):
    name = request["model"]["model_name"]
    receipt = {"schema_version": "SettingsStructuredChatExecutionReceipt-v2", "status": "PASS",
               "profile_kind": "API", "requested_model": name, "returned_model": "another-model",
               "token_usage": {"prompt_tokens": 1000, "completion_tokens": 719}, "actual_cost": None,
               "estimated_cost_cny": 0.0042, "cost_evidence": "FROZEN_PROFILE_ESTIMATE"}
    return {"schema_version": "SettingsStructuredChatRunnerResult-v1", "status": "PASS",
            "reason": "STRUCTURED_CHAT_COMPLETED", "requested_model": name, "returned_model": "another-model",
            "response": {"answers": []}, "external_network_calls": 1, "provider_calls": 1,
            "external_model_calls": 1, "external_process_launches": 0, "execution_receipt": receipt}


def test_a_reply_refused_for_naming_another_model_is_still_counted(tmp_path):
    executor = DataAnomalyExamExecutor(tmp_path)
    plan = executor.plan(NODE, TARGET, {})
    assert plan["status"] == "READY", plan
    authorization = {"schema_version": "WorkflowModelExamAuthorization-v1", "authorized": True,
                     "plan_sha256": plan["plan_sha256"], "cost_cap_cny": plan["budget_cap_cny"],
                     "acknowledged_subscription_no_per_call_price": False}
    result = executor.execute(NODE, TARGET, {}, authorization=authorization, structured_chat=another_model)
    assert result["status"] == "NOT_RUN" and result["external_model_calls"] == 1
    assert result["estimated_cost_cny"] == pytest.approx(0.0042) and result["cost_estimate_complete"] is True
