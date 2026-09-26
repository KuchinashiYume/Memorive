from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from time import monotonic
from typing import Any, Callable, Mapping, Sequence
import uuid

from pr_os_analysis_reviewer_exam import (
    BUNDLED_CORE_V2_IMPORT_ADAPTER_REVISION,
    SOURCE_CORE_V1_SHA256,
    SOURCE_CORE_V2_SHA256,
    core_v2,
)

from .exam_successor import (
    Scorability,
    WORKFLOW_EXAM_REPLY_TOKEN_CEILING,
    admit_quality_fail,
    build_repair_round,
    canonical_sha256,
    classify_execution_outcome,
    cumulative_category_repair_penalty,
    terminal_disposition,
    workflow_exam_output_limit_contract,
    workflow_exam_structured_output_policy,
)
from .workflow_exam import (
    AUTHORIZATION_SCHEMA,
    PHASE1_CARD_REVIEWER_ABSOLUTE_PROMPT_CHAR_LIMIT,
    PHASE1_CARD_REVIEWER_EFFECTIVE_PROMPT_CHAR_LIMIT,
    PLAN_SCHEMA,
    RESULT_SCHEMA,
    Phase2CardReviewerExamExecutor,
    _effective_structured_prompt_metrics,
    _file_sha256,
    _load_json,
    _not_available,
    _not_run,
    _sha256,
    _write_json_create_only,
    phase1_card_reviewer_workload_profile,
)


ANALYSIS_REVIEWER_CATEGORY_ID = "P02_ANALYSIS_REVIEWER_REFERENCE_REGRESSION"
ANALYSIS_REVIEWER_ROLE_ID = "ANALYSIS_REVIEWER"
ANALYSIS_REVIEWER_SAMPLE_REVISION = (
    "P08_ANALYSIS_REVIEWER_BALANCED_MEDIUM_HARD_SAMPLE_V1"
)
ANALYSIS_REVIEWER_SCORING_PROJECTION_REVISION = (
    "P08_ANALYSIS_REVIEWER_A4_OPERATIONAL_CONTAINMENT_V4"
)
ANALYSIS_REVIEWER_OPERATIONAL_POLICY_REVISION = (
    "P08_ANALYSIS_REVIEWER_OPERATIONAL_ELIGIBILITY_V2_CONTAINMENT"
)
EXPECTED_MANIFEST_SHA256 = (
    "8F1054A9F0BDC0638BE450BEAA588DABF45A99E5E030A7A01FB6258F93E07DAD"
)
EXPECTED_CHECKSUMS_SHA256 = (
    "360A31E8783A08527355C5535F7F1D6154E3C79A793D8D54D4DD3220303FF7A9"
)
EXPECTED_PACK_ID = "P02-T08-M14-ANALYSIS-REVIEWER-REFERENCE-EXAM-T-V4"
EXPECTED_PACK_REVISION = "r0.4-a4"
FAMILY_ORDER = (
    "unsupported_critical_claim",
    "contradiction_reversal",
    "causal_overclaim",
    "scope_extrapolation",
    "lineage_citation_mismatch",
    "none",
)
_FORBIDDEN_PROVIDER_KEYS = frozenset(
    {
        "gold",
        "expected_verdict",
        "expected_issue_count",
        "expected_issue_family",
        "required_evidence_refs",
        "allowed_evidence_refs",
        "accepted_actions",
        "accepted_claim_pointers",
        "critical",
    }
)
_DESTRUCTIVE_CLEAN_ACTIONS = frozenset({"retract", "downgrade"})


def _parse_sha256sums(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        match = re.fullmatch(r"([A-F0-9]{64})  (.+)", line)
        if match is None or match.group(2) in result:
            raise ValueError(f"ANALYSIS_REVIEWER_CHECKSUM_LINE_INVALID:{number}")
        result[match.group(2)] = match.group(1)
    return result


def verify_analysis_reviewer_local_pack(root: Path) -> dict[str, Any]:
    pack_root = Path(root).resolve()
    manifest_path = pack_root / "manifest.candidate.json"
    checksums_path = pack_root / "SHA256SUMS.txt"
    if not manifest_path.is_file() or not checksums_path.is_file():
        raise ValueError("ANALYSIS_REVIEWER_LOCAL_PACK_REQUIRED")
    if _file_sha256(manifest_path) != EXPECTED_MANIFEST_SHA256:
        raise ValueError("ANALYSIS_REVIEWER_MANIFEST_SHA256_MISMATCH")
    if _file_sha256(checksums_path) != EXPECTED_CHECKSUMS_SHA256:
        raise ValueError("ANALYSIS_REVIEWER_CHECKSUMS_SHA256_MISMATCH")
    checksums = _parse_sha256sums(checksums_path)
    for relative, expected in checksums.items():
        member = pack_root / Path(relative)
        if not member.is_file() or _file_sha256(member) != expected:
            raise ValueError(
                f"ANALYSIS_REVIEWER_PACK_MEMBER_SHA256_MISMATCH:{relative}"
            )
    manifest = _load_json(manifest_path)
    if (
        manifest.get("pack_id") != EXPECTED_PACK_ID
        or manifest.get("pack_revision") != EXPECTED_PACK_REVISION
        or manifest.get("classification") != "REFERENCE_EXAM_PACK_CANDIDATE"
        or manifest.get("declassification", {}).get("authorized") is not False
    ):
        raise ValueError("ANALYSIS_REVIEWER_PACK_AUTHORITY_MISMATCH")
    members = manifest.get("members")
    if not isinstance(members, list) or any(
        isinstance(row, Mapping)
        and row.get("provider_visible") is True
        and (
            str(row.get("path", "")).startswith("gold/")
            or str(row.get("role", "")).startswith("GOLD")
        )
        for row in members
    ):
        raise ValueError("ANALYSIS_REVIEWER_GOLD_PROVIDER_VISIBILITY_INVALID")
    return {
        "pack_root": str(pack_root),
        "manifest": manifest,
        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "checksums_sha256": EXPECTED_CHECKSUMS_SHA256,
        "member_count": len(checksums),
        "declassification_authorized": False,
        "distributable_exe_embedding_allowed": False,
        "runtime_mode": "FORMAL_LOCAL_REFERENCE_PACK_RESOLVER",
        "gold_provider_visible": False,
    }


def _project_output_schema(
    schema: Mapping[str, Any], case_ids: Sequence[str]
) -> dict[str, Any]:
    value = deepcopy(dict(schema))
    reviews = value["properties"]["case_reviews"]
    reviews["minItems"] = len(case_ids)
    reviews["maxItems"] = len(case_ids)
    reviews["items"]["properties"]["case_id"] = {
        "type": "string",
        "enum": list(case_ids),
    }
    return value


def _sample_choice(
    rows: Sequence[Mapping[str, Any]], *, family: str, slot: int
) -> Mapping[str, Any]:
    family_rows = [
        row for row in rows if row.get("expected_issue_family") == family
    ]
    if family == "none":
        selected = [
            row
            for row in family_rows
            if row.get("difficulty") == ("medium" if slot == 1 else "hard")
        ]
    else:
        selected = [
            row
            for row in family_rows
            if row.get("critical") is True
            and row.get("difficulty") == ("medium" if slot == 1 else "hard")
        ]
    index = 0 if slot in {1, 2} else 1
    if len(selected) <= index:
        raise ValueError("ANALYSIS_REVIEWER_BALANCED_SAMPLE_SOURCE_INCOMPLETE")
    return selected[index]


def build_analysis_reviewer_successor_sample(
    *,
    forms: Mapping[str, Mapping[str, Any]],
    golds: Mapping[str, Mapping[str, Any]],
    output_schema: Mapping[str, Any],
    slot: int,
) -> dict[str, Any]:
    if slot not in {1, 2, 3}:
        raise ValueError("ANALYSIS_REVIEWER_SAMPLE_SLOT_INVALID")
    sampled_forms: dict[str, dict[str, Any]] = {}
    sampled_golds: dict[str, dict[str, Any]] = {}
    sampled_schemas: dict[str, dict[str, Any]] = {}
    bindings: dict[str, list[dict[str, Any]]] = {}
    all_ids: list[str] = []
    for form_id in ("A", "B"):
        form = forms[form_id]
        gold = golds[form_id]
        gold_rows = [
            row for row in gold.get("cases", []) if isinstance(row, Mapping)
        ]
        selected_gold = [
            deepcopy(dict(_sample_choice(gold_rows, family=family, slot=slot)))
            for family in FAMILY_ORDER
        ]
        selected_ids = [str(row["case_id"]) for row in selected_gold]
        case_by_id = {
            str(row["case_id"]): row
            for row in form.get("cases", [])
            if isinstance(row, Mapping) and isinstance(row.get("case_id"), str)
        }
        if any(case_id not in case_by_id for case_id in selected_ids):
            raise ValueError("ANALYSIS_REVIEWER_SAMPLE_CASE_MISSING")
        sampled_form = deepcopy(dict(form))
        sampled_form["cases"] = [
            deepcopy(dict(case_by_id[case_id])) for case_id in selected_ids
        ]
        sampled_gold = deepcopy(dict(gold))
        sampled_gold["cases"] = selected_gold
        sampled_forms[form_id] = sampled_form
        sampled_golds[form_id] = sampled_gold
        sampled_schemas[form_id] = _project_output_schema(
            output_schema, selected_ids
        )
        bindings[form_id] = [
            {
                "case_id": str(row["case_id"]),
                "family": str(row["expected_issue_family"]),
                "difficulty": str(row["difficulty"]),
                "decision_critical": bool(row.get("critical")),
            }
            for row in selected_gold
        ]
        all_ids.extend(selected_ids)
    if len(all_ids) != 12 or len(all_ids) != len(set(all_ids)):
        raise ValueError("ANALYSIS_REVIEWER_SAMPLE_EXACT_SET_INVALID")
    manifest = {
        "schema_version": ANALYSIS_REVIEWER_SAMPLE_REVISION,
        "sample_slot": slot,
        "source_pack_id": EXPECTED_PACK_ID,
        "selection_policy": (
            "ONE_DECISION_CRITICAL_DEFECT_PER_FAMILY_PLUS_ONE_CLEAN_PER_FORM;"
            "SLOT1_MEDIUM_SLOT2_3_DISJOINT_HARD"
        ),
        "family_order": list(FAMILY_ORDER),
        "bindings": bindings,
        "selected_case_count": len(all_ids),
        "selected_case_ids_sha256": canonical_sha256(all_ids),
        "provider_receives_gold": False,
    }
    manifest["manifest_sha256"] = canonical_sha256(manifest)
    return {
        "forms": sampled_forms,
        "golds": sampled_golds,
        "schemas": sampled_schemas,
        "manifest": manifest,
    }


def _contains_forbidden_provider_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key).lower() in _FORBIDDEN_PROVIDER_KEYS
            or _contains_forbidden_provider_key(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_provider_key(item) for item in value)
    return False


def _score_form_successor(
    *,
    form: Mapping[str, Any],
    gold: Mapping[str, Any],
    response: Mapping[str, Any],
    output_schema: Mapping[str, Any],
    scoring_protocol: Mapping[str, Any],
) -> dict[str, Any]:
    value = deepcopy(
        core_v2.score_form(form, gold, response, output_schema, scoring_protocol)
    )
    phase2_failures = list(value.get("hard_gate_failures") or [])
    rows = {
        str(row.get("case_id")): row
        for row in response.get("case_reviews", [])
        if isinstance(row, Mapping) and isinstance(row.get("case_id"), str)
    }
    gold_by_id = {
        str(row.get("case_id")): row
        for row in gold.get("cases", [])
        if isinstance(row, Mapping) and isinstance(row.get("case_id"), str)
    }

    def operationally_contained(case_id: str) -> bool:
        """Return whether the reviewer already prevents the bad decision.

        Phase2 A4 treated an exact ``critical`` label as the definition of
        critical detection.  The public QSET is narrower: a hard miss occurs
        when a critical defect is reduced to something that need not be acted
        on.  A blocked, correctly targeted and evidence-bound correction is
        therefore operationally contained even when severity calibration is
        one band low.  Exact severity still loses its original points.
        """

        gold_case = gold_by_id.get(case_id)
        row = rows.get(case_id)
        if not isinstance(gold_case, Mapping) or not isinstance(row, Mapping):
            return False
        if row.get("review_verdict") not in {"NEEDS_REVISION", "REJECT"}:
            return False
        required = {
            str(item)
            for item in gold_case.get("required_evidence_refs", [])
            if isinstance(item, str)
        }
        allowed = {
            str(item)
            for item in gold_case.get("allowed_evidence_refs", [])
            if isinstance(item, str)
        }
        accepted_pointers = {
            str(item)
            for item in gold_case.get("accepted_claim_pointers", [])
            if isinstance(item, str)
        }
        accepted_actions = {
            str(item)
            for item in gold_case.get("accepted_actions", [])
            if isinstance(item, str)
        }
        for issue in row.get("issues", []):
            if not isinstance(issue, Mapping) or issue.get("blocking") is not True:
                continue
            observed = issue.get("evidence_refs")
            refs = {
                str(item)
                for item in (observed if isinstance(observed, list) else [])
                if isinstance(item, str)
            }
            if (
                issue.get("claim_pointer") in accepted_pointers
                and issue.get("action") in accepted_actions
                and required.issubset(refs)
                and refs.issubset(allowed)
            ):
                return True
        return False

    phase2_missed = [
        str(case_id) for case_id in value.get("missed_critical_case_ids", [])
    ]
    contained_calibration = sorted(
        case_id for case_id in phase2_missed if operationally_contained(case_id)
    )
    operationally_uncontained = sorted(
        set(phase2_missed) - set(contained_calibration)
    )
    critical_ids = sorted(
        case_id
        for case_id, row in gold_by_id.items()
        if row.get("critical") is True
    )
    containment_rate = (
        100.0
        * (len(critical_ids) - len(operationally_uncontained))
        / len(critical_ids)
        if critical_ids
        else 100.0
    )
    conservative_clean: list[str] = []
    destructive_clean: list[str] = []
    for gold_case in gold.get("cases", []):
        if not isinstance(gold_case, Mapping) or gold_case.get("expected_issue_count") != 0:
            continue
        case_id = str(gold_case["case_id"])
        row = rows.get(case_id, {})
        if row.get("review_verdict") == "ACCEPT" and not row.get("issues"):
            continue
        destructive = row.get("review_verdict") == "REJECT" and any(
            isinstance(issue, Mapping)
            and issue.get("action") in _DESTRUCTIVE_CLEAN_ACTIONS
            for issue in row.get("issues", [])
        )
        (destructive_clean if destructive else conservative_clean).append(case_id)
    failures: list[str] = []
    for reason in phase2_failures:
        if reason == "CLEAN_FALSE_POSITIVE_RATE_ABOVE_0_10" and not destructive_clean:
            continue
        if reason == "MISS_ANY_DECISION_CRITICAL_CASE" and not operationally_uncontained:
            continue
        if (
            reason == "CRITICAL_DETECTION_RATE_BELOW_0_95"
            and containment_rate >= 95.0
        ):
            continue
        failures.append(reason)
    value.update(
        {
            "phase2_hard_gate_failures": phase2_failures,
            "phase2_hard_gates_pass": not phase2_failures,
            "hard_gate_failures": failures,
            "hard_gates_pass": not failures,
            "clean_false_positive_successor_classification": {
                "conservative_calibration_case_ids": sorted(conservative_clean),
                "destructive_safety_blocking_case_ids": sorted(destructive_clean),
                "numeric_clean_specificity_penalty_preserved": True,
            },
            "critical_containment_successor_classification": {
                "phase2_missed_critical_case_ids": sorted(phase2_missed),
                "severity_calibration_case_ids": contained_calibration,
                "operationally_uncontained_case_ids": operationally_uncontained,
                "operational_containment_rate": round(containment_rate, 6),
                "exact_severity_point_penalty_preserved": True,
                "original_phase2_hard_gate_evidence_preserved": True,
            },
            "scoring_projection_revision": (
                ANALYSIS_REVIEWER_SCORING_PROJECTION_REVISION
            ),
        }
    )
    return value


def _wrong_case_ids(score: Mapping[str, Any]) -> list[str]:
    return [
        str(row["case_id"])
        for row in score.get("case_scores", [])
        if isinstance(row, Mapping)
        and isinstance(row.get("score"), (int, float))
        and not isinstance(row.get("score"), bool)
        and float(row["score"]) < 100.0
    ]


def _mismatched_output_fields(
    *,
    gold_case: Mapping[str, Any],
    response_case: Mapping[str, Any],
) -> list[str]:
    """Name mismatched output fields without disclosing any expected value."""

    fields: list[str] = []
    if response_case.get("review_verdict") != gold_case.get("expected_verdict"):
        fields.append("/review_verdict")
    issues = response_case.get("issues")
    issue_rows = issues if isinstance(issues, list) else []
    expected_count = gold_case.get("expected_issue_count")
    if (
        isinstance(expected_count, int)
        and not isinstance(expected_count, bool)
        and len(issue_rows) != expected_count
    ):
        fields.append("/issues")
    if expected_count != 1 or len(issue_rows) != 1:
        return fields
    issue = issue_rows[0]
    if not isinstance(issue, Mapping):
        return [*fields, "/issues/0"]
    if issue.get("issue_family") != gold_case.get("expected_issue_family"):
        fields.append("/issues/0/issue_family")
    accepted_pointers = gold_case.get("accepted_claim_pointers")
    if isinstance(accepted_pointers, list) and issue.get("claim_pointer") not in accepted_pointers:
        fields.append("/issues/0/claim_pointer")
    evidence_refs = issue.get("evidence_refs")
    observed_refs = set(evidence_refs) if isinstance(evidence_refs, list) else set()
    required_refs = set(gold_case.get("required_evidence_refs") or [])
    allowed_refs = set(gold_case.get("allowed_evidence_refs") or [])
    if not required_refs.issubset(observed_refs) or not observed_refs.issubset(allowed_refs):
        fields.append("/issues/0/evidence_refs")
    if issue.get("severity") != gold_case.get("severity"):
        fields.append("/issues/0/severity")
    if issue.get("blocking") != gold_case.get("blocking"):
        fields.append("/issues/0/blocking")
    accepted_actions = gold_case.get("accepted_actions")
    if isinstance(accepted_actions, list) and issue.get("action") not in accepted_actions:
        fields.append("/issues/0/action")
    return fields


def _machine_feedback(
    score: Mapping[str, Any],
    case_ids: Sequence[str],
    *,
    gold: Mapping[str, Any],
    response: Mapping[str, Any],
) -> dict[str, Any]:
    detail = {
        str(row["case_id"]): row
        for row in score.get("case_scores", [])
        if isinstance(row, Mapping) and isinstance(row.get("case_id"), str)
    }
    output: dict[str, Any] = {}
    gold_rows = {
        str(row["case_id"]): row
        for row in gold.get("cases", [])
        if isinstance(row, Mapping) and isinstance(row.get("case_id"), str)
    }
    response_rows = {
        str(row["case_id"]): row
        for row in response.get("case_reviews", [])
        if isinstance(row, Mapping) and isinstance(row.get("case_id"), str)
    }
    for case_id in case_ids:
        row = detail[case_id]
        failed: list[str] = []
        for name, points in row.get("components", {}).items():
            maxima = {
                "accept_verdict": 70.0,
                "no_false_issue": 30.0,
                "detection_and_issue_family": 40.0,
                "claim_localization": 20.0,
                "evidence_precision_recall": 25.0,
                "severity_blocking_action": 15.0,
            }
            if name in maxima and float(points) < maxima[name]:
                failed.append(str(name))
        output[case_id] = {
            "failed_component_groups": failed or ["complete_case_review"],
            "mismatched_output_fields": _mismatched_output_fields(
                gold_case=gold_rows[case_id],
                response_case=response_rows[case_id],
            ),
            "mismatched_output_fields_disclose_expected_values": False,
            "must_reconsider_every_listed_field": True,
            "current_case_score": float(row["score"]),
            "reference_values_disclosed": False,
        }
    return output


def _observed_output_value(
    response_case: Mapping[str, Any], field: str
) -> Any:
    if field == "/review_verdict":
        return deepcopy(response_case.get("review_verdict"))
    issues = response_case.get("issues")
    issue_rows = issues if isinstance(issues, list) else []
    if field == "/issues":
        return len(issue_rows)
    if not issue_rows or not isinstance(issue_rows[0], Mapping):
        return "MISSING"
    issue = issue_rows[0]
    leaf = field.removeprefix("/issues/0/")
    if leaf not in {
        "issue_family",
        "claim_pointer",
        "evidence_refs",
        "severity",
        "blocking",
        "action",
    }:
        return "MISSING"
    return deepcopy(issue.get(leaf, "MISSING"))


def _attach_rejected_observed_values(
    feedback: Mapping[str, Any],
    *,
    response: Mapping[str, Any],
    history: dict[str, dict[str, list[Any]]],
) -> dict[str, Any]:
    """Attach subject-owned rejected values; never attach scorer reference values."""

    response_rows = {
        str(row["case_id"]): row
        for row in response.get("case_reviews", [])
        if isinstance(row, Mapping) and isinstance(row.get("case_id"), str)
    }
    output = deepcopy(dict(feedback))
    for case_id, row in output.items():
        if not isinstance(row, dict):
            continue
        case_history = history.setdefault(case_id, {})
        for field in row.get("mismatched_output_fields", []):
            if not isinstance(field, str):
                continue
            value = _observed_output_value(response_rows[case_id], field)
            values = case_history.setdefault(field, [])
            value_hash = canonical_sha256(value)
            if all(canonical_sha256(existing) != value_hash for existing in values):
                values.append(value)
        row["rejected_observed_values_by_field"] = deepcopy(case_history)
        row["rejected_observed_values_are_subject_outputs"] = True
        row["rejected_observed_values_disclose_expected_values"] = False
    return output


def _merge_exact_rows(
    *,
    form: Mapping[str, Any],
    response: Mapping[str, Any],
    repair_response: Mapping[str, Any],
    target_ids: Sequence[str],
) -> dict[str, Any]:
    targets = list(target_ids)
    repaired = repair_response.get("case_reviews")
    if not isinstance(repaired, list):
        raise ValueError("ANALYSIS_REVIEWER_REPAIR_ROWS_REQUIRED")
    observed = [
        row.get("case_id") if isinstance(row, Mapping) else None for row in repaired
    ]
    if len(observed) != len(set(observed)) or set(observed) != set(targets):
        raise ValueError("ANALYSIS_REVIEWER_REPAIR_EXACT_SET_MISMATCH")
    row_map = {
        str(row["case_id"]): deepcopy(dict(row))
        for row in response.get("case_reviews", [])
        if isinstance(row, Mapping) and isinstance(row.get("case_id"), str)
    }
    for row in repaired:
        row_map[str(row["case_id"])] = deepcopy(dict(row))
    order = [str(row["case_id"]) for row in form.get("cases", [])]
    if any(case_id not in row_map for case_id in order):
        raise ValueError("ANALYSIS_REVIEWER_POST_REPAIR_CASE_MISSING")
    return {"case_reviews": [row_map[case_id] for case_id in order]}


class Phase2AnalysisReviewerExamSuccessorExecutor(Phase2CardReviewerExamExecutor):
    """Independent A4 Analysis Reviewer executor with local-only Gold custody."""

    EXECUTOR_REF = (
        "P08_MODEL_EXAM_SUCCESSOR_P02_ANALYSIS_REVIEWER_EXECUTOR_V6_"
        "MODEL_AWARE_LIMITS"
    )
    MODE = "LIVE_PHASE2_ANALYSIS_REVIEWER_A4_SUCCESSOR_REGRESSION"
    SEMANTIC_REPAIR_ROUND_LIMIT = 2
    API_MAXIMUM_REQUEST_ATTEMPTS = 24
    CLI_MAXIMUM_REQUEST_ATTEMPTS = 24
    LOCAL_MAXIMUM_REQUEST_ATTEMPTS = 24
    API_BUDGET_CAP_CNY = 10.0

    @staticmethod
    def _profile_kind(target: Mapping[str, Any]) -> str | None:
        """Admit both configured DeepSeek exam tiers without widening other profiles."""

        if target.get("kind") == "API":
            provider = re.sub(
                r"[^a-z0-9]", "", str(target.get("provider") or "").casefold()
            )
            model_name = target.get("model_name")
            expected_tier = {
                "deepseek-v4-flash": "flash",
                "deepseek-v4-pro": "pro",
            }.get(model_name)
            if (
                re.fullmatch(r"deepseek(?:apikey[a-z0-9]*)?", provider)
                and expected_tier is not None
                and target.get("thinking") is True
                and target.get("tier") == expected_tier
                and target.get("connection_status") == "AVAILABLE"
                and isinstance(target.get("config_id"), str)
                and bool(target.get("config_id"))
            ):
                return "API"
            return None
        return Phase2CardReviewerExamExecutor._profile_kind(target)

    def _structured_output_policy(
        self, target: Mapping[str, Any]
    ) -> tuple[dict[str, Any], int | None, str]:
        binding, output, mode = workflow_exam_structured_output_policy(target)
        if target.get("kind") == "LOCAL" and int(
            binding["context_window_tokens"]
        ) < 16_384:
            raise ValueError("LOCAL_EXAM_CONTEXT_LIMIT_PROFILE_INVALID")
        return binding, output, mode

    @staticmethod
    def _sample_slot(context: Mapping[str, Any]) -> int:
        explicit = context.get("workflow_exam_sample_slot")
        if explicit is not None:
            if isinstance(explicit, bool) or explicit not in {1, 2, 3}:
                raise ValueError("ANALYSIS_REVIEWER_SAMPLE_SLOT_INVALID")
            return int(explicit)
        ordinal = context.get("workflow_exam_attempt_ordinal", 1)
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1:
            raise ValueError("ANALYSIS_REVIEWER_ATTEMPT_ORDINAL_INVALID")
        return ((ordinal - 1) % 3) + 1

    def _load_sample(self, context: Mapping[str, Any]) -> dict[str, Any]:
        verified = verify_analysis_reviewer_local_pack(self._reference_pack_root)
        forms = {
            form_id: _load_json(self._reference_pack_root / f"forms/form_{form_id}.json")
            for form_id in ("A", "B")
        }
        golds = {
            form_id: _load_json(self._reference_pack_root / f"gold/form_{form_id}_gold.json")
            for form_id in ("A", "B")
        }
        schema = _load_json(
            self._reference_pack_root / "schemas/analysis_reviewer_output.schema.json"
        )
        sample = build_analysis_reviewer_successor_sample(
            forms=forms,
            golds=golds,
            output_schema=schema,
            slot=self._sample_slot(context),
        )
        sample["verification"] = verified
        sample["prompt"] = _load_json(
            self._reference_pack_root / "prompts/analysis_reviewer_prompt.json"
        )
        sample["scoring"] = _load_json(
            self._reference_pack_root / "scoring/scoring_protocol.json"
        )
        return sample

    def plan(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        if node.get("node_id") != "judgment_review":
            return _not_available("WORKFLOW_MODEL_EXAM_DYNAMIC_NODE_UNSUPPORTED")
        requested_role = context.get("workflow_exam_role_id")
        if requested_role not in {None, ANALYSIS_REVIEWER_ROLE_ID}:
            return _not_available("WORKFLOW_MODEL_EXAM_ROLE_MISMATCH")
        profile_kind = self._profile_kind(target)
        if profile_kind is None:
            return _not_available("WORKFLOW_MODEL_EXAM_DYNAMIC_PROFILE_UNSUPPORTED")
        try:
            sample = self._load_sample(context)
            _binding, request_max_output, output_mode = self._structured_output_policy(target)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
            reason = str(error)
            if re.fullmatch(r"[A-Z0-9_:.-]{1,160}", reason):
                return _not_available(reason)
            return _not_available("ANALYSIS_REVIEWER_LOCAL_PACK_INVALID")
        metrics: dict[str, Any] = {}
        total_chars = 0
        for form_id in ("A", "B"):
            request = {
                "phase": "INITIAL",
                "system": sample["prompt"]["system"],
                "user": sample["prompt"]["user_template"],
                "form": sample["forms"][form_id],
                "output_schema": sample["schemas"][form_id],
            }
            observed = _effective_structured_prompt_metrics(
                json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                sample["schemas"][form_id],
            )
            observed["exceeds_phase1_soft_reference"] = (
                observed["effective_prompt_chars"]
                > PHASE1_CARD_REVIEWER_EFFECTIVE_PROMPT_CHAR_LIMIT
            )
            observed["phase1_reference_semantics"] = (
                "SHARDING_AND_DIAGNOSTIC_GUIDANCE_NOT_A_FAILURE_GATE"
            )
            metrics[form_id] = observed
            total_chars += observed["effective_prompt_chars"]
        verification = sample["verification"]
        scoring_path = self._reference_pack_root / "scoring/scoring_protocol.json"
        comparison = {
            "exam_category_id": ANALYSIS_REVIEWER_CATEGORY_ID,
            "role_id": ANALYSIS_REVIEWER_ROLE_ID,
            "reference_pack_id": EXPECTED_PACK_ID,
            "reference_pack_revision": EXPECTED_PACK_REVISION,
            "reference_pack_sha256": EXPECTED_MANIFEST_SHA256,
            "scoring_protocol_revision": sample["scoring"]["schema_version"],
            "scoring_protocol_sha256": _file_sha256(scoring_path),
            "scoring_projection_revision": ANALYSIS_REVIEWER_SCORING_PROJECTION_REVISION,
            "sample_revision": ANALYSIS_REVIEWER_SAMPLE_REVISION,
            "comparison_cohort_id": (
                "ANALYSIS_REVIEWER_SUCCESSOR:"
                + canonical_sha256(
                    {
                        "pack": EXPECTED_MANIFEST_SHA256,
                        "scorer": _file_sha256(scoring_path),
                        "projection": ANALYSIS_REVIEWER_SCORING_PROJECTION_REVISION,
                        "sample": sample["manifest"],
                    }
                )
            ),
            "horizontal_comparison_eligible": True,
            "horizontal_comparison_allowed_only_with_same_cohort": True,
            "cross_category_comparison_forbidden": True,
            "global_ranking_forbidden": True,
        }
        comparison["comparison_binding_sha256"] = canonical_sha256(comparison)
        plan = {
            "schema_version": PLAN_SCHEMA,
            "status": "READY",
            "mode": self.MODE,
            "reason": "PHASE2_ANALYSIS_REVIEWER_SUCCESSOR_READY",
            "node_id": "judgment_review",
            "executor_ref": self.EXECUTOR_REF,
            "profile_kind": profile_kind,
            "profile_ref": target["config_id"],
            "model_name": target["model_name"],
            "settings_revision": context.get("settings_revision"),
            "settings_sha256": context.get("settings_sha256"),
            **comparison,
            "comparison": deepcopy(comparison),
            "sample_slot": sample["manifest"]["sample_slot"],
            "sample_manifest_sha256": sample["manifest"]["manifest_sha256"],
            "sample_case_count": 12,
            "workload_profile": phase1_card_reviewer_workload_profile(),
            "effective_prompt_metrics_by_half": metrics,
            "total_effective_prompt_chars": total_chars,
            "total_effective_prompt_chars_exceeds_single_call_reference": (
                total_chars > PHASE1_CARD_REVIEWER_ABSOLUTE_PROMPT_CHAR_LIMIT
            ),
            "total_effective_prompt_chars_semantics": (
                "SUM_OF_TWO_INDEPENDENT_CALLS_DIAGNOSTIC_ONLY_"
                "NEVER_COMPARED_TO_ONE_CALL_LIMIT"
            ),
            "semantic_repair_round_limit": 2,
            "second_semantic_repair_allowed": True,
            "maximum_model_calls": self._request_cap(profile_kind),
            "transient_retry_limit_per_call": self.TRANSIENT_RETRY_LIMIT,
            "budget_cap_cny": self.API_BUDGET_CAP_CNY if profile_kind == "API" else None,
            "cost_semantics": (
                "PROSPECTIVE_PRICE_SNAPSHOT_WORST_CASE_CAP"
                if profile_kind == "API"
                else (
                    "SUBSCRIPTION_CLI_NO_PER_CALL_PRICE"
                    if profile_kind == "CLI"
                    else "LOCAL_COMPUTE_NO_PROVIDER_API_COST"
                )
            ),
            "structured_output_profile": {
                "mode": output_mode,
                "request_max_output_tokens": request_max_output,
                "profile_specific_not_global": True,
            },
            "calibrated_reply_token_ceiling": (
                WORKFLOW_EXAM_REPLY_TOKEN_CEILING
            ),
            "output_limit_contract": workflow_exam_output_limit_contract(),
            "local_pack_authority": {
                "classification": "REFERENCE_EXAM_PACK_CANDIDATE",
                "declassification_authorized": verification["declassification_authorized"],
                "distributable_exe_embedding_allowed": False,
                "runtime_mode": verification["runtime_mode"],
                "manifest_sha256": EXPECTED_MANIFEST_SHA256,
                "checksums_sha256": EXPECTED_CHECKSUMS_SHA256,
            },
            "bundled_scorer_provenance": {
                "source_core_v1_sha256": SOURCE_CORE_V1_SHA256,
                "source_core_v2_sha256": SOURCE_CORE_V2_SHA256,
                "import_adapter_revision": BUNDLED_CORE_V2_IMPORT_ADAPTER_REVISION,
            },
            "reference_regression_only": True,
            "blind_holdout_eligible": False,
            "qualification_eligible": False,
            "authorization_schema_version": AUTHORIZATION_SCHEMA,
            "external_network_calls": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
        }
        plan["plan_sha256"] = canonical_sha256(plan)
        return plan

    def _call(
        self,
        *,
        run_id: str,
        run_root: Path,
        state: dict[str, Any],
        target: Mapping[str, Any],
        request: Mapping[str, Any],
        schema: Mapping[str, Any],
        structured_chat: Callable[..., Mapping[str, Any]],
        call_kind: str,
    ) -> dict[str, Any]:
        if _contains_forbidden_provider_key(request):
            raise ValueError("ANALYSIS_REVIEWER_PROVIDER_REQUEST_CONTAINS_GOLD")
        return self._stable_provider_call(
            run_id=run_id,
            run_root=run_root,
            state=state,
            target=target,
            request=request,
            response_schema=schema,
            structured_chat=structured_chat,
            call_kind=call_kind,
        )

    @staticmethod
    def _repair_penalty(
        scoring: Mapping[str, Any], target_count: int, case_count: int
    ) -> dict[str, Any]:
        contract = scoring["directed_repair"]
        fixed = float(contract["fixed_turn_penalty"]) if target_count else 0.0
        volume = (
            float(
                core_v2.interpolate(
                    contract["volume_penalty_knots"],
                    target_count / case_count,
                )
            )
            if target_count
            else 0.0
        )
        return {
            "fixed_penalty": round(fixed, 6),
            "volume_penalty": round(volume, 6),
            "total": round(fixed + volume, 6),
            "volume_denominator_case_count": case_count,
            "volume_denominator_semantics": (
                "FROZEN_A4_SOURCE_FORM_CASE_COUNT_NOT_SUCCESSOR_SAMPLE_COUNT"
            ),
        }

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
        requested_model = target.get("model_name")
        if plan.get("status") != "READY":
            return _not_run(str(plan.get("reason")), requested_model=str(requested_model))
        reason = self._authorization_reason(authorization, plan)
        if reason is not None:
            return _not_run(reason, requested_model=str(requested_model))
        run_id = "analysis-reviewer-exam-" + uuid.uuid4().hex
        run_root = self._scratch_root / "workflow_exams" / run_id
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
            sample = self._load_sample(context)
            responses: dict[str, dict[str, Any]] = {}
            initial_scores: dict[str, dict[str, Any]] = {}
            final_scores: dict[str, dict[str, Any]] = {}
            repair_records: dict[str, list[dict[str, Any]]] = {"A": [], "B": []}
            repair_penalties: dict[str, list[dict[str, Any]]] = {"A": [], "B": []}
            for form_id in ("A", "B"):
                form = sample["forms"][form_id]
                gold = sample["golds"][form_id]
                schema = sample["schemas"][form_id]
                request = {
                    "phase": "INITIAL",
                    "system": sample["prompt"]["system"],
                    "user": sample["prompt"]["user_template"],
                    "form": form,
                    "output_schema": schema,
                }
                request["request_payload_hash"] = canonical_sha256(request)
                response = self._call(
                    run_id=run_id,
                    run_root=run_root,
                    state=state,
                    target=target,
                    request=request,
                    schema=schema,
                    structured_chat=structured_chat,
                    call_kind=f"FORM_{form_id}_INITIAL",
                )
                normalization = core_v2.normalize_claim_pointer_leaves(form, response)
                response = deepcopy(dict(normalization["normalized_response"]))
                audit = core_v2.audit_response(form, response, schema)
                if not audit.get("schema_valid") or not audit.get("exact_case_set_valid"):
                    raise ValueError("ANALYSIS_REVIEWER_INITIAL_RESPONSE_NOT_SCOREABLE")
                score = _score_form_successor(
                    form=form,
                    gold=gold,
                    response=response,
                    output_schema=schema,
                    scoring_protocol=sample["scoring"],
                )
                initial_scores[form_id] = deepcopy(score)
                unchanged_surface = canonical_sha256(
                    {"form": form, "schema": schema, "scoring": sample["scoring"]}
                )
                prior_eligible: Mapping[str, Sequence[str]] | None = None
                rejected_observed_history: dict[str, dict[str, list[Any]]] = {}
                for round_number in (1, 2):
                    target_ids = _wrong_case_ids(score)
                    if not target_ids:
                        break
                    case_by_id = {
                        str(row["case_id"]): row for row in form["cases"]
                    }
                    row_by_id = {
                        str(row["case_id"]): row
                        for row in response["case_reviews"]
                    }
                    repair_schema = _project_output_schema(schema, target_ids)
                    feedback = _attach_rejected_observed_values(
                        _machine_feedback(
                            score,
                            target_ids,
                            gold=gold,
                            response=response,
                        ),
                        response=response,
                        history=rejected_observed_history,
                    )
                    repair_prompt = {
                        "instruction": (
                            "Replace all and only the listed residual case reviews. "
                            "Use the original rubric and local evidence. Machine feedback "
                            "names failed component groups and the exact output fields that "
                            "still mismatch, but supplies no expected values. Reconsider every "
                            "listed field from the original rubric and evidence. Values listed "
                            "under rejected_observed_values_by_field are your own earlier answers "
                            "that did not satisfy the scorer; do not reuse them for that field. "
                            "Return JSON only."
                        ),
                        "semantic_repair_round": round_number,
                        "repair_exact_set": target_ids,
                        "target_cases": [deepcopy(case_by_id[item]) for item in target_ids],
                        "current_case_reviews": [deepcopy(row_by_id[item]) for item in target_ids],
                        "machine_feedback": feedback,
                        "repair_output_schema": repair_schema,
                        "reference_values_disclosed": False,
                    }
                    lifecycle = build_repair_round(
                        round_number=round_number,
                        category_id=ANALYSIS_REVIEWER_CATEGORY_ID,
                        targets_by_form={form_id: target_ids},
                        prior_targets_by_form=(
                            {form_id: list(prior_eligible[form_id])}
                            if prior_eligible is not None
                            else None
                        ),
                        prompt_projection=repair_prompt,
                        unchanged_surface_sha256=unchanged_surface,
                    )
                    repair_request = {
                        "phase": "EXAM_SEMANTIC_DIRECTED_REPAIR",
                        "semantic_repair_round": round_number,
                        "system": sample["prompt"]["system"],
                        "user": repair_prompt,
                        "form": {
                            **{key: deepcopy(value) for key, value in form.items() if key != "cases"},
                            "cases": repair_prompt["target_cases"],
                        },
                        "output_schema": repair_schema,
                        "repair_lifecycle_sha256": lifecycle["contract_sha256"],
                    }
                    repair_request["request_payload_hash"] = canonical_sha256(repair_request)
                    repair_response = self._call(
                        run_id=run_id,
                        run_root=run_root,
                        state=state,
                        target=target,
                        request=repair_request,
                        schema=repair_schema,
                        structured_chat=structured_chat,
                        call_kind=f"FORM_{form_id}_SEMANTIC_REPAIR_ROUND_{round_number}",
                    )
                    subset_form = {
                        **{key: deepcopy(value) for key, value in form.items() if key != "cases"},
                        "cases": repair_prompt["target_cases"],
                    }
                    normalized = core_v2.normalize_claim_pointer_leaves(
                        subset_form, repair_response
                    )["normalized_response"]
                    subset_audit = core_v2.audit_response(
                        subset_form, normalized, repair_schema
                    )
                    if not subset_audit.get("schema_valid") or not subset_audit.get("exact_case_set_valid"):
                        raise ValueError("ANALYSIS_REVIEWER_REPAIR_RESPONSE_NOT_SCOREABLE")
                    response = _merge_exact_rows(
                        form=form,
                        response=response,
                        repair_response=normalized,
                        target_ids=target_ids,
                    )
                    score = _score_form_successor(
                        form=form,
                        gold=gold,
                        response=response,
                        output_schema=schema,
                        scoring_protocol=sample["scoring"],
                    )
                    penalty = self._repair_penalty(
                        sample["scoring"], len(target_ids), 72
                    )
                    repair_penalties[form_id].append(
                        {"round_number": round_number, **penalty}
                    )
                    repair_records[form_id].append(lifecycle)
                    prior_eligible = {form_id: _wrong_case_ids(score)}
                responses[form_id] = response
                final_scores[form_id] = score

            first_pass = round(
                sum(float(initial_scores[item]["score"]) for item in ("A", "B")) / 2.0,
                6,
            )
            adjusted_by_form: dict[str, float] = {}
            cumulative_by_form: dict[str, Any] = {}
            for form_id in ("A", "B"):
                rows = repair_penalties[form_id]
                if rows:
                    cumulative = cumulative_category_repair_penalty(
                        [
                            {
                                "round_number": row["round_number"],
                                "category_id": ANALYSIS_REVIEWER_CATEGORY_ID,
                                "nonlinear_category_penalty": 0.0,
                                "fixed_penalty": row["fixed_penalty"],
                                "volume_penalty": row["volume_penalty"],
                            }
                            for row in rows
                        ]
                    )
                    burden = float(cumulative["cumulative_penalty"])
                    cumulative_by_form[form_id] = cumulative
                else:
                    burden = 0.0
                    cumulative_by_form[form_id] = None
                adjusted_by_form[form_id] = round(
                    max(0.0, float(final_scores[form_id]["score"]) - burden), 6
                )
            score_exact = round(sum(adjusted_by_form.values()) / 2.0, 6)
            residual_by_form = {
                form_id: _wrong_case_ids(final_scores[form_id])
                for form_id in ("A", "B")
            }
            residual_count = sum(len(rows) for rows in residual_by_form.values())
            safety_ids = sorted(
                {
                    case_id
                    for form_id in ("A", "B")
                    for case_id in final_scores[form_id]
                    .get("critical_containment_successor_classification", {})
                    .get("operationally_uncontained_case_ids", [])
                }
                | {
                    case_id
                    for form_id in ("A", "B")
                    for case_id in final_scores[form_id][
                        "clean_false_positive_successor_classification"
                    ]["destructive_safety_blocking_case_ids"]
                }
            )
            calibration_ids = sorted(
                set(case_id for rows in residual_by_form.values() for case_id in rows)
                - set(safety_ids)
            )
            disposition = terminal_disposition(
                scorability=Scorability.SCOREABLE,
                residual_count=residual_count,
                safety_blocking_residual_count=len(safety_ids),
                calibration_residual_count=len(calibration_ids),
                governed_disposition_count=0,
            )
            hard_gates_pass = all(
                final_scores[form_id]["hard_gates_pass"] for form_id in ("A", "B")
            )
            operational_pass = hard_gates_pass and score_exact >= 80.0 and not safety_ids
            repair_rounds_used = max(
                len(repair_records["A"]), len(repair_records["B"])
            )
            blocking_rows = [
                {
                    "case_id": case_id,
                    "failed_fields": (
                        ["decision_critical_review"]
                        if case_id in safety_ids
                        else ["residual_scoring_components"]
                    ),
                    "evidence_anchor": f"forms/{form_id}/case_scores/{case_id}",
                    "blocking": True,
                }
                for form_id in ("A", "B")
                for case_id in residual_by_form[form_id]
            ]
            if not operational_pass and not blocking_rows:
                blocking_rows.append(
                    {
                        "case_id": "ANALYSIS_REVIEWER_AGGREGATE",
                        "failed_fields": ["aggregate_score_or_hard_gate"],
                        "evidence_anchor": "exam_result/aggregate",
                        "blocking": True,
                    }
                )
            if operational_pass:
                fail_admission = None
                verdict = "PASS"
            else:
                fail_admission = admit_quality_fail(
                    execution_outcome="COMPLETED",
                    scorability="SCOREABLE",
                    repair_rounds_used=repair_rounds_used,
                    repair_round_limit=2,
                    repair_cycle_closed=(
                        repair_rounds_used == 2 or residual_count == 0
                    ),
                    blocking_failures=blocking_rows,
                    exact_hashes={
                        "reference_pack_sha256": EXPECTED_MANIFEST_SHA256,
                        "scoring_protocol_sha256": plan["scoring_protocol_sha256"],
                        "executor_sha256": _file_sha256(Path(__file__)),
                        "input_exact_set_sha256": canonical_sha256(
                            sample["manifest"]["bindings"]
                        ),
                    },
                )
                verdict = (
                    "FAIL"
                    if fail_admission["fail_admitted"]
                    else "NOT_ASSESSED"
                )
            public = {
                "schema_version": "WorkflowModelExamRunResult-v2",
                "status": verdict,
                "execution_outcome": "COMPLETED",
                "scorability": "SCOREABLE",
                "raw_quality_candidate_verdict": (
                    "PASS" if operational_pass else "FAIL"
                ),
                "quality_verdict": verdict,
                "reason": "PHASE2_ANALYSIS_REVIEWER_SUCCESSOR_COMPLETED",
                "run_id": run_id,
                "plan_sha256": plan["plan_sha256"],
                "executor_ref": self.EXECUTOR_REF,
                "executor_sha256": _file_sha256(Path(__file__)),
                "requested_model": requested_model,
                "returned_model": requested_model,
                "score": round(score_exact),
                "score_exact": score_exact,
                "score_semantics": "POST_REPAIR_A4_NONLINEAR_SCORE_WITH_CUMULATIVE_CATEGORY_BURDEN",
                "first_pass_score": first_pass,
                "form_scores": adjusted_by_form,
                "raw_final_form_scores": {
                    form_id: final_scores[form_id]["score"] for form_id in ("A", "B")
                },
                "exam_category_id": ANALYSIS_REVIEWER_CATEGORY_ID,
                "role_id": ANALYSIS_REVIEWER_ROLE_ID,
                "reference_pack_id": EXPECTED_PACK_ID,
                "reference_pack_revision": EXPECTED_PACK_REVISION,
                "reference_pack_sha256": EXPECTED_MANIFEST_SHA256,
                "scoring_protocol_revision": plan["scoring_protocol_revision"],
                "scoring_protocol_sha256": plan["scoring_protocol_sha256"],
                "scoring_projection_revision": ANALYSIS_REVIEWER_SCORING_PROJECTION_REVISION,
                "comparison_binding_sha256": plan["comparison_binding_sha256"],
                "comparison_cohort_id": plan["comparison_cohort_id"],
                "horizontal_comparison_eligible": True,
                "horizontal_comparison_allowed_only_with_same_cohort": True,
                "cross_category_comparison_forbidden": True,
                "global_ranking_forbidden": True,
                "sample_revision": ANALYSIS_REVIEWER_SAMPLE_REVISION,
                "sample_slot": sample["manifest"]["sample_slot"],
                "sample_manifest_sha256": sample["manifest"][
                    "manifest_sha256"
                ],
                "sample_metadata": sample["manifest"],
                "semantic_repair_rounds": repair_rounds_used,
                "semantic_repair_round_limit": 2,
                "semantic_repair_lifecycle_by_form": repair_records,
                "cumulative_category_repair_penalty_by_form": cumulative_by_form,
                "failed_exam_item_count": residual_count,
                "residual_case_ids_by_form": residual_by_form,
                "safety_blocking_residual_count": len(safety_ids),
                "safety_blocking_case_ids": safety_ids,
                "calibration_residual_count": len(calibration_ids),
                "calibration_case_ids": calibration_ids,
                "terminal_disposition": disposition.value,
                "operational_eligibility_verdict": (
                    "SUITABLE"
                    if operational_pass and not residual_count
                    else (
                        "SUITABLE_WITH_CALIBRATION"
                        if operational_pass
                        else "NOT_RECOMMENDED"
                    )
                ),
                "operational_safety_gate": "PASS" if not safety_ids else "FAIL",
                "operational_policy_revision": ANALYSIS_REVIEWER_OPERATIONAL_POLICY_REVISION,
                "quality_fail_admission": fail_admission,
                "provider_received_answer_key": False,
                "local_pack_embedded_in_exe": False,
                "local_pack_runtime_resolver_required": True,
                "workload_profile": plan["workload_profile"],
                "total_effective_prompt_chars": plan["total_effective_prompt_chars"],
                "provider_calls": state["provider_calls"],
                "request_attempts": state["request_attempts"],
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
            }
            for form_id, response in responses.items():
                _write_json_create_only(
                    run_root / "responses" / f"form_{form_id}_final.json", response
                )
            evidence = _write_json_create_only(run_root / "exam_result.json", public)
            return {
                "schema_version": RESULT_SCHEMA,
                "kind": "WORKFLOW_NODE",
                **{key: value for key, value in public.items() if key not in {"schema_version", "run_id"}},
                "duration_ms": max(0, int((monotonic() - started) * 1000)),
                "exam_mode": self.MODE,
                "exam_run_id": run_id,
                "exam_run_root": str(run_root),
                "exam_evidence_sha256": evidence["sha256"],
            }
        except Exception as error:
            reason = str(error) or type(error).__name__
            if len(reason) > 200:
                reason = type(error).__name__
            execution_outcome = classify_execution_outcome(
                {"status": "ERROR", "reason": reason}
            ).value
            failure = {
                "schema_version": "WorkflowModelExamRunFailure-v2",
                "status": "NOT_ASSESSED",
                "execution_outcome": execution_outcome,
                "scorability": "NOT_ASSESSED",
                "quality_verdict": "NOT_ASSESSED",
                "reason": reason,
                "run_id": run_id,
                "plan_sha256": plan["plan_sha256"],
                "provider_calls": state["provider_calls"],
                "request_attempts": state["request_attempts"],
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
            }
            evidence = _write_json_create_only(run_root / "exam_failure.json", failure)
            result = _not_run(reason, requested_model=str(requested_model))
            result.update(
                assessment_status="NOT_ASSESSED",
                execution_outcome=execution_outcome,
                scorability="NOT_ASSESSED",
                quality_verdict="NOT_ASSESSED",
                duration_ms=max(0, int((monotonic() - started) * 1000)),
                provider_calls=state["provider_calls"],
                request_attempts=state["request_attempts"],
                external_model_calls=state["external_model_calls"],
                external_network_calls=state["external_network_calls"],
                external_process_launches=state["external_process_launches"],
                exam_run_id=run_id,
                exam_run_root=str(run_root),
                exam_evidence_sha256=evidence["sha256"],
                token_usage=state["token_usage"],
                estimated_cost_cny=failure["estimated_cost_cny"],
                cost_semantics=plan["cost_semantics"],
            )
            return result


__all__ = [
    "ANALYSIS_REVIEWER_CATEGORY_ID",
    "ANALYSIS_REVIEWER_ROLE_ID",
    "ANALYSIS_REVIEWER_SAMPLE_REVISION",
    "ANALYSIS_REVIEWER_SCORING_PROJECTION_REVISION",
    "Phase2AnalysisReviewerExamSuccessorExecutor",
    "build_analysis_reviewer_successor_sample",
    "verify_analysis_reviewer_local_pack",
]
