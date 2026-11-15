from __future__ import annotations

import unittest
from copy import deepcopy

from knowledge_feedback.canonical import make_hashed_payload
from knowledge_feedback.conversation_refinement.contracts import ConversationContractError
from knowledge_feedback.conversation_refinement.graph import validate_normalized_graph
from knowledge_feedback.conversation_refinement.graph import build_normalized_graph
from knowledge_feedback.conversation_refinement.provider_adapter import build_synthetic_snapshot
from tests.modules.knowledge_feedback.test_export_snapshot_contract import NOW, make_envelope, make_export, message


def graph_fixture() -> tuple[dict, dict]:
    export = make_export(
        [
            {
                "conversation_id": "conversation-001",
                "title": "branches",
                "messages": [
                    message("document_processing", "DECISION: use exact refs"),
                    message("evidence_extraction", "SUGGESTION: add replay", parent_id="document_processing", role="assistant"),
                    message("retrieval", "OVERTURNED: old route", parent_id="missing"),
                ],
                "attachments": [],
            }
        ]
    )
    snapshot = build_synthetic_snapshot(
        snapshot_id="snapshot-graph",
        envelope=make_envelope(),
        export=export,
        captured_at=NOW,
    )
    return export, snapshot


class ConversationGraphTests(unittest.TestCase):
    def test_graph_preserves_explicit_edges_and_orphans(self) -> None:
        export, snapshot = graph_fixture()
        graph = build_normalized_graph(graph_id="graph-001", snapshot=snapshot, export=export)
        self.assertEqual(graph["orphan_message_ids"], ["retrieval"])
        self.assertEqual(graph["edge_policy"], "EXPLICIT_PARENT_ONLY_NO_TIMESTAMP_INFERENCE")
        self.assertEqual([node["message_id"] for node in graph["nodes"]], ["document_processing", "evidence_extraction", "retrieval"])

    def test_replay_is_deterministic(self) -> None:
        export, snapshot = graph_fixture()
        first = build_normalized_graph(graph_id="graph-001", snapshot=snapshot, export=export)
        second = build_normalized_graph(graph_id="graph-001", snapshot=snapshot, export=export)
        self.assertEqual(first, second)

    def test_cycle_fails_closed(self) -> None:
        export, snapshot = graph_fixture()
        export = deepcopy(export)
        export["conversations"][0]["messages"][0]["parent_id"] = "evidence_extraction"
        snapshot = build_synthetic_snapshot(
            snapshot_id="snapshot-cycle",
            envelope=make_envelope(),
            export=export,
            captured_at=NOW,
        )
        with self.assertRaisesRegex(ConversationContractError, "GRAPH_CYCLE"):
            build_normalized_graph(graph_id="graph-cycle", snapshot=snapshot, export=export)

    def test_export_must_bind_snapshot_exact_payload_hash(self) -> None:
        export, snapshot = graph_fixture()
        changed = deepcopy(export)
        changed["conversations"][0]["messages"][0]["blocks"][0]["text"] += " tampered"
        with self.assertRaisesRegex(ConversationContractError, "SNAPSHOT_PAYLOAD_HASH_MISMATCH"):
            build_normalized_graph(graph_id="graph-tampered", snapshot=snapshot, export=changed)

    def test_rehashed_snapshot_cannot_claim_false_denominators(self) -> None:
        export, snapshot = graph_fixture()
        falsified = deepcopy(snapshot)
        falsified.pop("content_hash")
        falsified["message_count"] -= 1
        falsified = make_hashed_payload(falsified)
        with self.assertRaisesRegex(ConversationContractError, "SNAPSHOT_DENOMINATOR_MISMATCH"):
            build_normalized_graph(
                graph_id="graph-false-denominator",
                snapshot=falsified,
                export=export,
            )

    def test_rehashed_snapshot_cannot_claim_false_payload_bytes(self) -> None:
        export, snapshot = graph_fixture()
        falsified = deepcopy(snapshot)
        falsified.pop("content_hash")
        falsified["source_payload_bytes"] += 1
        falsified = make_hashed_payload(falsified)
        with self.assertRaisesRegex(ConversationContractError, "SNAPSHOT_PAYLOAD_BYTES_MISMATCH"):
            build_normalized_graph(
                graph_id="graph-false-payload-bytes",
                snapshot=falsified,
                export=export,
            )

    def test_tampered_graph_projection_is_rejected(self) -> None:
        export, snapshot = graph_fixture()
        graph = build_normalized_graph(graph_id="graph-001", snapshot=snapshot, export=export)
        tampered = dict(graph, orphan_message_ids=[])
        # The stored hash is not recomputed on read; the graph's own consistency check refuses it.
        with self.assertRaisesRegex(ConversationContractError, "GRAPH_ORPHAN_PROJECTION_DRIFT"):
            validate_normalized_graph(tampered)
        malformed = dict(graph, content_hash=dict(graph["content_hash"], value="not-a-sha256"))
        with self.assertRaisesRegex(ConversationContractError, "HASH_INVALID"):
            validate_normalized_graph(malformed)


if __name__ == "__main__":
    unittest.main()
