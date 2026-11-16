from __future__ import annotations

import unittest

from memorive.sessions.refinement.batch import compare_replay, make_batch_preview, make_rollback_receipt
from memorive.sessions.refinement.contracts import ConversationContractError
from memorive.sessions.refinement.destination import make_destination_projection
from memorive.sessions.refinement.refinement import deterministic_refine_graph
from tests.modules.knowledge_feedback.test_typed_refinement import refinement_fixture
from decision_log.conversation_refinement_decision import make_refinement_decision_receipt
from memorive.documents.card_states.conversation_ingest_preview import make_conversation_ingest_preview
NOW = "2026-08-18T02:00:00+09:00"


def accepted_projection(item: dict, ordinal: int) -> dict:
    decision = make_refinement_decision_receipt(
        decision_id=f"decision-{ordinal}",
        item=item,
        decision="ACCEPT",
        decided_by="SYNTHETIC_HUMAN_FIXTURE",
        decided_at=NOW,
        reason="qualification fixture",
    )
    return make_destination_projection(
        projection_id=f"projection-{ordinal}", item=item, decision_receipt=decision
    )


class DestinationAwarePreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        graph, selection = refinement_fixture()
        self.items = deterministic_refine_graph(graph=graph, selection_receipt=selection, scope="CONVERSATION-REFINEMENT")

    def test_type_destination_matrix_and_zero_effect_preview(self) -> None:
        projections = [accepted_projection(item, index) for index, item in enumerate(self.items)]
        self.assertEqual(
            [item["destination"] for item in projections],
            ["project_decision_candidate", "blocked_no_ingest", "provenance_only", "blocked_no_ingest"],
        )
        preview = make_conversation_ingest_preview(preview_id="ingest-preview", projection=projections[0])
        self.assertEqual(sum(preview["side_effect_counters"].values()), 0)
        self.assertEqual(preview["state_before"], preview["state_after"])
        self.assertFalse(preview["formal_ingest_authorized"])

    def test_non_accept_cannot_produce_destination(self) -> None:
        item = self.items[0]
        decision = make_refinement_decision_receipt(
            decision_id="decision-defer",
            item=item,
            decision="DEFER",
            decided_by="SYNTHETIC_HUMAN_FIXTURE",
            decided_at=NOW,
            reason="wait for evidence",
        )
        with self.assertRaisesRegex(ConversationContractError, "REQUIRES_ACCEPT"):
            make_destination_projection(projection_id="projection-blocked", item=item, decision_receipt=decision)

    def test_batch_replay_and_rollback_preserve_state(self) -> None:
        projections = [accepted_projection(item, index) for index, item in enumerate(self.items[:2])]
        first = make_batch_preview(batch_id="batch-001", projections=projections)
        second = make_batch_preview(batch_id="batch-001", projections=projections)
        self.assertEqual(compare_replay(first, second), "REPLAY_NO_OP")
        rollback = make_rollback_receipt(rollback_id="rollback-001", batch=first, reason="synthetic failure")
        self.assertFalse(rollback["prior_bytes_mutated"])
        self.assertEqual(rollback["formal_state_mutations"], 0)
        self.assertEqual(rollback["production_mutations"], 0)


if __name__ == "__main__":
    unittest.main()
