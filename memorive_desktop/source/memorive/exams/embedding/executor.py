"""QualityEmbeddingExamExecutor: plans, runs and scores the reference embedding exam.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import importlib
import importlib.util
from pathlib import Path
from time import monotonic
from typing import Any, Callable, Mapping
import uuid
from memorive.exams.outcomes import classify_execution_outcome
from memorive.exams.common import (
    AUTHORIZATION_SCHEMA,
    EXECUTOR_REF,
    PLAN_SCHEMA,
    RESULT_SCHEMA,
    SAFE_PACKAGE,
    _compact,
    _file_sha256,
    _load_json,
    _not_available,
    _not_run,
    _sha256,
    _write_json_create_only,
    _comparison_binding_sha256,
    _ollama_exam_profile,
    _plan_authorization_reason,
)


class QualityEmbeddingExamExecutor:
    """Run the frozen Quality embedding ranking exam through API or local."""

    EXECUTOR_REF = "Desktop_MODEL_EXAM_SUCCESSOR_Quality_EMBEDDING_EXECUTOR_V2"
    MODE = "LIVE_Quality_EMBEDDING_REFERENCE_REGRESSION"
    LOGICAL_CORPUS_CALLS = 240
    CORPORA_PER_MODEL_REQUEST = 5
    INPUTS_PER_CORPUS = 49
    INPUTS_PER_MODEL_REQUEST = CORPORA_PER_MODEL_REQUEST * INPUTS_PER_CORPUS
    PHYSICAL_MODEL_CALLS = 48
    API_BUDGET_CAP_CNY = 0.40
    SAMPLE_REVISION = "FULL_Quality_AB_240_CORPUS_V1"
    SCORING_PROJECTION_REVISION = "Desktop_EMBEDDING_ORDER_PRESERVING_BATCH_V1"
    BATCH_EQUIVALENCE_REVISION = (
        "Desktop_EMBEDDING_FIVE_CORPUS_ORDER_PRESERVING_BATCH_V1"
    )

    def __init__(
        self,
        *,
        scratch_root: Path,
        quality_package: str,
        reference_pack_root: Path,
    ) -> None:
        if not isinstance(quality_package, str) or not SAFE_PACKAGE.fullmatch(
            quality_package
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_Quality_PACKAGE_INVALID")
        self.scratch_root = Path(scratch_root).resolve()
        self._quality_package = quality_package
        self._reference_pack_root = Path(reference_pack_root).resolve()
        self._modules: dict[str, Any] | None = None

    def _quality(self) -> dict[str, Any]:
        if self._modules is None:
            self._modules = {
                "exam": importlib.import_module(
                    f"{self._quality_package}.embedding_text_exam"
                )
            }
        return self._modules

    def _embedding_diagnostics_for_scores(
        self,
        scores: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any] | None:
        """Successors may expose scorer-native detail without changing r0.2."""

        del scores
        return None

    @staticmethod
    def _profile_kind(target: Mapping[str, Any]) -> str | None:
        if target.get("kind") == "API":
            if (
                "siliconflow" in _compact(target.get("provider"))
                and target.get("model_name") == "Pro/BAAI/bge-m3"
                and target.get("connection_status") == "AVAILABLE"
                and isinstance(target.get("config_id"), str)
                and bool(target.get("config_id"))
            ):
                return "API"
            return None
        if target.get("kind") == "LOCAL":
            if _ollama_exam_profile(target, "capability", "EMBEDDING"):
                return "LOCAL"
        return None

    def plan(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        if node.get("node_id") != "chunk_embedding":
            return _not_available("WORKFLOW_MODEL_EXAM_DYNAMIC_NODE_UNSUPPORTED")
        profile_kind = self._profile_kind(target)
        if profile_kind is None:
            return _not_available("WORKFLOW_MODEL_EXAM_DYNAMIC_PROFILE_UNSUPPORTED")
        try:
            exam = self._quality()["exam"]
            diagnosis = exam.inspect_reference_pack(self._reference_pack_root)
            scorer = exam.load_bundled_scorer(self._reference_pack_root)
            replay = scorer.verify_reference_pack(self._reference_pack_root)
        except (ImportError, OSError, ValueError, RuntimeError):
            return _not_available("WORKFLOW_MODEL_EXAM_Quality_ASSETS_INVALID")
        if (
            diagnosis.get("status") != "PASS"
            or replay.get("verification_result") != "PASS"
        ):
            return _not_available("WORKFLOW_MODEL_EXAM_Quality_PACK_NOT_ELIGIBLE")
        manifest = _load_json(
            self._reference_pack_root / "manifest.reference.json"
        )
        reference_pack_sha256 = _file_sha256(
            self._reference_pack_root / "SHA256SUMS.txt"
        )
        scoring_protocol_sha256 = _file_sha256(
            self._reference_pack_root / "scoring" / "scoring_protocol.json"
        )
        comparison_basis = {
            "exam_category_id": "Quality_EMBEDDING_TEXT_REFERENCE_REGRESSION",
            "role_id": "EMBEDDING_TEXT",
            "reference_pack_sha256": reference_pack_sha256,
            "scoring_protocol_sha256": scoring_protocol_sha256,
            "scoring_projection_revision": self.SCORING_PROJECTION_REVISION,
            "sample_revision": self.SAMPLE_REVISION,
        }
        comparison_cohort_id = (
            "EMBEDDING_TEXT:" + _sha256(comparison_basis)
        )
        plan: dict[str, Any] = {
            "schema_version": PLAN_SCHEMA,
            "status": "READY",
            "mode": self.MODE,
            "reason": "Quality_REFERENCE_REGRESSION_READY",
            "node_id": "chunk_embedding",
            "executor_ref": self.EXECUTOR_REF,
            "profile_kind": profile_kind,
            "profile_ref": target["config_id"],
            "model_name": target["model_name"],
            "model_digest": target.get("model_digest"),
            "settings_revision": context.get("settings_revision"),
            "settings_sha256": context.get("settings_sha256"),
            "reference_pack_id": manifest.get("pack_id"),
            "reference_pack_revision": manifest.get("pack_revision"),
            "reference_pack_sha256": reference_pack_sha256,
            "scoring_protocol_sha256": scoring_protocol_sha256,
            "scoring_projection_revision": comparison_basis[
                "scoring_projection_revision"
            ],
            "exam_category_id": comparison_basis["exam_category_id"],
            "role_id": comparison_basis["role_id"],
            "sample_revision": comparison_basis["sample_revision"],
            "comparison_cohort_id": comparison_cohort_id,
            "comparison_binding_sha256": _comparison_binding_sha256(
                comparison_basis, comparison_cohort_id
            ),
            "horizontal_comparison_eligible": True,
            "horizontal_comparison_allowed_only_with_same_cohort": True,
            "cross_category_comparison_forbidden": True,
            "global_ranking_forbidden": True,
            "reference_regression_only": True,
            "blind_holdout_eligible": False,
            "qualification_eligible": False,
            "forms": ["A", "B"],
            "logical_corpus_calls": self.LOGICAL_CORPUS_CALLS,
            "maximum_model_calls": self.PHYSICAL_MODEL_CALLS,
            "corpora_per_model_request": self.CORPORA_PER_MODEL_REQUEST,
            "inputs_per_corpus": self.INPUTS_PER_CORPUS,
            "maximum_inputs_per_model_request": self.INPUTS_PER_MODEL_REQUEST,
            "batch_equivalence_revision": self.BATCH_EQUIVALENCE_REVISION,
            "budget_cap_cny": (
                self.API_BUDGET_CAP_CNY if profile_kind == "API" else None
            ),
            "cost_semantics": (
                "FROZEN_Quality_EMBEDDING_PRICE_CAP"
                if profile_kind == "API"
                else "LOCAL_COMPUTE_NO_PROVIDER_API_COST"
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
        """No embedding plan runs on a subscription."""
        return _plan_authorization_reason(authorization, plan, subscription=False)

    def execute(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, Any],
        *,
        authorization: Mapping[str, Any] | None,
        structured_chat: Callable[..., Mapping[str, Any]],
        embedding_call: Callable[..., Mapping[str, Any]],
    ) -> dict[str, Any]:
        del structured_chat
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
        state = {
            "request_attempts": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
            "external_network_calls": 0,
            "external_process_launches": 0,
            "estimated_cost_cny": 0.0,
            "token_usage": {"prompt_tokens": 0, "completion_tokens": 0},
        }
        try:
            exam = self._quality()["exam"]
            scorer = exam.load_bundled_scorer(self._reference_pack_root)
            scores: dict[str, dict[str, Any]] = {}
            dimension: int | None = None
            logical_call_index = 0
            for form_id in ("A", "B"):
                tasks = _load_json(
                    self._reference_pack_root / "forms" / f"form_{form_id}.json"
                )["tasks"]
                corpora = _load_json(
                    self._reference_pack_root
                    / "corpora"
                    / f"form_{form_id}_corpora.json"
                )["corpora"]
                gold_rows = _load_json(
                    self._reference_pack_root
                    / "gold"
                    / f"form_{form_id}_gold.json"
                )["gold"]
                tasks_by_corpus: dict[str, list[Mapping[str, Any]]] = {}
                for task in tasks:
                    tasks_by_corpus.setdefault(str(task["corpus_id"]), []).append(task)
                rankings: dict[str, list[str]] = {}
                work_rows: list[dict[str, Any]] = []
                for corpus_index, corpus in enumerate(corpora, start=1):
                    corpus_tasks = tasks_by_corpus[str(corpus["corpus_id"])]
                    documents = corpus["documents"]
                    inputs = [row["text"] for row in documents] + [
                        row["query"] for row in corpus_tasks
                    ]
                    if len(inputs) != self.INPUTS_PER_CORPUS:
                        raise ValueError(
                            "WORKFLOW_MODEL_EXAM_EMBEDDING_CORPUS_INPUT_COUNT_MISMATCH"
                        )
                    work_rows.append(
                        {
                            "corpus_index": corpus_index,
                            "corpus": corpus,
                            "tasks": corpus_tasks,
                            "documents": documents,
                            "inputs": inputs,
                        }
                    )
                for batch_start in range(
                    0, len(work_rows), self.CORPORA_PER_MODEL_REQUEST
                ):
                    batch_rows = work_rows[
                        batch_start : batch_start + self.CORPORA_PER_MODEL_REQUEST
                    ]
                    inputs = [
                        value
                        for work in batch_rows
                        for value in work["inputs"]
                    ]
                    logical_call_index += 1
                    claim = {
                        "schema_version": "WorkflowEmbeddingPreSendClaim-v1",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "call_id": f"call-{logical_call_index:02d}",
                        "call_kind": "EMBEDDING_CORPUS_BATCH",
                        "profile_kind": plan["profile_kind"],
                        "profile_ref": plan["profile_ref"],
                        "purpose": "workflow_model_exam",
                        "route": (
                            "EXISTING_SETTINGS_API_TRANSPORT"
                            if plan["profile_kind"] == "API"
                            else "OLLAMA_LOOPBACK"
                        ),
                        "requested_model": requested_model,
                        "model_digest": target.get("model_digest"),
                        "form_id": form_id,
                        "input_count": len(inputs),
                        "input_exact_set_sha256": _sha256(inputs),
                        "behavior_sha256": _sha256(
                            {
                                "model": requested_model,
                                "inputs": inputs,
                                "purpose": "workflow_model_exam",
                            }
                        ),
                        "plan_sha256": plan["plan_sha256"],
                        "provider_received_gold": False,
                    }
                    claim_receipt = _write_json_create_only(
                        run_root
                        / "claims"
                        / f"call_{logical_call_index:02d}.json",
                        claim,
                    )
                    result = dict(
                        embedding_call(
                            profile_kind=str(target["kind"]),
                            service=target,
                            model=target,
                            inputs=inputs,
                            purpose="workflow_model_exam",
                            timeout_seconds=120,
                        )
                    )
                    for field in (
                        "provider_calls",
                        "external_model_calls",
                        "external_network_calls",
                        "external_process_launches",
                    ):
                        state[field] += int(result.get(field) or 0)
                    state["request_attempts"] += int(
                        result.get("external_model_calls") or 0
                    )
                    receipt = result.get("execution_receipt")
                    if isinstance(receipt, Mapping):
                        estimate = receipt.get("estimated_cost_cny")
                        if isinstance(estimate, (int, float)) and not isinstance(estimate, bool):
                            state["estimated_cost_cny"] += float(estimate)
                        usage = receipt.get("token_usage")
                        if isinstance(usage, Mapping):
                            for field in ("prompt_tokens", "completion_tokens"):
                                value = usage.get(field)
                                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                                    state["token_usage"][field] = state["token_usage"][field] + value if state["token_usage"][field] is not None else None
                    if result.get("status") != "PASS":
                        raise ValueError(
                            str(result.get("reason") or "EMBEDDING_CALL_FAILED")
                        )
                    vectors = result.get("vectors")
                    if (
                        not isinstance(vectors, list)
                        or len(vectors) != len(inputs)
                    ):
                        raise ValueError("EMBEDDING_VECTORS_MISSING")
                    observed_dimension = scorer.validate_vectors(
                        vectors,
                        len(inputs),
                        expected_dim=(
                            1024
                            if str(target["kind"]) == "API"
                            else dimension
                        ),
                    )
                    if dimension is None:
                        dimension = observed_dimension
                    elif dimension != observed_dimension:
                        raise ValueError("EMBEDDING_DIMENSION_DRIFT")
                    vector_offset = 0
                    batch_corpus_receipts: list[dict[str, Any]] = []
                    for work in batch_rows:
                        corpus_vectors = vectors[
                            vector_offset : vector_offset + self.INPUTS_PER_CORPUS
                        ]
                        vector_offset += self.INPUTS_PER_CORPUS
                        documents = work["documents"]
                        corpus_tasks = work["tasks"]
                        doc_vectors = corpus_vectors[: len(documents)]
                        query_vectors = corpus_vectors[len(documents) :]
                        doc_ids = [row["unit_id"] for row in documents]
                        for task, query_vector in zip(
                            corpus_tasks, query_vectors
                        ):
                            cosine_rows, _ = scorer.rank_vectors(
                                doc_ids, doc_vectors, query_vector
                            )
                            rankings[str(task["task_id"])] = [
                                str(row["unit_id"]) for row in cosine_rows
                            ]
                        batch_corpus_receipts.append(
                            {
                                "corpus_index": work["corpus_index"],
                                "corpus_id": work["corpus"]["corpus_id"],
                                "input_count": self.INPUTS_PER_CORPUS,
                            }
                        )
                    _write_json_create_only(
                        run_root
                        / "receipts"
                        / (
                            f"form_{form_id}_corpus_batch_"
                            f"{batch_start // self.CORPORA_PER_MODEL_REQUEST + 1:03d}.json"
                        ),
                        {
                            "schema_version": "WorkflowEmbeddingCorpusBatchReceipt-v2",
                            "form": form_id,
                            "corpora": batch_corpus_receipts,
                            "input_count": len(inputs),
                            "physical_model_calls": result[
                                "external_model_calls"
                            ],
                            "pre_send_claim_sha256": claim_receipt["sha256"],
                            "order_preserving_batch_equivalent": True,
                            "execution_receipt": deepcopy(dict(receipt or {})),
                        },
                    )
                scores[form_id] = scorer.score_form(gold_rows, rankings)
                _write_json_create_only(
                    run_root / "scores" / f"form_{form_id}.json",
                    scores[form_id],
                )
            if (
                logical_call_index != self.PHYSICAL_MODEL_CALLS
                or state["request_attempts"] != self.PHYSICAL_MODEL_CALLS
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_CALL_COUNT_MISMATCH")
            mean = (
                float(scores["A"]["score"]) + float(scores["B"]["score"])
            ) / 2.0
            critical = int(scores["A"]["critical_miss_count"]) + int(
                scores["B"]["critical_miss_count"]
            )
            score = int(round(mean))
            embedding_diagnostics = self._embedding_diagnostics_for_scores(scores)
            cap = plan.get("budget_cap_cny")
            if cap is not None and state["estimated_cost_cny"] > float(cap):
                raise ValueError("WORKFLOW_MODEL_EXAM_COST_CAP_EXCEEDED")
            public_result = {
                "schema_version": "WorkflowModelExamRunResult-v1",
                "status": "PASS",
                "execution_outcome": "COMPLETED",
                "scorability": "SCOREABLE",
                "raw_quality_candidate_verdict": "NOT_ASSESSED",
                "reason": (
                    "Quality_EMBEDDING_REFERENCE_REGRESSION_"
                    "TECHNICALLY_COMPLETED"
                ),
                "run_id": run_id,
                "plan_sha256": plan["plan_sha256"],
                "requested_model": requested_model,
                "returned_model": state.get("returned_model") if target.get("adapter_id")=="command_template" else requested_model,
                "score": score,
                "score_exact": round(mean, 6),
                "quality_verdict": "NOT_ASSESSED",
                "acceptance_verdict": "NOT_ASSESSED",
                "quality_threshold_defined": False,
                "score_is_descriptive_within_category": True,
                "form_scores": {
                    "A": scores["A"]["score"],
                    "B": scores["B"]["score"],
                },
                "exam_category_id": plan["exam_category_id"],
                "role_id": plan["role_id"],
                "sample_revision": plan["sample_revision"],
                "comparison_cohort_id": plan["comparison_cohort_id"],
                "comparison_binding_sha256": plan[
                    "comparison_binding_sha256"
                ],
                "horizontal_comparison_eligible": True,
                "horizontal_comparison_allowed_only_with_same_cohort": True,
                "cross_category_comparison_forbidden": True,
                "global_ranking_forbidden": True,
                "failed_exam_item_count": critical,
                "critical_miss_count": critical,
                "failed_exam_item_count_semantics": (
                    "DIAGNOSTIC_CRITICAL_MISS_COUNT_NOT_ADMISSION_FAIL"
                ),
                "dimension": dimension,
                **state,
                "estimated_cost_cny": (
                    round(state["estimated_cost_cny"], 9)
                    if plan["profile_kind"] == "API"
                    else 0.0
                ),
                "actual_cost": None,
                "cost_semantics": plan["cost_semantics"],
                "reference_regression_only": True,
                "blind_holdout_eligible": False,
                "qualification_eligible": False,
                "provider_received_answer_key": False,
            }
            if embedding_diagnostics is not None:
                public_result["embedding_diagnostics"] = embedding_diagnostics
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
            reason = str(error) or type(error).__name__
            if len(reason) > 200:
                reason = type(error).__name__
            failure = {
                "schema_version": "WorkflowModelExamRunFailure-v1",
                "status": "NOT_ASSESSED",
                "execution_outcome": classify_execution_outcome(
                    {"status": "ERROR", "reason": reason}
                ).value,
                "scorability": "NOT_ASSESSED",
                "quality_verdict": "NOT_ASSESSED",
                "reason": reason,
                "run_id": run_id,
                "plan_sha256": plan["plan_sha256"],
                **state,
                "estimated_cost_cny": (
                    round(state["estimated_cost_cny"], 9)
                    if plan["profile_kind"] == "API"
                    else 0.0
                ),
                "score": None,
            }
            evidence = _write_json_create_only(
                run_root / "exam_failure.json", failure
            )
            result = _not_run(reason, requested_model=requested_model)
            projected_state = {
                **state,
                "estimated_cost_cny": failure["estimated_cost_cny"],
            }
            result.update(
                duration_ms=max(0, int((monotonic() - started) * 1000)),
                execution_outcome=failure["execution_outcome"],
                scorability="NOT_ASSESSED",
                quality_verdict="NOT_ASSESSED",
                exam_run_id=run_id,
                exam_run_root=str(run_root.resolve()),
                exam_evidence_sha256=evidence["sha256"],
                cost_semantics=plan["cost_semantics"],
                **projected_state,
            )
            return result
