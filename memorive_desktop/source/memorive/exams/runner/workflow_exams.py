"""WorkflowExams: the exam catalog, planning and running a node's exam, and the stored scores;
FailClosedWorkflowExams answers every call as unavailable.
"""
from __future__ import annotations

import hashlib
from inspect import signature
import json
from typing import (
    Any,
    Callable,
    Mapping,
    Sequence,
)
from memorive.models.endpoints import (
    SAFE_MODEL,
    SAFE_REASON_CODE,
    SAFE_SHA256,
    _api_profile,
)
from memorive.exams.score_history import ExamScoreHistory
from memorive.models.validation.runner import (
    FailClosedModelValidationRunner,
    default_executable_resolver,
    validation_result,
)
from memorive.exams.runner.metadata import WORKFLOW_EXAM_SCORE_METADATA_FIELDS
from memorive.exams.runner.catalog_validation import validate_workflow_exam_catalog


class WorkflowExams:
    """The workflow exams: the exam catalog, planning and running the exam of one workflow node, and
    the stored scores (SettingsController.test_workflow_node and the exam jobs call it).

    runner is the model runner (LiveModelValidationRunner): the exam's requests go through its
    execute_* methods, and its scratch_root holds the score history. executable_resolver finds the
    command-line tool for the OCR exam; local_profile_resolver turns a local:... profile into the
    local model it names. catalog holds scores of earlier versions (None for new installations);
    executors are the exam executors, each with plan() and execute()."""

    _base = staticmethod(validation_result)

    def __init__(
        self,
        *,
        runner: Any,
        executable_resolver: Callable[[str], str | None] = default_executable_resolver,
        local_profile_resolver: Callable[[str], Mapping[str, Any] | None] | None = None,
        catalog: Mapping[str, Any] | None = None,
        executors: Sequence[Any] = (),
    ):
        self.runner = runner
        self.executable_resolver = executable_resolver
        self.local_profile_resolver = local_profile_resolver
        self._exam_score_history = ExamScoreHistory(runner.scratch_root / "workflow_exam_scores")
        (
            self._workflow_exam_entries,
            self._workflow_exam_historical_entries,
        ) = validate_workflow_exam_catalog(catalog)
        self._workflow_exam_executors = tuple(executors)
        if any(
            not callable(getattr(executor, "plan", None))
            or not callable(getattr(executor, "execute", None))
            for executor in self._workflow_exam_executors
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_EXECUTOR_INVALID")

    def _resolve_exam_target(self, target: Mapping[str, Any]) -> dict[str, Any]:
        accepted = dict(target)
        if accepted.get("kind") != "BUILTIN_PROFILE":
            return accepted
        profile_ref = accepted.get("profile_ref")
        if (
            not isinstance(profile_ref, str)
            or not profile_ref.startswith("local:")
            or self.local_profile_resolver is None
        ):
            return accepted
        try:
            projection = self.local_profile_resolver(profile_ref)
        except Exception:
            return accepted
        if not isinstance(projection, Mapping):
            return accepted
        row = dict(projection)
        if (
            row.get("profile_ref") != profile_ref
            or row.get("endpoint_kind") != "ollama"
            or row.get("structured_chat_adapter")
            != "EvaluationAssets_LOCAL_STRUCTURED_CHAT_V1"
            or row.get("execution_eligible") is not True
            or row.get("exact_identity_available") is not True
            or not isinstance(row.get("chat_endpoint"), str)
            or not isinstance(row.get("model_name"), str)
            or not SAFE_MODEL.fullmatch(str(row["model_name"]))
            or not isinstance(row.get("model_digest"), str)
            or not SAFE_SHA256.fullmatch(str(row["model_digest"]).upper())
        ):
            return accepted
        return {
            **row,
            "kind": "LOCAL",
            "config_id": profile_ref,
            "profile_ref": profile_ref,
            "model_digest": str(row["model_digest"]).upper(),
            "connection_status": "AVAILABLE",
        }

    def _resolve_ocr_exam_target(self, node, target):
        accepted = self._resolve_exam_target(target)
        if (node.get('node_id') != 'ingest' or accepted.get('kind') != 'CLI'
                or accepted.get('adapter_id') != 'codex_cli'
                or accepted.get('connection_status') != 'AVAILABLE'):
            return accepted
        from memorive.models.cli.image_identity import bind_saved_cli_identity, FIELDS
        if any(key in accepted for key in FIELDS):
            return accepted
        executable = self.executable_resolver(accepted.get('executable'))
        if executable is None:
            return accepted
        proxy_mode, proxy_address, _ = self.runner.preferences()
        self.runner.scratch_root.mkdir(parents=True, exist_ok=True)
        accepted, _ = bind_saved_cli_identity(accepted, executable,
            process=self.runner.process_transport,
            environment=self.runner.safe_environment('codex_cli', self.runner.scratch_root,
                                              proxy_mode, proxy_address),
            root=self.runner.scratch_root)
        return accepted

    def _frozen_workflow_exam_row(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        active_executor_refs = {
            str(getattr(executor, "EXECUTOR_REF"))
            for executor in self._workflow_exam_executors
            if isinstance(getattr(executor, "EXECUTOR_REF", None), str)
        }
        recorded = self._exam_score_history.lookup(node, target, active_executor_refs)
        if recorded is None:
            recorded = self._exam_score_history.recover_completed_embedding(node, target, active_executor_refs)
        if recorded is not None:
            return recorded
        if target.get("adapter_id")=="command_template":
            return None

        def reusable(candidate: Mapping[str, Any]) -> bool:
            if candidate.get(
                "cache_reuse_status", "STABLE_REUSE"
            ) != "STABLE_REUSE":
                return False
            if candidate.get("cache_reuse_requires_executor_ref_match") is True:
                return candidate.get("executor_ref") in active_executor_refs
            return True

        if target.get("kind") == "LOCAL":
            endpoint_kind = target.get("endpoint_kind")
            model_name = target.get("model_name")
            model_digest = target.get("model_digest")
            if (
                endpoint_kind != "ollama"
                or not isinstance(model_name, str)
                or not isinstance(model_digest, str)
                or not SAFE_SHA256.fullmatch(model_digest.upper())
            ):
                return None
            identity = (
                node.get("node_id"),
                "LOCAL",
                endpoint_kind,
                model_name,
                model_digest.upper(),
            )
            return next(
                (
                    candidate
                    for candidate in self._workflow_exam_entries
                    if reusable(candidate)
                    and candidate.get("profile_kind") == "LOCAL"
                    and (
                        candidate["node_id"],
                        "LOCAL",
                        candidate["endpoint_kind"],
                        candidate["model_name"],
                        candidate["model_digest"],
                    )
                    == identity
                ),
                None,
            )
        if target.get("kind") == "CLI":
            adapter_id = target.get("adapter_id")
            model_name = target.get("model_name")
            thinking_mode = target.get("thinking_mode")
            if not all(
                isinstance(value, str) and bool(value)
                for value in (adapter_id, model_name, thinking_mode)
            ):
                return None
            identity = (
                node.get("node_id"),
                "CLI",
                adapter_id,
                model_name,
                thinking_mode,
            )
            return next(
                (
                    candidate
                    for candidate in self._workflow_exam_entries
                    if reusable(candidate)
                    and candidate.get("profile_kind") == "CLI"
                    and (
                        candidate["node_id"],
                        "CLI",
                        candidate["adapter_id"],
                        candidate["model_name"],
                        candidate["thinking_mode"],
                    )
                    == identity
                ),
                None,
            )
        if target.get("kind") != "API":
            return None
        try:
            api_profile = _api_profile(target)
        except ValueError:
            return None
        if api_profile is None:
            return None
        model_name = target.get("model_name")
        thinking = target.get("thinking")
        tier = target.get("tier")
        if (
            not isinstance(model_name, str)
            or not isinstance(thinking, bool)
            or not isinstance(tier, str)
        ):
            return None
        identity = (
            node.get("node_id"),
            "API",
            api_profile.provider_family,
            model_name,
            thinking,
            tier,
        )
        row = next(
            (
                candidate
                for candidate in self._workflow_exam_entries
                if reusable(candidate)
                and candidate.get("profile_kind") == "API"
                and (
                    candidate["node_id"],
                    candidate["profile_kind"],
                    candidate["provider_family"],
                    candidate["model_name"],
                    candidate["thinking"],
                    candidate["tier"],
                )
                == identity
            ),
            None,
        )
        return row

    def workflow_exam_score_catalog(self) -> Mapping[str, Any]:
        return {
            "schema_version": "WorkflowModelExamScoreCatalogProjection-v2",
            "horizontal_comparison_allowed_only_with_same_cohort": True,
            "cross_category_comparison_forbidden": True,
            "global_ranking_forbidden": True,
            "entries": [dict(row) for row in self._workflow_exam_entries],
            "historical_entries": [
                dict(row) for row in self._workflow_exam_historical_entries
            ],
        }

    def cached_workflow_node_score(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        resolved_target = self._resolve_exam_target(target)
        row = self._frozen_workflow_exam_row(node, resolved_target)
        return dict(row) if row is not None else None

    @staticmethod
    def _plan_hash(plan: Mapping[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(
                plan,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest().upper()

    def plan_workflow_node_exam(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        resolved_target = self._resolve_ocr_exam_target(node, target)
        row = None if (context or {}).get("workflow_exam_force_live") is True else self._frozen_workflow_exam_row(node, resolved_target)
        if row is not None:
            plan: dict[str, Any] = {
                "schema_version": "WorkflowModelExamPlan-v2",
                "status": "READY",
                "mode": "FROZEN_MODEL_EXAM_SCORE_REUSE",
                "reason": "FROZEN_MODEL_EXAM_SCORE_AVAILABLE",
                "node_id": node.get("node_id"),
                "model_name": resolved_target.get("model_name"),
                "maximum_model_calls": 0,
                "budget_cap_cny": 0.0,
                "reference_regression_only": True,
                "qualification_eligible": False,
                "exam_evidence_sha256": row["evidence_sha256"],
                "authorization_required": False,
                "external_network_calls": 0,
                "provider_calls": 0,
                "external_model_calls": 0,
            }
            plan.update(
                {
                    field: row[field]
                    for field in WORKFLOW_EXAM_SCORE_METADATA_FIELDS
                    if field in row
                }
            )
            plan["plan_sha256"] = self._plan_hash(plan)
            return plan
        accepted_context = dict(context or {})
        executor_plan_diagnostics: list[dict[str, str]] = []
        from memorive.exams.tiered import tier_executor
        for original_executor in self._workflow_exam_executors:
            executor = tier_executor(original_executor, accepted_context)
            executor_ref = getattr(
                executor, "EXECUTOR_REF", type(executor).__name__
            )
            if not isinstance(executor_ref, str) or not SAFE_MODEL.fullmatch(
                executor_ref
            ):
                executor_ref = type(executor).__name__
            try:
                plan = executor.plan(node, resolved_target, accepted_context)
            except Exception as exc:
                reason = type(exc).__name__
                missing_module = getattr(exc, "name", None)
                if isinstance(missing_module, str) and SAFE_MODEL.fullmatch(
                    missing_module
                ):
                    reason = f"{reason}:{missing_module}"
                elif (
                    exc.args
                    and isinstance(exc.args[0], str)
                    and SAFE_REASON_CODE.fullmatch(exc.args[0])
                ):
                    reason = exc.args[0]
                executor_plan_diagnostics.append(
                    {
                        "executor_ref": executor_ref,
                        "status": "ERROR",
                        "reason": reason,
                    }
                )
                continue
            if isinstance(plan, Mapping) and plan.get("status") == "READY":
                return dict(plan)
            plan_reason = (
                plan.get("reason") if isinstance(plan, Mapping) else None
            )
            executor_plan_diagnostics.append(
                {
                    "executor_ref": executor_ref,
                    "status": "NOT_READY",
                    "reason": (
                        plan_reason
                        if isinstance(plan_reason, str)
                        and SAFE_REASON_CODE.fullmatch(plan_reason)
                        else "WORKFLOW_NODE_EXAM_EXECUTOR_PLAN_NOT_READY"
                    ),
                }
            )
        return {
            "schema_version": "WorkflowModelExamPlan-v2",
            "status": "NOT_AVAILABLE",
            "mode": "NONE",
            "reason": "WORKFLOW_NODE_EXAM_SCORE_NOT_AVAILABLE",
            "executor_plan_diagnostics": executor_plan_diagnostics,
            "external_network_calls": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
        }

    def test_workflow_node(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        *,
        context: Mapping[str, Any] | None = None,
        authorization: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        result = self._base(
            "WORKFLOW_NODE",
            status="NOT_RUN",
            reason="WORKFLOW_NODE_EXAM_SCORE_NOT_AVAILABLE",
        )
        resolved_target = self._resolve_ocr_exam_target(node, target)
        row = None if (context or {}).get("workflow_exam_force_live") is True else self._frozen_workflow_exam_row(node, resolved_target)
        if row is None:
            accepted_context = dict(context or {})
            from memorive.exams.tiered import tier_executor, project_tier_result
            for original_executor in self._workflow_exam_executors:
                executor = tier_executor(original_executor, accepted_context)
                try:
                    plan = executor.plan(node, resolved_target, accepted_context)
                except Exception:
                    continue
                if not isinstance(plan, Mapping) or plan.get("status") != "READY":
                    continue
                try:
                    parameters = signature(executor.execute).parameters
                    from memorive.exams.jobs import observed_call
                    kwargs: dict[str, Any] = {
                        "authorization": authorization,
                        "structured_chat": observed_call(self.runner.execute_structured_chat, "structured_chat"),
                    }
                    if "embedding_call" in parameters:
                        kwargs["embedding_call"] = observed_call(self.runner.execute_embeddings, "embedding")
                    if "ocr_call" in parameters:
                        kwargs["ocr_call"] = observed_call(self.runner.execute_ocr_image, "ocr")
                    if "rerank_call" in parameters:
                        kwargs["rerank_call"] = observed_call(self.runner.execute_rerank, "rerank")
                    dynamic = executor.execute(
                        node,
                        resolved_target,
                        accepted_context,
                        **kwargs,
                    )
                except Exception:
                    result["reason"] = "WORKFLOW_NODE_EXAM_EXECUTOR_FAILED"
                    return result
                if not isinstance(dynamic, Mapping):
                    result["reason"] = "WORKFLOW_NODE_EXAM_EXECUTOR_FAILED"
                    return result
                projected = project_tier_result(dynamic, plan)
                try:
                    self._exam_score_history.record(
                        node, resolved_target, projected,
                        {getattr(e, "EXECUTOR_REF", None) for e in self._workflow_exam_executors},
                    )
                except (OSError, ValueError, TypeError):
                    # The answer and immutable run survive an auxiliary cache failure.
                    projected["score_cache_write_failed"] = True
                return projected
            return result
        model_name = resolved_target.get("model_name")
        result.update(
            status=row["verdict"],
            score=row["score"],
            reason="FROZEN_MODEL_EXAM_SCORE_REUSED",
            requested_model=model_name,
            returned_model=model_name,
            exam_evidence_sha256=row["evidence_sha256"],
            **{
                field: row[field]
                for field in WORKFLOW_EXAM_SCORE_METADATA_FIELDS
                if field in row
            },
        )
        return result


class FailClosedWorkflowExams:
    """Used by SettingsController when it is given no WorkflowExams: every exam is not available."""

    def plan_workflow_node_exam(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        del node, target, context
        return {
            "schema_version": "WorkflowModelExamPlan-v2",
            "status": "NOT_AVAILABLE",
            "mode": "NONE",
            "reason": "MODEL_VALIDATION_RUNNER_NOT_CONFIGURED",
            "external_network_calls": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
        }

    def test_workflow_node(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        *,
        context: Mapping[str, Any] | None = None,
        authorization: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        del node, target, context, authorization
        return FailClosedModelValidationRunner.not_run("WORKFLOW_NODE")
