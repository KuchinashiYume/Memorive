from __future__ import annotations

import copy

from memorive.platform.ledger.store import LedgerStore
from memorive.platform.ledger.errors import SchemaValidationError
from memorive.platform.ledger.schema import validate_record
from tests.modules.run_ledger.common import TS, IsolatedLedgerTestCase, attempt_amendment, attempt_record


class AmendmentTests(IsolatedLedgerTestCase):
    def test_late_accounting_is_visible_without_changing_attempt_count_or_raw_base(self) -> None:
        store = LedgerStore(self.temp_root / "ledger")
        base = attempt_record(result_category="incomplete")
        frozen_base = copy.deepcopy(base)
        amendment = attempt_amendment(base)
        store.append(base)
        store.append(amendment)

        self.assertEqual(len(store.attempts()), 1)
        self.assertEqual(store.attempt_chain(base["attempt_id"])[0], frozen_base)
        current = store.current_attempt(base["attempt_id"])
        self.assertEqual(current["accounting"]["actual_cost_cny"], 1.25)
        self.assertEqual(current["_amendment_record_ids"], [amendment["record_id"]])

    def test_heartbeat_or_result_rewrite_cannot_amend_a_sealed_attempt(self) -> None:
        base = attempt_record()
        for forbidden in ({"heartbeat": TS}, {"result_category": "success"}):
            amendment = attempt_amendment(base, fields=forbidden)
            with self.assertRaises(SchemaValidationError):
                validate_record(amendment)

    def test_retry_and_business_repair_relationships_cannot_be_combined(self) -> None:
        record = attempt_record(
            retry_of_attempt_id="attempt:old",
            business_repair_of="paper-outcome:old",
        )
        with self.assertRaises(SchemaValidationError):
            validate_record(record)
