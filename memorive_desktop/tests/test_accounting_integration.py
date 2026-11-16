from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from memorive.desktop.accounting.product_controller import AccountingProductController


class LocalModels:
    def call(self, method, params):
        assert method == "local_models.bootstrap" and params == {}
        return {
            "recognized_models": [{
                "profile_ref": "local:ollama:qwen",
                "endpoint_id": "ollama-default",
                "endpoint_name": "Ollama",
                "provider": "[本地] Ollama",
                "display_name": "[本地] qwen3:8b",
                "model_name": "qwen3:8b",
                "execution_eligible": True,
                "connection_status": "AVAILABLE",
            }]
        }


def _settings():
    return {
        "revision": 4,
        "settings": {
            "model_services": [{
                "config_id": "api:deepseek:flash",
                "provider": "DeepSeek",
                "model_name": "deepseek-v4-flash",
                "connection_status": "AVAILABLE",
            }],
            "cli_services": [{
                "adapter_id": "codex_cli",
                "display_name": "Codex CLI",
                "enabled": True,
                "models": [{
                    "profile_ref": "cli:codex:luna",
                    "display_name": "GPT-5.6-luna · high",
                    "model_name": "gpt-5.6-luna",
                    "thinking_mode": "high",
                    "connection_status": "AVAILABLE",
                }],
            }],
        },
    }


def _leaderboard():
    return {
        "revision": 8,
        "source_revision": 12,
        "projection_key": "leaderboard-12",
        "preferences": {"currency": "CNY"},
        "rows": [],
        "fx": {
            "id": "fx-1",
            "source": "ECB",
            "rateDate": datetime.now(timezone.utc).date().isoformat(),
            "fetchedAt": datetime.now(timezone.utc).isoformat(),
            "unitsPerUsd": {"USD": 1, "CNY": 7.1, "JPY": 150.2},
            "refreshFailed": False,
        },
    }


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_accounting_adapter_never_fuzzy_matches_missing_profile(tmp_path: Path):
    stamp = datetime.now(timezone.utc).isoformat()
    exam = tmp_path / "exam"
    _write(exam / "workflow_exams" / "exam-1" / "attempts" / "call-01.json", {
        "physical_call_id": "call-01",
        "profile_kind": "API",
        "recorded_at": stamp,
        "execution_receipt": {
            "status": "PASS",
            "requested_model": "deepseek-v4-flash",
            "token_usage": {"prompt_tokens": 100, "completion_tokens": 20},
        },
    })
    controller = AccountingProductController(
        settings_provider=_settings,
        local_model_registry=LocalModels(),
        leaderboard_provider=_leaderboard,
        exam_root=exam,
        core_root=tmp_path / "core",
    )
    result = controller.call("accounting.get_projection_v3", {
        "scope": "ALL", "current_job_id": None, "time_range": "7D",
        "model_profile_ref": "ALL", "preferred_currency": "USD",
        "group_by": "MODEL_PROFILE",
    })
    assert result["attempt_count"] == 0
    assert "UNBOUND_PROFILE_ROWS_EXCLUDED" in result["source_summary"]["reason_codes"]
