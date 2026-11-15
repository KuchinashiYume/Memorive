from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from model_gateway.local_reranker.doctor import reranker_doctor
from tests.modules.model_gateway.local_reranker_fixture import make_fake_manifest


class LocalRerankerDoctorTests(unittest.TestCase):
    def test_exact_asset_runtime_and_license_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = make_fake_manifest(root)
            receipt = reranker_doctor(manifest)
            self.assertEqual(receipt["verdict"], "PASS")
            self.assertEqual(receipt["physical_model_starts"], 0)
            self.assertEqual(receipt["external_requests"], 0)

    def test_member_drift_fails_without_starting_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = make_fake_manifest(root)
            (Path(manifest["model_root"]) / "dummy.bin").write_bytes(b"changed")
            receipt = reranker_doctor(manifest)
            self.assertEqual(receipt["verdict"], "FAIL")
            self.assertEqual(receipt["physical_model_starts"], 0)


if __name__ == "__main__":
    unittest.main()
