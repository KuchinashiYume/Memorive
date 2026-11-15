from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from research_analysis.review_orchestration_events import (
    AnalysisCompletionEventError,
    canonical_hash,
    validate_analysis_completed_event,
)

from .contracts import (
    ReviewOrchestrationContractError,
    review_job_key,
    validate_trigger_policy,
)


def analysis_subject_key(event: Mapping[str, Any]) -> str:
    validated = validate_analysis_completed_event(event)
    return canonical_hash(
        {
            "analysis_artifact_ref": validated["analysis_artifact_ref"],
            "analysis_revision": validated["analysis_revision"],
            "analysis_artifact_sha256": validated["analysis_artifact_sha256"],
            "ownership_snapshot_sha256": validated["ownership_snapshot_sha256"],
        }
    )


def sampling_bucket(policy: Mapping[str, Any], stratum_id: str, subject_key: str) -> int:
    digest = hashlib.sha256(
        "\x1f".join(
            (
                str(policy["effective_epoch_id"]),
                str(stratum_id),
                str(policy["sampling_seed"]),
                str(subject_key),
            )
        ).encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big") % 10000


def _rule_matches(event: Mapping[str, Any], rule: Mapping[str, Any]) -> bool:
    actual = event.get(rule["field"])
    operator = rule["operator"]
    expected = rule.get("value")
    if operator == "EQ":
        return actual == expected
    if operator == "GT":
        return isinstance(actual, (int, float)) and not isinstance(actual, bool) and actual > expected
    if operator == "IN":
        return actual in expected
    if operator == "INTERSECTS":
        return isinstance(actual, list) and bool(set(actual) & set(expected))
    raise ReviewOrchestrationContractError("POLICY_RULE_OPERATOR_INVALID", str(operator))


def _invalid_decision(event: Mapping[str, Any], reason: str) -> dict[str, Any]:
    body = {
        "schema_version": "ReviewOrchestrationReviewTriggerDecision-v1",
        "subject_key": canonical_hash({"invalid_event": dict(event)}),
        "policy_ref": "UNBOUND",
        "epoch_id": "UNBOUND",
        "eligibility_status": "INVALID",
        "decision": "INVALID_EVENT",
        "matched_rule_ids": [],
        "stratum_id": None,
        "sample_bucket": None,
        "rate_bps": None,
        "review_job_key": None,
        "ownership_route_precheck": "NOT_APPLICABLE",
        "reason_codes": [reason],
        "input_hashes": {"event_hash": canonical_hash(dict(event))},
    }
    return {**body, "content_hash": canonical_hash(body)}


def evaluate_trigger(event: Mapping[str, Any], policy: Mapping[str, Any]) -> dict[str, Any]:
    policy_value = validate_trigger_policy(policy)
    try:
        event_value = validate_analysis_completed_event(event)
        subject = analysis_subject_key(event_value)
    except (AnalysisCompletionEventError, ReviewOrchestrationContractError) as exc:
        return _invalid_decision(event, getattr(exc, "code", "EVENT_INVALID"))

    matched_must = [item["rule_id"] for item in policy_value["must_review_rules"] if _rule_matches(event_value, item)]
    matched_exempt = [item["rule_id"] for item in policy_value["explicit_exempt_rules"] if _rule_matches(event_value, item)]
    stratum = next((item for item in policy_value["sample_strata"] if _rule_matches(event_value, item)), None)

    bucket: int | None = None
    rate: int | None = None
    matched: list[str]
    if matched_must:
        decision = "MUST_REVIEW"
        matched = matched_must
        if stratum:
            rate = stratum["rate_bps"]
            bucket = sampling_bucket(policy_value, stratum["stratum_id"], subject)
    elif matched_exempt:
        decision = "POLICY_EXEMPT"
        matched = matched_exempt
        if stratum:
            rate = stratum["rate_bps"]
            bucket = sampling_bucket(policy_value, stratum["stratum_id"], subject)
    elif stratum:
        rate = stratum["rate_bps"]
        bucket = sampling_bucket(policy_value, stratum["stratum_id"], subject)
        decision = "SAMPLED_REVIEW" if bucket < rate else "NOT_SAMPLED"
        matched = ["DETERMINISTIC_SAMPLE_SELECTED" if decision == "SAMPLED_REVIEW" else "DETERMINISTIC_SAMPLE_NOT_SELECTED"]
    else:
        decision = "INVALID_EVENT"
        matched = []

    selected = decision in {"MUST_REVIEW", "SAMPLED_REVIEW"}
    if not selected:
        route = "NOT_APPLICABLE"
    elif event_value["safe_local_route_available"]:
        route = "SAFE_LOCAL_ROUTE_AVAILABLE"
    elif event_value["ownership_value"] in {"entrusted", "unknown", "mixed"}:
        route = "BLOCKED_NO_SAFE_LOCAL_ROUTE"
    else:
        route = "BLOCKED_ROUTE_NOT_ACTIVATED"
    reason_codes = list(matched)
    if decision == "INVALID_EVENT" and not reason_codes:
        reason_codes = ["NO_POLICY_RULE_MATCH_DEFAULT_BLOCK"]
    key = review_job_key(subject, policy_value) if selected else None
    body = {
        "schema_version": "ReviewOrchestrationReviewTriggerDecision-v1",
        "subject_key": subject,
        "policy_ref": f"{policy_value['policy_id']}@{policy_value['revision']}",
        "epoch_id": policy_value["effective_epoch_id"],
        "eligibility_status": "ELIGIBLE" if decision != "INVALID_EVENT" else "INVALID",
        "decision": decision,
        "matched_rule_ids": matched,
        "stratum_id": stratum["stratum_id"] if stratum else None,
        "sample_bucket": bucket,
        "rate_bps": rate,
        "review_job_key": key,
        "ownership_route_precheck": route,
        "reason_codes": reason_codes,
        "input_hashes": {
            "event_hash": event_value["content_hash"],
            "policy_hash": policy_value["content_hash"],
            "ownership_snapshot_sha256": event_value["ownership_snapshot_sha256"],
        },
    }
    return {**body, "content_hash": canonical_hash(body)}


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ReviewOrchestrationContractError("TIMESTAMP_TIMEZONE_REQUIRED")
    return parsed


@dataclass
class TriggerRegistry:
    deliveries: dict[str, dict[str, Any]] = field(default_factory=dict)
    event_id_hashes: dict[str, str] = field(default_factory=dict)
    subjects: dict[str, dict[str, Any]] = field(default_factory=dict)
    decisions: dict[str, dict[str, Any]] = field(default_factory=dict)
    jobs: dict[str, str] = field(default_factory=dict)
    next_epoch_deliveries: list[str] = field(default_factory=list)

    def ingest(
        self,
        *,
        event: Mapping[str, Any],
        delivery_id: str,
        delivered_at: str,
        policy: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(delivery_id, str) or not delivery_id:
            raise ReviewOrchestrationContractError("DELIVERY_ID_INVALID")
        event_hash = event.get("content_hash") or canonical_hash(dict(event))
        request_fingerprint = canonical_hash({"delivery_id": delivery_id, "event_hash": event_hash})
        if delivery_id in self.deliveries:
            existing = self.deliveries[delivery_id]
            if existing["request_fingerprint"] != request_fingerprint:
                raise ReviewOrchestrationContractError("DELIVERY_ID_CONFLICT")
            replay_body = {
                key: value for key, value in existing.items() if key != "content_hash"
            }
            replay_body["duplicate_delivery_id"] = True
            return {**replay_body, "content_hash": canonical_hash(replay_body)}

        policy_value = validate_trigger_policy(policy)
        if _time(delivered_at) > _time(policy_value["cutoff_at"]):
            body = {
                "delivery_id": delivery_id,
                "delivery_disposition": "NEXT_EPOCH",
                "event_hash": event_hash,
                "request_fingerprint": request_fingerprint,
                "duplicate_delivery_id": False,
                "idempotent_subject_replay": False,
                "decision_ref": None,
                "review_job_key": None,
            }
            receipt = {**body, "content_hash": canonical_hash(body)}
            self.deliveries[delivery_id] = receipt
            self.next_epoch_deliveries.append(delivery_id)
            return deepcopy(receipt)

        event_id = str(event.get("event_id", "INVALID-" + canonical_hash(dict(event))[:20]))
        prior_event_hash = self.event_id_hashes.get(event_id)
        if prior_event_hash is not None and prior_event_hash != event_hash:
            raise ReviewOrchestrationContractError("EVENT_ID_REUSED_WITH_DIFFERENT_HASH")
        self.event_id_hashes[event_id] = str(event_hash)
        decision = evaluate_trigger(event, policy_value)
        subject = decision["subject_key"]
        replay = subject in self.decisions
        if replay:
            decision = self.decisions[subject]
        else:
            self.subjects[subject] = dict(event)
            self.decisions[subject] = decision
            if decision["review_job_key"]:
                self.jobs[decision["review_job_key"]] = subject
        body = {
            "delivery_id": delivery_id,
            "delivery_disposition": "CURRENT_EPOCH",
            "event_hash": event_hash,
            "request_fingerprint": request_fingerprint,
            "duplicate_delivery_id": False,
            "idempotent_subject_replay": replay,
            "decision_ref": decision["content_hash"],
            "review_job_key": decision["review_job_key"],
        }
        receipt = {**body, "content_hash": canonical_hash(body)}
        self.deliveries[delivery_id] = receipt
        return deepcopy(receipt)

    def denominator(self) -> dict[str, int]:
        current = [item for item in self.deliveries.values() if item["delivery_disposition"] == "CURRENT_EPOCH"]
        return {
            "raw_event_deliveries": len(current),
            "unique_event_identities": len(self.event_id_hashes),
            "unique_analysis_subjects": len(self.subjects),
            "trigger_decisions": len(self.decisions),
            "review_jobs": len(self.jobs),
            "physical_attempts": 0,
            "next_epoch_deliveries": len(self.next_epoch_deliveries),
        }


__all__ = ["TriggerRegistry", "analysis_subject_key", "evaluate_trigger", "sampling_bucket"]
