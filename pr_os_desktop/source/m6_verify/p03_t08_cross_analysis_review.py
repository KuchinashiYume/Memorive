"""Provider-neutral P03 cross-analysis review and targeted closure.

This is a structural successor of the Phase-1 atomic Card closure:

    one Full review -> one consolidated producer repair -> one Delta review
    -> omit optional unresolved claims with tombstones or block required claims.

The original analysis is immutable.  Provider HTTP, credentials, retries,
fallback and receipt persistence remain owned by M9/M11 and are injected as
strict callables.  There is no whole-analysis regeneration and no second Full
review route.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import jsonschema

from m9_gateway.prompts import PromptRegistry


PASS = "PASS"
PASS_WITH_OMISSIONS = "PASS_WITH_OMISSIONS"
BLOCKED = "BLOCKED"

REVIEW_PROVIDER = "deepseek"
REVIEW_MODEL = "deepseek-v4-pro"
REVIEW_FULL_MAX_TOKENS = 8192
REVIEW_DELTA_MAX_TOKENS = 4096
REPAIR_MAX_TOKENS = 16384

FULL_REVIEW_VERSION = "pr-os.p03.t08.cross-analysis-review.v1"
DELTA_REVIEW_VERSION = "pr-os.p03.t08.cross-analysis-delta-review.v1"
REPAIR_VERSION = "pr-os.p03.t08.cross-analysis-targeted-repair.v1"
TOMBSTONE_VERSION = "pr-os.p03.t08.cross-analysis-tombstone.v1"

_PROMPTS = PromptRegistry(Path(__file__).resolve().parents[1] / "m9_gateway" / "prompts")
_SHA256 = re.compile(r"^[A-F0-9]{64}$")
_SOURCE_ID = re.compile(r"^[A-Za-z0-9_-]+#c[0-9]{4}$")
_SUCCESS_STOPS = frozenset({"stop", "end_turn"})
_SECTION_ORDER = (
    "scope_and_evidence_boundary",
    "comparison_dimensions",
    "evolution_path",
    "consensus_and_continuities",
    "differences_and_tensions",
    "mechanisms_and_tradeoffs",
    "limitations_and_uncertainties",
    "recheck_recommendations",
)
_PROTECTED_TOP_LEVEL = (
    "schema_version",
    "artifact_id",
    "group_id",
    "title",
    "primary_question",
    "execution",
    "evidence_scope",
    "required_facet_coverage",
    "judgment_boundary",
)


class CrossAnalysisReviewError(RuntimeError):
    """The review/repair closure cannot produce a safe successor."""


@dataclass(frozen=True)
class FrozenEvidenceSnippet:
    """One exact, provider-visible source snippet for a cited source ID."""

    source_id: str
    paper_id: str
    artifact_type: str
    artifact_sha256: str
    text: str


@dataclass(frozen=True)
class ReviewProfile:
    """Exact reviewer identity and shallow-reasoning request envelope."""

    slot: str
    provider: str
    model: str
    thinking: Mapping[str, str]
    effort: str | None
    full_max_tokens: int
    delta_max_tokens: int
    timeout_seconds: int


DEFAULT_REVIEW_PROFILE = ReviewProfile(
    slot="verify_judgment",
    provider=REVIEW_PROVIDER,
    model=REVIEW_MODEL,
    thinking={"type": "disabled"},
    effort=None,
    full_max_tokens=REVIEW_FULL_MAX_TOKENS,
    delta_max_tokens=REVIEW_DELTA_MAX_TOKENS,
    timeout_seconds=600,
)


@dataclass(frozen=True)
class CrossAnalysisClosureResult:
    status: str
    source_artifact_sha256: str
    final_artifact_sha256: str | None
    final_artifact: dict[str, Any] | None
    full_review: dict[str, Any]
    repair_bundle: dict[str, Any] | None
    repair_response: dict[str, Any] | None
    delta_view: dict[str, Any] | None
    delta_review: dict[str, Any] | None
    tombstones: tuple[dict[str, Any], ...]
    review_call_count: int
    repair_call_count: int
    reason_codes: tuple[str, ...]


ModelCall = Callable[[dict[str, Any]], Mapping[str, Any]]


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest().upper()


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest().upper()


def _safe_text(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise CrossAnalysisReviewError(f"{label}_INVALID")
    if value != value.strip() or any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise CrossAnalysisReviewError(f"{label}_UNSAFE")
    return value


def _snippet_map(values: Sequence[FrozenEvidenceSnippet]) -> dict[str, FrozenEvidenceSnippet]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise CrossAnalysisReviewError("EVIDENCE_SNIPPETS_INVALID")
    result: dict[str, FrozenEvidenceSnippet] = {}
    for index, item in enumerate(values):
        if not isinstance(item, FrozenEvidenceSnippet):
            raise CrossAnalysisReviewError(f"EVIDENCE_SNIPPET_TYPE_INVALID:{index}")
        if not _SOURCE_ID.fullmatch(item.source_id):
            raise CrossAnalysisReviewError(f"EVIDENCE_SOURCE_ID_INVALID:{index}")
        _safe_text(item.paper_id, f"EVIDENCE_PAPER_ID:{index}")
        if item.artifact_type not in {"CARD", "ANALYSIS"}:
            raise CrossAnalysisReviewError(f"EVIDENCE_ARTIFACT_TYPE_INVALID:{index}")
        if not _SHA256.fullmatch(item.artifact_sha256):
            raise CrossAnalysisReviewError(f"EVIDENCE_ARTIFACT_SHA256_INVALID:{index}")
        _safe_text(item.text, f"EVIDENCE_TEXT:{index}")
        if item.source_id in result:
            raise CrossAnalysisReviewError(f"EVIDENCE_SOURCE_ID_DUPLICATE:{item.source_id}")
        result[item.source_id] = item
    return result


def _evidence_ids(
    refs: Any,
    snippets: Mapping[str, FrozenEvidenceSnippet],
    *,
    label: str,
) -> list[str]:
    if not isinstance(refs, list) or not refs:
        raise CrossAnalysisReviewError(f"{label}_EVIDENCE_REFS_INVALID")
    result: list[str] = []
    for index, ref in enumerate(refs):
        if not isinstance(ref, Mapping):
            raise CrossAnalysisReviewError(f"{label}_EVIDENCE_REF_INVALID:{index}")
        source_id = ref.get("source_id")
        snippet = snippets.get(source_id)
        if snippet is None:
            raise CrossAnalysisReviewError(f"EVIDENCE_BINDING_MISMATCH:{label}:{source_id}")
        observed = (
            ref.get("paper_id"),
            ref.get("artifact_type"),
            ref.get("artifact_sha256"),
        )
        expected = (snippet.paper_id, snippet.artifact_type, snippet.artifact_sha256)
        if observed != expected:
            raise CrossAnalysisReviewError(f"EVIDENCE_BINDING_MISMATCH:{label}:{source_id}")
        if source_id not in result:
            result.append(source_id)
    return result


def _validate_analysis_identity(
    analysis: Any,
    *,
    expected_primary_question: str | None,
) -> dict[str, Any]:
    if not isinstance(analysis, Mapping):
        raise CrossAnalysisReviewError("ANALYSIS_NOT_OBJECT")
    value = copy.deepcopy(dict(analysis))
    if value.get("schema_version") != "pr-os.p03.t08.cross-topic-analysis.v2":
        raise CrossAnalysisReviewError("ANALYSIS_SCHEMA_VERSION_MISMATCH")
    question = _safe_text(value.get("primary_question"), "PRIMARY_QUESTION")
    if expected_primary_question is not None and question != expected_primary_question:
        raise CrossAnalysisReviewError("PRIMARY_QUESTION_BINDING_MISMATCH")
    _safe_text(value.get("executive_answer"), "EXECUTIVE_ANSWER")
    execution = value.get("execution")
    if not isinstance(execution, Mapping):
        raise CrossAnalysisReviewError("EXECUTION_BINDING_MISSING")
    _safe_text(execution.get("provider"), "EXECUTION_PROVIDER")
    _safe_text(execution.get("analysis_model"), "EXECUTION_MODEL")
    sections = value.get("sections")
    if not isinstance(sections, list) or tuple(
        item.get("section_id") if isinstance(item, Mapping) else None
        for item in sections
    ) != _SECTION_ORDER:
        raise CrossAnalysisReviewError("ANALYSIS_SECTION_ORDER_MISMATCH")
    judgment = value.get("judgment_boundary")
    if not isinstance(judgment, Mapping) or (
        judgment.get("winner_selected") is not False
        or judgment.get("overall_scientific_score_assigned") is not False
        or judgment.get("final_user_judgment_made") is not False
        or judgment.get("scientific_maturity") != "NOT_ASSESSED"
    ):
        raise CrossAnalysisReviewError("ANALYSIS_JUDGMENT_BOUNDARY_INVALID")
    return value


def _claim(
    *,
    claim_id: str,
    target_path: str,
    claim_type: str,
    criticality: str,
    value: str,
    allowed_evidence_source_ids: Sequence[str],
    dependent_claim_ids: Sequence[str] = (),
    context: Mapping[str, Any] | None = None,
    deletion_path: str | None = None,
) -> dict[str, Any]:
    return {
        "claim_id": claim_id,
        "target_path": target_path,
        "claim_type": claim_type,
        "criticality": criticality,
        "value": value,
        "allowed_evidence_source_ids": list(dict.fromkeys(allowed_evidence_source_ids)),
        "dependent_claim_ids": list(dict.fromkeys(dependent_claim_ids)),
        "context": copy.deepcopy(dict(context or {})),
        "deletion_path": deletion_path,
    }


def build_cross_analysis_review_view(
    analysis: Mapping[str, Any],
    evidence_snippets: Sequence[FrozenEvidenceSnippet],
    *,
    expected_primary_question: str | None = None,
) -> dict[str, Any]:
    """Build the exact atomic Full-review view from a validated topic output."""

    value = _validate_analysis_identity(
        analysis,
        expected_primary_question=expected_primary_question,
    )
    snippets = _snippet_map(evidence_snippets)
    claims: list[dict[str, Any]] = []
    item_claim_ids: list[str] = []
    section_summary_ids: dict[str, str] = {}
    seen_item_ids: set[str] = set()

    for section_index, section in enumerate(value["sections"]):
        section_id = section["section_id"]
        items = section.get("items")
        if not isinstance(items, list) or not items:
            raise CrossAnalysisReviewError(f"SECTION_ITEMS_INVALID:{section_id}")
        local_ids: list[str] = []
        for item_index, item in enumerate(items):
            if not isinstance(item, Mapping):
                raise CrossAnalysisReviewError(f"SECTION_ITEM_INVALID:{section_id}:{item_index}")
            item_id = _safe_text(item.get("item_id"), "ITEM_ID")
            if item_id in seen_item_ids:
                raise CrossAnalysisReviewError(f"ITEM_ID_DUPLICATE:{item_id}")
            seen_item_ids.add(item_id)
            source_ids = _evidence_ids(
                item.get("evidence_refs"),
                snippets,
                label=f"SECTION_ITEM:{item_id}",
            )
            claim_id = f"section_item:{item_id}"
            local_ids.append(claim_id)
            item_claim_ids.append(claim_id)
            claims.append(
                _claim(
                    claim_id=claim_id,
                    target_path=f"/sections/{section_index}/items/{item_index}/statement",
                    claim_type="section_item_statement",
                    criticality="required" if item_index == 0 else "optional",
                    value=_safe_text(item.get("statement"), f"ITEM_STATEMENT:{item_id}"),
                    allowed_evidence_source_ids=source_ids,
                    context={
                        "section_id": section_id,
                        "paper_ids": copy.deepcopy(item.get("paper_ids")),
                        "qualifiers": copy.deepcopy(item.get("qualifiers")),
                    },
                    deletion_path=(
                        f"/sections/{section_index}/items/{item_index}"
                        if item_index > 0
                        else None
                    ),
                )
            )
        summary_id = f"section_summary:{section_id}"
        section_summary_ids[section_id] = summary_id
        claims.append(
            _claim(
                claim_id=summary_id,
                target_path=f"/sections/{section_index}/summary",
                claim_type="section_summary",
                criticality="required",
                value=_safe_text(section.get("summary"), f"SECTION_SUMMARY:{section_id}"),
                allowed_evidence_source_ids=(),
                dependent_claim_ids=local_ids,
                context={"section_id": section_id},
            )
        )

    trajectory_claim_ids: list[str] = []
    trajectory = value.get("paper_trajectory")
    if not isinstance(trajectory, list) or not trajectory:
        raise CrossAnalysisReviewError("PAPER_TRAJECTORY_INVALID")
    for index, stage in enumerate(trajectory):
        if not isinstance(stage, Mapping):
            raise CrossAnalysisReviewError(f"PAPER_TRAJECTORY_ITEM_INVALID:{index}")
        paper_id = _safe_text(stage.get("paper_id"), f"TRAJECTORY_PAPER_ID:{index}")
        source_ids = _evidence_ids(
            stage.get("evidence_refs"),
            snippets,
            label=f"TRAJECTORY:{paper_id}",
        )
        role_id = f"trajectory_role:{paper_id}"
        transition_id = f"trajectory_transition:{paper_id}"
        trajectory_claim_ids.extend((role_id, transition_id))
        claims.extend(
            (
                _claim(
                    claim_id=role_id,
                    target_path=f"/paper_trajectory/{index}/role",
                    claim_type="trajectory_role",
                    criticality="required",
                    value=_safe_text(stage.get("role"), f"TRAJECTORY_ROLE:{paper_id}"),
                    allowed_evidence_source_ids=source_ids,
                    context={"paper_id": paper_id, "sequence": stage.get("sequence")},
                ),
                _claim(
                    claim_id=transition_id,
                    target_path=f"/paper_trajectory/{index}/transition_from_prior",
                    claim_type="trajectory_transition",
                    criticality="required",
                    value=_safe_text(
                        stage.get("transition_from_prior"),
                        f"TRAJECTORY_TRANSITION:{paper_id}",
                    ),
                    allowed_evidence_source_ids=source_ids,
                    context={"paper_id": paper_id, "sequence": stage.get("sequence")},
                ),
            )
        )

    claims.insert(
        0,
        _claim(
            claim_id="executive_answer",
            target_path="/executive_answer",
            claim_type="executive_answer",
            criticality="required",
            value=value["executive_answer"],
            allowed_evidence_source_ids=(),
            dependent_claim_ids=[*section_summary_ids.values(), *trajectory_claim_ids],
            context={"primary_question": value["primary_question"]},
        ),
    )
    cross_checks = [
        {
            "check_id": "question_alignment",
            "target_claim_id": "executive_answer",
            "instruction": "The executive answer must answer the frozen primary question without changing its scope.",
        },
        {
            "check_id": "cross_paper_logic",
            "target_claim_id": "executive_answer",
            "instruction": "Cross-paper synthesis must follow the reviewed section and trajectory claims, not invent a new comparison.",
        },
        {
            "check_id": "uncertainty_boundary",
            "target_claim_id": section_summary_ids["limitations_and_uncertainties"],
            "instruction": "Limitations, non-comparability and uncertainty must remain explicit.",
        },
        {
            "check_id": "source_version_fidelity",
            "target_claim_id": section_summary_ids["scope_and_evidence_boundary"],
            "instruction": "The analysis must respect the frozen paper versions and Card-only boundary.",
        },
    ]
    used_ids = {
        source_id
        for claim in claims
        for source_id in claim["allowed_evidence_source_ids"]
    }
    return {
        "schema_version": "pr-os.p03.t08.cross-analysis-review-view.v1",
        "artifact_id": value.get("artifact_id"),
        "artifact_sha256": _hash(value),
        "primary_question": value["primary_question"],
        "producer_binding": {
            "provider": value["execution"]["provider"],
            "model": value["execution"]["analysis_model"],
            "call_id": value["execution"].get("call_id"),
        },
        "claims": claims,
        "required_claim_manifest": [claim["claim_id"] for claim in claims],
        "cross_checks": cross_checks,
        "evidence_snippets": [
            {
                "source_id": source_id,
                "paper_id": snippets[source_id].paper_id,
                "artifact_type": snippets[source_id].artifact_type,
                "artifact_sha256": snippets[source_id].artifact_sha256,
                "text": snippets[source_id].text,
            }
            for source_id in sorted(used_ids)
        ],
        "review_boundary": {
            "whole_analysis_rewrite": False,
            "identity_fields_model_editable": False,
            "full_review_count_max": 1,
            "repair_round_count_max": 1,
            "delta_review_count_max": 1,
        },
    }


def _render_prompt(module: str, replacements: Mapping[str, Any]) -> str:
    try:
        body = _PROMPTS.get(module).body
    except (KeyError, OSError) as exc:
        raise CrossAnalysisReviewError(f"PROMPT_UNAVAILABLE:{module}") from exc
    rendered = body
    for marker, value in replacements.items():
        if rendered.count(marker) != 1:
            raise CrossAnalysisReviewError(f"PROMPT_MARKER_INVALID:{module}:{marker}")
        rendered = rendered.replace(
            marker,
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )
    if "__" in rendered:
        raise CrossAnalysisReviewError(f"PROMPT_UNRESOLVED_MARKER:{module}")
    return rendered


def _parse_json_object(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, str):
        raise CrossAnalysisReviewError("MODEL_OUTPUT_TEXT_MISSING")
    candidate = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, flags=re.I | re.S)
    if fenced:
        candidate = fenced.group(1)
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise CrossAnalysisReviewError("MODEL_OUTPUT_INVALID_JSON") from exc
    if not isinstance(value, dict):
        raise CrossAnalysisReviewError("MODEL_OUTPUT_NOT_OBJECT")
    return value


def _invoke_json(
    payload: dict[str, Any],
    *,
    model_call: ModelCall,
    expected_provider: str,
    expected_model: str,
) -> dict[str, Any]:
    response = model_call(payload)
    if not isinstance(response, Mapping):
        raise CrossAnalysisReviewError("MODEL_RESPONSE_NOT_OBJECT")
    if response.get("provider") != expected_provider:
        raise CrossAnalysisReviewError("RETURNED_PROVIDER_MISMATCH")
    if response.get("model") != expected_model:
        raise CrossAnalysisReviewError("RETURNED_MODEL_MISMATCH")
    if response.get("stop_reason") not in _SUCCESS_STOPS:
        raise CrossAnalysisReviewError("MODEL_STOP_REASON_NOT_COMPLETE")
    transport = response.get("transport")
    if not isinstance(transport, Mapping) or transport.get("complete") is not True:
        raise CrossAnalysisReviewError("MODEL_TRANSPORT_INCOMPLETE")
    if transport.get("stream") is not True:
        raise CrossAnalysisReviewError("MODEL_STREAM_MODE_MISMATCH")
    if not isinstance(response.get("usage"), Mapping):
        raise CrossAnalysisReviewError("MODEL_USAGE_MISSING")
    return _parse_json_object(response.get("text"))


def _review_payload(
    view: dict[str, Any],
    *,
    mode: str,
    profile: ReviewProfile,
) -> dict[str, Any]:
    if mode == "FULL":
        module = "cross_analysis_review_full"
        contract_key = "view"
        schema = FULL_REVIEW_SCHEMA
        max_tokens = profile.full_max_tokens
        marker = "__CROSS_ANALYSIS_REVIEW_VIEW_JSON__"
    else:
        module = "cross_analysis_review_delta"
        contract_key = "delta_view"
        schema = DELTA_REVIEW_SCHEMA
        max_tokens = profile.delta_max_tokens
        marker = "__CROSS_ANALYSIS_DELTA_VIEW_JSON__"
    prompt = _render_prompt(
        module,
        {
            marker: view,
            "__CROSS_ANALYSIS_REVIEW_SCHEMA_JSON__": schema,
        },
    )
    payload: dict[str, Any] = {
        "messages": [{"role": "user", "content": prompt}],
        "thinking": copy.deepcopy(dict(profile.thinking)),
        "max_tokens": max_tokens,
        "stream": True,
        "timeout": profile.timeout_seconds,
        "wall_clock_deadline_seconds": profile.timeout_seconds,
        "pr_os_contract": {
            "mode": mode,
            contract_key: copy.deepcopy(view),
            "schema": copy.deepcopy(schema),
            "review_profile": {
                "slot": profile.slot,
                "provider": profile.provider,
                "model": profile.model,
                "thinking": copy.deepcopy(dict(profile.thinking)),
                "effort": profile.effort,
                "max_tokens": max_tokens,
            },
            "prompt_sha256": _hash_text(prompt),
        },
    }
    if profile.provider == "anthropic":
        if profile.effort not in {"low", "medium", "high", "xhigh", "max"}:
            raise CrossAnalysisReviewError("ANTHROPIC_REVIEW_EFFORT_INVALID")
        payload["effort"] = profile.effort
        # The complete audit schema can exceed Anthropic's structured-output
        # grammar envelope. Keep native effort, but use the proven P01 pattern:
        # schema in the prompt plus strict local validation of the response.
        payload["output_config"] = {"effort": profile.effort}
        payload["pr_os_contract"]["structured_output_mode"] = (
            "PROMPT_JSON_PLUS_LOCAL_SCHEMA"
        )
    elif profile.provider == "deepseek":
        payload["response_format"] = {"type": "json_object"}
        if profile.effort is not None:
            payload["reasoning_effort"] = profile.effort
    else:
        raise CrossAnalysisReviewError("REVIEW_PROVIDER_UNSUPPORTED")
    return payload


def _repair_payload(
    bundle: dict[str, Any], *, max_tokens: int = REPAIR_MAX_TOKENS
) -> dict[str, Any]:
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens <= 0:
        raise CrossAnalysisReviewError("REPAIR_MAX_TOKENS_INVALID")
    producer = bundle["producer_binding"]
    prompt = _render_prompt(
        "cross_analysis_targeted_repair",
        {
            "__CROSS_ANALYSIS_REPAIR_BUNDLE_JSON__": bundle,
            "__CROSS_ANALYSIS_REPAIR_SCHEMA_JSON__": REPAIR_SCHEMA,
        },
    )
    payload: dict[str, Any] = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "stream": True,
        "timeout": 900,
        "wall_clock_deadline_seconds": 900,
        "pr_os_contract": {
            "mode": "TARGETED_REPAIR",
            "repair_bundle": copy.deepcopy(bundle),
            "schema": copy.deepcopy(REPAIR_SCHEMA),
            "prompt_sha256": _hash_text(prompt),
        },
    }
    if producer["provider"] == "anthropic":
        payload.update({"thinking": {"type": "adaptive"}, "effort": "low"})
        payload["output_config"] = {"effort": "low"}
        payload["pr_os_contract"]["structured_output_mode"] = (
            "PROMPT_JSON_PLUS_LOCAL_SCHEMA"
        )
    elif producer["provider"] == "deepseek":
        payload.update(
            {
                "thinking": {"type": "enabled"},
                "reasoning_effort": "high",
                "response_format": {"type": "json_object"},
            }
        )
    else:
        raise CrossAnalysisReviewError("PRODUCER_PROVIDER_UNSUPPORTED")
    return payload


def _validate_schema(value: Any, schema: Mapping[str, Any], code: str) -> dict[str, Any]:
    try:
        jsonschema.Draft202012Validator(dict(schema)).validate(value)
    except jsonschema.ValidationError as exc:
        raise CrossAnalysisReviewError(code) from exc
    return dict(value)


def _validate_full_review(raw: Any, view: dict[str, Any]) -> dict[str, Any]:
    value = _validate_schema(raw, FULL_REVIEW_SCHEMA, "FULL_REVIEW_SCHEMA_INVALID")
    if value["artifact_sha256"] != view["artifact_sha256"]:
        raise CrossAnalysisReviewError("FULL_REVIEW_ARTIFACT_BINDING_MISMATCH")
    claims = {claim["claim_id"]: claim for claim in view["claims"]}
    seen: set[str] = set()
    for row in value["claim_reviews"]:
        claim_id = row["claim_id"]
        if claim_id not in claims or claim_id in seen:
            raise CrossAnalysisReviewError("FULL_REVIEW_CLAIM_COVERAGE_INVALID")
        seen.add(claim_id)
        if not set(row["evidence_source_ids"]).issubset(
            claims[claim_id]["allowed_evidence_source_ids"]
        ):
            raise CrossAnalysisReviewError("FULL_REVIEW_EVIDENCE_REF_INVALID")
        # A positive row's prose is non-semantic.  Some JSON-object providers
        # add a benign audit explanation despite the prompt requiring an empty
        # string; canonicalize it instead of spending another model call.  A
        # non-null severity remains invalid and cannot be normalized away.
        if row["status"] == "SUPPORTED" and row["severity"] is None:
            row["reason"] = ""
        failed = row["status"] != "SUPPORTED"
        if failed != bool(row["reason"]) or failed != (row["severity"] is not None):
            raise CrossAnalysisReviewError("FULL_REVIEW_FINDING_SHAPE_INVALID")
    if seen != set(claims):
        raise CrossAnalysisReviewError("FULL_REVIEW_CLAIM_COVERAGE_INVALID")
    checks = {item["check_id"]: item for item in view["cross_checks"]}
    seen_checks: set[str] = set()
    allowed_all = {
        item for claim in view["claims"] for item in claim["allowed_evidence_source_ids"]
    }
    for row in value["cross_checks"]:
        check_id = row["check_id"]
        if check_id not in checks or check_id in seen_checks:
            raise CrossAnalysisReviewError("FULL_REVIEW_CROSS_CHECK_COVERAGE_INVALID")
        seen_checks.add(check_id)
        if row["target_claim_id"] != checks[check_id]["target_claim_id"]:
            raise CrossAnalysisReviewError("FULL_REVIEW_CROSS_CHECK_TARGET_MISMATCH")
        if not set(row["evidence_source_ids"]).issubset(allowed_all):
            raise CrossAnalysisReviewError("FULL_REVIEW_EVIDENCE_REF_INVALID")
        failed = row["status"] != "PASS"
        if failed and not row["reason"]:
            raise CrossAnalysisReviewError("FULL_REVIEW_CROSS_CHECK_SHAPE_INVALID")
    if seen_checks != set(checks):
        raise CrossAnalysisReviewError("FULL_REVIEW_CROSS_CHECK_COVERAGE_INVALID")
    return value


def _findings(full: dict[str, Any], view: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    claims = {claim["claim_id"]: claim for claim in view["claims"]}
    result: list[dict[str, Any]] = []
    blocked = False
    for family, rows, ok_status, identity_key in (
        ("claim", full["claim_reviews"], "SUPPORTED", "claim_id"),
        ("cross_check", full["cross_checks"], "PASS", "check_id"),
    ):
        for row in rows:
            if row["status"] == ok_status:
                continue
            claim_id = row.get("claim_id") or row.get("target_claim_id")
            claim = claims[claim_id]
            blocked = blocked or row["status"] == "BLOCK"
            identity = row[identity_key]
            finding_id = "F-" + _hash(
                {
                    "artifact_sha256": view["artifact_sha256"],
                    "family": family,
                    "identity": identity,
                    "claim_id": claim_id,
                    "status": row["status"],
                    "reason": row["reason"],
                }
            )[:16]
            result.append(
                {
                    "finding_id": finding_id,
                    "finding_family": family,
                    "finding_identity": identity,
                    "claim_id": claim_id,
                    "target_path": claim["target_path"],
                    "criticality": claim["criticality"],
                    "severity": row.get("severity") or "major",
                    "status": row["status"],
                    "reason": row["reason"],
                    "evidence_source_ids": list(row["evidence_source_ids"]),
                }
            )
    result.sort(key=lambda item: (item["target_path"], item["finding_id"]))
    return result, blocked


def _tokens(path: str) -> list[str]:
    if not isinstance(path, str) or not path.startswith("/"):
        raise CrossAnalysisReviewError("TARGET_PATH_INVALID")
    return [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")]


def _get_pointer(value: Any, path: str) -> Any:
    current = value
    for part in _tokens(path):
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError) as exc:
                raise CrossAnalysisReviewError(f"TARGET_PATH_MISSING:{path}") from exc
        elif isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            raise CrossAnalysisReviewError(f"TARGET_PATH_MISSING:{path}")
    return current


def _set_pointer(value: Any, path: str, replacement: Any) -> None:
    parts = _tokens(path)
    parent = value
    for part in parts[:-1]:
        parent = parent[int(part)] if isinstance(parent, list) else parent[part]
    leaf = parts[-1]
    if isinstance(parent, list):
        parent[int(leaf)] = replacement
    else:
        parent[leaf] = replacement


def _delete_pointer(value: Any, path: str) -> None:
    parts = _tokens(path)
    parent = value
    for part in parts[:-1]:
        parent = parent[int(part)] if isinstance(parent, list) else parent[part]
    leaf = parts[-1]
    if not isinstance(parent, list):
        raise CrossAnalysisReviewError("DELETE_TARGET_NOT_LIST_ITEM")
    del parent[int(leaf)]


def _repair_bundle(
    analysis: dict[str, Any],
    view: dict[str, Any],
    findings: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    claims = {claim["claim_id"]: claim for claim in view["claims"]}
    snippets = {item["source_id"]: item for item in view["evidence_snippets"]}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for finding in findings:
        grouped.setdefault(finding["claim_id"], []).append(finding)
    targets = []
    for claim_id in sorted(grouped, key=lambda item: claims[item]["target_path"]):
        claim = claims[claim_id]
        rows = grouped[claim_id]
        dependent = {
            other["claim_id"]: other["value"]
            for other in view["claims"]
            if other["claim_id"] in claim["dependent_claim_ids"]
        }
        targets.append(
            {
                "target_id": "T-" + hashlib.sha256(claim_id.encode("utf-8")).hexdigest()[:16].upper(),
                "claim_id": claim_id,
                "target_path": claim["target_path"],
                "deletion_path": claim["deletion_path"],
                "criticality": claim["criticality"],
                "current_value": copy.deepcopy(_get_pointer(analysis, claim["target_path"])),
                "finding_ids": [row["finding_id"] for row in rows],
                "findings": copy.deepcopy(rows),
                "allowed_actions": [
                    "replace",
                    *(["delete"] if claim["criticality"] == "optional" else []),
                    "no_change",
                ],
                "candidate_evidence": [
                    copy.deepcopy(snippets[source_id])
                    for source_id in claim["allowed_evidence_source_ids"]
                ],
                "dependent_claims": dependent,
            }
        )
    return {
        "schema_version": "pr-os.p03.t08.cross-analysis-repair-bundle.v1",
        "source_artifact_sha256": view["artifact_sha256"],
        "producer_binding": copy.deepcopy(view["producer_binding"]),
        "repair_round": 1,
        "targets": targets,
        "protected_top_level": list(_PROTECTED_TOP_LEVEL),
        "forbidden_actions": [
            "whole analysis regeneration",
            "identity or execution metadata edit",
            "evidence reference edit",
            "new target or second repair round",
        ],
    }


def _validate_repairs(raw: Any, bundle: dict[str, Any]) -> dict[str, Any]:
    value = _validate_schema(raw, REPAIR_SCHEMA, "REPAIR_RESPONSE_SCHEMA_INVALID")
    if value["source_artifact_sha256"] != bundle["source_artifact_sha256"]:
        raise CrossAnalysisReviewError("REPAIR_SOURCE_BINDING_MISMATCH")
    expected = {target["target_id"]: target for target in bundle["targets"]}
    seen: set[str] = set()
    for row in value["repairs"]:
        target = expected.get(row["target_id"])
        if target is None or row["target_id"] in seen:
            raise CrossAnalysisReviewError("REPAIR_TARGET_COVERAGE_INVALID")
        seen.add(row["target_id"])
        if row["finding_ids"] != target["finding_ids"]:
            raise CrossAnalysisReviewError("REPAIR_FINDING_BINDING_MISMATCH")
        if row["action"] not in target["allowed_actions"]:
            raise CrossAnalysisReviewError("REPAIR_ACTION_NOT_ALLOWED")
        if row["action"] == "replace":
            _safe_text(row["replacement"], "REPAIR_REPLACEMENT")
        elif row["replacement"] is not None:
            raise CrossAnalysisReviewError("REPAIR_REPLACEMENT_MUST_BE_NULL")
    if seen != set(expected):
        raise CrossAnalysisReviewError("REPAIR_TARGET_COVERAGE_INVALID")
    return value


def _protected_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(value.get(key)) for key in _PROTECTED_TOP_LEVEL}


def _apply_repairs(
    analysis: dict[str, Any],
    bundle: dict[str, Any],
    response: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    targets = {item["target_id"]: item for item in bundle["targets"]}
    candidate = copy.deepcopy(analysis)
    changes: list[dict[str, Any]] = []
    delete_rows: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for row in response["repairs"]:
        target = targets[row["target_id"]]
        before = copy.deepcopy(_get_pointer(analysis, target["target_path"]))
        if row["action"] == "replace":
            _set_pointer(candidate, target["target_path"], row["replacement"])
            after = row["replacement"]
        elif row["action"] == "delete":
            if not target["deletion_path"]:
                raise CrossAnalysisReviewError("DELETE_TARGET_NOT_OPTIONAL_ITEM")
            delete_rows.append((target["deletion_path"], target, row))
            after = None
        else:
            after = before
        changes.append(
            {
                "target_id": target["target_id"],
                "claim_id": target["claim_id"],
                "target_path": target["target_path"],
                "deletion_path": target["deletion_path"],
                "criticality": target["criticality"],
                "finding_ids": target["finding_ids"],
                "allowed_evidence_source_ids": [
                    item["source_id"] for item in target["candidate_evidence"]
                ],
                "action": row["action"],
                "before": before,
                "after": after,
                "reason": row["reason"],
            }
        )
    for path, _target, _row in sorted(delete_rows, key=lambda item: item[0], reverse=True):
        _delete_pointer(candidate, path)
    if _protected_projection(candidate) != _protected_projection(analysis):
        raise CrossAnalysisReviewError("REPAIR_PROTECTED_FIELD_CHANGED")
    return candidate, changes


def _delta_view(
    source: dict[str, Any],
    candidate: dict[str, Any],
    bundle: dict[str, Any],
    changes: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    target_by_id = {item["target_id"]: item for item in bundle["targets"]}
    return {
        "schema_version": "pr-os.p03.t08.cross-analysis-delta-view.v1",
        "whole_analysis_pass_forbidden": True,
        "source_artifact_sha256": bundle["source_artifact_sha256"],
        "candidate_artifact_sha256": _hash(candidate),
        "targets": [
            {
                **copy.deepcopy(change),
                "candidate_evidence": copy.deepcopy(
                    target_by_id[change["target_id"]]["candidate_evidence"]
                ),
                "dependent_claims": copy.deepcopy(
                    target_by_id[change["target_id"]]["dependent_claims"]
                ),
            }
            for change in changes
        ],
        "protected_projection_sha256": _hash(_protected_projection(source)),
    }


def _validate_delta(raw: Any, delta: dict[str, Any]) -> dict[str, Any]:
    value = _validate_schema(raw, DELTA_REVIEW_SCHEMA, "DELTA_REVIEW_SCHEMA_INVALID")
    if (
        value["source_artifact_sha256"] != delta["source_artifact_sha256"]
        or value["candidate_artifact_sha256"] != delta["candidate_artifact_sha256"]
    ):
        raise CrossAnalysisReviewError("DELTA_ARTIFACT_BINDING_MISMATCH")
    expected = {item["target_id"]: item for item in delta["targets"]}
    seen: set[str] = set()
    for row in value["target_reviews"]:
        target = expected.get(row["target_id"])
        if target is None or row["target_id"] in seen:
            raise CrossAnalysisReviewError("DELTA_TARGET_COVERAGE_INVALID")
        seen.add(row["target_id"])
        if not set(row["evidence_source_ids"]).issubset(
            target["allowed_evidence_source_ids"]
        ):
            raise CrossAnalysisReviewError("DELTA_EVIDENCE_REF_INVALID")
        if (row["status"] == "RESOLVED") == bool(row["reason"]):
            raise CrossAnalysisReviewError("DELTA_REVIEW_SHAPE_INVALID")
    if seen != set(expected):
        raise CrossAnalysisReviewError("DELTA_TARGET_COVERAGE_INVALID")
    return value


def _tombstone(change: Mapping[str, Any], source_sha256: str, disposition: str) -> dict[str, Any]:
    identity = {
        "source_artifact_sha256": source_sha256,
        "claim_id": change["claim_id"],
        "target_path": change["target_path"],
        "deleted_path": change.get("deletion_path"),
        "finding_ids": change["finding_ids"],
        "disposition": disposition,
    }
    return {
        "schema_version": TOMBSTONE_VERSION,
        "tombstone_id": "TS-" + _hash(identity)[:20],
        **identity,
        "deleted_value_sha256": _hash(change["before"]),
        "reason_code": disposition,
    }


def _finalize(
    candidate: dict[str, Any],
    delta_view: dict[str, Any],
    delta_review: dict[str, Any],
) -> tuple[str, dict[str, Any] | None, tuple[dict[str, Any], ...], tuple[str, ...]]:
    reviews = {item["target_id"]: item for item in delta_review["target_reviews"]}
    required_unresolved = [
        target
        for target in delta_view["targets"]
        if target["criticality"] == "required"
        and reviews[target["target_id"]]["status"] != "RESOLVED"
    ]
    if required_unresolved:
        return BLOCKED, None, (), ("REQUIRED_CLAIM_UNRESOLVED_AFTER_DELTA",)

    final = copy.deepcopy(candidate)
    to_delete: list[dict[str, Any]] = []
    dispositions: dict[str, str] = {}
    for target in delta_view["targets"]:
        review = reviews[target["target_id"]]
        if target["action"] == "delete":
            dispositions[target["target_id"]] = "PRODUCER_DELETE_DELTA_RESOLVED"
            continue
        if review["status"] != "RESOLVED":
            to_delete.append(target)
            dispositions[target["target_id"]] = "OPTIONAL_UNRESOLVED_AFTER_DELTA"
    for target in sorted(
        to_delete,
        key=lambda item: item.get("deletion_path") or item["target_path"],
        reverse=True,
    ):
        deletion_path = target.get("deletion_path")
        if not deletion_path:
            raise CrossAnalysisReviewError("DELETE_TARGET_NOT_OPTIONAL_ITEM")
        _delete_pointer(final, deletion_path)
    omitted = [
        target
        for target in delta_view["targets"]
        if target["target_id"] in dispositions
    ]
    tombstones = tuple(
        _tombstone(
            target,
            delta_view["source_artifact_sha256"],
            dispositions[target["target_id"]],
        )
        for target in omitted
    )
    status = PASS_WITH_OMISSIONS if tombstones else PASS
    reasons = ("OPTIONAL_CLAIMS_TOMBSTONED",) if tombstones else ("DELTA_REVIEW_RESOLVED",)
    return status, final, tombstones, reasons


def _validate_candidate_schema(
    value: dict[str, Any], output_schema: Mapping[str, Any] | None
) -> None:
    _validate_analysis_identity(value, expected_primary_question=value["primary_question"])
    if output_schema is None:
        return
    try:
        jsonschema.Draft202012Validator(dict(output_schema)).validate(value)
    except jsonschema.ValidationError as exc:
        raise CrossAnalysisReviewError("FINAL_ARTIFACT_SCHEMA_INVALID") from exc


def run_cross_analysis_review_closure(
    *,
    analysis: Mapping[str, Any],
    evidence_snippets: Sequence[FrozenEvidenceSnippet],
    expected_primary_question: str,
    review_call: ModelCall | None = None,
    repair_call: ModelCall | None = None,
    output_schema: Mapping[str, Any] | None = None,
    review_profile: ReviewProfile = DEFAULT_REVIEW_PROFILE,
    repair_max_tokens: int = REPAIR_MAX_TOKENS,
) -> CrossAnalysisClosureResult:
    """Execute one bounded Full/repair/Delta closure with no retry or fallback."""

    view = build_cross_analysis_review_view(
        analysis,
        evidence_snippets,
        expected_primary_question=expected_primary_question,
    )
    if review_call is None:
        raise CrossAnalysisReviewError("STRICT_REVIEW_CALL_BINDING_REQUIRED")
    source = copy.deepcopy(dict(analysis))
    full_raw = _invoke_json(
        _review_payload(view, mode="FULL", profile=review_profile),
        model_call=review_call,
        expected_provider=review_profile.provider,
        expected_model=review_profile.model,
    )
    full = _validate_full_review(full_raw, view)
    findings, reviewer_block = _findings(full, view)
    if reviewer_block:
        return CrossAnalysisClosureResult(
            status=BLOCKED,
            source_artifact_sha256=view["artifact_sha256"],
            final_artifact_sha256=None,
            final_artifact=None,
            full_review=full,
            repair_bundle=None,
            repair_response=None,
            delta_view=None,
            delta_review=None,
            tombstones=(),
            review_call_count=1,
            repair_call_count=0,
            reason_codes=("FULL_REVIEW_IRREDUCIBLE_BLOCK",),
        )
    if not findings:
        _validate_candidate_schema(source, output_schema)
        return CrossAnalysisClosureResult(
            status=PASS,
            source_artifact_sha256=view["artifact_sha256"],
            final_artifact_sha256=view["artifact_sha256"],
            final_artifact=source,
            full_review=full,
            repair_bundle=None,
            repair_response=None,
            delta_view=None,
            delta_review=None,
            tombstones=(),
            review_call_count=1,
            repair_call_count=0,
            reason_codes=("FULL_REVIEW_PASS",),
        )
    if repair_call is None:
        raise CrossAnalysisReviewError("STRICT_REPAIR_CALL_BINDING_REQUIRED")
    bundle = _repair_bundle(source, view, findings)
    producer = bundle["producer_binding"]
    repair_raw = _invoke_json(
        _repair_payload(bundle, max_tokens=repair_max_tokens),
        model_call=repair_call,
        expected_provider=producer["provider"],
        expected_model=producer["model"],
    )
    repair = _validate_repairs(repair_raw, bundle)
    candidate, changes = _apply_repairs(source, bundle, repair)
    delta = _delta_view(source, candidate, bundle, changes)
    delta_raw = _invoke_json(
        _review_payload(delta, mode="DELTA", profile=review_profile),
        model_call=review_call,
        expected_provider=review_profile.provider,
        expected_model=review_profile.model,
    )
    delta_review = _validate_delta(delta_raw, delta)
    status, final, tombstones, reasons = _finalize(candidate, delta, delta_review)
    if final is not None:
        _validate_candidate_schema(final, output_schema)
    return CrossAnalysisClosureResult(
        status=status,
        source_artifact_sha256=view["artifact_sha256"],
        final_artifact_sha256=_hash(final) if final is not None else None,
        final_artifact=final,
        full_review=full,
        repair_bundle=bundle,
        repair_response=repair,
        delta_view=delta,
        delta_review=delta_review,
        tombstones=tombstones,
        review_call_count=2,
        repair_call_count=1,
        reason_codes=reasons,
    )


_REVIEW_ROW = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "claim_id", "status", "severity", "evidence_source_ids", "reason"
    ],
    "properties": {
        "claim_id": {"type": "string", "minLength": 1},
        "status": {"enum": ["SUPPORTED", "REPAIR_REQUIRED", "BLOCK"]},
        "severity": {"type": ["string", "null"], "enum": ["minor", "major", None]},
        "evidence_source_ids": {
            "type": "array", "uniqueItems": True, "items": {"type": "string"}
        },
        "reason": {"type": "string", "maxLength": 600},
    },
}

FULL_REVIEW_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version", "review_mode", "artifact_sha256", "claim_reviews", "cross_checks"
    ],
    "properties": {
        "schema_version": {"const": FULL_REVIEW_VERSION},
        "review_mode": {"const": "FULL"},
        "artifact_sha256": {"type": "string", "pattern": "^[A-F0-9]{64}$"},
        "claim_reviews": {"type": "array", "minItems": 1, "items": _REVIEW_ROW},
        "cross_checks": {
            "type": "array",
            "minItems": 4,
            "maxItems": 4,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "check_id", "status", "target_claim_id", "evidence_source_ids", "reason"
                ],
                "properties": {
                    "check_id": {"type": "string"},
                    "status": {"enum": ["PASS", "REPAIR_REQUIRED", "BLOCK"]},
                    "target_claim_id": {"type": "string"},
                    "evidence_source_ids": {
                        "type": "array", "uniqueItems": True, "items": {"type": "string"}
                    },
                    "reason": {"type": "string", "maxLength": 600},
                },
            },
        },
    },
}

REPAIR_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "source_artifact_sha256", "repairs"],
    "properties": {
        "schema_version": {"const": REPAIR_VERSION},
        "source_artifact_sha256": {"type": "string", "pattern": "^[A-F0-9]{64}$"},
        "repairs": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "target_id", "finding_ids", "action", "replacement", "reason"
                ],
                "properties": {
                    "target_id": {"type": "string", "pattern": "^T-[A-F0-9]{16}$"},
                    "finding_ids": {
                        "type": "array", "minItems": 1, "uniqueItems": True,
                        "items": {"type": "string", "pattern": "^F-[A-F0-9]{16}$"},
                    },
                    "action": {"enum": ["replace", "delete", "no_change"]},
                    "replacement": {"type": ["string", "null"], "maxLength": 2400},
                    "reason": {"type": "string", "minLength": 1, "maxLength": 300},
                },
            },
        },
    },
}

DELTA_REVIEW_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version", "review_mode", "source_artifact_sha256",
        "candidate_artifact_sha256", "target_reviews"
    ],
    "properties": {
        "schema_version": {"const": DELTA_REVIEW_VERSION},
        "review_mode": {"const": "DELTA"},
        "source_artifact_sha256": {"type": "string", "pattern": "^[A-F0-9]{64}$"},
        "candidate_artifact_sha256": {"type": "string", "pattern": "^[A-F0-9]{64}$"},
        "target_reviews": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["target_id", "status", "evidence_source_ids", "reason"],
                "properties": {
                    "target_id": {"type": "string", "pattern": "^T-[A-F0-9]{16}$"},
                    "status": {"enum": ["RESOLVED", "UNRESOLVED", "FALSE_RESOLUTION"]},
                    "evidence_source_ids": {
                        "type": "array", "uniqueItems": True, "items": {"type": "string"}
                    },
                    "reason": {"type": "string", "maxLength": 600},
                },
            },
        },
    },
}


__all__ = [
    "PASS", "PASS_WITH_OMISSIONS", "BLOCKED",
    "CrossAnalysisReviewError", "FrozenEvidenceSnippet", "ReviewProfile",
    "DEFAULT_REVIEW_PROFILE",
    "CrossAnalysisClosureResult", "build_cross_analysis_review_view",
    "run_cross_analysis_review_closure", "FULL_REVIEW_SCHEMA",
    "REPAIR_SCHEMA", "DELTA_REVIEW_SCHEMA",
]
