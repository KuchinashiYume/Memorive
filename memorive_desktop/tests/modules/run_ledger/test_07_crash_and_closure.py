from __future__ import annotations

from memorive.platform.ledger.closure import enforce_follow_up_policy
from memorive.platform.ledger.store import LedgerStore
from memorive.platform.ledger.errors import PaperOutcomeClosureGap, PolicyViolation, SchemaValidationError, TruncatedLedgerError
from memorive.platform.ledger.closure import validate_paper_outcome_closure
from memorive.platform.ledger.schema import validate_record
from tests.modules.run_ledger.common import IsolatedLedgerTestCase, attempt_record, paper_outcome


SAMPLE_MANIFEST = {
    "tasks": [
        {"task_id": "A", "paper_id": "PaperA"},
        {"task_id": "B", "paper_id": "PaperB"},
        {"task_id": "C", "paper_id": "PaperC"},
    ]
}


class CrashAndClosureTests(IsolatedLedgerTestCase):
    def test_truncated_tail_fails_closed_and_preserves_corruption_evidence(self) -> None:
        store = LedgerStore(self.temp_root / "ledger")
        store.append(attempt_record())
        ledger = self.temp_root / "ledger" / "2026.jsonl"
        with ledger.open("ab") as stream:
            stream.write(b'{"partial":')
        corrupted = ledger.read_bytes()

        with self.assertRaises(TruncatedLedgerError) as caught:
            store.read_records()
        self.assertEqual(ledger.read_bytes(), corrupted)
        self.assertTrue(caught.exception.evidence_path.is_file())
        self.assertIn("original_preserved", caught.exception.evidence_path.read_text("utf-8"))

    def test_missing_paper_outcome_is_a_named_closure_gap(self) -> None:
        outcomes = [paper_outcome("PaperA"), paper_outcome("PaperB")]
        with self.assertRaises(PaperOutcomeClosureGap) as caught:
            validate_paper_outcome_closure(SAMPLE_MANIFEST, outcomes)
        self.assertEqual(caught.exception.code, "PAPER_OUTCOME_CLOSURE_GAP")
        self.assertEqual(caught.exception.missing_paper_ids, ("PaperC",))

    def test_full_frozen_manifest_closes_with_explicit_not_run_reason(self) -> None:
        outcomes = [
            paper_outcome("PaperA"),
            paper_outcome("PaperB", category="failure", code="QUALITY_REJECT"),
            paper_outcome(
                "PaperC",
                category="not_run",
                code="QUALITY_GATE_EARLY_STOP",
                reasons=["QUALITY_GATE_EARLY_STOP"],
            ),
        ]
        self.assertEqual(
            validate_paper_outcome_closure(SAMPLE_MANIFEST, outcomes),
            ("PaperA", "PaperB", "PaperC"),
        )

        invalid = paper_outcome("PaperC", category="not_run", reasons=[])
        with self.assertRaises(SchemaValidationError):
            validate_record(invalid)

    def test_semantic_failure_cannot_be_resampled_as_technical_retry(self) -> None:
        with self.assertRaises(PolicyViolation):
            enforce_follow_up_policy(
                prior_business_outcome="failure",
                relation="technical_retry",
            )
        with self.assertRaises(PolicyViolation):
            enforce_follow_up_policy(
                prior_business_outcome="failure",
                relation="business_repair",
                authorized_new_run=False,
            )
        enforce_follow_up_policy(
            prior_business_outcome="failure",
            relation="business_repair",
            authorized_new_run=True,
        )
