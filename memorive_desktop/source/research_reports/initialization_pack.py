"""Generalized DataContracts adapter for the accepted RESEARCH-CONTRACTS common-object semantics.

The accepted validator's hard-coded five-object fixture remains the oracle for
overlapping rules.  This adapter applies the same JCS hash and invariants to an
arbitrary non-empty DataContracts pack; it does not claim byte identity with that
five-object validator.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .adapters import FAILURE_SEMANTICS, IMMUTABLE_FIELDS
from .canonical import canonical_json_bytes, parse_datetime, sha256_bytes, typed_payload_hash
from .errors import ResearchReportsError
from .policy import SECTION_TITLES

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError as exc:  # pragma: no cover - frozen environment prerequisite
    raise RuntimeError("jsonschema is required by the accepted Initialization contract") from exc

COMMON_FIELDS = {
    "object_id",
    "schema_version",
    "revision",
    "content_hash",
    "producer",
    "consumers",
    "state",
    "parent_refs",
    "provenance_refs",
    "supersedes",
    "immutable_fields",
    "failure_semantics",
}
TYPE_FIELDS = {
    "ResearchEvent": {"event_kind", "occurred_at", "source_refs"},
    "ChangeDigest": {"change_scope", "summary", "evidence_refs"},
}
INITIAL_STATES = {
    "ResearchEvent": {"recorded"},
    "ChangeDigest": {"draft"},
}
REACHABLE_STATES = {
    "ResearchEvent": {"recorded", "validated", "rejected", "quarantined"},
    "ChangeDigest": {"draft", "validated", "rejected", "published"},
}
STATE_MACHINES = {
    "ResearchEvent": {
        "recorded->validated",
        "recorded->rejected",
        "recorded->quarantined",
        "validated->quarantined",
    },
    "ChangeDigest": {
        "draft->validated",
        "draft->rejected",
        "validated->published",
        "validated->rejected",
    },
}


class InitializationPackError(ResearchReportsError):
    """A candidate pack violates accepted Initialization common-object semantics."""

    def __init__(self, issues: list[str]):
        self.issues = sorted(set(issues))
        super().__init__("; ".join(self.issues))


def _text_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item for item in value)


def validate_common_object_pack(
    objects: list[dict[str, Any]],
    *,
    common_schema: dict[str, Any],
    actor_locators: set[str],
    authority_refs: set[str],
    authority_objects: dict[str, dict[str, Any]] | None = None,
    data_contracts_exact: bool = False,
) -> None:
    issues: list[str] = []
    if not isinstance(objects, list) or not objects:
        raise InitializationPackError(["common-object pack must be a non-empty list"])
    authority_objects = authority_objects or {}
    try:
        Draft202012Validator.check_schema(common_schema)
    except Exception as exc:
        raise InitializationPackError([f"accepted common schema invalid:{type(exc).__name__}"]) from exc
    schema_validator = Draft202012Validator(common_schema, format_checker=FormatChecker())
    payloads: list[tuple[str, dict[str, Any]]] = []
    for index, wrapper in enumerate(objects):
        if not isinstance(wrapper, dict) or set(wrapper) != {"object_type", "payload"}:
            issues.append(f"wrapper[{index}] fields mismatch")
            continue
        object_type = wrapper.get("object_type")
        payload = wrapper.get("payload")
        if object_type not in TYPE_FIELDS or not isinstance(payload, dict):
            issues.append(f"wrapper[{index}] unsupported object type")
            continue
        expected = COMMON_FIELDS | TYPE_FIELDS[object_type]
        if set(payload) != expected:
            issues.append(f"{object_type}[{index}] payload fields mismatch")
        payloads.append((object_type, payload))
        for error in schema_validator.iter_errors(wrapper):
            issues.append(
                f"schema:{object_type}:{'/'.join(str(value) for value in error.absolute_path)}:{error.validator}"
            )
    ids = [payload.get("object_id") for _, payload in payloads]
    if any(not isinstance(value, str) or not value for value in ids) or len(ids) != len(set(ids)):
        issues.append("duplicate or missing object_id")
    pack_ids = {value for value in ids if isinstance(value, str)}
    by_id = {payload["object_id"]: (object_type, payload) for object_type, payload in payloads if payload.get("object_id")}
    resolvable = pack_ids | authority_refs | set(authority_objects)
    for object_type, payload in payloads:
        object_id = payload.get("object_id")
        if payload.get("schema_version") != "2.0":
            issues.append(f"schema_version mismatch {object_id}")
        revision = payload.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            issues.append(f"invalid revision {object_id}")
        if payload.get("producer") not in actor_locators:
            issues.append(f"unresolved producer {payload.get('producer')}")
        consumers = payload.get("consumers")
        if not _text_list(consumers) or not consumers or len(consumers) != len(set(consumers)):
            issues.append(f"invalid consumers {object_id}")
        else:
            for consumer in consumers:
                if consumer not in actor_locators:
                    issues.append(f"unresolved consumer {consumer}")
        for field in ("parent_refs", "provenance_refs"):
            refs = payload.get(field)
            if not _text_list(refs):
                issues.append(f"invalid {field} {object_id}")
                continue
            for ref in refs:
                if ref not in resolvable:
                    issues.append(f"broken {field} {ref}")
        if payload.get("immutable_fields") != IMMUTABLE_FIELDS:
            issues.append(f"immutable_fields mismatch {object_id}")
        if payload.get("failure_semantics") != FAILURE_SEMANTICS:
            issues.append(f"failure_semantics mismatch {object_id}")
        try:
            expected_hash = typed_payload_hash(payload)
        except (TypeError, ValueError) as exc:
            issues.append(f"JCS domain violation {object_id}:{type(exc).__name__}")
        else:
            if payload.get("content_hash") != expected_hash:
                issues.append(f"content_hash mismatch {object_id}")
        supersedes = payload.get("supersedes")
        if revision == 1 and supersedes is not None:
            issues.append(f"revision 1 must not supersede {object_id}")
        if isinstance(revision, int) and revision > 1 and not supersedes:
            issues.append(f"revision {revision} lacks supersedes {object_id}")
        if supersedes == object_id:
            issues.append(f"self supersedes {object_id}")
        if supersedes:
            if supersedes not in resolvable:
                issues.append(f"broken supersedes {supersedes}")
            predecessor: tuple[str, dict[str, Any]] | None = by_id.get(supersedes)
            if predecessor is None and supersedes in authority_objects:
                prior = authority_objects[supersedes]
                if isinstance(prior, dict) and set(prior) == {"object_type", "payload"} and isinstance(prior["payload"], dict):
                    predecessor = (prior["object_type"], prior["payload"])
            if predecessor is None:
                issues.append(f"supersedes lacks state-bearing authority object {supersedes}")
            else:
                prior_type, prior_payload = predecessor
                if prior_type != object_type:
                    issues.append(f"supersedes type mismatch {object_id}")
                prior_revision = prior_payload.get("revision")
                if not isinstance(prior_revision, int) or revision != prior_revision + 1:
                    issues.append(f"revision is not predecessor+1 {object_id}")
                edge = f"{prior_payload.get('state')}->{payload.get('state')}"
                if edge not in STATE_MACHINES[object_type]:
                    issues.append(f"illegal transition {object_type}:{edge}")
        elif payload.get("state") not in REACHABLE_STATES[object_type]:
            issues.append(f"unreachable state {object_type}:{payload.get('state')}")
        if object_type == "ResearchEvent":
            generic_sources = payload.get("source_refs")
            if not _text_list(generic_sources) or not generic_sources:
                issues.append(f"invalid source_refs {object_id}")
            else:
                for ref in generic_sources:
                    if ref not in resolvable:
                        issues.append(f"unresolved source_ref {ref}")
        if object_type == "ChangeDigest":
            generic_evidence = payload.get("evidence_refs")
            if not _text_list(generic_evidence) or not generic_evidence:
                issues.append(f"invalid digest evidence_refs {object_id}")
            else:
                for ref in generic_evidence:
                    if ref not in resolvable:
                        issues.append(f"unresolved digest evidence_ref {ref}")
        if data_contracts_exact and object_type == "ResearchEvent":
            if payload.get("revision") != 1 or payload.get("supersedes") is not None:
                issues.append(f"DataContracts ResearchEvent must be base revision {object_id}")
            if payload.get("parent_refs") != []:
                issues.append(f"DataContracts ResearchEvent parent_refs must be empty {object_id}")
            source_refs = payload.get("source_refs")
            if _text_list(source_refs) and source_refs and payload.get("provenance_refs") != source_refs:
                issues.append(f"ResearchEvent provenance/source mismatch {object_id}")
            elif _text_list(source_refs):
                for ref in source_refs:
                    if ref not in authority_refs:
                        issues.append(f"unresolved source_ref {ref}")
        elif data_contracts_exact and object_type == "ChangeDigest":
            evidence_refs = payload.get("evidence_refs")
            if _text_list(evidence_refs) and evidence_refs and payload.get("provenance_refs") != evidence_refs:
                issues.append(f"ChangeDigest provenance/evidence mismatch {object_id}")
            for ref in evidence_refs if _text_list(evidence_refs) else []:
                if ref not in resolvable:
                    issues.append(f"unresolved digest evidence_ref {ref}")
    if issues:
        raise InitializationPackError(issues)


def validate_data_contracts_domain_pack(
    events: list[dict[str, Any]],
    digest: dict[str, Any],
    *,
    common_schema: dict[str, Any],
    actor_locators: set[str],
    authority_refs: set[str],
    prior_common_object: dict[str, Any] | None = None,
    historical_authority: dict[str, dict[str, Any]] | None = None,
) -> None:
    issues: list[str] = []
    wrappers = []
    for event in events:
        common = event.get("common_object")
        common_payload = common.get("payload") if isinstance(common, dict) else None
        if not isinstance(common_payload, dict) or common_payload.get("object_id") != event.get("event_id"):
            issues.append(f"ResearchEvent domain/common identity mismatch {event.get('event_id')}")
        else:
            wrappers.append(deepcopy(common))
            expected_source_refs = [event["source_record_ref"]] + sorted(
                (set(event.get("evidence_refs", [])) & authority_refs)
                - {event["source_record_ref"]}
            )
            if (
                common_payload.get("event_kind") != event.get("event_kind")
                or common_payload.get("occurred_at") != event.get("occurred_at")
                or common_payload.get("source_refs") != expected_source_refs
                or common_payload.get("provenance_refs") != expected_source_refs
                or common_payload.get("parent_refs") != []
                or common_payload.get("producer") != "RESEARCH_REPORTS"
                or common_payload.get("consumers") != ["RESEARCH_REPORTS"]
                or common_payload.get("state") != "recorded"
            ):
                issues.append(f"ResearchEvent domain/common field binding mismatch {event.get('event_id')}")
            projection = deepcopy(event)
            projection.pop("common_object", None)
            observed_projection_hash = projection.pop("projection_hash", {}).get("value")
            expected_projection_hash = sha256_bytes(canonical_json_bytes(projection))
            if observed_projection_hash != expected_projection_hash:
                issues.append(f"ResearchEvent projection_hash mismatch {event.get('event_id')}")
    common_digest = digest.get("common_object")
    if not isinstance(common_digest, dict):
        issues.append("ChangeDigest common object missing")
        common_payload: dict[str, Any] = {}
    else:
        common_payload = common_digest.get("payload") if isinstance(common_digest.get("payload"), dict) else {}
    if not common_payload or common_payload.get("object_id") != digest.get("digest_id"):
        issues.append("ChangeDigest domain/common identity mismatch")
    else:
        wrappers.append(deepcopy(common_digest))
    if digest.get("supersedes_digest_id") != common_payload.get("supersedes"):
        issues.append("outer/common supersedes mismatch")
    if digest.get("revision") != common_payload.get("revision"):
        issues.append("outer/common revision mismatch")
    expected_digest_state = "draft" if digest.get("revision") == 1 else "validated"
    expected_summary = f"Deterministic RESEARCH_REPORTS {digest.get('digest_kind')} change log; domain details are source-backed."
    if (
        common_payload.get("state") != expected_digest_state
        or common_payload.get("change_scope") != ["KNOWLEDGE_ADMISSION", "RUNTIME_LOG", "ARTIFACT_REGISTRY"]
        or common_payload.get("summary") != expected_summary
        or common_payload.get("producer") != "RESEARCH_REPORTS"
        or common_payload.get("consumers") != ["Retrieval", "Analysis"]
    ):
        issues.append("ChangeDigest domain/common field binding mismatch")
    report_payload = deepcopy(digest)
    report_payload.pop("common_object", None)
    report_payload.pop("content_hash", None)
    observed_report_hash = report_payload.pop("report_payload_hash", {}).get("value")
    expected_report_hash = sha256_bytes(canonical_json_bytes(report_payload))
    if observed_report_hash != expected_report_hash:
        issues.append("ChangeDigest report_payload_hash mismatch")
    try:
        expected_outer_hash = typed_payload_hash(digest)
    except (TypeError, ValueError) as exc:
        issues.append(f"ChangeDigest outer JCS domain violation:{type(exc).__name__}")
    else:
        if digest.get("content_hash") != expected_outer_hash:
            issues.append("ChangeDigest outer content_hash mismatch")
    event_by_id = {event.get("event_id"): event for event in events if isinstance(event.get("event_id"), str)}
    if len(event_by_id) != len(events):
        issues.append("admitted ResearchEvent IDs are missing or duplicate")

    def expected_item(event: dict[str, Any]) -> dict[str, Any]:
        return {
            "event_id": event["event_id"],
            "source_event_ref": event["event_id"],
            "source_record_ref": event["source_record_ref"],
            "artifact_or_ledger_refs": event["subject_refs"],
            "occurred_at": event["occurred_at"],
            "observed_at": event["observed_at"],
            "status_axis": event["status_axis"],
            "status": event["to_state"],
            "verification_status": event["verification_status"],
            "evidence_refs": event["evidence_refs"],
            "text": event["display_text"],
        }

    report_event_ids: set[str] = set()
    for section in digest.get("sections", []):
        seen_in_section: set[str] = set()
        for item in section.get("items", []) if isinstance(section, dict) else []:
            event_id = item.get("event_id") if isinstance(item, dict) else None
            event = event_by_id.get(event_id)
            if event is None:
                issues.append(f"section item references unknown admitted event {event_id}")
                continue
            if event_id in seen_in_section:
                issues.append(f"duplicate section item {event_id}")
            seen_in_section.add(event_id)
            report_event_ids.add(event_id)
            if item != expected_item(event):
                issues.append(f"section item projection differs from admitted event {event_id}")
    seen_late: set[str] = set()
    for item in digest.get("late_arrivals", []):
        event_id = item.get("event_id") if isinstance(item, dict) else None
        event = event_by_id.get(event_id)
        if event is None:
            issues.append(f"late item references unknown admitted event {event_id}")
            continue
        if event_id in seen_late:
            issues.append(f"duplicate late item {event_id}")
        seen_late.add(event_id)
        report_event_ids.add(event_id)
        proof = (historical_authority or {}).get(event["source_content_hash"])
        if not isinstance(proof, dict):
            issues.append(f"late item lacks frozen historical authority {event_id}")
            continue
        expected_late = expected_item(event)
        expected_late.update(
            {
                "original_window_ref": proof["original_window_ref"],
                "original_cutoff_at": proof["original_cutoff_at"],
                "original_digest_id": proof["original_digest_id"],
                "original_digest_raw_sha256": proof["original_digest_raw_sha256"],
                "original_common_payload_hash": proof["original_common_payload_hash"],
                "late_authority_ref": proof["authority_ref"],
            }
        )
        if item != expected_late:
            issues.append(f"late item projection differs from admitted event/authority {event_id}")

    # Recompute exact window/category coverage so omission or misplacement cannot
    # be hidden by rehashing the outer report and common object.
    try:
        start = parse_datetime(digest.get("window_start"), field_name="window_start")
        end = parse_datetime(digest.get("window_end"), field_name="window_end")
        cutoff = parse_datetime(digest.get("cutoff_at"), field_name="cutoff_at")
    except (TypeError, ValueError):
        issues.append("digest window timestamps are invalid")
        start = end = cutoff = None
    kind = digest.get("digest_kind")
    if kind not in SECTION_TITLES:
        issues.append("digest kind is unsupported")
    elif start is not None:
        current_events = []
        expected_late_items = []
        for event in events:
            occurred = parse_datetime(event["occurred_at"], field_name="occurred_at").astimezone(start.tzinfo)
            observed = parse_datetime(event["observed_at"], field_name="observed_at").astimezone(start.tzinfo)
            if observed > cutoff:
                continue
            if start <= occurred < end:
                current_events.append(event)
            elif occurred < start and start <= observed < end:
                proof = (historical_authority or {}).get(event["source_content_hash"])
                if isinstance(proof, dict) and observed > parse_datetime(proof["original_cutoff_at"], field_name="original_cutoff_at"):
                    late_item = expected_item(event)
                    late_item.update(
                        {
                            "original_window_ref": proof["original_window_ref"],
                            "original_cutoff_at": proof["original_cutoff_at"],
                            "original_digest_id": proof["original_digest_id"],
                            "original_digest_raw_sha256": proof["original_digest_raw_sha256"],
                            "original_common_payload_hash": proof["original_common_payload_hash"],
                            "late_authority_ref": proof["authority_ref"],
                        }
                    )
                    expected_late_items.append(late_item)
        current_events.sort(key=lambda value: (value["occurred_at"], value["event_kind"], value["event_id"]))
        expected_late_items.sort(key=lambda value: (value["occurred_at"], value["event_id"]))
        buckets = {title: [] for title in SECTION_TITLES[kind]}
        for event in current_events:
            if kind == "daily":
                categories = [event["daily_category"]]
            elif kind == "weekly":
                categories = list(event["weekly_categories"])
                if event.get("semantic_authority") == "RESEARCH_OPPORTUNITIES_TENSION_AUTHORITY":
                    categories.append("明确来源的潜在张力")
                if event.get("source_system") == "ARTIFACT_REGISTRY" and set(event.get("explicit_tags", [])) & {"important", "priority:high", "severity:high"}:
                    categories.append("明确标记的重要变化")
            else:
                categories = list(event["monthly_categories"])
                if event.get("semantic_authority") == "DECISION_LOG_DIRECTION_DECISION_AUTHORITY":
                    categories.append("明确记录的研究方向变化")
                if event.get("source_system") == "ARTIFACT_REGISTRY" and "long_term_todo" in set(event.get("explicit_tags", [])):
                    categories.append("长期待办")
            for category in dict.fromkeys(categories):
                if category in buckets:
                    buckets[category].append(expected_item(event))
        expected_sections = [
            {"section_id": f"S{index:02d}", "title": title, "items": buckets[title]}
            for index, title in enumerate(SECTION_TITLES[kind], start=1)
        ]
        if digest.get("sections") != expected_sections:
            issues.append("digest sections do not exactly cover window-admitted categories")
        if digest.get("late_arrivals") != expected_late_items:
            issues.append("digest late_arrivals do not exactly cover frozen late events")
    source_anchors = {
        value
        for value in common_payload.get("evidence_refs", [])
        if isinstance(value, str) and value.startswith("SOURCE-MANIFEST-")
    }
    if len(source_anchors) != 1:
        issues.append("ChangeDigest must bind exactly one SOURCE-MANIFEST anchor")
    else:
        source_anchor = next(iter(source_anchors))
        expected_evidence = sorted(report_event_ids | {source_anchor})
        expected_parents = sorted(report_event_ids) or [source_anchor]
        if common_payload.get("parent_refs") != expected_parents:
            issues.append("ChangeDigest common parent_refs do not exactly cover current+late events")
        if common_payload.get("evidence_refs") != expected_evidence or common_payload.get("provenance_refs") != expected_evidence:
            issues.append("ChangeDigest common evidence/provenance exact coverage mismatch")
    if issues:
        raise InitializationPackError(issues)
    authority_objects = {}
    if prior_common_object is not None:
        prior_payload = prior_common_object.get("payload") if isinstance(prior_common_object, dict) else None
        prior_id = prior_payload.get("object_id") if isinstance(prior_payload, dict) else None
        if isinstance(prior_id, str):
            authority_objects[prior_id] = prior_common_object
    validate_common_object_pack(
        wrappers,
        common_schema=common_schema,
        actor_locators=actor_locators,
        authority_refs=authority_refs,
        authority_objects=authority_objects,
        data_contracts_exact=True,
    )
