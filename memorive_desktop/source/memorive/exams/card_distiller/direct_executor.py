"""QualityCardDistillerDirectExamExecutor: the card distiller exam's direct (successor) form.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any, Callable, Mapping
import uuid
from memorive.exams.outcomes import (
    ExecutionOutcome,
    Scorability,
    admit_quality_fail,
    classify_execution_outcome,
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
    PLAN_SCHEMA,
    RESULT_SCHEMA,
    _compact,
    _file_sha256,
    _load_json,
    _not_available,
    _not_run,
    _serialized_request,
    _sha256,
    _write_json_create_only,
    core_primary_workload_profile,
    _ollama_exam_profile,
    _plan_authorization_reason,
)
from memorive.exams.card_distiller.policy import (
    CARD_DISTILLER_REPAIR_ISSUE_GUIDANCE_REVISION,
    CARD_DISTILLER_RESIDUAL_MATERIALITY_REVISION,
    CARD_DISTILLER_SUCCESSOR_SAMPLE_REVISION,
    _card_distiller_candidate_output_metrics,
)
from memorive.exams.card_distiller.executor import QualityCardDistillerExamExecutor


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
            if _ollama_exam_profile(
                target, "structured_chat_adapter", "EvaluationAssets_LOCAL_STRUCTURED_CHAT_V1"
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
        """A command-line plan acknowledges a subscription without per-call price."""
        return _plan_authorization_reason(authorization, plan, subscription=plan.get("profile_kind") == "CLI")

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
