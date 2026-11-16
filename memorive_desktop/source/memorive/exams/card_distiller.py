"""Card distiller exams.
"""
from __future__ import annotations

from memorive.models.provider_aliases import matches_result_model
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.util
import itertools
import json
import math
from pathlib import Path
import re
import statistics
from time import monotonic
from typing import Any, Callable, Mapping, Sequence
import uuid
from model_gateway.resource_limits import resolve_call_limits
from memorive.exams.outcomes import (
    ExecutionOutcome,
    Scorability,
    admit_quality_fail,
    build_repair_round,
    classify_execution_outcome,
    cumulative_category_repair_penalty,
    workflow_exam_structured_output_policy,
)
from memorive.models.output_limits import (
    WORKFLOW_EXAM_REPLY_TOKEN_CEILING,
    workflow_exam_output_limit_contract,
)

from memorive.exams.common import (
    AUTHORIZATION_SCHEMA,
    Core_PRIMARY_CARD_OUTPUT_BYTE_LIMIT,
    Core_PRIMARY_CARD_OUTPUT_CHAR_LIMIT,
    Core_PRIMARY_SOURCE_INPUT_CHAR_LIMIT,
    EXECUTOR_REF,
    MAX_OUTPUT_TOKENS,
    PLAN_SCHEMA,
    REQUEST_TIMEOUT_SECONDS,
    RESULT_SCHEMA,
    SAFE_PACKAGE,
    _compact,
    _file_sha256,
    _load_json,
    _not_available,
    _not_run,
    _primary_model_fail_projection,
    _public_forms,
    _serialized_request,
    _sha256,
    _write_json_create_only,
    core_primary_workload_profile,
)


PASSING_SCORE = 85.0


MAX_CALLS_PER_STAGE = 17


MAX_STRUCTURAL_REPAIR_ROUNDS = 2


CARD_DISTILLER_EXAM_CATEGORY_ID = "Quality_CARD_DISTILLER_REFERENCE_REGRESSION"


CARD_DISTILLER_SUCCESSOR_SAMPLE_REVISION = (
    "Desktop_CARD_DISTILLER_Core_X2_ROTATING_7_OF_8_PACKS_V1"
)


CARD_DISTILLER_REPAIR_ISSUE_GUIDANCE_REVISION = (
    "Desktop_CARD_DISTILLER_GOLD_FREE_ISSUE_GUIDANCE_V1"
)


CARD_DISTILLER_RESIDUAL_MATERIALITY_REVISION = (
    "Desktop_CARD_DISTILLER_D3_IMPLICIT_COMPARATOR_CALIBRATION_V1"
)


def _card_distiller_residual_materiality_projection(
    *,
    blocking_failures: list[dict[str, Any]],
    golds: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    family_by_case_id: dict[str, str] = {}
    for gold in golds.values():
        cases = gold.get("cases")
        if not isinstance(cases, list):
            continue
        for row in cases:
            if (
                isinstance(row, Mapping)
                and isinstance(row.get("case_id"), str)
                and isinstance(row.get("family"), str)
            ):
                family_by_case_id[str(row["case_id"])] = str(row["family"])
    implicit_comparator_codes = {
        "COMPARATOR_MISSING",
        "COMPARATOR_CONTEXT_INCOMPLETE",
    }
    projected: list[dict[str, Any]] = []
    calibration_case_ids: list[str] = []
    for source in blocking_failures:
        row = deepcopy(source)
        case_id = str(row.get("case_id") or "")
        reasons = {
            str(reason)
            for reason in (row.get("failed_fields") or [])
            if isinstance(reason, str) and reason
        }
        if (
            family_by_case_id.get(case_id) == "D3_MULTI_OCCURRENCE"
            and bool(reasons)
            and reasons.issubset(implicit_comparator_codes)
        ):
            row["model_fail_materiality"] = "CALIBRATION"
            row["model_fail_materiality_reason"] = (
                "D3_TARGET_ONLY_REQUEST_WITH_IMPLICIT_SIBLING_COMPARATOR"
            )
            calibration_case_ids.append(case_id)
        else:
            row["model_fail_materiality"] = "MATERIAL"
        projected.append(row)
    return {
        "schema_version": CARD_DISTILLER_RESIDUAL_MATERIALITY_REVISION,
        "blocking_failures": projected,
        "calibration_override_case_ids": sorted(set(calibration_case_ids)),
        "quality_raw_score_and_verdict_preserved": True,
        "d1_explicit_comparator_errors_remain_material": True,
        "provider_visible": False,
    }


def _card_distiller_candidate_output_metrics(
    response: Mapping[str, Any],
) -> dict[str, Any]:
    """Measure Card Distiller artifacts without conflating pipeline surfaces.

    Quality deliberately asks for both a fact-map intermediary and a Card
    projection. Core's measured final-Card ceiling is therefore applied to
    each logical artifact, not to the byte sum of two independently consumed
    artifacts. The combined size remains evidence-only so a naturally verbose
    but complete answer cannot be converted into an execution failure.
    """

    artifacts: dict[str, dict[str, int]] = {}
    for artifact in ("fact_map", "card_projection"):
        serialized = _serialized_request({artifact: response.get(artifact)})
        artifacts[artifact] = {
            "chars": len(serialized),
            "utf8_bytes": len(serialized.encode("utf-8")),
        }
    combined = _serialized_request(response)
    return {
        "artifacts": artifacts,
        "combined_diagnostic": {
            "chars": len(combined),
            "utf8_bytes": len(combined.encode("utf-8")),
        },
        "capacity_limit_surface": (
            "EACH_LOGICAL_ARTIFACT_PER_FORM_NOT_CUMULATIVE"
        ),
        "combined_metric_quality_semantics": (
            "DIAGNOSTIC_ONLY_NOT_MODEL_FAIL"
        ),
    }


def _card_distiller_candidate_artifact_limit_exceeded(
    metrics: Mapping[str, Any],
) -> bool:
    """Return a Core workload-reference diagnostic, never a fail gate."""

    artifacts = metrics.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise ValueError("WORKFLOW_MODEL_EXAM_CARD_OUTPUT_METRICS_INVALID")
    for artifact in ("fact_map", "card_projection"):
        row = artifacts.get(artifact)
        if not isinstance(row, Mapping):
            raise ValueError("WORKFLOW_MODEL_EXAM_CARD_OUTPUT_METRICS_INVALID")
        chars = row.get("chars")
        utf8_bytes = row.get("utf8_bytes")
        if (
            isinstance(chars, bool)
            or not isinstance(chars, int)
            or isinstance(utf8_bytes, bool)
            or not isinstance(utf8_bytes, int)
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_CARD_OUTPUT_METRICS_INVALID")
        if (
            chars > Core_PRIMARY_CARD_OUTPUT_CHAR_LIMIT
            or utf8_bytes > Core_PRIMARY_CARD_OUTPUT_BYTE_LIMIT
        ):
            return True
    return False


def _distiller_call_bound(forms: Mapping[str, Any], profile_kind: str) -> int:
    fact_size = 12 if profile_kind == 'LOCAL' else 36
    shards = sum(math.ceil(len(form['cases']) / fact_size)
                 + math.ceil(len(form.get('packs', [])) / 4) for form in forms.values())
    final_repair = (sum(math.ceil(len(form['cases']) / 12) for form in forms.values())
                    if profile_kind == 'LOCAL' else 1)
    return shards * (1 + MAX_STRUCTURAL_REPAIR_ROUNDS) + final_repair


class QualityCardDistillerExamExecutor:
    """Desktop adapter over the frozen Quality Card Distiller exam contracts.

    The adapter deliberately exposes a reference-regression score only.  It is
    not a blind holdout and cannot grant production qualification.
    """

    SEMANTIC_REPAIR_ROUND_LIMIT = 1
    EXAM_CATEGORY_ID = CARD_DISTILLER_EXAM_CATEGORY_ID

    def __init__(
        self,
        *,
        scratch_root: Path,
        quality_package: str,
        reference_pack_root: Path,
        pricing_profile_path: Path | None = None,
    ) -> None:
        if not isinstance(quality_package, str) or not SAFE_PACKAGE.fullmatch(
            quality_package
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_Quality_PACKAGE_INVALID")
        self.scratch_root = Path(scratch_root).resolve()
        self._quality_package = quality_package
        self._reference_pack_root = Path(reference_pack_root).resolve()
        self._pricing_profile_path = (
            Path(pricing_profile_path).resolve()
            if pricing_profile_path is not None
            else self._reference_pack_root.parent
            / "deepseek_flash_to_pro_profile.public.json"
        )
        self._modules: dict[str, Any] | None = None

    def _quality(self) -> dict[str, Any]:
        if self._modules is None:
            names = {
                "protocol": "card_distiller_exam_protocol",
                "assets": "card_distiller_exam_assets",
                "pack": "card_distiller_exam_pack",
                "repair": "card_distiller_exam_repair",
                "reference": "reference_exam_pack",
            }
            self._modules = {
                key: importlib.import_module(f"{self._quality_package}.{suffix}")
                for key, suffix in names.items()
            }
        return self._modules

    def _pricing_profile(self) -> dict[str, Any]:
        profile = _load_json(self._pricing_profile_path)
        if (
            profile.get("schema_version")
            != "model_evaluation-model_evaluation-card-distiller-flash-to-pro-public-profile-v1"
            or profile.get("suite") != "card_distiller_primary"
            or profile.get("credential_value_included") is not False
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_PRICING_PROFILE_INVALID")
        low = profile.get("low_cost")
        high = profile.get("high_cost")
        exception = profile.get("single_run_cost_ratio_exception")
        if (
            not isinstance(low, Mapping)
            or not isinstance(high, Mapping)
            or low.get("model_id") != "deepseek-v4-flash"
            or high.get("model_id") != "deepseek-v4-pro"
            or low.get("request_max_output_tokens") != MAX_OUTPUT_TOKENS
            or high.get("request_max_output_tokens") != MAX_OUTPUT_TOKENS
            or not isinstance(exception, Mapping)
            or exception.get("reusable") is not False
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_PRICING_PROFILE_INVALID")
        return profile

    @staticmethod
    def _eligible_target(target: Mapping[str, Any], *, low_cost: bool) -> bool:
        expected_model = "deepseek-v4-flash" if low_cost else "deepseek-v4-pro"
        expected_tier = "flash" if low_cost else "pro"
        return (
            target.get("kind") == "API"
            and re.fullmatch(
                r"deepseek(?:apikey[a-z0-9]*)?", _compact(target.get("provider"))
            )
            is not None
            and target.get("model_name") == expected_model
            and target.get("thinking") is True
            and target.get("tier") == expected_tier
            and target.get("connection_status") == "AVAILABLE"
            and isinstance(target.get("config_id"), str)
            and bool(target.get("config_id"))
        )

    def plan(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        if node.get("node_id") != "card_distill":
            return _not_available("WORKFLOW_MODEL_EXAM_DYNAMIC_NODE_UNSUPPORTED")
        if not self._eligible_target(target, low_cost=False):
            return _not_available("WORKFLOW_MODEL_EXAM_DYNAMIC_PROFILE_UNSUPPORTED")
        low_cost_target = context.get("low_cost_target")
        if not isinstance(low_cost_target, Mapping) or not self._eligible_target(
            low_cost_target, low_cost=True
        ):
            return _not_available("WORKFLOW_MODEL_EXAM_LOW_COST_PROFILE_UNAVAILABLE")
        try:
            modules = self._quality()
            verification = modules["pack"].verify_reference_pack(
                self._reference_pack_root
            )
            pricing = self._pricing_profile()
        except (ImportError, OSError, ValueError, RuntimeError):
            return _not_available("WORKFLOW_MODEL_EXAM_Quality_ASSETS_INVALID")
        if (
            verification.get("status") != "PASS"
            or verification.get("reference_regression_only") is not True
            or verification.get("qualification_eligible") is not False
        ):
            return _not_available("WORKFLOW_MODEL_EXAM_Quality_PACK_NOT_ELIGIBLE")
        low = pricing["low_cost"]
        high = pricing["high_cost"]
        plan: dict[str, Any] = {
            "schema_version": PLAN_SCHEMA,
            "status": "READY",
            "mode": "LIVE_Quality_CARD_DISTILLER_REFERENCE_REGRESSION",
            "reason": "Quality_REFERENCE_REGRESSION_READY",
            "node_id": "card_distill",
            "executor_ref": EXECUTOR_REF,
            "settings_revision": context.get("settings_revision"),
            "settings_sha256": context.get("settings_sha256"),
            "reference_pack_id": verification["pack_id"],
            "reference_pack_revision": verification["pack_revision"],
            "reference_pack_sha256": verification["pack_fingerprint"],
            "reference_regression_only": True,
            "blind_holdout_eligible": False,
            "qualification_eligible": False,
            "low_cost": {
                "profile_ref": low_cost_target["config_id"],
                "model_name": low_cost_target["model_name"],
                "tier": low_cost_target["tier"],
                "forms": ["G", "H"],
                "budget_cap_cny": float(low["budget_cap_cny"]),
            },
            "high_cost": {
                "profile_ref": target["config_id"],
                "model_name": target["model_name"],
                "tier": target["tier"],
                "forms": ["E", "F"],
                "budget_cap_cny": float(high["budget_cap_cny"]),
            },
            "maximum_provider_calls": _distiller_call_bound(_public_forms(self._reference_pack_root), 'API') * 2,
            "budget_cap_cny": float(low["budget_cap_cny"])
            + float(high["budget_cap_cny"]),
            "low_to_high_worst_cost_ratio": float(
                pricing["low_to_high_worst_cost_ratio"]
            ),
            "low_to_high_ratio_limit": float(pricing["low_to_high_ratio_limit"]),
            "requires_non_reusable_ratio_exception": True,
            "pricing_snapshot_date": pricing["profile_snapshot_date"],
            "pricing_profile_sha256": _file_sha256(self._pricing_profile_path),
            "authorization_schema_version": AUTHORIZATION_SCHEMA,
            "external_network_calls": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
        }
        plan["plan_sha256"] = _sha256(plan)
        return plan

    @staticmethod
    def _authorization_reason(
        authorization: Mapping[str, Any] | None,
        plan: Mapping[str, Any],
    ) -> str | None:
        if not isinstance(authorization, Mapping):
            return "WORKFLOW_MODEL_EXAM_AUTHORIZATION_REQUIRED"
        if set(authorization) != {
            "schema_version",
            "authorized",
            "plan_sha256",
            "cost_cap_cny",
            "acknowledged_non_reusable_ratio_exception",
        }:
            return "WORKFLOW_MODEL_EXAM_AUTHORIZATION_INVALID"
        if authorization.get("schema_version") != AUTHORIZATION_SCHEMA:
            return "WORKFLOW_MODEL_EXAM_AUTHORIZATION_INVALID"
        if authorization.get("authorized") is not True:
            return "WORKFLOW_MODEL_EXAM_AUTHORIZATION_REQUIRED"
        if authorization.get("plan_sha256") != plan.get("plan_sha256"):
            return "WORKFLOW_MODEL_EXAM_AUTHORIZATION_MISMATCH"
        cap = authorization.get("cost_cap_cny")
        if isinstance(cap, bool) or not isinstance(cap, (int, float)):
            return "WORKFLOW_MODEL_EXAM_AUTHORIZATION_INVALID"
        if float(cap) != float(plan["budget_cap_cny"]):
            return "WORKFLOW_MODEL_EXAM_COST_CAP_MISMATCH"
        if authorization.get("acknowledged_non_reusable_ratio_exception") is not True:
            return "WORKFLOW_MODEL_EXAM_RATIO_EXCEPTION_REQUIRED"
        return None

    @staticmethod
    def _validate_call_result(
        result: Mapping[str, Any], requested_model: str, profile_kind: str
    ) -> Mapping[str, Any]:
        is_api = profile_kind == "API"
        is_cli = profile_kind == "CLI"
        if (
            result.get("schema_version") != "SettingsStructuredChatRunnerResult-v1"
            or result.get("status") != "PASS"
            or result.get("requested_model") != requested_model
            or not matches_result_model(result, requested_model)
            or not isinstance(result.get("response"), Mapping)
            or result.get("provider_calls") != (1 if is_api else 0)
            or result.get("external_model_calls") != 1
            or result.get("external_network_calls") != (1 if is_api else 0)
            or result.get("external_process_launches") != (1 if is_cli else 0)
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_CALL_RESULT_INVALID")
        receipt = result.get("execution_receipt")
        if not isinstance(receipt, Mapping):
            raise ValueError("WORKFLOW_MODEL_EXAM_CALL_RECEIPT_MISSING")
        if (
            receipt.get("status") != "PASS"
            or receipt.get("profile_kind") != profile_kind
            or receipt.get("purpose") != "workflow_model_exam"
            or receipt.get("requested_model") != requested_model
            or not matches_result_model(receipt, requested_model)
            or not isinstance(receipt.get("route"), str)
            or not receipt.get("route")
            or not isinstance(receipt.get("region"), str)
            or not receipt.get("region")
            or not isinstance(receipt.get("egress"), str)
            or not receipt.get("egress")
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_CALL_RECEIPT_INVALID")
        from memorive.models.cli.templates import receipt_model_bound
        if is_cli and receipt_model_bound(receipt,requested_model):
            if receipt.get("actual_cost") is not None or receipt.get("estimated_cost_cny") is not None:
                raise ValueError("WORKFLOW_MODEL_EXAM_CLI_COST_EVIDENCE_INVALID")
            return receipt
        usage = receipt.get("token_usage")
        if not isinstance(usage, Mapping):
            raise ValueError("WORKFLOW_MODEL_EXAM_TOKEN_EVIDENCE_MISSING")
        for field in ("prompt_tokens", "completion_tokens"):
            value = usage.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("WORKFLOW_MODEL_EXAM_TOKEN_EVIDENCE_MISSING")
        actual = receipt.get("actual_cost")
        estimate = receipt.get("estimated_cost_cny")
        if is_api:
            if actual is None and (
                isinstance(estimate, bool)
                or not isinstance(estimate, (int, float))
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_COST_EVIDENCE_MISSING")
        elif is_cli:
            if (
                actual is not None
                or estimate is not None
                or receipt.get("cost_evidence")
                != "SUBSCRIPTION_CLI_NO_PER_CALL_PRICE"
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_CLI_COST_EVIDENCE_INVALID")
        elif (
            actual is not None
            or estimate != 0.0
            or receipt.get("cost_evidence")
            != "LOCAL_COMPUTE_NO_PROVIDER_API_COST"
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_LOCAL_COST_EVIDENCE_INVALID")
        return receipt

    def _structured_output_policy(
        self,
        target: Mapping[str, Any],
    ) -> tuple[dict[str, Any], int | None, str]:
        """Legacy Card Distiller contract kept for the frozen executor."""

        return deepcopy(dict(target)), MAX_OUTPUT_TOKENS, "EXPLICIT"

    def _request_timeout_seconds(self, profile_kind: str) -> int:
        del profile_kind
        return REQUEST_TIMEOUT_SECONDS

    def _provider_call(
        self,
        *,
        run_id: str,
        stage_root: Path,
        state: dict[str, Any],
        target: Mapping[str, Any],
        prompt: str,
        response_schema: Mapping[str, Any],
        structured_chat: Callable[..., Mapping[str, Any]],
        call_kind: str,
    ) -> dict[str, Any]:
        stage_call_cap = int(
            state.get("stage_call_cap", MAX_CALLS_PER_STAGE)
        )
        if state["stage_calls"] >= stage_call_cap:
            raise ValueError("WORKFLOW_MODEL_EXAM_STAGE_CALL_LIMIT_REACHED")
        call_number = state["stage_calls"] + 1
        call_id = f"call-{call_number:02d}"
        model_name = str(target["model_name"])
        profile_kind = str(target["kind"])
        is_api = profile_kind == "API"
        is_local = profile_kind == "LOCAL"
        request_timeout_seconds = self._request_timeout_seconds(profile_kind)
        if not 30 <= request_timeout_seconds <= 3_600:
            raise ValueError("WORKFLOW_MODEL_EXAM_TIMEOUT_PROFILE_INVALID")
        (
            model_binding,
            request_max_output_tokens,
            output_limit_mode,
        ) = self._structured_output_policy(target)
        prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest().upper()
        limits = resolve_call_limits(profile_kind=profile_kind, service=target, model=target)
        if output_limit_mode == "PROVIDER_DEFAULT":
            limits["max_output_tokens"] = None
            limits["wire_output_policy"] = "PROVIDER_DEFAULT"
        claim = {
            "schema_version": "WorkflowModelExamPreSendClaim-v1",
            "run_id": run_id,
            "call_id": call_id,
            "call_kind": call_kind,
            "profile_ref": target["config_id"],
            "requested_model": model_name,
            "profile_kind": profile_kind,
            "route": (
                "EXISTING_SETTINGS_API_TRANSPORT"
                if is_api
                else ("OLLAMA_LOOPBACK" if is_local else "DECLARATIVE_COMMAND_TEMPLATE" if target.get("adapter_id")=="command_template" else "CODEX_CLI_SUBSCRIPTION")
            ),
            "region": (
                "PROVIDER_MANAGED_UNDISCLOSED"
                if is_api
                else ("LOCAL_MACHINE" if is_local else "PROVIDER_MANAGED_UNDISCLOSED" if target.get("adapter_id")=="command_template" else "OPENAI_MANAGED_UNDISCLOSED")
            ),
            "egress": (
                "PROVIDER_API"
                if is_api
                else ("LOOPBACK_ONLY" if is_local else "CLI_PROVIDER_MANAGED" if target.get("adapter_id")=="command_template" else "CODEX_CLI")
            ),
            "behavior_sha256": prompt_sha256,
            "response_schema_sha256": _sha256(response_schema),
            "max_output_tokens": limits['max_output_tokens'],
            "timeout_seconds": limits['timeout_seconds'],
            "resource_limits": limits,
            "max_output_tokens_mode": output_limit_mode,
            "pricing_profile_sha256": (
                _file_sha256(self._pricing_profile_path) if is_api else None
            ),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_json_create_only(stage_root / "claims" / f"{call_id}.json", claim)
        result = dict(
            structured_chat(
                profile_kind=profile_kind,
                service=target,
                model=model_binding,
                prompt=prompt,
                response_schema=response_schema,
                purpose="workflow_model_exam",
                max_output_tokens=limits['max_output_tokens'],
                timeout_seconds=limits['timeout_seconds'],
            )
        )
        if result.get("status") != "PASS":
            receipt = result.get("execution_receipt")
            if not isinstance(receipt, Mapping):
                receipt = {
                    "schema_version": "SettingsStructuredChatExecutionReceipt-v2",
                    "status": "FAILED",
                    "purpose": "workflow_model_exam",
                    "requested_model": model_name,
                    "returned_model": result.get("returned_model"),
                    "token_usage": None,
                    "actual_cost": None,
                    "estimated_cost_cny": None,
                    "cost_evidence": "ATTEMPT_EVIDENCE_UNAVAILABLE",
                }
            _write_json_create_only(
                stage_root / "receipts" / f"{call_id}.json",
                {
                    **deepcopy(dict(receipt)),
                    "run_id": run_id,
                    "call_id": call_id,
                    "call_kind": call_kind,
                    "profile_ref": target["config_id"],
                    "pre_send_claim_sha256": _sha256(claim),
                    "provider_output_sha256": None,
                },
            )
            state["stage_calls"] += 1
            state["provider_calls"] += int(result.get("provider_calls") or 0)
            state["external_model_calls"] += int(
                result.get("external_model_calls") or 0
            )
            state["external_network_calls"] += int(
                result.get("external_network_calls") or 0
            )
            state["external_process_launches"] += int(
                result.get("external_process_launches") or 0
            )
            estimate = receipt.get("estimated_cost_cny")
            if isinstance(estimate, (int, float)) and not isinstance(estimate, bool):
                state["estimated_cost_cny"] += float(estimate)
            reason = result.get("reason")
            raise ValueError(
                reason if isinstance(reason, str) and reason else "WORKFLOW_MODEL_EXAM_CALL_FAILED"
            )
        receipt = self._validate_call_result(result, model_name, profile_kind)
        response = deepcopy(dict(result["response"]))
        output_receipt = _write_json_create_only(
            stage_root / "provider_outputs" / f"{call_id}.json", response
        )
        receipt_payload = {
            **deepcopy(dict(receipt)),
            "run_id": run_id,
            "call_id": call_id,
            "call_kind": call_kind,
            "profile_ref": target["config_id"],
            "pre_send_claim_sha256": _sha256(claim),
            "provider_output_sha256": output_receipt["sha256"],
        }
        _write_json_create_only(
            stage_root / "receipts" / f"{call_id}.json", receipt_payload
        )
        state["stage_calls"] += 1
        state["provider_calls"] += int(result["provider_calls"])
        state["external_model_calls"] += int(result["external_model_calls"])
        state["external_network_calls"] += int(result["external_network_calls"])
        state["external_process_launches"] += int(
            result["external_process_launches"]
        )
        state["estimated_cost_cny"] += float(
            receipt.get("estimated_cost_cny") or 0.0
        )
        return response

    def _run_shard(
        self,
        *,
        modules: Mapping[str, Any],
        run_id: str,
        stage_root: Path,
        state: dict[str, Any],
        target: Mapping[str, Any],
        form: Mapping[str, Any],
        artifact: str,
        target_ids: list[str],
        schema: Mapping[str, Any],
        structured_chat: Callable[..., Mapping[str, Any]],
    ) -> dict[str, Any]:
        protocol = modules["protocol"]
        prompt = protocol.build_artifact_prompt(
            dict(form), artifact, target_ids=target_ids, schema=dict(schema)
        )
        response = self._provider_call(
            run_id=run_id,
            stage_root=stage_root,
            state=state,
            target=target,
            prompt=prompt,
            response_schema=schema,
            structured_chat=structured_chat,
            call_kind=f"INITIAL_{artifact.upper()}",
        )
        audit = protocol.audit_artifact(
            form=dict(form),
            response=response,
            schema=dict(schema),
            artifact=artifact,
            target_ids=target_ids,
        )
        audit_name = (
            f"{artifact}-{target_ids[0]}-{target_ids[-1]}-initial.json"
        )
        _write_json_create_only(stage_root / "audits" / audit_name, audit)
        if audit["status"] == "PASS":
            return response
        source_collection = "cases" if artifact == "fact_map" else "packs"
        source_id_field = "case_id" if artifact == "fact_map" else "pack_id"
        target_source = [
            deepcopy(row)
            for row in form.get(source_collection, [])
            if isinstance(row, Mapping) and row.get(source_id_field) in target_ids
        ]
        partition_fields = [
            "research_question",
            "research_object",
            "method",
            "key_results",
            "author_conclusion",
            "boundary_conditions",
        ]
        artifact_contract = (
            {
                "row_identity": "case_id",
                "exact_target_order": target_ids,
                "rule": "Return exactly one fact_map row per target case_id.",
            }
            if artifact == "fact_map"
            else {
                "row_identity": "pack_id",
                "exact_target_order": target_ids,
                "partition_fields": partition_fields,
                "rules": [
                    "For each pack, every source case_id must occur exactly once across partition_fields[].case_ids.",
                    "No case_id may occur in two partition fields; move it to the single best matching field.",
                    "The union of partition_fields[].case_ids must equal that pack's source case_ids exactly.",
                    "Partition fields are buckets, not one-item slots: multiple case_ids in one field are required when the source pack has more cases than partition fields.",
                    "Before returning, verify that both total assignment count and unique assignment count equal the exact source case count for every pack.",
                    "key_data_case_ids is a separate summary list and does not relax the exact partition rule.",
                    "Use only chunk_id values present in the same source pack as anchor_ids.",
                ],
            }
        )

        def partition_diagnostics(current: Mapping[str, Any]) -> list[dict[str, Any]]:
            if artifact != "card_projection":
                return []
            source_by_id = {
                str(row.get("pack_id")): row
                for row in target_source
                if isinstance(row, Mapping)
            }
            current_rows = current.get("card_projection")
            current_by_id = (
                {
                    str(row.get("pack_id")): row
                    for row in current_rows
                    if isinstance(row, Mapping)
                }
                if isinstance(current_rows, list)
                else {}
            )
            diagnostics: list[dict[str, Any]] = []
            for pack_id in target_ids:
                source = source_by_id.get(pack_id, {})
                expected = [
                    str(case_id) for case_id in source.get("case_ids", [])
                    if isinstance(case_id, str)
                ]
                row = current_by_id.get(pack_id, {})
                observed_by_field: dict[str, list[str]] = {}
                assigned: list[str] = []
                for field in partition_fields:
                    field_value = row.get(field)
                    case_ids = (
                        field_value.get("case_ids", [])
                        if isinstance(field_value, Mapping)
                        else []
                    )
                    normalized = [
                        str(case_id) for case_id in case_ids
                        if isinstance(case_id, str)
                    ]
                    observed_by_field[field] = normalized
                    assigned.extend(normalized)
                counts = {
                    case_id: assigned.count(case_id) for case_id in set(assigned)
                }
                diagnostics.append(
                    {
                        "pack_id": pack_id,
                        "exact_source_case_ids": expected,
                        "expected_assignment_count": len(expected),
                        "observed_assignment_count": len(assigned),
                        "observed_unique_count": len(set(assigned)),
                        "missing_case_ids": [
                            case_id for case_id in expected if case_id not in assigned
                        ],
                        "duplicate_case_ids": sorted(
                            case_id for case_id, count in counts.items() if count > 1
                        ),
                        "extra_case_ids": sorted(set(assigned) - set(expected)),
                        "current_partition": observed_by_field,
                    }
                )
            return diagnostics

        def partition_decision_targets(
            diagnostics: list[dict[str, Any]],
        ) -> list[dict[str, Any]]:
            targets: list[dict[str, Any]] = []
            for diagnostic in diagnostics:
                pack_id = str(diagnostic["pack_id"])
                current_partition = diagnostic["current_partition"]
                missing = set(diagnostic["missing_case_ids"])
                duplicates = set(diagnostic["duplicate_case_ids"])
                for case_id in diagnostic["exact_source_case_ids"]:
                    if case_id not in missing and case_id not in duplicates:
                        continue
                    current_fields = [
                        field for field in partition_fields
                        if case_id in current_partition[field]
                    ]
                    targets.append(
                        {
                            "pack_id": pack_id,
                            "case_id": case_id,
                            "operation": (
                                "ASSIGN_MISSING"
                                if case_id in missing
                                else "KEEP_ONE_REMOVE_DUPLICATES"
                            ),
                            "allowed_fields": (
                                partition_fields
                                if case_id in missing
                                else current_fields
                            ),
                        }
                    )
            return targets

        def partition_patch_schema(
            decision_targets: list[dict[str, Any]],
        ) -> dict[str, Any]:
            case_ids = [str(row["case_id"]) for row in decision_targets]
            return {
                "type": "object",
                "additionalProperties": False,
                "required": ["decisions"],
                "properties": {
                    "decisions": {
                        "type": "array",
                        "minItems": len(decision_targets),
                        "maxItems": len(decision_targets),
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["pack_id", "case_id", "field"],
                            "properties": {
                                "pack_id": {
                                    "type": "string",
                                    "enum": target_ids,
                                },
                                "case_id": {
                                    "type": "string",
                                    "enum": case_ids,
                                },
                                "field": {
                                    "type": "string",
                                    "enum": partition_fields,
                                },
                            },
                        },
                    }
                },
            }

        def merge_partition_patch(
            current: Mapping[str, Any],
            patch: Mapping[str, Any],
            diagnostics: list[dict[str, Any]],
            decision_targets: list[dict[str, Any]],
        ) -> dict[str, Any]:
            decisions = patch.get("decisions")
            if not isinstance(decisions, list):
                raise ValueError("WORKFLOW_MODEL_EXAM_PARTITION_DECISIONS_REQUIRED")
            expected_pairs = [
                (str(row["pack_id"]), str(row["case_id"]))
                for row in decision_targets
            ]
            observed_pairs = [
                (
                    str(row.get("pack_id")),
                    str(row.get("case_id")),
                )
                for row in decisions
                if isinstance(row, Mapping)
            ]
            if observed_pairs != expected_pairs or len(decisions) != len(
                decision_targets
            ):
                raise ValueError(
                    "WORKFLOW_MODEL_EXAM_PARTITION_DECISION_EXACT_SET_MISMATCH"
                )
            allowed_by_pair = {
                (str(row["pack_id"]), str(row["case_id"])): set(
                    row["allowed_fields"]
                )
                for row in decision_targets
            }
            selected: dict[tuple[str, str], str] = {}
            for decision in decisions:
                if not isinstance(decision, Mapping):
                    raise ValueError(
                        "WORKFLOW_MODEL_EXAM_PARTITION_DECISION_MAPPING_REQUIRED"
                    )
                pair = (
                    str(decision.get("pack_id")),
                    str(decision.get("case_id")),
                )
                field = decision.get("field")
                if (
                    not isinstance(field, str)
                    or field not in allowed_by_pair[pair]
                ):
                    raise ValueError(
                        "WORKFLOW_MODEL_EXAM_PARTITION_DECISION_FIELD_INVALID"
                    )
                selected[pair] = field
            merged = deepcopy(dict(current))
            rows = merged.get("card_projection")
            if not isinstance(rows, list):
                raise ValueError("WORKFLOW_MODEL_EXAM_PARTITION_ROWS_REQUIRED")
            diagnostic_by_id = {
                str(row["pack_id"]): row for row in diagnostics
            }
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError(
                        "WORKFLOW_MODEL_EXAM_PARTITION_ROW_MAPPING_REQUIRED"
                    )
                pack_id = str(row.get("pack_id"))
                diagnostic = diagnostic_by_id.get(pack_id)
                if diagnostic is None:
                    raise ValueError(
                        "WORKFLOW_MODEL_EXAM_PARTITION_DIAGNOSTIC_MISSING"
                    )
                exact_source = set(diagnostic["exact_source_case_ids"])
                for field in partition_fields:
                    field_value = row.get(field)
                    if not isinstance(field_value, dict) or not isinstance(
                        field_value.get("case_ids"), list
                    ):
                        raise ValueError(
                            "WORKFLOW_MODEL_EXAM_PARTITION_FIELD_INVALID"
                        )
                    field_value["case_ids"] = [
                        case_id for case_id in field_value["case_ids"]
                        if case_id in exact_source
                    ]
                for decision_target in decision_targets:
                    if decision_target["pack_id"] != pack_id:
                        continue
                    case_id = str(decision_target["case_id"])
                    for field in partition_fields:
                        row[field]["case_ids"] = [
                            value for value in row[field]["case_ids"]
                            if value != case_id
                        ]
                    row[selected[(pack_id, case_id)]]["case_ids"].append(case_id)
            return merged

        current_audit = audit
        for repair_round in range(1, MAX_STRUCTURAL_REPAIR_ROUNDS + 1):
            diagnostics = partition_diagnostics(response)
            decision_targets = partition_decision_targets(diagnostics)
            use_directed_partition_patch = (
                artifact == "card_projection"
                and repair_round == MAX_STRUCTURAL_REPAIR_ROUNDS
                and bool(decision_targets)
            )
            if use_directed_partition_patch:
                patch_schema = partition_patch_schema(decision_targets)
                repair_prompt = json.dumps(
                    {
                        "instruction": (
                            "Return only the requested partition decisions in exact order. "
                            "For each duplicate case choose the one current semantic field to "
                            "keep; for each missing case choose the best semantic field to add. "
                            "Do not rewrite the projection and do not omit a decision."
                        ),
                        "repair_round": repair_round,
                        "artifact": artifact,
                        "decision_targets": decision_targets,
                        "partition_diagnostics": diagnostics,
                        "target_source": target_source,
                        "current_response": response,
                        "output_schema": patch_schema,
                        "source_task_prompt_sha256": hashlib.sha256(
                            prompt.encode("utf-8")
                        ).hexdigest().upper(),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                patch_response = self._provider_call(
                    run_id=run_id,
                    stage_root=stage_root,
                    state=state,
                    target=target,
                    prompt=repair_prompt,
                    response_schema=patch_schema,
                    structured_chat=structured_chat,
                    call_kind=(
                        "DIRECTED_PARTITION_PATCH_CARD_PROJECTION_ROUND_2"
                    ),
                )
                response = merge_partition_patch(
                    response,
                    patch_response,
                    diagnostics,
                    decision_targets,
                )
                repaired_audit = protocol.audit_artifact(
                    form=dict(form),
                    response=response,
                    schema=dict(schema),
                    artifact=artifact,
                    target_ids=target_ids,
                )
                _write_json_create_only(
                    stage_root
                    / "partition_patches"
                    / (
                        f"{artifact}-{target_ids[0]}-{target_ids[-1]}-"
                        "round-02.json"
                    ),
                    {
                        "schema_version": "WorkflowModelExamPartitionPatch-v1",
                        "decision_targets": decision_targets,
                        "patch_response": patch_response,
                        "merged_response_sha256": _sha256(response),
                        "audit": repaired_audit,
                    },
                )
            else:
                repair_prompt = json.dumps(
                    {
                        "instruction": (
                            "Repair all and only this failed Card Distiller shard. Return "
                            "the complete exact target set in the original order and no other rows. "
                            "Do not repeat the current response unchanged; correct every listed defect. "
                            "For card_projection, do not stop after deduplicating: reassign every "
                            "missing source case_id so each exact source case appears once."
                        ),
                        "repair_round": repair_round,
                        "maximum_repair_rounds": MAX_STRUCTURAL_REPAIR_ROUNDS,
                        "artifact": artifact,
                        "target_ids": target_ids,
                        "defects": current_audit["defects"],
                        "artifact_contract": artifact_contract,
                        "partition_diagnostics": diagnostics,
                        "target_source": target_source,
                        "current_response": response,
                        "output_schema": schema,
                        "source_task_prompt_sha256": hashlib.sha256(
                            prompt.encode("utf-8")
                        ).hexdigest().upper(),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                response = self._provider_call(
                    run_id=run_id,
                    stage_root=stage_root,
                    state=state,
                    target=target,
                    prompt=repair_prompt,
                    response_schema=schema,
                    structured_chat=structured_chat,
                    call_kind=(
                        f"STRUCTURAL_REPAIR_{artifact.upper()}_ROUND_{repair_round}"
                    ),
                )
                repaired_audit = protocol.audit_artifact(
                    form=dict(form),
                    response=response,
                    schema=dict(schema),
                    artifact=artifact,
                    target_ids=target_ids,
                )
            repaired_audit_name = (
                f"{artifact}-{target_ids[0]}-{target_ids[-1]}-repaired.json"
                if repair_round == 1
                else (
                    f"{artifact}-{target_ids[0]}-{target_ids[-1]}-"
                    f"repair-{repair_round:02d}.json"
                )
            )
            _write_json_create_only(
                stage_root / "audits" / repaired_audit_name, repaired_audit
            )
            if repaired_audit["status"] == "PASS":
                return response
            current_audit = repaired_audit
        raise ValueError("WORKFLOW_MODEL_EXAM_SHARD_CONTRACT_UNCLOSED")

    @staticmethod
    def _project_directed_repair_schema(
        repair_schema: Mapping[str, Any],
        *,
        form_id: str,
        case_ids: list[str],
    ) -> dict[str, Any]:
        form_key = f"form_{form_id}"
        properties = repair_schema.get("properties")
        if (
            form_id not in {"A", "B"}
            or not case_ids
            or len(case_ids) != len(set(case_ids))
            or not isinstance(properties, Mapping)
            or not isinstance(properties.get(form_key), Mapping)
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_REPAIR_SHARD_SCHEMA_INVALID")
        projected = deepcopy(dict(repair_schema))
        projected["required"] = [form_key]
        projected["properties"] = {
            form_key: deepcopy(dict(properties[form_key]))
        }
        try:
            fact_map = projected["properties"][form_key]["properties"]["fact_map"]
            case_id_schema = fact_map["items"]["properties"]["case_id"]
        except (KeyError, TypeError) as error:
            raise ValueError(
                "WORKFLOW_MODEL_EXAM_REPAIR_SHARD_SCHEMA_INVALID"
            ) from error
        fact_map["minItems"] = len(case_ids)
        fact_map["maxItems"] = len(case_ids)
        case_id_schema["enum"] = list(case_ids)
        return projected

    def _run_exam_directed_repair(
        self,
        *,
        run_id: str,
        stage_root: Path,
        state: dict[str, Any],
        target: Mapping[str, Any],
        package: Mapping[str, Any],
        structured_chat: Callable[..., Mapping[str, Any]],
        call_kind: str = "WHOLE_EXAM_DIRECTED_REPAIR",
    ) -> dict[str, Any]:
        repair_schema = package.get("repair_schema")
        contract = package.get("contract")
        repair_targets = package.get("repair_targets")
        prompt = package.get("prompt")
        if (
            not isinstance(repair_schema, Mapping)
            or not isinstance(contract, Mapping)
            or not isinstance(repair_targets, Mapping)
            or not isinstance(prompt, str)
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_REPAIR_PACKAGE_INVALID")
        exact_sets = contract.get("wrong_item_exact_set")
        if not isinstance(exact_sets, Mapping):
            raise ValueError("WORKFLOW_MODEL_EXAM_REPAIR_PACKAGE_INVALID")
        total_wrong = sum(
            len(case_ids)
            for case_ids in exact_sets.values()
            if isinstance(case_ids, list)
        )
        active_form_ids = [
            form_id
            for form_id in ("A", "B")
            if isinstance(exact_sets.get(form_id), list)
            and bool(exact_sets[form_id])
        ]
        is_local = target.get("kind") == "LOCAL"
        shard_size = 12 if is_local else 36
        try:
            parent_prompt = json.loads(prompt)
        except json.JSONDecodeError as error:
            raise ValueError(
                "WORKFLOW_MODEL_EXAM_REPAIR_PROMPT_INVALID"
            ) from error
        if not isinstance(parent_prompt, dict):
            raise ValueError("WORKFLOW_MODEL_EXAM_REPAIR_PROMPT_INVALID")
        if len(active_form_ids) == 1 and total_wrong <= shard_size:
            guided_prompt = self._card_distiller_repair_issue_guidance(
                parent_prompt, repair_targets
            )
            return self._provider_call(
                run_id=run_id,
                stage_root=stage_root,
                state=state,
                target=target,
                prompt=json.dumps(
                    guided_prompt,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                response_schema=repair_schema,
                structured_chat=structured_chat,
                call_kind=call_kind,
            )

        merged: dict[str, Any] = {}
        shard_receipts: list[dict[str, Any]] = []
        for form_id in ("A", "B"):
            case_ids = exact_sets.get(form_id)
            targets = repair_targets.get(form_id)
            if not isinstance(case_ids, list) or not isinstance(targets, list):
                raise ValueError("WORKFLOW_MODEL_EXAM_REPAIR_PACKAGE_INVALID")
            if not case_ids:
                continue
            if (
                any(not isinstance(case_id, str) or not case_id for case_id in case_ids)
                or len(case_ids) != len(set(case_ids))
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_REPAIR_EXACT_SET_INVALID")
            target_by_case: dict[str, Mapping[str, Any]] = {}
            for row in targets:
                if not isinstance(row, Mapping):
                    raise ValueError("WORKFLOW_MODEL_EXAM_REPAIR_TARGET_INVALID")
                case_id = row.get("case_id")
                if not isinstance(case_id, str) or case_id in target_by_case:
                    raise ValueError("WORKFLOW_MODEL_EXAM_REPAIR_TARGET_INVALID")
                target_by_case[case_id] = row
            if list(target_by_case) != case_ids:
                raise ValueError("WORKFLOW_MODEL_EXAM_REPAIR_TARGET_ORDER_INVALID")

            chunks = [
                case_ids[start : start + shard_size]
                for start in range(0, len(case_ids), shard_size)
            ]
            merged_rows: list[Any] = []
            for chunk_index, chunk_case_ids in enumerate(chunks, start=1):
                chunk_schema = self._project_directed_repair_schema(
                    repair_schema,
                    form_id=form_id,
                    case_ids=chunk_case_ids,
                )
                chunk_prompt = deepcopy(parent_prompt)
                chunk_prompt["wrong_item_exact_set"] = {
                    form_id: list(chunk_case_ids)
                }
                chunk_prompt["repair_targets"] = {
                    form_id: [
                        deepcopy(dict(target_by_case[case_id]))
                        for case_id in chunk_case_ids
                    ]
                }
                chunk_prompt["output_schema"] = chunk_schema
                chunk_prompt = self._card_distiller_repair_issue_guidance(
                    chunk_prompt,
                    chunk_prompt["repair_targets"],
                )
                chunk_prompt["transport_shard"] = {
                    "schema_version": "WorkflowModelExamRepairTransportShard-v1",
                    "parent_contract_sha256": contract.get("contract_sha256"),
                    "parent_package_sha256": package.get("package_sha256"),
                    "form_id": form_id,
                    "chunk_index": chunk_index,
                    "chunk_count": len(chunks),
                    "case_ids": list(chunk_case_ids),
                    "case_ids_sha256": _sha256(chunk_case_ids),
                }
                chunk_prompt_text = json.dumps(
                    chunk_prompt,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                shard_call_kind = (
                    f"{call_kind}_FORM_{form_id}_"
                    f"CHUNK_{chunk_index}_OF_{len(chunks)}"
                )
                response = self._provider_call(
                    run_id=run_id,
                    stage_root=stage_root,
                    state=state,
                    target=target,
                    prompt=chunk_prompt_text,
                    response_schema=chunk_schema,
                    structured_chat=structured_chat,
                    call_kind=shard_call_kind,
                )
                form_key = f"form_{form_id}"
                response_form = response.get(form_key)
                if not isinstance(response_form, Mapping):
                    raise ValueError(
                        "WORKFLOW_MODEL_EXAM_REPAIR_SHARD_RESPONSE_INVALID"
                    )
                rows = response_form.get("fact_map")
                if not isinstance(rows, list):
                    raise ValueError(
                        "WORKFLOW_MODEL_EXAM_REPAIR_SHARD_RESPONSE_INVALID"
                    )
                observed_ids = [
                    row.get("case_id") if isinstance(row, Mapping) else None
                    for row in rows
                ]
                if observed_ids != chunk_case_ids:
                    raise ValueError(
                        "WORKFLOW_MODEL_EXAM_REPAIR_SHARD_EXACT_SET_MISMATCH"
                    )
                merged_rows.extend(deepcopy(rows))
                shard_receipts.append(
                    {
                        "call_kind": shard_call_kind,
                        "form_id": form_id,
                        "chunk_index": chunk_index,
                        "chunk_count": len(chunks),
                        "case_ids": list(chunk_case_ids),
                        "case_ids_sha256": _sha256(chunk_case_ids),
                        "prompt_sha256": hashlib.sha256(
                            chunk_prompt_text.encode("utf-8")
                        ).hexdigest().upper(),
                        "response_schema_sha256": _sha256(chunk_schema),
                        "response_sha256": _sha256(response),
                    }
                )
            merged[f"form_{form_id}"] = {"fact_map": merged_rows}

        profile_kind = str(target.get("kind"))
        transport_receipt = {
                "schema_version": "WorkflowModelExamRepairTransportReceipt-v1",
                "profile_kind": profile_kind,
                "route": (
                    "EXISTING_SETTINGS_API_TRANSPORT"
                    if profile_kind == "API"
                    else (
                        ("DECLARATIVE_COMMAND_TEMPLATE" if target.get("adapter_id")=="command_template" else "CODEX_CLI_SUBSCRIPTION")
                        if profile_kind == "CLI"
                        else "OLLAMA_LOOPBACK"
                    )
                ),
                "egress": (
                    "PROVIDER_API"
                    if profile_kind == "API"
                    else (("CLI_PROVIDER_MANAGED" if target.get("adapter_id")=="command_template" else "CODEX_CLI") if profile_kind == "CLI" else "LOOPBACK_ONLY")
                ),
                "parent_contract_sha256": contract.get("contract_sha256"),
                "parent_package_sha256": package.get("package_sha256"),
                "wrong_item_count": total_wrong,
                "shard_size": shard_size,
                "shard_count": len(shard_receipts),
                "shard_activation": (
                    "MULTI_FORM_OR_PROFILE_CASE_LIMIT"
                ),
                "cross_form_response_required_from_provider": False,
                "shards": shard_receipts,
                "merged_response_sha256": _sha256(merged),
                "provider_received_answer_key": False,
            }
        _write_json_create_only(
            stage_root
            / (
                re.sub(r"[^A-Za-z0-9_-]", "_", call_kind).lower()
                + "_transport_shards.json"
            ),
            transport_receipt,
        )
        legacy_receipt = stage_root / "whole_exam_repair_transport_shards.json"
        if not legacy_receipt.exists():
            _write_json_create_only(legacy_receipt, transport_receipt)
        return merged

    @staticmethod
    def _card_distiller_repair_issue_guidance(
        prompt: Mapping[str, Any],
        repair_targets: Mapping[str, Any],
    ) -> dict[str, Any]:
        issue_codes = sorted(
            {
                str(code)
                for targets in repair_targets.values()
                if isinstance(targets, list)
                for target in targets
                if isinstance(target, Mapping)
                for code in (target.get("issue_codes") or [])
                if isinstance(code, str) and code
            }
        )
        guidance_by_code = {
            "COMPARATOR_MISSING": (
                "When the source unit provides a same-context sibling group "
                "or reference occurrence for the target, populate comparator "
                "with its label, value, and unit. Use null only when the "
                "source contains no comparator context."
            ),
            "COMPARATOR_CONTEXT_INCOMPLETE": (
                "Do not clear an existing comparator. Preserve it and add "
                "the missing source-expressed label, numeric value, and unit; "
                "a group label alone is incomplete."
            ),
        }
        projected = deepcopy(dict(prompt))
        projected["successor_issue_guidance"] = {
            "schema_version": CARD_DISTILLER_REPAIR_ISSUE_GUIDANCE_REVISION,
            "gold_consulted": False,
            "expected_values_disclosed": False,
            "preserve_unfailed_current_row_fields": True,
            "issue_codes": issue_codes,
            "guidance": {
                code: guidance_by_code[code]
                for code in issue_codes
                if code in guidance_by_code
            },
            "instruction": (
                "Apply only guidance for issue codes present in this shard. "
                "Derive every value from source_case.source_unit.text."
            ),
        }
        return projected

    def _stage_score_projection(
        self,
        *,
        protocol: Any,
        forms: Mapping[str, Mapping[str, Any]],
        initial_scores: Mapping[str, Mapping[str, Any]],
        final_scores: Mapping[str, Mapping[str, Any]],
        repair_rounds: int,
    ) -> dict[str, Any]:
        del protocol, forms, initial_scores, final_scores, repair_rounds
        return {}

    def _run_stage(
        self,
        *,
        modules: Mapping[str, Any],
        run_id: str,
        run_root: Path,
        stage_name: str,
        target: Mapping[str, Any],
        forms: Mapping[str, Mapping[str, Any]],
        golds: Mapping[str, Mapping[str, Any]],
        budget_cap_cny: float,
        state: dict[str, Any],
        structured_chat: Callable[..., Mapping[str, Any]],
        fact_shard_size: int = 36,
        projection_shard_size: int = 4,
        stage_call_cap: int = MAX_CALLS_PER_STAGE,
    ) -> dict[str, Any]:
        protocol = modules["protocol"]
        repair = modules["repair"]
        stage_root = run_root / stage_name
        stage_root.mkdir(parents=True, exist_ok=False)
        state["stage_calls"] = 0
        state["stage_call_cap"] = _distiller_call_bound(forms, str(target['kind']))
        stage_cost_before = state["estimated_cost_cny"]
        # Coverage views can retain different atomic packs in A and B. Each
        # response must satisfy its own exact form, not the other form's size.
        schemas_by_form = {fid: protocol.output_schema(
            fact_count=len(forms[fid]["cases"]), pack_count=len(forms[fid]["packs"]))
            for fid in ("A", "B")}
        full_schema = schemas_by_form["A"]
        repair_score_kwargs = ({} if schemas_by_form["A"] == schemas_by_form["B"]
                               else {"schemas_by_form": schemas_by_form})
        responses: dict[str, dict[str, Any]] = {}
        candidate_output_metrics_by_form: dict[str, dict[str, Any]] = {}
        for slot in ("A", "B"):
            form = forms[slot]
            form_schema = schemas_by_form[slot]
            fact_ids = [str(row["case_id"]) for row in form["cases"]]
            pack_ids = [str(row["pack_id"]) for row in form["packs"]]
            facts: list[dict[str, Any]] = []
            projections: list[dict[str, Any]] = []
            for start in range(0, len(fact_ids), fact_shard_size):
                target_ids = fact_ids[start : start + fact_shard_size]
                schema = protocol.artifact_schema(
                    form_schema, "fact_map", target_count=len(target_ids)
                )
                shard = self._run_shard(
                    modules=modules,
                    run_id=run_id,
                    stage_root=stage_root,
                    state=state,
                    target=target,
                    form=form,
                    artifact="fact_map",
                    target_ids=target_ids,
                    schema=schema,
                    structured_chat=structured_chat,
                )
                facts.extend(deepcopy(shard["fact_map"]))
            for start in range(0, len(pack_ids), projection_shard_size):
                target_ids = pack_ids[start : start + projection_shard_size]
                schema = protocol.artifact_schema(
                    form_schema, "card_projection", target_count=len(target_ids)
                )
                shard = self._run_shard(
                    modules=modules,
                    run_id=run_id,
                    stage_root=stage_root,
                    state=state,
                    target=target,
                    form=form,
                    artifact="card_projection",
                    target_ids=target_ids,
                    schema=schema,
                    structured_chat=structured_chat,
                )
                projections.extend(deepcopy(shard["card_projection"]))
            response = {"fact_map": facts, "card_projection": projections}
            protocol.validate_response_shape(
                response, dict(form), deepcopy(form_schema)
            )
            output_metrics = _card_distiller_candidate_output_metrics(response)
            output_metrics["core_x2_reference_exceeded"] = (
                _card_distiller_candidate_artifact_limit_exceeded(output_metrics)
            )
            output_metrics["core_x2_reference_semantics"] = (
                "DIAGNOSTIC_ONLY_NOT_INVALIDATION_MODEL_FAIL_OR_REPAIR"
            )
            candidate_output_metrics_by_form[slot] = output_metrics
            responses[slot] = response
        _write_json_create_only(stage_root / "responses_frozen.json", responses)

        initial_scores = {
            slot: protocol.score_response(
                deepcopy(responses[slot]),
                deepcopy(dict(forms[slot])),
                deepcopy(dict(golds[slot])),
                deepcopy(schemas_by_form[slot]),
            )
            for slot in ("A", "B")
        }
        initial_dual = protocol.aggregate_dual_form(
            initial_scores["A"], initial_scores["B"]
        )
        first_pass_role_ability_score = float(initial_dual["mean"])
        final_scores = deepcopy(initial_scores)
        wrong_count = sum(
            int(score["critical_count"]) + int(score["major_count"])
            for score in initial_scores.values()
        )
        failed_exam_item_count = 0
        repair_rounds = 0
        repair_lifecycle: list[dict[str, Any]] = []
        cumulative_penalty: dict[str, Any] | None = None
        if wrong_count:
            current_responses = deepcopy(responses)
            current_scores = deepcopy(initial_scores)
            initial_contract: Mapping[str, Any] | None = None
            first_round_exact_sets: dict[str, list[str]] | None = None
            round_two_burden: dict[str, Any] | None = None
            unchanged_surface_sha256 = _sha256(
                {
                    "forms": forms,
                    "golds_excluded": True,
                    "schema": full_schema,
                    "category_id": self.EXAM_CATEGORY_ID,
                }
            )
            for round_number in range(
                1, self.SEMANTIC_REPAIR_ROUND_LIMIT + 1
            ):
                source_scores = deepcopy(current_scores)
                if round_number == 2:
                    assert first_round_exact_sets is not None
                    for form_id in ("A", "B"):
                        allowed = set(first_round_exact_sets[form_id])
                        source_scores[form_id]["details"] = [
                            deepcopy(detail)
                            for detail in source_scores[form_id].get(
                                "details", []
                            )
                            if isinstance(detail, Mapping)
                            and detail.get("case_id") in allowed
                            and (
                                detail.get("critical_reasons")
                                or detail.get("major_reasons")
                            )
                        ]
                    if not any(
                        source_scores[form_id]["details"]
                        for form_id in ("A", "B")
                    ):
                        break
                package = repair.build_exam_directed_repair_package(
                    forms=forms,
                    original_responses=current_responses,
                    original_scores=source_scores,
                    schema=full_schema,
                )
                exact_sets = {
                    form_id: list(case_ids)
                    for form_id, case_ids in package["contract"][
                        "wrong_item_exact_set"
                    ].items()
                }
                if first_round_exact_sets is None:
                    first_round_exact_sets = deepcopy(exact_sets)
                    initial_contract = deepcopy(package["contract"])
                lifecycle = build_repair_round(
                    round_number=round_number,
                    category_id=self.EXAM_CATEGORY_ID,
                    targets_by_form=exact_sets,
                    prior_targets_by_form=(
                        first_round_exact_sets
                        if round_number == 2
                        else None
                    ),
                    prompt_projection=json.loads(package["prompt"]),
                    unchanged_surface_sha256=unchanged_surface_sha256,
                )
                repair_response = self._run_exam_directed_repair(
                    run_id=run_id,
                    stage_root=stage_root,
                    state=state,
                    target=target,
                    package=package,
                    structured_chat=structured_chat,
                    call_kind=(
                        f"WHOLE_EXAM_DIRECTED_REPAIR_ROUND_{round_number}"
                    ),
                )
                merge = repair.merge_exam_directed_repair(
                    forms=forms,
                    original_responses=current_responses,
                    repair_response=repair_response,
                    schema=full_schema,
                    repair_contract=package["contract"],
                )
                current_responses = deepcopy(merge["responses"])
                current_scores = {
                    form_id: protocol.score_response(
                        deepcopy(current_responses[form_id]),
                        deepcopy(dict(forms[form_id])),
                        deepcopy(dict(golds[form_id])),
                        deepcopy(schemas_by_form[form_id]),
                    )
                    for form_id in ("A", "B")
                }
                if round_number == 2:
                    second = repair.score_exam_after_directed_repair(
                        forms=forms,
                        golds=golds,
                        original_scores=source_scores,
                        merged_responses=current_responses,
                        schema=full_schema,
                        repair_contract=package["contract"],
                        **repair_score_kwargs,
                    )
                    round_two_burden = {
                        "by_form": {
                            form_id: {
                                "nonlinear_category_penalty": float(
                                    second["repair_penalty"][
                                        "initial_burden_by_form"
                                    ][form_id]["total"]
                                ),
                                "fixed_penalty": 0.0,
                                "volume_penalty": float(
                                    second["repair_penalty"][
                                        "initial_burden_by_form"
                                    ][form_id]["wrong_item_volume"]
                                ),
                            }
                            for form_id in ("A", "B")
                        }
                    }
                repair_lifecycle.append(lifecycle)
                if not sum(
                    int(score["critical_count"])
                    + int(score["major_count"])
                    for score in current_scores.values()
                ):
                    break
            assert initial_contract is not None
            final = repair.score_exam_after_directed_repair(
                forms=forms,
                golds=golds,
                original_scores=initial_scores,
                merged_responses=current_responses,
                schema=full_schema,
                repair_contract=initial_contract,
                **repair_score_kwargs,
            )
            penalty_rounds = [
                {
                    "round_number": 1,
                    "category_id": self.EXAM_CATEGORY_ID,
                    "nonlinear_category_penalty": float(
                        final["repair_penalty"][
                            "initial_burden_total_mean"
                        ]
                    ),
                    "fixed_penalty": 0.0,
                    "volume_penalty": 0.0,
                }
            ]
            if round_two_burden is not None:
                second_by_form = round_two_burden["by_form"]
                for form_id in ("A", "B"):
                    extra = sum(
                        float(second_by_form[form_id][field])
                        for field in (
                            "nonlinear_category_penalty",
                            "fixed_penalty",
                            "volume_penalty",
                        )
                    )
                    adjusted = max(
                        0.0,
                        float(final["final_form_scores"][form_id]) - extra,
                    )
                    final["final_form_scores"][form_id] = round(
                        adjusted, 2
                    )
                    final["forms"][form_id][
                        "round_two_additional_burden"
                    ] = deepcopy(second_by_form[form_id])
                    final["forms"][form_id]["final_form_score"] = round(
                        adjusted, 2
                    )
                final["final_score"] = round(
                    sum(final["final_form_scores"].values()) / 2.0, 2
                )
                penalty_rounds.append(
                    {
                        "round_number": 2,
                        "category_id": self.EXAM_CATEGORY_ID,
                        "nonlinear_category_penalty": sum(
                            float(row["nonlinear_category_penalty"])
                            for row in second_by_form.values()
                        )
                        / 2.0,
                        "fixed_penalty": sum(
                            float(row["fixed_penalty"])
                            for row in second_by_form.values()
                        )
                        / 2.0,
                        "volume_penalty": sum(
                            float(row["volume_penalty"])
                            for row in second_by_form.values()
                        )
                        / 2.0,
                    }
                )
            cumulative_penalty = cumulative_category_repair_penalty(
                penalty_rounds
            )
            score = float(final["final_score"])
            verdict = (
                "PASS"
                if final["quality_hard_gate"] == "PASS"
                and score >= PASSING_SCORE
                else "FAIL"
            )
            failed_exam_item_count = int(final["failed_exam_item_count"])
            repair_rounds = len(repair_lifecycle)
            final_scores = deepcopy(current_scores)
        else:
            dual = protocol.aggregate_dual_form(
                initial_scores["A"], initial_scores["B"]
            )
            score = float(dual["mean"])
            verdict = (
                "PASS"
                if dual["quality_hard_gate"] == "PASS" and score >= PASSING_SCORE
                else "FAIL"
            )
        score_projection = self._stage_score_projection(
            protocol=protocol,
            forms=forms,
            initial_scores=initial_scores,
            final_scores=final_scores,
            repair_rounds=repair_rounds,
        )
        primary_case_ids: set[str] | None = None
        if score_projection:
            projected_score = score_projection.get("score_override")
            projected_first_pass = score_projection.get(
                "first_pass_score_override"
            )
            projected_verdict = score_projection.get("quality_verdict_override")
            projected_ids = score_projection.get("primary_case_ids")
            if (
                isinstance(projected_score, (int, float))
                and not isinstance(projected_score, bool)
                and isinstance(projected_first_pass, (int, float))
                and not isinstance(projected_first_pass, bool)
                and projected_verdict in {"PASS", "FAIL"}
                and isinstance(projected_ids, list)
                and projected_ids
                and all(isinstance(value, str) and value for value in projected_ids)
            ):
                score = float(projected_score)
                first_pass_role_ability_score = float(projected_first_pass)
                verdict = str(projected_verdict)
                primary_case_ids = set(projected_ids)
            else:
                raise ValueError("CARD_DISTILLER_STAGE_SCORE_PROJECTION_INVALID")
        blocking_failures: list[dict[str, Any]] = []
        for form_id in ("A", "B"):
            for detail in final_scores[form_id].get("details", []):
                if not isinstance(detail, Mapping):
                    continue
                if (
                    primary_case_ids is not None
                    and str(detail.get("case_id")) not in primary_case_ids
                ):
                    continue
                failed_fields = sorted(
                    {
                        str(reason)
                        for key in ("critical_reasons", "major_reasons")
                        for reason in (detail.get(key) or [])
                        if isinstance(reason, str) and reason
                    }
                )
                if failed_fields:
                    blocking_failures.append(
                        {
                            "case_id": str(detail.get("case_id") or f"{form_id}-UNKNOWN"),
                            "failed_fields": failed_fields,
                            "evidence_anchor": (
                                f"{stage_name}/scores/form_{form_id}/"
                                f"case_details/{detail.get('case_id')}"
                            ),
                            "blocking": True,
                        }
                    )
        if (
            verdict == "FAIL"
            and not blocking_failures
            and primary_case_ids is None
        ):
            blocking_failures.append(
                {
                    "case_id": "CARD_DISTILLER_AGGREGATE",
                    "failed_fields": ["aggregate_score_or_repair_burden"],
                    "evidence_anchor": f"{stage_name}/stage_result/aggregate",
                    "blocking": True,
                }
            )
        residual_materiality = (
            _card_distiller_residual_materiality_projection(
                blocking_failures=blocking_failures,
                golds=golds,
            )
        )
        blocking_failures = residual_materiality["blocking_failures"]
        model_fail_projection = _primary_model_fail_projection(
            category_id=self.EXAM_CATEGORY_ID,
            score=score,
            raw_quality_verdict=verdict,
            blocking_failures=blocking_failures,
            guarded_operational_verdict=(
                "SUITABLE_WITH_CARD_REVIEWER_AND_GOVERNED_DISPOSITION"
            ),
        )
        repair_cycle_closed = (
            repair_rounds == self.SEMANTIC_REPAIR_ROUND_LIMIT
            or failed_exam_item_count == 0
        )
        stage_cost = round(state["estimated_cost_cny"] - stage_cost_before, 9)
        if stage_cost > budget_cap_cny:
            raise ValueError("WORKFLOW_MODEL_EXAM_STAGE_COST_CAP_EXCEEDED")
        stage_result = {
            "schema_version": "WorkflowModelExamStageResult-v1",
            "stage": stage_name,
            "chain_status": "PASS",
            "quality_verdict": verdict,
            "score": round(score, 2),
            "first_pass_role_ability_score": round(
                first_pass_role_ability_score, 2
            ),
            "post_repair_score": round(score, 2),
            "failed_exam_item_count": failed_exam_item_count,
            "repair_rounds": repair_rounds,
            "semantic_repair_round_limit": self.SEMANTIC_REPAIR_ROUND_LIMIT,
            "semantic_repair_lifecycle": repair_lifecycle,
            "cumulative_category_repair_penalty": cumulative_penalty,
            "blocking_failures": blocking_failures,
            **model_fail_projection,
            "residual_materiality_policy_revision": residual_materiality[
                "schema_version"
            ],
            "calibration_override_case_ids": residual_materiality[
                "calibration_override_case_ids"
            ],
            "quality_raw_score_and_verdict_preserved": True,
            "repair_cycle_closed": repair_cycle_closed,
            "input_exact_set_sha256": _sha256(
                {"forms": forms, "schema": full_schema}
            ),
            "transport_retry_is_semantic_repair": False,
            "provider_calls": state["stage_calls"],
            "estimated_cost_cny": stage_cost,
            "budget_cap_cny": budget_cap_cny,
            "provider_received_answer_key": False,
            "reference_regression_only": stage_name != "low_cost",
            "candidate_output_metrics_by_form": (
                candidate_output_metrics_by_form
            ),
            "candidate_output_capacity_limit_surface": (
                "EACH_LOGICAL_ARTIFACT_PER_FORM_NOT_CUMULATIVE"
            ),
            "combined_candidate_output_metric_quality_semantics": (
                "DIAGNOSTIC_ONLY_NOT_MODEL_FAIL"
            ),
            **{
                key: deepcopy(value)
                for key, value in score_projection.items()
                if key
                not in {
                    "score_override",
                    "first_pass_score_override",
                    "quality_verdict_override",
                    "primary_case_ids",
                }
            },
        }
        _write_json_create_only(stage_root / "stage_result.json", stage_result)
        return stage_result

    def execute(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, Any],
        *,
        authorization: Mapping[str, Any] | None,
        structured_chat: Callable[..., Mapping[str, Any]],
    ) -> dict[str, Any]:
        plan = self.plan(node, target, context)
        requested_model = (
            str(target.get("model_name"))
            if isinstance(target.get("model_name"), str)
            else None
        )
        if plan.get("status") != "READY":
            return _not_run(str(plan.get("reason") or "WORKFLOW_MODEL_EXAM_NOT_AVAILABLE"), requested_model=requested_model)
        authorization_reason = self._authorization_reason(authorization, plan)
        if authorization_reason is not None:
            return _not_run(authorization_reason, requested_model=requested_model)

        run_id = "workflow-exam-" + uuid.uuid4().hex
        run_root = self.scratch_root / "workflow_exams" / run_id
        run_root.mkdir(parents=True, exist_ok=False)
        _write_json_create_only(run_root / "plan.json", plan)
        _write_json_create_only(
            run_root / "authorization_receipt.json",
            {
                **deepcopy(dict(authorization or {})),
                "run_id": run_id,
                "accepted": True,
                "accepted_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        state = {
            "stage_calls": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
            "external_network_calls": 0,
            "external_process_launches": 0,
            "estimated_cost_cny": 0.0,
        }
        try:
            modules = self._quality()
            low_form_a, low_gold_a = modules["assets"].generate_form("G")
            low_form_b, low_gold_b = modules["assets"].generate_form("H")
            pack = modules["reference"].load_card_distiller_reference_exam_pack(
                self._reference_pack_root
            )
            high_form_a = _load_json(pack.member("forms/form_A.json"))
            high_form_b = _load_json(pack.member("forms/form_B.json"))
            high_gold_a = _load_json(pack.member("gold/form_A_gold.json"))
            high_gold_b = _load_json(pack.member("gold/form_B_gold.json"))
            low = self._run_stage(
                modules=modules,
                run_id=run_id,
                run_root=run_root,
                stage_name="low_cost",
                target=context["low_cost_target"],
                forms={"A": low_form_a, "B": low_form_b},
                golds={"A": low_gold_a, "B": low_gold_b},
                budget_cap_cny=float(plan["low_cost"]["budget_cap_cny"]),
                state=state,
                structured_chat=structured_chat,
            )
            if low["chain_status"] != "PASS":
                raise ValueError("LOW_COST_REHEARSAL_NOT_PASSED")
            high = self._run_stage(
                modules=modules,
                run_id=run_id,
                run_root=run_root,
                stage_name="high_cost",
                target=target,
                forms={"A": high_form_a, "B": high_form_b},
                golds={"A": high_gold_a, "B": high_gold_b},
                budget_cap_cny=float(plan["high_cost"]["budget_cap_cny"]),
                state=state,
                structured_chat=structured_chat,
            )
            if state["provider_calls"] > int(plan["maximum_provider_calls"]):
                raise ValueError("WORKFLOW_MODEL_EXAM_TOTAL_CALL_LIMIT_EXCEEDED")
            if state["estimated_cost_cny"] > float(plan["budget_cap_cny"]):
                raise ValueError("WORKFLOW_MODEL_EXAM_TOTAL_COST_CAP_EXCEEDED")
            public_result = {
                "schema_version": "WorkflowModelExamRunResult-v1",
                "status": high["quality_verdict"],
                "reason": "Quality_REFERENCE_REGRESSION_COMPLETED",
                "run_id": run_id,
                "plan_sha256": plan["plan_sha256"],
                "executor_ref": EXECUTOR_REF,
                "executor_sha256": _file_sha256(Path(__file__)),
                "requested_model": requested_model,
                "returned_model": state.get("returned_model") if target.get("adapter_id")=="command_template" else requested_model,
                "score": int(round(float(high["score"]))),
                "failed_exam_item_count": high["failed_exam_item_count"],
                "low_cost_chain_status": low["chain_status"],
                "low_cost_quality_verdict": low["quality_verdict"],
                "provider_calls": state["provider_calls"],
                "external_model_calls": state["external_model_calls"],
                "external_network_calls": state["external_network_calls"],
                "estimated_cost_cny": round(state["estimated_cost_cny"], 9),
                "actual_cost": None,
                "reference_regression_only": True,
                "blind_holdout_eligible": False,
                "qualification_eligible": False,
                "provider_received_answer_key": False,
            }
            evidence = _write_json_create_only(
                run_root / "exam_result.json", public_result
            )
            return {
                "schema_version": RESULT_SCHEMA,
                "kind": "WORKFLOW_NODE",
                "status": high["quality_verdict"],
                "score": int(round(float(high["score"]))),
                "reason": "Quality_REFERENCE_REGRESSION_COMPLETED",
                "requested_model": requested_model,
                "returned_model": state.get("returned_model") if target.get("adapter_id")=="command_template" else requested_model,
                "duration_ms": 0,
                "external_network_calls": state["external_network_calls"],
                "provider_calls": state["provider_calls"],
                "external_model_calls": state["external_model_calls"],
                "external_process_launches": state[
                    "external_process_launches"
                ],
                "exam_mode": plan["mode"],
                "exam_run_id": run_id,
                "exam_run_root": str(run_root.resolve()),
                "exam_evidence_sha256": evidence["sha256"],
                "failed_exam_item_count": high["failed_exam_item_count"],
                "estimated_cost_cny": round(state["estimated_cost_cny"], 9),
                "actual_cost": None,
                "reference_regression_only": True,
                "blind_holdout_eligible": False,
                "qualification_eligible": False,
            }
        except Exception as error:
            reason = str(error) or type(error).__name__
            if len(reason) > 200:
                reason = type(error).__name__
            failure = {
                "schema_version": "WorkflowModelExamRunFailure-v1",
                "status": "NOT_ASSESSED",
                "reason": reason,
                "run_id": run_id,
                "plan_sha256": plan["plan_sha256"],
                "provider_calls": state["provider_calls"],
                "external_model_calls": state["external_model_calls"],
                "external_network_calls": state["external_network_calls"],
                "external_process_launches": state[
                    "external_process_launches"
                ],
                "estimated_cost_cny": round(state["estimated_cost_cny"], 9),
                "score": None,
            }
            evidence = _write_json_create_only(run_root / "exam_failure.json", failure)
            result = _not_run(reason, requested_model=requested_model)
            result.update(
                provider_calls=state["provider_calls"],
                external_model_calls=state["external_model_calls"],
                external_network_calls=state["external_network_calls"],
                external_process_launches=state[
                    "external_process_launches"
                ],
                exam_run_id=run_id,
                exam_run_root=str(run_root.resolve()),
                exam_evidence_sha256=evidence["sha256"],
                estimated_cost_cny=round(state["estimated_cost_cny"], 9),
            )
            return result


class QualityCardDistillerDirectExamExecutor(QualityCardDistillerExamExecutor):
    """Run the frozen Card Distiller reference pack over API, CLI, or local."""

    EXECUTOR_REF = (
        "Desktop_MODEL_EXAM_SUCCESSOR_Quality_CARD_DISTILLER_EXECUTOR_V6_"
        "TWO_ROUND_GUIDED_REPAIR_CALIBRATED_FAIL"
    )
    MODE = "LIVE_Quality_CARD_DISTILLER_SUCCESSOR_REFERENCE_REGRESSION"
    SEMANTIC_REPAIR_ROUND_LIMIT = 2
    API_REQUEST_CAP = 25
    CLI_REQUEST_CAP = 25
    LOCAL_REQUEST_CAP = 64
    API_BUDGET_CAP_CNY = 6.0
    SAMPLE_ROTATION_SLOTS = 8
    REQUEST_TIMEOUT_SECONDS_BY_PROFILE = {
        "API": 600,
        "CLI": 1_200,
        "LOCAL": 1_200,
    }

    @classmethod
    def _request_timeout_seconds(cls, profile_kind: str) -> int:
        value = cls.REQUEST_TIMEOUT_SECONDS_BY_PROFILE.get(profile_kind)
        if value is None:
            raise ValueError("WORKFLOW_MODEL_EXAM_TIMEOUT_PROFILE_INVALID")
        return value

    @staticmethod
    def _profile_kind(target: Mapping[str, Any]) -> str | None:
        from memorive.models.cli.templates import is_custom,exam_eligible
        if is_custom(target):
            return "CLI" if exam_eligible(target) else None
        kind = target.get("kind")
        if kind == "API":
            if QualityCardDistillerExamExecutor._eligible_target(
                target, low_cost=True
            ):
                return "API"
            return None
        if kind == "CLI":
            if (
                _compact(target.get("adapter_id")) == "codexcli"
                and target.get("model_name") == "gpt-5.6-luna"
                and target.get("thinking_mode") in {"high", "xhigh"}
                and target.get("connection_status") == "AVAILABLE"
                and isinstance(target.get("config_id"), str)
                and bool(target.get("config_id"))
            ):
                return "CLI"
            return None
        if kind == "LOCAL":
            if (
                target.get("endpoint_kind") == "ollama"
                and target.get("structured_chat_adapter")
                == "EvaluationAssets_LOCAL_STRUCTURED_CHAT_V1"
                and target.get("execution_eligible") is True
                and target.get("exact_identity_available") is True
                and target.get("connection_status") == "AVAILABLE"
                and isinstance(target.get("model_name"), str)
                and bool(target.get("model_name"))
                and isinstance(target.get("model_digest"), str)
                and bool(re.fullmatch(r"[A-F0-9]{64}", target["model_digest"]))
                and isinstance(target.get("config_id"), str)
                and bool(target.get("config_id"))
            ):
                return "LOCAL"
        return None

    @classmethod
    def _sample_slot(cls, context: Mapping[str, Any]) -> int:
        explicit = context.get("workflow_exam_sample_slot")
        if explicit is not None:
            if (
                isinstance(explicit, bool)
                or not isinstance(explicit, int)
                or explicit < 1
                or explicit > cls.SAMPLE_ROTATION_SLOTS
            ):
                raise ValueError("CARD_DISTILLER_SUCCESSOR_SAMPLE_SLOT_INVALID")
            return explicit
        ordinal = context.get("workflow_exam_attempt_ordinal")
        if ordinal is None:
            revision = context.get("settings_revision", 0)
            if isinstance(revision, bool) or not isinstance(revision, int):
                raise ValueError("CARD_DISTILLER_SUCCESSOR_SAMPLE_SLOT_INVALID")
            ordinal = max(1, revision + 1)
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1:
            raise ValueError("CARD_DISTILLER_SUCCESSOR_ATTEMPT_ORDINAL_INVALID")
        return ((ordinal - 1) % cls.SAMPLE_ROTATION_SLOTS) + 1

    @classmethod
    def _exam_inputs(
        cls,
        *,
        pack: Any,
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        source_forms = {
            form_id: _load_json(pack.member(f"forms/form_{form_id}.json"))
            for form_id in ("A", "B")
        }
        source_golds = {
            form_id: _load_json(pack.member(f"gold/form_{form_id}_gold.json"))
            for form_id in ("A", "B")
        }
        sample_slot = cls._sample_slot(context)
        forms: dict[str, dict[str, Any]] = {}
        golds: dict[str, dict[str, Any]] = {}
        selected: dict[str, dict[str, Any]] = {}
        for form_id in ("A", "B"):
            source_form = source_forms[form_id]
            source_gold = source_golds[form_id]
            packs = source_form.get("packs")
            gold_packs = source_gold.get("packs")
            if (
                not isinstance(packs, list)
                or len(packs) != cls.SAMPLE_ROTATION_SLOTS
                or not isinstance(gold_packs, list)
                or len(gold_packs) != cls.SAMPLE_ROTATION_SLOTS
            ):
                raise ValueError("CARD_DISTILLER_SUCCESSOR_SOURCE_PACK_INVALID")
            omitted_pack_id = str(packs[sample_slot - 1].get("pack_id"))
            selected_packs = [
                deepcopy(row)
                for row in packs
                if isinstance(row, Mapping)
                and row.get("pack_id") != omitted_pack_id
            ]
            selected_pack_ids = [str(row["pack_id"]) for row in selected_packs]
            selected_pack_set = set(selected_pack_ids)
            selected_cases = [
                deepcopy(row)
                for row in source_form.get("cases", [])
                if isinstance(row, Mapping)
                and row.get("pack_id") in selected_pack_set
            ]
            selected_case_ids = [str(row["case_id"]) for row in selected_cases]
            selected_case_set = set(selected_case_ids)
            selected_gold_packs = [
                deepcopy(row)
                for row in gold_packs
                if isinstance(row, Mapping)
                and row.get("pack_id") in selected_pack_set
            ]
            selected_gold_cases = [
                deepcopy(row)
                for row in source_gold.get("cases", [])
                if isinstance(row, Mapping)
                and row.get("case_id") in selected_case_set
            ]
            if (
                len(selected_packs) != 7
                or len(selected_cases) != 63
                or len(selected_gold_packs) != 7
                or len(selected_gold_cases) != 63
                or [row["case_id"] for row in selected_gold_cases]
                != selected_case_ids
            ):
                raise ValueError("CARD_DISTILLER_SUCCESSOR_SAMPLE_INVALID")
            form = deepcopy(dict(source_form))
            form["packs"] = selected_packs
            form["cases"] = selected_cases
            form["successor_projection"] = {
                "revision": CARD_DISTILLER_SUCCESSOR_SAMPLE_REVISION,
                "source_content_sha256": source_form.get("content_sha256"),
                "omitted_pack_id": omitted_pack_id,
            }
            gold = deepcopy(dict(source_gold))
            gold["packs"] = selected_gold_packs
            gold["cases"] = selected_gold_cases
            gold["successor_projection"] = {
                "revision": CARD_DISTILLER_SUCCESSOR_SAMPLE_REVISION,
                "source_content_sha256": source_gold.get("content_sha256"),
                "omitted_pack_id": omitted_pack_id,
            }
            forms[form_id] = form
            golds[form_id] = gold
            selected[form_id] = {
                "omitted_pack_id": omitted_pack_id,
                "selected_pack_ids": selected_pack_ids,
                "selected_case_ids": selected_case_ids,
            }
        manifest = {
            "schema_version": CARD_DISTILLER_SUCCESSOR_SAMPLE_REVISION,
            "sample_slot": sample_slot,
            "rotation_slots": cls.SAMPLE_ROTATION_SLOTS,
            "selected_case_count_per_form": 63,
            "selected_pack_count_per_form": 7,
            "selection": selected,
            "coverage_semantics": "EACH_SOURCE_PACK_OMITTED_ONCE_ACROSS_EIGHT_SLOTS",
            "gold_in_subject_request": False,
        }
        manifest["manifest_sha256"] = _sha256(manifest)
        return {"forms": forms, "golds": golds, "sample_metadata": manifest}

    @classmethod
    def _request_cap(cls, profile_kind: str) -> int:
        return {
            "API": cls.API_REQUEST_CAP,
            "CLI": cls.CLI_REQUEST_CAP,
            "LOCAL": cls.LOCAL_REQUEST_CAP,
        }[profile_kind]

    def _structured_output_policy(
        self,
        target: Mapping[str, Any],
    ) -> tuple[dict[str, Any], int | None, str]:
        return workflow_exam_structured_output_policy(target)

    def plan(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        node_id = node.get("node_id")
        if node_id not in {"ingest", "card_distill"}:
            return _not_available("WORKFLOW_MODEL_EXAM_DYNAMIC_NODE_UNSUPPORTED")
        profile_kind = self._profile_kind(target)
        if profile_kind is None:
            return _not_available("WORKFLOW_MODEL_EXAM_DYNAMIC_PROFILE_UNSUPPORTED")
        try:
            modules = self._quality()
            verification = modules["pack"].verify_reference_pack(
                self._reference_pack_root
            )
            pack = modules["reference"].load_card_distiller_reference_exam_pack(
                self._reference_pack_root
            )
            inputs = self._exam_inputs(pack=pack, context=context)
            workload = core_primary_workload_profile()
            protocol = modules["protocol"]
            (
                model_binding,
                request_max_output_tokens,
                output_limit_mode,
            ) = self._structured_output_policy(target)
            perfect_metrics: dict[str, dict[str, int]] = {}
            perfect_artifact_metrics: dict[
                str, dict[str, dict[str, int]]
            ] = {}
            source_metrics: dict[str, int] = {}
            for form_id in ("A", "B"):
                source_metrics[form_id] = len(
                    _serialized_request(inputs["forms"][form_id])
                )
                perfect = protocol.perfect_response(inputs["golds"][form_id])
                measured = _card_distiller_candidate_output_metrics(perfect)
                perfect_metrics[form_id] = deepcopy(
                    measured["combined_diagnostic"]
                )
                perfect_artifact_metrics[form_id] = deepcopy(
                    measured["artifacts"]
                )
            if any(
                value > Core_PRIMARY_SOURCE_INPUT_CHAR_LIMIT
                for value in source_metrics.values()
            ) or any(
                row["chars"] > Core_PRIMARY_CARD_OUTPUT_CHAR_LIMIT
                or row["utf8_bytes"] > Core_PRIMARY_CARD_OUTPUT_BYTE_LIMIT
                for row in perfect_metrics.values()
            ):
                return _not_available(
                    "WORKFLOW_MODEL_EXAM_DESIGN_INPUT_OUT_OF_BOUNDS"
                )
            if profile_kind == "API":
                self._pricing_profile()
        except (ImportError, OSError, ValueError, RuntimeError):
            return _not_available("WORKFLOW_MODEL_EXAM_Quality_ASSETS_INVALID")
        if (
            verification.get("status") != "PASS"
            or verification.get("reference_regression_only") is not True
            or verification.get("qualification_eligible") is not False
        ):
            return _not_available("WORKFLOW_MODEL_EXAM_Quality_PACK_NOT_ELIGIBLE")
        budget_cap = self.API_BUDGET_CAP_CNY if profile_kind == "API" else None
        plan: dict[str, Any] = {
            "schema_version": PLAN_SCHEMA,
            "status": "READY",
            "mode": self.MODE,
            "reason": "Quality_REFERENCE_REGRESSION_READY",
            "node_id": node_id,
            "executor_ref": self.EXECUTOR_REF,
            "profile_kind": profile_kind,
            "profile_ref": target["config_id"],
            "model_name": target["model_name"],
            "model_digest": target.get("model_digest"),
            "thinking_mode": target.get("thinking_mode"),
            "settings_revision": context.get("settings_revision"),
            "settings_sha256": context.get("settings_sha256"),
            "reference_pack_id": verification["pack_id"],
            "reference_pack_revision": verification["pack_revision"],
            "reference_pack_sha256": verification["pack_fingerprint"],
            "reference_regression_only": True,
            "blind_holdout_eligible": False,
            "qualification_eligible": False,
            "forms": ["A", "B"],
            "base_model_calls": 16 if profile_kind == "LOCAL" else 8,
            "fact_shard_size": 12 if profile_kind == "LOCAL" else 36,
            "projection_shard_size": 4,
            "maximum_model_calls": self._request_cap(profile_kind),
            "request_timeout_seconds": self._request_timeout_seconds(
                profile_kind
            ),
            "request_timeout_policy": (
                "Core_X2_NODE_AND_PROFILE_SPECIFIC_NOT_GLOBAL"
            ),
            "output_limit_policy": (
                "CALIBRATED_CEILING_WITH_PROVIDER_DEFAULT_API_CLI_"
                "AND_MODEL_PROFILE_LOCAL"
            ),
            "calibrated_reply_token_ceiling": (
                WORKFLOW_EXAM_REPLY_TOKEN_CEILING
            ),
            "output_limit_contract": workflow_exam_output_limit_contract(),
            "structured_output_profile": {
                "mode": output_limit_mode,
                "request_max_output_tokens": request_max_output_tokens,
                "context_window_tokens": (
                    model_binding.get("context_window_tokens")
                    if profile_kind == "LOCAL"
                    else None
                ),
                "profile_specific_not_global": True,
            },
            "sample_revision": inputs["sample_metadata"]["schema_version"],
            "sample_slot": inputs["sample_metadata"]["sample_slot"],
            "sample_manifest_sha256": inputs["sample_metadata"][
                "manifest_sha256"
            ],
            "sample_case_count_per_form": 63,
            "sample_pack_count_per_form": 7,
            "sample_rotation_slots": self.SAMPLE_ROTATION_SLOTS,
            "workload_profile": workload,
            "source_input_chars_by_form": source_metrics,
            "perfect_output_metrics_by_form": perfect_metrics,
            "perfect_artifact_output_metrics_by_form": (
                perfect_artifact_metrics
            ),
            "exam_design_length_limit_surface": (
                "COMBINED_FROZEN_PERFECT_RESPONSE_PER_FORM"
            ),
            "candidate_output_capacity_limit_surface": (
                "NONE_Core_X2_ARTIFACT_SIZE_IS_DIAGNOSTIC_ONLY"
            ),
            "combined_candidate_output_metric_quality_semantics": (
                "DIAGNOSTIC_ONLY_NOT_MODEL_FAIL"
            ),
            "candidate_output_reference_quality_semantics": (
                "DIAGNOSTIC_ONLY_NOT_INVALIDATION_MODEL_FAIL_OR_REPAIR"
            ),
            "capacity_overflow_quality_semantics": "NOT_ASSESSED",
            "cross_category_comparison_forbidden": True,
            "global_ranking_forbidden": True,
            "exam_category_id": self.EXAM_CATEGORY_ID,
            "horizontal_comparison_eligible": True,
            "horizontal_comparison_allowed_only_with_same_cohort": True,
            "semantic_repair_round_limit": self.SEMANTIC_REPAIR_ROUND_LIMIT,
            "second_semantic_repair_allowed": True,
            "second_round_scope": (
                "ROUND1_RESIDUAL_EXACT_SET_ONLY_NO_FULL_FORM_RETRY"
            ),
            "second_round_penalty_semantics": (
                "CUMULATIVE_Quality_CATEGORY_NONLINEAR_PLUS_"
                "CATEGORY_VOLUME_BURDEN"
            ),
            "directed_repair_transport_policy": {
                "multi_form_response_required_from_provider": False,
                "api_cli_per_form_case_limit": 36,
                "local_per_form_case_limit": 12,
                "merge_authority": "LOCAL_Quality_REPAIR_MERGER",
                "semantic_round_increment_per_transport_shard": False,
                "issue_guidance_revision": (
                    CARD_DISTILLER_REPAIR_ISSUE_GUIDANCE_REVISION
                ),
                "gold_consulted_for_issue_guidance": False,
            },
            "residual_materiality_policy_revision": (
                CARD_DISTILLER_RESIDUAL_MATERIALITY_REVISION
            ),
            "budget_cap_cny": budget_cap,
            "cost_semantics": (
                "FROZEN_PROFILE_ESTIMATE_CAP"
                if profile_kind == "API"
                else (
                    ("CLI_MANAGED_UNKNOWN" if target.get("adapter_id")=="command_template" else "SUBSCRIPTION_CLI_NO_PER_CALL_PRICE")
                    if profile_kind == "CLI"
                    else "LOCAL_COMPUTE_NO_PROVIDER_API_COST"
                )
            ),
            "authorization_schema_version": AUTHORIZATION_SCHEMA,
            "external_network_calls": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
        }
        comparison_basis = {
            "category_id": self.EXAM_CATEGORY_ID,
            "reference_pack_sha256": plan["reference_pack_sha256"],
            "sample_revision": plan["sample_revision"],
            "sample_slot": plan["sample_slot"],
            "sample_manifest_sha256": plan["sample_manifest_sha256"],
            "executor_ref": self.EXECUTOR_REF,
        }
        comparison_sha256 = _sha256(comparison_basis)
        plan["comparison_binding_sha256"] = comparison_sha256
        plan["comparison_cohort_id"] = (
            f"CARD_DISTILLER_SUCCESSOR:{comparison_sha256}"
        )
        plan["plan_sha256"] = _sha256(plan)
        return plan

    @staticmethod
    def _authorization_reason(
        authorization: Mapping[str, Any] | None,
        plan: Mapping[str, Any],
    ) -> str | None:
        if not isinstance(authorization, Mapping):
            return "WORKFLOW_MODEL_EXAM_AUTHORIZATION_REQUIRED"
        if set(authorization) != {
            "schema_version",
            "authorized",
            "plan_sha256",
            "cost_cap_cny",
            "acknowledged_subscription_no_per_call_price",
        }:
            return "WORKFLOW_MODEL_EXAM_AUTHORIZATION_INVALID"
        if (
            authorization.get("schema_version") != AUTHORIZATION_SCHEMA
            or authorization.get("authorized") is not True
        ):
            return "WORKFLOW_MODEL_EXAM_AUTHORIZATION_REQUIRED"
        if authorization.get("plan_sha256") != plan.get("plan_sha256"):
            return "WORKFLOW_MODEL_EXAM_AUTHORIZATION_MISMATCH"
        if authorization.get("cost_cap_cny") != plan.get("budget_cap_cny"):
            return "WORKFLOW_MODEL_EXAM_COST_CAP_MISMATCH"
        if (
            authorization.get("acknowledged_subscription_no_per_call_price")
            is not (plan.get("profile_kind") == "CLI")
        ):
            return "WORKFLOW_MODEL_EXAM_COST_SEMANTICS_ACKNOWLEDGEMENT_REQUIRED"
        return None

    def _provider_call(self, **kwargs: Any) -> dict[str, Any]:
        state = kwargs["state"]
        target = kwargs["target"]
        if state["stage_calls"] >= self._request_cap(str(target["kind"])):
            raise ValueError("WORKFLOW_MODEL_EXAM_STAGE_CALL_LIMIT_REACHED")
        return super()._provider_call(**kwargs)

    def execute(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, Any],
        *,
        authorization: Mapping[str, Any] | None,
        structured_chat: Callable[..., Mapping[str, Any]],
    ) -> dict[str, Any]:
        started = monotonic()
        plan = self.plan(node, target, context)
        requested_model = (
            str(target.get("model_name"))
            if isinstance(target.get("model_name"), str)
            else None
        )
        if plan.get("status") != "READY":
            return _not_run(
                str(plan.get("reason") or "WORKFLOW_MODEL_EXAM_NOT_AVAILABLE"),
                requested_model=requested_model,
            )
        authorization_reason = self._authorization_reason(authorization, plan)
        if authorization_reason is not None:
            return _not_run(authorization_reason, requested_model=requested_model)
        run_id = "workflow-exam-" + uuid.uuid4().hex
        run_root = self.scratch_root / "workflow_exams" / run_id
        run_root.mkdir(parents=True, exist_ok=False)
        _write_json_create_only(run_root / "plan.json", plan)
        _write_json_create_only(
            run_root / "authorization_receipt.json",
            {
                **deepcopy(dict(authorization or {})),
                "run_id": run_id,
                "accepted": True,
                "accepted_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        state = {
            "stage_calls": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
            "external_network_calls": 0,
            "external_process_launches": 0,
            "estimated_cost_cny": 0.0,
        }
        try:
            modules = self._quality()
            pack = modules["reference"].load_card_distiller_reference_exam_pack(
                self._reference_pack_root
            )
            inputs = self._exam_inputs(pack=pack, context=context)
            forms = inputs["forms"]
            golds = inputs["golds"]
            if inputs["sample_metadata"]["manifest_sha256"] != plan.get(
                "sample_manifest_sha256"
            ):
                raise ValueError(
                    "WORKFLOW_MODEL_EXAM_CARD_SAMPLE_MANIFEST_MISMATCH"
                )
            _write_json_create_only(
                run_root / "sample_manifest.json",
                inputs["sample_metadata"],
            )
            for form in forms.values():
                if (
                    len(_serialized_request(form))
                    > Core_PRIMARY_SOURCE_INPUT_CHAR_LIMIT
                ):
                    raise ValueError(
                        "WORKFLOW_MODEL_EXAM_CARD_INPUT_200_PERCENT_LIMIT_EXCEEDED"
                    )
            stage = self._run_stage(
                modules=modules,
                run_id=run_id,
                run_root=run_root,
                stage_name="direct_reference",
                target=target,
                forms=forms,
                golds=golds,
                budget_cap_cny=float(plan["budget_cap_cny"] or 0.0),
                state=state,
                structured_chat=structured_chat,
                fact_shard_size=int(plan["fact_shard_size"]),
                projection_shard_size=int(plan["projection_shard_size"]),
                stage_call_cap=int(plan["maximum_model_calls"]),
            )
            if state["stage_calls"] > int(plan["maximum_model_calls"]):
                raise ValueError("WORKFLOW_MODEL_EXAM_TOTAL_CALL_LIMIT_EXCEEDED")
            score = int(round(float(stage["score"])))
            raw_quality_verdict = str(stage["quality_verdict"])
            raw_fail_admission = None
            if raw_quality_verdict == "FAIL":
                raw_fail_admission = admit_quality_fail(
                    execution_outcome=ExecutionOutcome.COMPLETED,
                    scorability=Scorability.SCOREABLE,
                    repair_rounds_used=int(stage["repair_rounds"]),
                    repair_round_limit=int(
                        stage["semantic_repair_round_limit"]
                    ),
                    repair_cycle_closed=bool(stage["repair_cycle_closed"]),
                    blocking_failures=stage["blocking_failures"],
                    exact_hashes={
                        "reference_pack_sha256": str(
                            plan["reference_pack_sha256"]
                        ),
                        "scoring_protocol_sha256": _file_sha256(
                            pack.member("scoring/scoring_protocol.json")
                        ),
                        "executor_sha256": _file_sha256(Path(__file__)),
                        "input_exact_set_sha256": str(
                            stage["input_exact_set_sha256"]
                        ),
                    },
                )
            model_fail_established = bool(stage["model_fail_established"])
            fail_admission = (
                raw_fail_admission if model_fail_established else None
            )
            verdict = "FAIL" if model_fail_established else "PASS"
            public_result = {
                "schema_version": "WorkflowModelExamRunResult-v1",
                "status": verdict,
                "reason": "Quality_REFERENCE_REGRESSION_COMPLETED",
                "run_id": run_id,
                "plan_sha256": plan["plan_sha256"],
                "executor_ref": self.EXECUTOR_REF,
                "executor_sha256": _file_sha256(Path(__file__)),
                "requested_model": requested_model,
                "returned_model": state.get("returned_model") if target.get("adapter_id")=="command_template" else requested_model,
                "score": score,
                "execution_outcome": ExecutionOutcome.COMPLETED.value,
                "scorability": Scorability.SCOREABLE.value,
                "raw_quality_candidate_verdict": raw_quality_verdict,
                "raw_quality_fail_admission": raw_fail_admission,
                "quality_fail_admission": fail_admission,
                "first_pass_role_ability_score": stage[
                    "first_pass_role_ability_score"
                ],
                "post_repair_score": stage["post_repair_score"],
                "model_fail_policy_revision": stage[
                    "model_fail_policy_revision"
                ],
                "model_fail_verdict": stage["model_fail_verdict"],
                "model_fail_established": model_fail_established,
                "model_fail_minimum_distinct_material_cases": stage[
                    "model_fail_minimum_distinct_material_cases"
                ],
                "material_residual_case_ids": stage[
                    "material_residual_case_ids"
                ],
                "calibration_residual_case_ids": stage[
                    "calibration_residual_case_ids"
                ],
                "operational_eligibility_verdict": stage[
                    "operational_eligibility_verdict"
                ],
                "exam_category_id": stage["category_id"],
                "category_local_score": stage["category_local_score"],
                "numeric_score_is_not_model_fail_verdict": True,
                "cross_category_comparison_forbidden": True,
                "global_ranking_forbidden": True,
                "comparison_binding_sha256": plan[
                    "comparison_binding_sha256"
                ],
                "comparison_cohort_id": plan["comparison_cohort_id"],
                "horizontal_comparison_eligible": True,
                "horizontal_comparison_allowed_only_with_same_cohort": True,
                "failed_exam_item_count": stage["failed_exam_item_count"],
                "semantic_repair_rounds": stage["repair_rounds"],
                "semantic_repair_round_limit": stage[
                    "semantic_repair_round_limit"
                ],
                "semantic_repair_lifecycle": stage[
                    "semantic_repair_lifecycle"
                ],
                "cumulative_category_repair_penalty": stage[
                    "cumulative_category_repair_penalty"
                ],
                "transport_retry_is_semantic_repair": False,
                "request_attempts": state["stage_calls"],
                "provider_calls": state["provider_calls"],
                "external_model_calls": state["external_model_calls"],
                "external_network_calls": state["external_network_calls"],
                "external_process_launches": state["external_process_launches"],
                "estimated_cost_cny": (
                    round(state["estimated_cost_cny"], 9)
                    if plan["profile_kind"] == "API"
                    else (0.0 if plan["profile_kind"] == "LOCAL" else None)
                ),
                "actual_cost": None,
                "cost_semantics": plan["cost_semantics"],
                "reference_regression_only": True,
                "blind_holdout_eligible": False,
                "qualification_eligible": False,
                "provider_received_answer_key": False,
                "sample_revision": plan["sample_revision"],
                "sample_slot": plan["sample_slot"],
                "sample_manifest_sha256": plan["sample_manifest_sha256"],
                "workload_profile": plan["workload_profile"],
                "residual_materiality_policy_revision": stage[
                    "residual_materiality_policy_revision"
                ],
                "calibration_override_case_ids": stage[
                    "calibration_override_case_ids"
                ],
                "quality_raw_score_and_verdict_preserved": stage[
                    "quality_raw_score_and_verdict_preserved"
                ],
                "candidate_output_metrics_by_form": stage[
                    "candidate_output_metrics_by_form"
                ],
                "candidate_output_capacity_limit_surface": stage[
                    "candidate_output_capacity_limit_surface"
                ],
                "combined_candidate_output_metric_quality_semantics": stage[
                    "combined_candidate_output_metric_quality_semantics"
                ],
                **(
                    {
                        "primary_language_track": stage[
                            "primary_language_track"
                        ],
                        "language_tracks": deepcopy(stage["language_tracks"]),
                        "cross_language_aggregation_forbidden": stage[
                            "cross_language_aggregation_forbidden"
                        ],
                        "repair_burden_affects_capability_score": stage[
                            "repair_burden_affects_capability_score"
                        ],
                    }
                    if "language_tracks" in stage
                    else {}
                ),
            }
            evidence = _write_json_create_only(
                run_root / "exam_result.json", public_result
            )
            return {
                **public_result,
                "schema_version": RESULT_SCHEMA,
                "kind": "WORKFLOW_NODE",
                "duration_ms": max(0, int((monotonic() - started) * 1000)),
                "exam_mode": plan["mode"],
                "exam_run_id": run_id,
                "exam_run_root": str(run_root.resolve()),
                "exam_evidence_sha256": evidence["sha256"],
                "executor_ref": self.EXECUTOR_REF,
                "executor_sha256": public_result["executor_sha256"],
            }
        except Exception as error:
            reason = str(error) or type(error).__name__
            if len(reason) > 200:
                reason = type(error).__name__
            failure = {
                "schema_version": "WorkflowModelExamRunFailure-v1",
                "status": "NOT_ASSESSED",
                "reason": reason,
                "run_id": run_id,
                "plan_sha256": plan["plan_sha256"],
                "request_attempts": state["stage_calls"],
                "provider_calls": state["provider_calls"],
                "external_model_calls": state["external_model_calls"],
                "external_network_calls": state["external_network_calls"],
                "external_process_launches": state["external_process_launches"],
                "estimated_cost_cny": (
                    round(state["estimated_cost_cny"], 9)
                    if plan["profile_kind"] == "API"
                    else (0.0 if plan["profile_kind"] == "LOCAL" else None)
                ),
                "score": None,
                "execution_outcome": classify_execution_outcome(
                    {"status": "ERROR", "reason": reason}
                ).value,
                "scorability": Scorability.NOT_ASSESSED.value,
            }
            evidence = _write_json_create_only(
                run_root / "exam_failure.json", failure
            )
            result = _not_run(reason, requested_model=requested_model)
            result.update(
                duration_ms=max(0, int((monotonic() - started) * 1000)),
                request_attempts=state["stage_calls"],
                provider_calls=state["provider_calls"],
                external_model_calls=state["external_model_calls"],
                external_network_calls=state["external_network_calls"],
                external_process_launches=state["external_process_launches"],
                exam_run_id=run_id,
                exam_run_root=str(run_root.resolve()),
                exam_evidence_sha256=evidence["sha256"],
                estimated_cost_cny=failure["estimated_cost_cny"],
                cost_semantics=plan["cost_semantics"],
                execution_outcome=failure["execution_outcome"],
                scorability=failure["scorability"],
            )
            return result


class QualityNewCardDistillerLanguageBankExamExecutor(
    QualityCardDistillerDirectExamExecutor
):
    """Quality_new Card exam over a 100/36/36 language bank."""

    EXECUTOR_REF = (
        "Desktop_MODEL_EXAM_SUCCESSOR_Quality_CARD_DISTILLER_LANGUAGE_BANK_"
        "EXECUTOR_V7"
    )
    MODE = "LIVE_Quality_CARD_DISTILLER_LANGUAGE_BANK_SUCCESSOR"
    API_REQUEST_CAP = 25
    CLI_REQUEST_CAP = 25
    LOCAL_REQUEST_CAP = 25
    SAMPLE_ROTATION_SLOTS = 5
    SAMPLE_REVISION = "CARD_DISTILLER_EN36_ZH6_JA6_DUAL_FORM_V2"

    @staticmethod
    def _prune_pack(
        *,
        pack: Mapping[str, Any],
        gold_pack: Mapping[str, Any],
        selected_case_ids: set[str],
        case_chunk_by_id: Mapping[str, str],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        projected_pack = deepcopy(dict(pack))
        projected_gold = deepcopy(dict(gold_pack))
        all_case_ids = [str(value) for value in pack.get("case_ids", [])]
        kept_case_ids = [
            case_id for case_id in all_case_ids if case_id in selected_case_ids
        ]
        all_case_chunks = {
            case_chunk_by_id[case_id]
            for case_id in all_case_ids
            if case_id in case_chunk_by_id
        }
        selected_chunks = {
            case_chunk_by_id[case_id]
            for case_id in kept_case_ids
            if case_id in case_chunk_by_id
        }
        projected_pack["case_ids"] = kept_case_ids
        projected_pack["chunks"] = [
            deepcopy(row)
            for row in pack.get("chunks", [])
            if str(row.get("chunk_id")) not in all_case_chunks
            or str(row.get("chunk_id")) in selected_chunks
        ]
        projected_gold["case_ids"] = kept_case_ids
        projection = projected_gold["expected_projection"]
        projection["key_data_case_ids"] = [
            value
            for value in projection["key_data_case_ids"]
            if value in selected_case_ids
        ]
        for field in (
            "research_question",
            "research_object",
            "method",
            "key_results",
            "author_conclusion",
            "boundary_conditions",
        ):
            projection[field]["case_ids"] = [
                value
                for value in projection[field]["case_ids"]
                if value in selected_case_ids
            ]
            projection[field]["anchor_ids"] = [
                value
                for value in projection[field]["anchor_ids"]
                if value not in all_case_chunks or value in selected_chunks
            ]
        return projected_pack, projected_gold

    @classmethod
    def _best_case_subset(
        cls,
        *,
        rows: Sequence[Mapping[str, Any]],
        gold_by_id: Mapping[str, Mapping[str, Any]],
        count: int,
        language: str,
        form_id: str,
        slot: int,
    ) -> list[str]:
        case_ids = [str(row["case_id"]) for row in rows]
        candidates: list[tuple[tuple[Any, ...], list[str]]] = []
        for combination in itertools.combinations(case_ids, count):
            gold_rows = [gold_by_id[case_id] for case_id in combination]
            difficulties = {
                value: sum(row.get("difficulty") == value for row in gold_rows)
                for value in ("easy", "medium", "hard")
            }
            families = {str(row.get("family")) for row in gold_rows}
            if language in {"zh", "ja"}:
                if difficulties != {"easy": 1, "medium": 1, "hard": 1}:
                    continue
                if len(families) != count:
                    continue
            elif families != {
                "D1_NUMERIC_UNIT_COMPARATOR",
                "D2_TIME_SAMPLE_SCOPE",
                "D3_MULTI_OCCURRENCE",
                "D4_MISSING_STATUS",
                "D5_SOURCE_AS_PRINTED",
                "D6_CLEAN_DIRECT_EXTRACTION",
            }:
                continue
            imbalance = max(difficulties.values()) - min(difficulties.values())
            minimum = min(difficulties.values())
            digest = _sha256(
                {
                    "revision": cls.SAMPLE_REVISION,
                    "form_id": form_id,
                    "slot": slot,
                    "language": language,
                    "case_ids": combination,
                }
            )
            candidates.append(((imbalance, -minimum, digest), list(combination)))
        if not candidates:
            raise ValueError("CARD_DISTILLER_LANGUAGE_SAMPLE_PARTITION_INVALID")
        candidates.sort(key=lambda row: row[0])
        return candidates[0][1]

    @classmethod
    def _exam_inputs(
        cls,
        *,
        pack: Any,
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        sample_slot = cls._sample_slot(context)
        forms: dict[str, dict[str, Any]] = {}
        golds: dict[str, dict[str, Any]] = {}
        selection: dict[str, Any] = {}
        for form_id in ("A", "B"):
            source_form = _load_json(pack.member(f"forms/form_{form_id}.json"))
            source_gold = _load_json(
                pack.member(f"gold/form_{form_id}_gold.json")
            )
            gold_by_id = {
                str(row["case_id"]): row for row in source_gold["cases"]
            }
            form_case_by_id = {
                str(row["case_id"]): row for row in source_form["cases"]
            }
            case_chunk_by_id = {
                case_id: str(row["source_unit"]["chunk_id"])
                for case_id, row in form_case_by_id.items()
            }
            packs_by_language = {
                language: [
                    row
                    for row in source_form["packs"]
                    if row.get("source_language") == language
                ]
                for language in ("en", "zh", "ja")
            }
            gold_pack_by_id = {
                str(row["pack_id"]): row for row in source_gold["packs"]
            }
            english_packs = packs_by_language["en"]
            english_pair = [
                english_packs[(sample_slot - 1) % len(english_packs)],
                english_packs[sample_slot % len(english_packs)],
            ]
            english_rows = [
                form_case_by_id[str(case_id)]
                for pack_row in english_pair
                for case_id in pack_row["case_ids"]
            ]
            english_ids = cls._best_case_subset(
                rows=english_rows,
                gold_by_id=gold_by_id,
                count=18,
                language="en",
                form_id=form_id,
                slot=sample_slot,
            )
            zh_pack = packs_by_language["zh"][(sample_slot - 1) % 2]
            ja_pack = packs_by_language["ja"][
                (sample_slot - 1 + (0 if form_id == "A" else 1)) % 2
            ]
            zh_rows = [
                form_case_by_id[str(case_id)] for case_id in zh_pack["case_ids"]
            ]
            ja_rows = [
                form_case_by_id[str(case_id)] for case_id in ja_pack["case_ids"]
            ]
            zh_ids = cls._best_case_subset(
                rows=zh_rows,
                gold_by_id=gold_by_id,
                count=3,
                language="zh",
                form_id=form_id,
                slot=sample_slot,
            )
            ja_ids = cls._best_case_subset(
                rows=ja_rows,
                gold_by_id=gold_by_id,
                count=3,
                language="ja",
                form_id=form_id,
                slot=sample_slot,
            )
            selected_ids = set([*english_ids, *zh_ids, *ja_ids])
            selected_pack_ids = [
                str(row["pack_id"])
                for row in [*english_pair, zh_pack, ja_pack]
            ]
            projected_packs: list[dict[str, Any]] = []
            projected_gold_packs: list[dict[str, Any]] = []
            for source_pack in source_form["packs"]:
                pack_id = str(source_pack["pack_id"])
                if pack_id not in selected_pack_ids:
                    continue
                projected_pack, projected_gold = cls._prune_pack(
                    pack=source_pack,
                    gold_pack=gold_pack_by_id[pack_id],
                    selected_case_ids=selected_ids,
                    case_chunk_by_id=case_chunk_by_id,
                )
                projected_packs.append(projected_pack)
                projected_gold_packs.append(projected_gold)
            selected_cases = [
                deepcopy(row)
                for row in source_form["cases"]
                if str(row["case_id"]) in selected_ids
            ]
            selected_case_ids = [str(row["case_id"]) for row in selected_cases]
            selected_gold_cases = [
                deepcopy(row)
                for row in source_gold["cases"]
                if str(row["case_id"]) in selected_ids
            ]
            if (
                len(selected_cases) != 24
                or len(projected_packs) != 4
                or [str(row["case_id"]) for row in selected_gold_cases]
                != selected_case_ids
            ):
                raise ValueError("CARD_DISTILLER_LANGUAGE_SAMPLE_INVALID")
            form = deepcopy(source_form)
            form["packs"] = projected_packs
            form["cases"] = selected_cases
            form["successor_projection"] = {
                "revision": cls.SAMPLE_REVISION,
                "source_content_sha256": source_form.get("content_sha256"),
            }
            gold = deepcopy(source_gold)
            gold["packs"] = projected_gold_packs
            gold["cases"] = selected_gold_cases
            gold["successor_projection"] = {
                "revision": cls.SAMPLE_REVISION,
                "source_content_sha256": source_gold.get("content_sha256"),
            }
            forms[form_id] = form
            golds[form_id] = gold
            selection[form_id] = {
                "selected_pack_ids": selected_pack_ids,
                "selected_case_ids": selected_case_ids,
                "language_counts": {"en": 18, "zh": 3, "ja": 3},
            }
        manifest = {
            "schema_version": cls.SAMPLE_REVISION,
            "sample_slot": sample_slot,
            "rotation_slots": cls.SAMPLE_ROTATION_SLOTS,
            "selected_case_count_per_form": 24,
            "selected_pack_count_per_form": 4,
            "language_counts_per_form": {"en": 18, "zh": 3, "ja": 3},
            "primary_language": "en",
            "diagnostic_languages": ["zh", "ja"],
            "cross_language_aggregation_forbidden": True,
            "selection": selection,
            "gold_in_subject_request": False,
        }
        manifest["manifest_sha256"] = _sha256(manifest)
        return {"forms": forms, "golds": golds, "sample_metadata": manifest}

    @staticmethod
    def _language_score_from_details(
        *,
        protocol: Any,
        details: Sequence[Mapping[str, Any]],
        primary: bool,
    ) -> dict[str, Any]:
        if not details:
            raise ValueError("CARD_DISTILLER_LANGUAGE_SCORE_EMPTY")
        values = [float(row["score"]) for row in details]
        critical_count = sum(bool(row.get("critical_reasons")) for row in details)
        major_count = sum(bool(row.get("major_reasons")) for row in details)
        family_means: dict[str, float] = {}
        for family in protocol.FAMILIES:
            family_values = [
                float(row["score"])
                for row in details
                if row.get("family") == family
            ]
            if family_values:
                family_means[family] = statistics.fmean(family_values)
        if primary:
            if set(family_means) != set(protocol.FAMILIES):
                raise ValueError("CARD_DISTILLER_ENGLISH_FAMILY_COVERAGE_INVALID")
            hard = [
                float(row["score"])
                for row in details
                if row.get("difficulty") == "hard"
            ]
            if not hard:
                raise ValueError("CARD_DISTILLER_ENGLISH_HARD_SET_EMPTY")
            tail_count = max(1, math.ceil(len(values) * 0.10))
            layers = {
                "overall_mean": statistics.fmean(values),
                "hard_mean": statistics.fmean(hard),
                "weakest_family": min(family_means.values()),
                "bottom_ten_percent": statistics.fmean(
                    sorted(values)[:tail_count]
                ),
                "structure": 100.0,
            }
            contributions = {
                key: protocol.piecewise_contribution(key, layers[key])
                for key in (
                    "overall_mean",
                    "hard_mean",
                    "weakest_family",
                    "bottom_ten_percent",
                )
            }
            contributions["structure"] = 15.0
            score = sum(contributions.values())
            caps: list[str] = []
            if critical_count and score > 59.0:
                score = 59.0
                caps.append("CATASTROPHIC_SCIENTIFIC_FAILURE_CAP_59")
            if major_count and score >= 90.0:
                score = min(score, 89.99)
                caps.append("MAJOR_FINDING_CEILING_BELOW_90")
            return {
                "score": round(score, 2),
                "case_count": len(details),
                "critical_count": critical_count,
                "major_count": major_count,
                "family_means": {
                    key: round(value, 2) for key, value in family_means.items()
                },
                "layers": {key: round(value, 2) for key, value in layers.items()},
                "score_caps_applied": caps,
                "score_semantics": "RAW_CAPABILITY_WITHOUT_REPAIR_BURDEN",
            }
        return {
            "score": round(statistics.fmean(values), 2),
            "case_count": len(details),
            "critical_count": critical_count,
            "major_count": major_count,
            "family_means_present": {
                key: round(value, 2) for key, value in family_means.items()
            },
            "score_semantics": "DESCRIPTIVE_CASE_MEAN_ONLY",
        }

    def _stage_score_projection(
        self,
        *,
        protocol: Any,
        forms: Mapping[str, Mapping[str, Any]],
        initial_scores: Mapping[str, Mapping[str, Any]],
        final_scores: Mapping[str, Mapping[str, Any]],
        repair_rounds: int,
    ) -> dict[str, Any]:
        language_by_case = {
            str(row["case_id"]): str(row["source_language"])
            for form in forms.values()
            for row in form["cases"]
        }

        def project(
            scores: Mapping[str, Mapping[str, Any]], language: str, primary: bool
        ) -> dict[str, Any]:
            by_form = {
                form_id: self._language_score_from_details(
                    protocol=protocol,
                    details=[
                        row
                        for row in scores[form_id]["details"]
                        if language_by_case.get(str(row.get("case_id")))
                        == language
                    ],
                    primary=primary,
                )
                for form_id in ("A", "B")
            }
            return {
                "score": round(
                    statistics.fmean(row["score"] for row in by_form.values()),
                    2,
                ),
                "forms": by_form,
                "case_count": sum(row["case_count"] for row in by_form.values()),
                "critical_count": sum(
                    row["critical_count"] for row in by_form.values()
                ),
                "major_count": sum(row["major_count"] for row in by_form.values()),
            }

        initial_en = project(initial_scores, "en", True)
        final_en = project(final_scores, "en", True)
        final_zh = project(final_scores, "zh", False)
        final_ja = project(final_scores, "ja", False)
        primary_case_ids = sorted(
            case_id
            for case_id, language in language_by_case.items()
            if language == "en"
        )
        quality = (
            "PASS"
            if final_en["critical_count"] == 0
            and final_en["score"] >= PASSING_SCORE
            else "FAIL"
        )
        return {
            "score_override": final_en["score"],
            "first_pass_score_override": initial_en["score"],
            "quality_verdict_override": quality,
            "primary_case_ids": primary_case_ids,
            "primary_language_track": "en",
            "language_tracks": {
                "en": {
                    "role": "PRODUCTION_PRIMARY",
                    "first_pass": initial_en,
                    "post_repair": final_en,
                    "affects_primary_score": True,
                    "affects_model_fail": True,
                },
                "zh": {
                    "role": "DIAGNOSTIC_ONLY",
                    "post_repair": final_zh,
                    "affects_primary_score": False,
                    "affects_model_fail": False,
                },
                "ja": {
                    "role": "DIAGNOSTIC_ONLY",
                    "post_repair": final_ja,
                    "affects_primary_score": False,
                    "affects_model_fail": False,
                },
            },
            "cross_language_aggregation_forbidden": True,
            "repair_burden_affects_capability_score": False,
            "repair_burden_reported_separately": True,
            "repair_rounds_used": repair_rounds,
        }

    def plan(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        plan = super().plan(node, target, context)
        if plan.get("status") != "READY":
            return plan
        plan.pop("plan_sha256", None)
        plan.update(
            reason="Quality_LANGUAGE_BANK_REFERENCE_REGRESSION_READY",
            base_model_calls=6 if plan["profile_kind"] == "LOCAL" else 4,
            sample_case_count_per_form=24,
            sample_pack_count_per_form=4,
            sample_rotation_slots=self.SAMPLE_ROTATION_SLOTS,
            sample_language_counts_per_form={"en": 18, "zh": 3, "ja": 3},
            primary_language_track="en",
            diagnostic_language_tracks=["zh", "ja"],
            cross_language_aggregation_forbidden=True,
            repair_burden_affects_capability_score=False,
            second_round_penalty_semantics=(
                "REPORTED_SEPARATELY_NOT_DEDUCTED_FROM_CAPABILITY_SCORE"
            ),
        )
        plan["plan_sha256"] = _sha256(plan)
        return plan
