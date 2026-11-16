from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from memorive.documents.registry.migration import registry_baseline
from memorive.documents.registry.migration_contracts import sha256_bytes
from memorive.documents.registry.query_projection import ProjectionCorruptError, ProjectionError, ProjectionStaleError, QueryProjectionBuilder, RegistryQueryService, projection_source_fingerprint, query_result_digest
from memorive.documents.registry.registry_v2 import ArtifactRegistry
from memorive.documents.registry.schema_v2 import parent_link
from memorive.documents.registry.schema_v2 import unresolved_requirement
from tests.modules.artifact_registry.test_migration import FIXED_TIME, T10TestCase


class ProjectionTestCase(T10TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.projection_path = self.root / "projection" / "artifact_registry.sqlite"
        self.builder = QueryProjectionBuilder(
            allowed_projection_root=self.root,
            production_roots=(Path(r"G:\Memorive-ops\artifact_registry\artifact_registry"),),
            clock=lambda: FIXED_TIME,
        )

    def populate_registry(self, *, extra: int = 0) -> list[dict]:
        parent = self.make_item(
            "parent",
            b"parent",
            artifact_type="source_pdf",
            paper_ids=["paper-001"],
            metadata={"status": "accepted"},
        )
        link_parent = parent_link(
            parent_artifact_id=parent["expected_artifact_id"],
            parent_content_hash=parent["expected_sha256"].lower(),
            relation="derived_from",
            evidence_kind="exact_manifest",
            source_field="parent_artifacts",
            evidence_ref="test:parent",
        )
        child = self.make_item(
            "child",
            b"child",
            artifact_type="card",
            parent_artifacts=[link_parent],
            metadata={"status": "reviewed"},
        )
        link_child = parent_link(
            parent_artifact_id=child["expected_artifact_id"],
            parent_content_hash=child["expected_sha256"].lower(),
            relation="derived_from",
            evidence_kind="exact_manifest",
            source_field="parent_artifacts",
            evidence_ref="test:child",
        )
        grandchild = self.make_item(
            "grandchild",
            b"grandchild",
            artifact_type="analysis_result",
            parent_artifacts=[link_child],
        )
        unresolved = self.make_item(
            "unresolved",
            b"unresolved",
            artifact_type="card",
            expected_disposition="UNRESOLVED_ACCEPTED",
            unresolved=[
                unresolved_requirement(
                    requirement="exact-source-parent",
                    reason_code="EVIDENCE_NOT_AVAILABLE",
                    evidence_refs=["test:unresolved"],
                    search_hints=["future-proof"],
                )
            ],
        )
        items = [parent, child, grandchild, unresolved]
        for index in range(extra):
            items.append(
                self.make_item(
                    f"extra-{index:02d}",
                    f"extra-{index:02d}".encode("ascii"),
                    artifact_type="extra",
                )
            )
        batch = self.make_batch(items, batch_id="projection-source-batch")
        plan = self.planner().plan(batch, self.registry_root)
        result = self.executor().apply_or_resume(
            batch,
            plan,
            target_registry_root=self.registry_root,
            receipt_path=self.receipt_path,
        )
        self.assertEqual(result["status"], "COMPLETED")
        return batch["items"]

    def build(self) -> tuple[list[dict], dict]:
        items = self.populate_registry(extra=3)
        receipt = self.builder.build(
            ArtifactRegistry(self.registry_root), self.projection_path
        )
        return items, receipt

    def service(self, *, rebuild: bool = False) -> RegistryQueryService:
        return RegistryQueryService(
            ArtifactRegistry(self.registry_root),
            self.projection_path,
            builder=self.builder,
            rebuild_authorized=rebuild,
            clock=lambda: FIXED_TIME,
        )

    def append_one(self, item_id: str = "later") -> dict:
        item = self.make_item(item_id, item_id.encode("ascii"), artifact_type="later_type")
        batch = self.make_batch([item], batch_id=f"batch-{item_id}")
        plan = self.planner().plan(batch, self.registry_root)
        self.executor().apply_or_resume(
            batch,
            plan,
            target_registry_root=self.registry_root,
            receipt_path=self.root / "receipts" / f"{item_id}.jsonl",
        )
        return item


class ProjectionBuildTests(ProjectionTestCase):
    def test_source_fingerprint_is_stable_and_exact(self) -> None:
        self.populate_registry()
        registry = ArtifactRegistry(self.registry_root)
        first = projection_source_fingerprint(registry)
        second = projection_source_fingerprint(registry)
        self.assertEqual(first, second)
        self.assertEqual(first["record_count"], 4)
        self.assertEqual(first["annual_file_manifest"][0]["raw_sha256"], sha256_bytes((self.registry_root / "2026.jsonl").read_bytes()))

    def test_fresh_build_has_expected_rows_and_no_reverse_write(self) -> None:
        _items, receipt = self.build()
        self.assertEqual(receipt["row_counts"]["artifacts"], 7)
        self.assertEqual(receipt["row_counts"]["parent_links"], 2)
        self.assertEqual(receipt["row_counts"]["unresolved_requirements"], 1)
        self.assertFalse(receipt["authoritative"])
        self.assertFalse(receipt["reverse_write_api"])
        self.assertTrue(receipt["atomic_replace"])

    def test_verify_requires_exact_source_fingerprint(self) -> None:
        self.build()
        result = self.builder.verify(ArtifactRegistry(self.registry_root), self.projection_path)
        self.assertEqual(result["status"], "FRESH")
        self.assertTrue(result["data_digest_matches"])

    def test_two_rebuilds_have_same_data_and_query_digest(self) -> None:
        self.build()
        first_build = self.builder.verify(ArtifactRegistry(self.registry_root), self.projection_path)
        first_query = self.service().find_artifacts({"artifact_type": "card"})
        self.builder.build(ArtifactRegistry(self.registry_root), self.projection_path)
        second_build = self.builder.verify(ArtifactRegistry(self.registry_root), self.projection_path)
        second_query = self.service().find_artifacts({"artifact_type": "card"})
        self.assertEqual(first_build["data_digest"], second_build["data_digest"])
        self.assertEqual(first_query["result_digest"], second_query["result_digest"])

    def test_projection_schema_accepts_build_receipt(self) -> None:
        _items, receipt = self.build()
        schema = json.loads(
            ((Path(__file__).resolve().parents[3] / "source" / "memorive" / "documents" / "registry") / "schemas" / "asset_migration_query_projection_v1.schema.json").read_text(encoding="utf-8")
        )
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(receipt)

    def test_projection_target_outside_sandbox_is_rejected(self) -> None:
        self.populate_registry()
        with self.assertRaises(ProjectionError) as caught:
            self.builder.build(ArtifactRegistry(self.registry_root), self.root.parent / "escape.sqlite")
        self.assertEqual(caught.exception.code, "PROJECTION_ROOT_ESCAPE")


class ProjectionQueryTests(ProjectionTestCase):
    def test_find_supports_id_hash_type_run_paper_time_and_status(self) -> None:
        items, _receipt = self.build()
        parent = items[0]
        service = self.service()
        cases = (
            {"artifact_id": parent["expected_artifact_id"]},
            {"content_hash": parent["expected_sha256"].lower()},
            {"artifact_type": "source_pdf"},
            {"run_ref": "asset_migration-test-run"},
            {"paper_id": "paper-001"},
            {"registered_from": FIXED_TIME, "registered_to": FIXED_TIME},
            {"status": "accepted"},
        )
        for filters in cases:
            with self.subTest(filters=filters):
                result = service.find_artifacts(filters)
                self.assertGreaterEqual(result["returned_count"], 1)
                self.assertEqual(result["freshness"], "FRESH")

    def test_stable_pagination_has_no_duplicates_or_gaps(self) -> None:
        self.build()
        service = self.service()
        first = service.find_artifacts({}, limit=3, offset=0)
        second = service.find_artifacts({}, limit=3, offset=3)
        third = service.find_artifacts({}, limit=3, offset=6)
        ids = [row["artifact_id"] for result in (first, second, third) for row in result["rows"]]
        self.assertEqual(len(ids), 7)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids, sorted(ids))

    def test_parent_child_and_bounded_multi_hop_queries(self) -> None:
        items, _receipt = self.build()
        service = self.service()
        grandchild = items[2]["expected_artifact_id"]
        one = service.trace_lineage(grandchild, direction="parents", max_depth=1)
        two = service.trace_lineage(grandchild, direction="parents", max_depth=2)
        self.assertEqual(len(one["nodes"]), 2)
        self.assertEqual(len(two["nodes"]), 3)
        parent = items[0]["expected_artifact_id"]
        children = service.trace_lineage(parent, direction="children", max_depth=2)
        self.assertEqual(len(children["nodes"]), 3)

    def test_lineage_node_limit_is_explicitly_reported(self) -> None:
        items, _receipt = self.build()
        result = self.service().trace_lineage(
            items[2]["expected_artifact_id"], direction="parents", max_depth=2, max_nodes=2
        )
        self.assertTrue(result["has_more"])
        self.assertIn("MAX_NODES_REACHED", result["warnings"])

    def test_unresolved_view_is_derived_and_filterable(self) -> None:
        self.build()
        result = self.service().list_unresolved({"reason_code": "EVIDENCE_NOT_AVAILABLE"})
        self.assertEqual(result["returned_count"], 1)
        self.assertEqual(result["rows"][0]["requirement"], "exact-source-parent")

    def test_query_result_schema_and_digest_match(self) -> None:
        self.build()
        result = self.service().find_artifacts({"artifact_type": "card"})
        schema = json.loads(
            ((Path(__file__).resolve().parents[3] / "source" / "memorive" / "documents" / "registry") / "schemas" / "asset_migration_query_result_v1.schema.json").read_text(encoding="utf-8")
        )
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(result)
        self.assertEqual(result["result_digest"], query_result_digest(result))

    def test_query_limits_and_unknown_filters_fail_closed(self) -> None:
        self.build()
        service = self.service()
        with self.assertRaises(ProjectionError):
            service.find_artifacts({}, limit=501)
        with self.assertRaises(ProjectionError):
            service.find_artifacts({"unknown": "x"})
        with self.assertRaises(ProjectionError):
            service.trace_lineage("missing", max_depth=9)


class ProjectionFreshnessAndRecoveryTests(ProjectionTestCase):
    def test_stale_projection_is_not_returned_without_rebuild_authority(self) -> None:
        self.build()
        self.append_one()
        with self.assertRaises(ProjectionStaleError) as caught:
            self.service().find_artifacts({})
        self.assertEqual(caught.exception.code, "STALE_REBUILD_REQUIRED")

    def test_stale_projection_rebuilds_when_authorized(self) -> None:
        self.build()
        item = self.append_one()
        result = self.service(rebuild=True).find_artifacts(
            {"artifact_id": item["expected_artifact_id"]}
        )
        self.assertEqual(result["returned_count"], 1)
        self.assertEqual(result["freshness"], "FRESH")

    def test_deleted_projection_rebuilds_when_authorized(self) -> None:
        self.build()
        self.projection_path.unlink()
        result = self.service(rebuild=True).find_artifacts({})
        self.assertEqual(result["returned_count"], 7)
        self.assertTrue(self.projection_path.is_file())

    def test_corrupt_projection_blocks_or_rebuilds_by_authority(self) -> None:
        self.build()
        self.projection_path.write_bytes(b"not-sqlite")
        with self.assertRaises(ProjectionCorruptError):
            self.service().find_artifacts({})
        result = self.service(rebuild=True).find_artifacts({})
        self.assertEqual(result["returned_count"], 7)

    def test_version_mismatch_requires_or_performs_rebuild(self) -> None:
        self.build()
        connection = sqlite3.connect(self.projection_path)
        try:
            connection.execute(
                "UPDATE projection_meta SET projection_schema_version = 'old-version' WHERE singleton = 1"
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(ProjectionStaleError) as caught:
            self.service().find_artifacts({})
        self.assertEqual(caught.exception.code, "VERSION_REBUILD_REQUIRED")
        result = self.service(rebuild=True).find_artifacts({})
        self.assertEqual(result["projection_version"], "asset_migration-query-projection-v1")

    def test_corrupt_registry_blocks_without_index_masking(self) -> None:
        self.build()
        annual = self.registry_root / "2026.jsonl"
        annual.write_bytes(annual.read_bytes() + b"broken-tail")
        before = sorted(path.name for path in self.registry_root.iterdir())
        with self.assertRaises(Exception) as caught:
            self.service(rebuild=True).find_artifacts({})
        self.assertEqual(getattr(caught.exception, "code", None), "SOURCE_CORRUPT_BLOCKED")
        self.assertEqual(before, sorted(path.name for path in self.registry_root.iterdir()))

    def test_query_service_has_no_reverse_write_api(self) -> None:
        self.build()
        service = self.service()
        for name in ("update_registry", "write_registry", "repair_registry", "reverse_write"):
            self.assertFalse(hasattr(service, name))


if __name__ == "__main__":
    unittest.main()
