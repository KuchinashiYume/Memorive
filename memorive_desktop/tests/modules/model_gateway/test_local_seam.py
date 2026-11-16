"""Offline contract and DataContracts/ServiceContracts integration tests for LOCAL-RETRIEVAL."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest


REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
sys.path.insert(0, str(REPO_ROOT))
CORE_SCHEMA_PATH = REPO_ROOT / "memorive_workflow_bridge" / "contracts" / "execution_execution_core.schema.json"
CORE_SCHEMA = json.loads(CORE_SCHEMA_PATH.read_text(encoding="utf-8"))
FAKE_WORKER = Path(__file__).resolve().with_name(
    "fake_local_embedding_worker.py"
)
FIXED_TIME = "2026-08-14T14:00:00Z"
FIXED_CLOCK = lambda: FIXED_TIME

from run_ledger.channel_evidence import ChannelAttemptBuilder, validate_channel_attempt
from memorive.models.gateway.local_embedding.worker import WorkerError, validate_request
from memorive.models.gateway.local_embedding import LocalEmbeddingSeam, LocalSeamViolation, build_compatibility_manifest, validate_compatibility_manifest
def compatibility_manifest(generation_root: Path, *, clock=FIXED_CLOCK) -> dict:
    return build_compatibility_manifest(
        manifest_id="local_seam_test_manifest",
        runtime_version="0.32.9",
        executable_sha256="0" * 64,
        endpoint="http://127.0.0.1:11434/api/embed",
        requested_model="bge-m3:latest",
        canonical_model="bge-m3",
        model_manifest_sha256="1" * 64,
        model_layer_digest="2" * 64,
        dimension=3,
        chroma_version="1.0-test",
        generation_root=generation_root,
        reranker_mode="OFF",
        source_evidence_refs=("fixture://local_retrieval-authority",),
        clock=clock,
    )


def worker_request() -> dict:
    return {
        "schema_version": "LOCAL_RETRIEVAL_LOCAL_EMBEDDING_WORKER_REQUEST_V1",
        "endpoint": "http://127.0.0.1:11434/api/embed",
        "model": "bge-m3:latest",
        "model_manifest_sha256": "1" * 64,
        "expected_canonical_model": "bge-m3",
        "expected_dimension": 3,
        "normalization": "L2_UNIT",
        "normalization_min": 0.999,
        "normalization_max": 1.001,
        "max_batch_size": 4,
        "max_input_utf8_bytes": 16384,
        "timeout_seconds": 60,
        "behavior_hash": "3" * 64,
        "texts": ["public-safe synthetic fixture"],
    }



def _skip_on_non_ascii_temp(testcase: unittest.TestCase) -> None:
    # The seam refuses a Chroma generation root with non-ASCII characters (Chroma cannot use
    # such paths), and these tests put that root under the system temp folder.
    if any(ord(character) > 127 for character in tempfile.gettempdir()):
        testcase.skipTest("the temp folder path has non-ASCII characters; Chroma paths must be ASCII")


class CompatibilityManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        _skip_on_non_ascii_temp(self)

    def test_identity_is_stable_across_timestamp_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="local_seam_manifest_") as temp:
            root = Path(temp) / "generation"
            first = compatibility_manifest(root, clock=lambda: "2026-08-14T14:00:00Z")
            second = compatibility_manifest(root, clock=lambda: "2026-08-14T14:01:00Z")
        self.assertNotEqual(first["created_at"], second["created_at"])
        self.assertEqual(
            first["manifest_identity_sha256"], second["manifest_identity_sha256"]
        )
        self.assertEqual(
            first["vector_compatibility_key_sha256"],
            second["vector_compatibility_key_sha256"],
        )

    def test_schema_tamper_and_remote_endpoint_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="local_seam_manifest_") as temp:
            manifest = compatibility_manifest(Path(temp) / "generation")
        extra = deepcopy(manifest)
        extra["unapproved"] = True
        with self.assertRaisesRegex(
            LocalSeamViolation, "COMPATIBILITY_MANIFEST_SCHEMA_INVALID"
        ):
            validate_compatibility_manifest(extra)
        remote = deepcopy(manifest)
        remote["runtime"]["endpoint"] = "https://example.test/api/embed"
        with self.assertRaisesRegex(
            LocalSeamViolation, "LOCAL_ENDPOINT_NOT_EXACT_LOOPBACK"
        ):
            validate_compatibility_manifest(remote)
        changed = deepcopy(manifest)
        changed["model"]["dimension"] = 4
        with self.assertRaisesRegex(
            LocalSeamViolation, "VECTOR_COMPATIBILITY_KEY_MISMATCH"
        ):
            validate_compatibility_manifest(changed)


class WorkerRequestTests(unittest.TestCase):
    def test_remote_oversized_and_extra_requests_are_rejected(self) -> None:
        remote = worker_request()
        remote["endpoint"] = "http://example.test:11434/api/embed"
        with self.assertRaises(WorkerError) as caught:
            validate_request(remote)
        self.assertEqual(caught.exception.code, "LOCAL_ENDPOINT_NOT_EXACT_LOOPBACK")

        oversized = worker_request()
        oversized["texts"] = ["a", "b", "c", "d", "e"]
        with self.assertRaises(WorkerError) as caught:
            validate_request(oversized)
        self.assertEqual(caught.exception.code, "WORKER_TEXT_FIXTURE_INVALID")

        extra = worker_request()
        extra["retry"] = 1
        with self.assertRaises(WorkerError) as caught:
            validate_request(extra)
        self.assertEqual(caught.exception.code, "WORKER_REQUEST_FIELDS_INVALID")


class LocalEmbeddingSeamTests(unittest.TestCase):
    def setUp(self) -> None:
        _skip_on_non_ascii_temp(self)
        self.temporary = tempfile.TemporaryDirectory(prefix="local_seam_")
        self.root = Path(self.temporary.name)
        self.workspace_parent = self.root / "workspaces"
        self.workspace_parent.mkdir()
        self.manifest = compatibility_manifest(self.root / "generation")
        self.seam = LocalEmbeddingSeam(
            CORE_SCHEMA, worker_script=FAKE_WORKER, clock=FIXED_CLOCK
        )
        self.builder = ChannelAttemptBuilder(CORE_SCHEMA, clock=FIXED_CLOCK)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def execute(self, texts: list[str], *, job_id: str, attempt_id: str):
        return self.seam.execute(
            texts,
            self.manifest,
            workspace_parent=self.workspace_parent,
            protected_roots=(REPO_ROOT,),
            job_id=job_id,
            attempt_id=attempt_id,
            data_ownership="self",
        )

    def test_happy_path_flows_through_handoff_and_channel_record(self) -> None:
        outcome = self.execute(
            ["synthetic alpha", "synthetic beta"],
            job_id="job_local_happy",
            attempt_id="attempt_local_happy",
        )
        self.assertTrue(outcome.success)
        self.assertTrue(outcome.request_started)
        self.assertEqual(len(outcome.embeddings), 2)
        self.assertTrue(all(len(vector) == 3 for vector in outcome.embeddings))
        self.assertIsNotNone(outcome.model_receipt)
        self.assertEqual(outcome.model_receipt["evidence_class"], "local_compute")
        self.assertFalse(outcome.model_receipt["external_request_started"])
        self.assertEqual(outcome.model_receipt["usage_status"], "N/A_LOCAL_COMPUTE")
        self.assertTrue(all(value is None for value in outcome.model_receipt["token_usage"].values()))
        self.assertEqual(outcome.model_receipt["cost_status"], "N/A_LOCAL_COMPUTE")
        self.assertIsNone(outcome.model_receipt["cost_value"])

        record = self.builder.build(outcome.handoff, outcome.channel_binding)
        validate_channel_attempt(record)
        self.assertEqual(record["channel_evidence"]["evidence_class"], "local_compute")
        self.assertEqual(record["result_category"], "success")
        self.assertTrue(record["channel_evidence"]["process_started"])
        self.assertFalse(record["channel_evidence"]["external_request_started"])
        self.assertEqual(record["provider_identity"]["returned_model"], "bge-m3:latest")
        self.assertEqual(record["accounting"]["usage_status"], "N/A_LOCAL_COMPUTE")
        self.assertEqual(record["cost"], {
            "status": "N/A_LOCAL_COMPUTE", "value": None, "currency": None
        })

    def test_wrong_dimension_is_terminal_failure_and_preserves_started_attempt(self) -> None:
        outcome = self.execute(
            ["__FAKE_EMBEDDING_WRONG_DIMENSION__"],
            job_id="job_local_wrong_dimension",
            attempt_id="attempt_local_wrong_dimension",
        )
        self.assertFalse(outcome.success)
        self.assertEqual(
            outcome.error_code, "LOCAL_EMBEDDING_VECTOR_DIMENSION_MISMATCH"
        )
        self.assertTrue(outcome.request_started)
        self.assertEqual(outcome.embeddings, ())
        record = self.builder.build(outcome.handoff, outcome.channel_binding)
        validate_channel_attempt(record)
        self.assertEqual(record["result_category"], "error")
        self.assertTrue(record["channel_evidence"]["process_started"])
        self.assertFalse(record["channel_evidence"]["external_request_started"])

    def test_worker_failure_before_model_start_has_no_model_receipt(self) -> None:
        outcome = self.execute(
            ["__FAKE_EMBEDDING_NO_MODEL_START__"],
            job_id="job_local_no_model_start",
            attempt_id="attempt_local_no_model_start",
        )
        self.assertFalse(outcome.success)
        self.assertFalse(outcome.request_started)
        self.assertIsNone(outcome.model_receipt)
        record = self.builder.build(outcome.handoff, outcome.channel_binding)
        validate_channel_attempt(record)
        self.assertEqual(record["result_category"], "error")
        self.assertTrue(record["channel_evidence"]["process_started"])
        self.assertFalse(record["channel_evidence"]["external_request_started"])
        self.assertIsNone(record["channel_evidence"]["model_receipt_ref"])

    def test_returned_model_and_terminal_binding_mismatch_fail_closed(self) -> None:
        model = self.execute(
            ["__FAKE_EMBEDDING_RETURNED_MODEL_MISMATCH__"],
            job_id="job_local_model_mismatch",
            attempt_id="attempt_local_model_mismatch",
        )
        self.assertFalse(model.success)
        self.assertEqual(model.error_code, "LOCAL_EMBEDDING_RETURNED_MODEL_MISMATCH")

        binding = self.execute(
            ["__FAKE_EMBEDDING_TERMINAL_BINDING_MISMATCH__"],
            job_id="job_local_binding_mismatch",
            attempt_id="attempt_local_binding_mismatch",
        )
        self.assertFalse(binding.success)
        self.assertEqual(binding.error_code, "LOCAL_MODEL_TERMINAL_BINDING_MISMATCH")

    def test_retry_is_rejected_before_workspace_creation(self) -> None:
        before = tuple(self.workspace_parent.iterdir())
        with self.assertRaisesRegex(
            LocalSeamViolation, "LOCAL_EMBEDDING_RETRY_NOT_AUTHORIZED"
        ):
            self.seam.execute(
                ["synthetic"],
                self.manifest,
                workspace_parent=self.workspace_parent,
                protected_roots=(REPO_ROOT,),
                job_id="job_local_retry",
                attempt_id="attempt_local_retry",
                attempt_no=2,
                retry_of_attempt_id="attempt_local_first",
                data_ownership="self",
            )
        self.assertEqual(tuple(self.workspace_parent.iterdir()), before)

    def test_unknown_ownership_is_rejected_before_workspace_creation(self) -> None:
        before = tuple(self.workspace_parent.iterdir())
        with self.assertRaisesRegex(
            LocalSeamViolation, "LOCAL_EMBEDDING_OWNERSHIP_INVALID"
        ):
            self.seam.execute(
                ["synthetic"],
                self.manifest,
                workspace_parent=self.workspace_parent,
                protected_roots=(REPO_ROOT,),
                job_id="job_local_ownership",
                attempt_id="attempt_local_ownership",
                data_ownership="unknown",
            )
        self.assertEqual(tuple(self.workspace_parent.iterdir()), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
