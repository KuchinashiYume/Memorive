"""Registry v2: a parent must be registered with exactly the hash the child names, and a ledger
reference must be a typed DataContracts reference.

The older tests in this folder need the private P1 library and release manifest and are skipped
without them; these build their artifacts from small files in a temporary folder.
"""
from __future__ import annotations

import pytest

from artifact_registry import (ArtifactConflictError, ArtifactRegistry, EnvelopeValidationError, UnknownArtifactError,
                               build_envelope, ledger_record_ref, new_artifact_id, parent_link, sha256_file)
from artifact_registry.schema_v2 import unresolved_requirement, validate_envelope

AT = "2026-07-17T08:00:00+08:00"
CONTRACT = "memorive://runtime_log/terminal-ledger/v2"


@pytest.fixture
def registry(tmp_path):
    return ArtifactRegistry(tmp_path / "registry", corruption_evidence_root=tmp_path / "corruption", clock=lambda: AT)


def _artifact(tmp_path, name, **fields):
    path = tmp_path / name
    path.write_bytes(name.encode() + b"\n")
    values = dict(artifact_id=new_artifact_id(), artifact_type="card", content_hash=sha256_file(path),
                  locator_path=path, registered_at=AT, legacy_import=False, evidence_refs=["test:parents"])
    values.update(fields)
    return build_envelope(**values)


def _link(parent, content_hash=None):
    return parent_link(parent_artifact_id=parent["artifact_id"],
                       parent_content_hash=content_hash or parent["content_hash"]["value"],
                       relation="derived_from", evidence_kind="exact_manifest", source_field="parent_artifacts",
                       evidence_ref="test:link")


def test_a_child_naming_its_registered_parent_is_appended(registry, tmp_path):
    parent = _artifact(tmp_path, "parent.pdf", artifact_type="source_pdf")
    assert registry.register(parent) == "appended"
    child = _artifact(tmp_path, "child.md", parent_artifacts=[_link(parent)])
    assert registry.register(child) == "appended"
    assert registry.get(child["artifact_id"])["parent_artifacts"][0]["parent_artifact_id"] == parent["artifact_id"]


def test_a_child_naming_an_unregistered_parent_is_refused(registry, tmp_path):
    parent = _artifact(tmp_path, "parent.pdf", artifact_type="source_pdf")
    child = _artifact(tmp_path, "child.md", parent_artifacts=[_link(parent)])
    with pytest.raises(UnknownArtifactError, match="not registered"):
        registry.register(child)
    assert registry.read_events() == []


def test_a_child_naming_another_version_of_its_parent_is_refused(registry, tmp_path):
    parent = _artifact(tmp_path, "parent.pdf", artifact_type="source_pdf")
    registry.register(parent)
    child = _artifact(tmp_path, "child.md", parent_artifacts=[_link(parent, "0" * 64)])
    with pytest.raises(ArtifactConflictError, match="parent hash mismatch"):
        registry.register(child)
    assert len(registry.read_events()) == 1


def test_resolving_lineage_later_checks_the_parent_the_same_way(registry, tmp_path):
    parent = _artifact(tmp_path, "parent.pdf", artifact_type="source_pdf")
    registry.register(parent)
    child = _artifact(tmp_path, "child.md", unresolved_parent_requirements=[
        unresolved_requirement("exact-source-parent", "EVIDENCE_NOT_AVAILABLE", evidence_refs=["test:open"])])
    registry.register(child)
    for link, error in [(_link(parent, "0" * 64), ArtifactConflictError),
                        (_link(dict(parent, artifact_id=new_artifact_id())), UnknownArtifactError)]:
        with pytest.raises(error):
            registry.append_lineage_resolution(child["artifact_id"], parent_artifacts=[link],
                                               resolved_requirements=["exact-source-parent"],
                                               evidence_refs=["test:resolve"])
    assert len(registry.read_events()) == 2
    assert registry.append_lineage_resolution(child["artifact_id"], parent_artifacts=[_link(parent)],
                                              resolved_requirements=["exact-source-parent"],
                                              evidence_refs=["test:resolve"]) == "appended"


# Ledger references

def _ref(**changes):
    values = dict(record_type="ledger_record", ledger_type="paper_outcome", record_id="outcome_001", run_id="run_001",
                  contract_ref=CONTRACT)
    values.update(changes)
    return values


@pytest.mark.parametrize("values", [
    _ref(),
    _ref(ledger_type="attempt"),
    _ref(ledger_type="publication"),
    _ref(record_type="run_terminal_event", ledger_type=None),
])
def test_typed_ledger_references_are_built(values):
    assert ledger_record_ref(**values) == values


@pytest.mark.parametrize("values, message", [
    (_ref(record_type="artifact"), "record_type is unsupported"),
    (_ref(ledger_type=None), "requires a DataContracts ledger_type"),
    (_ref(ledger_type="invoice"), "requires a DataContracts ledger_type"),
    (_ref(record_type="run_terminal_event", ledger_type="attempt"), "ledger_type must be null"),
    (_ref(record_id=""), "record_id"),
    (_ref(run_id=7), "run_id"),
    (_ref(contract_ref=" "), "contract_ref"),
])
def test_a_ledger_reference_out_of_form_is_refused(values, message):
    with pytest.raises(EnvelopeValidationError, match=message):
        ledger_record_ref(**values)


def test_an_envelope_checks_each_ledger_reference(tmp_path):
    good = ledger_record_ref(**_ref())
    envelope = _artifact(tmp_path, "assessment.json", ledger_record_refs=[good])
    validate_envelope(envelope)
    for refs in ([dict(good, note="x")], [{k: v for k, v in good.items() if k != "run_id"}], ["outcome_001"],
                 [good, dict(good)], dict(good), {}):
        changed = dict(envelope, ledger_record_refs=refs)
        with pytest.raises(EnvelopeValidationError):
            validate_envelope(changed)
