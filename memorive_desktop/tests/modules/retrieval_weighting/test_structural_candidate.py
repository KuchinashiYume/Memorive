from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

from memorive.research.ranking.errors import ContractViolation
from memorive.research.ranking.structural_candidate import build_structural_observed
from tests.modules.retrieval_weighting.current_field_records import structural_inputs
INJECTIONS = Path(__file__).resolve().parent / "fixtures" / "structural" / "structural_injections.json"


def _raw(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _build(query: dict | None = None) -> dict:
    pack, records, pack_sha, export_sha = structural_inputs()
    return build_structural_observed(
        pack if query is None else query,
        records,
        json.loads(INJECTIONS.read_text(encoding="utf-8")),
        query_pack_sha256=pack_sha,
        field_export_sha256=export_sha,
        injection_fixture_sha256=_raw(INJECTIONS),
    )


class StructuralCandidateTests(unittest.TestCase):
    def test_actual_public_inputs_build_eleven_rows_including_honest_underfill(self) -> None:
        result = _build()
        self.assertEqual(len(result["queries"]), 11)
        by_id = {row["query_id"]: row for row in result["queries"]}
        self.assertEqual(
            by_id["SA-Q10-FILTERED-UNDERFILL"]["retrieval_outcome"],
            "UNDERFILLED",
        )
        self.assertIn(
            "OUT_OF_EXPLICIT_SCOPE",
            by_id["SA-Q10-FILTERED-UNDERFILL"]["underfill_reasons"],
        )
        self.assertEqual(
            by_id["SA-Q11-COUNTER-UNDERFILL"]["observed_channels"],
            ["counterevidence"],
        )
        self.assertEqual(
            by_id["SA-Q08-PROVENANCE"]["key_original_harmful_displacement"],
            0,
        )
        self.assertEqual(by_id["SA-Q09-DIVERSITY"]["distinct_sources"], 3)

    def test_same_public_inputs_are_deterministic_and_semantic_claims_stay_closed(self) -> None:
        self.assertEqual(_build(), _build())
        result = _build()
        self.assertFalse(result["semantic_B_eligible"])
        self.assertEqual(result["field_semantic_retrieval_quality"], "NOT_ASSESSED")
        self.assertTrue(all(value == 0 for value in result["safety"].values()))

    def test_query_injection_hash_drift_fails_closed_without_gold_input(self) -> None:
        query = structural_inputs()[0]
        query["structural_injection_fixture_sha256"] = "F" * 64
        with self.assertRaisesRegex(ContractViolation, "injection hash drift"):
            _build(query)


if __name__ == "__main__":
    unittest.main()
