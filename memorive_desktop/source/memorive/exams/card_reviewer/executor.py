"""QualityCardReviewerExamExecutor: plans, authorizes and runs the card reviewer exam and scores it.
"""
from __future__ import annotations

from memorive.models.provider_aliases import matches_result_model
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.util
import json
import math
from pathlib import Path
import re
from time import monotonic
from typing import Any, Callable, Mapping
import uuid
from memorive.models.gateway.resource_limits import resolve_call_limits
from memorive.exams.outcomes import (
    ExecutionOutcome,
    Scorability,
    admit_quality_fail,
    build_repair_round,
    classify_execution_outcome,
    cumulative_category_repair_penalty,
)
from memorive.exams.common import (
    AUTHORIZATION_SCHEMA,
    EXECUTOR_REF,
    MAX_OUTPUT_TOKENS,
    PLAN_SCHEMA,
    REQUEST_TIMEOUT_SECONDS,
    RESULT_SCHEMA,
    SAFE_PACKAGE,
    _compact,
    _effective_structured_prompt_metrics,
    _file_sha256,
    _load_json,
    _not_available,
    _not_run,
    _public_forms,
    _sha256,
    _write_json_create_only,
    _ollama_exam_profile,
    _plan_authorization_reason,
)
from memorive.exams.card_reviewer.policy import (
    CARD_REVIEWER_EXAM_CATEGORY_ID,
    CARD_REVIEWER_OPERATIONAL_PASSING_SCORE,
    CARD_REVIEWER_OPERATIONAL_POLICY_REVISION,
    CARD_REVIEWER_REPAIR_PROMPT_PROJECTION_REVISION,
    CARD_REVIEWER_SCORE_SEMANTICS,
    CARD_REVIEWER_SCORING_PROJECTION_REVISION,
    CORE_CARD_REVIEWER_LIFECYCLE_REVISION,
    _api_worst_case_request_reserve_cny,
    card_reviewer_comparison_metadata,
)
from memorive.exams.card_reviewer.successor import (
    CARD_REVIEWER_SUCCESSOR_POINTER_NORMALIZATION_REVISION,
)
from memorive.exams.card_reviewer.repair_prompt import project_card_reviewer_repair_prompt
from memorive.exams.card_reviewer.role_projection import score_card_reviewer_role_projection


@dataclass
class _CardReviewerExamRun:
    """One run of the card reviewer exam (QualityCardReviewerExamExecutor.execute).

    The plan, the run folder and the call counters are set when the run starts; _load_card_reviewer_inputs
    reads the pack, its schemas and contracts and the two forms; execute sends forms A and B and scores
    the replies; _repair_card_reviewer_forms runs the repair round and fixes the verdict, the scorecard
    and the comparison; _publish_card_reviewer_exam decides the operational outcome, writes the
    evidence and builds the result. A failure is reported from the plan and counters as they are."""

    # The run as planned.
    target: Mapping[str, Any]
    context: Mapping[str, Any]
    structured_chat: Callable[..., Mapping[str, Any]]
    plan: Mapping[str, Any]
    requested_model: Any
    started: float
    run_id: str
    run_root: Path
    state: dict[str, Any]
    # The pack and the inputs of this run.
    protocol: Any = None
    exam: Any = None
    schema: Any = None
    prompt_contract: Any = None
    scoring_protocol: Any = None
    comparison_metadata: Any = None
    exam_inputs: Any = None
    forms: Any = None
    golds: Any = None
    form_schemas: Any = None
    # Replies and first scores of forms A and B.
    scores: dict[str, dict[str, Any]] = field(default_factory=dict)
    responses: dict[str, dict[str, Any]] = field(default_factory=dict)
    initial_form_scores: Any = None
    pointer_normalization_actions: Any = None
    # The repair round and the verdict it leads to.
    repair_projection: dict[str, Any] | None = None
    repair_prompt_projection: dict[str, Any] | None = None
    qualification_verdict: str = ""
    operational_eligibility: str = ""
    operational_safety_gate: str = ""
    safety_blocking_residual_count: int | None = None
    calibration_residual_count: int | None = None
    scorecard: dict[str, Any] | None = None
    comparison_projection: Any = None
    verdict: Any = None
    score: Any = None
    score_semantics: Any = None
    repair_rounds: Any = None
    repair_completion_rate: Any = None
    repair_outcome_score: Any = None
    repair_outcome_form_scores: Any = None
    role_ability_form_scores: Any = None
    failed_exam_item_count: Any = None


class QualityCardReviewerExamExecutor:
    """Run the frozen Quality Card Reviewer A/B reference regression.

    The same Gold-isolated protocol is used for API, subscription CLI, and an
    exact-digest loopback local model.  A score produced here is diagnostic
    only and cannot grant model qualification.
    """

    EXECUTOR_REF = (
        "SESSIONS_CARD_REVIEWER_EXECUTOR_R1_2_Quality_R1_1_CORE"
    )
    MODE = "LIVE_Quality_CARD_REVIEWER_REFERENCE_REGRESSION"
    Quality_SEMANTIC_CALL_LIMIT = 5
    API_MAXIMUM_REQUEST_ATTEMPTS = 15
    CLI_MAXIMUM_REQUEST_ATTEMPTS = 10
    LOCAL_MAXIMUM_REQUEST_ATTEMPTS = 10
    CASES_PER_CHUNK = 36
    TRANSIENT_RETRY_LIMIT = 1
    REQUEST_TIMEOUT_SECONDS = 600
    API_BUDGET_CAP_CNY = 6.0
    SEMANTIC_REPAIR_ROUND_LIMIT = 1

    def __init__(
        self,
        *,
        scratch_root: Path,
        quality_package: str,
        reference_pack_root: Path,
        pricing_profile_path: Path | None = None,
        api_request_cap: int | None = None,
        cli_request_cap: int | None = None,
        local_request_cap: int | None = None,
    ) -> None:
        if not isinstance(quality_package, str) or not SAFE_PACKAGE.fullmatch(
            quality_package
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_Quality_PACKAGE_INVALID")
        self.scratch_root = Path(scratch_root).resolve()
        self._quality_package = quality_package
        self._reference_pack_root = Path(reference_pack_root).resolve()
        default_pricing = (
            self._reference_pack_root.parents[2]
            / "card_distiller_exam"
            / "v1"
            / "deepseek_flash_to_pro_profile.public.json"
        )
        self._pricing_profile_path = (
            Path(pricing_profile_path).resolve()
            if pricing_profile_path is not None
            else default_pricing
        )
        self._api_request_cap = self._validated_request_cap(
            api_request_cap,
            maximum=self.API_MAXIMUM_REQUEST_ATTEMPTS,
        )
        self._cli_request_cap = self._validated_request_cap(
            cli_request_cap,
            maximum=self.CLI_MAXIMUM_REQUEST_ATTEMPTS,
        )
        self._local_request_cap = self._validated_request_cap(
            local_request_cap,
            maximum=self.LOCAL_MAXIMUM_REQUEST_ATTEMPTS,
        )
        self._modules: dict[str, Any] | None = None

    @staticmethod
    def _validated_request_cap(value: int | None, *, maximum: int) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("WORKFLOW_MODEL_EXAM_REQUEST_CAP_INVALID")
        return value

    def _request_cap(self, profile_kind: str) -> int:
        explicit = {'API': self._api_request_cap, 'CLI': self._cli_request_cap,
                    'LOCAL': self._local_request_cap}[profile_kind]
        if explicit is not None:
            return explicit  # preserve an explicitly authorized per-run limit
        forms = _public_forms(self._reference_pack_root)
        if hasattr(self, '_shards'):
            shards = sum(len(self._shards(form, form_id=key, artifact=artifact,
                                         profile_kind=profile_kind))
                         for key, form in forms.items()
                         for artifact in ('claim_map', 'analysis_summary'))
            # Existing analysis loop: initial + one structural repair; each may
            # use the existing transient retries. No additional call is added.
            return shards * 2 * (1 + self.TRANSIENT_RETRY_LIMIT)
        shards = sum(math.ceil(len(form['cases']) / (self.CASES_PER_CHUNK or len(form['cases'])))
                     for form in forms.values())
        return shards * self.Quality_SEMANTIC_CALL_LIMIT * (1 + self.TRANSIENT_RETRY_LIMIT)

    def _structured_output_policy(
        self,
        target: Mapping[str, Any],
    ) -> tuple[dict[str, Any], int | None, str]:
        """Legacy executor policy; the successor overrides this per channel."""

        return deepcopy(dict(target)), MAX_OUTPUT_TOKENS, "EXPLICIT"

    def _subject_request_projection(
        self,
        target: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Return the exact request visible to the selected subject."""

        del target
        return deepcopy(dict(request))

    def _quality(self) -> dict[str, Any]:
        if self._modules is None:
            names = {
                "protocol": "card_reviewer_exam_protocol",
                "exam": "card_reviewer_exam",
                "pack": "card_reviewer_exam_pack",
                "reference": "reference_exam_pack",
            }
            self._modules = {
                key: importlib.import_module(f"{self._quality_package}.{suffix}")
                for key, suffix in names.items()
            }
        return self._modules

    @staticmethod
    def _quality_asset_failure(stage: str, exc: Exception) -> dict[str, Any]:
        missing_module = getattr(exc, "name", None)
        missing_suffix = ""
        if isinstance(missing_module, str) and re.fullmatch(
            r"[A-Za-z0-9_.-]{1,128}", missing_module
        ):
            missing_suffix = "_MISSING_" + re.sub(
                r"[^A-Za-z0-9]", "_", missing_module
            ).upper()
        return _not_available(
            "WORKFLOW_MODEL_EXAM_Quality_"
            f"{stage}_{type(exc).__name__.upper()}{missing_suffix}"
        )

    def _exam_inputs(
        self,
        *,
        pack: Any,
        schema: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        del context
        return {
            "forms": {
                form_id: _load_json(pack.member(f"forms/form_{form_id}.json"))
                for form_id in ("A", "B")
            },
            "golds": {
                form_id: _load_json(pack.member(f"gold/form_{form_id}_gold.json"))
                for form_id in ("A", "B")
            },
            "schemas": {"A": deepcopy(dict(schema)), "B": deepcopy(dict(schema))},
            "execution_profile": "LEGACY_FULL_DUAL_FORM",
            "sample_metadata": None,
        }

    def _execute_form_protocol(
        self,
        *,
        protocol: Any,
        exam: Any,
        form: Mapping[str, Any],
        schema: Mapping[str, Any],
        prompt_contract: Mapping[str, Any],
        scoring_protocol: Mapping[str, Any],
        subject_call: Callable[[Mapping[str, Any]], Mapping[str, Any]],
        gold: Mapping[str, Any],
    ) -> dict[str, Any]:
        del exam
        return protocol.execute_form(
            form=form,
            schema=schema,
            prompt_contract=prompt_contract,
            scoring_protocol=scoring_protocol,
            subject_call=subject_call,
            gold_loader=lambda: deepcopy(dict(gold)),
        )

    def _score_after_semantic_repair(
        self,
        *,
        exam: Any,
        forms: Mapping[str, Mapping[str, Any]],
        golds: Mapping[str, Mapping[str, Any]],
        original_scores: Mapping[str, Mapping[str, Any]],
        merged_responses: Mapping[str, Mapping[str, Any]],
        schema: Mapping[str, Any],
        scoring_protocol: Mapping[str, Any],
        repair_contract: Mapping[str, Any],
    ) -> dict[str, Any]:
        return score_card_reviewer_role_projection(
            exam=exam,
            forms=forms,
            golds=golds,
            original_scores=original_scores,
            merged_responses=merged_responses,
            schema=schema,
            scoring_protocol=scoring_protocol,
            repair_contract=repair_contract,
        )

    def _score_current_responses(
        self,
        *,
        exam: Any,
        forms: Mapping[str, Mapping[str, Any]],
        golds: Mapping[str, Mapping[str, Any]],
        responses: Mapping[str, Mapping[str, Any]],
        schema: Mapping[str, Any],
        scoring_protocol: Mapping[str, Any],
    ) -> dict[str, dict[str, Any]]:
        return {
            form_id: dict(
                exam.score_form(
                    form=forms[form_id],
                    final_response=responses[form_id],
                    gold=golds[form_id],
                    schema=schema,
                    scoring_protocol=scoring_protocol,
                    repair_metrics={
                        "form_case_count": len(
                            exam.form_cases(forms[form_id])[1]
                        ),
                        "repair_turns": 0,
                        "directed_repair_case_count": 0,
                        "all_repairs_succeeded": True,
                    },
                )
            )
            for form_id in ("A", "B")
        }

    @staticmethod
    def _wrong_item_count(scores: Mapping[str, Mapping[str, Any]]) -> int:
        return sum(
            1
            for form_id in ("A", "B")
            for detail in scores[form_id].get("details", [])
            if isinstance(detail, Mapping)
            and isinstance(detail.get("score"), (int, float))
            and not isinstance(detail.get("score"), bool)
            and float(detail["score"]) < 100.0
        )

    @staticmethod
    def _round_two_category_burden(
        *,
        exam: Any,
        forms: Mapping[str, Mapping[str, Any]],
        source_scores: Mapping[str, Mapping[str, Any]],
        exact_sets: Mapping[str, list[str]],
        scoring_protocol: Mapping[str, Any],
    ) -> dict[str, Any]:
        directed = scoring_protocol.get("directed_repair")
        if not isinstance(directed, Mapping):
            raise ValueError("CARD_REVIEWER_DIRECTED_REPAIR_SCORING_REQUIRED")
        fixed_points = float(directed.get("fixed_turn_penalty", 0.0))
        volume_knots = directed.get("volume_penalty_knots")
        if not isinstance(volume_knots, list):
            raise ValueError("CARD_REVIEWER_DIRECTED_REPAIR_KNOTS_REQUIRED")
        by_form: dict[str, dict[str, Any]] = {}
        for form_id in ("A", "B"):
            target_ids = list(exact_sets.get(form_id, []))
            details = {
                str(item.get("case_id")): item
                for item in source_scores[form_id].get("details", [])
                if isinstance(item, Mapping)
                and isinstance(item.get("case_id"), str)
            }
            if any(case_id not in details for case_id in target_ids):
                raise ValueError("ROUND2_RESIDUAL_DETAIL_MISSING")
            issue_categories: set[str] = set()
            severity_categories: set[str] = set()
            for case_id in target_ids:
                issues, _fields, severity = exam.reviewer_exam_issue_metadata(
                    details[case_id]
                )
                issue_categories.update(issues)
                severity_categories.add(severity)
            form_case_count = len(exam.form_cases(forms[form_id])[1])
            nonlinear = exam.reviewer_exam_form_penalty(
                form_case_count=form_case_count,
                wrong_case_count=len(target_ids),
                issue_categories=issue_categories,
                severity_categories=severity_categories,
            )
            fixed = fixed_points if target_ids else 0.0
            volume = (
                float(
                    exam.interpolate(
                        volume_knots,
                        len(target_ids) / form_case_count,
                    )
                )
                if target_ids
                else 0.0
            )
            total = float(nonlinear["total"]) + fixed + volume
            by_form[form_id] = {
                "target_case_ids": target_ids,
                "target_case_count": len(target_ids),
                "nonlinear_category_penalty": round(
                    float(nonlinear["total"]), 6
                ),
                "nonlinear_category_penalty_detail": nonlinear,
                "fixed_penalty": round(fixed, 6),
                "volume_penalty": round(volume, 6),
                "total": round(total, 6),
            }
        return {
            "schema_version": "DesktopCardReviewerRound2CategoryBurden-v1",
            "by_form": by_form,
            "nonlinear_category_penalty_mean": round(
                sum(
                    float(row["nonlinear_category_penalty"])
                    for row in by_form.values()
                )
                / 2.0,
                6,
            ),
            "fixed_penalty_mean": round(
                sum(float(row["fixed_penalty"]) for row in by_form.values())
                / 2.0,
                6,
            ),
            "volume_penalty_mean": round(
                sum(float(row["volume_penalty"]) for row in by_form.values())
                / 2.0,
                6,
            ),
            "total_mean": round(
                sum(float(row["total"]) for row in by_form.values()) / 2.0,
                6,
            ),
            "coefficient_source": (
                "Quality_CARD_REVIEWER_EXAM_REPAIR_PENALTY_AND_"
                "DIRECTED_REPAIR_CONTRACTS"
            ),
            "cross_category_coefficients_reused": False,
        }

    @staticmethod
    def _apply_round_two_burden(
        projection: Mapping[str, Any],
        burden: Mapping[str, Any],
    ) -> dict[str, Any]:
        value = deepcopy(dict(projection))
        form_scores = deepcopy(dict(value["repair_outcome_form_scores"]))
        forms = deepcopy(dict(value["forms"]))
        by_form = burden["by_form"]
        for form_id in ("A", "B"):
            adjusted = max(
                0.0,
                float(form_scores[form_id])
                - float(by_form[form_id]["total"]),
            )
            form_scores[form_id] = round(adjusted, 6)
            forms[form_id]["round_two_additional_burden"] = deepcopy(
                dict(by_form[form_id])
            )
            forms[form_id]["reviewer_repair_outcome_form_score"] = round(
                adjusted, 6
            )
        outcome = round(sum(form_scores.values()) / 2.0, 6)
        residual_burden = float(
            value.get("reviewer_repair_burden", {}).get(
                "governed_disposition_burden_total_mean", 0.0
            )
        )
        value["forms"] = forms
        value["repair_outcome_form_scores"] = form_scores
        value["reviewer_repair_outcome_score"] = outcome
        value["core_closure_score"] = round(
            max(0.0, outcome - residual_burden), 6
        )
        value["round_two_additional_burden"] = deepcopy(dict(burden))
        value["reviewer_repair_burden"]["round_two_additional_burden"] = (
            deepcopy(dict(burden))
        )
        value["reviewer_repair_burden"]["burden_order"] = [
            "ROUND1_CATEGORY_NONLINEAR_BURDEN",
            "ROUND1_DELTA_RECHECK",
            "ROUND2_RESIDUAL_CATEGORY_NONLINEAR_PLUS_FIXED_VOLUME_BURDEN",
            "ROUND2_DELTA_RECHECK",
            "GOVERNED_DISPOSITION_IF_NEEDED",
        ]
        value["reviewer_repair_burden"][
            "governed_disposition_is_second_repair"
        ] = False
        return value

    def _execute_semantic_repair_rounds(
        self,
        *,
        run_id: str,
        run_root: Path,
        state: dict[str, Any],
        target: Mapping[str, Any],
        structured_chat: Callable[..., Mapping[str, Any]],
        exam: Any,
        forms: Mapping[str, Mapping[str, Any]],
        golds: Mapping[str, Mapping[str, Any]],
        form_schemas: Mapping[str, Mapping[str, Any]],
        schema: Mapping[str, Any],
        scoring_protocol: Mapping[str, Any],
        prompt_contract: Mapping[str, Any],
        original_scores: Mapping[str, Mapping[str, Any]],
        original_responses: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        current_scores = {
            key: deepcopy(dict(value))
            for key, value in original_scores.items()
        }
        current_responses = {
            key: deepcopy(dict(value))
            for key, value in original_responses.items()
        }
        initial_contract: Mapping[str, Any] | None = None
        first_round_exact_sets: dict[str, list[str]] | None = None
        final_projection: dict[str, Any] | None = None
        prompt_projections: list[dict[str, Any]] = []
        lifecycle_contracts: list[dict[str, Any]] = []
        round_two_burden: dict[str, Any] | None = None
        unchanged_surface_sha256 = _sha256(
            {
                "forms": forms,
                "form_schemas": form_schemas,
                "scoring_protocol": scoring_protocol,
                "prompt_contract": prompt_contract,
            }
        )
        for round_number in range(1, self.SEMANTIC_REPAIR_ROUND_LIMIT + 1):
            if not self._wrong_item_count(current_scores):
                break
            source_scores = deepcopy(current_scores)
            if round_number == 2:
                assert first_round_exact_sets is not None
                for form_id in ("A", "B"):
                    allowed = set(first_round_exact_sets[form_id])
                    source_scores[form_id]["details"] = [
                        deepcopy(detail)
                        for detail in source_scores[form_id].get("details", [])
                        if isinstance(detail, Mapping)
                        and detail.get("case_id") in allowed
                        and isinstance(detail.get("score"), (int, float))
                        and not isinstance(detail.get("score"), bool)
                        and float(detail["score"]) < 100.0
                    ]
                if not any(
                    source_scores[form_id]["details"]
                    for form_id in ("A", "B")
                ):
                    break
            package = exam.build_exam_directed_repair_package(
                forms=forms,
                original_responses=current_responses,
                original_scores=source_scores,
                schema=schema,
            )
            if initial_contract is None:
                initial_contract = deepcopy(dict(package["contract"]))
            exact_sets = {
                form_id: list(case_ids)
                for form_id, case_ids in package["contract"][
                    "wrong_item_exact_set"
                ].items()
            }
            if first_round_exact_sets is None:
                first_round_exact_sets = deepcopy(exact_sets)
            repair_prompt_projection = project_card_reviewer_repair_prompt(
                package,
                reviewer_system_rubric=str(prompt_contract["system"]),
            )
            projected_prompt_payload = json.loads(
                repair_prompt_projection["prompt"]
            )
            lifecycle_contract = build_repair_round(
                round_number=round_number,
                category_id=CARD_REVIEWER_EXAM_CATEGORY_ID,
                targets_by_form=exact_sets,
                prior_targets_by_form=(
                    first_round_exact_sets
                    if round_number == 2
                    else None
                ),
                prompt_projection=projected_prompt_payload,
                unchanged_surface_sha256=unchanged_surface_sha256,
            )
            if round_number == 2:
                round_two_burden = self._round_two_category_burden(
                    exam=exam,
                    forms=forms,
                    source_scores=source_scores,
                    exact_sets=exact_sets,
                    scoring_protocol=scoring_protocol,
                )
            request = {
                "phase": "EXAM_DIRECTED_REPAIR",
                "semantic_repair_round": round_number,
                "semantic_repair_round_limit": (
                    self.SEMANTIC_REPAIR_ROUND_LIMIT
                ),
                "system": (
                    str(prompt_contract["system"])
                    + "\n\n"
                    + (
                        "Perform directed repair round 1. Replace all and only "
                        "the listed rows, return every listed case exactly once, "
                        "and include no Markdown."
                        if round_number == 1
                        else "Perform directed repair round 2 on the frozen "
                        "round-1 residual exact set only. Do not revisit any "
                        "unlisted row; return every listed case exactly once, "
                        "and include no Markdown."
                    )
                ),
                "user": repair_prompt_projection["prompt"],
                "form": {
                    "forms": [
                        form_id for form_id in ("A", "B") if exact_sets.get(form_id)
                    ],
                    "wrong_item_exact_set": exact_sets,
                },
                "output_schema": package["repair_schema"],
                "repair_contract_sha256": package["contract"][
                    "contract_sha256"
                ],
                "successor_repair_lifecycle_sha256": lifecycle_contract[
                    "contract_sha256"
                ],
                "repair_prompt_projection": {
                    key: value
                    for key, value in repair_prompt_projection.items()
                    if key != "prompt"
                },
            }
            request["request_payload_hash"] = exam.sha256(request)
            repair_response = self._chunked_provider_call(
                run_id=run_id,
                run_root=run_root,
                state=state,
                target=target,
                request=request,
                response_schema=package["repair_schema"],
                structured_chat=structured_chat,
                call_kind=f"WHOLE_EXAM_DIRECTED_REPAIR_ROUND_{round_number}",
            )
            merged = exam.merge_exam_directed_repair(
                forms=forms,
                original_responses=current_responses,
                repair_response=repair_response,
                schema=schema,
                repair_contract=package["contract"],
            )
            current_responses = {
                key: deepcopy(dict(value))
                for key, value in merged["responses"].items()
            }
            assert initial_contract is not None
            final_projection = self._score_after_semantic_repair(
                exam=exam,
                forms=forms,
                golds=golds,
                original_scores=original_scores,
                merged_responses=current_responses,
                schema=schema,
                scoring_protocol=scoring_protocol,
                repair_contract=initial_contract,
            )
            current_scores = self._score_current_responses(
                exam=exam,
                forms=forms,
                golds=golds,
                responses=current_responses,
                schema=schema,
                scoring_protocol=scoring_protocol,
            )
            prompt_projections.append(
                {
                    "semantic_repair_round": round_number,
                    **{
                        key: value
                        for key, value in repair_prompt_projection.items()
                        if key != "prompt"
                    },
                }
            )
            lifecycle_contracts.append(lifecycle_contract)

        if final_projection is None or initial_contract is None:
            raise ValueError("SEMANTIC_REPAIR_CYCLE_DID_NOT_EXECUTE")
        initial_burden = float(
            final_projection["reviewer_repair_burden"][
                "initial_burden_total_mean"
            ]
        )
        penalty_rounds: list[dict[str, Any]] = [
            {
                "round_number": 1,
                "category_id": CARD_REVIEWER_EXAM_CATEGORY_ID,
                "nonlinear_category_penalty": initial_burden,
                "fixed_penalty": 0.0,
                "volume_penalty": 0.0,
            }
        ]
        if round_two_burden is not None:
            penalty_rounds.append(
                {
                    "round_number": 2,
                    "category_id": CARD_REVIEWER_EXAM_CATEGORY_ID,
                    "nonlinear_category_penalty": round_two_burden[
                        "nonlinear_category_penalty_mean"
                    ],
                    "fixed_penalty": round_two_burden["fixed_penalty_mean"],
                    "volume_penalty": round_two_burden["volume_penalty_mean"],
                }
            )
            final_projection = self._apply_round_two_burden(
                final_projection,
                round_two_burden,
            )
        cumulative_penalty = cumulative_category_repair_penalty(
            penalty_rounds
        )
        final_projection["semantic_repair_rounds"] = len(
            lifecycle_contracts
        )
        final_projection["semantic_repair_round_limit"] = (
            self.SEMANTIC_REPAIR_ROUND_LIMIT
        )
        final_projection["second_semantic_repair_allowed"] = (
            self.SEMANTIC_REPAIR_ROUND_LIMIT >= 2
        )
        final_projection["semantic_repair_lifecycle"] = lifecycle_contracts
        final_projection["cumulative_category_repair_penalty"] = (
            cumulative_penalty
        )
        final_projection["transport_retry_is_semantic_repair"] = False
        return {
            "projection": final_projection,
            "responses": current_responses,
            "repair_rounds": len(lifecycle_contracts),
            "repair_prompt_projection": prompt_projections[-1],
            "repair_prompt_projections": prompt_projections,
            "semantic_repair_lifecycle": lifecycle_contracts,
            "cumulative_category_repair_penalty": cumulative_penalty,
        }

    def _flash_pricing_binding(self, target: Mapping[str, Any] | None = None) -> dict[str, Any]:
        profile = _load_json(self._pricing_profile_path)
        pro = target is not None and target.get("tier") == "pro"
        low = profile.get("high_cost" if pro else "low_cost")
        if (
            profile.get("schema_version")
            != "model_evaluation-model_evaluation-card-distiller-flash-to-pro-public-profile-v1"
            or not isinstance(low, Mapping)
            or low.get("model_id") != ("deepseek-v4-pro" if pro else "deepseek-v4-flash")
            or float(low.get("budget_cap_cny", -1)) <= 0
            or (not pro and low.get("request_max_output_tokens") != MAX_OUTPUT_TOKENS)
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_PRICING_PROFILE_INVALID")
        return {
            "snapshot_date": profile.get("profile_snapshot_date"),
            "sha256": _file_sha256(self._pricing_profile_path),
            "budget_cap_cny": float(low["budget_cap_cny"]),
        }

    @staticmethod
    def _profile_kind(target: Mapping[str, Any]) -> str | None:
        from memorive.models.cli.templates import is_custom,exam_eligible
        if is_custom(target):
            return "CLI" if exam_eligible(target) else None
        kind = target.get("kind")
        if kind == "API":
            if (
                re.fullmatch(
                    r"deepseek(?:apikey[a-z0-9]*)?",
                    _compact(target.get("provider")),
                )
                and (target.get("model_name"), target.get("tier")) in {("deepseek-v4-flash", "flash"), ("deepseek-flash", "flash"), ("deepseek-v4-pro", "pro"), ("deepseek-pro", "pro")}
                and target.get("thinking") is True
                and target.get("connection_status") == "AVAILABLE"
                and isinstance(target.get("config_id"), str)
                and bool(target.get("config_id"))
            ):
                return "API"
            return None
        if kind == "CLI":
            if (
                _compact(target.get("adapter_id")) == "codexcli"
                and target.get("model_name") == "gpt-5.6-sol"
                and target.get("thinking_mode") == "high"
                and target.get("connection_status") == "AVAILABLE"
                and isinstance(target.get("config_id"), str)
                and bool(target.get("config_id"))
            ):
                return "CLI"
        if kind == "LOCAL":
            if _ollama_exam_profile(
                target, "structured_chat_adapter", "EvaluationAssets_LOCAL_STRUCTURED_CHAT_V1"
            ):
                return "LOCAL"
        return None

    def plan(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        node_id = node.get("node_id")
        if node_id != "transport_review":
            return _not_available("WORKFLOW_MODEL_EXAM_DYNAMIC_NODE_UNSUPPORTED")
        profile_kind = self._profile_kind(target)
        if profile_kind is None:
            return _not_available("WORKFLOW_MODEL_EXAM_DYNAMIC_PROFILE_UNSUPPORTED")
        try:
            modules = self._quality()
        except (ImportError, OSError, ValueError, RuntimeError) as exc:
            return self._quality_asset_failure("MODULE_IMPORT_FAILED", exc)
        try:
            verification = modules["pack"].verify_reference_pack(
                self._reference_pack_root
            )
        except (ImportError, OSError, ValueError, RuntimeError) as exc:
            return self._quality_asset_failure("PACK_VERIFY_FAILED", exc)
        try:
            pack = modules["reference"].load_card_reviewer_reference_exam_pack(
                self._reference_pack_root
            )
            manifest = dict(pack.manifest)
            scoring_protocol_path = pack.member(
                "scoring/scoring_protocol.json"
            )
            scoring_protocol = _load_json(scoring_protocol_path)
        except (ImportError, OSError, ValueError, RuntimeError) as exc:
            return self._quality_asset_failure("PACK_LOAD_FAILED", exc)
        try:
            pricing = (
                self._flash_pricing_binding(target)
                if profile_kind == "API"
                else None
            )
        except (ImportError, OSError, ValueError, RuntimeError) as exc:
            return self._quality_asset_failure("PRICING_LOAD_FAILED", exc)
        if (
            verification.get("status") != "PASS"
            or verification.get("reference_regression_only") is not True
            or verification.get("qualification_eligible") is not False
            or manifest.get("classification") != "REFERENCE_EXAM_PACK"
        ):
            return _not_available("WORKFLOW_MODEL_EXAM_Quality_PACK_NOT_ELIGIBLE")
        comparison_metadata = card_reviewer_comparison_metadata(
            reference_pack_id=str(manifest.get("pack_id") or ""),
            reference_pack_revision=str(
                manifest.get("pack_revision") or ""
            ),
            reference_pack_sha256=str(verification["pack_fingerprint"]),
            scoring_protocol_revision=str(
                scoring_protocol.get("schema_version") or ""
            ),
            scoring_protocol_sha256=_file_sha256(scoring_protocol_path),
            executor_ref=self.EXECUTOR_REF,
        )
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
            "thinking_mode": target.get("thinking_mode"),
            "settings_revision": context.get("settings_revision"),
            "settings_sha256": context.get("settings_sha256"),
            "reference_pack_id": manifest.get("pack_id"),
            "reference_pack_revision": manifest.get("pack_revision"),
            "reference_pack_sha256": verification["pack_fingerprint"],
            "comparison": comparison_metadata,
            **comparison_metadata,
            "reference_regression_only": True,
            "blind_holdout_eligible": False,
            "qualification_eligible": False,
            "forms": ["A", "B"],
            "maximum_model_calls": self._request_cap(profile_kind),
            "quality_semantic_call_limit": self.Quality_SEMANTIC_CALL_LIMIT,
            "case_chunk_limit": self.CASES_PER_CHUNK,
            "transient_retry_limit_per_chunk": self.TRANSIENT_RETRY_LIMIT,
            "budget_cap_cny": (
                pricing["budget_cap_cny"] if pricing is not None else None
            ),
            "cost_semantics": (
                "FROZEN_PROFILE_ESTIMATE_CAP"
                if profile_kind == "API"
                else (
                    ("CLI_MANAGED_UNKNOWN" if target.get("adapter_id")=="command_template" else "SUBSCRIPTION_CLI_NO_PER_CALL_PRICE")
                    if profile_kind == "CLI"
                    else "LOCAL_COMPUTE_NO_PROVIDER_API_COST"
                )
            ),
            "model_digest": target.get("model_digest"),
            "pricing_snapshot_date": (
                pricing["snapshot_date"] if pricing is not None else None
            ),
            "pricing_profile_sha256": (
                pricing["sha256"] if pricing is not None else None
            ),
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
        """A command-line plan acknowledges a subscription without per-call price."""
        return _plan_authorization_reason(authorization, plan, subscription=plan.get("profile_kind") == "CLI")

    @staticmethod
    def _validate_call_result(
        result: Mapping[str, Any],
        *,
        requested_model: str,
        profile_kind: str,
    ) -> Mapping[str, Any]:
        is_api = profile_kind == "API"
        is_cli = profile_kind == "CLI"
        expected = {
            "provider_calls": 1 if is_api else 0,
            "external_network_calls": 1 if is_api else 0,
            "external_process_launches": 1 if is_cli else 0,
        }
        if (
            result.get("schema_version")
            != "SettingsStructuredChatRunnerResult-v1"
            or result.get("status") != "PASS"
            or result.get("requested_model") != requested_model
            or not matches_result_model(result, requested_model)
            or not isinstance(result.get("response"), Mapping)
            or result.get("external_model_calls") != 1
            or any(result.get(key) != value for key, value in expected.items())
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
            or any(
                not isinstance(receipt.get(field), str) or not receipt.get(field)
                for field in ("route", "region", "egress", "behavior_sha256")
            )
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
        if is_api:
            actual = receipt.get("actual_cost")
            estimate = receipt.get("estimated_cost_cny")
            if actual is None and (
                isinstance(estimate, bool) or not isinstance(estimate, (int, float))
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_COST_EVIDENCE_MISSING")
        elif is_cli and (
            receipt.get("actual_cost") is not None
            or receipt.get("estimated_cost_cny") is not None
            or receipt.get("cost_evidence")
            != "SUBSCRIPTION_CLI_NO_PER_CALL_PRICE"
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_CLI_COST_EVIDENCE_INVALID")
        elif not is_api and not is_cli and (
            receipt.get("actual_cost") is not None
            or receipt.get("estimated_cost_cny") != 0.0
            or receipt.get("cost_evidence")
            != "LOCAL_COMPUTE_NO_PROVIDER_API_COST"
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_LOCAL_COST_EVIDENCE_INVALID")
        return receipt

    def _provider_call(
        self,
        *,
        run_id: str,
        run_root: Path,
        state: dict[str, Any],
        target: Mapping[str, Any],
        request: Mapping[str, Any],
        response_schema: Mapping[str, Any],
        structured_chat: Callable[..., Mapping[str, Any]],
        call_kind: str,
    ) -> dict[str, Any]:
        profile_kind = str(target["kind"])
        if state["request_attempts"] >= self._request_cap(profile_kind):
            raise ValueError("WORKFLOW_MODEL_EXAM_CALL_LIMIT_REACHED")
        call_number = state["request_attempts"] + 1
        call_id = f"call-{call_number:02d}"
        model_name = str(target["model_name"])
        subject_request = self._subject_request_projection(target, request)
        prompt = json.dumps(
            subject_request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest().upper()
        prompt_metrics = _effective_structured_prompt_metrics(
            prompt,
            response_schema,
        )
        model_binding, request_max_output_tokens, output_limit_mode = (
            self._structured_output_policy(target)
        )
        is_api = profile_kind == "API"
        is_local = profile_kind == "LOCAL"
        limits = resolve_call_limits(profile_kind=profile_kind, service=target, model=target)
        if output_limit_mode == "PROVIDER_DEFAULT":
            limits["max_output_tokens"] = None
            limits["wire_output_policy"] = "PROVIDER_DEFAULT"
        worst_case_reserve_cny = (
            _api_worst_case_request_reserve_cny(
                target, prompt_metrics, request_max_output_tokens
            )
            if is_api
            else None
        )
        if worst_case_reserve_cny is not None:
            cost_cap = target.get("exam_budget_cap_cny")
            if (
                isinstance(cost_cap, bool)
                or not isinstance(cost_cap, (int, float))
                or float(cost_cap)
                < float(state["estimated_cost_cny"]) + worst_case_reserve_cny
            ):
                raise ValueError(
                    "WORKFLOW_MODEL_EXAM_PER_CALL_WORST_CASE_RESERVE_NOT_COVERED"
                )
        claim = {
            "schema_version": "WorkflowModelExamPreSendClaim-v1",
            "run_id": run_id,
            "call_id": call_id,
            "call_kind": call_kind,
            "profile_kind": profile_kind,
            "profile_ref": target["config_id"],
            "requested_model": model_name,
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
            "request_max_output_tokens": limits["max_output_tokens"],
            "effective_prompt_metrics": prompt_metrics,
            "pricing_profile_sha256": (
                (
                    target.get("pricing_profile_sha256")
                    or _file_sha256(self._pricing_profile_path)
                )
                if is_api
                else None
            ),
            "estimated_cost_cny_before_send": (
                round(float(state["estimated_cost_cny"]), 9)
                if is_api
                else None
            ),
            "per_call_worst_case_reserve_cny": worst_case_reserve_cny,
            "cost_cap_cny": target.get("exam_budget_cap_cny") if is_api else None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        claim_receipt = _write_json_create_only(
            run_root / "claims" / f"{call_id}.json", claim
        )
        state["request_attempts"] += 1
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
                    "profile_kind": profile_kind,
                    "purpose": "workflow_model_exam",
                    "requested_model": model_name,
                    "returned_model": result.get("returned_model"),
                    "token_usage": None,
                    "actual_cost": None,
                    "estimated_cost_cny": None,
                    "cost_evidence": "ATTEMPT_EVIDENCE_UNAVAILABLE",
                }
            _write_json_create_only(
                run_root / "receipts" / f"{call_id}.json",
                {
                    **deepcopy(dict(receipt)),
                    "run_id": run_id,
                    "call_id": call_id,
                    "call_kind": call_kind,
                    "profile_ref": target["config_id"],
                    "pre_send_claim_sha256": claim_receipt["sha256"],
                    "provider_output_sha256": None,
                },
            )
            for field in (
                "provider_calls",
                "external_model_calls",
                "external_network_calls",
                "external_process_launches",
            ):
                state[field] += int(result.get(field) or 0)
            estimate = receipt.get("estimated_cost_cny")
            if isinstance(estimate, (int, float)) and not isinstance(estimate, bool):
                state["estimated_cost_cny"] += float(estimate)
            usage = receipt.get("token_usage")
            if isinstance(usage, Mapping):
                for field in ("prompt_tokens", "completion_tokens"):
                    value = usage.get(field)
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                        state["token_usage"][field] = state["token_usage"][field] + value if state["token_usage"][field] is not None else None
            reason = result.get("reason")
            raise ValueError(
                reason if isinstance(reason, str) and reason else "WORKFLOW_MODEL_EXAM_CALL_FAILED"
            )
        receipt = self._validate_call_result(
            result,
            requested_model=model_name,
            profile_kind=profile_kind,
        )
        response = deepcopy(dict(result["response"]))
        output_receipt = _write_json_create_only(
            run_root / "provider_outputs" / f"{call_id}.json", response
        )
        _write_json_create_only(
            run_root / "receipts" / f"{call_id}.json",
            {
                **deepcopy(dict(receipt)),
                "run_id": run_id,
                "call_id": call_id,
                "call_kind": call_kind,
                "profile_ref": target["config_id"],
                "pre_send_claim_sha256": claim_receipt["sha256"],
                "provider_output_sha256": output_receipt["sha256"],
            },
        )
        for field in (
            "provider_calls",
            "external_model_calls",
            "external_network_calls",
            "external_process_launches",
        ):
            state[field] += int(result[field])
        estimate = receipt.get("estimated_cost_cny")
        if isinstance(estimate, (int, float)) and not isinstance(estimate, bool):
            state["estimated_cost_cny"] += float(estimate)
        from memorive.models.cli.templates import accumulate_usage
        accumulate_usage(state,receipt.get("token_usage"))
        state["returned_model"]=receipt.get("returned_model")
        return response

    def _stable_provider_call(
        self,
        *,
        run_id: str,
        run_root: Path,
        state: dict[str, Any],
        target: Mapping[str, Any],
        request: Mapping[str, Any],
        response_schema: Mapping[str, Any],
        structured_chat: Callable[..., Mapping[str, Any]],
        call_kind: str,
    ) -> dict[str, Any]:
        retryable = {
            "API_NETWORK_OR_RESPONSE_FAILURE",
            "API_RATE_LIMITED",
            "API_STRUCTURED_CHAT_REQUEST_FAILED",
            "STRUCTURED_CHAT_RESPONSE_EMPTY",
            "STRUCTURED_CHAT_RESPONSE_INVALID",
            "CLI_STRUCTURED_CHAT_TIMEOUT",
        }
        last_error: ValueError | None = None
        for retry_index in range(self.TRANSIENT_RETRY_LIMIT + 1):
            effective_kind = (
                call_kind
                if retry_index == 0
                else f"{call_kind}_TRANSPORT_RETRY_{retry_index}"
            )
            try:
                return self._provider_call(
                    run_id=run_id,
                    run_root=run_root,
                    state=state,
                    target=target,
                    request=request,
                    response_schema=response_schema,
                    structured_chat=structured_chat,
                    call_kind=effective_kind,
                )
            except ValueError as error:
                last_error = error
                if str(error) not in retryable:
                    raise
                if state["request_attempts"] >= self._request_cap(str(target["kind"])):
                    raise
        if last_error is not None:
            raise last_error
        raise ValueError("WORKFLOW_MODEL_EXAM_CALL_FAILED")

    def _chunked_provider_call(
        self,
        *,
        run_id: str,
        run_root: Path,
        state: dict[str, Any],
        target: Mapping[str, Any],
        request: Mapping[str, Any],
        response_schema: Mapping[str, Any],
        structured_chat: Callable[..., Mapping[str, Any]],
        call_kind: str,
    ) -> dict[str, Any]:
        form = request.get("form")
        cases = form.get("cases") if isinstance(form, Mapping) else None
        if not isinstance(cases, list) or len(cases) <= self.CASES_PER_CHUNK:
            return self._stable_provider_call(
                run_id=run_id,
                run_root=run_root,
                state=state,
                target=target,
                request=request,
                response_schema=response_schema,
                structured_chat=structured_chat,
                call_kind=call_kind,
            )
        chunks = [
            cases[index : index + self.CASES_PER_CHUNK]
            for index in range(0, len(cases), self.CASES_PER_CHUNK)
        ]
        merged_reviews: list[Any] = []
        parent_hash = str(request.get("request_payload_hash") or _sha256(request))
        for chunk_index, chunk in enumerate(chunks, start=1):
            chunk_request = deepcopy(dict(request))
            chunk_form = deepcopy(dict(form))
            chunk_form["cases"] = deepcopy(chunk)
            case_ids = [
                row.get("case_id")
                for row in chunk
                if isinstance(row, Mapping) and isinstance(row.get("case_id"), str)
            ]
            if len(case_ids) != len(chunk) or len(set(case_ids)) != len(case_ids):
                raise ValueError("WORKFLOW_MODEL_EXAM_CHUNK_CASE_SET_INVALID")
            chunk_request["form"] = chunk_form
            chunk_request["chunk_contract"] = {
                "schema_version": "WorkflowModelExamChunkContract-v1",
                "parent_request_payload_hash": parent_hash,
                "chunk_index": chunk_index,
                "chunk_count": len(chunks),
                "case_ids": case_ids,
                "case_ids_sha256": _sha256(case_ids),
            }
            chunk_request.pop("request_payload_hash", None)
            chunk_request["request_payload_hash"] = _sha256(chunk_request)
            response = self._stable_provider_call(
                run_id=run_id,
                run_root=run_root,
                state=state,
                target=target,
                request=chunk_request,
                response_schema=response_schema,
                structured_chat=structured_chat,
                call_kind=f"{call_kind}_CHUNK_{chunk_index}_OF_{len(chunks)}",
            )
            reviews = response.get("reviews")
            if isinstance(reviews, list):
                merged_reviews.extend(deepcopy(reviews))
        return {"reviews": merged_reviews}

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
        run = _CardReviewerExamRun(
            target=target, context=context, structured_chat=structured_chat, plan=plan,
            requested_model=requested_model, started=started, run_id=run_id, run_root=run_root,
            state={
            "request_attempts": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
            "external_network_calls": 0,
            "external_process_launches": 0,
            "estimated_cost_cny": 0.0,
            "token_usage": {"prompt_tokens": 0, "completion_tokens": 0},
        },
        )
        try:
            self._load_card_reviewer_inputs(run)
            run.scores = {}
            run.responses = {}
            for form_id in ("A", "B"):
                call_index = {"value": 0}

                def subject_call(
                    request: Mapping[str, Any],
                    *,
                    current_form: str = form_id,
                    index: dict[str, int] = call_index,
                ) -> dict[str, Any]:
                    index["value"] += 1
                    response_schema = request.get("output_schema")
                    if not isinstance(response_schema, Mapping):
                        raise ValueError("WORKFLOW_MODEL_EXAM_RESPONSE_SCHEMA_MISSING")
                    response = self._chunked_provider_call(
                        run_id=run.run_id,
                        run_root=run.run_root,
                        state=run.state,
                        target=run.target,
                        request=request,
                        response_schema=response_schema,
                        structured_chat=run.structured_chat,
                        call_kind=(
                            f"FORM_{current_form}_INITIAL"
                            if index["value"] == 1
                            else f"FORM_{current_form}_STRUCTURAL_REPAIR"
                        ),
                    )
                    return {"status": "COMPLETED", "response": response}

                result = self._execute_form_protocol(
                    protocol=run.protocol,
                    exam=run.exam,
                    form=run.forms[form_id],
                    schema=run.form_schemas[form_id],
                    prompt_contract=run.prompt_contract,
                    scoring_protocol=run.scoring_protocol,
                    subject_call=subject_call,
                    gold=run.golds[form_id],
                )
                run.scores[form_id] = deepcopy(dict(result))
                final_response = result.get("final_response")
                if isinstance(final_response, Mapping):
                    run.responses[form_id] = deepcopy(dict(final_response))

            run.initial_form_scores = {
                form_id: (
                    round(float(run.scores[form_id]["score"]), 6)
                    if isinstance(run.scores[form_id].get("score"), (int, float))
                    and not isinstance(run.scores[form_id].get("score"), bool)
                    else None
                )
                for form_id in ("A", "B")
            }
            run.pointer_normalization_actions = [
                {
                    "form_id": form_id,
                    **deepcopy(dict(action)),
                }
                for form_id in ("A", "B")
                for action in run.scores[form_id].get(
                    "pointer_normalizations", []
                )
                if isinstance(action, Mapping)
            ]
            run.repair_projection = None
            run.repair_prompt_projection = None
            run.qualification_verdict = "DISQUALIFIED"
            run.operational_eligibility = "NOT_RECOMMENDED"
            run.operational_safety_gate = "FAIL"
            run.safety_blocking_residual_count = None
            run.calibration_residual_count = None
            run.scorecard = None
            run.comparison_projection = deepcopy(run.comparison_metadata)

            self._repair_card_reviewer_forms(run)

            return self._publish_card_reviewer_exam(run)
        except Exception as error:
            reason = str(error) or type(error).__name__
            if len(reason) > 200:
                reason = type(error).__name__
            failure = {
                "schema_version": "WorkflowModelExamRunFailure-v1",
                "status": "NOT_ASSESSED",
                "reason": reason,
                "run_id": run.run_id,
                "plan_sha256": run.plan["plan_sha256"],
                "provider_calls": run.state["provider_calls"],
                "request_attempts": run.state["request_attempts"],
                "external_model_calls": run.state["external_model_calls"],
                "external_network_calls": run.state["external_network_calls"],
                "external_process_launches": run.state[
                    "external_process_launches"
                ],
                "token_usage": run.state["token_usage"],
                "estimated_cost_cny": (
                    round(run.state["estimated_cost_cny"], 9)
                    if run.plan["profile_kind"] == "API"
                    else (0.0 if run.plan["profile_kind"] == "LOCAL" else None)
                ),
                "score": None,
                "execution_outcome": classify_execution_outcome(
                    {"status": "ERROR", "reason": reason}
                ).value,
                "scorability": Scorability.NOT_ASSESSED.value,
            }
            evidence = _write_json_create_only(
                run.run_root / "exam_failure.json", failure
            )
            result = _not_run(reason, requested_model=run.requested_model)
            result.update(
                duration_ms=max(0, int((monotonic() - run.started) * 1000)),
                provider_calls=run.state["provider_calls"],
                request_attempts=run.state["request_attempts"],
                external_model_calls=run.state["external_model_calls"],
                external_network_calls=run.state["external_network_calls"],
                external_process_launches=run.state[
                    "external_process_launches"
                ],
                exam_run_id=run.run_id,
                exam_run_root=str(run.run_root.resolve()),
                exam_evidence_sha256=evidence["sha256"],
                token_usage=run.state["token_usage"],
                estimated_cost_cny=failure["estimated_cost_cny"],
                cost_semantics=run.plan["cost_semantics"],
                execution_outcome=failure["execution_outcome"],
                scorability=failure["scorability"],
            )
            return result

    def _load_card_reviewer_inputs(self, run: _CardReviewerExamRun) -> None:
        """The reference pack, its schema, prompt contract and scoring protocol, the comparison metadata,
        and the forms, golds and form schemas of this run."""
        modules = self._quality()
        run.protocol = modules["protocol"]
        run.exam = modules["exam"]
        pack = modules["reference"].load_card_reviewer_reference_exam_pack(
            self._reference_pack_root
        )
        run.schema = _load_json(
            pack.member("schemas/card_reviewer_output.schema.json")
        )
        run.prompt_contract = _load_json(
            pack.member("prompts/card_reviewer_prompt.json")
        )
        run.scoring_protocol = _load_json(
            pack.member("scoring/scoring_protocol.json")
        )
        planned_comparison = run.plan.get("comparison")
        run.comparison_metadata = (
            deepcopy(dict(planned_comparison))
            if isinstance(planned_comparison, Mapping)
            else card_reviewer_comparison_metadata(
                reference_pack_id=str(run.plan["reference_pack_id"]),
                reference_pack_revision=str(
                    run.plan["reference_pack_revision"]
                ),
                reference_pack_sha256=str(run.plan["reference_pack_sha256"]),
                scoring_protocol_revision=str(
                    run.scoring_protocol["schema_version"]
                ),
                scoring_protocol_sha256=_file_sha256(
                    pack.member("scoring/scoring_protocol.json")
                ),
                executor_ref=self.EXECUTOR_REF,
            )
        )
        run.exam_inputs = self._exam_inputs(
            pack=pack,
            schema=run.schema,
            context=run.context,
        )
        run.forms = run.exam_inputs["forms"]
        run.golds = run.exam_inputs["golds"]
        run.form_schemas = run.exam_inputs["schemas"]

    def _repair_card_reviewer_forms(self, run: _CardReviewerExamRun) -> None:
        """Run the repair round on the scored forms (or record why there is none) and fix the verdict, the
        scorecard, the residual counts and the comparison."""
        if not all(run.scores[form_id].get("status") == "SCORED" for form_id in ("A", "B")):
            raise ValueError(
                "CARD_REVIEWER_STRUCTURAL_SCORING_NOT_SCOREABLE"
            )
            run.score = 0.0
            run.score_semantics = "STRUCTURAL_CONTRACT_FAILURE_ZERO"
            run.verdict = "FAIL"
            run.failed_exam_item_count = sum(
                len(run.exam.form_cases(run.forms[form_id])[1])
                for form_id in ("A", "B")
            )
            run.role_ability_form_scores = {"A": 0.0, "B": 0.0}
            run.repair_outcome_form_scores = {"A": 0.0, "B": 0.0}
            run.repair_outcome_score = 0.0
            run.repair_completion_rate = 0.0
            hard_gates = {"A": "FAIL", "B": "FAIL"}
            run.repair_rounds = 0
            run.comparison_projection["horizontal_comparison_eligible"] = False
            run.comparison_projection["noncomparison_reason"] = (
                "STRUCTURAL_SCORING_FAILURE"
            )
            run.repair_projection = {
                "schema_version": "DesktopCardReviewerRoleProjection-v5",
                "status": "STRUCTURAL_SCORING_FAILURE",
                "qualification_verdict": run.qualification_verdict,
                "quality_exact_repair_qualification_verdict": (
                    run.qualification_verdict
                ),
                "operational_eligibility_verdict": (
                    run.operational_eligibility
                ),
                "operational_safety_gate": run.operational_safety_gate,
                "operational_policy_revision": (
                    CARD_REVIEWER_OPERATIONAL_POLICY_REVISION
                ),
                "core_lifecycle_revision": (
                    CORE_CARD_REVIEWER_LIFECYCLE_REVISION
                ),
                "core_closure_verdict": "HUMAN_REQUIRED",
                "core_closure_score": 0.0,
                "semantic_repair_rounds": 0,
                "second_semantic_repair_allowed": (
                    self.SEMANTIC_REPAIR_ROUND_LIMIT >= 2
                ),
                "role_ability_score": 0.0,
                "score_semantics": run.score_semantics,
                "role_ability_form_scores": deepcopy(
                    run.role_ability_form_scores
                ),
                "reviewer_repair_outcome_score": 0.0,
                "repair_completion_rate": 0.0,
                "repair_completion_rate_semantics": (
                    "ORIGINAL_WRONG_CASE_REACHES_EXACT_SCORE_100_"
                    "WITH_NEW_WRONGS_COUNTED"
                ),
                "exact_case_repair_rate": 0.0,
                "case_score_deficit_recovery_rate": 0.0,
                "repair_metadata_collision_suspect_count": 0,
                "collision_adjusted_repair_diagnostic_rate": 0.0,
                "collision_adjusted_repair_diagnostic_only": True,
                "repair_outcome_form_scores": deepcopy(
                    run.repair_outcome_form_scores
                ),
                "structural_contract_failure_final_score": 0.0,
                "numeric_score_is_not_qualification_verdict": True,
                "cross_category_coefficients_reused": False,
            }
        else:
            wrong_count = sum(
                1
                for form_id in ("A", "B")
                for detail in run.scores[form_id].get("details", [])
                if isinstance(detail, Mapping)
                and isinstance(detail.get("score"), (int, float))
                and not isinstance(detail.get("score"), bool)
                and float(detail["score"]) < 100.0
            )
            run.repair_rounds = 0
            if wrong_count:
                repair_cycle = self._execute_semantic_repair_rounds(
                    run_id=run.run_id,
                    run_root=run.run_root,
                    state=run.state,
                    target=run.target,
                    structured_chat=run.structured_chat,
                    exam=run.exam,
                    forms=run.forms,
                    golds=run.golds,
                    form_schemas=run.form_schemas,
                    schema=run.schema,
                    scoring_protocol=run.scoring_protocol,
                    prompt_contract=run.prompt_contract,
                    original_scores=run.scores,
                    original_responses=run.responses,
                )
                repaired = repair_cycle["projection"]
                run.repair_rounds = int(repair_cycle["repair_rounds"])
                run.repair_projection = deepcopy(dict(repaired))
                run.repair_prompt_projection = deepcopy(
                    dict(repair_cycle["repair_prompt_projection"])
                )
                run.responses = {
                    key: deepcopy(dict(value))
                    for key, value in repair_cycle["responses"].items()
                }
                run.score = float(repaired["role_ability_score"])
                run.score_semantics = str(repaired["score_semantics"])
                run.repair_outcome_score = float(
                    repaired["reviewer_repair_outcome_score"]
                )
                run.repair_completion_rate = float(
                    repaired["repair_completion_rate"]
                )
                run.failed_exam_item_count = int(
                    repaired["failed_exam_item_count"]
                )
                run.role_ability_form_scores = dict(
                    repaired["role_ability_form_scores"]
                )
                run.repair_outcome_form_scores = dict(
                    repaired["repair_outcome_form_scores"]
                )
                hard_gates = {
                    form_id: repaired["forms"][form_id][
                        "hard_gate_verdict"
                    ]
                    for form_id in ("A", "B")
                }
                run.qualification_verdict = str(
                    repaired["qualification_verdict"]
                )
                run.operational_eligibility = str(
                    repaired["operational_eligibility_verdict"]
                )
                run.operational_safety_gate = str(
                    repaired["operational_safety_gate"]
                )
                run.safety_blocking_residual_count = int(
                    repaired["safety_blocking_residual_count"]
                )
                run.calibration_residual_count = int(
                    repaired["calibration_residual_count"]
                )
            else:
                run.score = (
                    float(run.scores["A"]["score"])
                    + float(run.scores["B"]["score"])
                ) / 2.0
                run.score_semantics = CARD_REVIEWER_SCORE_SEMANTICS
                run.repair_outcome_score = run.score
                run.repair_completion_rate = 1.0
                run.failed_exam_item_count = 0
                run.role_ability_form_scores = {
                    form_id: float(run.scores[form_id]["score"])
                    for form_id in ("A", "B")
                }
                run.repair_outcome_form_scores = deepcopy(
                    run.role_ability_form_scores
                )
                hard_gates = {
                    form_id: str(run.scores[form_id]["hard_gate_verdict"])
                    for form_id in ("A", "B")
                }
                run.qualification_verdict = (
                    "PASS"
                    if all(value == "PASS" for value in hard_gates.values())
                    else "DISQUALIFIED"
                )
                run.operational_safety_gate = (
                    "PASS"
                    if run.qualification_verdict == "PASS"
                    else "FAIL"
                )
                run.operational_eligibility = (
                    "SUITABLE"
                    if run.operational_safety_gate == "PASS"
                    and run.score >= CARD_REVIEWER_OPERATIONAL_PASSING_SCORE
                    else (
                        "REJECT"
                        if run.score < 60.0
                        else "NOT_RECOMMENDED"
                    )
                )
                run.safety_blocking_residual_count = 0
                run.calibration_residual_count = 0
                run.repair_projection = {
                    "schema_version": "DesktopCardReviewerRoleProjection-v5",
                    "status": "DIRECTED_REPAIR_NOT_REQUIRED",
                    "qualification_verdict": run.qualification_verdict,
                    "quality_exact_repair_qualification_verdict": (
                        run.qualification_verdict
                    ),
                    "operational_eligibility_verdict": (
                        run.operational_eligibility
                    ),
                    "operational_safety_gate": run.operational_safety_gate,
                    "operational_policy_revision": (
                        CARD_REVIEWER_OPERATIONAL_POLICY_REVISION
                    ),
                    "core_lifecycle_revision": (
                        CORE_CARD_REVIEWER_LIFECYCLE_REVISION
                    ),
                    "core_closure_verdict": "DELTA_PASS",
                    "core_closure_score": round(run.score, 6),
                    "semantic_repair_rounds": 0,
                    "second_semantic_repair_allowed": (
                        self.SEMANTIC_REPAIR_ROUND_LIMIT >= 2
                    ),
                    "governed_disposition_case_count": 0,
                    "operational_passing_score": (
                        CARD_REVIEWER_OPERATIONAL_PASSING_SCORE
                    ),
                    "safety_blocking_residual_count": 0,
                    "safety_blocking_case_ids": [],
                    "calibration_residual_count": 0,
                    "calibration_case_ids": [],
                    "operational_residual_diagnostics": {"A": [], "B": []},
                    "role_ability_score": round(run.score, 6),
                    "score_semantics": run.score_semantics,
                    "initial_nonlinear_score": round(run.score, 6),
                    "post_repair_nonlinear_score": round(run.score, 6),
                    "reviewer_repair_outcome_score": round(run.score, 6),
                    "repair_completion_rate": 1.0,
                    "repair_completion_rate_semantics": (
                        "ORIGINAL_WRONG_CASE_REACHES_EXACT_SCORE_100_"
                        "WITH_NEW_WRONGS_COUNTED"
                    ),
                    "exact_case_repair_rate": 1.0,
                    "case_score_deficit_recovery_rate": 1.0,
                    "repair_metadata_collision_suspect_count": 0,
                    "repair_metadata_collision_suspect_case_ids": [],
                    "collision_adjusted_repair_diagnostic_rate": 1.0,
                    "collision_adjusted_repair_diagnostic_only": True,
                    "collision_adjusted_repair_qualification_effect": (
                        "NONE"
                    ),
                    "reviewer_repair_burden": {
                        "initial_burden_total_mean": 0.0,
                    },
                    "role_ability_form_scores": deepcopy(
                        run.role_ability_form_scores
                    ),
                    "repair_outcome_form_scores": deepcopy(
                        run.repair_outcome_form_scores
                    ),
                    "failed_exam_item_count": 0,
                    "scoring_axes": {
                        "ability": (
                            "Quality_CARD_REVIEWER_NONLINEAR_PIECEWISE"
                        ),
                        "repair_outcome": (
                            "Quality_CARD_REVIEWER_R1_1"
                        ),
                        "qualification": (
                            "Quality_CARD_REVIEWER_R1_1_"
                            "DISQUALIFY_ON_RESIDUAL"
                        ),
                        "operational_eligibility": (
                            CARD_REVIEWER_OPERATIONAL_POLICY_REVISION
                        ),
                        "comparison": (
                            "EXACT_SAME_CATEGORY_ROLE_PACK_SCORER_"
                            "PROJECTION_ONLY"
                        ),
                    },
                    "numeric_score_is_not_qualification_verdict": True,
                    "cross_category_coefficients_reused": False,
                    "global_ranking_forbidden": True,
                }
            run.scorecard = run.exam.publish_dual_form_result(
                provider=str(run.target.get("provider") or run.target.get("adapter_id")),
                model=str(run.target["model_name"]),
                parameters={
                    "profile_kind": run.target["kind"],
                    "thinking": run.target.get("thinking"),
                    "thinking_mode": run.target.get("thinking_mode"),
                    "maximum_model_calls": self._request_cap(
                        str(run.target["kind"])
                    ),
                    "quality_semantic_call_limit": self.Quality_SEMANTIC_CALL_LIMIT,
                    "case_chunk_limit": self.CASES_PER_CHUNK,
                },
                forms={
                    form_id: {
                        "status": "SCORED",
                        "score": run.role_ability_form_scores[form_id],
                        "hard_gate_verdict": hard_gates[form_id],
                    }
                    for form_id in ("A", "B")
                },
                evidence_refs=[
                    f"responses/form_{form_id}_final.json"
                    for form_id in ("A", "B")
                ],
            )
            run.verdict = (
                "PASS"
                if run.operational_eligibility
                in {
                    "SUITABLE",
                    "SUITABLE_WITH_CALIBRATION",
                    "SUITABLE_WITH_GOVERNED_DISPOSITION",
                }
                and run.scorecard["recommendation_code"]
                == "SUITABLE_AS_CARD_REVIEWER"
                else "FAIL"
            )

    def _publish_card_reviewer_exam(self, run: _CardReviewerExamRun) -> Any:
        """Decide the operational outcome from the verdict and the blocking failures, check the call and
        cost caps, write the evidence and the public result, and return the result."""
        raw_quality_verdict = run.verdict
        blocking_failures: list[dict[str, Any]] = []
        operational_rows = run.repair_projection.get(
            "operational_residual_diagnostics", {}
        )
        if isinstance(operational_rows, Mapping):
            for form_id in ("A", "B"):
                rows = operational_rows.get(form_id, [])
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    if not isinstance(row, Mapping):
                        continue
                    failed_fields = sorted(
                        {
                            str(value)
                            for key in (
                                "safety_reasons",
                                "calibration_reasons",
                                "failed_output_fields",
                            )
                            for value in (row.get(key) or [])
                            if isinstance(value, str) and value
                        }
                    )
                    if not failed_fields:
                        failed_fields = [
                            str(row.get("risk_class") or "ROLE_RESIDUAL")
                        ]
                    blocking_failures.append(
                        {
                            "case_id": str(
                                row.get("case_id")
                                or f"{form_id}-UNKNOWN"
                            ),
                            "failed_fields": failed_fields,
                            "evidence_anchor": (
                                f"scoring_projection/forms/{form_id}/"
                                f"{row.get('case_id')}"
                            ),
                            "blocking": True,
                        }
                    )
        if raw_quality_verdict == "FAIL" and not blocking_failures:
            blocking_failures.append(
                {
                    "case_id": "CARD_REVIEWER_AGGREGATE",
                    "failed_fields": [
                        "operational_eligibility_or_category_score"
                    ],
                    "evidence_anchor": "scoring_projection/aggregate",
                    "blocking": True,
                }
            )
        quality_fail_admission = None
        if raw_quality_verdict == "FAIL":
            quality_fail_admission = admit_quality_fail(
                execution_outcome=ExecutionOutcome.COMPLETED,
                scorability=Scorability.SCOREABLE,
                repair_rounds_used=run.repair_rounds,
                repair_round_limit=self.SEMANTIC_REPAIR_ROUND_LIMIT,
                repair_cycle_closed=(
                    run.repair_rounds == self.SEMANTIC_REPAIR_ROUND_LIMIT
                    or run.failed_exam_item_count == 0
                ),
                blocking_failures=blocking_failures,
                exact_hashes={
                    "reference_pack_sha256": str(
                        run.plan["reference_pack_sha256"]
                    ),
                    "scoring_protocol_sha256": str(
                        run.comparison_projection["scoring_protocol_sha256"]
                    ),
                    "executor_sha256": _file_sha256(Path(__file__)),
                    "input_exact_set_sha256": _sha256(
                        {"forms": run.forms, "schemas": run.form_schemas}
                    ),
                },
            )
            run.verdict = str(quality_fail_admission["quality_verdict"])

        exact_case_repair_rate = float(
            run.repair_projection.get(
                "exact_case_repair_rate", run.repair_completion_rate
            )
        )
        case_score_deficit_recovery_rate = float(
            run.repair_projection.get(
                "case_score_deficit_recovery_rate",
                exact_case_repair_rate,
            )
        )
        repair_metadata_collision_suspect_count = int(
            run.repair_projection.get(
                "repair_metadata_collision_suspect_count", 0
            )
        )
        collision_adjusted_repair_diagnostic_rate = float(
            run.repair_projection.get(
                "collision_adjusted_repair_diagnostic_rate",
                exact_case_repair_rate,
            )
        )

        if run.state["request_attempts"] > self._request_cap(str(run.target["kind"])):
            raise ValueError("WORKFLOW_MODEL_EXAM_CALL_LIMIT_EXCEEDED")
        cap = run.plan.get("budget_cap_cny")
        if cap is not None and run.state["estimated_cost_cny"] > float(cap):
            raise ValueError("WORKFLOW_MODEL_EXAM_COST_CAP_EXCEEDED")
        for form_id, response in run.responses.items():
            _write_json_create_only(
                run.run_root / "responses" / f"form_{form_id}_final.json",
                response,
            )
        public_result = {
            "schema_version": "WorkflowModelExamRunResult-v1",
            "status": run.verdict,
            "reason": "Quality_REFERENCE_REGRESSION_COMPLETED",
            "run_id": run.run_id,
            "plan_sha256": run.plan["plan_sha256"],
            "executor_ref": self.EXECUTOR_REF,
            "executor_binding_sha256": _file_sha256(Path(__file__)),
            "requested_model": run.requested_model,
            "returned_model": run.state.get("returned_model") if run.target.get("adapter_id")=="command_template" else run.requested_model,
            "score": int(round(run.score)),
            "score_exact": round(run.score, 6),
            "execution_outcome": ExecutionOutcome.COMPLETED.value,
            "scorability": Scorability.SCOREABLE.value,
            "raw_quality_candidate_verdict": raw_quality_verdict,
            "quality_fail_admission": quality_fail_admission,
            "score_semantics": run.score_semantics,
            "form_scores": {
                key: round(float(value), 6)
                for key, value in run.role_ability_form_scores.items()
            },
            "role_ability_form_scores": {
                key: round(float(value), 6)
                for key, value in run.role_ability_form_scores.items()
            },
            "repair_outcome_form_scores": {
                key: round(float(value), 6)
                for key, value in run.repair_outcome_form_scores.items()
            },
            "initial_form_scores": run.initial_form_scores,
            "post_repair_nonlinear_form_scores": {
                form_id: round(
                    float(
                        run.repair_projection.get("forms", {})
                        .get(form_id, {})
                        .get(
                            "post_repair_nonlinear_score",
                            run.role_ability_form_scores[form_id],
                        )
                    ),
                    6,
                )
                for form_id in ("A", "B")
            },
            "qualification_verdict": run.qualification_verdict,
            "quality_exact_repair_qualification_verdict": (
                run.qualification_verdict
            ),
            "operational_eligibility_verdict": run.operational_eligibility,
            "operational_safety_gate": run.operational_safety_gate,
            "operational_policy_revision": (
                CARD_REVIEWER_OPERATIONAL_POLICY_REVISION
            ),
            "core_lifecycle_revision": CORE_CARD_REVIEWER_LIFECYCLE_REVISION,
            "core_closure_verdict": run.repair_projection.get(
                "core_closure_verdict",
                "DELTA_PASS" if run.failed_exam_item_count == 0 else "HUMAN_REQUIRED",
            ),
            "core_closure_score": run.repair_projection.get(
                "core_closure_score",
                run.repair_outcome_score,
            ),
            "governed_disposition_case_count": run.repair_projection.get(
                "governed_disposition_case_count",
                0,
            ),
            "safety_blocking_residual_count": (
                run.safety_blocking_residual_count
            ),
            "calibration_residual_count": run.calibration_residual_count,
            "repair_prompt_projection": (
                {
                    key: value
                    for key, value in run.repair_prompt_projection.items()
                    if key != "prompt"
                }
                if isinstance(run.repair_prompt_projection, Mapping)
                else None
            ),
            "repair_prompt_projection_revision": (
                CARD_REVIEWER_REPAIR_PROMPT_PROJECTION_REVISION
            ),
            "scorecard_recommendation_code": (
                run.scorecard.get("recommendation_code")
                if isinstance(run.scorecard, Mapping)
                else None
            ),
            "scoring_system_revision": (
                CARD_REVIEWER_SCORING_PROJECTION_REVISION
            ),
            "score_axes": {
                "role_ability_score": round(
                    float(
                        run.repair_projection.get(
                            "role_ability_score", run.score
                        )
                    ),
                    6,
                ),
                "post_repair_diagnostic_score": round(
                    float(
                        run.repair_projection.get(
                            "post_repair_nonlinear_score", run.score
                        )
                    ),
                    6,
                ),
                "reviewer_repair_outcome_score": round(
                    run.repair_outcome_score, 6
                ),
                "reviewer_repair_burden": round(
                    float(
                        run.repair_projection.get(
                            "reviewer_repair_burden", {}
                        ).get(
                            "initial_burden_total_mean", 0.0
                        )
                    ),
                    6,
                ),
                "repair_completion_rate": round(
                    run.repair_completion_rate, 9
                ),
                "exact_case_repair_rate": round(
                    exact_case_repair_rate, 9
                ),
                "case_score_deficit_recovery_rate": round(
                    case_score_deficit_recovery_rate, 9
                ),
                "repair_metadata_collision_suspect_count": (
                    repair_metadata_collision_suspect_count
                ),
                "collision_adjusted_repair_diagnostic_rate": round(
                    collision_adjusted_repair_diagnostic_rate, 9
                ),
                "collision_adjusted_repair_diagnostic_only": True,
                "qualification_verdict": run.qualification_verdict,
                "operational_eligibility_verdict": (
                    run.operational_eligibility
                ),
                "operational_safety_gate": run.operational_safety_gate,
                "core_closure_score": round(
                    float(
                        run.repair_projection.get(
                            "core_closure_score",
                            run.repair_outcome_score,
                        )
                    ),
                    6,
                ),
            },
            "scoring_projection": run.repair_projection,
            "comparison": run.comparison_projection,
            **run.comparison_projection,
            "failed_exam_item_count": run.failed_exam_item_count,
            "repair_rounds": run.repair_rounds,
            "semantic_repair_rounds": run.repair_rounds,
            "semantic_repair_round_limit": (
                self.SEMANTIC_REPAIR_ROUND_LIMIT
            ),
            "semantic_repair_lifecycle": run.repair_projection.get(
                "semantic_repair_lifecycle", []
            ),
            "cumulative_category_repair_penalty": run.repair_projection.get(
                "cumulative_category_repair_penalty"
            ),
            "structural_repair_turns": sum(
                int(run.scores[form_id].get("repair_turns") or 0)
                for form_id in ("A", "B")
            ),
            "pointer_normalization_revision": (
                CARD_REVIEWER_SUCCESSOR_POINTER_NORMALIZATION_REVISION
            ),
            "pointer_normalization_count": len(
                run.pointer_normalization_actions
            ),
            "pointer_normalizations": run.pointer_normalization_actions,
            "execution_profile": run.exam_inputs.get("execution_profile"),
            "sample_metadata": run.exam_inputs.get("sample_metadata"),
            "workload_profile": run.plan.get("workload_profile"),
            "initial_total_effective_prompt_chars": run.plan.get(
                "initial_total_effective_prompt_chars"
            ),
            "transport_topologies": deepcopy(
                run.state.get("transport_topologies", [])
            ),
            "output_limit_policy": run.plan.get("output_limit_policy"),
            "provider_calls": run.state["provider_calls"],
            "request_attempts": run.state["request_attempts"],
            "external_model_calls": run.state["external_model_calls"],
            "external_network_calls": run.state["external_network_calls"],
            "external_process_launches": run.state[
                "external_process_launches"
            ],
            "token_usage": run.state["token_usage"],
            "estimated_cost_cny": (
                round(run.state["estimated_cost_cny"], 9)
                if run.plan["profile_kind"] == "API"
                else (0.0 if run.plan["profile_kind"] == "LOCAL" else None)
            ),
            "actual_cost": None,
            "cost_semantics": run.plan["cost_semantics"],
            "reference_regression_only": True,
            "blind_holdout_eligible": False,
            "qualification_eligible": False,
            "provider_received_answer_key": False,
        }
        evidence = _write_json_create_only(
            run.run_root / "exam_result.json", public_result
        )
        duration_ms = max(0, int((monotonic() - run.started) * 1000))
        return {
            "schema_version": RESULT_SCHEMA,
            "kind": "WORKFLOW_NODE",
            "status": run.verdict,
            "score": int(round(run.score)),
            "score_exact": round(run.score, 6),
            "score_semantics": run.score_semantics,
            "execution_outcome": public_result["execution_outcome"],
            "scorability": public_result["scorability"],
            "raw_quality_candidate_verdict": public_result[
                "raw_quality_candidate_verdict"
            ],
            "quality_fail_admission": public_result[
                "quality_fail_admission"
            ],
            "reason": "Quality_REFERENCE_REGRESSION_COMPLETED",
            "requested_model": run.requested_model,
            "returned_model": run.state.get("returned_model") if run.target.get("adapter_id")=="command_template" else run.requested_model,
            "duration_ms": duration_ms,
            "external_network_calls": run.state["external_network_calls"],
            "provider_calls": run.state["provider_calls"],
            "request_attempts": run.state["request_attempts"],
            "external_model_calls": run.state["external_model_calls"],
            "external_process_launches": run.state[
                "external_process_launches"
            ],
            "exam_mode": run.plan["mode"],
            "exam_run_id": run.run_id,
            "exam_run_root": str(run.run_root.resolve()),
            "exam_evidence_sha256": evidence["sha256"],
            "executor_ref": self.EXECUTOR_REF,
            "executor_binding_sha256": public_result[
                "executor_binding_sha256"
            ],
            "failed_exam_item_count": run.failed_exam_item_count,
            "repair_rounds": run.repair_rounds,
            "semantic_repair_rounds": run.repair_rounds,
            "semantic_repair_round_limit": public_result[
                "semantic_repair_round_limit"
            ],
            "semantic_repair_lifecycle": public_result[
                "semantic_repair_lifecycle"
            ],
            "cumulative_category_repair_penalty": public_result[
                "cumulative_category_repair_penalty"
            ],
            "structural_repair_turns": public_result[
                "structural_repair_turns"
            ],
            "pointer_normalization_revision": public_result[
                "pointer_normalization_revision"
            ],
            "pointer_normalization_count": public_result[
                "pointer_normalization_count"
            ],
            "pointer_normalizations": public_result[
                "pointer_normalizations"
            ],
            "core_closure_verdict": public_result[
                "core_closure_verdict"
            ],
            "core_closure_score": public_result["core_closure_score"],
            "governed_disposition_case_count": public_result[
                "governed_disposition_case_count"
            ],
            "execution_profile": public_result["execution_profile"],
            "sample_metadata": public_result["sample_metadata"],
            "workload_profile": public_result["workload_profile"],
            "initial_total_effective_prompt_chars": public_result[
                "initial_total_effective_prompt_chars"
            ],
            "transport_topologies": public_result[
                "transport_topologies"
            ],
            "output_limit_policy": public_result[
                "output_limit_policy"
            ],
            "qualification_verdict": run.qualification_verdict,
            "quality_exact_repair_qualification_verdict": (
                run.qualification_verdict
            ),
            "operational_eligibility_verdict": run.operational_eligibility,
            "operational_safety_gate": run.operational_safety_gate,
            "operational_policy_revision": (
                CARD_REVIEWER_OPERATIONAL_POLICY_REVISION
            ),
            "safety_blocking_residual_count": (
                run.safety_blocking_residual_count
            ),
            "calibration_residual_count": run.calibration_residual_count,
            "scoring_system_revision": (
                CARD_REVIEWER_SCORING_PROJECTION_REVISION
            ),
            "repair_outcome_score": round(run.repair_outcome_score, 6),
            "repair_completion_rate": round(
                run.repair_completion_rate, 9
            ),
            "exact_case_repair_rate": round(
                exact_case_repair_rate, 9
            ),
            "case_score_deficit_recovery_rate": round(
                case_score_deficit_recovery_rate, 9
            ),
            "repair_metadata_collision_suspect_count": (
                repair_metadata_collision_suspect_count
            ),
            "collision_adjusted_repair_diagnostic_rate": round(
                collision_adjusted_repair_diagnostic_rate, 9
            ),
            "collision_adjusted_repair_diagnostic_only": True,
            "repair_prompt_projection_revision": (
                CARD_REVIEWER_REPAIR_PROMPT_PROJECTION_REVISION
            ),
            **run.comparison_projection,
            "token_usage": run.state["token_usage"],
            "estimated_cost_cny": public_result["estimated_cost_cny"],
            "actual_cost": None,
            "cost_semantics": run.plan["cost_semantics"],
            "reference_regression_only": True,
            "blind_holdout_eligible": False,
            "qualification_eligible": False,
            "provider_received_answer_key": False,
        }
