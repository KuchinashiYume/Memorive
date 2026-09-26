"""M12 decision carrier for one exact DerivedKnowledgeCandidate revision.

Construction A can create only explicitly synthetic fixtures.  The validator
also defines the future real-receipt shape, but this module performs no user
decision, status transition, ingest, Registry write, or activation.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Mapping, Sequence

from m7_weighting.contracts import make_hashed_payload, validate_hash_descriptor, verify_hashed_payload


RECEIPT_SCHEMA = "P03_T06_HUMAN_KNOWLEDGE_DECISION_RECEIPT_V1"
DECISION_SCOPE = "CONTROLLED_INGEST_EXACT_CANDIDATE_REVISION"
_RECEIPT_KEYS = {
    "schema_version",
    "receipt_id",
    "candidate_id",
    "candidate_revision",
    "candidate_hash",
    "decision_scope",
    "decision",
    "decided_by",
    "decided_at",
    "expires_at",
    "evidence_refs",
    "synthetic_fixture",
    "authority_class",
    "controlled_ingest_authorized",
    "content_hash",
}


class KnowledgeDecisionError(ValueError):
    """A decision receipt cannot authorize the exact candidate supplied."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise KnowledgeDecisionError(f"{field} must be non-empty exact text")
    return value


def _timestamp(value: Any, field: str) -> datetime:
    text = _text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise KnowledgeDecisionError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise KnowledgeDecisionError(f"{field} must include an offset")
    return parsed


def _refs(value: Any, field: str) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise KnowledgeDecisionError(f"{field} must be an array")
    result = [_text(item, f"{field}[]") for item in value]
    if not result or result != sorted(set(result)):
        raise KnowledgeDecisionError(f"{field} must be non-empty, sorted and unique")
    return result


def validate_human_knowledge_decision(
    receipt: Mapping[str, Any],
    *,
    evaluated_at: str,
    allow_synthetic: bool,
    require_synthetic: bool = False,
) -> dict[str, Any]:
    try:
        result = verify_hashed_payload(receipt, "knowledge_decision_receipt")
    except Exception as exc:
        raise KnowledgeDecisionError("DECISION_RECEIPT_HASH_OR_SHAPE_INVALID") from exc
    if set(result) != _RECEIPT_KEYS:
        raise KnowledgeDecisionError("DECISION_RECEIPT_EXACT_KEYS_MISMATCH")
    if result["schema_version"] != RECEIPT_SCHEMA:
        raise KnowledgeDecisionError("DECISION_RECEIPT_SCHEMA_UNSUPPORTED")
    _text(result["receipt_id"], "receipt_id")
    _text(result["candidate_id"], "candidate_id")
    revision = result["candidate_revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise KnowledgeDecisionError("candidate_revision must be a positive integer")
    validate_hash_descriptor(result["candidate_hash"], "candidate_hash")
    if result["decision_scope"] != DECISION_SCOPE:
        raise KnowledgeDecisionError("DECISION_SCOPE_MISMATCH")
    if result["decision"] not in {"APPROVE", "REJECT"}:
        raise KnowledgeDecisionError("DECISION_VALUE_INVALID")
    _text(result["decided_by"], "decided_by")
    decided = _timestamp(result["decided_at"], "decided_at")
    expires = _timestamp(result["expires_at"], "expires_at")
    evaluated = _timestamp(evaluated_at, "evaluated_at")
    if expires <= decided:
        raise KnowledgeDecisionError("DECISION_EXPIRY_NOT_AFTER_DECISION")
    if evaluated < decided:
        raise KnowledgeDecisionError("DECISION_NOT_YET_EFFECTIVE")
    if evaluated >= expires:
        raise KnowledgeDecisionError("DECISION_RECEIPT_EXPIRED")
    refs = _refs(result["evidence_refs"], "evidence_refs")
    if not isinstance(result["synthetic_fixture"], bool):
        raise KnowledgeDecisionError("synthetic_fixture must be boolean")
    if not isinstance(result["controlled_ingest_authorized"], bool):
        raise KnowledgeDecisionError("controlled_ingest_authorized must be boolean")
    if result["decision"] == "APPROVE" and result["controlled_ingest_authorized"] is not True:
        raise KnowledgeDecisionError("APPROVE_REQUIRES_OBJECT_AUTHORIZATION")
    if result["decision"] == "REJECT" and result["controlled_ingest_authorized"] is not False:
        raise KnowledgeDecisionError("REJECT_CANNOT_AUTHORIZE_CONTROLLED_INGEST")
    if result["synthetic_fixture"]:
        if not allow_synthetic:
            raise KnowledgeDecisionError("SYNTHETIC_DECISION_NOT_ALLOWED")
        if result["authority_class"] != "TEST_ONLY_NON_AUTHORITATIVE":
            raise KnowledgeDecisionError("SYNTHETIC_AUTHORITY_CLASS_MISMATCH")
        if not result["decided_by"].startswith("TEST_ONLY_SYNTHETIC_HUMAN"):
            raise KnowledgeDecisionError("SYNTHETIC_DECIDER_IDENTITY_MISMATCH")
        if not all(ref.startswith("fixture://") for ref in refs):
            raise KnowledgeDecisionError("SYNTHETIC_DECISION_EVIDENCE_MUST_BE_FIXTURE_ONLY")
    elif result["authority_class"] != "USER_OR_DESIGNATED_HUMAN":
        raise KnowledgeDecisionError("REAL_DECISION_AUTHORITY_CLASS_MISMATCH")
    if require_synthetic and result["synthetic_fixture"] is not True:
        raise KnowledgeDecisionError("REAL_DECISION_NOT_ALLOWED_IN_SYNTHETIC_RUN")
    return result


def assert_decision_binds_candidate(
    receipt: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    evaluated_at: str,
    allow_synthetic: bool,
    require_synthetic: bool = False,
) -> dict[str, Any]:
    result = validate_human_knowledge_decision(
        receipt,
        evaluated_at=evaluated_at,
        allow_synthetic=allow_synthetic,
        require_synthetic=require_synthetic,
    )
    if not isinstance(candidate, Mapping):
        raise KnowledgeDecisionError("candidate must be an object")
    common = candidate.get("common_object")
    if not isinstance(common, Mapping):
        raise KnowledgeDecisionError("candidate.common_object is missing")
    if result["candidate_id"] != common.get("object_id"):
        raise KnowledgeDecisionError("DECISION_CANDIDATE_ID_MISMATCH")
    if result["candidate_revision"] != common.get("revision"):
        raise KnowledgeDecisionError("DECISION_CANDIDATE_REVISION_MISMATCH")
    if result["candidate_hash"] != candidate.get("content_hash"):
        raise KnowledgeDecisionError("DECISION_CANDIDATE_HASH_MISMATCH")
    return result


def build_synthetic_human_decision_fixture(
    *,
    receipt_id: str,
    candidate: Mapping[str, Any],
    decision: str,
    decided_at: str,
    expires_at: str,
    evidence_refs: Sequence[str],
) -> dict[str, Any]:
    if not isinstance(candidate, Mapping) or not isinstance(candidate.get("common_object"), Mapping):
        raise KnowledgeDecisionError("candidate exact identity is required")
    common = candidate["common_object"]
    payload = {
        "schema_version": RECEIPT_SCHEMA,
        "receipt_id": receipt_id,
        "candidate_id": common.get("object_id"),
        "candidate_revision": common.get("revision"),
        "candidate_hash": deepcopy(candidate.get("content_hash")),
        "decision_scope": DECISION_SCOPE,
        "decision": decision,
        "decided_by": "TEST_ONLY_SYNTHETIC_HUMAN_A",
        "decided_at": decided_at,
        "expires_at": expires_at,
        "evidence_refs": list(evidence_refs),
        "synthetic_fixture": True,
        "authority_class": "TEST_ONLY_NON_AUTHORITATIVE",
        "controlled_ingest_authorized": decision == "APPROVE",
    }
    return validate_human_knowledge_decision(
        make_hashed_payload(payload),
        evaluated_at=decided_at,
        allow_synthetic=True,
        require_synthetic=True,
    )


__all__ = [
    "DECISION_SCOPE",
    "KnowledgeDecisionError",
    "RECEIPT_SCHEMA",
    "assert_decision_binds_candidate",
    "build_synthetic_human_decision_fixture",
    "validate_human_knowledge_decision",
]

