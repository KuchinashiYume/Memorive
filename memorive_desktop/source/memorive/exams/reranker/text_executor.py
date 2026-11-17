"""QualityRerankerTextExamExecutor: plans, runs and scores the reference reranker text exam.
"""
from __future__ import annotations

from memorive.models.provider_aliases import matches_result_model
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib
import math
from pathlib import Path
import statistics
from time import monotonic
from typing import (
    Any,
    Callable,
    Mapping,
    Sequence,
)
import uuid
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
)
from memorive.exams.specialized_base import SHA256, _SpecializedExamBase


class QualityRerankerTextExamExecutor(_SpecializedExamBase):
    """Capability-gated 48-call successor sample over the frozen reranker pack."""

    EXECUTOR_REF = "Desktop_MODEL_EXAM_SUCCESSOR_Quality_RERANKER_TEXT_EXECUTOR_V1"
    MODE = "LIVE_Quality_RERANKER_BALANCED_SAMPLE_REFERENCE_REGRESSION"
    MAIN_CALLS = 40
    REPEAT_CALLS = 8
    MODEL_CALLS = MAIN_CALLS + REPEAT_CALLS
    API_BUDGET_CAP_CNY = 0.05
    SAMPLE_REVISION = "RERANKER_BALANCED_40_PLUS_8_REPEAT_V1"
    FAMILY_QUOTAS = {
        "RR1": 4,
        "RR2": 3,
        "RR3": 3,
        "RR4": 3,
        "RR5": 3,
        "RR6": 2,
        "RR7": 2,
    }
    REPEAT_FAMILIES = ("RR1", "RR2", "RR3", "RR7")
    SCORING_PROJECTION_REVISION = "Desktop_RERANKER_BALANCED_40_PLUS_8_V1"

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
        self._module: Any | None = None

    def _quality(self) -> Any:
        if self._module is None:
            self._module = importlib.import_module(f"{self._quality_package}.reranker_text_exam")
        return self._module

    @staticmethod
    def _profile_kind(target: Mapping[str, Any]) -> str | None:
        if target.get("kind") == "API":
            return (
                "API"
                if "siliconflow" in str(target.get("provider") or "").casefold()
                and target.get("model_name") in {
                    "BAAI/bge-reranker-v2-m3",
                    "Pro/BAAI/bge-reranker-v2-m3",
                }
                and target.get("connection_status") == "AVAILABLE"
                and isinstance(target.get("config_id"), str)
                and bool(target.get("config_id"))
                else None
            )
        if target.get("kind") == "LOCAL":
            return (
                "LOCAL"
                if target.get("endpoint_kind") == "local_reranker"
                and target.get("capability") == "RERANKER"
                and target.get("execution_eligible") is True
                and target.get("exact_identity_available") is True
                and target.get("connection_status") == "AVAILABLE"
                and isinstance(target.get("rerank_endpoint"), str)
                and isinstance(target.get("model_digest"), str)
                and bool(SHA256.fullmatch(str(target["model_digest"])))
                and isinstance(target.get("config_id"), str)
                and bool(target.get("config_id"))
                else None
            )
        return None

    @staticmethod
    def _claim_route(profile_kind: str) -> str:
        return {
            "API": "EXISTING_SETTINGS_API_RERANK_TRANSPORT",
            "LOCAL": "LOCAL_RERANKER_LOOPBACK",
        }[profile_kind]

    def _claim_route_for_target(
        self,
        profile_kind: str,
        target: Mapping[str, Any],
    ) -> str:
        del target
        return self._claim_route(profile_kind)

    @staticmethod
    def _model_identity_accepted(
        result: Mapping[str, Any],
        target: Mapping[str, Any],
        requested_model: str | None,
    ) -> bool:
        del target
        return (
            isinstance(requested_model, str)
            and result.get("requested_model") == requested_model
            and matches_result_model(result, requested_model)
        )

    @classmethod
    def _ordered_family_sample(
        cls,
        items: Sequence[Mapping[str, Any]],
        *,
        form_id: str,
        slot: int,
    ) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        for family_id, quota in cls.FAMILY_QUOTAS.items():
            members = [deepcopy(dict(row)) for row in items if row.get("family_id") == family_id]
            members.sort(
                key=lambda row: hashlib.sha256(
                    f"{cls.SAMPLE_REVISION}|{form_id}|{slot}|{row['item_id']}".encode("utf-8")
                ).hexdigest()
            )
            selected.extend(members[:quota])
        order = {str(row["item_id"]): index for index, row in enumerate(items)}
        selected.sort(key=lambda row: order[str(row["item_id"])])
        expected = cls.MAIN_CALLS // 2
        if len(selected) != expected or len({row["item_id"] for row in selected}) != expected:
            raise ValueError("RERANKER_SAMPLE_PARTITION_INVALID")
        return selected

    def _sample_manifest(self, slot: int) -> dict[str, Any]:
        forms = {
            form_id: _load_json(self._reference_pack_root / "forms" / f"form_{form_id}.json")
            for form_id in ("A", "B")
        }
        selected = {
            form_id: self._ordered_family_sample(forms[form_id]["items"], form_id=form_id, slot=slot)
            for form_id in ("A", "B")
        }
        repeats: list[dict[str, str]] = []
        for form_id in ("A", "B"):
            for family_id in self.REPEAT_FAMILIES:
                row = next(item for item in selected[form_id] if item["family_id"] == family_id)
                repeats.append({"form_id": form_id, "item_id": row["item_id"]})
        if len(repeats) != self.REPEAT_CALLS:
            raise ValueError("RERANKER_REPEAT_SAMPLE_PARTITION_INVALID")
        value = {
            "schema_version": "WorkflowRerankerSampleManifest-v1",
            "sample_revision": self.SAMPLE_REVISION,
            "sample_slot": slot,
            "main_items_by_form": {
                form_id: [str(row["item_id"]) for row in selected[form_id]]
                for form_id in ("A", "B")
            },
            "repeat_items": repeats,
            "main_call_count": self.MAIN_CALLS,
            "repeat_call_count": self.REPEAT_CALLS,
            "provider_received_gold": False,
        }
        value["sample_manifest_sha256"] = _sha256(value)
        value["_selected"] = selected
        return value

    def plan(self, node: Mapping[str, Any], target: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
        if node.get("node_id") != "context_pack":
            return _not_available("WORKFLOW_MODEL_EXAM_DYNAMIC_NODE_UNSUPPORTED")
        profile_kind = self._profile_kind(target)
        if profile_kind is None:
            return _not_available("WORKFLOW_MODEL_EXAM_DYNAMIC_PROFILE_UNSUPPORTED")
        slot = context.get("sample_slot", 1)
        if slot not in {1, 2, 3}:
            return _not_available("WORKFLOW_MODEL_EXAM_SAMPLE_SLOT_INVALID")
        retry_budget = context.get("transport_retry_budget", 0)
        if (
            isinstance(retry_budget, bool)
            or not isinstance(retry_budget, int)
            or not 0 <= retry_budget <= 1
        ):
            return _not_available("WORKFLOW_MODEL_EXAM_RETRY_BUDGET_INVALID")
        try:
            diagnosis = self._quality().inspect_reference_pack(self._reference_pack_root)
            manifest = _load_json(self._reference_pack_root / "manifest.reference.json")
            sample = self._sample_manifest(int(slot))
        except (ImportError, OSError, ValueError, RuntimeError):
            return _not_available("WORKFLOW_MODEL_EXAM_Quality_ASSETS_INVALID")
        if diagnosis.get("status") != "PASS" or manifest.get("status") != "FROZEN_EXECUTABLE_REFERENCE_PACK":
            return _not_available("WORKFLOW_MODEL_EXAM_Quality_PACK_NOT_ELIGIBLE")
        reference_pack_sha256 = _file_sha256(
            self._reference_pack_root / "manifest.reference.json"
        )
        scoring_protocol_sha256 = _file_sha256(
            self._reference_pack_root / "scoring" / "scoring_protocol.json"
        )
        comparison_basis = {
            "exam_category_id": "Quality_RERANKER_TEXT_REFERENCE_REGRESSION",
            "role_id": "RERANKER_TEXT",
            "reference_pack_sha256": reference_pack_sha256,
            "scoring_protocol_sha256": scoring_protocol_sha256,
            "scoring_projection_revision": self.SCORING_PROJECTION_REVISION,
            "sample_revision": self.SAMPLE_REVISION,
            "sample_manifest_sha256": sample["sample_manifest_sha256"],
        }
        comparison_cohort_id = "RERANKER_TEXT:" + _sha256(comparison_basis)
        plan: dict[str, Any] = {
            "schema_version": PLAN_SCHEMA,
            "status": "READY",
            "mode": self.MODE,
            "reason": "Quality_REFERENCE_REGRESSION_READY",
            "node_id": "context_pack",
            "role_id": "RERANKER_TEXT",
            "exam_category_id": comparison_basis["exam_category_id"],
            "executor_ref": self.EXECUTOR_REF,
            "profile_kind": profile_kind,
            "profile_ref": target["config_id"],
            "model_name": target["model_name"],
            "model_digest": target.get("model_digest"),
            "settings_revision": context.get("settings_revision"),
            "settings_sha256": context.get("settings_sha256"),
            "reference_pack_id": "MODEL-EVALUATION-MODEL_EVALUATION-RERANKER-TEXT-REFERENCE-EXAM-R0.1.1",
            "reference_pack_revision": manifest["pack_revision"],
            "reference_pack_sha256": reference_pack_sha256,
            "scoring_protocol_revision": "reranker_text_scoring_r0.1",
            "scoring_protocol_sha256": scoring_protocol_sha256,
            "scoring_projection_revision": comparison_basis[
                "scoring_projection_revision"
            ],
            "sample_slot": int(slot),
            "sample_revision": self.SAMPLE_REVISION,
            "sample_manifest_sha256": sample["sample_manifest_sha256"],
            "comparison_cohort_id": comparison_cohort_id,
            "comparison_binding_sha256": _comparison_binding_sha256(
                comparison_basis, comparison_cohort_id
            ),
            "horizontal_comparison_eligible": True,
            "horizontal_comparison_allowed_only_with_same_cohort": True,
            "cross_category_comparison_forbidden": True,
            "global_ranking_forbidden": True,
            "main_model_calls": self.MAIN_CALLS,
            "repeat_model_calls": self.REPEAT_CALLS,
            "transport_retry_budget": retry_budget,
            "maximum_model_calls": self.MODEL_CALLS + retry_budget,
            "full_quality_logical_calls": 188,
            "full_quality_cohort_reused_as_sample_cohort": False,
            "reference_regression_only": True,
            "blind_holdout_eligible": False,
            "qualification_eligible": False,
            "budget_cap_cny": self.API_BUDGET_CAP_CNY if profile_kind == "API" else None,
            "cost_semantics": (
                "FROZEN_Quality_RERANKER_INPUT_PRICE_CAP"
                if profile_kind == "API"
                else "LOCAL_COMPUTE_NO_PROVIDER_API_COST"
            ),
            "no_embedding_substitution": True,
            "authorization_schema_version": AUTHORIZATION_SCHEMA,
            "external_network_calls": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
        }
        plan["plan_sha256"] = _sha256(plan)
        return plan

    @staticmethod
    def _sample_score(
        *,
        tool: Any,
        selected: Mapping[str, Sequence[Mapping[str, Any]]],
        gold_by_item: Mapping[str, Mapping[str, Any]],
        rankings: Mapping[str, Sequence[str]],
        repeats: Mapping[str, Sequence[Sequence[str]]],
    ) -> dict[str, Any]:
        forms: dict[str, Any] = {}
        all_item_scores: list[dict[str, Any]] = []
        for form_id in ("A", "B"):
            per_form: list[dict[str, Any]] = []
            repeat_values: list[float] = []
            for item in selected[form_id]:
                item_id = str(item["item_id"])
                result = tool.item_score(list(rankings[item_id]), dict(gold_by_item[item_id]))
                result["form_id"] = form_id
                per_form.append(result)
                all_item_scores.append(result)
                for repeated in repeats.get(item_id, []):
                    repeat_values.append(tool.rank_similarity(list(rankings[item_id]), list(repeated)))
            family_means = {
                family_id: statistics.fmean(row["raw_score"] for row in per_form if row["family_id"] == family_id)
                for family_id in ("RR1", "RR2", "RR3", "RR4", "RR5", "RR6", "RR7")
            }
            hard = [row["raw_score"] for row in per_form if row["family_id"] != "RR7"]
            clean = [row["raw_score"] for row in per_form if row["family_id"] == "RR7"]
            bottom_count = max(1, math.ceil(len(per_form) * 0.10))
            components = {
                "overall_item_mean": statistics.fmean(row["raw_score"] for row in per_form),
                "hard_item_mean": statistics.fmean(hard),
                "worst_family_mean": min(family_means.values()),
                "bottom_decile_mean": statistics.fmean(sorted(row["raw_score"] for row in per_form)[:bottom_count]),
                "clean_control_mean": statistics.fmean(clean),
                "repeatability": statistics.fmean(repeat_values) * 100.0 if repeat_values else 100.0,
            }
            weights = {
                "overall_item_mean": 0.40,
                "hard_item_mean": 0.20,
                "worst_family_mean": 0.15,
                "bottom_decile_mean": 0.10,
                "clean_control_mean": 0.10,
                "repeatability": 0.05,
            }
            raw = sum(components[key] * weights[key] for key in weights)
            forms[form_id] = {
                "raw_score": round(raw, 6),
                "selection_score": round(tool.selection_score(raw), 6),
                "components": {key: round(value, 6) for key, value in components.items()},
                "family_means": {key: round(value, 6) for key, value in family_means.items()},
                "repeatability_observation_count": len(repeat_values),
            }
        overall = statistics.fmean(forms[form_id]["raw_score"] for form_id in ("A", "B"))
        return {
            "schema_version": "WorkflowRerankerSampleScore-v1",
            "status": "SCORED",
            "raw_score": round(overall, 6),
            "selection_score": round(tool.selection_score(overall), 6),
            "forms": forms,
            "item_scores": all_item_scores,
            "score_semantics": "Quality_RERANKER_FORMULA_ON_BALANCED_SUCCESSOR_SAMPLE",
            "full_quality_score_comparison_eligible": False,
        }

    def _result_extensions(
        self,
        *,
        plan: Mapping[str, Any],
        score_result: Mapping[str, Any],
    ) -> dict[str, Any]:
        del plan, score_result
        return {}

    def execute(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, Any],
        *,
        authorization: Mapping[str, Any] | None,
        structured_chat: Callable[..., Mapping[str, Any]],
        rerank_call: Callable[..., Mapping[str, Any]],
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
            sample = self._sample_manifest(int(plan["sample_slot"]))
            selected = sample.pop("_selected")
            if sample["sample_manifest_sha256"] != plan["sample_manifest_sha256"]:
                raise ValueError("RERANKER_SAMPLE_MANIFEST_DRIFT")
            _write_json_create_only(run_root / "sample_manifest.json", sample)
            rankings: dict[str, list[str]] = {}
            repeats: dict[str, list[list[str]]] = {}
            returned_model_evidence: list[str | None] = []
            identity_grades: list[str] = []
            physical_index = 0
            retry_budget_remaining = int(plan["transport_retry_budget"])
            item_by_id = {
                str(row["item_id"]): row
                for form_id in ("A", "B")
                for row in selected[form_id]
            }

            def run_item(item: Mapping[str, Any], *, repeat: bool, logical_index: int) -> list[str]:
                nonlocal physical_index, retry_budget_remaining
                candidates = item["candidates"]
                candidate_ids = [str(row["candidate_id"]) for row in candidates]
                documents = [str(row["text"]) for row in candidates]
                query = str(item["query"])
                while True:
                    physical_index += 1
                    claim = {
                        "schema_version": "WorkflowRerankerPreSendClaim-v2",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "physical_call_id": f"call-{physical_index:02d}",
                        "logical_index": logical_index,
                        "call_kind": (
                            "RERANKER_REPEAT_ITEM"
                            if repeat
                            else "RERANKER_MAIN_ITEM"
                        ),
                        "profile_kind": plan["profile_kind"],
                        "profile_ref": plan["profile_ref"],
                        "purpose": "workflow_model_exam",
                        "route": self._claim_route_for_target(
                            str(plan["profile_kind"]), target
                        ),
                        "region": (
                            ("PROVIDER_MANAGED_UNDISCLOSED" if target.get("adapter_id")=="command_template" else "OPENAI_MANAGED_UNDISCLOSED")
                            if plan["profile_kind"] == "CLI"
                            else "LOCAL_MACHINE"
                            if plan["profile_kind"] == "LOCAL"
                            else "PROVIDER_MANAGED_UNDISCLOSED"
                        ),
                        "egress": (
                            ("CLI_PROVIDER_MANAGED" if target.get("adapter_id")=="command_template" else "CODEX_CLI")
                            if plan["profile_kind"] == "CLI"
                            else "LOOPBACK_ONLY"
                            if plan["profile_kind"] == "LOCAL"
                            else "PROVIDER_API"
                        ),
                        "requested_model": requested_model,
                        "model_digest": target.get("model_digest"),
                        "catalog_model_identity_receipt_sha256": target.get(
                            "catalog_model_identity_receipt_sha256"
                        ),
                        "item_id": item["item_id"],
                        "repeat": repeat,
                        "query_sha256": hashlib.sha256(
                            query.encode("utf-8")
                        ).hexdigest().upper(),
                        "documents_exact_set_sha256": _sha256(documents),
                        "behavior_sha256": _sha256(
                            {
                                "model": requested_model,
                                "query": query,
                                "documents": documents,
                                "purpose": "workflow_model_exam",
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
                        rerank_call(
                            profile_kind=str(target["kind"]),
                            service=target,
                            model=target,
                            query=query,
                            documents=documents,
                            purpose="workflow_model_exam",
                            timeout_seconds=120,
                        )
                    )
                    self._consume_result(state, result)
                    _write_json_create_only(
                        run_root / "attempts" / f"call_{physical_index:02d}.json",
                        {
                            "schema_version": "WorkflowRerankerAttemptReceipt-v1",
                            "physical_index": physical_index,
                            "logical_index": logical_index,
                            "item_id": item["item_id"],
                            "repeat": repeat,
                            "status": result.get("status"),
                            "reason": result.get("reason"),
                            "requested_model": result.get("requested_model"),
                            "returned_model": result.get("returned_model"),
                            "physical_model_calls": int(
                                result.get("physical_model_calls")
                                if result.get("physical_model_calls") is not None
                                else result.get("external_model_calls") or 0
                            ),
                            "pre_send_claim_sha256": claim_receipt["sha256"],
                            "execution_receipt": deepcopy(
                                dict(result.get("execution_receipt") or {})
                            ),
                            "provider_received_gold": False,
                        },
                    )
                    if result.get("status") == "PASS":
                        break
                    failure_reason = str(
                        result.get("reason") or "RERANK_CALL_FAILED"
                    )
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
                    if not retryable or retry_budget_remaining <= 0:
                        raise ValueError(failure_reason)
                    retry_budget_remaining -= 1
                if not self._model_identity_accepted(
                    result, target, requested_model
                ):
                    raise ValueError("RERANK_MODEL_IDENTITY_MISMATCH")
                execution_receipt = result.get("execution_receipt")
                if not isinstance(execution_receipt, Mapping):
                    raise ValueError("RERANK_EXECUTION_RECEIPT_MISSING")
                returned_model_evidence.append(
                    result.get("returned_model")
                    if isinstance(result.get("returned_model"), str)
                    else None
                )
                identity_grades.append(
                    str(
                        execution_receipt.get("model_identity_grade")
                        or execution_receipt.get("model_identity_evidence")
                        or "UNKNOWN"
                    )
                )
                rows = result.get("results")
                if not isinstance(rows, list) or len(rows) != len(candidate_ids):
                    raise ValueError("RERANK_RESULT_COUNT_MISMATCH")
                ranking = [candidate_ids[int(row["index"])] for row in rows]
                if len(ranking) != len(set(ranking)) or set(ranking) != set(candidate_ids):
                    raise ValueError("RERANK_CANDIDATE_EXACT_SET_MISMATCH")
                _write_json_create_only(
                    run_root / "receipts" / f"call_{physical_index:02d}.json",
                    {
                        "schema_version": "WorkflowRerankCallReceipt-v1",
                        "physical_index": physical_index,
                        "logical_index": logical_index,
                        "item_id": item["item_id"],
                        "repeat": repeat,
                        "query_sha256": hashlib.sha256(str(item["query"]).encode("utf-8")).hexdigest().upper(),
                        "candidate_ids_sha256": _sha256(candidate_ids),
                        "ranking_sha256": _sha256(ranking),
                        "ranking_candidate_ids": ranking,
                        "pre_send_claim_sha256": claim_receipt["sha256"],
                        "provider_received_gold": False,
                        "execution_receipt": deepcopy(dict(execution_receipt)),
                    },
                )
                return ranking

            logical_index = 0
            for form_id in ("A", "B"):
                for item in selected[form_id]:
                    logical_index += 1
                    rankings[str(item["item_id"])] = run_item(item, repeat=False, logical_index=logical_index)
            for repeat_row in sample["repeat_items"]:
                logical_index += 1
                item_id = str(repeat_row["item_id"])
                repeats.setdefault(item_id, []).append(
                    run_item(item_by_id[item_id], repeat=True, logical_index=logical_index)
                )
            if (
                logical_index != self.MODEL_CALLS
                or not self.MODEL_CALLS
                <= state["physical_model_calls"]
                <= plan["maximum_model_calls"]
            ):
                raise ValueError("RERANKER_SAMPLE_CALL_COUNT_MISMATCH")
            frozen = {
                "schema_version": "WorkflowRerankerSampleRankings-v1",
                "sample_manifest_sha256": sample["sample_manifest_sha256"],
                "requested_model": requested_model,
                "returned_model_evidence": returned_model_evidence,
                "model_identity_grades": identity_grades,
                "rankings": rankings,
                "repeats": repeats,
                "gold_sent_to_provider": False,
            }
            frozen_receipt = _write_json_create_only(run_root / "rankings_frozen_before_gold.json", frozen)
            gold_by_item: dict[str, Mapping[str, Any]] = {}
            for form_id in ("A", "B"):
                gold = _load_json(self._reference_pack_root / "gold" / f"form_{form_id}_gold.json")
                gold_by_item.update({str(row["item_id"]): row for row in gold["items"]})
            tool = self._quality()._load_bundled_tool()
            score_result = self._sample_score(
                tool=tool,
                selected=selected,
                gold_by_item=gold_by_item,
                rankings=rankings,
                repeats=repeats,
            )
            _write_json_create_only(run_root / "sample_score.json", score_result)
            cap = plan.get("budget_cap_cny")
            if cap is not None and state["estimated_cost_cny"] > float(cap):
                raise ValueError("RERANKER_COST_CAP_EXCEEDED")
            public_result = {
                "schema_version": "WorkflowModelExamRunResult-v2",
                "status": "PASS",
                "reason": "Quality_RERANKER_BALANCED_SAMPLE_REFERENCE_REGRESSION_COMPLETED",
                "execution_outcome": "COMPLETED",
                "scorability": "SCOREABLE",
                "run_id": run_id,
                "plan_sha256": plan["plan_sha256"],
                "requested_model": requested_model,
                "returned_model": (
                    requested_model
                    if returned_model_evidence
                    and all(value == requested_model for value in returned_model_evidence)
                    else None
                ),
                "model_identity_grade": (
                    identity_grades[0]
                    if identity_grades
                    and len(set(identity_grades)) == 1
                    else "MIXED"
                ),
                "score": int(round(float(score_result["selection_score"]))),
                "score_exact": score_result["selection_score"],
                "raw_score": score_result["raw_score"],
                "quality_verdict": "NOT_ASSESSED",
                "acceptance_verdict": "NOT_ASSESSED",
                "quality_threshold_defined": False,
                "score_is_descriptive_within_category": True,
                "form_scores": {
                    form_id: score_result["forms"][form_id]["selection_score"]
                    for form_id in ("A", "B")
                },
                "sample_slot": plan["sample_slot"],
                "sample_revision": plan["sample_revision"],
                "sample_manifest_sha256": plan["sample_manifest_sha256"],
                "exam_category_id": plan["exam_category_id"],
                "role_id": plan["role_id"],
                "comparison_cohort_id": plan["comparison_cohort_id"],
                "comparison_binding_sha256": plan[
                    "comparison_binding_sha256"
                ],
                "horizontal_comparison_eligible": True,
                "horizontal_comparison_allowed_only_with_same_cohort": True,
                "failed_exam_item_count": 0,
                "failed_exam_item_count_semantics": (
                    "NO_ADMISSION_THRESHOLD_DEFINED"
                ),
                "semantic_repair_rounds": 0,
                **state,
                "estimated_cost_cny": (
                    None
                    if plan["profile_kind"] == "CLI"
                    else round(float(state["estimated_cost_cny"]), 9)
                ),
                "actual_cost": None,
                "cost_semantics": plan["cost_semantics"],
                "reranker_adapter_kind": plan.get(
                    "reranker_adapter_kind", "DEDICATED_RERANK_ENDPOINT_V1"
                ),
                "dedicated_reranker_identity_claimed": plan.get(
                    "dedicated_reranker_identity_claimed", True
                ),
                "reference_regression_only": True,
                "blind_holdout_eligible": False,
                "qualification_eligible": False,
                "provider_received_answer_key": False,
                "provider_received_gold": False,
                "rankings_frozen_before_gold_sha256": frozen_receipt["sha256"],
                "full_quality_score_comparison_eligible": False,
                "cross_category_comparison_forbidden": True,
                "global_ranking_forbidden": True,
                **self._result_extensions(plan=plan, score_result=score_result),
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
