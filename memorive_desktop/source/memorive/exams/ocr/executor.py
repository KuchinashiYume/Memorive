"""QualityOcrPageExamExecutor: plans, runs and scores the current OCR page exam.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from time import monotonic
from typing import Any, Callable, Mapping
import uuid
from memorive.exams.outcomes import workflow_exam_structured_output_policy
from memorive.models.output_limits import (
    WORKFLOW_EXAM_REPLY_TOKEN_CEILING,
    workflow_exam_output_limit_contract,
)
from memorive.exams.ocr_scoring import (
    MAIN_CASE_IDS as OCR_MAIN_CASE_IDS,
    SAMPLE_REVISION as OCR_SAMPLE_REVISION,
    SCORING_PROJECTION_REVISION as OCR_SCORING_PROJECTION_REVISION,
    STABILITY_CASE_ID as OCR_STABILITY_CASE_ID,
    STABILITY_REPEAT_COUNT as OCR_STABILITY_REPEAT_COUNT,
    score_balanced_sample as score_ocr_balanced_sample,
    score_case_successor as score_ocr_case_successor,
)
from memorive.exams.common import (
    AUTHORIZATION_SCHEMA,
    PLAN_SCHEMA,
    RESULT_SCHEMA,
    _file_sha256,
    _load_json,
    _not_available,
    _not_run,
    _sha256,
    _write_json_create_only,
)
from memorive.exams.specialized_base import SHA256
from memorive.exams.ocr.v1_executor import _QualityOcrPageExamExecutorV1


class QualityOcrPageExamExecutor(_QualityOcrPageExamExecutorV1):
    """Balanced OCR diagnostic over Quality assets for API, CLI, and local vision.

    The 48-call complete-form executor remains above as the immutable V1
    implementation.  This successor selects a frozen cross-form family sample,
    adds two exact-repeat observations, and separates execution, descriptive
    first-pass score, deterministic post-processing, bounded recovery, source
    guards, and the much stricter model-failure finding.
    """

    EXECUTOR_REF = "Desktop_MODEL_EXAM_SUCCESSOR_Quality_OCR_PAGE_EXECUTOR_V3"
    MODE = (
        "LIVE_Quality_OCR_PAGE_WORKFLOW_POSITION_DIAGNOSTIC_REFERENCE_REGRESSION"
    )
    MAIN_CALLS = len(OCR_MAIN_CASE_IDS)
    REPEAT_CALLS = OCR_STABILITY_REPEAT_COUNT
    MODEL_CALLS = MAIN_CALLS + REPEAT_CALLS
    API_BUDGET_CAP_CNY = 0.0

    @staticmethod
    def _profile_kind(target: Mapping[str, Any]) -> str | None:
        from memorive.models.cli.templates import is_custom,exam_eligible
        if is_custom(target):
            return "CLI" if exam_eligible(target,"images") else None
        inherited = _QualityOcrPageExamExecutorV1._profile_kind(target)
        if inherited is not None:
            return inherited
        from memorive.models.cli.templates import is_custom,exam_eligible
        if is_custom(target):
            return "CLI" if exam_eligible(target) else None
        if target.get("kind") != "CLI":
            return None
        return (
            "CLI"
            if target.get("adapter_id") == "codex_cli"
            and target.get("connection_status") == "AVAILABLE"
            and isinstance(target.get("executable"), str)
            and bool(target.get("executable"))
            and isinstance(target.get("model_name"), str)
            and bool(target.get("model_name"))
            and isinstance(target.get("thinking_mode"), str)
            and bool(target.get("thinking_mode"))
            and isinstance(target.get("cli_binary_sha256"), str)
            and bool(SHA256.fullmatch(str(target["cli_binary_sha256"])))
            and isinstance(target.get("cli_version"), str)
            and bool(target.get("cli_version"))
            and isinstance(target.get("cli_identity_authority_ref"), str)
            and bool(target.get("cli_identity_authority_ref"))
            and isinstance(target.get("config_id"), str)
            and bool(target.get("config_id"))
            else None
        )

    def _sample_manifest(self) -> tuple[dict[str, Any], dict[str, Mapping[str, Any]]]:
        forms = {
            form_id: _load_json(
                self._reference_pack_root / "forms" / f"form_{form_id}.json"
            )
            for form_id in ("A", "B")
        }
        by_case: dict[str, tuple[str, Mapping[str, Any]]] = {}
        for form_id, form in forms.items():
            for row in form["cases"]:
                case_id = str(row["case_id"])
                if case_id in by_case:
                    raise ValueError("OCR_SUCCESSOR_DUPLICATE_CASE_ID")
                by_case[case_id] = (form_id, row)
        if any(case_id not in by_case for case_id in OCR_MAIN_CASE_IDS):
            raise ValueError("OCR_SUCCESSOR_SAMPLE_CASE_MISSING")
        main_cases = [
            {
                "logical_index": index,
                "generation": "gen1",
                "form_id": by_case[case_id][0],
                "case_id": case_id,
                "family": by_case[case_id][1]["family"],
                "difficulty": by_case[case_id][1]["difficulty"],
                "image_sha256": by_case[case_id][1]["image_sha256"],
            }
            for index, case_id in enumerate(OCR_MAIN_CASE_IDS, start=1)
        ]
        repeat_cases = [
            {
                "logical_index": self.MAIN_CALLS + repeat_index,
                "generation": f"gen{repeat_index + 1}",
                "form_id": by_case[OCR_STABILITY_CASE_ID][0],
                "case_id": OCR_STABILITY_CASE_ID,
                "family": by_case[OCR_STABILITY_CASE_ID][1]["family"],
                "difficulty": by_case[OCR_STABILITY_CASE_ID][1]["difficulty"],
                "image_sha256": by_case[OCR_STABILITY_CASE_ID][1]["image_sha256"],
            }
            for repeat_index in range(1, OCR_STABILITY_REPEAT_COUNT + 1)
        ]
        manifest = {
            "schema_version": "WorkflowOcrBalancedSampleManifest-v2",
            "sample_revision": OCR_SAMPLE_REVISION,
            "main_cases": main_cases,
            "repeat_cases": repeat_cases,
            "main_call_count": self.MAIN_CALLS,
            "repeat_call_count": self.REPEAT_CALLS,
            "logical_model_calls": self.MODEL_CALLS,
            "family_count": len({row["family"] for row in main_cases}),
            "stability_case_id": OCR_STABILITY_CASE_ID,
            "same_frozen_sample_for_all_models": True,
            "provider_received_gold": False,
        }
        manifest["sample_manifest_sha256"] = _sha256(manifest)
        return manifest, forms

    def plan(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        if node.get("node_id") != "ingest":
            return _not_available("WORKFLOW_MODEL_EXAM_DYNAMIC_NODE_UNSUPPORTED")
        profile_kind = self._profile_kind(target)
        if profile_kind is None:
            return _not_available("WORKFLOW_MODEL_EXAM_DYNAMIC_PROFILE_UNSUPPORTED")
        sample_slot = context.get("sample_slot", 1)
        if sample_slot not in {1, 2, 3}:
            return _not_available("WORKFLOW_MODEL_EXAM_SAMPLE_SLOT_INVALID")
        retry_budget = context.get("transport_retry_budget", 0)
        if (
            isinstance(retry_budget, bool)
            or not isinstance(retry_budget, int)
            or not 0 <= retry_budget <= 3
        ):
            return _not_available("WORKFLOW_MODEL_EXAM_RETRY_BUDGET_INVALID")
        try:
            verification = self._quality()["pack"].verify_reference_pack(
                self._reference_pack_root
            )
            manifest = _load_json(self._reference_pack_root / "manifest.reference.json")
            sample, _ = self._sample_manifest()
        except (ImportError, OSError, ValueError, RuntimeError):
            return _not_available("WORKFLOW_MODEL_EXAM_Quality_ASSETS_INVALID")
        if (
            verification.get("verification_result") != "PASS"
            or verification.get("reference_regression_only") is not True
            or manifest.get("qualification_eligible") is not False
            or sample["family_count"] != len(self._quality()["exam"].FAMILIES)
        ):
            return _not_available("WORKFLOW_MODEL_EXAM_Quality_PACK_NOT_ELIGIBLE")
        scoring_protocol_sha256 = _file_sha256(
            self._reference_pack_root / "scoring" / "scoring_protocol.json"
        )
        comparison_basis = {
            "exam_category_id": "Quality_OCR_PAGE_REFERENCE_REGRESSION",
            "role_id": "OCR_PAGE",
            "reference_pack_sha256": verification["checksums_sha256"],
            "scoring_protocol_sha256": scoring_protocol_sha256,
            "scoring_projection_revision": OCR_SCORING_PROJECTION_REVISION,
            "sample_revision": OCR_SAMPLE_REVISION,
            "sample_manifest_sha256": sample["sample_manifest_sha256"],
        }
        comparison_cohort_id = "OCR_PAGE:" + _sha256(comparison_basis)
        if profile_kind == "API":
            budget_cap_cny: float | None = self.API_BUDGET_CAP_CNY
            cost_semantics = "FROZEN_Quality_OCR_ZERO_PRICE_PROFILE_2026-07-31"
        elif profile_kind == "CLI":
            budget_cap_cny = None
            cost_semantics = "CLI_MANAGED_UNKNOWN" if target.get("adapter_id")=="command_template" else "CHATGPT_SUBSCRIPTION_CLI_NO_PER_CALL_PRICE_RECEIPT"
        else:
            budget_cap_cny = None
            cost_semantics = "LOCAL_COMPUTE_NO_PROVIDER_API_COST"
        try:
            (
                _output_binding,
                request_max_output_tokens,
                output_limit_mode,
            ) = workflow_exam_structured_output_policy(
                target,
                local_declared_field="local_exam_max_output_tokens",
            )
        except ValueError as error:
            return _not_available(str(error))
        plan: dict[str, Any] = {
            "schema_version": PLAN_SCHEMA,
            "status": "READY",
            "mode": self.MODE,
            "reason": "Quality_OCR_WORKFLOW_POSITION_DIAGNOSTIC_READY",
            "node_id": "ingest",
            "role_id": "OCR_PAGE",
            "exam_category_id": comparison_basis["exam_category_id"],
            "executor_ref": self.EXECUTOR_REF,
            "profile_kind": profile_kind,
            "profile_ref": target["config_id"],
            "model_name": target["model_name"],
            "model_digest": target.get("model_digest"),
            "thinking_mode": target.get("thinking_mode"),
            "cli_binary_sha256": target.get("cli_binary_sha256"),
            "cli_version": target.get("cli_version"),
            "cli_identity_authority_ref": target.get(
                "cli_identity_authority_ref"
            ),
            "settings_revision": context.get("settings_revision"),
            "settings_sha256": context.get("settings_sha256"),
            "reference_pack_id": verification["reference_pack_id"],
            "reference_pack_revision": verification["revision"],
            "reference_pack_sha256": verification["checksums_sha256"],
            "scoring_protocol_revision": manifest["scoring_protocol_id"],
            "scoring_protocol_sha256": scoring_protocol_sha256,
            "scoring_projection_revision": OCR_SCORING_PROJECTION_REVISION,
            "sample_slot": int(sample_slot),
            "sample_revision": OCR_SAMPLE_REVISION,
            "sample_manifest_sha256": sample["sample_manifest_sha256"],
            "comparison_cohort_id": comparison_cohort_id,
            "comparison_binding_sha256": _sha256(
                {**comparison_basis, "comparison_cohort_id": comparison_cohort_id}
            ),
            "horizontal_comparison_eligible": True,
            "horizontal_comparison_allowed_only_with_same_cohort": True,
            "full_quality_score_comparison_eligible": False,
            "cross_category_comparison_forbidden": True,
            "global_ranking_forbidden": True,
            "main_model_calls": self.MAIN_CALLS,
            "repeat_model_calls": self.REPEAT_CALLS,
            "logical_model_calls": self.MODEL_CALLS,
            "transport_retry_budget": retry_budget,
            "maximum_model_calls": self.MODEL_CALLS + retry_budget,
            "semantic_repair_round_limit": 0,
            "content_directed_repair_allowed": False,
            "reference_regression_only": True,
            "blind_holdout_eligible": False,
            "qualification_eligible": False,
            "category_score_is_descriptive": True,
            "model_fail_proof_rule": (
                "deterministic_score_below_60_and_two_distinct_material_cases_or_"
                "three_generation_repeat_material_failure"
            ),
            "recovery_credit_policy": (
                "NO_PAGE_RECOVERY_CREDIT_WITHOUT_NEW_FROZEN_OUTPUT_AND_"
                "LOCAL_VERIFICATION"
            ),
            "workflow_position": "PAGE_LEVEL_RESCUE_AND_DUAL_PATH_CHECK",
            "budget_cap_cny": budget_cap_cny,
            "cost_semantics": cost_semantics,
            "output_limit_policy": (
                output_limit_mode
            ),
            "request_max_output_tokens": request_max_output_tokens,
            "calibrated_reply_token_ceiling": (
                WORKFLOW_EXAM_REPLY_TOKEN_CEILING
            ),
            "output_limit_contract": workflow_exam_output_limit_contract(),
            "authorization_schema_version": AUTHORIZATION_SCHEMA,
            "external_network_calls": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
        }
        plan["plan_sha256"] = _sha256(plan)
        return plan

    @staticmethod
    def _route(profile_kind: str) -> str:
        return {
            "API": "EXISTING_SETTINGS_API_MULTIMODAL_TRANSPORT",
            "CLI": "CODEX_CLI_SUBSCRIPTION_MULTIMODAL",
            "LOCAL": "OLLAMA_LOOPBACK_VISION",
        }[profile_kind]

    def execute(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, Any],
        *,
        authorization: Mapping[str, Any] | None,
        structured_chat: Callable[..., Mapping[str, Any]],
        ocr_call: Callable[..., Mapping[str, Any]],
    ) -> dict[str, Any]:
        del structured_chat
        started = monotonic()
        plan = self.plan(node, target, context)
        requested_model = (
            target.get("model_name")
            if isinstance(target.get("model_name"), str)
            else None
        )
        if plan.get("status") != "READY":
            return _not_run(
                str(plan.get("reason") or "WORKFLOW_MODEL_EXAM_NOT_AVAILABLE"),
                requested_model=requested_model,
            )
        reason = self._authorization_reason(authorization, plan)
        if reason is not None:
            return _not_run(reason, requested_model=requested_model)
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
        state = self._state()
        try:
            modules = self._quality()
            sample, forms = self._sample_manifest()
            if sample["sample_manifest_sha256"] != plan["sample_manifest_sha256"]:
                raise ValueError("OCR_SAMPLE_MANIFEST_DRIFT")
            _write_json_create_only(run_root / "sample_manifest.json", sample)
            form_case_by_id = {
                str(row["case_id"]): (form_id, row)
                for form_id, form in forms.items()
                for row in form["cases"]
            }
            physical_index = 0
            retry_budget_remaining = int(plan["transport_retry_budget"])
            returned_model_evidence: list[str | None] = []
            identity_grades: list[str] = []

            def run_case(sample_row: Mapping[str, Any]) -> dict[str, Any]:
                nonlocal physical_index, retry_budget_remaining
                case_id = str(sample_row["case_id"])
                generation = str(sample_row["generation"])
                form_id, case = form_case_by_id[case_id]
                form = forms[form_id]
                prompt = (
                    self._reference_pack_root / str(form["prompt_ref"])
                ).read_text(encoding="utf-8")
                visible = modules["protocol"].build_provider_visible_payload(
                    pack=self._reference_pack_root,
                    form=form,
                    case_id=case_id,
                )
                image_bytes = __import__("base64").b64decode(
                    visible["image_base64"], validate=True
                )
                request_max_output_tokens = plan["request_max_output_tokens"]
                timeout_seconds = target.get(
                    "exam_timeout_seconds",
                    300 if target.get("kind") == "CLI" else 180,
                )
                while True:
                    physical_index += 1
                    claim = {
                        "schema_version": "WorkflowOcrPreSendClaim-v2",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "physical_call_id": f"call-{physical_index:02d}",
                        "logical_index": sample_row["logical_index"],
                        "generation": generation,
                        "call_kind": (
                            "OCR_PAGE_REPEAT_CASE"
                            if generation != "gen1"
                            else "OCR_PAGE_MAIN_CASE"
                        ),
                        "profile_kind": plan["profile_kind"],
                        "profile_ref": plan["profile_ref"],
                        "purpose": "workflow_model_exam",
                        "route": ("DECLARATIVE_COMMAND_TEMPLATE" if target.get("adapter_id")=="command_template" else self._route(str(plan["profile_kind"]))),
                        "requested_model": requested_model,
                        "model_digest": target.get("model_digest"),
                        "thinking_mode": target.get("thinking_mode"),
                        "cli_binary_sha256": target.get("cli_binary_sha256"),
                        "cli_version": target.get("cli_version"),
                        "cli_identity_authority_ref": target.get(
                            "cli_identity_authority_ref"
                        ),
                        "case_id": case_id,
                        "image_sha256": case["image_sha256"],
                        "mime_type": visible["mime_type"],
                        "prompt_sha256": hashlib.sha256(
                            prompt.encode("utf-8")
                        ).hexdigest().upper(),
                        "request_max_output_tokens": request_max_output_tokens,
                        "behavior_sha256": _sha256(
                            {
                                "model": requested_model,
                                "thinking_mode": target.get("thinking_mode"),
                                "cli_binary_sha256": target.get(
                                    "cli_binary_sha256"
                                ),
                                "cli_version": target.get("cli_version"),
                                "image_sha256": case["image_sha256"],
                                "mime_type": visible["mime_type"],
                                "prompt": prompt,
                                "purpose": "workflow_model_exam",
                                "max_output_tokens": request_max_output_tokens,
                            }
                        ),
                        "plan_sha256": plan["plan_sha256"],
                        "provider_received_gold": False,
                    }
                    claim_receipt = _write_json_create_only(
                        run_root / "claims" / f"call_{physical_index:02d}.json",
                        claim,
                    )
                    result = dict(
                        ocr_call(
                            profile_kind=str(target["kind"]),
                            service=target,
                            model=target,
                            image_bytes=image_bytes,
                            mime_type=visible["mime_type"],
                            prompt=prompt,
                            purpose="workflow_model_exam",
                            max_output_tokens=request_max_output_tokens,
                            timeout_seconds=timeout_seconds,
                        )
                    )
                    self._consume_result(state, result)
                    attempt_summary = {
                        "schema_version": "WorkflowOcrAttemptReceipt-v2",
                        "physical_index": physical_index,
                        "logical_index": sample_row["logical_index"],
                        "generation": generation,
                        "case_id": case_id,
                        "status": result.get("status"),
                        "reason": result.get("reason"),
                        "requested_model": result.get("requested_model"),
                        "returned_model": result.get("returned_model"),
                        "external_model_calls": int(
                            result.get("external_model_calls") or 0
                        ),
                        "physical_model_calls": int(
                            result.get("physical_model_calls")
                            if result.get("physical_model_calls") is not None
                            else result.get("external_model_calls") or 0
                        ),
                        "external_budget_calls": int(
                            result.get("external_budget_calls") or 0
                        ),
                        "local_model_calls": int(
                            result.get("local_model_calls") or 0
                        ),
                        "duration_ms": result.get("duration_ms"),
                        "pre_send_claim_sha256": claim_receipt["sha256"],
                        "execution_receipt": deepcopy(
                            dict(result.get("execution_receipt") or {})
                        ),
                        "provider_received_gold": False,
                    }
                    _write_json_create_only(
                        run_root
                        / "attempts"
                        / f"call_{physical_index:02d}.json",
                        attempt_summary,
                    )
                    if result.get("status") == "PASS":
                        break
                    failure_reason = str(result.get("reason") or "OCR_CALL_FAILED")
                    retryable = any(
                        token in failure_reason.upper()
                        for token in (
                            "RATE_LIMIT",
                            "NETWORK",
                            "TIMEOUT",
                            "TEMPORARY",
                            "CONNECTION",
                        )
                    )
                    if (
                        not retryable
                        or retry_budget_remaining <= 0
                        or int(result.get("external_model_calls") or 0) <= 0
                    ):
                        raise ValueError(failure_reason)
                    retry_budget_remaining -= 1

                if result.get("requested_model") != requested_model:
                    raise ValueError("OCR_MODEL_IDENTITY_MISMATCH")
                execution_receipt = result.get("execution_receipt")
                if not isinstance(execution_receipt, Mapping):
                    raise ValueError("OCR_EXECUTION_RECEIPT_MISSING")
                returned = result.get("returned_model")
                identity_grade = execution_receipt.get("model_identity_grade")
                recorded_unverified = (
                    target.get("kind") == "CLI"
                    and returned is None
                    and identity_grade == "RECORDED_UNVERIFIED"
                    and execution_receipt.get("provider_event_model_identity_grade")
                    == "PROVIDER_NOT_EXPOSED"
                    and execution_receipt.get("request_model_binding_status")
                    == "EXACT_ARGV_AND_FROZEN_ALLOWLIST"
                    and isinstance(
                        execution_receipt.get("request_model_binding_sha256"), str
                    )
                    and bool(
                        SHA256.fullmatch(
                            str(execution_receipt["request_model_binding_sha256"])
                        )
                    )
                )
                from memorive.models.cli.templates import receipt_model_bound
                generic_bound=target.get("adapter_id")=="command_template" and receipt_model_bound(execution_receipt,requested_model)
                if returned != requested_model and not recorded_unverified and not generic_bound:
                    raise ValueError("OCR_MODEL_IDENTITY_MISMATCH")
                returned_model_evidence.append(
                    returned if isinstance(returned, str) else None
                )
                identity_grades.append(
                    str(identity_grade or "DIRECT_RETURNED_MODEL_MATCH")
                )
                raw_text = result.get("raw_text", result.get("text"))
                if not isinstance(raw_text, str) or not raw_text:
                    raise ValueError("OCR_RAW_TEXT_EVIDENCE_MISSING")
                provider_output = {
                    "schema_version": "WorkflowOcrProviderOutput-v2",
                    "physical_index": physical_index,
                    "logical_index": sample_row["logical_index"],
                    "generation": generation,
                    "case_id": case_id,
                    "requested_model": requested_model,
                    "returned_model": result.get("returned_model"),
                    "finish_reason": str(result.get("finish_reason") or "unknown"),
                    "raw_text": raw_text,
                    "normalized_text": result["text"],
                    "raw_text_sha256": hashlib.sha256(
                        raw_text.encode("utf-8")
                    ).hexdigest().upper(),
                    "normalized_text_sha256": hashlib.sha256(
                        str(result["text"]).encode("utf-8")
                    ).hexdigest().upper(),
                    "normalization_revision": result.get(
                        "normalization_revision", "LEGACY_IDENTITY_FALLBACK"
                    ),
                    "normalization_applied": bool(
                        result.get(
                            "normalization_applied", raw_text != result["text"]
                        )
                    ),
                    "provider_received_gold": False,
                }
                provider_output_receipt = _write_json_create_only(
                    run_root
                    / "provider_outputs"
                    / f"logical_{int(sample_row['logical_index']):02d}_{generation}.json",
                    provider_output,
                )
                output = modules["protocol"].freeze_output_envelope(
                    form=form,
                    case_id=case_id,
                    output_text=result["text"],
                    transport_status="MODEL_DELIVERED",
                    finish_reason=str(result.get("finish_reason") or "unknown"),
                )
                _write_json_create_only(
                    run_root
                    / "receipts"
                    / f"logical_{int(sample_row['logical_index']):02d}_{generation}.json",
                    {
                        "schema_version": "WorkflowOcrCallReceipt-v2",
                        "physical_index": physical_index,
                        "logical_index": sample_row["logical_index"],
                        "generation": generation,
                        "case_id": case_id,
                        "image_sha256": case["image_sha256"],
                        "output_sha256": output["output_sha256"],
                        "pre_send_claim_sha256": claim_receipt["sha256"],
                        "provider_output_sha256": provider_output_receipt["sha256"],
                        "provider_received_gold": False,
                        "execution_receipt": deepcopy(dict(execution_receipt)),
                    },
                )
                return output

            main_outputs = [run_case(row) for row in sample["main_cases"]]
            repeat_outputs = [run_case(row) for row in sample["repeat_cases"]]
            if not self.MODEL_CALLS <= state["external_model_calls"] <= plan["maximum_model_calls"]:
                raise ValueError("OCR_SUCCESSOR_PHYSICAL_CALL_COUNT_MISMATCH")
            frozen = {
                "schema_version": "WorkflowOcrBalancedOutputsFrozen-v2",
                "sample_manifest_sha256": sample["sample_manifest_sha256"],
                "requested_model": requested_model,
                "main_outputs": main_outputs,
                "repeat_outputs": repeat_outputs,
                "physical_model_calls": state["physical_model_calls"],
                "gold_sent_to_provider": False,
            }
            frozen_receipt = _write_json_create_only(
                run_root / "outputs_frozen_before_gold.json", frozen
            )

            gold_by_case: dict[str, Mapping[str, Any]] = {}
            for form_id in ("A", "B"):
                gold = _load_json(
                    self._reference_pack_root / "gold" / f"form_{form_id}_gold.json"
                )
                gold_by_case.update(
                    {str(row["case_id"]): row for row in gold["cases"]}
                )
            main_details = [
                score_ocr_case_successor(
                    exam=modules["exam"],
                    form_case=form_case_by_id[str(output["case_id"])][1],
                    gold_case=gold_by_case[str(output["case_id"])],
                    output=output,
                )
                for output in main_outputs
            ]
            repeat_details = [
                score_ocr_case_successor(
                    exam=modules["exam"],
                    form_case=form_case_by_id[str(output["case_id"])][1],
                    gold_case=gold_by_case[str(output["case_id"])],
                    output=output,
                )
                for output in repeat_outputs
            ]
            stability_texts = [
                next(
                    str(output["output_text"])
                    for output in main_outputs
                    if output["case_id"] == OCR_STABILITY_CASE_ID
                ),
                *(str(output["output_text"]) for output in repeat_outputs),
            ]
            score_result = score_ocr_balanced_sample(
                exam=modules["exam"],
                main_details=main_details,
                repeat_details=repeat_details,
                stability_texts=stability_texts,
            )
            _write_json_create_only(run_root / "sample_score.json", score_result)
            if (
                plan.get("budget_cap_cny") is not None
                and state["estimated_cost_cny"] > float(plan["budget_cap_cny"])
            ):
                raise ValueError("OCR_SUCCESSOR_COST_CAP_EXCEEDED")
            quality_verdict = (
                "FAIL" if score_result["model_fail_established"] else "PASS"
            )
            operational_by_guard = {
                "RECOMMENDED": ("SUITABLE", "SUITABLE"),
                "RECOMMENDED_AFTER_DETERMINISTIC_NORMALIZATION": (
                    "SUITABLE_AFTER_DETERMINISTIC_NORMALIZATION",
                    "SUITABLE_AFTER_DETERMINISTIC_NORMALIZATION",
                ),
                "PASS_WITH_BOUNDED_PAGE_RECOVERY": (
                    "SUITABLE_AFTER_VERIFIED_PAGE_RECOVERY",
                    "PAGE_RECOVERY_REQUIRED_BEFORE_RELEASE",
                ),
                "PASS_WITH_SOURCE_COMPARISON_GUARD": (
                    "SUITABLE_WITH_SOURCE_COMPARISON_GUARD",
                    "SOURCE_COMPARISON_REQUIRED_BEFORE_RELEASE",
                ),
                "PASS_WITH_STRUCTURE_GUARD": (
                    "SUITABLE_WITH_STRUCTURE_GUARD",
                    "STRUCTURE_REVIEW_REQUIRED_BEFORE_RELEASE",
                ),
            }
            operational, terminal = operational_by_guard.get(
                score_result["guarded_eligibility"],
                ("NOT_RECOMMENDED", "NOT_RECOMMENDED_DIAGNOSTIC_SAMPLE"),
            )
            safety_count = len(score_result["material_safety_case_ids"])
            if score_result["model_fail_established"]:
                operational_safety_gate = "FAIL"
            elif score_result["material_safety_case_ids"]:
                operational_safety_gate = "SOURCE_COMPARISON_REQUIRED"
            elif score_result["structural_case_ids"]:
                operational_safety_gate = "STRUCTURE_REVIEW_REQUIRED"
            else:
                operational_safety_gate = "PASS"
            calibration_count = len(score_result["calibration_case_ids"]) + sum(
                int(bool(detail["presentation_resolutions"]))
                for detail in main_details
            )
            failed_items = sorted(
                set(score_result["material_safety_case_ids"])
                | set(score_result["structural_case_ids"])
                | set(score_result["coverage_case_ids"])
            )
            estimated_cost = (
                None
                if plan["profile_kind"] == "CLI"
                else round(float(state["estimated_cost_cny"]), 9)
            )
            aggregate_returned_model = (
                requested_model
                if returned_model_evidence
                and all(value == requested_model for value in returned_model_evidence)
                else None
            )
            aggregate_identity_grade = ('REQUEST_BOUND_RETURNED_UNKNOWN' if 'REQUEST_BOUND_RETURNED_UNKNOWN' in identity_grades
                else 'RECORDED_UNVERIFIED' if 'RECORDED_UNVERIFIED' in identity_grades else 'VERIFIED')
            public_result = {
                "schema_version": "WorkflowModelExamRunResult-v3",
                "status": quality_verdict,
                "reason": "Quality_OCR_WORKFLOW_POSITION_DIAGNOSTIC_COMPLETED",
                "execution_outcome": "COMPLETED",
                "scorability": "SCOREABLE",
                "raw_quality_candidate_verdict": quality_verdict,
                "quality_verdict": quality_verdict,
                "model_fail_verdict": score_result["model_fail_verdict"],
                "model_fail_established": score_result["model_fail_established"],
                "score_band": score_result["score_band"],
                "guarded_eligibility": score_result["guarded_eligibility"],
                "unrestricted_eligibility": score_result[
                    "unrestricted_eligibility"
                ],
                "workflow_eligibility": score_result["workflow_eligibility"],
                "workflow_disposition": score_result["workflow_disposition"],
                "operational_eligibility_verdict": operational,
                "operational_policy_revision": (
                    "Desktop_OCR_WORKFLOW_RECOVERY_AND_SOURCE_GUARDS_V3"
                ),
                "operational_safety_gate": operational_safety_gate,
                "terminal_disposition": terminal,
                "run_id": run_id,
                "plan_sha256": plan["plan_sha256"],
                "requested_model": requested_model,
                "returned_model": aggregate_returned_model,
                "model_identity_grade": aggregate_identity_grade,
                "score": int(round(float(score_result["score"]))),
                "score_exact": score_result["score"],
                "first_pass_score_exact": score_result["first_pass_score"],
                "deterministic_postprocess_score_exact": score_result[
                    "deterministic_postprocess_score"
                ],
                "verified_recovery_score_exact": score_result[
                    "verified_recovery_score"
                ],
                "unverified_recovery_ceiling": score_result[
                    "unverified_recovery_ceiling"
                ],
                "unverified_recovery_ceiling_is_achieved_score": False,
                "recovery_credit_policy": score_result[
                    "recovery_credit_policy"
                ],
                "category_score_is_descriptive": True,
                "sample_slot": plan["sample_slot"],
                "sample_revision": plan["sample_revision"],
                "sample_manifest_sha256": plan["sample_manifest_sha256"],
                "exam_category_id": plan["exam_category_id"],
                "role_id": plan["role_id"],
                "comparison_cohort_id": plan["comparison_cohort_id"],
                "comparison_binding_sha256": plan["comparison_binding_sha256"],
                "horizontal_comparison_eligible": True,
                "horizontal_comparison_allowed_only_with_same_cohort": True,
                "failed_exam_item_count": len(failed_items),
                "safety_blocking_residual_count": safety_count,
                "calibration_residual_count": calibration_count,
                "material_safety_case_ids": score_result[
                    "material_safety_case_ids"
                ],
                "structural_case_ids": score_result["structural_case_ids"],
                "coverage_case_ids": score_result["coverage_case_ids"],
                "auto_normalization_case_ids": score_result[
                    "auto_normalization_case_ids"
                ],
                "bounded_recovery_case_ids": score_result[
                    "bounded_recovery_case_ids"
                ],
                "source_guard_case_ids": score_result[
                    "source_guard_case_ids"
                ],
                "semantic_repair_rounds": 0,
                "page_recovery_rounds": 0,
                **state,
                "estimated_cost_cny": estimated_cost,
                "actual_cost": None,
                "cost_semantics": plan["cost_semantics"],
                "reference_regression_only": True,
                "blind_holdout_eligible": False,
                "qualification_eligible": False,
                "qualification_verdict": "NOT_ASSESSED",
                "provider_received_answer_key": False,
                "provider_received_gold": False,
                "local_reference_pack_embedded": True,
                "outputs_frozen_before_gold_sha256": frozen_receipt["sha256"],
                "cross_category_comparison_forbidden": True,
                "global_ranking_forbidden": True,
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
            }
        except Exception as error:
            return self._failure(
                started=started,
                run_id=run_id,
                run_root=run_root,
                plan=plan,
                requested_model=requested_model,
                state=state,
                error=error,
            )
