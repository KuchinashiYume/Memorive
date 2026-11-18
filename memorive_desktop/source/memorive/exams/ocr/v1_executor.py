"""The OCR page exam's first form (_QualityOcrPageExamExecutorV1) and the source character limits
both forms use.
"""
from __future__ import annotations

from memorive.models.provider_aliases import matches_result_model
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib
from pathlib import Path
from time import monotonic
from typing import Any, Callable, Mapping
import uuid
from memorive.exams.outcomes import ExecutionOutcome, Scorability, admit_quality_fail
from memorive.exams.common import (
    AUTHORIZATION_SCHEMA,
    PLAN_SCHEMA,
    RESULT_SCHEMA,
    SAFE_PACKAGE,
    _file_sha256,
    _load_json,
    _not_available,
    _not_run,
    _sha256,
    _write_json_create_only,
    _comparison_binding_sha256,
    _ollama_exam_profile,
)
from memorive.exams.specialized_base import _SpecializedExamBase


Core_MEAN_SOURCE_CHARS = 69_274


EXAM_SOFT_SOURCE_CHAR_LIMIT = 103_912


EXAM_ABSOLUTE_SOURCE_CHAR_LIMIT = 138_549


class _QualityOcrPageExamExecutorV1(_SpecializedExamBase):
    """One complete 48-case Quality OCR form through API or local vision.

    The original dual-form run requires 96 primary calls plus stability calls.
    The successor keeps an entire frozen form intact so one run stays within
    the authorized 50-call channel cap.  Form A and Form B are separate cohorts.
    The Quality pack explicitly forbids content-directed repair.
    """

    EXECUTOR_REF = "Desktop_MODEL_EXAM_SUCCESSOR_Quality_OCR_PAGE_EXECUTOR_V1"
    MODE = "LIVE_Quality_OCR_PAGE_SINGLE_COMPLETE_FORM_REFERENCE_REGRESSION"
    MODEL_CALLS = 48
    API_BUDGET_CAP_CNY = 0.0

    def __init__(
        self,
        *,
        scratch_root: Path,
        quality_package: str,
        reference_pack_root: Path,
    ) -> None:
        if not isinstance(quality_package, str) or not SAFE_PACKAGE.fullmatch(quality_package):
            raise ValueError("WORKFLOW_MODEL_EXAM_Quality_PACKAGE_INVALID")
        self.scratch_root = Path(scratch_root).resolve()
        self._quality_package = quality_package
        self._reference_pack_root = Path(reference_pack_root).resolve()
        self._modules: dict[str, Any] | None = None

    def _quality(self) -> dict[str, Any]:
        if self._modules is None:
            self._modules = {
                "exam": importlib.import_module(f"{self._quality_package}.ocr_page_exam"),
                "pack": importlib.import_module(f"{self._quality_package}.ocr_page_exam_pack"),
                "protocol": importlib.import_module(f"{self._quality_package}.ocr_page_exam_protocol"),
            }
        return self._modules

    @staticmethod
    def _profile_kind(target: Mapping[str, Any]) -> str | None:
        if target.get("kind") == "API":
            return (
                "API"
                if "siliconflow" in str(target.get("provider") or "").casefold()
                and target.get("model_name") == "deepseek-ai/DeepSeek-OCR"
                and target.get("connection_status") == "AVAILABLE"
                and isinstance(target.get("config_id"), str)
                and bool(target.get("config_id"))
                else None
            )
        if target.get("kind") == "LOCAL":
            return (
                "LOCAL"
                if _ollama_exam_profile(target, "capability", "VISION_OCR")
                else None
            )
        return None

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
        if sample_slot not in {1, 2}:
            return _not_available("WORKFLOW_MODEL_EXAM_SAMPLE_SLOT_INVALID")
        form_id = "A" if sample_slot == 1 else "B"
        try:
            verification = self._quality()["pack"].verify_reference_pack(self._reference_pack_root)
            manifest = _load_json(self._reference_pack_root / "manifest.reference.json")
            form = _load_json(self._reference_pack_root / "forms" / f"form_{form_id}.json")
        except (ImportError, OSError, ValueError, RuntimeError):
            return _not_available("WORKFLOW_MODEL_EXAM_Quality_ASSETS_INVALID")
        if (
            verification.get("verification_result") != "PASS"
            or verification.get("reference_regression_only") is not True
            or manifest.get("qualification_eligible") is not False
            or form.get("content_directed_repair_allowed") is not False
            or len(form.get("cases") or []) != self.MODEL_CALLS
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
            "scoring_projection_revision": "Desktop_OCR_COMPLETE_FORM_V1",
            "sample_revision": f"OCR_COMPLETE_FORM_{form_id}_V1",
        }
        comparison_cohort_id = "OCR_PAGE:" + _sha256(comparison_basis)
        plan: dict[str, Any] = {
            "schema_version": PLAN_SCHEMA,
            "status": "READY",
            "mode": self.MODE,
            "reason": "Quality_REFERENCE_REGRESSION_READY",
            "node_id": "ingest",
            "role_id": "OCR_PAGE",
            "exam_category_id": comparison_basis["exam_category_id"],
            "executor_ref": self.EXECUTOR_REF,
            "profile_kind": profile_kind,
            "profile_ref": target["config_id"],
            "model_name": target["model_name"],
            "model_digest": target.get("model_digest"),
            "settings_revision": context.get("settings_revision"),
            "settings_sha256": context.get("settings_sha256"),
            "reference_pack_id": verification["reference_pack_id"],
            "reference_pack_revision": verification["revision"],
            "reference_pack_sha256": verification["checksums_sha256"],
            "scoring_protocol_revision": manifest["scoring_protocol_id"],
            "scoring_protocol_sha256": scoring_protocol_sha256,
            "scoring_projection_revision": comparison_basis[
                "scoring_projection_revision"
            ],
            "form_id": form_id,
            "sample_slot": sample_slot,
            "sample_revision": f"OCR_COMPLETE_FORM_{form_id}_V1",
            "comparison_cohort_id": comparison_cohort_id,
            "comparison_binding_sha256": _comparison_binding_sha256(
                comparison_basis, comparison_cohort_id
            ),
            "horizontal_comparison_eligible": True,
            "horizontal_comparison_allowed_only_with_same_cohort": True,
            "cross_category_comparison_forbidden": True,
            "global_ranking_forbidden": True,
            "form_sha256": form["form_sha256"],
            "reference_regression_only": True,
            "blind_holdout_eligible": False,
            "qualification_eligible": False,
            "logical_model_calls": self.MODEL_CALLS,
            "maximum_model_calls": self.MODEL_CALLS,
            "semantic_repair_round_limit": 0,
            "content_directed_repair_allowed": False,
            "budget_cap_cny": self.API_BUDGET_CAP_CNY if profile_kind == "API" else None,
            "cost_semantics": (
                "FROZEN_Quality_OCR_ZERO_PRICE_PROFILE_2026-07-31"
                if profile_kind == "API"
                else "LOCAL_COMPUTE_NO_PROVIDER_API_COST"
            ),
            "length_policy": {
                "core_mean_source_chars": Core_MEAN_SOURCE_CHARS,
                "soft_limit_chars": EXAM_SOFT_SOURCE_CHAR_LIMIT,
                "absolute_limit_chars": EXAM_ABSOLUTE_SOURCE_CHAR_LIMIT,
                "applicability": "IMAGE_CASES_NOT_TEXT_LENGTH_SCALED",
            },
            "authorization_schema_version": AUTHORIZATION_SCHEMA,
            "external_network_calls": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
        }
        plan["plan_sha256"] = _sha256(plan)
        return plan

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
        requested_model = target.get("model_name") if isinstance(target.get("model_name"), str) else None
        if plan.get("status") != "READY":
            return _not_run(str(plan.get("reason") or "WORKFLOW_MODEL_EXAM_NOT_AVAILABLE"), requested_model=requested_model)
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
            form_id = str(plan["form_id"])
            form = _load_json(self._reference_pack_root / "forms" / f"form_{form_id}.json")
            prompt_path = self._reference_pack_root / str(form["prompt_ref"])
            prompt = prompt_path.read_text(encoding="utf-8")
            outputs: list[dict[str, Any]] = []
            for logical_index, case in enumerate(form["cases"], start=1):
                visible = modules["protocol"].build_provider_visible_payload(
                    pack=self._reference_pack_root,
                    form=form,
                    case_id=case["case_id"],
                )
                image_bytes = __import__("base64").b64decode(visible["image_base64"], validate=True)
                request_max_output_tokens = (
                    target.get("local_exam_max_output_tokens")
                    if target.get("kind") == "LOCAL"
                    else None
                )
                claim = {
                    "schema_version": "WorkflowOcrPreSendClaim-v1",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "call_id": f"call-{logical_index:02d}",
                    "call_kind": "OCR_PAGE_CASE",
                    "profile_kind": plan["profile_kind"],
                    "profile_ref": plan["profile_ref"],
                    "purpose": "workflow_model_exam",
                    "route": (
                        "EXISTING_SETTINGS_API_MULTIMODAL_TRANSPORT"
                        if plan["profile_kind"] == "API"
                        else "OLLAMA_LOOPBACK_VISION"
                    ),
                    "requested_model": requested_model,
                    "model_digest": target.get("model_digest"),
                    "case_id": case["case_id"],
                    "image_sha256": case["image_sha256"],
                    "mime_type": visible["mime_type"],
                    "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest().upper(),
                    "request_max_output_tokens": request_max_output_tokens,
                    "behavior_sha256": _sha256(
                        {
                            "model": requested_model,
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
                if plan["profile_kind"] == "CLI":
                    claim["cli_identity_binding"] = {
                        "adapter_id": target.get("adapter_id"),
                        "cli_binary_sha256": target.get("cli_binary_sha256"),
                        "cli_version": target.get("cli_version"),
                        "cli_identity_authority_ref": target.get(
                            "cli_identity_authority_ref"
                        ),
                        "thinking_mode": target.get("thinking_mode"),
                    }
                claim_receipt = _write_json_create_only(
                    run_root / "claims" / f"call_{logical_index:02d}.json",
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
                        timeout_seconds=120,
                    )
                )
                self._consume_result(state, result)
                if result.get("status") != "PASS":
                    raise ValueError(str(result.get("reason") or "OCR_CALL_FAILED"))
                if result.get("requested_model") != requested_model or not matches_result_model(result, requested_model):
                    raise ValueError("OCR_MODEL_IDENTITY_MISMATCH")
                raw_text = result.get("raw_text", result.get("text"))
                if not isinstance(raw_text, str) or not raw_text:
                    raise ValueError("OCR_RAW_TEXT_EVIDENCE_MISSING")
                provider_output = {
                    "schema_version": "WorkflowOcrProviderOutput-v1",
                    "logical_index": logical_index,
                    "case_id": case["case_id"],
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
                    / f"call_{logical_index:02d}.json",
                    provider_output,
                )
                output = modules["protocol"].freeze_output_envelope(
                    form=form,
                    case_id=case["case_id"],
                    output_text=result["text"],
                    transport_status="MODEL_DELIVERED",
                    finish_reason=str(result.get("finish_reason") or "unknown"),
                )
                outputs.append(output)
                _write_json_create_only(
                    run_root / "receipts" / f"call_{logical_index:02d}.json",
                    {
                        "schema_version": "WorkflowOcrCallReceipt-v1",
                        "logical_index": logical_index,
                        "case_id": case["case_id"],
                        "image_sha256": case["image_sha256"],
                        "output_sha256": output["output_sha256"],
                        "pre_send_claim_sha256": claim_receipt["sha256"],
                        "provider_output_sha256": provider_output_receipt[
                            "sha256"
                        ],
                        "provider_received_gold": False,
                        "execution_receipt": deepcopy(dict(result["execution_receipt"])),
                    },
                )
            if state["external_model_calls"] != self.MODEL_CALLS:
                raise ValueError("OCR_COMPLETE_FORM_CALL_COUNT_MISMATCH")
            response = {
                "schema_version": modules["exam"].OUTPUT_BUNDLE_SCHEMA_VERSION,
                "form_id": form_id,
                "generation": "gen1",
                "outputs": outputs,
                "provider_requests": self.MODEL_CALLS,
                "gold_sent_to_provider": False,
            }
            response_receipt = _write_json_create_only(run_root / "responses" / f"form_{form_id}.json", response)
            gold = _load_json(self._reference_pack_root / "gold" / f"form_{form_id}_gold.json")
            scoring = _load_json(self._reference_pack_root / "scoring" / "scoring_protocol.json")
            score_result = modules["exam"].score_form(
                form=form,
                gold=gold,
                response=response,
                scoring_protocol=scoring,
            )
            _write_json_create_only(run_root / "scores" / f"form_{form_id}.json", score_result)
            score_exact = float(score_result["score"])
            quality_candidate_pass = score_exact >= 80.0 and score_result["hard_gate_verdict"] == "PASS"
            blocking: list[dict[str, Any]] = []
            if not quality_candidate_pass:
                for detail in score_result["case_details"]:
                    failed_fields = list(detail.get("hard_failures") or [])
                    if not failed_fields and float(detail["case_score"]) < 80.0:
                        failed_fields = ["case_score_below_recommended_band"]
                    if failed_fields:
                        blocking.append(
                            {
                                "case_id": detail["case_id"],
                                "failed_fields": failed_fields,
                                "evidence_anchor": f"scores/form_{form_id}.json#case_details/{detail['case_id']}",
                                "blocking": True,
                            }
                        )
            admission = None
            if quality_candidate_pass:
                verdict = "PASS"
            else:
                admission = admit_quality_fail(
                    execution_outcome=ExecutionOutcome.COMPLETED,
                    scorability=Scorability.SCOREABLE,
                    repair_rounds_used=0,
                    repair_round_limit=0,
                    blocking_failures=blocking,
                )
                verdict = str(admission["quality_verdict"])
            if verdict not in {"PASS", "FAIL"}:
                raise ValueError("OCR_QUALITY_FAIL_NOT_ADMITTED")
            public_result = {
                "schema_version": "WorkflowModelExamRunResult-v2",
                "status": verdict,
                "reason": "Quality_OCR_COMPLETE_FORM_REFERENCE_REGRESSION_COMPLETED",
                "execution_outcome": "COMPLETED",
                "scorability": "SCOREABLE",
                "raw_quality_candidate_verdict": (
                    "PASS" if quality_candidate_pass else "FAIL"
                ),
                "quality_verdict": verdict,
                "run_id": run_id,
                "plan_sha256": plan["plan_sha256"],
                "requested_model": requested_model,
                "returned_model": requested_model,
                "score": int(round(score_exact)),
                "score_exact": round(score_exact, 6),
                "form_id": form_id,
                "sample_revision": plan["sample_revision"],
                "exam_category_id": plan["exam_category_id"],
                "role_id": plan["role_id"],
                "comparison_cohort_id": plan["comparison_cohort_id"],
                "comparison_binding_sha256": plan[
                    "comparison_binding_sha256"
                ],
                "horizontal_comparison_eligible": True,
                "horizontal_comparison_allowed_only_with_same_cohort": True,
                "failed_exam_item_count": len(blocking),
                "quality_fail_admission": admission,
                "semantic_repair_rounds": 0,
                **state,
                "estimated_cost_cny": round(float(state["estimated_cost_cny"]), 9),
                "actual_cost": None,
                "cost_semantics": plan["cost_semantics"],
                "reference_regression_only": True,
                "blind_holdout_eligible": False,
                "qualification_eligible": False,
                "provider_received_answer_key": False,
                "provider_received_gold": False,
                "response_sha256": response_receipt["sha256"],
                "cross_category_comparison_forbidden": True,
                "global_ranking_forbidden": True,
            }
            evidence = _write_json_create_only(run_root / "exam_result.json", public_result)
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
