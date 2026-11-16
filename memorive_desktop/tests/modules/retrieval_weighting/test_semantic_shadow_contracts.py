from __future__ import annotations

from copy import deepcopy
import unittest

from memorive.research.ranking.contracts import make_hashed_payload
from memorive.research.ranking.errors import FieldVectorError
from memorive.research.ranking.semantic_shadow_contracts import canonical_artifact_raw_sha256, validate_semantic_profile_terminal_receipt, validate_semantic_retrieval_policy_snapshot
from tests.modules.retrieval_weighting.test_semantic_field_profiles import (
    raw_hash_for,
    semantic_overlay,
    semantic_profile,
    source_fields,
)
from tests.modules.retrieval_weighting.test_shadow_contracts import _snapshot


def terminal_receipt() -> dict:
    profile = semantic_profile()
    overlay = semantic_overlay()
    payload = {
        "schema_version": "SEMANTIC_AGGREGATION_SEMANTIC_PROFILE_QUALIFICATION_TERMINAL_RECEIPT_V1",
        "object_type": "SemanticProfileQualificationTerminalReceipt",
        "receipt_id": "semantic-profile-terminal:sample",
        "candidate_id": "semantic-profile-sample",
        "terminalizer_identity": "LOCAL_MECHANICAL_SEMANTIC_PROFILE_QUALIFIER_NOT_ACCEPTANCE_AUTHORITY",
        "profile_id": profile["profile_id"],
        "profile_content_hash": profile["content_hash"],
        "profile_raw_sha256": canonical_artifact_raw_sha256(profile),
        "overlay_id": overlay["overlay_id"],
        "overlay_content_hash": overlay["content_hash"],
        "overlay_raw_sha256": canonical_artifact_raw_sha256(overlay),
        "source_pack_raw_sha256": profile["source_pack_raw_sha256"],
        "query_pack_raw_sha256": raw_hash_for("query-pack"),
        "precomputed_query_vectors_raw_sha256": raw_hash_for("query-vectors"),
        "observed_results_raw_sha256": raw_hash_for("observed-results"),
        "score_receipt_raw_sha256": raw_hash_for("score-receipt"),
        "execution_authorization_raw_sha256": raw_hash_for("execution-authorization"),
        "lifecycle_status": "completed",
        "verification_result": "PASS",
        "qualification_result": "PASS",
        "acceptance_verdict": "NOT_ASSESSED",
        "semantic_shadow_eligible": True,
        "eligible_for_B_freeze_candidate_construction": True,
        "semantic_B_eligible": False,
        "release_to_B": False,
        "field_semantic_retrieval_quality": "NOT_ASSESSED",
        "retrieval_improvement_over_baseline": "NOT_ASSESSED",
        "production_eligible": False,
        "model_calls": 1,
        "reranker_calls": 0,
        "loopback_http_calls": 2,
        "external_network_calls": 0,
        "provider_tokens": 0,
        "cost_cny": 0,
        "create_only": True,
        "hash_kind": "rfc8785_jcs_sha256",
    }
    return make_hashed_payload(payload)


def semantic_policy() -> dict:
    base = _snapshot()
    profile = semantic_profile()
    overlay = semantic_overlay()
    receipt = terminal_receipt()
    payload = {key: deepcopy(value) for key, value in base.items() if key != "content_hash"}
    payload.update(
        {
            "object_id": "RPS-SEMANTIC-002",
            "schema_version": "2.1",
            "revision": 2,
            "state": "shadow_only",
            "supersedes": base["object_id"],
            "activation_authorized": False,
            "semantic_shadow_eligible": True,
            "semantic_qualification": "QUALIFIED_FOR_SHADOW_B_INPUT",
            "structural_only": False,
            "field_semantic_retrieval_quality": "NOT_ASSESSED",
            "retrieval_improvement_over_baseline": "NOT_ASSESSED",
            "acceptance_verdict": "NOT_ASSESSED",
            "eligible_for_B_freeze_candidate_construction": True,
            "semantic_B_eligible": False,
            "release_to_B": False,
            "semantic_profile_binding": {
                "profile_id": profile["profile_id"],
                "profile_content_hash": profile["content_hash"],
                "profile_raw_sha256": canonical_artifact_raw_sha256(profile),
                "overlay_id": overlay["overlay_id"],
                "overlay_content_hash": overlay["content_hash"],
                "overlay_raw_sha256": canonical_artifact_raw_sha256(overlay),
                "source_pack_raw_sha256": profile["source_pack_raw_sha256"],
            },
            "qualification_terminal_receipt_binding": {
                "receipt_id": receipt["receipt_id"],
                "receipt_content_hash": receipt["content_hash"],
                "receipt_raw_sha256": canonical_artifact_raw_sha256(receipt),
                "qualification_result": "PASS",
            },
            "semantic_execution_policy": {
                "model": "NONE",
                "reranker": "NONE",
                "model_calls": 0,
                "reranker_calls": 0,
                "network_calls": 0,
                "provider_tokens": 0,
                "cost_cny": 0,
            },
        }
    )
    payload["field_export_identity"] = {
        "field_registry_hash": profile["field_registry_hash"],
        "field_index_manifest_hash": profile["content_hash"],
        "field_export_hash": overlay["content_hash"],
        "embedding_profile_id": profile["profile_id"],
        "embedding_dimension": profile["embedding_dimension"],
        "embedding_normalization": "l2",
        "production_eligible": False,
    }
    return make_hashed_payload(payload)


class SemanticShadowContractTests(unittest.TestCase):
    def test_terminal_receipt_and_policy_form_one_exact_gate(self) -> None:
        profile = semantic_profile()
        overlay = semantic_overlay()
        receipt = terminal_receipt()
        policy = semantic_policy()
        self.assertEqual(validate_semantic_profile_terminal_receipt(receipt), receipt)
        self.assertEqual(
            validate_semantic_retrieval_policy_snapshot(
                policy,
                terminal_receipt=receipt,
                profile=profile,
                overlay=overlay,
                source_records=source_fields(),
            ),
            policy,
        )

    def test_receipt_rejects_release_or_unbound_profile(self) -> None:
        receipt_payload = deepcopy(terminal_receipt())
        receipt_payload.pop("content_hash")
        receipt_payload["release_to_B"] = True
        with self.assertRaises(FieldVectorError):
            validate_semantic_profile_terminal_receipt(make_hashed_payload(receipt_payload))

        policy_payload = deepcopy(semantic_policy())
        policy_payload.pop("content_hash")
        policy_payload["semantic_profile_binding"]["profile_raw_sha256"] = raw_hash_for("forged")
        with self.assertRaises(FieldVectorError):
            validate_semantic_retrieval_policy_snapshot(
                make_hashed_payload(policy_payload),
                terminal_receipt=terminal_receipt(),
                profile=semantic_profile(),
                overlay=semantic_overlay(),
                source_records=source_fields(),
            )

    def test_policy_keeps_activation_quality_and_b_release_unassessed(self) -> None:
        for field_name, value in (
            ("activation_authorized", True),
            ("acceptance_verdict", "PASS"),
            ("semantic_B_eligible", True),
        ):
            payload = deepcopy(semantic_policy())
            payload.pop("content_hash")
            payload[field_name] = value
            with self.assertRaises(FieldVectorError):
                validate_semantic_retrieval_policy_snapshot(
                    make_hashed_payload(payload),
                    terminal_receipt=terminal_receipt(),
                    profile=semantic_profile(),
                    overlay=semantic_overlay(),
                    source_records=source_fields(),
                )


if __name__ == "__main__":
    unittest.main()
