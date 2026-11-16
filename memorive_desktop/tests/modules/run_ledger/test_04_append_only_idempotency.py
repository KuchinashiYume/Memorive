from __future__ import annotations

import copy
import json

from run_ledger import ConcurrentWriterError
from memorive.platform.ledger.errors import AppendOnlyViolation, RecordConflictError
from memorive.platform.ledger.store import LedgerStore
from tests.modules.run_ledger.common import IsolatedLedgerTestCase, attempt_record, paper_outcome


class AppendOnlyAndIdempotencyTests(IsolatedLedgerTestCase):
    def test_same_record_is_noop_and_conflicting_content_preserves_old_bytes(self) -> None:
        store = LedgerStore(self.temp_root / "ledger")
        record = attempt_record()
        self.assertEqual(store.append(record), "appended")
        path = self.temp_root / "ledger" / "2026.jsonl"
        original = path.read_bytes()
        self.assertEqual(store.append(copy.deepcopy(record)), "noop")
        self.assertEqual(path.read_bytes(), original)

        conflict = copy.deepcopy(record)
        conflict["result_category"] = "failure"
        with self.assertRaises(RecordConflictError):
            store.append(conflict)
        self.assertEqual(path.read_bytes(), original)

    def test_every_new_append_preserves_the_complete_old_prefix(self) -> None:
        store = LedgerStore(self.temp_root / "ledger")
        store.append(attempt_record())
        path = self.temp_root / "ledger" / "2026.jsonl"
        prefix = path.read_bytes()
        store.append(paper_outcome())
        self.assertTrue(path.read_bytes().startswith(prefix))

    def test_mutation_apis_and_competing_writer_are_rejected(self) -> None:
        store = LedgerStore(self.temp_root / "ledger")
        for method in (store.overwrite, store.update, store.truncate):
            with self.assertRaises(AppendOnlyViolation):
                method({})

        store.root.mkdir(parents=True, exist_ok=True)
        (store.root / ".writer.lock").write_text("pid=test\n", encoding="ascii")
        with self.assertRaises(ConcurrentWriterError):
            store.append(attempt_record())

    def test_legacy_v1_rows_are_readable_but_cannot_be_newly_appended(self) -> None:
        store = LedgerStore(self.temp_root / "ledger")
        legacy = attempt_record()
        legacy["schema_version"] = "1.0"
        legacy.pop("record_type")
        store.root.mkdir(parents=True, exist_ok=True)
        path = store.root / "2026.jsonl"
        path.write_text(
            json.dumps(legacy, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.assertEqual(len(store.attempts()), 1)

        second = copy.deepcopy(legacy)
        second["record_id"] = "attempt:legacy:new"
        second["attempt_id"] = second["record_id"]
        with self.assertRaises(Exception) as caught:
            store.append(second)
        self.assertIn("legacy v1 is read-only", str(caught.exception))
