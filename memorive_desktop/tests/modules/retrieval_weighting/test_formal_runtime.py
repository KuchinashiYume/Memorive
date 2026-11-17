from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from memorive.research.ranking.candidate_index import build_candidate_index
from memorive.platform.jcs import canonical_json_bytes
from memorive.research.ranking.contracts import make_hashed_payload
from memorive.research.ranking.field_registry import get_field_registry
from memorive.research.ranking.field_vectors import build_field_vector_records
from memorive.research.ranking.locator import validate_locator
from memorive.research.ranking.source_snapshot import build_source_snapshot
from tests.modules.retrieval_weighting.current_field_records import stable_fields
REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
FIXTURE_ROOT = Path(__file__).resolve().parent / "field_vector_acceptance" / "fixtures"
ORACLE = FIXTURE_ROOT / "reference_oracle_v1" / "expected_records.jsonl"
BUILD_CUTOFF = "2026-08-03T00:00:00Z"
# The run roots are made in the temp folder, so running the tests leaves no folder in the checkout.


def _load(name: str) -> dict:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def _canonical_jsonl(rows: list[dict]) -> bytes:
    return b"".join(canonical_json_bytes(row) + b"\n" for row in rows)


def _oracle_rows() -> list[dict]:
    return [json.loads(line) for line in ORACLE.read_text(encoding="utf-8").splitlines() if line]


def _bundle() -> dict:
    fixture_pack = _load("source_snapshot_public_safe.json")
    fixture = next(
        row
        for row in fixture_pack["records"]
        if row["fixture_id"] == "FX-ACTIVE-SELF-LITERATURE-R1"
    )
    metadata = _load("doc_type_metadata_public_safe.json")["records"]
    snapshot = build_source_snapshot(
        records=[fixture],
        doc_type_metadata=metadata,
        build_cutoff_at=BUILD_CUTOFF,
        public_safe=True,
    )
    registry = get_field_registry()
    profile = {
        "embedding_profile_id": "offline:fixture-v1-d4",
        "embedding_model_id": "fixture-hash-vector-v2",
        "embedding_dimension": 4,
        "embedding_normalization": "l2",
        "index_version": 1,
    }
    identity = make_hashed_payload(
        {
            "index_id": "offline:index-v1-d4",
            "version": 1,
            "collection_id": "offline:candidate-v1-d4",
            "build_cutoff_at": BUILD_CUTOFF,
            "vector_type": "card_field",
            "field_registry_id": registry["registry_id"],
            "field_registry_version": registry["revision"],
            "field_registry_hash": registry["content_hash"]["value"],
            "production_eligible": False,
        }
    )
    vectors = build_field_vector_records(
        source_snapshot=snapshot,
        profile=profile,
        index_id=identity["index_id"],
        index_version=identity["version"],
        collection_id=identity["collection_id"],
    )
    return {
        "snapshot": snapshot,
        "profile": profile,
        "identity": identity,
        "records": vectors["records"],
    }


def _absent_baseline(root: Path) -> dict:
    authority = b"FIELD-VECTORS public-safe formal runtime absent-index baseline"
    return {
        "authority_ref": "formal-runtime://research-service_contracts/absent-active-index",
        "authority_raw_sha256": hashlib.sha256(authority).hexdigest().upper(),
        "declared_locator": str((root / "absent_active_index").resolve()),
        "exists": False,
        "member_count": 0,
        "bytes": 0,
        "tree_sha256": None,
        "read_only": True,
    }


@contextmanager
def _governed_run_root(kind: str):
    prefix = f"field_vectors_cross_field_vector_v1_formal_runtime_{kind}_"
    with tempfile.TemporaryDirectory(prefix=prefix) as temporary:
        root = Path(temporary).resolve()
        manifest_dir = root / "00_manifest"
        manifest_dir.mkdir()
        start_manifest = {
            "schema_version": "FIELD_VECTORS_FORMAL_RUNTIME_START_V1",
            "task_id": "ServiceContracts",
            "module_owner": "CROSS",
            "sandbox_root": str(root),
            "public_safe": True,
            "synthetic_only": True,
            "production_activation": False,
        }
        (manifest_dir / "start_manifest.json").write_bytes(
            canonical_json_bytes(start_manifest) + b"\n"
        )
        yield root


class FormalRuntimeTests(unittest.TestCase):
    def test_public_fixture_build_matches_frozen_oracle(self) -> None:
        bundle = _bundle()
        built = _canonical_jsonl(bundle["records"])
        self.assertEqual(len(bundle["records"]), 17)
        self.assertEqual(stable_fields(bundle["records"]), stable_fields(_oracle_rows()))
        self.assertEqual(_canonical_jsonl(_bundle()["records"]), built)
        with _governed_run_root("api") as root:
            destination = root / "10_candidate" / "index_api"
            destination.parent.mkdir(parents=True)
            locator = build_candidate_index(
                bundle["records"],
                bundle["profile"],
                bundle["identity"],
                destination,
                owned_root=root,
                source_snapshot=bundle["snapshot"],
                active_index_baseline=_absent_baseline(root),
            )
            checked = validate_locator(locator, root)
            manifest = json.loads(
                (Path(checked["root"]) / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual((Path(checked["root"]) / "records.jsonl").read_bytes(), built)
            self.assertEqual(manifest["state"], "immutable_candidate")
            self.assertFalse(manifest["activation"])
            self.assertFalse(manifest["production_eligible"])

    def test_explicit_path_cli_builds_inside_owned_root(self) -> None:
        bundle = _bundle()
        with _governed_run_root("cli") as root:
            request_dir = root / "10_request"
            candidate_dir = root / "10_candidate"
            receipt_dir = root / "20_receipt"
            for directory in (request_dir, candidate_dir, receipt_dir):
                directory.mkdir(parents=True)
            request = {
                "operation": "build",
                "records": bundle["records"],
                "profile": bundle["profile"],
                "index_identity": bundle["identity"],
                "candidate_relative_path": "10_candidate/index_cli",
                "source_snapshot": bundle["snapshot"],
                "active_index_baseline": _absent_baseline(root),
            }
            request_path = request_dir / "build.json"
            receipt_path = receipt_dir / "build_receipt.json"
            request_path.write_bytes(canonical_json_bytes(request) + b"\n")
            environment = dict(os.environ)
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            environment["PYTHONPATH"] = str(REPO_ROOT)
            process = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "memorive.research.ranking",
                    "--input",
                    str(request_path),
                    "--output",
                    str(receipt_path),
                    "--root",
                    str(root),
                    "--cutoff",
                    BUILD_CUTOFF,
                ],
                cwd=REPO_ROOT,
                env=environment,
                capture_output=True,
                check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr.decode("utf-8", errors="replace"))
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt["verification_result"], "PASS")
            self.assertEqual(receipt["acceptance_verdict"], "NOT_ASSESSED")
            self.assertFalse(receipt["production_eligible"])
            self.assertFalse(receipt["activation"])
            self.assertEqual(
                (Path(receipt["locator"]["root"]) / "records.jsonl").read_bytes(),
                _canonical_jsonl(bundle["records"]),
            )


if __name__ == "__main__":
    unittest.main()
