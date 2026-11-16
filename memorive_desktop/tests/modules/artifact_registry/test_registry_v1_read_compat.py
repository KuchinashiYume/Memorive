"""Reading registries written in the v1 format (artifact-envelope-v1, registry event v1).

The current registry appends only v2 objects, but it must keep reading files that earlier versions
wrote: schema_v2.validate_readable_envelope and schema_v2.validate_event hand v1 objects to
memorive.documents.registry.schema. These tests build v1 objects with the v1 helpers, read them back through the
current registry and the v2 entry points, and check that malformed v1 objects are refused.
"""
from __future__ import annotations

import copy
import json

import pytest

from memorive.documents.registry import ArtifactRegistry, CorruptRegistryError, EnvelopeValidationError
from memorive.documents.registry import schema as v1
from memorive.documents.registry.schema_v2 import validate_event, validate_readable_envelope

A, B, C = ("art_" + digit * 32 for digit in "123")
AT = "2026-07-17T07:45:14+08:00"


def envelope(artifact_id=A, **overrides):
    values = dict(artifact_id=artifact_id, artifact_type="card", content_hash="a" * 64, locator_path="cards/a.md",
                  registered_at=AT, paper_ids=["paper-001"], legacy_import=True, evidence_refs=["test:v1"])
    values.update(overrides)
    return v1.build_envelope(**values)


def link(parent=A, content_hash="a" * 64):
    return v1.parent_link(parent_artifact_id=parent, parent_content_hash=content_hash, relation="derived_from",
                          evidence_kind="exact_manifest", source_field="parent_artifacts", evidence_ref="test:link")


def unresolved(requirement="exact-source-parent"):
    return v1.unresolved_requirement(requirement, "EVIDENCE_NOT_AVAILABLE", evidence_refs=["test:unresolved"])


def conflict():
    return {"requirement": "exact-source-parent", "reason_code": "TWO_CANDIDATES",
            "candidate_artifact_ids": [A, C], "evidence_refs": ["test:conflict"]}


def event(number, kind, payload):
    return {"event_schema_version": v1.EVENT_SCHEMA_VERSION, "event_id": f"evt_{number:032x}",
            "event_type": kind, "recorded_at": AT, "payload": payload}


def write_registry(root, events):
    root.mkdir(parents=True, exist_ok=True)
    (root / "2026.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return ArtifactRegistry(root)


def test_a_v1_registry_file_is_read_with_its_later_events_applied(tmp_path):
    parent = envelope(A, artifact_type="source_pdf", locator_path="p.pdf")
    child = envelope(B, content_hash="b" * 64, locator_path="c.md", unresolved_parent_requirements=[unresolved()])
    registry = write_registry(tmp_path / "registry", [
        event(1, "artifact_registered", parent),
        event(2, "artifact_registered", child),
        event(3, "lineage_resolution", {"artifact_id": B, "resolved_parent_links": [link()],
                                        "resolved_requirements": ["exact-source-parent"], "evidence_refs": ["test:r"]}),
        event(4, "locator_updated", {"artifact_id": A, "content_hash": "a" * 64, "locator": {"path": "moved.pdf"},
                                     "evidence_refs": ["test:m"]}),
        event(5, "state_transition_observed", {"artifact_id": B, "state_owner": "KNOWLEDGE_ADMISSION",
                                               "event_ref": "e1", "from_status": "pending", "to_status": "active"}),
    ])
    assert len(registry.read_events()) == 5
    by_id = {item["artifact_id"]: item for item in registry.artifacts()}
    assert by_id[A]["schema_version"] == v1.ENVELOPE_SCHEMA_VERSION
    assert by_id[A]["locator"] == {"path": "moved.pdf"}
    assert [l["parent_artifact_id"] for l in by_id[B]["lineage"]["resolved_parent_links"]] == [A]
    assert by_id[B]["lineage"]["unresolved_parent_requirements"] == []
    assert registry.get(B)["artifact_type"] == "card"


def test_a_broken_v1_event_makes_the_registry_refuse_the_file_and_keep_evidence(tmp_path):
    broken = event(1, "artifact_registered", envelope())
    broken["recorded_at"] = "2026-07-17T07:45:14"  # no timezone
    registry = write_registry(tmp_path / "registry", [broken])
    with pytest.raises(CorruptRegistryError, match="recorded_at must include a timezone"):
        registry.read_events()
    assert list((tmp_path / "corruption-evidence").rglob("*.json"))


@pytest.mark.parametrize("lineage, expected", [
    ({}, "complete"),
    ({"resolved_parent_links": [link()]}, "complete"),
    ({"resolved_parent_links": [link()], "unresolved_parent_requirements": [unresolved()]}, "partial"),
    ({"unresolved_parent_requirements": [unresolved()]}, "unknown"),
    ({"conflicting_candidates": [conflict()]}, "conflicted"),
])
def test_lineage_status_of_v1_envelopes(lineage, expected):
    assert v1.lineage_status(envelope(B, **lineage)) == expected


def test_a_well_formed_v1_envelope_passes_the_readable_check():
    value = envelope(B, created_at="2026-07-16T00:00:00Z", source_artifact_ids=[A],
                     resolved_parent_links=[link()], unresolved_parent_requirements=[unresolved()],
                     conflicting_candidates=[conflict()])
    validate_readable_envelope(value)


def _broken(change):
    value = envelope(B, resolved_parent_links=[link()], unresolved_parent_requirements=[unresolved()],
                     conflicting_candidates=[conflict()])
    change(value)
    return value


@pytest.mark.parametrize("change, message", [
    (lambda e: e.update(registered_at="17 July 2026"), "registered_at must be ISO 8601"),
    (lambda e: e.update(registered_at="2026-07-17T07:45:14"), "registered_at must include a timezone"),
    (lambda e: e.update(created_at=""), "created_at must be a non-empty string"),
    (lambda e: e["source_scope"].update(paper_ids=["p", "p"]), "paper_ids entries must be unique"),
    (lambda e: e["source_scope"].update(paper_ids="paper-001"), "paper_ids must be a list"),
    (lambda e: e["source_scope"].update(source_artifact_ids=["not-an-id"]), "source_artifact_ids entry has an invalid format"),
    (lambda e: e.update(evidence_refs=[]), "evidence_refs must not be empty"),
    (lambda e: e.update(evidence_refs=["  "]), "evidence_refs entry must be a non-empty string"),
    (lambda e: e["lineage"]["resolved_parent_links"][0]["evidence_basis"].pop("details"), "evidence_basis fields mismatch"),
    (lambda e: e["lineage"]["resolved_parent_links"][0].update(parent_content_hash="A" * 64), "parent_content_hash has an invalid format"),
    (lambda e: e["lineage"]["unresolved_parent_requirements"][0].update(evidence_refs=[]), "evidence_refs must not be empty"),
    (lambda e: e["lineage"]["unresolved_parent_requirements"][0].update(candidate_artifact_ids=["x"]), "candidate_artifact_ids entry has an invalid format"),
    (lambda e: e["lineage"]["unresolved_parent_requirements"][0].pop("search_hints"), "unresolved requirement fields mismatch"),
    (lambda e: e["lineage"]["conflicting_candidates"][0].update(candidate_artifact_ids=[]), "candidate_artifact_ids must not be empty"),
    (lambda e: e["lineage"]["conflicting_candidates"][0].update(candidate_artifact_ids=[A, A]), "candidate_artifact_ids entries must be unique"),
    (lambda e: e["lineage"]["conflicting_candidates"][0].update(reason_code=""), "reason_code must be a non-empty string"),
    (lambda e: e.update(parents=[]), "parents must exactly mirror"),
    (lambda e: e.update(metadata=[]), "metadata must be an object"),
    (lambda e: e.update(legacy_import="yes"), "legacy_import must be boolean"),
])
def test_malformed_v1_envelopes_are_refused(change, message):
    with pytest.raises(EnvelopeValidationError, match=message):
        validate_readable_envelope(_broken(change))


def test_a_v1_envelope_cannot_name_itself_as_parent():
    with pytest.raises(EnvelopeValidationError, match="its own parent"):
        envelope(A, resolved_parent_links=[link(A)])
    value = envelope(A)
    value["lineage"]["resolved_parent_links"] = [link(A)]
    value["parents"] = [link(A)]
    with pytest.raises(EnvelopeValidationError, match="its own parent"):
        validate_readable_envelope(value)


@pytest.mark.parametrize("payload_change, kind, message", [
    (None, "artifact_registered", None),
    (lambda p: p.update(resolved_parent_links=[]), "lineage_resolution", "requires parent links"),
    (lambda p: p.update(resolved_requirements=["a", "a"]), "lineage_resolution", "resolved_requirements entries must be unique"),
    (lambda p: p["locator"].update(extra="x"), "locator_updated", "locator must contain path"),
    (lambda p: p.update(state_owner="ARTIFACT_REGISTRY"), "state_transition_observed", "must remain KNOWLEDGE_ADMISSION"),
    (None, "something_else", "unsupported event_type"),
])
def test_v1_events_are_checked_by_type(payload_change, kind, message):
    payloads = {
        "artifact_registered": envelope(),
        "lineage_resolution": {"artifact_id": B, "resolved_parent_links": [link()],
                               "resolved_requirements": ["exact-source-parent"], "evidence_refs": ["test:r"]},
        "locator_updated": {"artifact_id": A, "content_hash": "a" * 64, "locator": {"path": "x"}, "evidence_refs": ["t"]},
        "state_transition_observed": {"artifact_id": B, "state_owner": "KNOWLEDGE_ADMISSION", "event_ref": "e1",
                                      "from_status": "pending", "to_status": "active"},
        "something_else": {},
    }
    payload = copy.deepcopy(payloads[kind])
    if payload_change:
        payload_change(payload)
    value = event(1, kind, payload)
    if message is None:
        validate_event(value)
    else:
        with pytest.raises(EnvelopeValidationError, match=message):
            validate_event(value)


def test_v1_event_time_and_id_are_checked():
    value = event(1, "artifact_registered", envelope())
    with pytest.raises(EnvelopeValidationError, match="recorded_at must be ISO 8601"):
        validate_event(dict(value, recorded_at="yesterday"))
    with pytest.raises(EnvelopeValidationError, match="event_id has an invalid format"):
        validate_event(dict(value, event_id="evt_123"))
