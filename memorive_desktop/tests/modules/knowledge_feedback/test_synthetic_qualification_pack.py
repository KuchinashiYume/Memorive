from __future__ import annotations

import unittest

from memorive.sessions.refinement.contracts import DECISION_STATES, DESTINATIONS, REFINEMENT_TYPES
from memorive.sessions.refinement.contracts import make_selection_receipt
from memorive.sessions.refinement.destination import TYPE_DESTINATION, make_destination_projection
from memorive.sessions.refinement.graph import build_normalized_graph
from memorive.sessions.refinement.provider_adapter import build_synthetic_snapshot
from memorive.sessions.refinement.refinement import PREFIX_TYPES
from memorive.sessions.refinement.refinement import deterministic_refine_graph
from tests.modules.knowledge_feedback.test_export_snapshot_contract import NOW, make_envelope, make_export, message
from memorive.sessions.refinement.decision import make_refinement_decision_receipt
FAMILIES = [
    "decision_constraint",
    "fact_evidence",
    "method_reuse",
    "suggestion_hypothesis",
    "question_context",
    "failure_overturn",
    "provenance_forbidden",
    "scope_noise",
]


def qualification_fixture() -> tuple[dict, dict, list[str], dict[str, str]]:
    prefixes = list(PREFIX_TYPES)
    conversations: list[dict] = []
    decisions: list[dict] = []
    selected_ids: list[str] = []
    family_by_message: dict[str, str] = {}
    selected_ordinal = 0
    for conversation_ordinal in range(6):
        conversation_id = f"conversation-{conversation_ordinal + 1:03d}"
        messages = []
        conversation_selected: list[str] = []
        parent: str | None = None
        for local_ordinal in range(5):
            message_id = f"message-{conversation_ordinal + 1:02d}-{local_ordinal + 1:02d}"
            if local_ordinal < 4:
                prefix = prefixes[selected_ordinal % len(prefixes)]
                text = f"{prefix} synthetic item {selected_ordinal + 1}"
                family_by_message[message_id] = FAMILIES[selected_ordinal % len(FAMILIES)]
                selected_ordinal += 1
                conversation_selected.append(message_id)
                selected_ids.append(message_id)
            else:
                text = "daily small talk that must remain outside selection"
            messages.append(message(message_id, text, parent_id=parent, role="user"))
            parent = message_id
        conversations.append(
            {
                "conversation_id": conversation_id,
                "title": f"synthetic family carrier {conversation_ordinal + 1}",
                "messages": messages,
                "attachments": [],
            }
        )
        decisions.append(
            {
                "conversation_id": conversation_id,
                "message_ids": conversation_selected,
                "state": "INCLUDE",
                "reason": "frozen synthetic qualification selection",
            }
        )
    export = make_export(conversations)
    snapshot = build_synthetic_snapshot(
        snapshot_id="snapshot-pack",
        envelope=make_envelope(conversations=6, messages=30),
        export=export,
        captured_at=NOW,
    )
    graph = build_normalized_graph(graph_id="graph-pack", snapshot=snapshot, export=export)
    selection = make_selection_receipt(
        selection_id="selection-pack",
        snapshot=snapshot,
        decisions=decisions,
        created_at=NOW,
    )
    return graph, selection, selected_ids, family_by_message


class SyntheticQualificationPackTests(unittest.TestCase):
    def test_frozen_logical_denominator_6_8_24_plus_6_noise(self) -> None:
        graph, selection, selected_ids, family_by_message = qualification_fixture()
        items = deterministic_refine_graph(graph=graph, selection_receipt=selection, scope="CONVERSATION-REFINEMENT/B")
        self.assertEqual(len(graph["conversation_ids"]), 6)
        self.assertEqual(len(FAMILIES), 8)
        self.assertEqual(len(items), 24)
        self.assertEqual(len(graph["nodes"]) - len(selected_ids), 6)
        self.assertEqual(set(family_by_message), set(selected_ids))
        self.assertEqual(set(family_by_message.values()), set(FAMILIES))
        self.assertEqual(
            {family: list(family_by_message.values()).count(family) for family in FAMILIES},
            {family: 3 for family in FAMILIES},
        )
        self.assertEqual(set(item["refinement_type"] for item in items), REFINEMENT_TYPES)
        self.assertNotIn("daily small talk", " ".join(item["candidate_text"] for item in items))

    def test_pack_replay_is_byte_semantic_deterministic(self) -> None:
        graph, selection, _, _ = qualification_fixture()
        first = deterministic_refine_graph(graph=graph, selection_receipt=selection, scope="CONVERSATION-REFINEMENT/B")
        second = deterministic_refine_graph(graph=graph, selection_receipt=selection, scope="CONVERSATION-REFINEMENT/B")
        self.assertEqual(first, second)

    def test_pack_mechanically_covers_four_decisions_and_five_destinations(self) -> None:
        graph, selection, _, _ = qualification_fixture()
        items = deterministic_refine_graph(graph=graph, selection_receipt=selection, scope="CONVERSATION-REFINEMENT/B")
        destination_carriers: dict[str, dict] = {}
        for item in items:
            destination_carriers.setdefault(TYPE_DESTINATION[item["refinement_type"]], item)
        self.assertEqual(set(destination_carriers), DESTINATIONS)
        for ordinal, (destination, item) in enumerate(sorted(destination_carriers.items()), 1):
            receipt = make_refinement_decision_receipt(
                decision_id=f"pack-accept-{ordinal:02d}",
                item=item,
                decision="ACCEPT",
                decided_by="SYNTHETIC_HUMAN_FIXTURE",
                decided_at=NOW,
                reason="qualification destination carrier",
            )
            projection = make_destination_projection(
                projection_id=f"pack-projection-{ordinal:02d}",
                item=item,
                decision_receipt=receipt,
            )
            self.assertEqual(projection["destination"], destination)

        for ordinal, decision in enumerate(sorted(DECISION_STATES)):
            receipt = make_refinement_decision_receipt(
                decision_id=f"pack-decision-{decision.lower()}",
                item=items[ordinal],
                decision=decision,
                decided_by="SYNTHETIC_HUMAN_FIXTURE",
                decided_at=NOW,
                reason="qualification decision coverage",
                edited_text="successor text remains unaccepted" if decision == "EDIT" else None,
            )
            self.assertEqual(receipt["decision"], decision)


if __name__ == "__main__":
    unittest.main()
