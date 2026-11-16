from __future__ import annotations

import unittest
from copy import deepcopy

from memorive.sessions.refinement.contracts import ConversationContractError
from memorive.sessions.refinement.refinement import deterministic_refine_graph
from tests.modules.knowledge_feedback.test_typed_refinement import refinement_fixture
from memorive.sessions.refinement.decision import assert_decision_binds_item, make_refinement_decision_receipt, validate_refinement_decision_receipt
NOW = "2026-08-18T02:00:00+09:00"


def first_item() -> dict:
    graph, selection = refinement_fixture()
    return deterministic_refine_graph(graph=graph, selection_receipt=selection, scope="CONVERSATION-REFINEMENT")[0]


class RefinementDecisionTests(unittest.TestCase):
    def test_accept_binds_exact_item_without_ingest_authority(self) -> None:
        item = first_item()
        receipt = make_refinement_decision_receipt(
            decision_id="decision-001",
            item=item,
            decision="ACCEPT",
            decided_by="SYNTHETIC_HUMAN_FIXTURE",
            decided_at=NOW,
            reason="qualification fixture",
        )
        self.assertTrue(assert_decision_binds_item(receipt, item)["accepted_for_projection"])
        self.assertFalse(receipt["real_ingest_authorized"])

    def test_edit_never_auto_accepts(self) -> None:
        item = first_item()
        receipt = make_refinement_decision_receipt(
            decision_id="decision-edit",
            item=item,
            decision="EDIT",
            decided_by="SYNTHETIC_HUMAN_FIXTURE",
            decided_at=NOW,
            reason="clarify scope",
            edited_text="preserve exact failed-run lineage",
        )
        self.assertIsNotNone(receipt["edited_text_hash"])
        self.assertFalse(receipt["accepted_for_projection"])
        self.assertFalse(receipt["real_ingest_authorized"])

    def test_reject_and_defer_have_no_projection_credit(self) -> None:
        item = first_item()
        for decision in ("REJECT", "DEFER"):
            with self.subTest(decision=decision):
                receipt = make_refinement_decision_receipt(
                    decision_id=f"decision-{decision.lower()}",
                    item=item,
                    decision=decision,
                    decided_by="SYNTHETIC_HUMAN_FIXTURE",
                    decided_at=NOW,
                    reason="qualification fixture",
                )
                self.assertFalse(receipt["accepted_for_projection"])

    def test_edited_receipt_is_read_and_a_missing_or_malformed_hash_is_refused(self) -> None:
        item = first_item()
        receipt = make_refinement_decision_receipt(
            decision_id="decision-tamper",
            item=item,
            decision="ACCEPT",
            decided_by="SYNTHETIC_HUMAN_FIXTURE",
            decided_at=NOW,
            reason="qualification fixture",
        )
        receipt["item_id"] = "other-item"  # the stored hash is not recomputed on read
        self.assertEqual(validate_refinement_decision_receipt(receipt)["item_id"], "other-item")
        malformed = dict(receipt, content_hash=dict(receipt["content_hash"], value="not-a-sha256"))
        with self.assertRaisesRegex(ConversationContractError, "HASH_INVALID"):
            validate_refinement_decision_receipt(malformed)
        missing = {key: value for key, value in receipt.items() if key != "content_hash"}
        with self.assertRaisesRegex(ConversationContractError, "HASH_INVALID"):
            validate_refinement_decision_receipt(missing)


if __name__ == "__main__":
    unittest.main()
