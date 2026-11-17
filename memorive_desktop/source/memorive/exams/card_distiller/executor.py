"""QualityCardDistillerExamExecutor: plans, authorizes and runs the card distiller exam and scores it.
"""
from __future__ import annotations

from memorive.models.provider_aliases import matches_result_model
from copy import deepcopy
from dataclasses import dataclass, field
from functools import cached_property
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping
import uuid
from memorive.models.gateway.resource_limits import resolve_call_limits
from memorive.exams.outcomes import build_repair_round, cumulative_category_repair_penalty
from memorive.exams.common import (
    AUTHORIZATION_SCHEMA,
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
    _sha256,
    _write_json_create_only,
)
from memorive.exams.card_distiller.partition import (
    PARTITION_FIELDS,
    merge_partition_patch,
    partition_decision_targets,
    partition_diagnostics,
    partition_patch_schema,
)
from memorive.exams.card_distiller.policy import (
    CARD_DISTILLER_EXAM_CATEGORY_ID,
    CARD_DISTILLER_REPAIR_ISSUE_GUIDANCE_REVISION,
    MAX_CALLS_PER_STAGE,
    MAX_STRUCTURAL_REPAIR_ROUNDS,
    PASSING_SCORE,
    _card_distiller_candidate_artifact_limit_exceeded,
    _card_distiller_candidate_output_metrics,
    _card_distiller_residual_materiality_projection,
    _distiller_call_bound,
)


@dataclass
class _DistillerStage:
    """One stage of the card distiller exam while it runs (QualityCardDistillerExamExecutor._run_stage).

    The stage's inputs and folder are set when it starts; _distiller_stage_responses asks the model under
    test for forms A and B; when items are wrong, _repair_distiller_stage runs the repair rounds and
    fixes the final scores; _distiller_stage_result scores what is left and writes the stage result."""

    # The stage as asked for.
    target: Mapping[str, Any]
    modules: Mapping[str, Any]
    run_id: str
    stage_name: str
    forms: Mapping[str, Mapping[str, Any]]
    golds: Mapping[str, Mapping[str, Any]]
    budget_cap_cny: float
    state: dict[str, Any]
    structured_chat: Callable[..., Mapping[str, Any]]
    fact_shard_size: int
    projection_shard_size: int
    protocol: Any
    repair: Any
    stage_root: Path
    stage_cost_before: float
    schemas_by_form: dict[str, Any]
    full_schema: Any
    repair_score_kwargs: dict[str, Any]
    # Replies and scores of forms A and B.
    responses: dict[str, dict[str, Any]] = field(default_factory=dict)
    candidate_output_metrics_by_form: dict[str, dict[str, Any]] = field(default_factory=dict)
    initial_scores: Any = None
    first_pass_role_ability_score: float = 0.0
    final_scores: Any = None
    # The repair rounds and the stage's score.
    failed_exam_item_count: int = 0
    repair_rounds: int = 0
    repair_lifecycle: list[dict[str, Any]] = field(default_factory=list)
    cumulative_penalty: dict[str, Any] | None = None
    score: float = 0.0
    verdict: str = ""


@dataclass
class _Shard:
    """One shard of a card distiller stage: the fact map or card projection rows of some targets, which
    the model under test answers in one request (QualityCardDistillerExamExecutor._run_shard)."""

    protocol: Any
    run_id: str
    stage_root: Path
    state: dict[str, Any]
    target: Mapping[str, Any]
    form: Mapping[str, Any]
    artifact: str
    target_ids: list[str]
    schema: Mapping[str, Any]
    structured_chat: Callable[..., Mapping[str, Any]]
    prompt: str = ""

    @property
    def name(self) -> str:
        """The shard's name in its audit and patch file names."""
        return f"{self.artifact}-{self.target_ids[0]}-{self.target_ids[-1]}"

    @cached_property
    def source(self) -> list[dict[str, Any]]:
        """The form's source rows of the targets: its cases for a fact map, its packs for a projection."""
        source_collection = "cases" if self.artifact == "fact_map" else "packs"
        source_id_field = "case_id" if self.artifact == "fact_map" else "pack_id"
        return [
            deepcopy(row)
            for row in self.form.get(source_collection, [])
            if isinstance(row, Mapping) and row.get(source_id_field) in self.target_ids
        ]

    def contract(self) -> dict[str, Any]:
        """What a repaired shard must hold: one row per target in order, and for a card projection the
        exact partition of each pack's source cases."""
        return (
            {
                "row_identity": "case_id",
                "exact_target_order": self.target_ids,
                "rule": "Return exactly one fact_map row per target case_id.",
            }
            if self.artifact == "fact_map"
            else {
                "row_identity": "pack_id",
                "exact_target_order": self.target_ids,
                "partition_fields": list(PARTITION_FIELDS),
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

    def audit(self, response: Mapping[str, Any]) -> dict[str, Any]:
        """The exam protocol's audit of a reply to this shard."""
        return self.protocol.audit_artifact(
            form=dict(self.form),
            response=response,
            schema=dict(self.schema),
            artifact=self.artifact,
            target_ids=self.target_ids,
        )

    def request(self, fields: Mapping[str, Any]) -> str:
        """A repair request: the fields and the hash of the shard's task prompt, as compact sorted JSON."""
        return json.dumps(
            {
                **fields,
                "source_task_prompt_sha256": hashlib.sha256(
                    self.prompt.encode("utf-8")
                ).hexdigest().upper(),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


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
        """Ask the model under test for one shard, then repair it until the protocol's audit passes, in at
        most MAX_STRUCTURAL_REPAIR_ROUNDS rounds. A round asks for the whole shard again (_repair_shard),
        except the last round of a card projection with cases missing or doubled, which asks only where
        those cases go (_patch_shard_partition). Every audit is written to the stage's audits folder."""
        shard = _Shard(
            protocol=modules["protocol"],
            run_id=run_id,
            stage_root=stage_root,
            state=state,
            target=target,
            form=form,
            artifact=artifact,
            target_ids=target_ids,
            schema=schema,
            structured_chat=structured_chat,
        )
        shard.prompt = shard.protocol.build_artifact_prompt(
            dict(form), artifact, target_ids=target_ids, schema=dict(schema)
        )
        response = self._shard_call(shard, shard.prompt, schema, f"INITIAL_{artifact.upper()}")
        audit = shard.audit(response)
        _write_json_create_only(stage_root / "audits" / f"{shard.name}-initial.json", audit)
        if audit["status"] == "PASS":
            return response
        for repair_round in range(1, MAX_STRUCTURAL_REPAIR_ROUNDS + 1):
            diagnostics = (
                partition_diagnostics(response, target_ids, shard.source)
                if artifact == "card_projection"
                else []
            )
            decision_targets = partition_decision_targets(diagnostics)
            if (
                artifact == "card_projection"
                and repair_round == MAX_STRUCTURAL_REPAIR_ROUNDS
                and bool(decision_targets)
            ):
                response, audit = self._patch_shard_partition(
                    shard, repair_round, response, diagnostics, decision_targets
                )
            else:
                response, audit = self._repair_shard(shard, repair_round, response, audit, diagnostics)
            audit_name = (
                f"{shard.name}-repaired.json"
                if repair_round == 1
                else f"{shard.name}-repair-{repair_round:02d}.json"
            )
            _write_json_create_only(stage_root / "audits" / audit_name, audit)
            if audit["status"] == "PASS":
                return response
        raise ValueError("WORKFLOW_MODEL_EXAM_SHARD_CONTRACT_UNCLOSED")

    def _repair_shard(
        self,
        shard: _Shard,
        repair_round: int,
        response: Mapping[str, Any],
        audit: Mapping[str, Any],
        diagnostics: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """One repair round: ask for the whole shard again with the last audit's defects, the contract,
        the partition diagnostics and the source rows. Returns the reply and its audit."""
        repair_prompt = shard.request(
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
                "artifact": shard.artifact,
                "target_ids": shard.target_ids,
                "defects": audit["defects"],
                "artifact_contract": shard.contract(),
                "partition_diagnostics": diagnostics,
                "target_source": shard.source,
                "current_response": response,
                "output_schema": shard.schema,
            }
        )
        response = self._shard_call(
            shard,
            repair_prompt,
            shard.schema,
            f"STRUCTURAL_REPAIR_{shard.artifact.upper()}_ROUND_{repair_round}",
        )
        return response, shard.audit(response)

    def _patch_shard_partition(
        self,
        shard: _Shard,
        repair_round: int,
        response: Mapping[str, Any],
        diagnostics: list[dict[str, Any]],
        decision_targets: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """The last round of a card projection: ask only which field each missing or doubled case goes
        to, merge the decisions into the current projection and record the patch. Returns the merged
        projection and its audit."""
        patch_schema = partition_patch_schema(decision_targets, shard.target_ids)
        repair_prompt = shard.request(
            {
                "instruction": (
                    "Return only the requested partition decisions in exact order. "
                    "For each duplicate case choose the one current semantic field to "
                    "keep; for each missing case choose the best semantic field to add. "
                    "Do not rewrite the projection and do not omit a decision."
                ),
                "repair_round": repair_round,
                "artifact": shard.artifact,
                "decision_targets": decision_targets,
                "partition_diagnostics": diagnostics,
                "target_source": shard.source,
                "current_response": response,
                "output_schema": patch_schema,
            }
        )
        patch_response = self._shard_call(
            shard, repair_prompt, patch_schema, "DIRECTED_PARTITION_PATCH_CARD_PROJECTION_ROUND_2"
        )
        response = merge_partition_patch(response, patch_response, diagnostics, decision_targets)
        audit = shard.audit(response)
        _write_json_create_only(
            shard.stage_root / "partition_patches" / f"{shard.name}-round-02.json",
            {
                "schema_version": "WorkflowModelExamPartitionPatch-v1",
                "decision_targets": decision_targets,
                "patch_response": patch_response,
                "merged_response_sha256": _sha256(response),
                "audit": audit,
            },
        )
        return response, audit

    def _shard_call(
        self,
        shard: _Shard,
        prompt: str,
        response_schema: Mapping[str, Any],
        call_kind: str,
    ) -> dict[str, Any]:
        """One request of a shard to the model under test (_provider_call)."""
        return self._provider_call(
            run_id=shard.run_id,
            stage_root=shard.stage_root,
            state=shard.state,
            target=shard.target,
            prompt=prompt,
            response_schema=response_schema,
            structured_chat=shard.structured_chat,
            call_kind=call_kind,
        )

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
        stage = _DistillerStage(
            target=target, modules=modules, run_id=run_id, stage_name=stage_name, forms=forms,
            golds=golds, budget_cap_cny=budget_cap_cny, state=state, structured_chat=structured_chat,
            fact_shard_size=fact_shard_size, projection_shard_size=projection_shard_size,
            protocol=protocol, repair=repair, stage_root=stage_root,
            stage_cost_before=stage_cost_before, schemas_by_form=schemas_by_form,
            full_schema=full_schema, repair_score_kwargs=repair_score_kwargs,
        )
        self._distiller_stage_responses(stage)
        _write_json_create_only(stage.stage_root / "responses_frozen.json", stage.responses)

        stage.initial_scores = {
            slot: stage.protocol.score_response(
                deepcopy(stage.responses[slot]),
                deepcopy(dict(stage.forms[slot])),
                deepcopy(dict(stage.golds[slot])),
                deepcopy(stage.schemas_by_form[slot]),
            )
            for slot in ("A", "B")
        }
        initial_dual = stage.protocol.aggregate_dual_form(
            stage.initial_scores["A"], stage.initial_scores["B"]
        )
        stage.first_pass_role_ability_score = float(initial_dual["mean"])
        stage.final_scores = deepcopy(stage.initial_scores)
        wrong_count = sum(
            int(score["critical_count"]) + int(score["major_count"])
            for score in stage.initial_scores.values()
        )
        stage.failed_exam_item_count = 0
        stage.repair_rounds = 0
        stage.repair_lifecycle = []
        stage.cumulative_penalty = None
        if wrong_count:
            self._repair_distiller_stage(stage)
        else:
            dual = stage.protocol.aggregate_dual_form(
                stage.initial_scores["A"], stage.initial_scores["B"]
            )
            stage.score = float(dual["mean"])
            stage.verdict = (
                "PASS"
                if dual["quality_hard_gate"] == "PASS" and stage.score >= PASSING_SCORE
                else "FAIL"
            )
        return self._distiller_stage_result(stage)

    def _distiller_stage_responses(self, stage: _DistillerStage) -> None:
        """Ask the model under test for forms A and B, shard by shard, and keep each reply."""
        for slot in ("A", "B"):
            form = stage.forms[slot]
            form_schema = stage.schemas_by_form[slot]
            fact_ids = [str(row["case_id"]) for row in form["cases"]]
            pack_ids = [str(row["pack_id"]) for row in form["packs"]]
            facts: list[dict[str, Any]] = []
            projections: list[dict[str, Any]] = []
            for start in range(0, len(fact_ids), stage.fact_shard_size):
                target_ids = fact_ids[start : start + stage.fact_shard_size]
                schema = stage.protocol.artifact_schema(
                    form_schema, "fact_map", target_count=len(target_ids)
                )
                shard = self._run_shard(
                    modules=stage.modules,
                    run_id=stage.run_id,
                    stage_root=stage.stage_root,
                    state=stage.state,
                    target=stage.target,
                    form=form,
                    artifact="fact_map",
                    target_ids=target_ids,
                    schema=schema,
                    structured_chat=stage.structured_chat,
                )
                facts.extend(deepcopy(shard["fact_map"]))
            for start in range(0, len(pack_ids), stage.projection_shard_size):
                target_ids = pack_ids[start : start + stage.projection_shard_size]
                schema = stage.protocol.artifact_schema(
                    form_schema, "card_projection", target_count=len(target_ids)
                )
                shard = self._run_shard(
                    modules=stage.modules,
                    run_id=stage.run_id,
                    stage_root=stage.stage_root,
                    state=stage.state,
                    target=stage.target,
                    form=form,
                    artifact="card_projection",
                    target_ids=target_ids,
                    schema=schema,
                    structured_chat=stage.structured_chat,
                )
                projections.extend(deepcopy(shard["card_projection"]))
            response = {"fact_map": facts, "card_projection": projections}
            stage.protocol.validate_response_shape(
                response, dict(form), deepcopy(form_schema)
            )
            output_metrics = _card_distiller_candidate_output_metrics(response)
            output_metrics["core_x2_reference_exceeded"] = (
                _card_distiller_candidate_artifact_limit_exceeded(output_metrics)
            )
            output_metrics["core_x2_reference_semantics"] = (
                "DIAGNOSTIC_ONLY_NOT_INVALIDATION_MODEL_FAIL_OR_REPAIR"
            )
            stage.candidate_output_metrics_by_form[slot] = output_metrics
            stage.responses[slot] = response

    def _repair_distiller_stage(self, stage: _DistillerStage) -> None:
        """Repair rounds for the wrong items: directed repair packages, the repaired replies and their
        scores, the penalty and the final scores."""
        current_responses = deepcopy(stage.responses)
        current_scores = deepcopy(stage.initial_scores)
        initial_contract: Mapping[str, Any] | None = None
        first_round_exact_sets: dict[str, list[str]] | None = None
        round_two_burden: dict[str, Any] | None = None
        unchanged_surface_sha256 = _sha256(
            {
                "forms": stage.forms,
                "golds_excluded": True,
                "schema": stage.full_schema,
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
            package = stage.repair.build_exam_directed_repair_package(
                forms=stage.forms,
                original_responses=current_responses,
                original_scores=source_scores,
                schema=stage.full_schema,
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
                run_id=stage.run_id,
                stage_root=stage.stage_root,
                state=stage.state,
                target=stage.target,
                package=package,
                structured_chat=stage.structured_chat,
                call_kind=(
                    f"WHOLE_EXAM_DIRECTED_REPAIR_ROUND_{round_number}"
                ),
            )
            merge = stage.repair.merge_exam_directed_repair(
                forms=stage.forms,
                original_responses=current_responses,
                repair_response=repair_response,
                schema=stage.full_schema,
                repair_contract=package["contract"],
            )
            current_responses = deepcopy(merge["responses"])
            current_scores = {
                form_id: stage.protocol.score_response(
                    deepcopy(current_responses[form_id]),
                    deepcopy(dict(stage.forms[form_id])),
                    deepcopy(dict(stage.golds[form_id])),
                    deepcopy(stage.schemas_by_form[form_id]),
                )
                for form_id in ("A", "B")
            }
            if round_number == 2:
                second = stage.repair.score_exam_after_directed_repair(
                    forms=stage.forms,
                    golds=stage.golds,
                    original_scores=source_scores,
                    merged_responses=current_responses,
                    schema=stage.full_schema,
                    repair_contract=package["contract"],
                    **stage.repair_score_kwargs,
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
            stage.repair_lifecycle.append(lifecycle)
            if not sum(
                int(score["critical_count"])
                + int(score["major_count"])
                for score in current_scores.values()
            ):
                break
        assert initial_contract is not None
        final = stage.repair.score_exam_after_directed_repair(
            forms=stage.forms,
            golds=stage.golds,
            original_scores=stage.initial_scores,
            merged_responses=current_responses,
            schema=stage.full_schema,
            repair_contract=initial_contract,
            **stage.repair_score_kwargs,
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
        stage.cumulative_penalty = cumulative_category_repair_penalty(
            penalty_rounds
        )
        stage.score = float(final["final_score"])
        stage.verdict = (
            "PASS"
            if final["quality_hard_gate"] == "PASS"
            and stage.score >= PASSING_SCORE
            else "FAIL"
        )
        stage.failed_exam_item_count = int(final["failed_exam_item_count"])
        stage.repair_rounds = len(stage.repair_lifecycle)
        stage.final_scores = deepcopy(current_scores)

    def _distiller_stage_result(self, stage: _DistillerStage) -> Any:
        """Score what is left after the repairs, decide the blocking failures and the stage verdict, check
        the stage's cost, and write and return the stage result."""
        score_projection = self._stage_score_projection(
            protocol=stage.protocol,
            forms=stage.forms,
            initial_scores=stage.initial_scores,
            final_scores=stage.final_scores,
            repair_rounds=stage.repair_rounds,
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
                stage.score = float(projected_score)
                stage.first_pass_role_ability_score = float(projected_first_pass)
                stage.verdict = str(projected_verdict)
                primary_case_ids = set(projected_ids)
            else:
                raise ValueError("CARD_DISTILLER_STAGE_SCORE_PROJECTION_INVALID")
        blocking_failures: list[dict[str, Any]] = []
        for form_id in ("A", "B"):
            for detail in stage.final_scores[form_id].get("details", []):
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
                                f"{stage.stage_name}/scores/form_{form_id}/"
                                f"case_details/{detail.get('case_id')}"
                            ),
                            "blocking": True,
                        }
                    )
        if (
            stage.verdict == "FAIL"
            and not blocking_failures
            and primary_case_ids is None
        ):
            blocking_failures.append(
                {
                    "case_id": "CARD_DISTILLER_AGGREGATE",
                    "failed_fields": ["aggregate_score_or_repair_burden"],
                    "evidence_anchor": f"{stage.stage_name}/stage_result/aggregate",
                    "blocking": True,
                }
            )
        residual_materiality = (
            _card_distiller_residual_materiality_projection(
                blocking_failures=blocking_failures,
                golds=stage.golds,
            )
        )
        blocking_failures = residual_materiality["blocking_failures"]
        model_fail_projection = _primary_model_fail_projection(
            category_id=self.EXAM_CATEGORY_ID,
            score=stage.score,
            raw_quality_verdict=stage.verdict,
            blocking_failures=blocking_failures,
            guarded_operational_verdict=(
                "SUITABLE_WITH_CARD_REVIEWER_AND_GOVERNED_DISPOSITION"
            ),
        )
        repair_cycle_closed = (
            stage.repair_rounds == self.SEMANTIC_REPAIR_ROUND_LIMIT
            or stage.failed_exam_item_count == 0
        )
        stage_cost = round(stage.state["estimated_cost_cny"] - stage.stage_cost_before, 9)
        if stage_cost > stage.budget_cap_cny:
            raise ValueError("WORKFLOW_MODEL_EXAM_STAGE_COST_CAP_EXCEEDED")
        stage_result = {
            "schema_version": "WorkflowModelExamStageResult-v1",
            "stage": stage.stage_name,
            "chain_status": "PASS",
            "quality_verdict": stage.verdict,
            "score": round(stage.score, 2),
            "first_pass_role_ability_score": round(
                stage.first_pass_role_ability_score, 2
            ),
            "post_repair_score": round(stage.score, 2),
            "failed_exam_item_count": stage.failed_exam_item_count,
            "repair_rounds": stage.repair_rounds,
            "semantic_repair_round_limit": self.SEMANTIC_REPAIR_ROUND_LIMIT,
            "semantic_repair_lifecycle": stage.repair_lifecycle,
            "cumulative_category_repair_penalty": stage.cumulative_penalty,
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
                {"forms": stage.forms, "schema": stage.full_schema}
            ),
            "transport_retry_is_semantic_repair": False,
            "provider_calls": stage.state["stage_calls"],
            "estimated_cost_cny": stage_cost,
            "budget_cap_cny": stage.budget_cap_cny,
            "provider_received_answer_key": False,
            "reference_regression_only": stage.stage_name != "low_cost",
            "candidate_output_metrics_by_form": (
                stage.candidate_output_metrics_by_form
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
        _write_json_create_only(stage.stage_root / "stage_result.json", stage_result)
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
