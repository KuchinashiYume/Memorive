from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from memorive.documents.registry.maintenance import RegistryMaintenanceService, human_summary, main
from memorive.documents.registry.migration import registry_baseline
from tests.modules.artifact_registry.test_migration import T10TestCase


class MaintenanceTests(T10TestCase):
    def make_service(self, *, rebuild: bool = False) -> RegistryMaintenanceService:
        return RegistryMaintenanceService(
            registry_root=self.registry_root,
            projection_path=self.root / "projection" / "index.sqlite",
            allowed_sandbox_root=self.root,
            production_roots=(Path(r"G:\Memorive-ops\artifact_registry\artifact_registry"),),
            rebuild_authorized=rebuild,
        )

    def import_one(self, service: RegistryMaintenanceService) -> tuple[dict, dict]:
        item = self.make_item("item-01", b"one", artifact_type="maintenance_asset")
        batch = self.make_batch([item])
        plan = service.plan_migration(batch)
        result = service.apply_or_resume(batch, plan, receipt_path=self.receipt_path)
        self.assertEqual(result["status"], "COMPLETED")
        return item, result

    def test_facade_runs_plan_apply_build_verify_and_query(self) -> None:
        service = self.make_service()
        item, _result = self.import_one(service)
        build = service.rebuild_projection()
        verify = service.verify_projection()
        query = service.find_artifacts({"artifact_id": item["expected_artifact_id"]})
        self.assertEqual(build["build_status"], "COMPLETED")
        self.assertEqual(verify["status"], "FRESH")
        self.assertEqual(query["returned_count"], 1)

    def test_doctor_reports_missing_then_fresh_projection(self) -> None:
        service = self.make_service()
        self.import_one(service)
        missing = service.registry_status()
        self.assertEqual(missing["projection"]["status"], "MISSING")
        self.assertFalse(missing["projection_authoritative"])
        service.rebuild_projection()
        fresh = service.registry_status()
        self.assertEqual(fresh["projection"]["status"], "FRESH")
        self.assertEqual(fresh["artifact_count"], 1)

    def test_remove_and_rebuild_projection_never_changes_registry(self) -> None:
        service = self.make_service(rebuild=True)
        self.import_one(service)
        service.rebuild_projection()
        before = registry_baseline(self.registry_root)
        removal = service.doctor.remove_projection_for_rebuild()
        self.assertEqual(removal["status"], "REMOVED_FOR_REBUILD")
        self.assertEqual(removal["registry_writes"], 0)
        result = service.find_artifacts({})
        self.assertEqual(result["returned_count"], 1)
        self.assertEqual(before, registry_baseline(self.registry_root))

    def test_human_summary_uses_typed_result_digest(self) -> None:
        service = self.make_service()
        self.import_one(service)
        service.rebuild_projection()
        result = service.find_artifacts({})
        rendered = human_summary(result)
        self.assertIn(result["result_digest"], rendered)
        self.assertIn("find_artifacts", rendered)

    def test_cli_status_json_is_same_doctor_result_shape(self) -> None:
        service = self.make_service()
        self.import_one(service)
        service.rebuild_projection()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(
                [
                    "--sandbox-root",
                    str(self.root),
                    "--registry-root",
                    str(self.registry_root),
                    "--projection",
                    str(self.root / "projection" / "index.sqlite"),
                    "--json",
                    "status",
                ]
            )
        value = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(value["artifact_count"], 1)
        self.assertEqual(value["projection"]["status"], "FRESH")

    def test_cli_find_human_output_includes_same_digest(self) -> None:
        service = self.make_service()
        item, _result = self.import_one(service)
        service.rebuild_projection()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(
                [
                    "--sandbox-root",
                    str(self.root),
                    "--registry-root",
                    str(self.registry_root),
                    "--projection",
                    str(self.root / "projection" / "index.sqlite"),
                    "find",
                    "--artifact-id",
                    item["expected_artifact_id"],
                ]
            )
        self.assertEqual(code, 0)
        self.assertIn("kind=find_artifacts", output.getvalue())
        self.assertIn("digest=", output.getvalue())

    def test_all_four_task_schemas_are_valid_draft_2020_12(self) -> None:
        schema_root = (Path(__file__).resolve().parents[3] / "source" / "memorive" / "documents" / "registry") / "schemas"
        paths = sorted(schema_root.glob("asset_migration_*.schema.json"))
        self.assertEqual(len(paths), 4)
        for path in paths:
            with self.subTest(path=path.name):
                Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))

    def test_maintenance_facade_has_no_production_or_reverse_write_shortcut(self) -> None:
        service = self.make_service()
        forbidden = {
            "activate_production",
            "write_production_registry",
            "reverse_write_projection",
            "move_canonical",
            "update_latest",
        }
        self.assertTrue(all(not hasattr(service, name) for name in forbidden))

    def test_exact_product_surface_is_twelve_paths(self) -> None:
        package = (Path(__file__).resolve().parents[3] / "source" / "memorive" / "documents" / "registry")
        exact = {
            package / "migration_contracts.py",
            package / "migration.py",
            package / "query_projection.py",
            package / "maintenance.py",
            package / "schemas" / "asset_migration_migration_batch_v1.schema.json",
            package / "schemas" / "asset_migration_migration_receipt_v1.schema.json",
            package / "schemas" / "asset_migration_query_projection_v1.schema.json",
            package / "schemas" / "asset_migration_query_result_v1.schema.json",
            Path(__file__).resolve().parent / "test_migration.py",
            Path(__file__).resolve().parent / "test_query_projection.py",
            Path(__file__).resolve().parent / "test_fault_matrix.py",
            Path(__file__).resolve().parent / "test_maintenance.py",
        }
        self.assertEqual(len(exact), 12)
        self.assertTrue(all(path.is_file() for path in exact))


if __name__ == "__main__":
    unittest.main()
