from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import yaml

from knowledge_feedback.conversation_refinement.contracts import make_selection_receipt
from knowledge_feedback.conversation_refinement.graph import build_normalized_graph
from knowledge_feedback.conversation_refinement.provider_adapter import build_synthetic_snapshot
from knowledge_feedback.conversation_refinement.redaction import redact_text
from knowledge_feedback.conversation_refinement.refinement import REFINEMENT_SUBJECT_SLOT
from knowledge_feedback.conversation_refinement.refinement import deterministic_refine_graph, make_conflict_set, make_refinement_subject_binding
from tests.modules.knowledge_feedback.test_export_snapshot_contract import NOW, make_envelope, make_export, message
from memorive.models.gateway.config import GatewayConfig
from memorive.models.gateway.prompts import PromptRegistry


REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")


def refinement_fixture() -> tuple[dict, dict]:
    synthetic_key = "sk-" + "abcdefghijklmnopqrstuvwxyz"
    export = make_export(
        [
            {
                "conversation_id": "conversation-001",
                "title": "typed",
                "messages": [
                    message("m1", "DECISION: keep failures immutable"),
                    message("m2", "FACT: build is deployed", parent_id="m1", role="assistant"),
                    message("m3", "FAILED: route timed out", parent_id="m1"),
                    message("m4", f"FORBIDDEN: credential {synthetic_key}", parent_id="m3"),
                    message("m5", "SUGGESTION: this unselected text must not appear", role="assistant"),
                ],
                "attachments": [],
            }
        ]
    )
    snapshot = build_synthetic_snapshot(
        snapshot_id="snapshot-refine",
        envelope=make_envelope(),
        export=export,
        captured_at=NOW,
    )
    graph = build_normalized_graph(graph_id="graph-refine", snapshot=snapshot, export=export)
    selection = make_selection_receipt(
        selection_id="selection-refine",
        snapshot=snapshot,
        decisions=[
            {
                "conversation_id": "conversation-001",
                "message_ids": ["m1", "m2", "m3", "m4"],
                "state": "INCLUDE",
                "reason": "synthetic exact-set",
            }
        ],
        created_at=NOW,
    )
    return graph, selection


class TypedRefinementTests(unittest.TestCase):
    def test_refinement_subject_uses_replaceable_m9_slot(self) -> None:
        config = GatewayConfig(REPO_ROOT / "memorive" / "models" / "gateway" / "models.yaml")
        prompt = PromptRegistry(REPO_ROOT / "memorive" / "models" / "gateway" / "prompts").get(
            REFINEMENT_SUBJECT_SLOT
        )
        slot = config.slot(REFINEMENT_SUBJECT_SLOT)
        binding = make_refinement_subject_binding(
            slot=slot,
            prompt_body=prompt.body,
            prompt_version=f"v{prompt.version}",
        )

        self.assertEqual(REFINEMENT_SUBJECT_SLOT, "conversation_refinement")
        self.assertTrue(slot.enabled)
        self.assertEqual(slot.provider, "deepseek")
        self.assertEqual(slot.model_id, "deepseek-v4-pro")
        self.assertEqual(slot.api_key_env, "DEEPSEEK_API_KEY")
        self.assertEqual(slot.thinking_type, "enabled")
        self.assertEqual(slot.effort, "high")
        self.assertEqual(slot.max_tokens, 8192)
        self.assertEqual(slot.temperature, 0.0)
        self.assertEqual(slot.timeout_seconds, 600)
        self.assertEqual(binding["selection_source"], "MODEL_GATEWAY_USER_REPLACEABLE_SLOT")
        self.assertTrue(binding["user_replaceable"])
        self.assertFalse(binding["credential_value_captured"])
        self.assertFalse(binding["private_history_allowed"])
        self.assertFalse(binding["gold_expected_allowed"])

    def test_replacing_slot_model_changes_frozen_behavior_without_code_change(self) -> None:
        source = yaml.safe_load(
            (REPO_ROOT / "memorive" / "models" / "gateway" / "models.yaml").read_text(encoding="utf-8")
        )
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "models.yaml"
            original = GatewayConfig(REPO_ROOT / "memorive" / "models" / "gateway" / "models.yaml")
            prompt = PromptRegistry(REPO_ROOT / "memorive" / "models" / "gateway" / "prompts").get(
                REFINEMENT_SUBJECT_SLOT
            )
            before = make_refinement_subject_binding(
                slot=original.slot(REFINEMENT_SUBJECT_SLOT),
                prompt_body=prompt.body,
                prompt_version=f"v{prompt.version}",
            )
            source["slots"][REFINEMENT_SUBJECT_SLOT]["model_id"] = "deepseek-v4-flash"
            source["slots"][REFINEMENT_SUBJECT_SLOT]["thinking_type"] = "disabled"
            source["slots"][REFINEMENT_SUBJECT_SLOT]["effort"] = None
            config_path.write_text(
                yaml.safe_dump(source, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            replaced = GatewayConfig(config_path)
            after = make_refinement_subject_binding(
                slot=replaced.slot(REFINEMENT_SUBJECT_SLOT),
                prompt_body=prompt.body,
                prompt_version=f"v{prompt.version}",
            )

        self.assertEqual(before["slot_name"], after["slot_name"])
        self.assertEqual(after["requested_model"], "deepseek-v4-flash")
        self.assertNotEqual(before["content_hash"], after["content_hash"])

    def test_selected_scope_and_types_are_exact(self) -> None:
        graph, selection = refinement_fixture()
        items = deterministic_refine_graph(graph=graph, selection_receipt=selection, scope="CONVERSATION-REFINEMENT")
        self.assertEqual(len(items), 4)
        self.assertEqual(
            [item["refinement_type"] for item in items],
            ["USER_DECISION_CLAIM", "REPOSITORY_VERIFIABLE_FACT_CLAIM", "FAILED_ATTEMPT", "FORBIDDEN_REUSE"],
        )
        self.assertTrue(all(item["truth_decided_by_refiner"] is False for item in items))
        self.assertNotIn("unselected text", " ".join(item["candidate_text"] for item in items))

    def test_assistant_fact_claim_remains_unverified(self) -> None:
        graph, selection = refinement_fixture()
        item = deterministic_refine_graph(graph=graph, selection_receipt=selection, scope="CONVERSATION-REFINEMENT")[1]
        self.assertEqual(item["evidence_state"], "UNVERIFIED_CLAIM")
        self.assertIn("REQUIRES_INDEPENDENT_EVIDENCE_OR_USER_DECISION", item["limitations"])

    def test_secret_like_value_is_redacted(self) -> None:
        synthetic_key = "sk-" + "abcdefghijklmnopqrstuvwxyz"
        result = redact_text(f"credential {synthetic_key}")
        self.assertNotIn(synthetic_key, result["redacted_text"])
        self.assertEqual(result["categories"], ["OPENAI_STYLE_KEY"])
        graph, selection = refinement_fixture()
        forbidden = deterministic_refine_graph(graph=graph, selection_receipt=selection, scope="CONVERSATION-REFINEMENT")[-1]
        self.assertNotIn(synthetic_key, forbidden["candidate_text"])
        self.assertEqual(forbidden["evidence_state"], "FORBIDDEN")

    def test_unselected_parent_is_not_copied_into_context(self) -> None:
        graph, selection = refinement_fixture()
        # m3 is selected and its parent m1 is selected; m2 does not leak into m3.
        failed = deterministic_refine_graph(graph=graph, selection_receipt=selection, scope="CONVERSATION-REFINEMENT")[2]
        self.assertEqual(failed["message_refs"], ["m3"])

    def test_conflict_set_preserves_history_and_blocks_unresolved(self) -> None:
        graph, selection = refinement_fixture()
        items = deterministic_refine_graph(graph=graph, selection_receipt=selection, scope="CONVERSATION-REFINEMENT")
        conflict = make_conflict_set(
            conflict_id="conflict-001",
            items=items[:2],
            dimension="project state",
        )
        self.assertEqual(conflict["resolution"], "BLOCK_UNRESOLVED")
        self.assertFalse(conflict["resolved"])
        self.assertFalse(conflict["history_overwritten"])


if __name__ == "__main__":
    unittest.main()
