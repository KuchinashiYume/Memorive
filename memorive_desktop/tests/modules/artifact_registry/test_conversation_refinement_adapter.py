from __future__ import annotations

import unittest

from memorive.sessions.refinement.destination import make_destination_projection
from memorive.sessions.refinement.knowledge_feedback_adapter import make_knowledge_feedback_candidate_preview
from memorive.sessions.refinement.refinement import deterministic_refine_graph
from tests.modules.knowledge_feedback.test_synthetic_qualification_pack import qualification_fixture
from decision_log.conversation_refinement_decision import make_refinement_decision_receipt
from knowledge_admission.conversation_ingest_preview import make_conversation_ingest_preview
from artifact_registry.conversation_refinement_adapter import make_conversation_registration_preview
NOW = "2026-08-18T02:00:00+09:00"


class ConversationRefinementAdapterTests(unittest.TestCase):
    def test_feedback_decision_registry_chain_is_create_only(self) -> None:
        graph, selection, _, _ = qualification_fixture()
        items = deterministic_refine_graph(graph=graph, selection_receipt=selection, scope="CONVERSATION-REFINEMENT")
        method = next(item for item in items if item["refinement_type"] == "REUSABLE_METHOD")
        decision = make_refinement_decision_receipt(
            decision_id="decision-method",
            item=method,
            decision="ACCEPT",
            decided_by="SYNTHETIC_HUMAN_FIXTURE",
            decided_at=NOW,
            reason="qualification fixture",
        )
        projection = make_destination_projection(
            projection_id="projection-method", item=method, decision_receipt=decision
        )
        feedback_preview = make_knowledge_feedback_candidate_preview(
            preview_id="m10-preview", item=method, projection=projection
        )
        ingest = make_conversation_ingest_preview(preview_id="knowledge_admission-preview", projection=projection)
        artifact_registry = make_conversation_registration_preview(
            preview_id="artifact_registry-preview", projection=projection, ingest_preview=ingest
        )
        self.assertTrue(feedback_preview["derived"])
        self.assertTrue(feedback_preview["not_literature_card"])
        self.assertFalse(feedback_preview["materialized"])
        self.assertFalse(feedback_preview["formal_ingest_authorized"])
        self.assertEqual(artifact_registry["state_owner"], "M08")
        self.assertFalse(artifact_registry["artifact_registry_is_state_owner"])
        self.assertEqual(artifact_registry["registry_mutation_count"], 0)
        self.assertEqual(artifact_registry["query_projection_write_count"], 0)
        self.assertEqual(artifact_registry["index_mutation_count"], 0)
        self.assertFalse(artifact_registry["formal_registration_authorized"])


if __name__ == "__main__":
    unittest.main()
