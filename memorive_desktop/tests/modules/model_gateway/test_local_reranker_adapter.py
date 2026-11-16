from __future__ import annotations

import json
from pathlib import Path
import tempfile
import textwrap
import unittest

from jsonschema import Draft202012Validator

from memorive.models.gateway.local_reranker.adapter import LocalRerankerAdapter
from tests.modules.model_gateway.local_reranker_fixture import (
    make_fake_manifest,
    provenance,
)


REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
CORE_SCHEMA_PATH = REPO_ROOT / "memorive" / "models" / "gateway" / "execution_core" / "schemas" / "execution_execution_core.schema.json"
RESOURCE_SCHEMA_PATH = REPO_ROOT / "memorive" / "models" / "gateway" / "local_reranker" / "schemas" / "resource_receipt_v1.schema.json"


def _fake_worker(path: Path, *, duplicate_index: bool = False) -> None:
    source = f'''
import argparse, hashlib, json
from pathlib import Path

def canon(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest().upper()

p = argparse.ArgumentParser()
p.add_argument("--worker", action="store_true")
p.add_argument("--input", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()
r = json.loads(a.input.read_text(encoding="utf-8"))
batches = []
for batch in r["batches"]:
    scores = []
    for document in batch["documents"]:
        index = 0 if {str(duplicate_index)} and document["index"] == 1 else document["index"]
        scores.append({{"index": index, "candidate_id": document["candidate_id"], "text_sha256": document["text_sha256"], "provenance_sha256": document["provenance_sha256"], "model_input_sha256": document["model_input_sha256"], "score": 1.0 - document["index"] * 0.1}})
    batches.append({{"query_id": batch["query_id"], "scores": scores}})
result = {{
    "schema_version": "RetrievalCalibrationLocalRerankerResult-v2",
    "worker_version": "RetrievalCalibration_QWEN3_LOGIT_WORKER_v2",
    "request_sha256": r["request_sha256"],
    "identity": {{**r["model"], "transformers_version": "fake", "torch_version": "fake", "dtype": "fake", "device": "cpu"}},
    "batches": batches,
    "resources": {{"cold_load_seconds": 0.01, "warm_query_seconds_p50": 0.01, "warm_query_seconds_p95": 0.01, "sub_batch_seconds_p50": 0.01, "sub_batch_seconds_p95": 0.01, "peak_rss_bytes_sampled": 1, "peak_vram_allocated_bytes": None, "peak_vram_reserved_bytes": None, "cpu_percent_process_time_over_wall": 1.0, "gpu_utilization_percent": None, "gpu_utilization_status": "NOT_MEASURED", "device_name": "fake", "query_count": len(batches), "document_count": sum(len(x["scores"]) for x in batches), "input_token_estimate_max": 1, "timeout_count": 0, "oom_count": 0, "physical_starts": 1, "resource_sampling_scope": "FAKE_TEST"}},
    "side_effects": {{"external_requests": 0, "network_connect_attempts": 0, "cloud_fallback_started": False, "document_mutations": 0}},
}}
result["result_sha256"] = canon(result)
a.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True), encoding="utf-8")
'''
    path.write_text(textwrap.dedent(source), encoding="utf-8")


class LocalRerankerAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.core_schema = json.loads(CORE_SCHEMA_PATH.read_text(encoding="utf-8"))
        cls.resource_schema = json.loads(RESOURCE_SCHEMA_PATH.read_text(encoding="utf-8"))

    def test_isolated_process_and_atomic_valid_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            worker = root / "worker.py"
            _fake_worker(worker)
            manifest = make_fake_manifest(root)
            jobs = root / "jobs"
            jobs.mkdir()
            outcome = LocalRerankerAdapter(self.core_schema, worker_script=worker).execute(
                [{"query_id": "Q1", "query": "answer?", "documents": [{"candidate_id": "C1", "text": "answer", "provenance": provenance()}, {"candidate_id": "C2", "text": "noise", "provenance": provenance(source_role="references")}]}],
                manifest,
                workspace_parent=jobs,
                protected_roots=(),
                job_id="job001",
                attempt_id="attempt001",
                timeout_seconds=30,
            )
            self.assertTrue(outcome.success)
            self.assertEqual(outcome.physical_starts, 1)
            self.assertTrue(outcome.process_receipt["process_started"])
            self.assertIn("USERNAME", outcome.workspace_manifest["environment_allowlist"])
            self.assertIn("TORCHINDUCTOR_CACHE_DIR", outcome.workspace_manifest["environment_allowlist"])
            self.assertEqual(outcome.score_batches[0]["scores"][1]["index"], 1)
            self.assertEqual(outcome.resource_receipt["side_effects"]["external_requests"], 0)
            Draft202012Validator(self.resource_schema).validate(outcome.resource_receipt)

    def test_duplicate_index_rejects_entire_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            worker = root / "worker.py"
            _fake_worker(worker, duplicate_index=True)
            manifest = make_fake_manifest(root)
            jobs = root / "jobs"
            jobs.mkdir()
            outcome = LocalRerankerAdapter(self.core_schema, worker_script=worker).execute(
                [{"query_id": "Q1", "query": "answer?", "documents": [{"candidate_id": "C1", "text": "answer", "provenance": provenance()}, {"candidate_id": "C2", "text": "noise", "provenance": provenance(source_role="references")}]}],
                manifest,
                workspace_parent=jobs,
                protected_roots=(),
                job_id="job002",
                attempt_id="attempt002",
                timeout_seconds=30,
            )
            self.assertFalse(outcome.success)
            self.assertIn(outcome.error_code, {"SCORE_INDEX_MISMATCH", "SCORE_INDEX_OR_DOCUMENT_MISMATCH"})
            self.assertEqual(outcome.score_batches, ())


if __name__ == "__main__":
    unittest.main()
