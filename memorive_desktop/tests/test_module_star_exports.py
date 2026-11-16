"""Modules keep the public names they listed before this round's cleanup.

Callers outside this repository may write `from module import *`. For the 21 modules whose
__all__ lost names during the removals and came back in one commit, a star import must give every
listed name, and the same object as attribute access. The command outbox, whose removal was
approved, stays removed.
"""
from __future__ import annotations

import importlib
import importlib.util

import pytest

# Names each module listed in __all__ before this round and got back (commit 90147ac).
RESTORED = {'evidence_extraction.segmented_distill.contracts': ['dispatch_artifact_validation', 'validate_field_candidate'],
 'evidence_extraction.segmented_distill.merge': ['merge_facts'],
 'evidence_extraction.segmented_distill.topology': ['plan_bounded_segment_split'],
 'evidence_review.ownership_binding': ['compute_analysis_binding_hash'],
 'evidence_review.segmented_distill_repair': ['apply_targeted_repair', 'freeze_targeted_repair_bundle'],
 'memorive.sessions.refinement.contracts': ['make_capture_attempt_receipt',
                                                          'make_export_snapshot',
                                                          'make_selection_receipt',
                                                          'make_source_access_envelope',
                                                          'payload_identity',
                                                          'validate_capture_attempt_receipt',
                                                          'validate_export_snapshot',
                                                          'validate_selection_receipt',
                                                          'validate_source_access_envelope'],
 'memorive.sessions.refinement.graph': ['build_normalized_graph'],
 'memorive.sessions.refinement.provider_adapter': ['build_readonly_thread_bridge_snapshot',
                                                                 'build_visible_ui_thread_bridge_snapshot',
                                                                 'build_synthetic_snapshot',
                                                                 'capture_local_jsonl_snapshot',
                                                                 'profile_admission',
                                                                 'validate_provider_export',
                                                                 'validate_synthetic_export'],
 'memorive.sessions.refinement.refinement': ['deterministic_refine_graph',
                                                           'make_conflict_set',
                                                           'make_refinement_subject_binding',
                                                           'make_typed_refinement_item'],
 'literature_discovery.live_metadata_transport': ['ReceiptValidator',
                                                  'build_pre_send_receipt',
                                                  'build_url',
                                                  'execute_get',
                                                  'with_content_hash'],
 'memorive_app.contracts': ['error_envelope'],
 'memorive_desktop_service.adapters': ['FakeFacadeAdapter'],
 'memorive_desktop_service.errors': ['BackpressureExceeded', 'InstanceAlreadyActive'],
 'memorive.documents.inbox.contracts': ['require_item_state'],
 'memorive_research_runtime.identity': ['normalize_identifier', 'initialization_candidate_base_projection'],
 'research_reports.api_briefing': ['run_api_briefing'],
 'retrieval.coverage_artifacts': ['admit_chunk_snapshots',
                                  'build_facet_definition_artifact',
                                  'build_logical_span_artifact',
                                  'build_references_bias_policy',
                                  'build_retrieval_coverage_assessment',
                                  'build_source_role_assessment',
                                  'build_source_role_policy',
                                  'build_span_assembly_policy',
                                  'build_verification_artifact_record',
                                  'legacy_analysis_projection',
                                  'pointer_from_payload',
                                  'resolved_parent_link',
                                  'validate_artifact_graph',
                                  'validate_holdout_public_manifest'],
 'retrieval.ownership': ['render_snapshot_frontmatter', 'parse_snapshot_frontmatter'],
 'retrieval_weighting.locator': ['ensure_owned_path',
                                 'load_and_validate_locator',
                                 'load_json_file',
                                 'make_locator',
                                 'tree_sha256',
                                 'validate_locator',
                                 'validate_run_root'],
 'retrieval_weighting.migration': ['MigrationFailure', 'migrate_candidate'],
 'retrieval_weighting.shadow_contracts': ['require_boolean',
                                          'validate_derived_penalty_policy',
                                          'validate_retrieval_policy_snapshot']}
MODULES = sorted(RESTORED)


@pytest.mark.parametrize("module_name", MODULES)
def test_a_star_import_gives_every_listed_name(module_name):
    module = importlib.import_module(module_name)
    listed = list(module.__all__)
    assert listed and len(listed) == len(set(listed))
    namespace = {}
    exec(f"from {module_name} import *", namespace)
    missing = [name for name in listed if name not in namespace]
    assert missing == []
    assert [name for name in listed if namespace[name] is not getattr(module, name)] == []
    assert sorted(set(RESTORED[module_name]) - set(listed)) == []


@pytest.mark.parametrize("module_name, name, kind", [
    ("memorive.sessions.refinement.provider_adapter", "capture_local_jsonl_snapshot", "function"),
    ("retrieval_weighting.migration", "migrate_candidate", "function"),
    ("retrieval_weighting.migration", "MigrationFailure", "exception"),
    ("memorive_desktop_service.errors", "BackpressureExceeded", "exception"),
    ("memorive_desktop_service.errors", "InstanceAlreadyActive", "exception"),
])
def test_names_older_callers_import_are_there_with_their_kind(module_name, name, kind):
    namespace = {}
    exec(f"from {module_name} import *", namespace)
    value = namespace[name]
    if kind == "exception":
        assert isinstance(value, type) and issubclass(value, Exception)
    else:
        assert callable(value)


def test_the_command_outbox_stays_removed():
    assert importlib.util.find_spec("memorive_desktop_service.command_outbox") is None
    errors = importlib.import_module("memorive_desktop_service.errors")
    assert "DesktopCommandOutbox" not in errors.__all__
