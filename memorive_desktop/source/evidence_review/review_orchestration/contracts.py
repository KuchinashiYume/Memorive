from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence


SHA256_RE = re.compile(r"^[0-9A-F]{64}$")
DECISIONS = {"MUST_REVIEW", "SAMPLED_REVIEW", "NOT_SAMPLED", "POLICY_EXEMPT", "INVALID_EVENT"}
JOB_STATES = {"PENDING", "RUNNING", "PAUSED", "BLOCKED", "COMPLETED", "FAILED", "ABORTED"}
REVIEW_VERDICTS = {"PASS", "PASS_WITH_OMISSIONS", "BLOCKED", "NOT_ASSESSED"}
OWNERSHIP_VALUES = {"self", "entrusted", "unknown", "mixed"}
TRIGGER_POLICY_FIELDS = {
    "schema_version",
    "policy_id",
    "revision",
    "effective_epoch_id",
    "must_review_rules",
    "explicit_exempt_rules",
    "sample_strata",
    "sampling_seed",
    "sampling_seed_sha256",
    "cutoff_at",
    "max_dispatch_attempts",
    "provider_retry_ceiling",
    "stage_topology",
    "review_contract_sha256",
    "stage_topology_sha256",
    "ownership_policy_sha256",
    "route_decision_separate_from_trigger",
    "default_action",
    "default_activation",
    "content_hash",
}
JOB_SPEC_FIELDS = {
    "schema_version",
    "job_id",
    "review_job_key",
    "analysis_subject_key",
    "analysis_artifact_ref",
    "analysis_artifact_sha256",
    "trigger_decision_ref",
    "policy_ref",
    "review_contract_sha256",
    "stage_topology_sha256",
    "ownership_policy_sha256",
    "ownership_snapshot_ref",
    "ownership_snapshot_sha256",
    "ownership_value",
    "producer_role_ref",
    "reviewer_role_ref",
    "max_dispatch_attempts",
    "provider_retry_ceiling",
    "stage_semantic_call_ceilings",
    "workspace_ref",
    "input_ref",
    "output_ref",
    "predecessor_job_ref",
    "default_activation",
    "content_hash",
}
JOB_EVENT_FIELDS = {
    "schema_version",
    "event_id",
    "job_id",
    "event_ordinal",
    "event_type",
    "occurred_at",
    "actor_kind",
    "actor_ref",
    "expected_previous_head_hash",
    "attempt_id",
    "predecessor_attempt_ref",
    "reason_code",
    "evidence_refs",
    "payload_hash",
    "dedupe_key",
    "request_fingerprint",
    "transition_guard",
    "review_verdict",
    "event_hash",
}
JOB_EVENT_TYPES = {
    "JOB_CREATED",
    "RUN_CLAIMED",
    "PAUSE_REQUESTED",
    "CANCEL_REQUESTED",
    "RUN_PAUSED",
    "RUN_RESUMED",
    "RUN_REQUEUED",
    "RUN_BLOCKED",
    "RUN_COMPLETED",
    "RUN_FAILED",
    "RUN_ABORTED",
}


class ReviewOrchestrationContractError(ValueError):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}" if detail else code)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest().upper()


def require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewOrchestrationContractError("REQUIRED_TEXT_INVALID", field)
    return value.strip()


def require_hash(value: Any, field: str) -> str:
    text = require_text(value, field).upper()
    if not SHA256_RE.fullmatch(text):
        raise ReviewOrchestrationContractError("SHA256_INVALID", field)
    return text


def _exact_content_hash(value: Mapping[str, Any], field: str = "content_hash") -> None:
    supplied = require_hash(value.get(field), field)
    body = {key: item for key, item in value.items() if key != field}
    if canonical_hash(body) != supplied:
        raise ReviewOrchestrationContractError("CONTENT_HASH_MISMATCH", field)


def _exact_fields(value: Mapping[str, Any], expected: set[str], code: str) -> None:
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        raise ReviewOrchestrationContractError(code, f"missing={missing};extra={extra}")


def _timestamp(value: Any, field: str) -> str:
    text = require_text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReviewOrchestrationContractError("TIMESTAMP_INVALID", field) from exc
    if parsed.tzinfo is None:
        raise ReviewOrchestrationContractError("TIMESTAMP_TIMEZONE_REQUIRED", field)
    return text


def validate_trigger_policy(policy: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(policy)
    _exact_fields(value, TRIGGER_POLICY_FIELDS, "TRIGGER_POLICY_FIELD_SET_INVALID")
    if value.get("schema_version") != "ReviewOrchestrationReviewTriggerPolicy-v1":
        raise ReviewOrchestrationContractError("TRIGGER_POLICY_VERSION_UNSUPPORTED")
    require_text(value.get("policy_id"), "policy_id")
    require_text(value.get("effective_epoch_id"), "effective_epoch_id")
    _timestamp(value.get("cutoff_at"), "cutoff_at")
    revision = value.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ReviewOrchestrationContractError("POLICY_REVISION_INVALID")
    if value.get("default_action") != "BLOCK" or value.get("default_activation") != "DISABLED":
        raise ReviewOrchestrationContractError("POLICY_DEFAULT_MUST_FAIL_CLOSED")
    seed = require_text(value.get("sampling_seed"), "sampling_seed")
    if require_hash(value.get("sampling_seed_sha256"), "sampling_seed_sha256") != hashlib.sha256(seed.encode("utf-8")).hexdigest().upper():
        raise ReviewOrchestrationContractError("SAMPLING_SEED_HASH_MISMATCH")
    rule_ids: list[str] = []
    for group in ("must_review_rules", "explicit_exempt_rules"):
        rules = value.get(group)
        if not isinstance(rules, list):
            raise ReviewOrchestrationContractError("POLICY_RULE_LIST_INVALID", group)
        for rule in rules:
            if not isinstance(rule, dict):
                raise ReviewOrchestrationContractError("POLICY_RULE_INVALID", group)
            if set(rule) != {"rule_id", "field", "operator", "value"}:
                raise ReviewOrchestrationContractError("POLICY_RULE_FIELD_SET_INVALID")
            rule_ids.append(require_text(rule.get("rule_id"), "rule_id"))
            operator = rule.get("operator")
            if operator not in {"EQ", "GT", "IN", "INTERSECTS"}:
                raise ReviewOrchestrationContractError("POLICY_RULE_OPERATOR_INVALID")
            require_text(rule.get("field"), "field")
            expected = rule.get("value")
            if operator == "GT" and (
                isinstance(expected, bool) or not isinstance(expected, (int, float))
            ):
                raise ReviewOrchestrationContractError("POLICY_RULE_VALUE_INVALID", rule["rule_id"])
            if operator in {"IN", "INTERSECTS"} and (
                not isinstance(expected, list) or not expected
            ):
                raise ReviewOrchestrationContractError("POLICY_RULE_VALUE_INVALID", rule["rule_id"])
    strata = value.get("sample_strata")
    if not isinstance(strata, list) or not strata:
        raise ReviewOrchestrationContractError("SAMPLE_STRATA_REQUIRED")
    stratum_ids: list[str] = []
    for item in strata:
        if not isinstance(item, dict) or set(item) != {
            "stratum_id",
            "field",
            "operator",
            "value",
            "rate_bps",
        }:
            raise ReviewOrchestrationContractError("SAMPLE_STRATUM_FIELD_SET_INVALID")
        stratum_ids.append(require_text(item.get("stratum_id"), "stratum_id"))
        rate = item.get("rate_bps")
        if isinstance(rate, bool) or not isinstance(rate, int) or not 0 <= rate <= 10000:
            raise ReviewOrchestrationContractError("SAMPLE_RATE_BPS_INVALID")
        if item.get("operator") != "EQ" or item.get("field") != "stratum_id":
            raise ReviewOrchestrationContractError("SAMPLE_STRATUM_PREDICATE_INVALID")
    if len(rule_ids) != len(set(rule_ids)) or len(stratum_ids) != len(set(stratum_ids)):
        raise ReviewOrchestrationContractError("POLICY_IDENTIFIER_DUPLICATE")
    if value.get("max_dispatch_attempts") != 2 or value.get("provider_retry_ceiling") != 0:
        raise ReviewOrchestrationContractError("ATTEMPT_CEILING_INVALID")
    topology = value.get("stage_topology")
    if (
        not isinstance(topology, list)
        or any(not isinstance(item, dict) or set(item) != {"stage", "max_semantic_calls"} for item in topology)
        or [item.get("stage") for item in topology]
        != ["FULL_REVIEW", "TARGETED_REPAIR", "DELTA_REVIEW"]
    ):
        raise ReviewOrchestrationContractError("STAGE_TOPOLOGY_INVALID")
    if [item.get("max_semantic_calls") for item in topology] != [1, 1, 1]:
        raise ReviewOrchestrationContractError("STAGE_CALL_CEILING_INVALID")
    for field in ("review_contract_sha256", "stage_topology_sha256", "ownership_policy_sha256"):
        require_hash(value.get(field), field)
    if value["stage_topology_sha256"] != canonical_hash(topology):
        raise ReviewOrchestrationContractError("STAGE_TOPOLOGY_HASH_MISMATCH")
    if value.get("route_decision_separate_from_trigger") is not True:
        raise ReviewOrchestrationContractError("ROUTE_DECISION_SEPARATION_REQUIRED")
    _exact_content_hash(value)
    return value


def review_job_key(subject_key: str, policy: Mapping[str, Any]) -> str:
    return canonical_hash(
        {
            "analysis_subject_key": require_hash(subject_key, "subject_key"),
            "policy": f"{require_text(policy.get('policy_id'), 'policy_id')}@{policy.get('revision')}",
            "review_contract_sha256": require_hash(policy.get("review_contract_sha256"), "review_contract_sha256"),
            "stage_topology_sha256": require_hash(policy.get("stage_topology_sha256"), "stage_topology_sha256"),
            "ownership_policy_sha256": require_hash(policy.get("ownership_policy_sha256"), "ownership_policy_sha256"),
        }
    )


def build_review_job_spec(
    *,
    event: Mapping[str, Any],
    decision: Mapping[str, Any],
    policy: Mapping[str, Any],
    workspace_ref: str,
    input_ref: str,
    output_ref: str,
    trigger_decision_ref: str,
    producer_role_ref: str,
    reviewer_role_ref: str,
    predecessor_job_ref: str | None = None,
) -> dict[str, Any]:
    validate_trigger_policy(policy)
    if decision.get("decision") not in {"MUST_REVIEW", "SAMPLED_REVIEW"}:
        raise ReviewOrchestrationContractError("DECISION_NOT_JOB_ELIGIBLE")
    subject = require_hash(decision.get("subject_key"), "subject_key")
    key = review_job_key(subject, policy)
    if decision.get("review_job_key") != key:
        raise ReviewOrchestrationContractError("REVIEW_JOB_KEY_MISMATCH")
    body: dict[str, Any] = {
        "schema_version": "ReviewOrchestrationReviewJobSpec-v1",
        "job_id": "RJOB-" + key[:32],
        "review_job_key": key,
        "analysis_subject_key": subject,
        "analysis_artifact_ref": require_text(event.get("analysis_artifact_ref"), "analysis_artifact_ref"),
        "analysis_artifact_sha256": require_hash(event.get("analysis_artifact_sha256"), "analysis_artifact_sha256"),
        "trigger_decision_ref": require_text(trigger_decision_ref, "trigger_decision_ref"),
        "policy_ref": f"{policy['policy_id']}@{policy['revision']}",
        "review_contract_sha256": require_hash(policy.get("review_contract_sha256"), "review_contract_sha256"),
        "stage_topology_sha256": require_hash(policy.get("stage_topology_sha256"), "stage_topology_sha256"),
        "ownership_policy_sha256": require_hash(policy.get("ownership_policy_sha256"), "ownership_policy_sha256"),
        "ownership_snapshot_ref": require_text(event.get("ownership_snapshot_ref"), "ownership_snapshot_ref"),
        "ownership_snapshot_sha256": require_hash(event.get("ownership_snapshot_sha256"), "ownership_snapshot_sha256"),
        "ownership_value": require_text(event.get("ownership_value"), "ownership_value"),
        "producer_role_ref": require_text(producer_role_ref, "producer_role_ref"),
        "reviewer_role_ref": require_text(reviewer_role_ref, "reviewer_role_ref"),
        "max_dispatch_attempts": 2,
        "provider_retry_ceiling": 0,
        "stage_semantic_call_ceilings": {"FULL_REVIEW": 1, "TARGETED_REPAIR": 1, "DELTA_REVIEW": 1},
        "workspace_ref": require_text(workspace_ref, "workspace_ref"),
        "input_ref": require_text(input_ref, "input_ref"),
        "output_ref": require_text(output_ref, "output_ref"),
        "predecessor_job_ref": predecessor_job_ref,
        "default_activation": "DISABLED",
    }
    return {**body, "content_hash": canonical_hash(body)}


def validate_review_job_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(spec)
    _exact_fields(value, JOB_SPEC_FIELDS, "JOB_SPEC_FIELD_SET_INVALID")
    if value.get("schema_version") != "ReviewOrchestrationReviewJobSpec-v1":
        raise ReviewOrchestrationContractError("JOB_SPEC_VERSION_UNSUPPORTED")
    for field in ("review_job_key", "analysis_subject_key", "analysis_artifact_sha256", "review_contract_sha256", "stage_topology_sha256", "ownership_policy_sha256", "ownership_snapshot_sha256"):
        require_hash(value.get(field), field)
    for field in ("job_id", "analysis_artifact_ref", "trigger_decision_ref", "policy_ref", "ownership_snapshot_ref", "ownership_value", "producer_role_ref", "reviewer_role_ref", "workspace_ref", "input_ref", "output_ref"):
        require_text(value.get(field), field)
    if value["ownership_value"] not in OWNERSHIP_VALUES:
        raise ReviewOrchestrationContractError("OWNERSHIP_VALUE_INVALID")
    predecessor = value.get("predecessor_job_ref")
    if predecessor is not None:
        require_text(predecessor, "predecessor_job_ref")
    if value.get("stage_semantic_call_ceilings") != {
        "FULL_REVIEW": 1,
        "TARGETED_REPAIR": 1,
        "DELTA_REVIEW": 1,
    }:
        raise ReviewOrchestrationContractError("JOB_SPEC_STAGE_CEILING_INVALID")
    if value.get("default_activation") != "DISABLED" or value.get("provider_retry_ceiling") != 0 or value.get("max_dispatch_attempts") != 2:
        raise ReviewOrchestrationContractError("JOB_SPEC_CEILING_OR_ACTIVATION_INVALID")
    _exact_content_hash(value)
    return value


def validate_review_job_event(event: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(event)
    _exact_fields(value, JOB_EVENT_FIELDS, "JOB_EVENT_FIELD_SET_INVALID")
    if value.get("schema_version") != "ReviewOrchestrationReviewJobEvent-v1":
        raise ReviewOrchestrationContractError("JOB_EVENT_VERSION_UNSUPPORTED")
    for field in ("event_id", "job_id", "event_type", "occurred_at", "actor_kind", "actor_ref", "dedupe_key"):
        require_text(value.get(field), field)
    _timestamp(value.get("occurred_at"), "occurred_at")
    if value["event_type"] not in JOB_EVENT_TYPES:
        raise ReviewOrchestrationContractError("JOB_EVENT_TYPE_INVALID")
    if value["actor_kind"] not in {"SYSTEM", "OPERATOR", "WORKER", "RECOVERY"}:
        raise ReviewOrchestrationContractError("JOB_EVENT_ACTOR_KIND_INVALID")
    for nullable in ("attempt_id", "predecessor_attempt_ref", "transition_guard"):
        if value.get(nullable) is not None:
            require_text(value.get(nullable), nullable)
    if value.get("review_verdict") is not None and value.get("review_verdict") not in REVIEW_VERDICTS:
        raise ReviewOrchestrationContractError("REVIEW_VERDICT_INVALID")
    evidence_refs = value.get("evidence_refs")
    if (
        not isinstance(evidence_refs, list)
        or any(not isinstance(item, str) or not item for item in evidence_refs)
        or len(evidence_refs) != len(set(evidence_refs))
    ):
        raise ReviewOrchestrationContractError("EVIDENCE_REFS_INVALID")
    previous_head = value.get("expected_previous_head_hash")
    if previous_head != "GENESIS":
        require_hash(previous_head, "expected_previous_head_hash")
    for field in ("payload_hash", "request_fingerprint", "event_hash"):
        require_hash(value.get(field), field)
    ordinal = value.get("event_ordinal")
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1:
        raise ReviewOrchestrationContractError("EVENT_ORDINAL_INVALID")
    expected = require_hash(value.get("event_hash"), "event_hash")
    body = {key: item for key, item in value.items() if key != "event_hash"}
    if canonical_hash(body) != expected:
        raise ReviewOrchestrationContractError("JOB_EVENT_HASH_MISMATCH")
    return value


def _cost_string(value: Any) -> str:
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ReviewOrchestrationContractError("COST_DECIMAL_INVALID") from exc
    if amount < 0:
        raise ReviewOrchestrationContractError("COST_NEGATIVE")
    return format(amount, "f")


def build_analysis_review_receipt(
    *,
    job_spec: Mapping[str, Any],
    job_state: str,
    review_verdict: str,
    final_artifact_ref: str,
    final_artifact_sha256: str,
    attempt_refs: Sequence[str],
    finding_refs: Sequence[str],
    ownership_snapshot_ref: str,
    route_receipt_ref: str,
    model_qualification_ref: str,
    accounting_receipt_ref: str,
    cost_cny: Any,
    external_calls: int,
) -> dict[str, Any]:
    validate_review_job_spec(job_spec)
    if job_state not in {"COMPLETED", "FAILED", "ABORTED", "BLOCKED"}:
        raise ReviewOrchestrationContractError("RECEIPT_JOB_STATE_INVALID")
    if review_verdict not in REVIEW_VERDICTS:
        raise ReviewOrchestrationContractError("REVIEW_VERDICT_INVALID")
    if isinstance(external_calls, bool) or not isinstance(external_calls, int) or external_calls < 0:
        raise ReviewOrchestrationContractError("EXTERNAL_CALL_COUNT_INVALID")
    body = {
        "schema_version": "ReviewOrchestrationAnalysisReviewReceipt-v1",
        "job_id": job_spec["job_id"],
        "review_job_key": job_spec["review_job_key"],
        "job_state": job_state,
        "review_verdict": review_verdict,
        "source_artifact_ref": job_spec["analysis_artifact_ref"],
        "source_artifact_sha256": job_spec["analysis_artifact_sha256"],
        "final_artifact_ref": require_text(final_artifact_ref, "final_artifact_ref"),
        "final_artifact_sha256": require_hash(final_artifact_sha256, "final_artifact_sha256"),
        "immutable_predecessor_sha256": job_spec["analysis_artifact_sha256"],
        "attempt_refs": [require_text(item, "attempt_ref") for item in attempt_refs],
        "finding_refs": [require_text(item, "finding_ref") for item in finding_refs],
        "ownership_snapshot_ref": require_text(ownership_snapshot_ref, "ownership_snapshot_ref"),
        "route_receipt_ref": require_text(route_receipt_ref, "route_receipt_ref"),
        "model_qualification_ref": require_text(model_qualification_ref, "model_qualification_ref"),
        "accounting_receipt_ref": require_text(accounting_receipt_ref, "accounting_receipt_ref"),
        "cost_cny": _cost_string(cost_cny),
        "external_calls": external_calls,
        "knowledge_admission_mutation_count": 0,
        "card_mutation_count": 0,
        "production_analysis_mutation_count": 0,
    }
    if body["ownership_snapshot_ref"] != job_spec["ownership_snapshot_ref"]:
        raise ReviewOrchestrationContractError("OWNERSHIP_SNAPSHOT_REF_MISMATCH")
    if len(body["attempt_refs"]) != len(set(body["attempt_refs"])):
        raise ReviewOrchestrationContractError("ATTEMPT_REFS_DUPLICATE")
    if len(body["finding_refs"]) != len(set(body["finding_refs"])):
        raise ReviewOrchestrationContractError("FINDING_REFS_DUPLICATE")
    return {**body, "content_hash": canonical_hash(body)}


__all__ = [
    "DECISIONS",
    "JOB_STATES",
    "REVIEW_VERDICTS",
    "ReviewOrchestrationContractError",
    "build_analysis_review_receipt",
    "build_review_job_spec",
    "canonical_hash",
    "canonical_json_bytes",
    "require_hash",
    "review_job_key",
    "validate_review_job_event",
    "validate_review_job_spec",
    "validate_trigger_policy",
]
