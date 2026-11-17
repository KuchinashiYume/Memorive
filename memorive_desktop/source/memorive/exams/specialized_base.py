"""The base class of the OCR and reranker exam executors.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
from time import monotonic
from typing import Any, Mapping
from memorive.exams.common import (
    _not_run,
    _write_json_create_only,
    _plan_authorization_reason,
)


SHA256 = re.compile(r"^[A-F0-9]{64}$")


class _SpecializedExamBase:
    @staticmethod
    def _authorization_reason(
        authorization: Mapping[str, Any] | None,
        plan: Mapping[str, Any],
    ) -> str | None:
        """A plan on a subscription command line without per-call price acknowledges it."""
        return _plan_authorization_reason(
            authorization,
            plan,
            subscription=plan.get("cost_semantics") in {
                "CHATGPT_SUBSCRIPTION_CLI_NO_PER_CALL_PRICE_RECEIPT",
                "CLI_MANAGED_UNKNOWN",
            },
        )

    @staticmethod
    def _state() -> dict[str, Any]:
        return {
            "request_attempts": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
            "physical_model_calls": 0,
            "external_budget_calls": 0,
            "local_model_calls": 0,
            "external_network_calls": 0,
            "external_process_launches": 0,
            "estimated_cost_cny": 0.0,
            "token_usage": {"prompt_tokens": 0, "completion_tokens": 0},
        }

    @staticmethod
    def _consume_result(state: dict[str, Any], result: Mapping[str, Any]) -> None:
        for field in (
            "provider_calls",
            "external_model_calls",
            "external_network_calls",
            "external_process_launches",
        ):
            state[field] += int(result.get(field) or 0)
        physical_model_calls = int(
            result.get("physical_model_calls")
            if result.get("physical_model_calls") is not None
            else result.get("external_model_calls") or 0
        )
        receipt = result.get("execution_receipt")
        profile_kind = (
            str(receipt.get("profile_kind"))
            if isinstance(receipt, Mapping)
            and isinstance(receipt.get("profile_kind"), str)
            else ""
        )
        external_budget_calls = int(
            result.get("external_budget_calls")
            if result.get("external_budget_calls") is not None
            else physical_model_calls if profile_kind in {"API", "CLI"} else 0
        )
        local_model_calls = int(
            result.get("local_model_calls")
            if result.get("local_model_calls") is not None
            else physical_model_calls if profile_kind == "LOCAL" else 0
        )
        state["physical_model_calls"] += physical_model_calls
        state["external_budget_calls"] += external_budget_calls
        state["local_model_calls"] += local_model_calls
        state["request_attempts"] += physical_model_calls
        if not isinstance(receipt, Mapping):
            return
        estimate = receipt.get("estimated_cost_cny")
        if isinstance(estimate, (int, float)) and not isinstance(estimate, bool):
            state["estimated_cost_cny"] += float(estimate)
        from memorive.models.cli.templates import accumulate_usage
        accumulate_usage(state,receipt.get("token_usage"))

    @staticmethod
    def _failure(
        *,
        started: float,
        run_id: str,
        run_root: Path,
        plan: Mapping[str, Any],
        requested_model: str | None,
        state: Mapping[str, Any],
        error: Exception,
    ) -> dict[str, Any]:
        reason = str(error) or type(error).__name__
        if len(reason) > 200:
            reason = type(error).__name__
        failure = {
            "schema_version": "WorkflowModelExamRunFailure-v2",
            "status": "NOT_ASSESSED",
            "execution_outcome": (
                "CAPACITY"
                if any(token in reason.upper() for token in ("CAPACITY", "MAX_TOKEN", "LENGTH"))
                else "INVALID_RESPONSE"
                if any(token in reason.upper() for token in ("INVALID", "MISSING", "MISMATCH", "UNCLOSED"))
                else "UNKNOWN_ERROR"
            ),
            "scorability": "NOT_ASSESSED",
            "reason": reason,
            "run_id": run_id,
            "plan_sha256": plan["plan_sha256"],
            **deepcopy(dict(state)),
            "score": None,
        }
        evidence = _write_json_create_only(run_root / "exam_failure.json", failure)
        result = _not_run(reason, requested_model=requested_model)
        result.update(
            duration_ms=max(0, int((monotonic() - started) * 1000)),
            execution_outcome=failure["execution_outcome"],
            scorability="NOT_ASSESSED",
            exam_run_id=run_id,
            exam_run_root=str(run_root.resolve()),
            exam_evidence_sha256=evidence["sha256"],
            **deepcopy(dict(state)),
        )
        return result
