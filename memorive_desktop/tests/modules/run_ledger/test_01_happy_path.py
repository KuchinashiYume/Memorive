from __future__ import annotations

import copy

from run_ledger.store import LedgerStore
from run_ledger.errors import SchemaValidationError
from tests.modules.run_ledger.common import (
    IsolatedLedgerTestCase,
    RUN_ID,
    attempt_record,
    paper_outcome,
    publication_record,
    run_terminal_event,
)


class HappyPathTests(IsolatedLedgerTestCase):
    def test_three_records_share_a_run_but_remain_separate_views(self) -> None:
        store = LedgerStore(self.temp_root / "ledger")
        records = [
            attempt_record(),
            paper_outcome(),
            publication_record(),
            run_terminal_event(),
        ]
        self.assertEqual([store.append(record) for record in records], ["appended"] * 4)

        self.assertEqual(len(store.attempts(run_id=RUN_ID)), 1)
        self.assertEqual(len(store.paper_outcomes(run_id=RUN_ID)), 1)
        self.assertEqual(len(store.publications(run_id=RUN_ID)), 1)
        self.assertEqual(store.current_run_terminal(RUN_ID)["acceptance_verdict"], "PASS")
        self.assertEqual((self.temp_root / "ledger" / "2026.jsonl").read_bytes().count(b"\n"), 4)

    def test_not_requested_requires_explicit_nulls_and_absence_reason(self) -> None:
        store = LedgerStore(self.temp_root / "ledger")
        valid = publication_record(status="not_requested")
        self.assertEqual(store.append(valid), "appended")
        visible = store.publications()[0]
        self.assertIsNone(visible["manifest_ref"])
        self.assertIsNone(visible["receipt_ref"])
        self.assertEqual(visible["absence_reason"], "NO_PUBLICATION_REQUEST")

        for field, value in (
            ("manifest_ref", "manifest://fabricated"),
            ("receipt_ref", "publication-receipt://fabricated"),
            ("absence_reason", None),
        ):
            invalid = copy.deepcopy(valid)
            invalid["record_id"] = f"invalid:{field}"
            invalid["publication_event_id"] = invalid["record_id"]
            invalid[field] = value
            with self.assertRaises(SchemaValidationError):
                store.append(invalid)

    def test_published_requires_real_publication_references(self) -> None:
        store = LedgerStore(self.temp_root / "ledger")
        self.assertEqual(store.append(publication_record(status="published")), "appended")
        publication = store.publications()[0]
        self.assertEqual(publication["publication_status"], "published")
        self.assertTrue(publication["manifest_ref"])
        self.assertTrue(publication["receipt_ref"])
