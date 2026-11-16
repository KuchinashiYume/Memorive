from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from memorive.research.discovery.connector_framework import ConnectorError, sha256_json
from memorive.research.discovery.connector_framework import CrossrefOfflineConnector, make_frozen_replay_observation, make_frozen_response_snapshot, verify_snapshot
from memorive.research.discovery.bibliographic_identity_literature_discovery_bibliographic_identity import IdentityResolutionError, resolve_identity_batch
# The repository's own Discovery contracts.
CONTRACT_ROOT = Path(__file__).resolve().parents[4] / "contracts" / "Discovery_文献发现" / "10_模块契约"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "frozen_responses" / "g1_crossref.json"


def validate_schema(token: str, value: dict) -> None:
    schema_path = next(CONTRACT_ROOT.glob(f"*_{token}_*r0.2.schema.json"))
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
        key=lambda item: list(item.path),
    )
    if errors:
        raise AssertionError("; ".join(error.message for error in errors[:3]))


class FrozenResponseReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = FIXTURE.read_bytes()
        cls.connector = CrossrefOfflineConnector({})
        cls.normalized = cls.connector._parse(cls.raw)
        cls.snapshot = make_frozen_response_snapshot(
            connector_id="CROSSREF",
            endpoint_family="CROSSREF_WORKS",
            source_schema_revision="CROSSREF_REST_WORKS_JSON",
            raw_bytes=cls.raw,
            normalized_payload=cls.normalized,
            request_id="extattempt_0123456789abcdef01234567",
            response_locator="responses/g1_crossref.json",
            captured_at="2026-08-12T00:00:01+09:00",
        )
        cls.observation = make_frozen_replay_observation(
            "CROSSREF",
            cls.snapshot,
            cls.normalized["records"][0],
            run_id="REPLAY_TEST_RUN",
        )

    def test_successor_schemas_and_identity_exact_evidence_binding(self) -> None:
        validate_schema("SourceSnapshot", self.snapshot)
        validate_schema("SourceObservation", self.observation)
        registry = {
            self.observation["object_id"]: {
                "content_hash": self.observation["content_hash"],
                "source_snapshot_id": self.observation["source_snapshot_id"],
                "snapshot_sha256": self.observation["source_evidence"]["snapshot_sha256"],
                "normalized_record_sha256": self.observation["source_evidence"]["normalized_record_sha256"],
            }
        }
        result = resolve_identity_batch([self.observation], registry)
        self.assertEqual(1, len(result["manifestations"]))
        self.assertEqual("VERIFIED", result["work_clusters"][0]["identity_status"])

    def test_raw_or_normalized_drift_fails_closed(self) -> None:
        with self.assertRaisesRegex(ConnectorError, "SNAPSHOT_HASH_MISMATCH"):
            verify_snapshot(self.snapshot, self.raw + b"\n", self.normalized)
        drifted = copy.deepcopy(self.normalized)
        drifted["records"][0]["bibliographic"]["title"] += " drift"
        with self.assertRaisesRegex(ConnectorError, "SNAPSHOT_HASH_MISMATCH"):
            verify_snapshot(self.snapshot, self.raw, drifted)

    def test_acquisition_request_binding_is_mandatory_and_hash_bound(self) -> None:
        drifted = copy.deepcopy(self.observation)
        drifted["source_evidence"].pop("acquisition_request_id")
        drifted["content_hash"] = sha256_json({key: value for key, value in drifted.items() if key != "content_hash"})
        registry = {
            drifted["object_id"]: {
                "content_hash": drifted["content_hash"],
                "source_snapshot_id": drifted["source_snapshot_id"],
                "snapshot_sha256": drifted["source_evidence"]["snapshot_sha256"],
                "normalized_record_sha256": drifted["source_evidence"]["normalized_record_sha256"],
            }
        }
        with self.assertRaisesRegex(IdentityResolutionError, "SOURCE_EVIDENCE_SCHEMA_MISMATCH"):
            resolve_identity_batch([drifted], registry)

    def test_crossref_partial_records_are_excluded_without_poisoning_page(self) -> None:
        payload = {
            "status": "ok",
            "message": {
                "total-results": 4,
                "items-per-page": 4,
                "items": [
                    {
                        "DOI": "10.99999/valid.1",
                        "title": ["Complete record"],
                        "author": [{"given": "Ada", "family": "Valid"}],
                        "published": {"date-parts": [[2026, 8, 12]]},
                    },
                    {
                        "DOI": "10.99999/missing-author",
                        "title": ["Missing author record"],
                    },
                    {
                        "DOI": "10.99999/missing-title",
                        "title": [],
                        "author": [{"given": "Terry", "family": "Titled"}],
                    },
                    {
                        "DOI": "10.99999/partial-author",
                        "title": ["Partial author list"],
                        "author": [
                            {"sequence": "additional"},
                            {"given": "Grace", "family": "Good"},
                        ],
                    },
                ],
            },
        }
        normalized = self.connector._parse(json.dumps(payload).encode("utf-8"))
        self.assertEqual(0, normalized["start_index"])
        self.assertEqual(2, len(normalized["records"]))
        self.assertEqual(
            ["CORE_AUTHORS_MISSING", "CORE_TITLE_MISSING"],
            [row["reason_codes"][0] for row in normalized["skipped_records"]],
        )
        partial = normalized["records"][1]
        self.assertEqual("Grace Good", partial["bibliographic"]["authors"][0]["display_name"])
        self.assertIn("bibliographic.authors.partial", partial["partial_fields"])
        self.assertIn("bibliographic.published_date", partial["partial_fields"])

    def test_crossref_live_query_start_index_is_authoritative(self) -> None:
        payload = {
            "status": "ok",
            "message": {
                "total-results": 100,
                "items-per-page": 1,
                "query": {"start-index": 40},
                "items": [
                    {
                        "DOI": "10.99999/live-page.41",
                        "title": ["Live page offset"],
                        "author": [{"given": "Page", "family": "FortyOne"}],
                        "published": {"date-parts": [[2026, 8, 12]]},
                    }
                ],
            },
        }
        normalized = self.connector._parse(json.dumps(payload).encode("utf-8"))
        self.assertEqual(40, normalized["start_index"])


if __name__ == "__main__":
    unittest.main()
