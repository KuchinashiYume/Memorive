from __future__ import annotations

from run_ledger.store import LedgerStore
from tests.modules.run_ledger.common import (
    IsolatedLedgerTestCase,
    attempt_record,
    paper_outcome,
    publication_record,
)


class OrthogonalityTests(IsolatedLedgerTestCase):
    def test_attempt_success_does_not_force_paper_or_publication_success(self) -> None:
        store = LedgerStore(self.temp_root / "ledger")
        store.append(attempt_record(result_category="success"))
        store.append(
            paper_outcome(
                category="failure",
                code="SEMANTIC_REJECT",
                reasons=["SEMANTIC_REJECT"],
            )
        )
        store.append(publication_record(status="not_requested"))

        self.assertEqual(store.attempts()[0]["result_category"], "success")
        self.assertEqual(store.paper_outcomes()[0]["outcome_category"], "failure")
        self.assertEqual(store.publications()[0]["publication_status"], "not_requested")

    def test_publication_failure_does_not_rewrite_paper_success(self) -> None:
        store = LedgerStore(self.temp_root / "ledger")
        store.append(paper_outcome(category="success"))
        store.append(publication_record(status="publication_failed"))
        self.assertEqual(store.paper_outcomes()[0]["outcome_category"], "success")
        self.assertEqual(
            store.publications()[0]["publication_status"], "publication_failed"
        )
