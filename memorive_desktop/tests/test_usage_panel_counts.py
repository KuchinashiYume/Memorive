"""What the usage panel counts (desktop/accounting): failures a document job found, connection checks, local days.

- A document job may refuse a model's answer after the call ledger recorded the call as passed; the job's own
  call file says FAILED. The panel took the ledger's PASS and showed fewer failures than there were.
- Connection checks (purpose verify_*) reach the provider, but the panel left them out.
- Days and ranges were UTC days: an evening request in Tokyo went to the next day.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from memorive.desktop.accounting.product_controller import AccountingProductController
from memorive.desktop.accounting.projection import build_projection
from test_accounting_integration import LocalModels, _leaderboard, _settings

PANEL = {"scope": "ALL", "current_job_id": None, "time_range": "7D", "model_profile_ref": "ALL",
         "preferred_currency": "USD", "group_by": "MODEL_PROFILE"}


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _call(execution_id, status, purpose):
    return {"execution_id": execution_id, "profile_ref": "api:deepseek:flash", "purpose": purpose, "status": status,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "execution_receipt": {"status": status, "execution_id": execution_id,
                                  "token_usage": {"prompt_tokens": 10, "completion_tokens": 5}}}


def test_a_refused_answer_counts_as_failed_and_a_connection_check_counts(tmp_path):
    exam, core = tmp_path / "exam", tmp_path / "core"
    ledger = exam / "model_call_ledger" / "executions"
    _write(ledger / "e1.post.json", _call("E1", "PASS", "core_document_processing"))
    _write(core / "job-1" / "model_calls" / "call-000017.post.json", _call("E1", "FAILED", "core_document_processing"))
    _write(ledger / "e2.post.json", _call("E2", "PASS", "verify_api_model"))
    controller = AccountingProductController(settings_provider=_settings, local_model_registry=LocalModels(),
                                             leaderboard_provider=_leaderboard, exam_root=exam, core_root=core)
    result = controller.call("accounting.get_projection_v3", dict(PANEL))
    assert (result["attempt_count"], result["failed_attempt_count"]) == (2, 1)


def test_days_are_the_local_days(tmp_path):
    tokyo = timezone(timedelta(hours=9))
    evening = datetime(2026, 11, 21, 22, 30, tzinfo=timezone.utc)   # 2026-11-22 07:30 in Tokyo
    attempt = {"attempt_id": "attempt:1", "occurred_at": evening.isoformat(), "job_id": None,
               "profile_ref": "api:deepseek:flash", "profile_kind": "API", "provider_label": "DeepSeek",
               "model_label": "deepseek-v4-flash", "model_id": "deepseek-v4-flash", "outcome": "SUCCEEDED",
               "tokens_input": 10, "tokens_output": 5, "tokens_reasoning": 0, "cost_status": "NOT_ASSESSED",
               "cost_value": None, "currency": None, "source_kind": "ACCOUNTING_ENVELOPE"}
    context = AccountingProductController(settings_provider=_settings, local_model_registry=LocalModels(),
                                          leaderboard_provider=_leaderboard, exam_root=tmp_path / "exam",
                                          core_root=tmp_path / "core").commerce_context()
    projection = build_projection([attempt], dict(PANEL), context, now=evening + timedelta(minutes=5), zone=tokyo)
    assert projection["range"]["end_date"] == "2026-11-22"
    assert projection["attempt_count"] == 1 and projection["series"][-1]["bucket"] == "2026-11-22"
