from __future__ import annotations

import copy
import unittest
from pathlib import Path
from unittest.mock import patch

from artifact_registry.migration import PlannedMigrationFault, registry_baseline
from artifact_registry.migration_contracts import artifact_id_from_identity, load_jsonl_receipts
from artifact_registry.query_projection import ProjectionError, ProjectionStaleError, QueryProjectionBuilder, RegistryQueryService, projection_source_fingerprint
from artifact_registry.registry_v2 import ArtifactRegistry
from artifact_registry.schema_v2 import build_envelope
from tests.modules.artifact_registry.test_migration import FIXED_TIME, T10TestCase


MIGRATION_FAULTS = (
    "exact_chain",
    "repeat_manifest_noop",
    "same_id_different_hash",
    "same_id_hash_different_immutable_facts",
    "known_plus_unresolved",
    "multiple_parent_candidates",
    "source_hash_drift",
    "unlisted_sibling_zero_reads",
    "bad_rights_classification",
    "crash_before_registry_append",
    "crash_after_append_before_receipt",
    "crash_after_receipt",
    "target_bad_tail",
    "concurrent_writer",
    "production_root_in_capability_mode",
)
PROJECTION_FAULTS = (
    "fresh_build",
    "bounded_find_filters",
    "parent_child_one_hop",
    "bounded_multi_hop",
    "unresolved_filter_group",
    "stable_pagination",
    "jsonl_head_change",
    "stale_authorized_rebuild",
    "stale_unauthorized_block",
    "sqlite_deleted_rebuild",
    "sqlite_corrupt_rebuild",
    "projection_version_mismatch",
    "jsonl_corrupt_block",
    "reverse_write_forbidden",
)


class FaultMatrixTests(T10TestCase):
    def test_frozen_fault_catalog_has_all_15_plus_14_unique_cases(self) -> None:
        self.assertEqual(len(MIGRATION_FAULTS), 15)
        self.assertEqual(len(PROJECTION_FAULTS), 14)
        self.assertEqual(len(set(MIGRATION_FAULTS)), 15)
        self.assertEqual(len(set(PROJECTION_FAULTS)), 14)

    def test_same_id_same_hash_different_facts_is_conflict(self) -> None:
        item = self.make_item(
            "item-01", b"same-bytes", identity="same-id", expected_disposition="CONFLICT_BLOCKED"
        )
        existing_source = self.source_root / "existing.bin"
        existing_source.write_bytes(b"same-bytes")
        ArtifactRegistry(self.registry_root, clock=lambda: FIXED_TIME).register(
            build_envelope(
                artifact_id=item["expected_artifact_id"],
                artifact_type=item["artifact_type"],
                content_hash=item["expected_sha256"].lower(),
                locator_path=existing_source,
                registered_at=FIXED_TIME,
                run_ref="different-immutable-run",
                legacy_import=True,
                evidence_refs=["test:different-facts"],
            )
        )
        batch = self.make_batch([item])
        plan = self.planner().plan(batch, self.registry_root)
        self.assertEqual(plan["items"][0]["error_code"], "IMMUTABLE_FACT_CONFLICT")

    def test_multiple_parent_candidates_are_preserved_not_guessed(self) -> None:
        candidates = [artifact_id_from_identity("candidate-a"), artifact_id_from_identity("candidate-b")]
        conflict = {
            "requirement": "exact-parent",
            "reason_code": "MULTIPLE_CANDIDATES_NO_UNIQUE_EVIDENCE",
            "candidate_artifact_ids": candidates,
            "evidence_refs": ["test:ambiguous-parent"],
        }
        item = self.make_item(
            "item-01",
            b"ambiguous",
            conflicts=[conflict],
            expected_disposition="UNRESOLVED_ACCEPTED",
        )
        batch = self.make_batch([item])
        plan = self.planner().plan(batch, self.registry_root)
        result = self.executor().apply_or_resume(
            batch, plan, target_registry_root=self.registry_root, receipt_path=self.receipt_path
        )
        envelope = ArtifactRegistry(self.registry_root).get(item["expected_artifact_id"])
        self.assertEqual(result["dispositions"]["item-01"], "UNRESOLVED_ACCEPTED")
        self.assertEqual(envelope["conflicting_candidates"][0]["candidate_artifact_ids"], candidates)

    def test_unlisted_sibling_is_not_in_plan_or_read_inventory(self) -> None:
        item = self.make_item("listed", b"listed")
        sibling = self.source_root / "unlisted-secret.bin"
        sibling.write_bytes(b"must-not-be-read")
        batch = self.make_batch([item])
        plan = self.planner().plan(batch, self.registry_root)
        rendered = repr(plan)
        self.assertNotIn(sibling.name, rendered)
        self.assertEqual(plan["unlisted_sibling_reads"], 0)

    def test_existing_registry_writer_lock_is_not_stolen(self) -> None:
        item = self.make_item("item-01", b"one")
        batch = self.make_batch([item])
        plan = self.planner().plan(batch, self.registry_root)
        self.registry_root.mkdir()
        lock = self.registry_root / ".writer.lock"
        lock.write_text("pid=external\n", encoding="ascii")
        result = self.executor().apply_or_resume(
            batch, plan, target_registry_root=self.registry_root, receipt_path=self.receipt_path
        )
        self.assertEqual(result["status"], "PARTIAL_BLOCKED")
        self.assertEqual(result["dispositions"]["item-01"], "CONFLICT_BLOCKED")
        self.assertEqual(lock.read_text(encoding="ascii"), "pid=external\n")

    def test_after_append_fault_has_one_event_and_recovery_receipt(self) -> None:
        item = self.make_item("item-01", b"one")
        batch = self.make_batch([item])
        plan = self.planner().plan(batch, self.registry_root)

        def fault(point: str, _item: dict, _context: dict) -> None:
            if point == "AFTER_REGISTRY_APPEND_BEFORE_RECEIPT_COMMIT":
                raise PlannedMigrationFault("frozen-item-02-window")

        with self.assertRaises(PlannedMigrationFault):
            self.executor().apply_or_resume(
                batch,
                plan,
                target_registry_root=self.registry_root,
                receipt_path=self.receipt_path,
                fault_hook=fault,
            )
        self.executor().apply_or_resume(
            batch, plan, target_registry_root=self.registry_root, receipt_path=self.receipt_path
        )
        self.assertEqual(registry_baseline(self.registry_root)["record_count"], 1)
        terminals = [
            record for record in load_jsonl_receipts(self.receipt_path) if record["phase"] == "RECEIPT_COMMITTED"
        ]
        self.assertEqual(len(terminals), 1)
        self.assertEqual(terminals[0]["recovery_class"], "RECOVERED_AS_NOOP")

    def test_source_change_during_projection_build_aborts_before_replace(self) -> None:
        item = self.make_item("item-01", b"one")
        batch = self.make_batch([item])
        plan = self.planner().plan(batch, self.registry_root)
        self.executor().apply_or_resume(
            batch, plan, target_registry_root=self.registry_root, receipt_path=self.receipt_path
        )
        builder = QueryProjectionBuilder(allowed_projection_root=self.root, clock=lambda: FIXED_TIME)
        projection = self.root / "projection" / "index.sqlite"
        registry = ArtifactRegistry(self.registry_root)
        real = projection_source_fingerprint(registry)
        drifted = copy.deepcopy(real)
        drifted["record_count"] += 1
        with patch(
            "artifact_registry.query_projection.projection_source_fingerprint",
            side_effect=[real, drifted],
        ):
            with self.assertRaises(ProjectionError) as caught:
                builder.build(registry, projection)
        self.assertEqual(caught.exception.code, "SOURCE_CHANGED_DURING_BUILD")
        self.assertFalse(projection.exists())

    def test_stale_index_requires_authority_then_rebuilds(self) -> None:
        first = self.make_item("first", b"first")
        batch = self.make_batch([first])
        plan = self.planner().plan(batch, self.registry_root)
        self.executor().apply_or_resume(
            batch, plan, target_registry_root=self.registry_root, receipt_path=self.receipt_path
        )
        projection = self.root / "projection" / "index.sqlite"
        builder = QueryProjectionBuilder(allowed_projection_root=self.root, clock=lambda: FIXED_TIME)
        builder.build(ArtifactRegistry(self.registry_root), projection)
        later = self.make_item("later", b"later")
        later_batch = self.make_batch([later], batch_id="batch-later")
        later_plan = self.planner().plan(later_batch, self.registry_root)
        self.executor().apply_or_resume(
            later_batch,
            later_plan,
            target_registry_root=self.registry_root,
            receipt_path=self.root / "receipts" / "later.jsonl",
        )
        with self.assertRaises(ProjectionStaleError):
            RegistryQueryService(ArtifactRegistry(self.registry_root), projection).find_artifacts({})
        result = RegistryQueryService(
            ArtifactRegistry(self.registry_root),
            projection,
            builder=builder,
            rebuild_authorized=True,
            clock=lambda: FIXED_TIME,
        ).find_artifacts({})
        self.assertEqual(result["returned_count"], 2)

    def test_delete_and_rebuild_keeps_query_digest_and_registry_bytes(self) -> None:
        item = self.make_item("item-01", b"one")
        batch = self.make_batch([item])
        plan = self.planner().plan(batch, self.registry_root)
        self.executor().apply_or_resume(
            batch, plan, target_registry_root=self.registry_root, receipt_path=self.receipt_path
        )
        registry = ArtifactRegistry(self.registry_root)
        projection = self.root / "projection" / "index.sqlite"
        builder = QueryProjectionBuilder(allowed_projection_root=self.root, clock=lambda: FIXED_TIME)
        builder.build(registry, projection)
        before_registry = (self.registry_root / "2026.jsonl").read_bytes()
        first = RegistryQueryService(registry, projection, clock=lambda: FIXED_TIME).find_artifacts({})
        projection.unlink()
        second = RegistryQueryService(
            registry,
            projection,
            builder=builder,
            rebuild_authorized=True,
            clock=lambda: FIXED_TIME,
        ).find_artifacts({})
        self.assertEqual(first["result_digest"], second["result_digest"])
        self.assertEqual(before_registry, (self.registry_root / "2026.jsonl").read_bytes())

    def test_projection_facade_exposes_no_reverse_write_method(self) -> None:
        service = RegistryQueryService(ArtifactRegistry(self.registry_root), self.root / "none.sqlite")
        forbidden = {"register", "append", "update", "delete_registry", "reverse_write"}
        self.assertTrue(all(not hasattr(service, name) for name in forbidden))


if __name__ == "__main__":
    unittest.main()
