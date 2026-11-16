"""The analysis exam.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import importlib
import importlib.util
import json
from pathlib import Path
import re
from time import monotonic
from typing import Any, Callable, Mapping
import uuid
from memorive_settings.exam_successor import (
    ANALYSIS_REPAIR_ACCEPTANCE_REVISION,
    ANALYSIS_REPAIR_MATERIALITY_REVISION,
    ExecutionOutcome,
    Scorability,
    admit_quality_fail,
    analysis_cohort_model_fail_decision,
    analysis_material_repair_targets,
    analysis_repair_acceptance,
    build_analysis_panel_cohort,
    build_repair_round,
    classify_execution_outcome,
    cumulative_category_repair_penalty,
    workflow_exam_structured_output_policy,
)
from memorive.models.output_limits import (
    WORKFLOW_EXAM_REPLY_TOKEN_CEILING,
    workflow_exam_output_limit_contract,
)

from memorive_settings.workflow_exam.card_reviewer import QualityCardReviewerExamSuccessorExecutor
from memorive_settings.workflow_exam.common import (
    AUTHORIZATION_SCHEMA,
    Core_PRIMARY_ANALYSIS_OUTPUT_CHAR_LIMIT,
    Core_PRIMARY_ANALYSIS_PROMPT_CHAR_SURROGATE_LIMIT,
    EXECUTOR_REF,
    PLAN_SCHEMA,
    PRIMARY_MODEL_FAIL_POLICY_REVISION,
    RESULT_SCHEMA,
    _compact,
    _effective_structured_prompt_metrics,
    _file_sha256,
    _load_json,
    _not_available,
    _not_run,
    _serialized_request,
    _sha256,
    _write_json_create_only,
    core_primary_workload_profile,
)


ANALYSIS_PRIMARY_EXAM_CATEGORY_ID = "Quality_ANALYSIS_PRIMARY_REFERENCE_REGRESSION"


ANALYSIS_PRIMARY_SUCCESSOR_SAMPLE_REVISION = (
    "Desktop_ANALYSIS_PRIMARY_NO_LENGTH_INVALIDATION_THREE_PANEL_V3"
)


ANALYSIS_PRIMARY_SUBJECT_PROJECTION_REVISION = (
    "Desktop_ANALYSIS_PRIMARY_REDUNDANT_SCOPE_TEXT_PROJECTION_V1"
)


ANALYSIS_PRIMARY_ANCHOR_NORMALIZATION_REVISION = (
    "Desktop_ANALYSIS_PRIMARY_CASE_LOCAL_ANCHOR_ALIAS_V1"
)


ANALYSIS_PRIMARY_STRUCTURAL_REPAIR_PROJECTION_REVISION = (
    "Desktop_ANALYSIS_PRIMARY_BOUNDED_STRUCTURAL_RESIDUAL_V2"
)


ANALYSIS_PRIMARY_SEMANTIC_REPAIR_SHARDING_REVISION = (
    "Desktop_ANALYSIS_PRIMARY_Core_X2_DYNAMIC_RESIDUAL_SHARDS_V1"
)


class QualityAnalysisExamExecutor(QualityCardReviewerExamSuccessorExecutor):
    """Transport the frozen Quality analysis A/B exam through API/CLI/local."""

    EXECUTOR_REF = (
        "Desktop_MODEL_EXAM_SUCCESSOR_Quality_ANALYSIS_PRIMARY_EXECUTOR_V5_"
        "NO_LENGTH_INVALIDATION"
    )
    MODE = "LIVE_Quality_ANALYSIS_SUCCESSOR_REFERENCE_REGRESSION"
    API_MAXIMUM_REQUEST_ATTEMPTS = 25
    CLI_MAXIMUM_REQUEST_ATTEMPTS = 25
    LOCAL_MAXIMUM_REQUEST_ATTEMPTS = 40
    API_BUDGET_CAP_CNY = 20.0
    TARGETS_PER_SHARD = 6
    SAMPLE_ROTATION_SLOTS = 12
    SEMANTIC_REPAIR_ROUND_LIMIT = 2
    PANELS_REQUIRED_FOR_CACHE = 3
    PANEL_COHORT_COUNT = 4
    EXAM_CATEGORY_ID = ANALYSIS_PRIMARY_EXAM_CATEGORY_ID

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
                and target.get("model_name") == "deepseek-v4-pro"
                and target.get("thinking") is True
                and target.get("tier") == "pro"
                and target.get("connection_status") == "AVAILABLE"
                and isinstance(target.get("config_id"), str)
                and bool(target.get("config_id"))
            ):
                return "API"
            if (
                target.get("analysis_primary_exam_eligible") is True
                and target.get("catalog_model_identity_status") == "AVAILABLE"
                and target.get("connection_status") == "AVAILABLE"
                and isinstance(target.get("config_id"), str)
                and bool(target.get("config_id"))
                and isinstance(target.get("provider"), str)
                and bool(target.get("provider"))
                and isinstance(target.get("model_name"), str)
                and bool(target.get("model_name"))
                and isinstance(target.get("thinking"), bool)
                and isinstance(target.get("tier"), str)
                and bool(target.get("tier"))
            ):
                return "API"
            return None
        if kind == "CLI":
            if (
                _compact(target.get("adapter_id")) == "codexcli"
                and target.get("model_name") == "gpt-5.6-sol"
                and target.get("thinking_mode") in {"high", "xhigh", "max"}
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

    def _analysis_pricing_binding(
        self,
        target: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if target is not None and target.get("analysis_primary_exam_eligible") is True:
            snapshot = target.get("pricing_snapshot_date")
            profile_sha256 = target.get("pricing_profile_sha256")
            budget_cap = target.get("exam_budget_cap_cny")
            pricing_role = target.get("pricing_role")
            if (
                not isinstance(snapshot, str)
                or not snapshot
                or not isinstance(profile_sha256, str)
                or not re.fullmatch(r"[A-F0-9]{64}", profile_sha256)
                or isinstance(budget_cap, bool)
                or not isinstance(budget_cap, (int, float))
                or not 0 < float(budget_cap) <= 100_000
                or not isinstance(pricing_role, str)
                or not pricing_role
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_PRICING_PROFILE_INVALID")
            return {
                "snapshot_date": snapshot,
                "sha256": profile_sha256,
                "budget_cap_cny": float(budget_cap),
                "pricing_role": pricing_role,
            }
        profile = _load_json(self._pricing_profile_path)
        high = profile.get("high_cost")
        if (
            profile.get("schema_version")
            != "model_evaluation-model_evaluation-card-distiller-flash-to-pro-public-profile-v1"
            or not isinstance(high, Mapping)
            or high.get("model_id") != "deepseek-v4-pro"
            or float(high.get("budget_cap_cny", -1)) != self.API_BUDGET_CAP_CNY
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_PRICING_PROFILE_INVALID")
        return {
            "snapshot_date": profile.get("profile_snapshot_date"),
            "sha256": _file_sha256(self._pricing_profile_path),
            "budget_cap_cny": float(high["budget_cap_cny"]),
            "pricing_role": "HIGH_COST_DEEPSEEK_PRO",
        }

    def _structured_output_policy(
        self,
        target: Mapping[str, Any],
    ) -> tuple[dict[str, Any], int | None, str]:
        return workflow_exam_structured_output_policy(target)

    @classmethod
    def _subject_request_projection(
        cls,
        target: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        if target.get("kind") != "LOCAL":
            return deepcopy(dict(request))
        # Reuse the Execution/7 marker-safe local projection.  Its reviewer
        # contract expects an inline schema, so supply a validator-only dummy
        # and remove it after the marker check; the real response schema stays
        # on the structured transport rather than being duplicated in prompt.
        augmented = deepcopy(dict(request))
        augmented["output_schema"] = {
            "type": "object",
            "additionalProperties": True,
        }
        projected = super()._subject_request_projection(target, augmented)
        projected.pop("output_schema", None)
        projected.pop("request_payload_hash", None)
        projected["request_payload_hash"] = _sha256(projected)
        return projected

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
                raise ValueError("ANALYSIS_PRIMARY_SAMPLE_SLOT_INVALID")
            return explicit
        ordinal = context.get("workflow_exam_attempt_ordinal", 1)
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1:
            raise ValueError("ANALYSIS_PRIMARY_ATTEMPT_ORDINAL_INVALID")
        return ((ordinal - 1) % cls.SAMPLE_ROTATION_SLOTS) + 1

    @classmethod
    def _panel_cohort_slots(cls, sample_slot: int) -> list[int]:
        if (
            isinstance(sample_slot, bool)
            or not isinstance(sample_slot, int)
            or sample_slot < 1
            or sample_slot > cls.SAMPLE_ROTATION_SLOTS
        ):
            raise ValueError("ANALYSIS_PRIMARY_SAMPLE_SLOT_INVALID")
        base = (
            ((sample_slot - 1) // cls.PANELS_REQUIRED_FOR_CACHE)
            * cls.PANELS_REQUIRED_FOR_CACHE
            + 1
        )
        return list(range(base, base + cls.PANELS_REQUIRED_FOR_CACHE))

    @staticmethod
    def _panel_model_identity(target: Mapping[str, Any]) -> dict[str, Any]:
        model_name = target.get("model_name")
        return {
            "profile_kind": target.get("kind"),
            "requested_model": model_name,
            "returned_model": model_name,
            "provider": target.get("provider"),
            "adapter_id": target.get("adapter_id"),
            "endpoint_kind": target.get("endpoint_kind"),
            "thinking_mode": target.get("thinking_mode"),
            "thinking": target.get("thinking"),
            "tier": target.get("tier"),
            "model_digest": target.get("model_digest"),
        }

    @classmethod
    def _panel_cohort_basis(cls, reference_pack_sha256: str) -> dict[str, Any]:
        return {
            "category_id": cls.EXAM_CATEGORY_ID,
            "reference_pack_sha256": reference_pack_sha256,
            "sample_revision": ANALYSIS_PRIMARY_SUCCESSOR_SAMPLE_REVISION,
            "subject_projection_revision": (
                ANALYSIS_PRIMARY_SUBJECT_PROJECTION_REVISION
            ),
            "executor_ref": cls.EXECUTOR_REF,
        }

    def _completed_panel_records(
        self,
        *,
        target: Mapping[str, Any],
        cohort_basis: Mapping[str, Any],
        cohort_slots: list[int],
    ) -> list[dict[str, Any]]:
        workflow_root = self.scratch_root / "workflow_exams"
        if not workflow_root.is_dir():
            return []
        expected_identity = self._panel_model_identity(target)
        candidates: list[tuple[int, int, str, dict[str, Any]]] = []
        for path in workflow_root.glob(
            "workflow-exam-*/panel_scores/panel_record.json"
        ):
            try:
                record = _load_json(path)
                stored_hash = record.get("panel_record_sha256")
                unsigned = {
                    key: value
                    for key, value in record.items()
                    if key != "panel_record_sha256"
                }
                if (
                    record.get("schema_version")
                    != "DesktopAnalysisPrimaryPanelRecord-v1"
                    or not isinstance(stored_hash, str)
                    or stored_hash != _sha256(unsigned)
                    or record.get("cohort_basis") != dict(cohort_basis)
                    or record.get("model_identity") != expected_identity
                    or record.get("sample_slot") not in cohort_slots
                    or record.get("single_panel_cache_eligible") is not False
                ):
                    continue
                slot = int(record["sample_slot"])
                candidates.append(
                    (
                        slot,
                        path.stat().st_mtime_ns,
                        str(path.resolve()),
                        record,
                    )
                )
            except (OSError, ValueError, TypeError):
                continue
        selected: dict[int, tuple[int, str, dict[str, Any]]] = {}
        for slot, modified, path_text, record in sorted(
            candidates, key=lambda row: (row[0], row[1], row[2])
        ):
            selected.setdefault(slot, (modified, path_text, record))
        return [
            deepcopy(selected[slot][2])
            for slot in sorted(selected)
        ]

    def _runtime_panel_selection(
        self,
        *,
        context: Mapping[str, Any],
        target: Mapping[str, Any],
        cohort_basis: Mapping[str, Any],
    ) -> dict[str, Any]:
        explicit = (
            "workflow_exam_sample_slot" in context
            or "workflow_exam_attempt_ordinal" in context
        )
        if explicit:
            slot = self._sample_slot(context)
            cohort_slots = self._panel_cohort_slots(slot)
            records = self._completed_panel_records(
                target=target,
                cohort_basis=cohort_basis,
                cohort_slots=cohort_slots,
            )
            return {
                "sample_slot": slot,
                "cohort_slots": cohort_slots,
                "existing_panel_count": len(records),
                "state_driven_rotation": False,
            }
        cohort_slots = [1, 2, 3]
        records = self._completed_panel_records(
            target=target,
            cohort_basis=cohort_basis,
            cohort_slots=cohort_slots,
        )
        completed = {int(record["sample_slot"]) for record in records}
        missing = [slot for slot in cohort_slots if slot not in completed]
        slot = missing[0] if missing else cohort_slots[-1]
        return {
            "sample_slot": slot,
            "cohort_slots": cohort_slots,
            "existing_panel_count": len(records),
            "state_driven_rotation": True,
            "cohort_already_complete": not missing,
        }

    @staticmethod
    def _project_evidence_text(text: str) -> str:
        if not isinstance(text, str) or not text:
            raise ValueError("ANALYSIS_PRIMARY_EVIDENCE_TEXT_INVALID")
        marker = " Scope record for "
        if text.count(marker) != 1:
            raise ValueError("ANALYSIS_PRIMARY_SCOPE_MARKER_INVALID")
        prefix, remainder = text.split(marker, 1)
        boundary = (
            "The unit does not authorize a broader population, a longer time "
            "window, or a stronger causal verb than it explicitly reports."
        )
        suffix = ""
        boundary_index = remainder.find(boundary)
        if boundary_index >= 0:
            suffix = remainder[boundary_index + len(boundary) :].strip()
        return prefix.strip() + ((" " + suffix) if suffix else "")

    @classmethod
    def _exam_inputs(
        cls,
        *,
        pack: Any,
        source_schema: Mapping[str, Any],
        context: Mapping[str, Any],
        protocol: Any,
    ) -> dict[str, Any]:
        sample_slot = cls._sample_slot(context)
        forms: dict[str, dict[str, Any]] = {}
        golds: dict[str, dict[str, Any]] = {}
        selection: dict[str, list[dict[str, Any]]] = {}
        difficulty_cycle = ("hard", "medium", "easy")
        cohort_index = (sample_slot - 1) // cls.PANELS_REQUIRED_FOR_CACHE
        panel_index = (sample_slot - 1) % cls.PANELS_REQUIRED_FOR_CACHE
        family_order = list(protocol.FAMILIES)
        for form_id in ("A", "B"):
            source_form = _load_json(pack.member(f"forms/form_{form_id}.json"))
            source_gold = _load_json(
                pack.member(f"gold/form_{form_id}_gold.json")
            )
            form_by_id = {
                str(row["case_id"]): row for row in source_form.get("cases", [])
            }
            gold_by_id = {
                str(row["case_id"]): row for row in source_gold.get("cases", [])
            }
            selected_ids: list[str] = []
            selected_meta: list[dict[str, Any]] = []
            for family_index, family in enumerate(family_order):
                selected_difficulty = difficulty_cycle[
                    (panel_index + family_index) % len(difficulty_cycle)
                ]
                family_rows = [
                    row
                    for row in source_gold.get("cases", [])
                    if isinstance(row, Mapping)
                    and row.get("family") == family
                    and row.get("difficulty") == selected_difficulty
                ]
                family_rows.sort(key=lambda row: str(row.get("case_id")))
                if len(family_rows) < cls.PANELS_REQUIRED_FOR_CACHE:
                    raise ValueError(
                        "ANALYSIS_PRIMARY_FAMILY_DIFFICULTY_POPULATION_INVALID"
                    )
                index = (cohort_index + family_index) % len(family_rows)
                selected = family_rows[index]
                case_id = str(selected["case_id"])
                selected_ids.append(case_id)
                selected_meta.append(
                    {
                        "case_id": case_id,
                        "family": family,
                        "difficulty": selected.get("difficulty"),
                    }
                )
            if len(selected_ids) != 6 or len(set(selected_ids)) != 6:
                raise ValueError("ANALYSIS_PRIMARY_SAMPLE_INVALID")
            projected_cases: list[dict[str, Any]] = []
            for case_id in selected_ids:
                case = deepcopy(dict(form_by_id[case_id]))
                for unit in case.get("evidence_units", []):
                    unit["text"] = cls._project_evidence_text(str(unit["text"]))
                projected_cases.append(case)
            form = deepcopy(dict(source_form))
            form["cases"] = projected_cases
            form["subject_projection"] = {
                "revision": ANALYSIS_PRIMARY_SUBJECT_PROJECTION_REVISION,
                "removed_content": "REDUNDANT_SCOPE_BOILERPLATE_ONLY",
                "structured_scope_fields_preserved": True,
                "variant_specific_suffix_preserved": True,
            }
            gold = deepcopy(dict(source_gold))
            gold["cases"] = [deepcopy(gold_by_id[case_id]) for case_id in selected_ids]
            forms[form_id] = form
            golds[form_id] = gold
            selection[form_id] = selected_meta
        # Length is not a quality or structural-failure dimension.  Keep the
        # Core mean x2 value as a workload diagnostic, but remove field
        # maxLength from the subject/scoring schema so an otherwise complete
        # answer is retained and scored without a paid compression repair.
        # The accepted Quality protocol is immutable and still requires a
        # positive compatibility value.  Reuse it to build the schema, then
        # remove only its legacy text-length keywords in this successor
        # adapter.  Newer protocol copies accept ``None`` directly, but this
        # path deliberately remains compatible with the frozen Quality asset.
        schema = protocol.successor_output_schema(
            deepcopy(dict(source_schema)),
            text_max_length=512,
        )
        for artifact, field_name in (
            ("claim_map", "claim_text"),
            ("analysis_summary", "summary_text"),
        ):
            schema["properties"][artifact]["items"]["properties"][
                field_name
            ].pop("maxLength", None)
        for artifact in ("claim_map", "analysis_summary"):
            schema["properties"][artifact]["minItems"] = 6
            schema["properties"][artifact]["maxItems"] = 6
        manifest = {
            "schema_version": ANALYSIS_PRIMARY_SUCCESSOR_SAMPLE_REVISION,
            "sample_slot": sample_slot,
            "panel_cohort_slots": cls._panel_cohort_slots(sample_slot),
            "panels_required_for_cache": cls.PANELS_REQUIRED_FOR_CACHE,
            "panel_evidence_only": True,
            "single_panel_cache_eligible": False,
            "rotation_slots": cls.SAMPLE_ROTATION_SLOTS,
            "selected_case_count_per_form": 6,
            "selected_family_count_per_form": 6,
            "difficulty_rotation": (
                "LATIN_2H_2M_2E_PER_PANEL_AND_HME_PER_FAMILY_PER_COHORT"
            ),
            "selection": selection,
            "projection_revision": ANALYSIS_PRIMARY_SUBJECT_PROJECTION_REVISION,
            "coverage_semantics": (
                "ONE_FROZEN_TRANSPORT_PANEL; SCORE_ONLY_AFTER_THREE_"
                "NONOVERLAPPING_LATIN_PANELS_AGGREGATE_BEFORE_TAIL;_"
                "EACH_FAMILY_HAS_EASY_MEDIUM_HARD"
            ),
            "gold_in_subject_request": False,
        }
        manifest["manifest_sha256"] = _sha256(manifest)
        return {
            "forms": forms,
            "golds": golds,
            "schema": schema,
            "sample_metadata": manifest,
        }

    def _quality(self) -> dict[str, Any]:
        if self._modules is None:
            names = {
                "protocol": "analysis_exam_protocol",
                "exam": "analysis_exam",
                "reference": "reference_exam_pack",
            }
            self._modules = {
                key: importlib.import_module(f"{self._quality_package}.{suffix}")
                for key, suffix in names.items()
            }
        return self._modules

    def plan(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        if node.get("node_id") != "analysis":
            return _not_available("WORKFLOW_MODEL_EXAM_DYNAMIC_NODE_UNSUPPORTED")
        profile_kind = self._profile_kind(target)
        if profile_kind is None:
            return _not_available("WORKFLOW_MODEL_EXAM_DYNAMIC_PROFILE_UNSUPPORTED")
        try:
            preload_root = context.get(
                "workflow_exam_preloaded_initial_artifacts"
            )
            preload_binding = context.get(
                "workflow_exam_preload_binding_sha256"
            )
            if preload_root is not None and (
                not isinstance(preload_root, Mapping)
                or not isinstance(preload_binding, str)
                or preload_binding != _sha256(preload_root)
            ):
                raise ValueError("ANALYSIS_PRELOAD_CONTEXT_BINDING_INVALID")
            modules = self._quality()
            pack = modules["reference"].load_analysis_reference_exam_pack(
                self._reference_pack_root
            )
            cohort_basis = self._panel_cohort_basis(pack.pack_fingerprint)
            runtime_selection = self._runtime_panel_selection(
                context=context,
                target=target,
                cohort_basis=cohort_basis,
            )
            effective_context = {
                **dict(context),
                "workflow_exam_sample_slot": runtime_selection["sample_slot"],
            }
            source_schema = _load_json(
                pack.member("schemas/analysis_primary_output.schema.json")
            )
            inputs = self._exam_inputs(
                pack=pack,
                source_schema=source_schema,
                context=effective_context,
                protocol=modules["protocol"],
            )
            pricing = (
                self._analysis_pricing_binding(target)
                if profile_kind == "API"
                else None
            )
            (
                _model_binding,
                request_max_output_tokens,
                output_limit_mode,
            ) = self._structured_output_policy(target)
        except (ImportError, OSError, ValueError, RuntimeError):
            return _not_available("WORKFLOW_MODEL_EXAM_Quality_ASSETS_INVALID")
        manifest = dict(pack.manifest)
        if (
            manifest.get("classification") != "REFERENCE_EXAM_PACK"
            or manifest.get("reference_regression_only") is not True
            or manifest.get("qualification_eligible") is not False
        ):
            return _not_available("WORKFLOW_MODEL_EXAM_Quality_PACK_NOT_ELIGIBLE")
        prompt_metrics: dict[str, dict[str, list[dict[str, int]]]] = {}
        maximum_effective_prompt_chars = 0
        for form_id in ("A", "B"):
            prompt_metrics[form_id] = {}
            for artifact in ("claim_map", "analysis_summary"):
                artifact_metrics: list[dict[str, int]] = []
                for shard in self._shards(
                    inputs["forms"][form_id],
                    form_id=form_id,
                    artifact=artifact,
                    profile_kind=profile_kind,
                ):
                    preload = self._preloaded_initial_artifact_rows(
                        context=context,
                        target=target,
                        sample_slot=int(runtime_selection["sample_slot"]),
                        form_id=form_id,
                        artifact=artifact,
                        target_case_ids=list(shard["target_ids"]),
                    )
                    target_ids = list(preload["missing_case_ids"])
                    if not target_ids:
                        continue
                    case_by_id = {
                        str(row["case_id"]): deepcopy(dict(row))
                        for row in shard["context_form"]["cases"]
                    }
                    context_form = deepcopy(dict(shard["context_form"]))
                    context_form["cases"] = [
                        deepcopy(case_by_id[case_id])
                        for case_id in target_ids
                    ]
                    schema = modules["protocol"].shard_artifact_schema(
                        inputs["schema"],
                        artifact,
                        target_count=len(target_ids),
                    )
                    prompt = modules["protocol"].build_artifact_prompt(
                        context_form,
                        artifact,
                        target_case_ids=target_ids,
                    )
                    request = {
                        "phase": "ANALYSIS_REFERENCE_EXAM",
                        "artifact": artifact,
                        "target_case_ids": target_ids,
                        "subject_prompt": prompt,
                    }
                    metrics = _effective_structured_prompt_metrics(
                        _serialized_request(
                            self._subject_request_projection(target, request)
                        ),
                        schema,
                    )
                    artifact_metrics.append(metrics)
                    maximum_effective_prompt_chars = max(
                        maximum_effective_prompt_chars,
                        metrics["effective_prompt_chars"],
                    )
                prompt_metrics[form_id][artifact] = artifact_metrics
        base_calls = sum(
            len(rows)
            for form_metrics in prompt_metrics.values()
            for rows in form_metrics.values()
        )
        plan: dict[str, Any] = {
            "schema_version": PLAN_SCHEMA,
            "status": "READY",
            "mode": self.MODE,
            "reason": (
                "ANALYSIS_PRIMARY_THREE_PANEL_COHORT_REUSE_READY"
                if runtime_selection.get("cohort_already_complete", False)
                else "Quality_REFERENCE_REGRESSION_READY"
            ),
            "node_id": "analysis",
            "executor_ref": self.EXECUTOR_REF,
            "profile_kind": profile_kind,
            "profile_ref": target["config_id"],
            "model_name": target["model_name"],
            "model_digest": target.get("model_digest"),
            "thinking_mode": target.get("thinking_mode"),
            "output_limit_policy": output_limit_mode,
            "request_max_output_tokens": request_max_output_tokens,
            "calibrated_reply_token_ceiling": (
                WORKFLOW_EXAM_REPLY_TOKEN_CEILING
            ),
            "output_limit_contract": workflow_exam_output_limit_contract(),
            "provider_max_output_tokens": target.get(
                "provider_max_output_tokens"
            ),
            "settings_revision": context.get("settings_revision"),
            "settings_sha256": context.get("settings_sha256"),
            "reference_pack_id": manifest.get("pack_id"),
            "reference_pack_revision": manifest.get("pack_revision"),
            "reference_pack_sha256": pack.pack_fingerprint,
            "reference_regression_only": True,
            "blind_holdout_eligible": False,
            "qualification_eligible": False,
            "forms": ["A", "B"],
            "base_model_calls": (
                0
                if runtime_selection.get("cohort_already_complete", False)
                else base_calls
            ),
            "initial_exact_progress_reuse_requested": (
                preload_root is not None
            ),
            "initial_exact_progress_reuse_binding_sha256": (
                preload_binding if preload_root is not None else None
            ),
            "maximum_model_calls": (
                0
                if runtime_selection.get("cohort_already_complete", False)
                else self._request_cap(profile_kind)
            ),
            "structural_retry_limit_per_shard": 1,
            "structural_retry_limit_per_semantic_repair_shard": 1,
            "structural_retry_semantics": (
                "TRANSPORT_CONTRACT_CLOSURE_NOT_SEMANTIC_REPAIR_ROUND"
            ),
            "semantic_repair_round_limit": self.SEMANTIC_REPAIR_ROUND_LIMIT,
            "second_semantic_repair_allowed": True,
            "second_round_scope": (
                "ROUND1_RESIDUAL_EXACT_SET_ONLY_NO_FULL_FORM_RETRY"
            ),
            "second_round_penalty_semantics": (
                "CUMULATIVE_Quality_ANALYSIS_NONLINEAR_PLUS_"
                "CATEGORY_VOLUME_BURDEN"
            ),
            "sharding_revision": "LONG_DOCUMENT_EXACT_SET_NO_OVERLAP_ADAPTER_V1",
            "sample_revision": inputs["sample_metadata"]["schema_version"],
            "sample_slot": inputs["sample_metadata"]["sample_slot"],
            "sample_manifest_sha256": inputs["sample_metadata"][
                "manifest_sha256"
            ],
            "sample_case_count_per_form": 6,
            "sample_family_count_per_form": 6,
            "sample_rotation_slots": self.SAMPLE_ROTATION_SLOTS,
            "state_driven_panel_rotation": runtime_selection[
                "state_driven_rotation"
            ],
            "existing_panel_count": runtime_selection[
                "existing_panel_count"
            ],
            "cohort_already_complete": runtime_selection.get(
                "cohort_already_complete", False
            ),
            "panel_cohort_slots": inputs["sample_metadata"][
                "panel_cohort_slots"
            ],
            "panels_required_for_cache": self.PANELS_REQUIRED_FOR_CACHE,
            "panel_evidence_only": True,
            "single_panel_cache_eligible": False,
            "stable_score_cache_eligible": False,
            "aggregate_before_tail_components_required": True,
            "subject_projection_revision": (
                ANALYSIS_PRIMARY_SUBJECT_PROJECTION_REVISION
            ),
            "anchor_normalization_revision": (
                ANALYSIS_PRIMARY_ANCHOR_NORMALIZATION_REVISION
            ),
            "anchor_normalization_semantics": (
                "GOLD_FREE_UNAMBIGUOUS_CASE_LOCAL_ALIAS_RECOVERY_"
                "DOES_NOT_INCREMENT_SEMANTIC_REPAIR_ROUNDS"
            ),
            "structural_repair_projection_revision": (
                ANALYSIS_PRIMARY_STRUCTURAL_REPAIR_PROJECTION_REVISION
            ),
            "semantic_repair_sharding_revision": (
                ANALYSIS_PRIMARY_SEMANTIC_REPAIR_SHARDING_REVISION
            ),
            "repair_materiality_revision": (
                ANALYSIS_REPAIR_MATERIALITY_REVISION
            ),
            "repair_acceptance_revision": (
                ANALYSIS_REPAIR_ACCEPTANCE_REVISION
            ),
            "transport_target_cases_per_shard": self.TARGETS_PER_SHARD,
            "initial_effective_prompt_metrics": prompt_metrics,
            "maximum_initial_effective_prompt_chars": (
                maximum_effective_prompt_chars
            ),
            "workload_profile": core_primary_workload_profile(),
            "text_length_policy": (
                "NO_LENGTH_BASED_INVALIDATION_OR_PAID_REPAIR"
            ),
            "text_field_schema_ceiling_chars": None,
            "paid_repair_for_field_length_only": False,
            "visible_output_reference_ceiling_chars": (
                Core_PRIMARY_ANALYSIS_OUTPUT_CHAR_LIMIT
            ),
            "visible_output_reference_semantics": "DIAGNOSTIC_ONLY",
            "prompt_reference_ceiling_chars": (
                Core_PRIMARY_ANALYSIS_PROMPT_CHAR_SURROGATE_LIMIT
            ),
            "prompt_reference_semantics": (
                "SHARDING_GUIDANCE_ONLY_NOT_FAILURE_OR_MODEL_REPAIR"
            ),
            "maximum_initial_effective_prompt_exceeds_reference": (
                maximum_effective_prompt_chars
                > Core_PRIMARY_ANALYSIS_PROMPT_CHAR_SURROGATE_LIMIT
            ),
            "capacity_overflow_quality_semantics": "NOT_ASSESSED",
            "cross_category_comparison_forbidden": True,
            "global_ranking_forbidden": True,
            "exam_category_id": self.EXAM_CATEGORY_ID,
            "horizontal_comparison_eligible": False,
            "horizontal_comparison_pending_panel_cohort": True,
            "horizontal_comparison_allowed_only_with_same_cohort": True,
            "budget_cap_cny": (
                pricing["budget_cap_cny"] if pricing is not None else None
            ),
            "pricing_snapshot_date": (
                pricing["snapshot_date"] if pricing is not None else None
            ),
            "pricing_profile_sha256": (
                pricing["sha256"] if pricing is not None else None
            ),
            "pricing_role": (
                pricing["pricing_role"] if pricing is not None else None
            ),
            "cost_semantics": (
                "CACHE_REUSE_NO_EXTERNAL_CALL"
                if runtime_selection.get("cohort_already_complete", False)
                else (
                    "FROZEN_PROFILE_ESTIMATE_CAP"
                    if profile_kind == "API"
                    else (
                        ("CLI_MANAGED_UNKNOWN" if target.get("adapter_id")=="command_template" else "SUBSCRIPTION_CLI_NO_PER_CALL_PRICE")
                        if profile_kind == "CLI"
                        else "LOCAL_COMPUTE_NO_PROVIDER_API_COST"
                    )
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
            "panel_cohort_slots": plan["panel_cohort_slots"],
            "panels_required_for_cache": plan["panels_required_for_cache"],
            "subject_projection_revision": plan[
                "subject_projection_revision"
            ],
            "executor_ref": self.EXECUTOR_REF,
        }
        comparison_sha256 = _sha256(comparison_basis)
        plan["comparison_binding_sha256"] = comparison_sha256
        plan["comparison_cohort_id"] = (
            f"ANALYSIS_PRIMARY_SUCCESSOR:{comparison_sha256}"
        )
        panel_binding = {
            **comparison_basis,
            "sample_slot": plan["sample_slot"],
            "sample_manifest_sha256": plan["sample_manifest_sha256"],
        }
        plan["panel_binding_sha256"] = _sha256(panel_binding)
        plan["plan_sha256"] = _sha256(plan)
        return plan

    @staticmethod
    def _shards(
        form: Mapping[str, Any],
        *,
        form_id: str,
        artifact: str,
        profile_kind: str,
    ) -> list[dict[str, Any]]:
        cases = form.get("cases")
        if not isinstance(cases, list) or not cases:
            raise ValueError("WORKFLOW_MODEL_EXAM_ANALYSIS_FORM_INVALID")
        case_by_id = {
            str(row.get("case_id")): row
            for row in cases
            if isinstance(row, Mapping) and isinstance(row.get("case_id"), str)
        }
        ordered = [str(row.get("case_id")) for row in cases]
        if len(case_by_id) != len(cases) or len(set(ordered)) != len(cases):
            raise ValueError("WORKFLOW_MODEL_EXAM_ANALYSIS_FORM_INVALID")
        if profile_kind not in {"API", "CLI", "LOCAL"}:
            raise ValueError("WORKFLOW_MODEL_EXAM_ANALYSIS_PROFILE_INVALID")
        if artifact not in {"claim_map", "analysis_summary"}:
            raise ValueError("WORKFLOW_MODEL_EXAM_ANALYSIS_ARTIFACT_INVALID")
        target_count = QualityAnalysisExamExecutor.TARGETS_PER_SHARD
        shards: list[dict[str, Any]] = []
        for start in range(0, len(ordered), target_count):
            target_ids = ordered[start : start + target_count]
            target_form = deepcopy(dict(form))
            target_form["cases"] = [deepcopy(case_by_id[key]) for key in target_ids]
            shards.append(
                {
                    "target_ids": target_ids,
                    "target_form": target_form,
                    "context_form": deepcopy(target_form),
                }
            )
        return shards

    @staticmethod
    def _preloaded_initial_artifact_rows(
        *,
        context: Mapping[str, Any],
        target: Mapping[str, Any],
        sample_slot: int,
        form_id: str,
        artifact: str,
        target_case_ids: list[str],
    ) -> dict[str, Any]:
        """Reuse immutable completed rows and request only the missing cases."""

        missing_default = list(target_case_ids)
        preload_root = context.get("workflow_exam_preloaded_initial_artifacts")
        if preload_root is None:
            return {
                "rows": [],
                "reused_case_ids": [],
                "missing_case_ids": missing_default,
                "source_calls": [],
                "provider_call_required_for_reused_rows": False,
            }
        if not isinstance(preload_root, Mapping):
            raise ValueError("ANALYSIS_PRELOAD_ROOT_INVALID")
        entry = preload_root.get(f"{form_id}:{artifact}")
        if entry is None:
            return {
                "rows": [],
                "reused_case_ids": [],
                "missing_case_ids": missing_default,
                "source_calls": [],
                "provider_call_required_for_reused_rows": False,
            }
        if not isinstance(entry, Mapping):
            raise ValueError("ANALYSIS_PRELOAD_ENTRY_INVALID")
        rows = entry.get("rows")
        source_calls = entry.get("source_calls")
        if (
            entry.get("sample_slot") != sample_slot
            or entry.get("requested_model") != target.get("model_name")
            or entry.get("returned_model") != target.get("model_name")
            or not isinstance(rows, list)
            or not rows
            or entry.get("rows_sha256") != _sha256(rows)
            or not isinstance(source_calls, list)
            or not source_calls
        ):
            raise ValueError("ANALYSIS_PRELOAD_BINDING_INVALID")
        target_set = set(target_case_ids)
        row_ids: list[str] = []
        for row in rows:
            if not isinstance(row, Mapping) or not isinstance(
                row.get("case_id"), str
            ):
                raise ValueError("ANALYSIS_PRELOAD_ROWS_INVALID")
            row_ids.append(str(row["case_id"]))
        if (
            len(row_ids) != len(set(row_ids))
            or not set(row_ids).issubset(target_set)
        ):
            raise ValueError("ANALYSIS_PRELOAD_EXACT_SET_INVALID")
        for source_call in source_calls:
            if not isinstance(source_call, Mapping) or any(
                not isinstance(source_call.get(field), str)
                or not source_call.get(field)
                for field in (
                    "call_id",
                    "receipt_sha256",
                    "provider_output_sha256",
                )
            ):
                raise ValueError("ANALYSIS_PRELOAD_SOURCE_CALL_INVALID")
            for field in ("receipt_sha256", "provider_output_sha256"):
                if not re.fullmatch(r"[A-F0-9]{64}", str(source_call[field])):
                    raise ValueError("ANALYSIS_PRELOAD_SOURCE_HASH_INVALID")
        missing = [case_id for case_id in target_case_ids if case_id not in row_ids]
        return {
            "rows": deepcopy(rows),
            "reused_case_ids": row_ids,
            "missing_case_ids": missing,
            "source_calls": deepcopy(source_calls),
            "provider_call_required_for_reused_rows": False,
        }

    def _stable_provider_call(self, **kwargs: Any) -> dict[str, Any]:
        request = kwargs.get("request")
        response_schema = kwargs.get("response_schema")
        target = kwargs.get("target")
        if not all(
            isinstance(value, Mapping)
            for value in (request, response_schema, target)
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_ANALYSIS_CALL_INVALID")
        metrics = _effective_structured_prompt_metrics(
            _serialized_request(
                self._subject_request_projection(
                    target,  # type: ignore[arg-type]
                    request,  # type: ignore[arg-type]
                )
            ),
            response_schema,  # type: ignore[arg-type]
        )
        return super()._stable_provider_call(**kwargs)

    @staticmethod
    def _normalize_case_local_anchor_aliases(
        *,
        form: Mapping[str, Any],
        response: Mapping[str, Any],
        artifact: str,
    ) -> dict[str, Any]:
        """Recover only an unambiguous case-local ``Uxx`` anchor alias.

        The Quality Analysis schema binds every anchor to its case, for
        example ``S13AIE08-U01``.  Some otherwise exact structured models
        return the local suffix ``U01``.  This projection is Gold-free: it
        expands the suffix only when the current source case contains exactly
        that canonical anchor.  All ambiguous or unknown values remain
        untouched for the frozen Quality audit.
        """

        projected = deepcopy(dict(response))
        rows = projected.get(artifact)
        if artifact != "claim_map" or not isinstance(rows, list):
            return {
                "schema_version": (
                    ANALYSIS_PRIMARY_ANCHOR_NORMALIZATION_REVISION
                ),
                "normalized_response": projected,
                "normalization_count": 0,
                "actions": [],
                "gold_consulted": False,
                "provider_call_required": False,
                "semantic_repair_round_increment": 0,
            }
        case_by_id = {
            str(case.get("case_id")): case
            for case in form.get("cases", [])
            if isinstance(case, Mapping)
            and isinstance(case.get("case_id"), str)
        }
        anchor_fields = (
            "supporting_anchors",
            "contradicting_anchors",
            "qualification_anchors",
            "context_only_anchors",
            "lineage_invalid_anchors",
        )
        actions: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            case_id = row.get("case_id")
            case = case_by_id.get(str(case_id))
            if case is None:
                continue
            canonical_anchors = {
                str(unit.get("anchor"))
                for unit in case.get("evidence_units", [])
                if isinstance(unit, Mapping)
                and isinstance(unit.get("anchor"), str)
            }
            for field in anchor_fields:
                values = row.get(field)
                if not isinstance(values, list):
                    continue
                for index, observed in enumerate(values):
                    if (
                        not isinstance(observed, str)
                        or not re.fullmatch(r"U[0-9]{2,}", observed)
                    ):
                        continue
                    canonical = f"{case_id}-{observed}"
                    if canonical not in canonical_anchors:
                        continue
                    values[index] = canonical
                    actions.append(
                        {
                            "case_id": str(case_id),
                            "field": field,
                            "item_index": index,
                            "observed_anchor": observed,
                            "normalized_anchor": canonical,
                            "rule": (
                                "EXPAND_UNAMBIGUOUS_CASE_LOCAL_ANCHOR_SUFFIX"
                            ),
                            "gold_consulted": False,
                            "provider_call_required": False,
                            "semantic_repair_round_increment": 0,
                        }
                    )
        return {
            "schema_version": ANALYSIS_PRIMARY_ANCHOR_NORMALIZATION_REVISION,
            "normalized_response": projected,
            "normalization_count": len(actions),
            "actions": actions,
            "gold_consulted": False,
            "provider_call_required": False,
            "semantic_repair_round_increment": 0,
        }

    @staticmethod
    def _bounded_structural_residual_projection(
        audit: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Compact the exhaustive audit into a repair-only residual view."""

        by_case: dict[str, dict[str, Any]] = {}
        aggregate_issue_codes: set[str] = set()
        defects = audit.get("defects")
        if not isinstance(defects, list):
            defects = []
        for defect in defects:
            if not isinstance(defect, Mapping):
                continue
            code = defect.get("code")
            if not isinstance(code, str) or not code:
                continue
            case_id = defect.get("case_id")
            if not isinstance(case_id, str) or not case_id:
                aggregate_issue_codes.add(code)
                continue
            row = by_case.setdefault(
                case_id,
                {
                    "case_id": case_id,
                    "issue_codes": set(),
                    "failed_output_fields": set(),
                    "defect_locators": [],
                },
            )
            row["issue_codes"].add(code)
            field = defect.get("field")
            if isinstance(field, str) and field:
                row["failed_output_fields"].add(field)
            locator: dict[str, Any] = {"code": code}
            anchor = defect.get("anchor")
            if isinstance(anchor, str) and anchor:
                locator["anchor"] = anchor
            if isinstance(field, str) and field:
                locator["field"] = field
            for key in ("observed", "expected"):
                if key not in defect:
                    continue
                value = defect.get(key)
                if value is None or isinstance(value, (bool, int, float)):
                    locator[key] = value
                elif isinstance(value, str) and len(value) <= 256:
                    locator[key] = value
            repair_action = defect.get("repair_action")
            if isinstance(repair_action, str) and repair_action:
                locator["repair_action"] = repair_action
            if len(locator) > 1 and locator not in row["defect_locators"]:
                row["defect_locators"].append(locator)
        ordered_ids = [
            str(case_id)
            for case_id in audit.get("repair_exact_set", [])
            if isinstance(case_id, str)
        ]
        for case_id in sorted(by_case):
            if case_id not in ordered_ids:
                ordered_ids.append(case_id)
        rows = []
        for case_id in ordered_ids:
            row = by_case.get(case_id)
            if row is None:
                continue
            rows.append(
                {
                    "case_id": case_id,
                    "issue_codes": sorted(row["issue_codes"]),
                    "failed_output_fields": sorted(
                        row["failed_output_fields"]
                    ),
                    "defect_locators": deepcopy(row["defect_locators"]),
                }
            )
        return {
            "schema_version": (
                ANALYSIS_PRIMARY_STRUCTURAL_REPAIR_PROJECTION_REVISION
            ),
            "audit_status": audit.get("status"),
            "audit_defect_count": audit.get("defect_count"),
            "repair_exact_set": ordered_ids,
            "case_residuals": rows,
            "aggregate_issue_codes": sorted(aggregate_issue_codes),
            "full_audit_sent_to_provider": False,
            "gold_consulted": False,
        }

    @staticmethod
    def _analysis_wrong_sets(
        scores: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, list[str]]:
        return deepcopy(
            analysis_material_repair_targets(scores)["targets_by_form"]
        )

    @staticmethod
    def _analysis_cohort_raw_projection(
        score: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Project only scorer signals needed by the local panel aggregator.

        Expected-answer and observed-answer fields in the Quality raw detail are
        deliberately omitted so a panel receipt cannot become a Gold sidecar.
        """

        details = score.get("details")
        if not isinstance(details, list):
            raise ValueError("ANALYSIS_PANEL_RAW_DETAILS_REQUIRED")
        projected_details: list[dict[str, Any]] = []
        allowed = {
            "case_id",
            "family",
            "difficulty",
            "score",
            "critical_reasons",
            "axes",
            "anchor_role_accuracy",
        }
        for detail in details:
            if not isinstance(detail, Mapping):
                raise ValueError("ANALYSIS_PANEL_RAW_DETAIL_INVALID")
            projected_details.append(
                {
                    key: deepcopy(detail[key])
                    for key in allowed
                    if key in detail
                }
            )
        return {
            "sample_count": len(projected_details),
            "details": projected_details,
            "family_scores": deepcopy(dict(score.get("family_scores") or {})),
            "axis_scores": deepcopy(dict(score.get("axis_scores") or {})),
            "form_score": score.get("form_score"),
            "critical_any": bool(score.get("critical_any", False)),
            "critical_count": int(score.get("critical_count", 0)),
            "critical_findings": [
                {
                    "case_id": str(detail.get("case_id")),
                    "critical_reasons": deepcopy(
                        list(detail.get("critical_reasons") or [])
                    ),
                }
                for detail in details
                if isinstance(detail, Mapping)
                and detail.get("critical_reasons")
            ],
            "quality_hard_gate": score.get("quality_hard_gate"),
            "max_single_sample_effect_points": score.get(
                "max_single_sample_effect_points"
            ),
            "gold_fields_projected": False,
        }

    @staticmethod
    def _analysis_case_local_acceptance_merge(
        *,
        original_responses: Mapping[str, Mapping[str, Any]],
        candidate_responses: Mapping[str, Mapping[str, Any]],
        accepted_by_form: Mapping[str, list[str]],
    ) -> dict[str, dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for form_id in ("A", "B"):
            original = original_responses.get(form_id)
            candidate = candidate_responses.get(form_id)
            if not isinstance(original, Mapping) or not isinstance(
                candidate, Mapping
            ):
                raise ValueError("ANALYSIS_REPAIR_RESPONSE_MAPPING_REQUIRED")
            accepted = set(accepted_by_form.get(form_id, []))
            form_value: dict[str, Any] = {}
            for artifact in ("claim_map", "analysis_summary"):
                original_rows = original.get(artifact)
                candidate_rows = candidate.get(artifact)
                if not isinstance(original_rows, list) or not isinstance(
                    candidate_rows, list
                ):
                    raise ValueError("ANALYSIS_REPAIR_ARTIFACT_ARRAY_REQUIRED")
                candidate_by_id = {
                    str(row.get("case_id")): row
                    for row in candidate_rows
                    if isinstance(row, Mapping)
                    and isinstance(row.get("case_id"), str)
                }
                original_ids = [
                    str(row.get("case_id"))
                    for row in original_rows
                    if isinstance(row, Mapping)
                    and isinstance(row.get("case_id"), str)
                ]
                if (
                    len(original_ids) != len(original_rows)
                    or set(candidate_by_id) != set(original_ids)
                ):
                    raise ValueError("ANALYSIS_REPAIR_FULL_CASE_SET_MISMATCH")
                form_value[artifact] = [
                    deepcopy(
                        dict(
                            candidate_by_id[case_id]
                            if case_id in accepted
                            else original_rows[index]
                        )
                    )
                    for index, case_id in enumerate(original_ids)
                ]
            merged[form_id] = form_value
        return merged

    @staticmethod
    def _analysis_category_burden(
        *,
        exam: Any,
        forms: Mapping[str, Mapping[str, Any]],
        scores: Mapping[str, Mapping[str, Any]],
        exact_sets: Mapping[str, list[str]],
    ) -> dict[str, Any]:
        by_form: dict[str, dict[str, Any]] = {}
        for form_id in ("A", "B"):
            target_ids = list(exact_sets.get(form_id, []))
            details = {
                str(detail.get("case_id")): detail
                for detail in scores[form_id].get("details", [])
                if isinstance(detail, Mapping)
                and isinstance(detail.get("case_id"), str)
            }
            if any(case_id not in details for case_id in target_ids):
                raise ValueError("ANALYSIS_REPAIR_RESIDUAL_DETAIL_MISSING")
            issue_categories: set[str] = set()
            severity_categories: set[str] = set()
            artifact_categories: set[str] = set()
            for case_id in target_ids:
                issues, _failed_fields, artifacts, severity = (
                    exam.analysis_exam_issue_metadata(details[case_id])
                )
                issue_categories.update(issues)
                severity_categories.add(severity)
                artifact_categories.update(artifacts)
            case_count = len(forms[form_id]["cases"])
            penalty = exam.analysis_exam_form_penalty(
                form_case_count=case_count,
                wrong_case_count=len(target_ids),
                issue_categories=issue_categories,
                severity_categories=severity_categories,
                artifact_categories=artifact_categories,
            )
            by_form[form_id] = {
                "target_case_ids": target_ids,
                "target_case_count": len(target_ids),
                **deepcopy(dict(penalty)),
            }
        return {
            "schema_version": "AnalysisCategoryRepairBurden-v1",
            "by_form": by_form,
            "total_mean": round(
                sum(float(row["total"]) for row in by_form.values()) / 2.0,
                6,
            ),
            "volume_mean": round(
                sum(
                    float(row["wrong_item_volume"])
                    for row in by_form.values()
                )
                / 2.0,
                6,
            ),
            "coefficient_source": "Quality_ANALYSIS_EXAM_REPAIR_PENALTY_V1",
            "cross_category_coefficients_reused": False,
        }

    @staticmethod
    def _project_analysis_repair_schema(
        repair_schema: Mapping[str, Any],
        *,
        form_id: str,
        case_ids: list[str],
    ) -> dict[str, Any]:
        form_key = f"form_{form_id}"
        projected = deepcopy(dict(repair_schema))
        properties = projected.get("properties")
        if (
            not isinstance(properties, Mapping)
            or not isinstance(properties.get(form_key), Mapping)
            or not case_ids
            or len(case_ids) != len(set(case_ids))
        ):
            raise ValueError("ANALYSIS_REPAIR_SHARD_SCHEMA_INVALID")
        projected["required"] = [form_key]
        projected["properties"] = {
            form_key: deepcopy(dict(properties[form_key]))
        }
        try:
            artifact_properties = projected["properties"][form_key][
                "properties"
            ]
            for artifact in ("claim_map", "analysis_summary"):
                rows = artifact_properties[artifact]
                rows["minItems"] = len(case_ids)
                rows["maxItems"] = len(case_ids)
                rows["items"]["properties"]["case_id"] = {
                    "type": "string",
                    "enum": list(case_ids),
                }
        except (KeyError, TypeError) as error:
            raise ValueError(
                "ANALYSIS_REPAIR_SHARD_SCHEMA_INVALID"
            ) from error
        return projected

    def _run_analysis_semantic_repair(
        self,
        *,
        run_id: str,
        run_root: Path,
        state: dict[str, Any],
        target: Mapping[str, Any],
        package: Mapping[str, Any],
        lifecycle_contract: Mapping[str, Any],
        round_number: int,
        structured_chat: Callable[..., Mapping[str, Any]],
    ) -> dict[str, Any]:
        contract = package.get("contract")
        repair_schema = package.get("repair_schema")
        repair_targets = package.get("repair_targets")
        if not all(
            isinstance(value, Mapping)
            for value in (contract, repair_schema, repair_targets)
        ):
            raise ValueError("ANALYSIS_REPAIR_PACKAGE_INVALID")
        assert isinstance(contract, Mapping)
        assert isinstance(repair_schema, Mapping)
        assert isinstance(repair_targets, Mapping)
        exam = self._quality()["exam"]
        exact_sets = contract.get("wrong_item_exact_set")
        if not isinstance(exact_sets, Mapping):
            raise ValueError("ANALYSIS_REPAIR_PACKAGE_INVALID")
        merged: dict[str, Any] = {}
        receipts: list[dict[str, Any]] = []
        for form_id in ("A", "B"):
            case_ids = exact_sets.get(form_id)
            targets = repair_targets.get(form_id)
            if not isinstance(case_ids, list) or not isinstance(targets, list):
                raise ValueError("ANALYSIS_REPAIR_PACKAGE_INVALID")
            if not case_ids:
                continue
            target_by_id = {
                str(row.get("case_id")): row
                for row in targets
                if isinstance(row, Mapping)
                and isinstance(row.get("case_id"), str)
            }
            if list(target_by_id) != case_ids:
                raise ValueError("ANALYSIS_REPAIR_TARGET_ORDER_INVALID")

            def request_for(
                selected_ids: list[str],
                *,
                chunk_index: int,
                chunk_count: int,
            ) -> tuple[dict[str, Any], dict[str, Any]]:
                selected_schema = self._project_analysis_repair_schema(
                    repair_schema,
                    form_id=form_id,
                    case_ids=selected_ids,
                )
                selected_request = {
                    "phase": "ANALYSIS_EXAM_DIRECTED_REPAIR",
                    "semantic_repair_round": round_number,
                    "instruction": (
                        "Replace all and only the listed synthetic analysis "
                        "cases. Return complete claim_map and analysis_summary "
                        "rows for each listed case exactly once and no other "
                        "case. Use source_case as the only source of truth, "
                        "current_rows as the starting point, and correct every "
                        "listed issue_code and failed_output_field. The JSON "
                        "Schema is supplied once by the structured transport."
                    ),
                    "wrong_item_exact_set": {form_id: list(selected_ids)},
                    "repair_targets": {
                        form_id: [
                            deepcopy(dict(target_by_id[case_id]))
                            for case_id in selected_ids
                        ]
                    },
                    "parent_contract_sha256": contract.get(
                        "contract_sha256"
                    ),
                    "successor_lifecycle_sha256": lifecycle_contract.get(
                        "contract_sha256"
                    ),
                    "transport_shard": {
                        "form_id": form_id,
                        "chunk_index": chunk_index,
                        "chunk_count": chunk_count,
                        "case_ids_sha256": _sha256(selected_ids),
                    },
                }
                return selected_request, selected_schema

            chunks: list[list[str]] = []
            current: list[str] = []
            for case_id in case_ids:
                candidate = [*current, case_id]
                candidate_request, candidate_schema = request_for(
                    candidate,
                    chunk_index=99,
                    chunk_count=99,
                )
                metrics = _effective_structured_prompt_metrics(
                    _serialized_request(
                        self._subject_request_projection(
                            target, candidate_request
                        )
                    ),
                    candidate_schema,
                )
                if (
                    current
                    and metrics["effective_prompt_chars"]
                    > Core_PRIMARY_ANALYSIS_PROMPT_CHAR_SURROGATE_LIMIT
                ):
                    chunks.append(current)
                    current = [case_id]
                else:
                    current = candidate
            if current:
                chunks.append(current)
            form_rows = {"claim_map": [], "analysis_summary": []}
            for chunk_index, chunk_ids in enumerate(chunks, start=1):
                request, chunk_schema = request_for(
                    chunk_ids,
                    chunk_index=chunk_index,
                    chunk_count=len(chunks),
                )
                prompt_metrics = _effective_structured_prompt_metrics(
                    _serialized_request(
                        self._subject_request_projection(target, request)
                    ),
                    chunk_schema,
                )
                response = self._stable_provider_call(
                    run_id=run_id,
                    run_root=run_root,
                    state=state,
                    target=target,
                    request=request,
                    response_schema=chunk_schema,
                    structured_chat=structured_chat,
                    call_kind=(
                        f"ANALYSIS_SEMANTIC_REPAIR_ROUND_{round_number}_"
                        f"FORM_{form_id}_CHUNK_{chunk_index}_OF_{len(chunks)}"
                    ),
                )
                response_form = response.get(f"form_{form_id}")
                if not isinstance(response_form, Mapping):
                    raise ValueError("ANALYSIS_REPAIR_SHARD_RESPONSE_INVALID")
                source_cases = [
                    target_by_id[case_id].get("source_case")
                    for case_id in chunk_ids
                ]
                if not all(isinstance(case, Mapping) for case in source_cases):
                    raise ValueError("ANALYSIS_REPAIR_SOURCE_CASE_MISSING")
                normalization = self._normalize_case_local_anchor_aliases(
                    form={"cases": source_cases},
                    response=response_form,
                    artifact="claim_map",
                )
                response_form = normalization["normalized_response"]
                state["analysis_anchor_normalizations"].extend(
                    {
                        "form_id": form_id,
                        "artifact": "claim_map",
                        "transport_stage": (
                            f"SEMANTIC_REPAIR_ROUND_{round_number}"
                        ),
                        "shard_index": chunk_index,
                        **deepcopy(action),
                    }
                    for action in normalization["actions"]
                )

                subset_form = {
                    "form": form_id,
                    "cases": deepcopy(source_cases),
                }

                def audit_candidate(
                    candidate: Mapping[str, Any],
                ) -> dict[str, dict[str, Any]]:
                    try:
                        form_schema = chunk_schema["properties"][
                            f"form_{form_id}"
                        ]
                        artifact_properties = form_schema["properties"]
                    except (KeyError, TypeError) as error:
                        raise ValueError(
                            "ANALYSIS_REPAIR_SHARD_SCHEMA_INVALID"
                        ) from error
                    result: dict[str, dict[str, Any]] = {}
                    for artifact in ("claim_map", "analysis_summary"):
                        artifact_schema = {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [artifact],
                            "properties": {
                                artifact: deepcopy(
                                    artifact_properties[artifact]
                                )
                            },
                        }
                        result[artifact] = exam.audit_analysis_artifact(
                            form=subset_form,
                            response={artifact: candidate.get(artifact)},
                            schema=artifact_schema,
                            artifact=artifact,
                        )
                    return result

                audits = audit_candidate(response_form)
                structural_retry_used = 0
                structural_retry_prompt_metrics = None
                if any(
                    audit.get("status") != "PASS"
                    for audit in audits.values()
                ):
                    residual_projections = {
                        artifact: self._bounded_structural_residual_projection(
                            audit
                        )
                        for artifact, audit in audits.items()
                        if audit.get("status") != "PASS"
                    }
                    residual_id_set = {
                        str(case_id)
                        for projection in residual_projections.values()
                        for case_id in projection["repair_exact_set"]
                    }
                    structural_ids = [
                        case_id
                        for case_id in chunk_ids
                        if case_id in residual_id_set
                    ]
                    if not structural_ids:
                        structural_ids = list(chunk_ids)
                    structural_schema = self._project_analysis_repair_schema(
                        repair_schema,
                        form_id=form_id,
                        case_ids=structural_ids,
                    )
                    structural_source_cases = [
                        target_by_id[case_id]["source_case"]
                        for case_id in structural_ids
                    ]
                    structural_current_response = {
                        artifact: [
                            deepcopy(row)
                            for row in response_form.get(artifact, [])
                            if isinstance(row, Mapping)
                            and row.get("case_id") in structural_ids
                        ]
                        for artifact in ("claim_map", "analysis_summary")
                    }
                    structural_request = {
                        "phase": (
                            "ANALYSIS_SEMANTIC_REPAIR_SHARD_STRUCTURAL_REPAIR"
                        ),
                        "semantic_repair_round": round_number,
                        "instruction": (
                            "Repair all and only the listed residual rows from "
                            "the current directed-repair response. Preserve "
                            "its semantic decisions unless a listed structural "
                            "defect requires change. Return complete claim_map "
                            "and analysis_summary rows exactly once in the "
                            "listed order. Use source_cases only to resolve "
                            "anchor identity or role conflicts."
                        ),
                        "residual_exact_set": {
                            form_id: list(structural_ids)
                        },
                        "residual_projections": residual_projections,
                        "source_cases": {
                            form_id: deepcopy(structural_source_cases)
                        },
                        "current_response": {
                            f"form_{form_id}": structural_current_response
                        },
                        "parent_contract_sha256": contract.get(
                            "contract_sha256"
                        ),
                        "successor_lifecycle_sha256": (
                            lifecycle_contract.get("contract_sha256")
                        ),
                        "semantic_repair_round_increment": 0,
                    }
                    structural_retry_prompt_metrics = (
                        _effective_structured_prompt_metrics(
                            _serialized_request(
                                self._subject_request_projection(
                                    target, structural_request
                                )
                            ),
                            structural_schema,
                        )
                    )
                    structural_response = self._stable_provider_call(
                        run_id=run_id,
                        run_root=run_root,
                        state=state,
                        target=target,
                        request=structural_request,
                        response_schema=structural_schema,
                        structured_chat=structured_chat,
                        call_kind=(
                            f"ANALYSIS_SEMANTIC_REPAIR_ROUND_{round_number}_"
                            f"FORM_{form_id}_CHUNK_{chunk_index}_STRUCTURAL_REPAIR"
                        ),
                    )
                    structural_form = structural_response.get(
                        f"form_{form_id}"
                    )
                    if not isinstance(structural_form, Mapping):
                        raise ValueError(
                            "ANALYSIS_REPAIR_SHARD_STRUCTURAL_RESPONSE_INVALID"
                        )
                    structural_normalization = (
                        self._normalize_case_local_anchor_aliases(
                            form={"cases": structural_source_cases},
                            response=structural_form,
                            artifact="claim_map",
                        )
                    )
                    structural_form = structural_normalization[
                        "normalized_response"
                    ]
                    state["analysis_anchor_normalizations"].extend(
                        {
                            "form_id": form_id,
                            "artifact": "claim_map",
                            "transport_stage": (
                                f"SEMANTIC_REPAIR_ROUND_{round_number}_"
                                "STRUCTURAL_REPAIR"
                            ),
                            "shard_index": chunk_index,
                            **deepcopy(action),
                        }
                        for action in structural_normalization["actions"]
                    )
                    for artifact in ("claim_map", "analysis_summary"):
                        replacement_rows = structural_form.get(artifact)
                        observed_replacement_ids = [
                            row.get("case_id")
                            if isinstance(row, Mapping)
                            else None
                            for row in replacement_rows
                        ] if isinstance(replacement_rows, list) else None
                        if observed_replacement_ids != structural_ids:
                            raise ValueError(
                                "ANALYSIS_REPAIR_SHARD_STRUCTURAL_EXACT_SET_"
                                "MISMATCH"
                            )
                        replacement_by_id = {
                            str(row["case_id"]): deepcopy(row)
                            for row in replacement_rows
                        }
                        response_form[artifact] = [
                            replacement_by_id.get(
                                str(row.get("case_id")), deepcopy(row)
                            )
                            for row in response_form.get(artifact, [])
                            if isinstance(row, Mapping)
                        ]
                    structural_retry_used = 1
                    state[
                        "analysis_semantic_repair_structural_retries"
                    ] += 1
                    audits = audit_candidate(response_form)
                if any(
                    audit.get("status") != "PASS"
                    for audit in audits.values()
                ):
                    raise ValueError(
                        "ANALYSIS_REPAIR_SHARD_CONTRACT_UNCLOSED"
                    )
                for artifact in ("claim_map", "analysis_summary"):
                    rows = response_form.get(artifact)
                    observed = [
                        row.get("case_id")
                        if isinstance(row, Mapping)
                        else None
                        for row in rows
                    ] if isinstance(rows, list) else None
                    if observed != chunk_ids:
                        raise ValueError(
                            "ANALYSIS_REPAIR_SHARD_EXACT_SET_MISMATCH"
                        )
                    form_rows[artifact].extend(deepcopy(rows))
                receipts.append(
                    {
                        "form_id": form_id,
                        "chunk_index": chunk_index,
                        "chunk_count": len(chunks),
                        "case_ids": list(chunk_ids),
                        "case_ids_sha256": _sha256(chunk_ids),
                        "response_sha256": _sha256(response),
                        "normalized_response_form_sha256": _sha256(
                            response_form
                        ),
                        "effective_prompt_metrics": prompt_metrics,
                        "artifact_audits": {
                            artifact: {
                                "status": audit.get("status"),
                                "defect_count": audit.get("defect_count"),
                                "repair_exact_set": audit.get(
                                    "repair_exact_set"
                                ),
                                "audit_sha256": audit.get("audit_sha256"),
                            }
                            for artifact, audit in audits.items()
                        },
                        "structural_retry_used": structural_retry_used,
                        "structural_retry_prompt_metrics": (
                            structural_retry_prompt_metrics
                        ),
                    }
                )
            merged[f"form_{form_id}"] = form_rows
        _write_json_create_only(
            run_root
            / "responses"
            / f"analysis_semantic_repair_round_{round_number}_shards.json",
            {
                "schema_version": "AnalysisRepairTransportShards-v1",
                "semantic_repair_round": round_number,
                "parent_contract_sha256": contract.get("contract_sha256"),
                "sharding_revision": (
                    ANALYSIS_PRIMARY_SEMANTIC_REPAIR_SHARDING_REVISION
                ),
                "provider_received_answer_key": False,
                "shards": receipts,
                "merged_response_sha256": _sha256(merged),
            },
        )
        return merged

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
            "request_attempts": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
            "external_network_calls": 0,
            "external_process_launches": 0,
            "estimated_cost_cny": 0.0,
            "token_usage": {"prompt_tokens": 0, "completion_tokens": 0},
            "analysis_anchor_normalizations": [],
            "analysis_semantic_repair_structural_retries": 0,
            "analysis_preloaded_initial_rows": [],
        }
        try:
            if plan.get("cohort_already_complete") is True:
                cohort_basis = self._panel_cohort_basis(
                    str(plan["reference_pack_sha256"])
                )
                completed_records = self._completed_panel_records(
                    target=target,
                    cohort_basis=cohort_basis,
                    cohort_slots=list(plan["panel_cohort_slots"]),
                )
                if len(completed_records) != self.PANELS_REQUIRED_FOR_CACHE:
                    raise ValueError(
                        "ANALYSIS_PANEL_COHORT_REUSE_SOURCE_INCOMPLETE"
                    )
                source_panel = max(
                    completed_records,
                    key=lambda record: int(record["sample_slot"]),
                )
                source_run_id = str(source_panel["panel_id"])
                source_run_root = (
                    self.scratch_root / "workflow_exams" / source_run_id
                )
                source_cohort_path = (
                    source_run_root
                    / "panel_scores"
                    / "cohort_result.json"
                )
                source_result_path = source_run_root / "exam_result.json"
                source_cohort = _load_json(source_cohort_path)
                stored_cohort_hash = source_cohort.get(
                    "cohort_result_sha256"
                )
                unsigned_cohort = {
                    key: value
                    for key, value in source_cohort.items()
                    if key != "cohort_result_sha256"
                }
                if (
                    not isinstance(stored_cohort_hash, str)
                    or stored_cohort_hash != _sha256(unsigned_cohort)
                    or source_cohort.get("stable_score_cache_eligible")
                    is not True
                    or source_cohort.get("comparison_binding_sha256")
                    != plan["comparison_binding_sha256"]
                ):
                    raise ValueError(
                        "ANALYSIS_PANEL_COHORT_REUSE_SOURCE_INVALID"
                    )
                source_public_result = _load_json(source_result_path)
                if (
                    source_public_result.get("stable_score_cache_eligible")
                    is not True
                    or source_public_result.get("cohort_binding_sha256")
                    != source_cohort.get("cohort_binding_sha256")
                    or source_public_result.get(
                        "comparison_binding_sha256"
                    )
                    != plan["comparison_binding_sha256"]
                ):
                    raise ValueError(
                        "ANALYSIS_PANEL_COHORT_REUSE_RESULT_INVALID"
                    )
                copied_cohort_receipt = _write_json_create_only(
                    run_root / "panel_scores" / "cohort_result.json",
                    source_cohort,
                )
                reuse_receipt = {
                    "schema_version": (
                        "DesktopAnalysisPrimaryCohortReuseReceipt-v1"
                    ),
                    "source_run_id": source_run_id,
                    "source_exam_result_sha256": _file_sha256(
                        source_result_path
                    ),
                    "source_cohort_result_sha256": _file_sha256(
                        source_cohort_path
                    ),
                    "cohort_binding_sha256": source_cohort[
                        "cohort_binding_sha256"
                    ],
                    "comparison_binding_sha256": plan[
                        "comparison_binding_sha256"
                    ],
                    "external_model_calls": 0,
                    "provider_received_answer_key": False,
                }
                reuse_receipt["reuse_receipt_sha256"] = _sha256(
                    reuse_receipt
                )
                reuse_receipt_file = _write_json_create_only(
                    run_root / "score_cache_reuse_receipt.json",
                    reuse_receipt,
                )
                public_result = deepcopy(source_public_result)
                public_result.update(
                    {
                        "schema_version": (
                            "WorkflowModelExamRunResult-v1"
                        ),
                        "status": source_cohort["status"],
                        "reason": (
                            "ANALYSIS_PRIMARY_THREE_PANEL_COHORT_REUSED"
                        ),
                        "run_id": run_id,
                        "plan_sha256": plan["plan_sha256"],
                        "request_attempts": 0,
                        "provider_calls": 0,
                        "external_model_calls": 0,
                        "external_network_calls": 0,
                        "external_process_launches": 0,
                        "token_usage": {
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                        },
                        "estimated_cost_cny": (
                            0.0
                            if plan["profile_kind"] in {"API", "LOCAL"}
                            else None
                        ),
                        "actual_cost": None,
                        "cost_semantics": (
                            "CACHE_REUSE_NO_EXTERNAL_CALL"
                        ),
                        "score_cache_reused": True,
                        "reused_from_run_id": source_run_id,
                        "cohort_result": (
                            "panel_scores/cohort_result.json"
                        ),
                        "cohort_result_file_sha256": (
                            copied_cohort_receipt["sha256"]
                        ),
                        "score_cache_reuse_receipt": (
                            "score_cache_reuse_receipt.json"
                        ),
                        "score_cache_reuse_receipt_sha256": (
                            reuse_receipt_file["sha256"]
                        ),
                        "panel_record": None,
                        "panel_record_sha256": None,
                        "current_panel_score_diagnostic": None,
                        "current_panel_form_scores": None,
                        "current_panel_first_pass_role_ability_score": None,
                        "semantic_repair_rounds": 0,
                        "semantic_repair_lifecycle": [],
                        "semantic_repair_acceptance": [],
                        "anchor_normalization_count": 0,
                        "anchor_normalization_receipt": None,
                        "anchor_normalization_receipt_sha256": None,
                    }
                )
                evidence = _write_json_create_only(
                    run_root / "exam_result.json", public_result
                )
                return {
                    **public_result,
                    "schema_version": RESULT_SCHEMA,
                    "kind": "WORKFLOW_NODE",
                    "duration_ms": max(
                        0, int((monotonic() - started) * 1000)
                    ),
                    "exam_mode": plan["mode"],
                    "exam_run_id": run_id,
                    "exam_run_root": str(run_root.resolve()),
                    "exam_evidence_sha256": evidence["sha256"],
                }
            modules = self._quality()
            protocol = modules["protocol"]
            exam = modules["exam"]
            pack = modules["reference"].load_analysis_reference_exam_pack(
                self._reference_pack_root
            )
            source_schema = _load_json(
                pack.member("schemas/analysis_primary_output.schema.json")
            )
            inputs = self._exam_inputs(
                pack=pack,
                source_schema=source_schema,
                context={
                    **dict(context),
                    "workflow_exam_sample_slot": plan["sample_slot"],
                },
                protocol=protocol,
            )
            full_schema = inputs["schema"]
            forms = inputs["forms"]
            golds = inputs["golds"]
            if inputs["sample_metadata"]["manifest_sha256"] != plan.get(
                "sample_manifest_sha256"
            ):
                raise ValueError(
                    "WORKFLOW_MODEL_EXAM_ANALYSIS_SAMPLE_MANIFEST_MISMATCH"
                )
            _write_json_create_only(
                run_root / "sample_manifest.json",
                inputs["sample_metadata"],
            )
            responses: dict[str, dict[str, Any]] = {}
            raw_scores: dict[str, dict[str, Any]] = {}
            for form_id in ("A", "B"):
                merged: dict[str, list[Any]] = {
                    "claim_map": [],
                    "analysis_summary": [],
                }
                for artifact in ("claim_map", "analysis_summary"):
                    shards = self._shards(
                        forms[form_id],
                        form_id=form_id,
                        artifact=artifact,
                        profile_kind=str(target["kind"]),
                    )
                    for shard_index, shard in enumerate(shards, start=1):
                        preload = self._preloaded_initial_artifact_rows(
                            context=context,
                            target=target,
                            sample_slot=int(plan["sample_slot"]),
                            form_id=form_id,
                            artifact=artifact,
                            target_case_ids=list(shard["target_ids"]),
                        )
                        case_by_id = {
                            str(row["case_id"]): deepcopy(dict(row))
                            for row in shard["target_form"]["cases"]
                        }

                        def subset_form(case_ids: list[str]) -> dict[str, Any]:
                            projected = deepcopy(dict(shard["target_form"]))
                            projected["cases"] = [
                                deepcopy(case_by_id[case_id])
                                for case_id in case_ids
                            ]
                            return projected

                        preloaded_rows = deepcopy(preload["rows"])
                        if preloaded_rows:
                            preloaded_form = subset_form(
                                list(preload["reused_case_ids"])
                            )
                            preloaded_schema = protocol.shard_artifact_schema(
                                full_schema,
                                artifact,
                                target_count=len(preloaded_rows),
                            )
                            preloaded_response = {artifact: preloaded_rows}
                            normalization = (
                                self._normalize_case_local_anchor_aliases(
                                    form=preloaded_form,
                                    response=preloaded_response,
                                    artifact=artifact,
                                )
                            )
                            preloaded_response = normalization[
                                "normalized_response"
                            ]
                            state["analysis_anchor_normalizations"].extend(
                                {
                                    "form_id": form_id,
                                    "artifact": artifact,
                                    "transport_stage": "PRELOADED_INITIAL",
                                    "shard_index": shard_index,
                                    **deepcopy(action),
                                }
                                for action in normalization["actions"]
                            )
                            preload_audit = exam.audit_analysis_artifact(
                                form=preloaded_form,
                                response=preloaded_response,
                                schema=preloaded_schema,
                                artifact=artifact,
                            )
                            if preload_audit.get("status") != "PASS":
                                raise ValueError(
                                    "ANALYSIS_PRELOAD_ARTIFACT_INVALID"
                                )
                            preloaded_rows = deepcopy(
                                preloaded_response[artifact]
                            )

                        live_rows: list[Any] = []
                        missing_case_ids = list(preload["missing_case_ids"])
                        if missing_case_ids:
                            missing_form = subset_form(missing_case_ids)
                            schema = protocol.shard_artifact_schema(
                                full_schema,
                                artifact,
                                target_count=len(missing_case_ids),
                            )
                            prompt = protocol.build_artifact_prompt(
                                missing_form,
                                artifact,
                                target_case_ids=missing_case_ids,
                            )
                            request = {
                                "phase": "ANALYSIS_REFERENCE_EXAM",
                                "artifact": artifact,
                                "target_case_ids": missing_case_ids,
                                "subject_prompt": prompt,
                            }
                            response = self._stable_provider_call(
                                run_id=run_id,
                                run_root=run_root,
                                state=state,
                                target=target,
                                request=request,
                                response_schema=schema,
                                structured_chat=structured_chat,
                                call_kind=(
                                    f"FORM_{form_id}_{artifact.upper()}_SHARD_{shard_index:02d}"
                                ),
                            )
                            normalization = self._normalize_case_local_anchor_aliases(
                                form=missing_form,
                                response=response,
                                artifact=artifact,
                            )
                            response = normalization["normalized_response"]
                            state["analysis_anchor_normalizations"].extend(
                                {
                                    "form_id": form_id,
                                    "artifact": artifact,
                                    "transport_stage": "INITIAL",
                                    "shard_index": shard_index,
                                    **deepcopy(action),
                                }
                                for action in normalization["actions"]
                            )
                            audit = exam.audit_analysis_artifact(
                                form=missing_form,
                                response=response,
                                schema=schema,
                                artifact=artifact,
                            )
                            if audit.get("status") != "PASS":
                                residual_projection = (
                                    self._bounded_structural_residual_projection(
                                        audit
                                    )
                                )
                                repair_request = {
                                    "phase": "ANALYSIS_STRUCTURAL_REPAIR",
                                    "instruction": (
                                        "Repair only the listed structural residuals in "
                                        "the current response. Use each defect_locator, "
                                        "including any named anchor, as an exact locator. "
                                        "Return the complete artifact rows for all and only "
                                        "target_case_ids in their declared order."
                                    ),
                                    "artifact": artifact,
                                    "target_case_ids": missing_case_ids,
                                    "residual_projection": residual_projection,
                                    "current_response": response,
                                    "subject_prompt": prompt,
                                }
                                response = self._stable_provider_call(
                                    run_id=run_id,
                                    run_root=run_root,
                                    state=state,
                                    target=target,
                                    request=repair_request,
                                    response_schema=schema,
                                    structured_chat=structured_chat,
                                    call_kind=(
                                        f"FORM_{form_id}_{artifact.upper()}_SHARD_{shard_index:02d}_REPAIR"
                                    ),
                                )
                                normalization = (
                                    self._normalize_case_local_anchor_aliases(
                                        form=missing_form,
                                        response=response,
                                        artifact=artifact,
                                    )
                                )
                                response = normalization[
                                    "normalized_response"
                                ]
                                state["analysis_anchor_normalizations"].extend(
                                    {
                                        "form_id": form_id,
                                        "artifact": artifact,
                                        "transport_stage": "STRUCTURAL_REPAIR",
                                        "shard_index": shard_index,
                                        **deepcopy(action),
                                    }
                                    for action in normalization["actions"]
                                )
                                audit = exam.audit_analysis_artifact(
                                    form=missing_form,
                                    response=response,
                                    schema=schema,
                                    artifact=artifact,
                                )
                            if audit.get("status") != "PASS":
                                raise ValueError(
                                    "WORKFLOW_MODEL_EXAM_ANALYSIS_SHARD_CONTRACT_UNCLOSED"
                                )
                            rows = response.get(artifact)
                            if not isinstance(rows, list):
                                raise ValueError(
                                    "WORKFLOW_MODEL_EXAM_ANALYSIS_SHARD_INVALID"
                                )
                            live_rows = deepcopy(rows)

                        combined_rows = preloaded_rows + live_rows
                        combined_by_id: dict[str, Any] = {}
                        for row in combined_rows:
                            if (
                                not isinstance(row, Mapping)
                                or not isinstance(row.get("case_id"), str)
                                or row["case_id"] in combined_by_id
                            ):
                                raise ValueError(
                                    "WORKFLOW_MODEL_EXAM_ANALYSIS_SHARD_INVALID"
                                )
                            combined_by_id[str(row["case_id"])] = deepcopy(row)
                        if set(combined_by_id) != set(shard["target_ids"]):
                            raise ValueError(
                                "WORKFLOW_MODEL_EXAM_ANALYSIS_SHARD_EXACT_SET_INVALID"
                            )
                        ordered_rows = [
                            combined_by_id[case_id]
                            for case_id in shard["target_ids"]
                        ]
                        combined_schema = protocol.shard_artifact_schema(
                            full_schema,
                            artifact,
                            target_count=len(shard["target_ids"]),
                        )
                        combined_audit = exam.audit_analysis_artifact(
                            form=shard["target_form"],
                            response={artifact: ordered_rows},
                            schema=combined_schema,
                            artifact=artifact,
                        )
                        if combined_audit.get("status") != "PASS":
                            raise ValueError(
                                "WORKFLOW_MODEL_EXAM_ANALYSIS_GLOBAL_AUDIT_FAILED"
                            )
                        if preloaded_rows:
                            state["analysis_preloaded_initial_rows"].append(
                                {
                                    "form_id": form_id,
                                    "artifact": artifact,
                                    "shard_index": shard_index,
                                    "reused_case_ids": list(
                                        preload["reused_case_ids"]
                                    ),
                                    "missing_case_ids": missing_case_ids,
                                    "source_calls": deepcopy(
                                        preload["source_calls"]
                                    ),
                                    "provider_calls_for_reused_rows": 0,
                                    "rows_sha256": _sha256(preloaded_rows),
                                }
                            )
                        merged[artifact].extend(deepcopy(ordered_rows))
                response = {
                    "claim_map": merged["claim_map"],
                    "analysis_summary": merged["analysis_summary"],
                }
                for artifact in ("claim_map", "analysis_summary"):
                    audit = exam.audit_analysis_artifact(
                        form=forms[form_id],
                        response={artifact: response[artifact]},
                        schema=protocol.artifact_schema(full_schema, artifact),
                        artifact=artifact,
                    )
                    if audit.get("status") != "PASS":
                        raise ValueError(
                            "WORKFLOW_MODEL_EXAM_ANALYSIS_GLOBAL_AUDIT_FAILED"
                        )
                raw = protocol.score_response(
                    response,
                    golds[form_id],
                    full_schema,
                )
                responses[form_id] = deepcopy(response)
                raw_scores[form_id] = deepcopy(raw)

            initial_preload_receipt = None
            if state["analysis_preloaded_initial_rows"]:
                preload_payload = {
                    "schema_version": (
                        "DesktopAnalysisPrimaryExactProgressReuseReceipt-v1"
                    ),
                    "sample_slot": plan["sample_slot"],
                    "requested_model": requested_model,
                    "returned_model": state.get("returned_model") if target.get("adapter_id")=="command_template" else requested_model,
                    "reuse_actions": deepcopy(
                        state["analysis_preloaded_initial_rows"]
                    ),
                    "reused_case_row_count": sum(
                        len(action["reused_case_ids"])
                        for action in state[
                            "analysis_preloaded_initial_rows"
                        ]
                    ),
                    "provider_calls_for_reused_rows": 0,
                    "provider_received_answer_key": False,
                    "length_only_repair_outputs_reused": False,
                    "completed_initial_answers_retested": False,
                }
                preload_payload["reuse_receipt_sha256"] = _sha256(
                    preload_payload
                )
                initial_preload_receipt = _write_json_create_only(
                    run_root / "initial_exact_progress_reuse_receipt.json",
                    preload_payload,
                )

            first_pass_form_scores = {
                form_id: float(
                    exam.score_analysis_form_v2(
                        raw_scoring=raw_scores[form_id]
                    )["score"]
                )
                for form_id in ("A", "B")
            }
            first_pass_role_ability_score = round(
                sum(first_pass_form_scores.values()) / 2.0, 2
            )
            for form_id in ("A", "B"):
                _write_json_create_only(
                    run_root
                    / "panel_scores"
                    / f"form_{form_id}_initial_raw_projection.json",
                    self._analysis_cohort_raw_projection(raw_scores[form_id]),
                )
            current_responses = deepcopy(responses)
            current_scores = deepcopy(raw_scores)
            initial_contract: Mapping[str, Any] | None = None
            first_round_exact_sets: dict[str, list[str]] | None = None
            repair_lifecycle: list[dict[str, Any]] = []
            repair_burdens: list[dict[str, Any]] = []
            repair_acceptance_receipts: list[dict[str, Any]] = []
            unchanged_surface_sha256 = _sha256(
                {
                    "forms": forms,
                    "schema": full_schema,
                    "category_id": self.EXAM_CATEGORY_ID,
                    "provider_receives_answer_key": False,
                }
            )
            for round_number in range(
                1, self.SEMANTIC_REPAIR_ROUND_LIMIT + 1
            ):
                wrong_sets = self._analysis_wrong_sets(current_scores)
                if not any(wrong_sets.values()):
                    break
                source_scores = deepcopy(current_scores)
                for form_id in ("A", "B"):
                    target_ids = set(wrong_sets[form_id])
                    source_scores[form_id]["details"] = [
                        deepcopy(detail)
                        for detail in source_scores[form_id].get(
                            "details", []
                        )
                        if isinstance(detail, Mapping)
                        and detail.get("case_id") in target_ids
                    ]
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
                repair_burdens.append(
                    self._analysis_category_burden(
                        exam=exam,
                        forms=forms,
                        scores=source_scores,
                        exact_sets=exact_sets,
                    )
                )
                repair_response = self._run_analysis_semantic_repair(
                    run_id=run_id,
                    run_root=run_root,
                    state=state,
                    target=target,
                    package=package,
                    lifecycle_contract=lifecycle,
                    round_number=round_number,
                    structured_chat=structured_chat,
                )
                merged_repair = exam.merge_exam_directed_repair(
                    forms=forms,
                    original_responses=current_responses,
                    repair_response=repair_response,
                    schema=full_schema,
                    repair_contract=package["contract"],
                )
                candidate_responses = deepcopy(merged_repair["responses"])
                candidate_scores = {
                    form_id: protocol.score_response(
                        candidate_responses[form_id],
                        golds[form_id],
                        full_schema,
                    )
                    for form_id in ("A", "B")
                }
                acceptance = analysis_repair_acceptance(
                    before_scores=current_scores,
                    candidate_scores=candidate_scores,
                    targets_by_form=exact_sets,
                )
                current_responses = self._analysis_case_local_acceptance_merge(
                    original_responses=current_responses,
                    candidate_responses=candidate_responses,
                    accepted_by_form=acceptance["accepted_by_form"],
                )
                current_scores = {
                    form_id: protocol.score_response(
                        current_responses[form_id],
                        golds[form_id],
                        full_schema,
                    )
                    for form_id in ("A", "B")
                }
                repair_acceptance_receipts.append(acceptance)
                repair_lifecycle.append(lifecycle)

            residual_materiality = analysis_material_repair_targets(
                current_scores
            )
            residual_sets = deepcopy(
                residual_materiality["targets_by_form"]
            )
            calibration_sets = deepcopy(
                residual_materiality["calibration_only_by_form"]
            )
            residual_burden = self._analysis_category_burden(
                exam=exam,
                forms=forms,
                scores=current_scores,
                exact_sets=residual_sets,
            )
            penalty_round_rows: list[dict[str, Any]] = []
            for round_index, burden in enumerate(repair_burdens, start=1):
                penalty_round_rows.append(
                    {
                        "round_number": round_index,
                        "category_id": self.EXAM_CATEGORY_ID,
                        "nonlinear_category_penalty": burden["total_mean"],
                        "fixed_penalty": 0.0,
                        "volume_penalty": 0.0,
                    }
                )
            cumulative_penalty = (
                cumulative_category_repair_penalty(penalty_round_rows)
                if penalty_round_rows
                else None
            )
            form_results: dict[str, dict[str, Any]] = {}
            form_scores: dict[str, float] = {}
            for form_id in ("A", "B"):
                visible_output_chars = len(
                    _serialized_request(current_responses[form_id])
                )
                piecewise = exam.score_analysis_form_v2(
                    raw_scoring=current_scores[form_id]
                )
                base_score = float(piecewise["score"])
                form_score = round(max(0.0, base_score), 2)
                form_scores[form_id] = form_score
                form_results[form_id] = {
                    "status": "SCORED",
                    "score": form_score,
                    "score_before_successor_repair_burden": base_score,
                    "repair_burden_applied_to_quality_score": False,
                    "quality_hard_gate": current_scores[form_id][
                        "quality_hard_gate"
                    ],
                    "critical_count": current_scores[form_id][
                        "critical_count"
                    ],
                    "repair_round_burdens": [
                        deepcopy(burden["by_form"][form_id])
                        for burden in repair_burdens
                    ],
                    "residual_burden": deepcopy(
                        residual_burden["by_form"][form_id]
                    ),
                    "calibration_only_case_ids": calibration_sets[form_id],
                    "visible_output_chars": visible_output_chars,
                    "visible_output_reference_ceiling_chars": (
                        Core_PRIMARY_ANALYSIS_OUTPUT_CHAR_LIMIT
                    ),
                    "visible_output_exceeds_reference": (
                        visible_output_chars
                        > Core_PRIMARY_ANALYSIS_OUTPUT_CHAR_LIMIT
                    ),
                    "visible_output_reference_semantics": "DIAGNOSTIC_ONLY",
                }
                _write_json_create_only(
                    run_root
                    / "panel_scores"
                    / f"form_{form_id}_final_raw_projection.json",
                    self._analysis_cohort_raw_projection(
                        current_scores[form_id]
                    ),
                )
                _write_json_create_only(
                    run_root / "responses" / f"form_{form_id}_final.json",
                    current_responses[form_id],
                )
            failed_item_count = sum(
                len(case_ids) for case_ids in residual_sets.values()
            )
            calibration_residual_count = sum(
                len(case_ids) for case_ids in calibration_sets.values()
            )
            publication = exam.publish_analysis_exam_result(
                provider=str(
                    target.get("provider")
                    or target.get("adapter_id")
                    or target.get("endpoint_kind")
                ),
                model=str(target["model_name"]),
                parameters={
                    "profile_kind": target["kind"],
                    "model_digest": target.get("model_digest"),
                    "base_model_calls": plan["base_model_calls"],
                    "maximum_model_calls": plan["maximum_model_calls"],
                },
                forms=form_results,
                evidence_refs=[
                    "responses/form_A_final.json",
                    "responses/form_B_final.json",
                ],
            )
            score = int(round(float(publication["total_score_out_of_100"])))
            panel_quality_candidate_verdict = (
                "PASS"
                if score >= 80
                and failed_item_count == 0
                and all(
                    current_scores[form_id]["quality_hard_gate"] == "PASS"
                    for form_id in ("A", "B")
                )
                else "FAIL"
            )
            blocking_failures: list[dict[str, Any]] = []
            for form_id in ("A", "B"):
                residual_ids = set(residual_sets[form_id])
                for detail in current_scores[form_id].get("details", []):
                    if (
                        not isinstance(detail, Mapping)
                        or detail.get("case_id") not in residual_ids
                    ):
                        continue
                    failed_fields = sorted(
                        {
                            str(reason)
                            for reason in (detail.get("critical_reasons") or [])
                            if isinstance(reason, str) and reason
                        }
                    )
                    if not failed_fields:
                        failed_fields = ["CASE_SCORE_BELOW_80_MATERIAL"]
                    blocking_failures.append(
                        {
                            "case_id": str(detail["case_id"]),
                            "failed_fields": failed_fields,
                            "evidence_anchor": (
                                f"scores/form_{form_id}.json#"
                                f"case_details/{detail['case_id']}"
                            ),
                            "blocking": True,
                            "model_fail_materiality": "MATERIAL",
                        }
                    )
            material_residual_case_ids = sorted(
                str(row["case_id"]) for row in blocking_failures
            )
            calibration_residual_case_ids = sorted(
                case_id
                for case_ids in calibration_sets.values()
                for case_id in case_ids
            )
            model_fail_projection = {
                "model_fail_policy_revision": (
                    PRIMARY_MODEL_FAIL_POLICY_REVISION
                ),
                "model_fail_verdict": "NOT_ASSESSED",
                "model_fail_established": False,
                "model_fail_minimum_distinct_material_cases": None,
                "material_residual_case_ids": material_residual_case_ids,
                "calibration_residual_case_ids": (
                    calibration_residual_case_ids
                ),
                "operational_eligibility_verdict": (
                    "PENDING_THREE_PANEL_COHORT"
                ),
                "category_id": self.EXAM_CATEGORY_ID,
                "category_local_score": round(float(score), 2),
                "numeric_score_is_not_model_fail_verdict": True,
                "single_panel_cannot_establish_model_fail": True,
                "cross_category_comparison_forbidden": True,
                "global_ranking_forbidden": True,
            }
            raw_quality_fail_admission = None
            quality_fail_admission = None
            verdict = "NOT_ASSESSED"
            cap = plan.get("budget_cap_cny")
            if cap is not None and state["estimated_cost_cny"] > float(cap):
                raise ValueError("WORKFLOW_MODEL_EXAM_COST_CAP_EXCEEDED")
            normalization_receipt = _write_json_create_only(
                run_root
                / "normalizations"
                / "analysis_case_local_anchor_aliases.json",
                {
                    "schema_version": (
                        ANALYSIS_PRIMARY_ANCHOR_NORMALIZATION_REVISION
                    ),
                    "normalization_count": len(
                        state["analysis_anchor_normalizations"]
                    ),
                    "actions": deepcopy(
                        state["analysis_anchor_normalizations"]
                    ),
                    "gold_consulted": False,
                    "provider_call_required": False,
                    "semantic_repair_round_increment": 0,
                },
            )
            panel_record = {
                "schema_version": "DesktopAnalysisPrimaryPanelRecord-v1",
                "panel_id": run_id,
                "sample_slot": plan["sample_slot"],
                "panel_binding_sha256": plan["panel_binding_sha256"],
                "cohort_basis": {
                    "category_id": self.EXAM_CATEGORY_ID,
                    "reference_pack_sha256": plan[
                        "reference_pack_sha256"
                    ],
                    "sample_revision": plan["sample_revision"],
                    "subject_projection_revision": plan[
                        "subject_projection_revision"
                    ],
                    "executor_ref": self.EXECUTOR_REF,
                },
                "model_identity": self._panel_model_identity(target),
                "selection": deepcopy(
                    inputs["sample_metadata"]["selection"]
                ),
                "initial_raw_scores": {
                    form_id: self._analysis_cohort_raw_projection(
                        raw_scores[form_id]
                    )
                    for form_id in ("A", "B")
                },
                "raw_scores": {
                    form_id: self._analysis_cohort_raw_projection(
                        current_scores[form_id]
                    )
                    for form_id in ("A", "B")
                },
                "panel_quality_candidate_verdict": (
                    panel_quality_candidate_verdict
                ),
                "panel_score_diagnostic": score,
                "first_pass_panel_score_diagnostic": (
                    first_pass_role_ability_score
                ),
                "material_residual_case_ids_by_form": deepcopy(
                    residual_sets
                ),
                "calibration_residual_case_ids_by_form": deepcopy(
                    calibration_sets
                ),
                "semantic_repair_rounds": len(repair_lifecycle),
                "semantic_repair_acceptance": deepcopy(
                    repair_acceptance_receipts
                ),
                "panel_evidence_only": True,
                "single_panel_cache_eligible": False,
                "provider_received_answer_key": False,
                "initial_exact_progress_reuse": bool(
                    state["analysis_preloaded_initial_rows"]
                ),
                "initial_reused_case_row_count": sum(
                    len(action["reused_case_ids"])
                    for action in state[
                        "analysis_preloaded_initial_rows"
                    ]
                ),
            }
            panel_record["panel_record_sha256"] = _sha256(panel_record)
            panel_record_receipt = _write_json_create_only(
                run_root / "panel_scores" / "panel_record.json",
                panel_record,
            )
            panel_score_diagnostic = score
            panel_form_scores = deepcopy(form_scores)
            panel_first_pass_role_ability_score = (
                first_pass_role_ability_score
            )
            result_reason = (
                "ANALYSIS_PRIMARY_PANEL_COMPLETED_COHORT_PENDING"
            )
            panel_evidence_only = True
            stable_score_cache_eligible = False
            horizontal_comparison_eligible = False
            horizontal_comparison_pending = True
            cohort_panel_count = 1
            cohort_sample_slots = [int(plan["sample_slot"])]
            cohort_binding_sha256: str | None = None
            cohort_result_receipt: dict[str, Any] | None = None
            cohort_difficulty_coverage: dict[str, Any] | None = None
            cohort_panel_score_range: float | None = None
            completed_panel_records = self._completed_panel_records(
                target=target,
                cohort_basis=self._panel_cohort_basis(
                    str(plan["reference_pack_sha256"])
                ),
                cohort_slots=list(plan["panel_cohort_slots"]),
            )
            cohort_panel_count = len(completed_panel_records)
            cohort_sample_slots = sorted(
                int(record["sample_slot"])
                for record in completed_panel_records
            )
            if len(completed_panel_records) == self.PANELS_REQUIRED_FOR_CACHE:
                final_cohort = build_analysis_panel_cohort(
                    completed_panel_records
                )
                initial_panel_records: list[dict[str, Any]] = []
                for record in completed_panel_records:
                    initial_scores = record.get("initial_raw_scores")
                    if not isinstance(initial_scores, Mapping):
                        raise ValueError(
                            "ANALYSIS_PANEL_INITIAL_RAW_SCORES_REQUIRED"
                        )
                    initial_record = deepcopy(record)
                    initial_record["raw_scores"] = deepcopy(
                        dict(initial_scores)
                    )
                    initial_panel_records.append(initial_record)
                initial_cohort = build_analysis_panel_cohort(
                    initial_panel_records
                )
                aggregate_raw_scores = final_cohort[
                    "aggregate_raw_scores"
                ]
                aggregate_initial_raw_scores = initial_cohort[
                    "aggregate_raw_scores"
                ]
                form_scores = {
                    form_id: round(
                        float(
                            exam.score_analysis_form_v2(
                                raw_scoring=aggregate_raw_scores[form_id]
                            )["score"]
                        ),
                        2,
                    )
                    for form_id in ("A", "B")
                }
                first_pass_form_scores = {
                    form_id: round(
                        float(
                            exam.score_analysis_form_v2(
                                raw_scoring=aggregate_initial_raw_scores[
                                    form_id
                                ]
                            )["score"]
                        ),
                        2,
                    )
                    for form_id in ("A", "B")
                }
                score = int(
                    round(sum(form_scores.values()) / len(form_scores))
                )
                first_pass_role_ability_score = round(
                    sum(first_pass_form_scores.values())
                    / len(first_pass_form_scores),
                    2,
                )
                cohort_materiality = analysis_material_repair_targets(
                    aggregate_raw_scores
                )
                residual_sets = deepcopy(
                    cohort_materiality["targets_by_form"]
                )
                calibration_sets = deepcopy(
                    cohort_materiality["calibration_only_by_form"]
                )
                material_rows: list[dict[str, Any]] = []
                for form_id in ("A", "B"):
                    detail_by_id = {
                        str(detail.get("case_id")): detail
                        for detail in aggregate_raw_scores[form_id].get(
                            "details", []
                        )
                        if isinstance(detail, Mapping)
                    }
                    owner_by_id: dict[str, str] = {}
                    for record in completed_panel_records:
                        selected = record.get("selection", {}).get(
                            form_id, []
                        )
                        if not isinstance(selected, list):
                            raise ValueError(
                                "ANALYSIS_PANEL_SELECTION_INVALID"
                            )
                        for item in selected:
                            if isinstance(item, Mapping) and isinstance(
                                item.get("case_id"), str
                            ):
                                owner_by_id[str(item["case_id"])] = str(
                                    record["panel_id"]
                                )
                    for case_id in residual_sets[form_id]:
                        detail = detail_by_id.get(case_id)
                        panel_id = owner_by_id.get(case_id)
                        if detail is None or panel_id is None:
                            raise ValueError(
                                "ANALYSIS_COHORT_RESIDUAL_OWNER_MISSING"
                            )
                        family = str(detail.get("family") or "")
                        if not family:
                            raise ValueError(
                                "ANALYSIS_COHORT_RESIDUAL_FAMILY_MISSING"
                            )
                        failed_fields = sorted(
                            {
                                str(reason)
                                for reason in (
                                    detail.get("critical_reasons") or []
                                )
                                if isinstance(reason, str) and reason
                            }
                        )
                        if not failed_fields:
                            failed_fields = [
                                "CASE_SCORE_BELOW_80_MATERIAL"
                            ]
                        material_rows.append(
                            {
                                "form_id": form_id,
                                "case_id": case_id,
                                "panel_id": panel_id,
                                "family": family,
                                "failed_fields": failed_fields,
                                "evidence_anchor": (
                                    "panel_scores/cohort_result.json#"
                                    f"aggregate_raw_scores/{form_id}/"
                                    f"details/{case_id}"
                                ),
                                "blocking": True,
                                "model_fail_materiality": "MATERIAL",
                            }
                        )
                failed_item_count = len(material_rows)
                calibration_residual_count = sum(
                    len(case_ids)
                    for case_ids in calibration_sets.values()
                )
                cohort_fail_decision = (
                    analysis_cohort_model_fail_decision(
                        score=float(score),
                        material_residuals=material_rows,
                        quality_hard_gate_pass=all(
                            aggregate_raw_scores[form_id][
                                "quality_hard_gate"
                            ]
                            == "PASS"
                            for form_id in ("A", "B")
                        ),
                    )
                )
                strict_model_fail = bool(
                    cohort_fail_decision["model_fail_established"]
                )
                cohort_quality_pass = (
                    cohort_fail_decision["status"] == "PASS"
                )
                verdict = str(cohort_fail_decision["status"])
                if verdict in {"PASS", "FAIL"}:
                    result_reason = (
                        "ANALYSIS_PRIMARY_THREE_PANEL_COHORT_COMPLETED"
                    )
                else:
                    verdict = "NOT_ASSESSED"
                    result_reason = (
                        "ANALYSIS_PRIMARY_THREE_PANEL_COHORT_INCONCLUSIVE"
                    )
                panel_quality_candidate_verdict = (
                    "PASS" if cohort_quality_pass else "FAIL"
                )
                blocking_failures = deepcopy(material_rows)
                material_residual_case_ids = sorted(
                    f"{row['form_id']}:{row['case_id']}"
                    for row in material_rows
                )
                calibration_residual_case_ids = sorted(
                    f"{form_id}:{case_id}"
                    for form_id, case_ids in calibration_sets.items()
                    for case_id in case_ids
                )
                model_fail_projection = {
                    "model_fail_policy_revision": (
                        PRIMARY_MODEL_FAIL_POLICY_REVISION
                    ),
                    "cohort_fail_policy_revision": (
                        cohort_fail_decision["schema_version"]
                    ),
                    "model_fail_verdict": cohort_fail_decision[
                        "model_fail_verdict"
                    ],
                    "model_fail_established": strict_model_fail,
                    "model_fail_minimum_distinct_material_cases": (
                        cohort_fail_decision[
                            "minimum_distinct_material_cases"
                        ]
                    ),
                    "model_fail_minimum_distinct_panels": (
                        cohort_fail_decision["minimum_distinct_panels"]
                    ),
                    "model_fail_minimum_distinct_families": (
                        cohort_fail_decision["minimum_distinct_families"]
                    ),
                    "model_fail_distinct_material_case_count": (
                        cohort_fail_decision[
                            "distinct_material_case_count"
                        ]
                    ),
                    "model_fail_distinct_panel_count": (
                        cohort_fail_decision[
                            "distinct_material_panel_count"
                        ]
                    ),
                    "model_fail_distinct_family_count": (
                        cohort_fail_decision[
                            "distinct_material_family_count"
                        ]
                    ),
                    "material_residual_case_ids": (
                        material_residual_case_ids
                    ),
                    "calibration_residual_case_ids": (
                        calibration_residual_case_ids
                    ),
                    "operational_eligibility_verdict": (
                        cohort_fail_decision[
                            "operational_eligibility_verdict"
                        ]
                    ),
                    "category_id": self.EXAM_CATEGORY_ID,
                    "category_local_score": round(float(score), 2),
                    "numeric_score_is_not_model_fail_verdict": True,
                    "single_panel_cannot_establish_model_fail": True,
                    "cross_category_comparison_forbidden": True,
                    "global_ranking_forbidden": True,
                }
                quality_fail_admission = None
                raw_quality_fail_admission = None
                if strict_model_fail:
                    quality_fail_admission = admit_quality_fail(
                        execution_outcome=(
                            ExecutionOutcome.COMPLETED.value
                        ),
                        scorability=Scorability.SCOREABLE.value,
                        repair_rounds_used=max(
                            int(record.get("semantic_repair_rounds", 0))
                            for record in completed_panel_records
                        ),
                        repair_round_limit=(
                            self.SEMANTIC_REPAIR_ROUND_LIMIT
                        ),
                        blocking_failures=blocking_failures,
                        exact_hashes={
                            "reference_pack_sha256": str(
                                plan["reference_pack_sha256"]
                            ),
                            "scoring_protocol_sha256": _file_sha256(
                                Path(str(exam.__file__))
                            ),
                            "executor_sha256": _file_sha256(
                                Path(__file__)
                            ),
                            "input_exact_set_sha256": str(
                                final_cohort["cohort_binding_sha256"]
                            ),
                        },
                        repair_cycle_closed=True,
                    )
                    raw_quality_fail_admission = deepcopy(
                        quality_fail_admission
                    )
                panel_evidence_only = False
                stable_score_cache_eligible = True
                horizontal_comparison_eligible = True
                horizontal_comparison_pending = False
                cohort_panel_count = int(final_cohort["panel_count"])
                cohort_sample_slots = list(final_cohort["sample_slots"])
                cohort_binding_sha256 = str(
                    final_cohort["cohort_binding_sha256"]
                )
                cohort_difficulty_coverage = deepcopy(
                    final_cohort[
                        "difficulty_coverage_by_form_family"
                    ]
                )
                cohort_panel_scores = [
                    float(record["panel_score_diagnostic"])
                    for record in completed_panel_records
                ]
                cohort_panel_score_range = round(
                    max(cohort_panel_scores) - min(cohort_panel_scores),
                    6,
                )
                cohort_result = {
                    "schema_version": "DesktopAnalysisPrimaryCohortResult-v1",
                    "status": verdict,
                    "reason": result_reason,
                    "comparison_binding_sha256": plan[
                        "comparison_binding_sha256"
                    ],
                    "cohort_binding_sha256": cohort_binding_sha256,
                    "model_identity": self._panel_model_identity(target),
                    "panel_ids": list(final_cohort["panel_ids"]),
                    "sample_slots": cohort_sample_slots,
                    "panel_count": cohort_panel_count,
                    "panel_score_diagnostics": cohort_panel_scores,
                    "panel_score_range": cohort_panel_score_range,
                    "panel_stability_is_diagnostic_only": True,
                    "panel_repair_diagnostics": [
                        {
                            "panel_id": str(record["panel_id"]),
                            "sample_slot": int(record["sample_slot"]),
                            "first_pass_panel_score": float(
                                record[
                                    "first_pass_panel_score_diagnostic"
                                ]
                            ),
                            "final_panel_score": float(
                                record["panel_score_diagnostic"]
                            ),
                            "semantic_repair_rounds": int(
                                record.get("semantic_repair_rounds", 0)
                            ),
                            "semantic_repair_acceptance": deepcopy(
                                record.get(
                                    "semantic_repair_acceptance", []
                                )
                            ),
                        }
                        for record in completed_panel_records
                    ],
                    "aggregate_case_count_per_form": final_cohort[
                        "aggregate_case_count_per_form"
                    ],
                    "aggregate_before_tail_components": True,
                    "difficulty_coverage_by_form_family": (
                        cohort_difficulty_coverage
                    ),
                    "first_pass_form_scores": first_pass_form_scores,
                    "first_pass_role_ability_score": (
                        first_pass_role_ability_score
                    ),
                    "form_scores": form_scores,
                    "score": score,
                    "aggregate_initial_raw_scores": (
                        aggregate_initial_raw_scores
                    ),
                    "aggregate_raw_scores": aggregate_raw_scores,
                    "material_residuals": material_rows,
                    "cohort_fail_decision": cohort_fail_decision,
                    "calibration_residual_case_ids_by_form": (
                        calibration_sets
                    ),
                    "strict_model_fail": strict_model_fail,
                    "model_fail_requires_two_cases_two_panels_two_families": (
                        True
                    ),
                    "provider_received_answer_key": False,
                    "stable_score_cache_eligible": True,
                    "horizontal_comparison_eligible": True,
                }
                cohort_result["cohort_result_sha256"] = _sha256(
                    cohort_result
                )
                cohort_result_receipt = _write_json_create_only(
                    run_root / "panel_scores" / "cohort_result.json",
                    cohort_result,
                )
            public_result = {
                "schema_version": "WorkflowModelExamRunResult-v1",
                "status": verdict,
                "reason": result_reason,
                "run_id": run_id,
                "plan_sha256": plan["plan_sha256"],
                "requested_model": requested_model,
                "returned_model": state.get("returned_model") if target.get("adapter_id")=="command_template" else requested_model,
                "score": score,
                "first_pass_role_ability_score": first_pass_role_ability_score,
                "executor_ref": self.EXECUTOR_REF,
                "executor_sha256": _file_sha256(Path(__file__)),
                "first_pass_form_scores": first_pass_form_scores,
                "post_repair_score": score,
                "current_panel_score_diagnostic": panel_score_diagnostic,
                "current_panel_form_scores": panel_form_scores,
                "current_panel_first_pass_role_ability_score": (
                    panel_first_pass_role_ability_score
                ),
                "execution_outcome": ExecutionOutcome.COMPLETED.value,
                "scorability": Scorability.SCOREABLE.value,
                "raw_quality_candidate_verdict": (
                    panel_quality_candidate_verdict
                ),
                "raw_quality_fail_admission": raw_quality_fail_admission,
                "quality_fail_admission": quality_fail_admission,
                **model_fail_projection,
                "form_scores": form_scores,
                "failed_exam_item_count": failed_item_count,
                "semantic_repair_rounds": len(repair_lifecycle),
                "semantic_repair_round_limit": (
                    self.SEMANTIC_REPAIR_ROUND_LIMIT
                ),
                "semantic_repair_lifecycle": repair_lifecycle,
                "semantic_repair_acceptance": (
                    repair_acceptance_receipts
                ),
                "repair_materiality_revision": (
                    ANALYSIS_REPAIR_MATERIALITY_REVISION
                ),
                "repair_acceptance_revision": (
                    ANALYSIS_REPAIR_ACCEPTANCE_REVISION
                ),
                "cumulative_category_repair_penalty": cumulative_penalty,
                "repair_penalty_applied_to_role_ability": False,
                "repair_penalty_semantics": (
                    "SEPARATE_REPAIRABILITY_DIAGNOSTIC_ONLY"
                ),
                "residual_category_burden": residual_burden,
                "material_residual_count": failed_item_count,
                "calibration_residual_count": calibration_residual_count,
                "transport_retry_is_semantic_repair": False,
                "request_attempts": state["request_attempts"],
                "provider_calls": state["provider_calls"],
                "external_model_calls": state["external_model_calls"],
                "external_network_calls": state["external_network_calls"],
                "external_process_launches": state["external_process_launches"],
                "token_usage": state["token_usage"],
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
                "initial_exact_progress_reuse": bool(
                    state["analysis_preloaded_initial_rows"]
                ),
                "initial_reused_case_row_count": sum(
                    len(action["reused_case_ids"])
                    for action in state[
                        "analysis_preloaded_initial_rows"
                    ]
                ),
                "completed_initial_answers_retested": False,
                "length_only_repair_outputs_reused": False,
                "initial_exact_progress_reuse_receipt": (
                    "initial_exact_progress_reuse_receipt.json"
                    if initial_preload_receipt is not None
                    else None
                ),
                "initial_exact_progress_reuse_receipt_sha256": (
                    initial_preload_receipt["sha256"]
                    if initial_preload_receipt is not None
                    else None
                ),
                "sample_revision": plan["sample_revision"],
                "sample_slot": plan["sample_slot"],
                "sample_manifest_sha256": plan["sample_manifest_sha256"],
                "panel_cohort_slots": plan["panel_cohort_slots"],
                "panels_required_for_cache": plan[
                    "panels_required_for_cache"
                ],
                "panel_evidence_only": panel_evidence_only,
                "single_panel_cache_eligible": False,
                "stable_score_cache_eligible": (
                    stable_score_cache_eligible
                ),
                "cohort_panel_count": cohort_panel_count,
                "cohort_sample_slots": cohort_sample_slots,
                "cohort_binding_sha256": cohort_binding_sha256,
                "cohort_difficulty_coverage_by_form_family": (
                    cohort_difficulty_coverage
                ),
                "cohort_panel_score_range": (
                    cohort_panel_score_range
                ),
                "cohort_result": (
                    "panel_scores/cohort_result.json"
                    if cohort_result_receipt is not None
                    else None
                ),
                "cohort_result_file_sha256": (
                    cohort_result_receipt["sha256"]
                    if cohort_result_receipt is not None
                    else None
                ),
                "panel_record": "panel_scores/panel_record.json",
                "panel_record_sha256": panel_record_receipt["sha256"],
                "panel_binding_sha256": plan["panel_binding_sha256"],
                "subject_projection_revision": plan[
                    "subject_projection_revision"
                ],
                "anchor_normalization_revision": plan[
                    "anchor_normalization_revision"
                ],
                "anchor_normalization_count": len(
                    state["analysis_anchor_normalizations"]
                ),
                "anchor_normalization_receipt": (
                    "normalizations/analysis_case_local_anchor_aliases.json"
                ),
                "anchor_normalization_receipt_sha256": (
                    normalization_receipt["sha256"]
                ),
                "structural_repair_projection_revision": plan[
                    "structural_repair_projection_revision"
                ],
                "semantic_repair_sharding_revision": plan[
                    "semantic_repair_sharding_revision"
                ],
                "semantic_repair_structural_retry_count": state[
                    "analysis_semantic_repair_structural_retries"
                ],
                "structural_retry_is_semantic_repair": False,
                "workload_profile": plan["workload_profile"],
                "comparison_binding_sha256": plan[
                    "comparison_binding_sha256"
                ],
                "comparison_cohort_id": plan["comparison_cohort_id"],
                "horizontal_comparison_eligible": (
                    horizontal_comparison_eligible
                ),
                "horizontal_comparison_pending_panel_cohort": (
                    horizontal_comparison_pending
                ),
                "horizontal_comparison_allowed_only_with_same_cohort": True,
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
            reason = str(error) or type(error).__name__
            if len(reason) > 200:
                reason = type(error).__name__
            failure = {
                "schema_version": "WorkflowModelExamRunFailure-v1",
                "status": "NOT_ASSESSED",
                "reason": reason,
                "run_id": run_id,
                "plan_sha256": plan["plan_sha256"],
                "request_attempts": state["request_attempts"],
                "provider_calls": state["provider_calls"],
                "external_model_calls": state["external_model_calls"],
                "external_network_calls": state["external_network_calls"],
                "external_process_launches": state["external_process_launches"],
                "token_usage": state["token_usage"],
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
                request_attempts=state["request_attempts"],
                provider_calls=state["provider_calls"],
                external_model_calls=state["external_model_calls"],
                external_network_calls=state["external_network_calls"],
                external_process_launches=state["external_process_launches"],
                exam_run_id=run_id,
                exam_run_root=str(run_root.resolve()),
                exam_evidence_sha256=evidence["sha256"],
                token_usage=state["token_usage"],
                estimated_cost_cny=failure["estimated_cost_cny"],
                cost_semantics=plan["cost_semantics"],
                execution_outcome=failure["execution_outcome"],
                scorability=failure["scorability"],
            )
            return result
