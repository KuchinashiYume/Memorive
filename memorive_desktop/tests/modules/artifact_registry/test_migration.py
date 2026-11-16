from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from memorive.documents.registry.errors import ConcurrentWriterError
from memorive.documents.registry.migration import MigrationDivergenceError, MigrationExecutor, MigrationPlanner, MigrationPreflightError, PlannedMigrationFault, registry_baseline
from memorive.documents.registry.migration_contracts import MigrationContractError, artifact_id_from_identity, inventory_digest, load_jsonl_receipts, sha256_bytes
from memorive.documents.registry.registry_v2 import ArtifactRegistry
from memorive.documents.registry.schema_v2 import build_envelope, parent_link
from memorive.documents.registry.schema_v2 import unresolved_requirement
FIXED_TIME = "2026-08-17T12:00:00+09:00"


class T10TestCase(unittest.TestCase):
    def setUp(self) -> None:
        parent_value = os.environ.get("MEMORIVE_MIGRATION_TEST_TEMP_ROOT")
        if not parent_value:
            self.fail("MEMORIVE_MIGRATION_TEST_TEMP_ROOT is required")
        parent = Path(parent_value)
        parent.mkdir(parents=True, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(dir=parent)
        self.root = Path(self._temporary.name)
        self.source_root = self.root / "sources"
        self.source_root.mkdir()
        self.registry_root = self.root / "registry"
        self.receipt_path = self.root / "receipts" / "migration.jsonl"

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def make_item(
        self,
        item_id: str,
        content: bytes,
        *,
        identity: str | None = None,
        expected_disposition: str = "IMPORTED",
        rights: str = "self",
        classification: str = "public",
        locator_class: str = "RUN_LOCAL_PUBLIC_SAFE_FIXTURE",
        parent_artifacts: list[dict] | None = None,
        unresolved: list[dict] | None = None,
        conflicts: list[dict] | None = None,
        artifact_type: str = "migration_test_asset",
        paper_ids: list[str] | None = None,
        metadata: dict | None = None,
        write_source: bool = True,
    ) -> dict:
        identity_basis = identity or f"identity:{item_id}"
        source = self.source_root / f"{item_id}.bin"
        if write_source:
            source.write_bytes(content)
        return {
            "item_id": item_id,
            "item_order": 1,
            "source_locator_class": locator_class,
            "source_locator": str(source),
            "expected_bytes": len(content),
            "expected_sha256": sha256_bytes(content),
            "rights": rights,
            "classification": classification,
            "artifact_type": artifact_type,
            "identity_basis": identity_basis,
            "expected_artifact_id": artifact_id_from_identity(identity_basis),
            "run_ref": "asset_migration-test-run",
            "schema_ref": None,
            "evidence_refs": [f"test:{item_id}"],
            "parent_artifacts": list(parent_artifacts or []),
            "unresolved_parent_requirements": list(unresolved or []),
            "conflicting_candidates": list(conflicts or []),
            "expected_disposition": expected_disposition,
            "adapter_key": "LEGACY_MANIFEST_V1",
            "adapter_version": "1.0",
            "paper_ids": list(paper_ids or []),
            "created_at": None,
            "registered_at": None,
            "metadata": dict(metadata or {}),
        }

    def make_batch(
        self,
        items: list[dict],
        *,
        batch_id: str = "batch-001",
        mode: str = "CAPABILITY_SANDBOX",
        root_class: str = "ISOLATED_SANDBOX",
        target_root: Path | None = None,
    ) -> dict:
        ordered: list[dict] = []
        for index, item in enumerate(items, start=1):
            value = copy.deepcopy(item)
            value["item_order"] = index
            ordered.append(value)
        target = target_root or self.registry_root
        return {
            "batch_schema_version": "asset_migration-migration-batch-v1",
            "batch_id": batch_id,
            "mode": mode,
            "authority_ref": "test:asset_migration-authority",
            "source_snapshot": {"fixture_class": "PUBLIC_SAFE_SYNTHETIC"},
            "source_inventory_sha256": inventory_digest(ordered),
            "target_registry_root_class": root_class,
            "target_registry_baseline": registry_baseline(target),
            "items": ordered,
            "failure_policy": {
                "preflight_failure": "STOP_BEFORE_FIRST_APPEND",
                "unexpected_conflict": "STOP_AND_MARK_NOT_RUN",
                "runtime_error": "STOP_AND_PRESERVE_PARTIAL",
            },
            "external_side_effect_ceiling": 0,
            "created_at": FIXED_TIME,
        }

    def planner(self) -> MigrationPlanner:
        return MigrationPlanner(
            allowed_sandbox_root=self.root,
            production_roots=(Path(r"G:\Memorive-ops\artifact_registry\artifact_registry"),),
        )

    def executor(self) -> MigrationExecutor:
        return MigrationExecutor(
            allowed_sandbox_root=self.root,
            production_roots=(Path(r"G:\Memorive-ops\artifact_registry\artifact_registry"),),
            clock=lambda: FIXED_TIME,
        )


class MigrationContractAndPlannerTests(T10TestCase):
    def test_identity_is_stable_and_not_path_based(self) -> None:
        self.assertEqual(
            artifact_id_from_identity("stable-evidence"),
            artifact_id_from_identity("stable-evidence"),
        )
        self.assertNotEqual(
            artifact_id_from_identity("stable-evidence"),
            artifact_id_from_identity("other-evidence"),
        )

    def test_unknown_batch_field_fails_closed(self) -> None:
        batch = self.make_batch([self.make_item("item-01", b"one")])
        batch["unknown"] = True
        with self.assertRaises(MigrationContractError):
            self.planner().plan(batch, self.registry_root)

    def test_production_mode_requires_separate_authority(self) -> None:
        batch = self.make_batch(
            [self.make_item("item-01", b"one")],
            mode="PRODUCTION_EXACT_BATCH",
            root_class="PRODUCTION_ARTIFACT_REGISTRY",
        )
        with self.assertRaises(MigrationContractError) as caught:
            self.planner().plan(batch, self.registry_root)
        self.assertEqual(caught.exception.code, "PRODUCTION_AUTHORITY_REQUIRED")

    def test_nonzero_external_side_effect_ceiling_is_rejected(self) -> None:
        batch = self.make_batch([self.make_item("item-01", b"one")])
        batch["external_side_effect_ceiling"] = 1
        with self.assertRaises(MigrationContractError):
            self.planner().plan(batch, self.registry_root)

    def test_plan_is_byte_deterministic_and_zero_write(self) -> None:
        batch = self.make_batch([self.make_item("item-01", b"one")])
        first = self.planner().plan(batch, self.registry_root)
        second = self.planner().plan(batch, self.registry_root)
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "DRY_RUN_PASS")
        self.assertFalse(self.registry_root.exists())
        self.assertEqual(first["registry_writes"], 0)
        self.assertEqual(first["unlisted_sibling_reads"], 0)

    def test_source_drift_fails_before_registry_append(self) -> None:
        item = self.make_item("item-01", b"one")
        batch = self.make_batch([item])
        Path(item["source_locator"]).write_bytes(b"changed")
        plan = self.planner().plan(batch, self.registry_root)
        self.assertEqual(plan["status"], "FAILED_PREFLIGHT")
        self.assertEqual(plan["error_code"], "SOURCE_DRIFT")
        self.assertEqual(registry_baseline(self.registry_root)["record_count"], 0)

    def test_contract_rejection_does_not_read_missing_source(self) -> None:
        item = self.make_item(
            "item-01",
            b"private",
            rights="blocked",
            classification="private",
            expected_disposition="REJECTED_BY_CONTRACT",
            write_source=False,
        )
        batch = self.make_batch([item])
        plan = self.planner().plan(batch, self.registry_root)
        self.assertEqual(plan["items"][0]["source_check"]["status"], "NOT_READ_CONTRACT_REJECTED")
        summary = self.executor().apply_or_resume(
            batch,
            plan,
            target_registry_root=self.registry_root,
            receipt_path=self.receipt_path,
        )
        self.assertEqual(summary["dispositions"]["item-01"], "REJECTED_BY_CONTRACT")
        self.assertEqual(summary["registry_baseline"]["record_count"], 0)

    def test_bad_tail_is_source_corrupt_blocked_without_evidence_write(self) -> None:
        item = self.make_item("item-01", b"one")
        batch = self.make_batch([item])
        self.registry_root.mkdir()
        annual = self.registry_root / "2026.jsonl"
        annual.write_bytes(b'{"broken":true}')
        with self.assertRaises(MigrationPreflightError) as caught:
            self.planner().plan(batch, self.registry_root)
        self.assertEqual(caught.exception.code, "SOURCE_CORRUPT_BLOCKED")
        self.assertEqual(sorted(path.name for path in self.registry_root.iterdir()), ["2026.jsonl"])


class MigrationExecutionTests(T10TestCase):
    def test_import_and_exact_repeat_are_idempotent(self) -> None:
        item = self.make_item("item-01", b"one", identity="stable-item")
        batch = self.make_batch([item], batch_id="batch-001")
        plan = self.planner().plan(batch, self.registry_root)
        first = self.executor().apply_or_resume(
            batch, plan, target_registry_root=self.registry_root, receipt_path=self.receipt_path
        )
        self.assertEqual(first["dispositions"], {"item-01": "IMPORTED"})
        before = registry_baseline(self.registry_root)
        repeat_item = self.make_item("item-01", b"one", identity="stable-item", expected_disposition="NOOP_EXACT")
        repeat_batch = self.make_batch([repeat_item], batch_id="batch-002")
        repeat_plan = self.planner().plan(repeat_batch, self.registry_root)
        repeat = self.executor().apply_or_resume(
            repeat_batch,
            repeat_plan,
            target_registry_root=self.registry_root,
            receipt_path=self.root / "receipts" / "repeat.jsonl",
        )
        self.assertEqual(repeat["dispositions"], {"item-01": "NOOP_EXACT"})
        repeat_terminal = [
            record
            for record in load_jsonl_receipts(self.root / "receipts" / "repeat.jsonl")
            if record["phase"] == "RECEIPT_COMMITTED"
        ][0]
        self.assertIsNone(repeat_terminal["recovery_class"])
        self.assertEqual(before, registry_baseline(self.registry_root))

    def test_unresolved_is_accepted_not_failed(self) -> None:
        unresolved = unresolved_requirement(
            requirement="exact-parent",
            reason_code="EVIDENCE_NOT_AVAILABLE",
            evidence_refs=["test:missing-parent"],
            search_hints=["future-evidence"],
        )
        item = self.make_item(
            "item-01",
            b"partial",
            expected_disposition="UNRESOLVED_ACCEPTED",
            unresolved=[unresolved],
        )
        batch = self.make_batch([item])
        plan = self.planner().plan(batch, self.registry_root)
        result = self.executor().apply_or_resume(
            batch, plan, target_registry_root=self.registry_root, receipt_path=self.receipt_path
        )
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["dispositions"]["item-01"], "UNRESOLVED_ACCEPTED")
        self.assertFalse(result["unresolved_is_failure"])

    def test_known_parent_chain_imports_in_order(self) -> None:
        parent = self.make_item("parent", b"parent")
        link = parent_link(
            parent_artifact_id=parent["expected_artifact_id"],
            parent_content_hash=parent["expected_sha256"].lower(),
            relation="derived_from",
            evidence_kind="exact_manifest",
            source_field="parent_artifacts",
            evidence_ref="test:parent-link",
        )
        child = self.make_item("child", b"child", parent_artifacts=[link])
        batch = self.make_batch([parent, child])
        plan = self.planner().plan(batch, self.registry_root)
        self.assertEqual([item["disposition"] for item in plan["items"]], ["IMPORTED", "IMPORTED"])
        result = self.executor().apply_or_resume(
            batch, plan, target_registry_root=self.registry_root, receipt_path=self.receipt_path
        )
        self.assertEqual(result["terminal_item_count"], 2)

    def test_predicted_conflict_marks_later_items_not_run(self) -> None:
        first = self.make_item("first", b"new", identity="conflicted-id", expected_disposition="CONFLICT_BLOCKED")
        existing_source = self.source_root / "existing.bin"
        existing_source.write_bytes(b"old")
        ArtifactRegistry(self.registry_root, clock=lambda: FIXED_TIME).register(
            build_envelope(
                artifact_id=first["expected_artifact_id"],
                artifact_type=first["artifact_type"],
                content_hash=sha256_bytes(b"old").lower(),
                locator_path=existing_source,
                registered_at=FIXED_TIME,
                run_ref="preexisting",
                legacy_import=True,
                evidence_refs=["test:preexisting"],
            )
        )
        second = self.make_item("second", b"second", expected_disposition="NOT_RUN")
        batch = self.make_batch([first, second])
        plan = self.planner().plan(batch, self.registry_root)
        result = self.executor().apply_or_resume(
            batch, plan, target_registry_root=self.registry_root, receipt_path=self.receipt_path
        )
        self.assertEqual(result["status"], "PARTIAL_BLOCKED")
        self.assertEqual(result["dispositions"], {"first": "CONFLICT_BLOCKED", "second": "NOT_RUN"})

    def test_crash_before_append_resumes_normally(self) -> None:
        item = self.make_item("item-01", b"one")
        batch = self.make_batch([item])
        plan = self.planner().plan(batch, self.registry_root)

        def fault(point: str, _item: dict, _context: dict) -> None:
            if point == "BEFORE_REGISTRY_APPEND":
                raise PlannedMigrationFault("planned-before")

        with self.assertRaises(PlannedMigrationFault):
            self.executor().apply_or_resume(
                batch,
                plan,
                target_registry_root=self.registry_root,
                receipt_path=self.receipt_path,
                fault_hook=fault,
            )
        self.assertEqual(registry_baseline(self.registry_root)["record_count"], 0)
        result = self.executor().apply_or_resume(
            batch, plan, target_registry_root=self.registry_root, receipt_path=self.receipt_path
        )
        self.assertEqual(result["status"], "COMPLETED")

    def test_crash_after_append_is_recovered_from_registry_truth(self) -> None:
        item = self.make_item("item-01", b"one")
        batch = self.make_batch([item])
        plan = self.planner().plan(batch, self.registry_root)

        def fault(point: str, _item: dict, _context: dict) -> None:
            if point == "AFTER_REGISTRY_APPEND_BEFORE_RECEIPT_COMMIT":
                raise PlannedMigrationFault("planned-after-append")

        with self.assertRaises(PlannedMigrationFault):
            self.executor().apply_or_resume(
                batch,
                plan,
                target_registry_root=self.registry_root,
                receipt_path=self.receipt_path,
                fault_hook=fault,
            )
        self.assertEqual(registry_baseline(self.registry_root)["record_count"], 1)
        result = self.executor().apply_or_resume(
            batch, plan, target_registry_root=self.registry_root, receipt_path=self.receipt_path
        )
        self.assertEqual(result["status"], "COMPLETED")
        records = load_jsonl_receipts(self.receipt_path)
        terminal = [record for record in records if record["phase"] == "RECEIPT_COMMITTED"][0]
        self.assertEqual(terminal["recovery_class"], "RECOVERED_AS_NOOP")
        self.assertEqual(registry_baseline(self.registry_root)["record_count"], 1)

    def test_crash_after_receipt_does_not_duplicate_event_or_receipt(self) -> None:
        item = self.make_item("item-01", b"one")
        batch = self.make_batch([item])
        plan = self.planner().plan(batch, self.registry_root)

        def fault(point: str, _item: dict, _context: dict) -> None:
            if point == "AFTER_RECEIPT_COMMIT":
                raise PlannedMigrationFault("planned-after-receipt")

        with self.assertRaises(PlannedMigrationFault):
            self.executor().apply_or_resume(
                batch,
                plan,
                target_registry_root=self.registry_root,
                receipt_path=self.receipt_path,
                fault_hook=fault,
            )
        before_records = load_jsonl_receipts(self.receipt_path)
        result = self.executor().apply_or_resume(
            batch, plan, target_registry_root=self.registry_root, receipt_path=self.receipt_path
        )
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(before_records, load_jsonl_receipts(self.receipt_path))
        self.assertEqual(registry_baseline(self.registry_root)["record_count"], 1)

    def test_committed_receipt_with_missing_registry_fails_closed(self) -> None:
        item = self.make_item("item-01", b"one")
        batch = self.make_batch([item])
        plan = self.planner().plan(batch, self.registry_root)
        self.executor().apply_or_resume(
            batch, plan, target_registry_root=self.registry_root, receipt_path=self.receipt_path
        )
        (self.registry_root / "2026.jsonl").unlink()
        with self.assertRaises(MigrationDivergenceError):
            self.executor().apply_or_resume(
                batch, plan, target_registry_root=self.registry_root, receipt_path=self.receipt_path
            )

    def test_truncated_receipt_chain_fails_closed(self) -> None:
        self.receipt_path.parent.mkdir()
        self.receipt_path.write_bytes(b'{"partial":true}')
        with self.assertRaises(MigrationContractError) as caught:
            load_jsonl_receipts(self.receipt_path)
        self.assertEqual(caught.exception.code, "RECEIPT_TRUNCATED")

    def test_receipt_writer_lock_is_not_stolen(self) -> None:
        item = self.make_item("item-01", b"one")
        batch = self.make_batch([item])
        plan = self.planner().plan(batch, self.registry_root)
        self.receipt_path.parent.mkdir()
        lock = self.receipt_path.with_suffix(self.receipt_path.suffix + ".writer.lock")
        lock.write_text("held", encoding="utf-8")
        with self.assertRaises(ConcurrentWriterError):
            self.executor().apply_or_resume(
                batch, plan, target_registry_root=self.registry_root, receipt_path=self.receipt_path
            )
        self.assertEqual(lock.read_text(encoding="utf-8"), "held")


class MigrationSchemaTests(T10TestCase):
    def test_batch_and_receipt_schemas_accept_runtime_objects(self) -> None:
        item = self.make_item("item-01", b"one")
        batch = self.make_batch([item])
        plan = self.planner().plan(batch, self.registry_root)
        self.executor().apply_or_resume(
            batch, plan, target_registry_root=self.registry_root, receipt_path=self.receipt_path
        )
        schema_root = (Path(__file__).resolve().parents[3] / "source" / "memorive" / "documents" / "registry") / "schemas"
        checker = FormatChecker()
        batch_schema = json.loads(
            (schema_root / "asset_migration_migration_batch_v1.schema.json").read_text(encoding="utf-8")
        )
        receipt_schema = json.loads(
            (schema_root / "asset_migration_migration_receipt_v1.schema.json").read_text(encoding="utf-8")
        )
        Draft202012Validator(batch_schema, format_checker=checker).validate(batch)
        for record in load_jsonl_receipts(self.receipt_path):
            Draft202012Validator(receipt_schema, format_checker=checker).validate(record)

    def test_schema_rejects_unknown_field_and_bad_hash(self) -> None:
        schema_root = (Path(__file__).resolve().parents[3] / "source" / "memorive" / "documents" / "registry") / "schemas"
        schema = json.loads(
            (schema_root / "asset_migration_migration_batch_v1.schema.json").read_text(encoding="utf-8")
        )
        batch = self.make_batch([self.make_item("item-01", b"one")])
        bad = copy.deepcopy(batch)
        bad["unknown"] = True
        self.assertTrue(list(Draft202012Validator(schema).iter_errors(bad)))
        bad = copy.deepcopy(batch)
        bad["source_inventory_sha256"] = "0" * 63
        self.assertTrue(list(Draft202012Validator(schema).iter_errors(bad)))


if __name__ == "__main__":
    unittest.main()
