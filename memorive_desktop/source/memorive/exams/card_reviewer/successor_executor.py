"""QualityCardReviewerExamSuccessorExecutor: runs the card reviewer exam's successor form.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping
from memorive.exams.outcomes import workflow_exam_structured_output_policy
from memorive.models.output_limits import (
    WORKFLOW_EXAM_REPLY_TOKEN_CEILING,
    workflow_exam_output_limit_contract,
)
from memorive.exams.common import (
    EXECUTOR_REF,
    _effective_structured_prompt_metrics,
    _load_json,
    _not_available,
    _serialized_request,
    _sha256,
)
from memorive.exams.card_reviewer.policy import (
    CARD_REVIEWER_REPAIR_PROMPT_PROJECTION_REVISION,
    CARD_REVIEWER_SCORING_PROJECTION_REVISION,
    CORE_CARD_REVIEWER_ABSOLUTE_PROMPT_CHAR_LIMIT,
    CORE_CARD_REVIEWER_EFFECTIVE_PROMPT_CHAR_LIMIT,
    core_card_reviewer_workload_profile,
)
from memorive.exams.card_reviewer.successor import (
    CARD_REVIEWER_SUCCESSOR_POINTER_NORMALIZATION_REVISION,
    CARD_REVIEWER_SUCCESSOR_SAMPLE_SLOTS,
    _project_card_reviewer_schema,
    build_card_reviewer_successor_sample,
    execute_card_reviewer_successor_form,
    score_card_reviewer_successor_form,
)
from memorive.exams.card_reviewer.role_projection import score_card_reviewer_role_projection
from memorive.exams.card_reviewer.executor import QualityCardReviewerExamExecutor


class QualityCardReviewerExamSuccessorExecutor(QualityCardReviewerExamExecutor):
    """Balanced, workload-bounded successor used by the Desktop executable."""

    EXECUTOR_REF = (
        "Desktop_MODEL_EXAM_SUCCESSOR_Quality_CARD_REVIEWER_EXECUTOR_V3_TWO_ROUND"
    )
    MODE = "LIVE_Quality_CARD_REVIEWER_SUCCESSOR_REFERENCE_REGRESSION"
    Quality_SEMANTIC_CALL_LIMIT = 4
    SEMANTIC_REPAIR_ROUND_LIMIT = 2
    API_MAXIMUM_REQUEST_ATTEMPTS = 24
    CLI_MAXIMUM_REQUEST_ATTEMPTS = 24
    LOCAL_MAXIMUM_REQUEST_ATTEMPTS = 24
    MAX_TRANSPORT_SHARDS = 12
    CASES_PER_CHUNK = None
    LOCAL_SUBJECT_PROJECTION_REVISION = (
        "Desktop_LOCAL_EVALUATOR_MARKER_SAFE_SUBJECT_PROJECTION_V1"
    )

    def _request_cap(self, profile_kind: str) -> int:
        # The successor selects one balanced panel, not the entire ancestor
        # archive. Reuse its frozen per-run budget declaration; this is an
        # authorization bound shown before execution, not a token/output limit.
        explicit = {"API": self._api_request_cap, "CLI": self._cli_request_cap,
                    "LOCAL": self._local_request_cap}[profile_kind]
        return explicit if explicit is not None else {
            "API": self.API_MAXIMUM_REQUEST_ATTEMPTS,
            "CLI": self.CLI_MAXIMUM_REQUEST_ATTEMPTS,
            "LOCAL": self.LOCAL_MAXIMUM_REQUEST_ATTEMPTS,
        }[profile_kind]

    @staticmethod
    def _sample_slot(context: Mapping[str, Any]) -> int:
        explicit = context.get("workflow_exam_sample_slot")
        if explicit is not None:
            if isinstance(explicit, bool) or not isinstance(explicit, int):
                raise ValueError("CARD_REVIEWER_SUCCESSOR_SAMPLE_SLOT_INVALID")
            if explicit not in CARD_REVIEWER_SUCCESSOR_SAMPLE_SLOTS:
                raise ValueError("CARD_REVIEWER_SUCCESSOR_SAMPLE_SLOT_INVALID")
            return explicit
        ordinal = context.get("workflow_exam_attempt_ordinal", 1)
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1:
            raise ValueError("CARD_REVIEWER_SUCCESSOR_ATTEMPT_ORDINAL_INVALID")
        return ((ordinal - 1) % len(CARD_REVIEWER_SUCCESSOR_SAMPLE_SLOTS)) + 1

    def _exam_inputs(
        self,
        *,
        pack: Any,
        schema: Mapping[str, Any],
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
        sample = build_card_reviewer_successor_sample(
            forms=source_forms,
            golds=source_golds,
            schema=schema,
            sample_slot=self._sample_slot(context),
        )
        return {
            "forms": sample["forms"],
            "golds": sample["golds"],
            "schemas": sample["schemas"],
            "execution_profile": "BALANCED_MEDIUM_HARD_SAMPLE",
            "sample_metadata": sample["manifest"],
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
        return execute_card_reviewer_successor_form(
            protocol=protocol,
            exam=exam,
            form=form,
            schema=schema,
            prompt_contract=prompt_contract,
            scoring_protocol=scoring_protocol,
            subject_call=subject_call,
            gold=gold,
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
        def successor_scorer(**values: Any) -> Mapping[str, Any]:
            return score_card_reviewer_successor_form(exam=exam, **values)

        return score_card_reviewer_role_projection(
            exam=exam,
            forms=forms,
            golds=golds,
            original_scores=original_scores,
            merged_responses=merged_responses,
            schema=schema,
            scoring_protocol=scoring_protocol,
            repair_contract=repair_contract,
            score_form_fn=successor_scorer,
            call_legacy_whole_exam_scorer=False,
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
                score_card_reviewer_successor_form(
                    exam=exam,
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

        def safe_text(value: str) -> str:
            replacements = (
                (re.compile(r"(?i)answer[_ -]?key"), "reference_values"),
                (re.compile(r"(?i)expected[ -]+answer"), "reference value"),
                (re.compile(r"(?i)\bgold\b"), "reference labels"),
                (re.compile(r"(?i)\bscorer\b"), "local validator"),
            )
            projected = value
            for pattern, replacement in replacements:
                projected = pattern.sub(replacement, projected)
            return projected

        def project(value: Any) -> Any:
            if isinstance(value, Mapping):
                return {
                    safe_text(str(key)): project(item)
                    for key, item in value.items()
                }
            if isinstance(value, list):
                return [project(item) for item in value]
            if isinstance(value, str):
                return safe_text(value)
            return deepcopy(value)

        projected = project(request)
        if not isinstance(projected, dict):
            raise ValueError("LOCAL_SUBJECT_REQUEST_PROJECTION_INVALID")
        projected.pop("request_payload_hash", None)
        projected["subject_transport_projection"] = {
            "schema_version": cls.LOCAL_SUBJECT_PROJECTION_REVISION,
            "semantic_contract_changed": False,
            "evaluator_data_included": False,
            "integration_marker_gate_preserved": True,
        }
        projected["request_payload_hash"] = _sha256(projected)
        serialized = _serialized_request(projected)
        if re.search(
            r"(?i)(?:\bgold\b|\bscorer\b|answer[_ -]?key|expected[ -]+answer)",
            serialized,
        ):
            raise ValueError("LOCAL_SUBJECT_REQUEST_PROJECTION_INCOMPLETE")
        projected_schema = projected.get("output_schema")
        if not isinstance(projected_schema, Mapping):
            raise ValueError("LOCAL_SUBJECT_RESPONSE_SCHEMA_MISSING")
        if (
            _effective_structured_prompt_metrics(
                serialized,
                projected_schema,
            )["effective_prompt_chars"]
            > CORE_CARD_REVIEWER_ABSOLUTE_PROMPT_CHAR_LIMIT
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_DESIGN_INPUT_OUT_OF_BOUNDS")
        return projected

    @staticmethod
    def _project_exam_repair_schema(
        schema: Mapping[str, Any],
        exact_sets: Mapping[str, list[str]],
    ) -> dict[str, Any]:
        properties = schema.get("properties")
        if not isinstance(properties, Mapping):
            raise ValueError("CARD_REVIEWER_REPAIR_SCHEMA_INVALID")
        selected_properties: dict[str, Any] = {}
        required: list[str] = []
        for form_id in ("A", "B"):
            case_ids = exact_sets.get(form_id, [])
            if not case_ids:
                continue
            form_key = f"form_{form_id}"
            source = properties.get(form_key)
            if not isinstance(source, Mapping):
                raise ValueError("CARD_REVIEWER_REPAIR_SCHEMA_INVALID")
            projected = deepcopy(dict(source))
            try:
                reviews = projected["properties"]["reviews"]
                case_schema = reviews["items"]["properties"]["case_id"]
            except (KeyError, TypeError) as error:
                raise ValueError("CARD_REVIEWER_REPAIR_SCHEMA_INVALID") from error
            reviews["minItems"] = len(case_ids)
            reviews["maxItems"] = len(case_ids)
            case_schema.clear()
            case_schema.update({"type": "string", "enum": list(case_ids)})
            selected_properties[form_key] = projected
            required.append(form_key)
        if not required:
            raise ValueError("CARD_REVIEWER_REPAIR_SCHEMA_EMPTY")
        value = deepcopy(dict(schema))
        value["properties"] = selected_properties
        value["required"] = required
        return value

    def _case_shards(
        self,
        *,
        request: Mapping[str, Any],
        response_schema: Mapping[str, Any],
    ) -> list[tuple[dict[str, Any], dict[str, Any], list[str], bool]] | None:
        form = request.get("form")
        cases = form.get("cases") if isinstance(form, Mapping) else None
        if not isinstance(cases, list) or not cases:
            return None
        parent_hash = str(request.get("request_payload_hash") or _sha256(request))

        def build(
            selected: list[Any],
            *,
            index: int,
            count: int,
        ) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
            case_ids = [
                str(item.get("case_id"))
                for item in selected
                if isinstance(item, Mapping) and isinstance(item.get("case_id"), str)
            ]
            if len(case_ids) != len(selected) or len(case_ids) != len(set(case_ids)):
                raise ValueError("WORKFLOW_MODEL_EXAM_CHUNK_CASE_SET_INVALID")
            projected_schema = _project_card_reviewer_schema(
                response_schema,
                case_ids,
            )
            chunk_request = deepcopy(dict(request))
            chunk_form = deepcopy(dict(form))
            chunk_form["cases"] = deepcopy(selected)
            chunk_request["form"] = chunk_form
            chunk_request["output_schema"] = deepcopy(projected_schema)
            chunk_request["chunk_contract"] = {
                "schema_version": "DesktopCardReviewerCapabilitiesBoundedShard-v1",
                "parent_request_payload_hash": parent_hash,
                "chunk_index": index,
                "chunk_count": count,
                "case_ids": case_ids,
                "case_ids_sha256": _sha256(case_ids),
                "overlap_unit_count": 0,
                "merge_policy": "EXACT_SET_ORDERED_CONCATENATION",
            }
            chunk_request.pop("request_payload_hash", None)
            chunk_request["request_payload_hash"] = _sha256(chunk_request)
            return chunk_request, projected_schema, case_ids

        groups: list[list[Any]] = []
        current: list[Any] = []
        for case in cases:
            candidate = [*current, case]
            candidate_request, candidate_schema, _ = build(
                candidate,
                index=99,
                count=99,
            )
            metrics = _effective_structured_prompt_metrics(
                _serialized_request(candidate_request),
                candidate_schema,
            )
            if (
                current
                and metrics["effective_prompt_chars"]
                > CORE_CARD_REVIEWER_EFFECTIVE_PROMPT_CHAR_LIMIT
            ):
                groups.append(current)
                current = [case]
            else:
                current = candidate
        if current:
            groups.append(current)
        if len(groups) > self.MAX_TRANSPORT_SHARDS:
            raise ValueError("WORKFLOW_MODEL_EXAM_MAX_LEAF_SEGMENTS_EXCEEDED")
        result: list[tuple[dict[str, Any], dict[str, Any], list[str], bool]] = []
        for index, group in enumerate(groups, start=1):
            chunk_request, projected_schema, case_ids = build(
                group,
                index=index,
                count=len(groups),
            )
            metrics = _effective_structured_prompt_metrics(
                _serialized_request(chunk_request),
                projected_schema,
            )
            observed = metrics["effective_prompt_chars"]
            atomic_exception = (
                observed > CORE_CARD_REVIEWER_EFFECTIVE_PROMPT_CHAR_LIMIT
                and len(group) == 1
            )
            if observed > CORE_CARD_REVIEWER_ABSOLUTE_PROMPT_CHAR_LIMIT:
                raise ValueError(
                    "WORKFLOW_MODEL_EXAM_DESIGN_INPUT_OUT_OF_BOUNDS"
                )
            if (
                observed > CORE_CARD_REVIEWER_EFFECTIVE_PROMPT_CHAR_LIMIT
                and not atomic_exception
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_SOFT_LIMIT_SHARDING_FAILED")
            result.append(
                (chunk_request, projected_schema, case_ids, atomic_exception)
            )
        return result

    def _repair_shards(
        self,
        *,
        request: Mapping[str, Any],
        response_schema: Mapping[str, Any],
    ) -> list[
        tuple[dict[str, Any], dict[str, Any], dict[str, list[str]], bool]
    ] | None:
        if request.get("phase") != "EXAM_DIRECTED_REPAIR":
            return None
        semantic_repair_round = request.get("semantic_repair_round", 1)
        if semantic_repair_round not in {1, 2}:
            raise ValueError("CARD_REVIEWER_SEMANTIC_REPAIR_ROUND_INVALID")
        user = request.get("user")
        form = request.get("form")
        if not isinstance(user, str) or not isinstance(form, Mapping):
            raise ValueError("CARD_REVIEWER_REPAIR_REQUEST_INVALID")
        try:
            parent_prompt = json.loads(user)
        except json.JSONDecodeError as error:
            raise ValueError("CARD_REVIEWER_REPAIR_PROMPT_INVALID") from error
        targets_by_form = parent_prompt.get("repair_targets")
        exact_sets = form.get("wrong_item_exact_set")
        if not isinstance(targets_by_form, Mapping) or not isinstance(
            exact_sets, Mapping
        ):
            raise ValueError("CARD_REVIEWER_REPAIR_TARGETS_INVALID")
        units: list[tuple[str, dict[str, Any]]] = []
        for form_id in ("A", "B"):
            targets = targets_by_form.get(form_id, [])
            expected = list(exact_sets.get(form_id, []))
            if not isinstance(targets, list) or [
                item.get("case_id") if isinstance(item, Mapping) else None
                for item in targets
            ] != expected:
                raise ValueError("CARD_REVIEWER_REPAIR_TARGET_ORDER_INVALID")
            units.extend((form_id, deepcopy(dict(item))) for item in targets)
        if not units:
            raise ValueError("CARD_REVIEWER_REPAIR_TARGETS_EMPTY")
        parent_hash = str(request.get("request_payload_hash") or _sha256(request))

        def build(
            selected: list[tuple[str, dict[str, Any]]],
            *,
            index: int,
            count: int,
        ) -> tuple[dict[str, Any], dict[str, Any], dict[str, list[str]]]:
            selected_targets = {"A": [], "B": []}
            selected_ids: dict[str, list[str]] = {"A": [], "B": []}
            for form_id, target in selected:
                case_id = target.get("case_id")
                if not isinstance(case_id, str) or not case_id:
                    raise ValueError("CARD_REVIEWER_REPAIR_TARGET_ID_INVALID")
                selected_targets[form_id].append(deepcopy(target))
                selected_ids[form_id].append(case_id)
            selected_targets = {
                key: value for key, value in selected_targets.items() if value
            }
            nonempty_ids = {
                key: value for key, value in selected_ids.items() if value
            }
            projected_schema = self._project_exam_repair_schema(
                response_schema,
                nonempty_ids,
            )
            shard_prompt = deepcopy(dict(parent_prompt))
            shard_prompt["wrong_item_exact_set"] = deepcopy(nonempty_ids)
            shard_prompt["repair_targets"] = selected_targets
            shard_prompt["output_schema"] = deepcopy(projected_schema)
            shard_prompt["transport_shard"] = {
                "schema_version": "DesktopCardReviewerCapabilitiesBoundedRepairShard-v1",
                "parent_request_payload_hash": parent_hash,
                "chunk_index": index,
                "chunk_count": count,
                "case_ids_by_form": deepcopy(nonempty_ids),
                "case_ids_sha256": _sha256(nonempty_ids),
                "semantic_repair_round": semantic_repair_round,
                "transport_shards_do_not_increment_semantic_round": True,
                "merge_policy": "EXACT_SET_BY_FORM_ORDERED_CONCATENATION",
            }
            shard_request = deepcopy(dict(request))
            shard_request["user"] = json.dumps(
                shard_prompt,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            shard_request["form"] = {
                "forms": list(nonempty_ids),
                "wrong_item_exact_set": deepcopy(nonempty_ids),
            }
            shard_request["output_schema"] = deepcopy(projected_schema)
            shard_request["transport_shard"] = deepcopy(
                shard_prompt["transport_shard"]
            )
            shard_request.pop("request_payload_hash", None)
            shard_request["request_payload_hash"] = _sha256(shard_request)
            return shard_request, projected_schema, nonempty_ids

        groups: list[list[tuple[str, dict[str, Any]]]] = []
        current: list[tuple[str, dict[str, Any]]] = []
        for unit in units:
            candidate = [*current, unit]
            candidate_request, candidate_schema, _ = build(
                candidate,
                index=99,
                count=99,
            )
            metrics = _effective_structured_prompt_metrics(
                _serialized_request(candidate_request),
                candidate_schema,
            )
            if (
                current
                and metrics["effective_prompt_chars"]
                > CORE_CARD_REVIEWER_EFFECTIVE_PROMPT_CHAR_LIMIT
            ):
                groups.append(current)
                current = [unit]
            else:
                current = candidate
        if current:
            groups.append(current)
        if len(groups) > self.MAX_TRANSPORT_SHARDS:
            raise ValueError("WORKFLOW_MODEL_EXAM_MAX_LEAF_SEGMENTS_EXCEEDED")
        result = []
        for index, group in enumerate(groups, start=1):
            shard_request, projected_schema, ids_by_form = build(
                group,
                index=index,
                count=len(groups),
            )
            metrics = _effective_structured_prompt_metrics(
                _serialized_request(shard_request),
                projected_schema,
            )
            observed = metrics["effective_prompt_chars"]
            atomic_exception = (
                observed > CORE_CARD_REVIEWER_EFFECTIVE_PROMPT_CHAR_LIMIT
                and len(group) == 1
            )
            if observed > CORE_CARD_REVIEWER_ABSOLUTE_PROMPT_CHAR_LIMIT:
                raise ValueError(
                    "WORKFLOW_MODEL_EXAM_DESIGN_INPUT_OUT_OF_BOUNDS"
                )
            if (
                observed > CORE_CARD_REVIEWER_EFFECTIVE_PROMPT_CHAR_LIMIT
                and not atomic_exception
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_SOFT_LIMIT_SHARDING_FAILED")
            result.append(
                (shard_request, projected_schema, ids_by_form, atomic_exception)
            )
        return result

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
        repair_shards = self._repair_shards(
            request=request,
            response_schema=response_schema,
        )
        semantic_repair_round = request.get("semantic_repair_round", 1)
        case_shards = (
            None
            if repair_shards is not None
            else self._case_shards(request=request, response_schema=response_schema)
        )
        if repair_shards is None and case_shards is None:
            metrics = _effective_structured_prompt_metrics(
                _serialized_request(request),
                response_schema,
            )
            if (
                metrics["effective_prompt_chars"]
                > CORE_CARD_REVIEWER_ABSOLUTE_PROMPT_CHAR_LIMIT
            ):
                raise ValueError(
                    "WORKFLOW_MODEL_EXAM_DESIGN_INPUT_OUT_OF_BOUNDS"
                )
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

        topology_records: list[dict[str, Any]] = []
        if repair_shards is not None:
            merged: dict[str, dict[str, list[Any]]] = {}
            for index, (shard_request, shard_schema, ids_by_form, atomic) in enumerate(
                repair_shards,
                start=1,
            ):
                response = self._stable_provider_call(
                    run_id=run_id,
                    run_root=run_root,
                    state=state,
                    target=target,
                    request=shard_request,
                    response_schema=shard_schema,
                    structured_chat=structured_chat,
                    call_kind=(
                        f"{call_kind}_SHARD_{index}_OF_{len(repair_shards)}"
                    ),
                )
                for form_id, expected_ids in ids_by_form.items():
                    form_key = f"form_{form_id}"
                    value = response.get(form_key)
                    rows = value.get("reviews") if isinstance(value, Mapping) else None
                    observed_ids = [
                        row.get("case_id") if isinstance(row, Mapping) else None
                        for row in rows or []
                    ]
                    if observed_ids != expected_ids:
                        raise ValueError(
                            "CARD_REVIEWER_REPAIR_SHARD_EXACT_SET_MISMATCH"
                        )
                    merged.setdefault(form_key, {"reviews": []})["reviews"].extend(
                        deepcopy(rows)
                    )
                topology_records.append(
                    {
                        "shard_index": index,
                        "case_ids_by_form": deepcopy(ids_by_form),
                        "atomic_soft_limit_exception": atomic,
                        "request_payload_sha256": shard_request[
                            "request_payload_hash"
                        ],
                    }
                )
            state.setdefault("transport_topologies", []).append(
                {
                    "call_kind": call_kind,
                    "topology_revision": "LONG_DOCUMENT_EXACT_SET_NO_OVERLAP_ADAPTER_V1",
                    "semantic_repair_round": semantic_repair_round,
                    "shards": topology_records,
                    "merge_hash": _sha256(merged),
                }
            )
            return merged

        assert case_shards is not None
        merged_reviews: list[Any] = []
        for index, (shard_request, shard_schema, case_ids, atomic) in enumerate(
            case_shards,
            start=1,
        ):
            response = self._stable_provider_call(
                run_id=run_id,
                run_root=run_root,
                state=state,
                target=target,
                request=shard_request,
                response_schema=shard_schema,
                structured_chat=structured_chat,
                call_kind=f"{call_kind}_SHARD_{index}_OF_{len(case_shards)}",
            )
            rows = response.get("reviews")
            observed_ids = [
                row.get("case_id") if isinstance(row, Mapping) else None
                for row in rows or []
            ]
            if not isinstance(rows, list) or observed_ids != case_ids:
                raise ValueError("CARD_REVIEWER_SHARD_EXACT_SET_MISMATCH")
            merged_reviews.extend(deepcopy(rows))
            topology_records.append(
                {
                    "shard_index": index,
                    "case_ids": case_ids,
                    "atomic_soft_limit_exception": atomic,
                    "request_payload_sha256": shard_request[
                        "request_payload_hash"
                    ],
                }
            )
        state.setdefault("transport_topologies", []).append(
            {
                "call_kind": call_kind,
                "topology_revision": "LONG_DOCUMENT_EXACT_SET_NO_OVERLAP_ADAPTER_V1",
                "shards": topology_records,
                "merge_hash": _sha256(merged_reviews),
            }
        )
        return {"reviews": merged_reviews}

    def plan(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        plan = super().plan(node, target, context)
        if plan.get("status") != "READY":
            return plan
        try:
            modules = self._quality()
            pack = modules["reference"].load_card_reviewer_reference_exam_pack(
                self._reference_pack_root
            )
            schema = _load_json(
                pack.member("schemas/card_reviewer_output.schema.json")
            )
            prompt_contract = _load_json(
                pack.member("prompts/card_reviewer_prompt.json")
            )
            (
                model_binding,
                request_max_output_tokens,
                output_limit_mode,
            ) = self._structured_output_policy(target)
            inputs = self._exam_inputs(pack=pack, schema=schema, context=context)
            initial_metrics: dict[str, dict[str, int]] = {}
            for form_id in ("A", "B"):
                request = modules["protocol"].build_initial_request(
                    form=inputs["forms"][form_id],
                    schema=inputs["schemas"][form_id],
                    prompt_contract=prompt_contract,
                )
                planned_shards = self._case_shards(
                    request=request,
                    response_schema=inputs["schemas"][form_id],
                )
                if not planned_shards:
                    raise ValueError("WORKFLOW_MODEL_EXAM_INITIAL_TOPOLOGY_EMPTY")
                shard_metrics = [
                    _effective_structured_prompt_metrics(
                        _serialized_request(shard_request),
                        shard_schema,
                    )
                    for shard_request, shard_schema, _case_ids, _atomic
                    in planned_shards
                ]
                initial_metrics[form_id] = {
                    "shard_count": len(shard_metrics),
                    "prompt_chars": sum(
                        item["prompt_chars"] for item in shard_metrics
                    ),
                    "response_schema_chars": sum(
                        item["response_schema_chars"] for item in shard_metrics
                    ),
                    "effective_prompt_chars": sum(
                        item["effective_prompt_chars"] for item in shard_metrics
                    ),
                    "effective_prompt_utf8_bytes": sum(
                        item["effective_prompt_utf8_bytes"] for item in shard_metrics
                    ),
                    "maximum_shard_effective_prompt_chars": max(
                        item["effective_prompt_chars"] for item in shard_metrics
                    ),
                }
            total_initial_chars = sum(
                item["effective_prompt_chars"] for item in initial_metrics.values()
            )
        except ValueError as error:
            bounded_design_reasons = {
                "WORKFLOW_MODEL_EXAM_DESIGN_INPUT_OUT_OF_BOUNDS",
                "WORKFLOW_MODEL_EXAM_MAX_LEAF_SEGMENTS_EXCEEDED",
                "WORKFLOW_MODEL_EXAM_SOFT_LIMIT_SHARDING_FAILED",
                "LOCAL_EXAM_OUTPUT_LIMIT_PROFILE_INVALID",
                "LOCAL_EXAM_CONTEXT_LIMIT_PROFILE_INVALID",
            }
            reason = str(error)
            if reason in bounded_design_reasons:
                return _not_available(reason)
            return _not_available("WORKFLOW_MODEL_EXAM_SUCCESSOR_ASSETS_INVALID")
        except (ImportError, OSError, RuntimeError):
            return _not_available("WORKFLOW_MODEL_EXAM_SUCCESSOR_ASSETS_INVALID")
        sample_manifest = inputs["sample_metadata"]
        comparison_basis = {
            "legacy_comparison_binding_sha256": plan[
                "comparison_binding_sha256"
            ],
            "executor_ref": self.EXECUTOR_REF,
            "sample_revision": sample_manifest["schema_version"],
            "sample_slot": sample_manifest["sample_slot"],
            "sample_manifest_sha256": sample_manifest["manifest_sha256"],
            "scoring_projection_revision": (
                CARD_REVIEWER_SCORING_PROJECTION_REVISION
            ),
            "pointer_normalization_revision": (
                CARD_REVIEWER_SUCCESSOR_POINTER_NORMALIZATION_REVISION
            ),
            "repair_prompt_projection_revision": (
                CARD_REVIEWER_REPAIR_PROMPT_PROJECTION_REVISION
            ),
            "subject_transport_projection_revision": (
                self.LOCAL_SUBJECT_PROJECTION_REVISION
                if plan["profile_kind"] == "LOCAL"
                else "NONE"
            ),
        }
        successor_cohort = _sha256(comparison_basis)
        comparison = deepcopy(dict(plan["comparison"]))
        comparison.update(
            {
                "sample_revision": sample_manifest["schema_version"],
                "sample_slot": sample_manifest["sample_slot"],
                "sample_manifest_sha256": sample_manifest["manifest_sha256"],
                "comparison_cohort_id": (
                    f"CARD_REVIEWER_SUCCESSOR:{successor_cohort}"
                ),
                "comparison_binding_sha256": successor_cohort,
                "three_slot_series_aggregate_required_for_stable_cache": True,
                "subject_transport_projection_revision": comparison_basis[
                    "subject_transport_projection_revision"
                ],
            }
        )
        plan.update(
            {
                "mode": self.MODE,
                "reason": "Quality_CARD_REVIEWER_SUCCESSOR_READY",
                "executor_ref": self.EXECUTOR_REF,
                "comparison": comparison,
                **comparison,
                "sample_revision": sample_manifest["schema_version"],
                "sample_slot": sample_manifest["sample_slot"],
                "sample_manifest_sha256": sample_manifest["manifest_sha256"],
                "sample_case_count": sample_manifest["selected_case_count"],
                "sample_difficulty_profile": "MEDIUM_PLUS_HARD_NO_ADVERSARIAL",
                "workload_profile": core_card_reviewer_workload_profile(),
                "initial_effective_prompt_metrics_by_half": initial_metrics,
                "initial_total_effective_prompt_chars": total_initial_chars,
                "initial_total_within_soft_target": (
                    total_initial_chars
                    <= CORE_CARD_REVIEWER_EFFECTIVE_PROMPT_CHAR_LIMIT
                ),
                "initial_total_effective_prompt_semantics": (
                    "SUM_OF_INDEPENDENT_TRANSPORT_CALLS_DIAGNOSTIC_ONLY_"
                    "NEVER_COMPARED_TO_ONE_CALL_LIMIT"
                ),
                "total_question_material_semantics": (
                    "UNIQUE_BALANCED_SAMPLE_FULL_REVIEW_PER_SEMANTIC_PASS"
                ),
                "sharding_revision": (
                    "LONG_DOCUMENT_EXACT_SET_NO_OVERLAP_ADAPTER_V1"
                ),
                "case_chunk_limit": None,
                "char_bounded_sharding": True,
                "output_limit_policy": (
                    "CALIBRATED_CEILING_WITH_PROVIDER_DEFAULT_API_CLI_"
                    "AND_MODEL_PROFILE_LOCAL"
                ),
                "calibrated_reply_token_ceiling": (
                    WORKFLOW_EXAM_REPLY_TOKEN_CEILING
                ),
                "output_limit_contract": workflow_exam_output_limit_contract(),
                "subject_transport_projection_revision": comparison_basis[
                    "subject_transport_projection_revision"
                ],
                "structured_output_profile": {
                    "mode": output_limit_mode,
                    "request_max_output_tokens": request_max_output_tokens,
                    "context_window_tokens": (
                        model_binding.get("context_window_tokens")
                        if plan["profile_kind"] == "LOCAL"
                        else None
                    ),
                    "profile_specific_not_global": True,
                },
                "semantic_repair_round_limit": 2,
                "second_semantic_repair_allowed": True,
                "second_round_scope": (
                    "ROUND1_RESIDUAL_EXACT_SET_ONLY_NO_FULL_FORM_RETRY"
                ),
                "second_round_penalty_semantics": (
                    "CUMULATIVE_Quality_CATEGORY_PENALTY_PLUS_EXISTING_"
                    "CATEGORY_FIXED_AND_VOLUME_BURDEN"
                ),
                "pointer_normalization_revision": (
                    CARD_REVIEWER_SUCCESSOR_POINTER_NORMALIZATION_REVISION
                ),
                "repair_prompt_projection_revision": (
                    CARD_REVIEWER_REPAIR_PROMPT_PROJECTION_REVISION
                ),
                "pointer_normalization_semantics": (
                    "GOLD_FREE_DETERMINISTIC_MECHANICAL_RECOVERY_"
                    "DOES_NOT_INCREMENT_SEMANTIC_REPAIR_ROUNDS"
                ),
                "core_terminal_disposition_enabled": True,
            }
        )
        plan["plan_sha256"] = _sha256(
            {key: value for key, value in plan.items() if key != "plan_sha256"}
        )
        return plan
