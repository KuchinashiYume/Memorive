from __future__ import annotations

from copy import deepcopy
import unittest

from memorive.platform.jcs import canonical_json_bytes, sha256_bytes, typed_payload_hash
from memorive.research.ranking.contracts import make_hashed_payload
from memorive.research.ranking.errors import FieldVectorError
from memorive.research.ranking.semantic_field_profiles import semantic_overlay_vector_id, validate_semantic_field_embedding_overlay, validate_semantic_field_profile
def hash_for(label: str) -> dict[str, str]:
    return typed_payload_hash({"label": label})


def raw_hash_for(label: str) -> str:
    return sha256_bytes(label.encode("utf-8"))


def source_fields() -> list[dict]:
    return [
        {
            "field_id": "field-001",
            "paper_id": "paper-001",
            "card_id": "card-001",
            "field_path": "method/0",
            "text": "randomized controlled experiment",
            "provenance_state": "original",
        },
        {
            "field_id": "field-002",
            "paper_id": "paper-002",
            "card_id": "card-002",
            "field_path": "limitations/0",
            "text": "small sample limits generalization",
            "provenance_state": "original",
        },
    ]


def rights() -> dict:
    return {
        "data_classification": "PROJECT_AUTHORED_SYNTHETIC_PUBLIC_SAFE_SELF_OWNED",
        "data_ownership": "self",
        "rights_status": "CLEARED_FOR_ISOLATED_SHADOW_EVALUATION",
        "redistribution_allowed": True,
        "entrusted_data_included": False,
        "restricted_source_included": False,
    }


def semantic_profile() -> dict:
    payload = {
        "object_type": "SemanticFieldProfile",
        "schema_version": "1.0",
        "profile_id": "semantic:ollama-bge-m3-v1-d3",
        "revision": 1,
        "representation_kind": "precomputed_semantic_embedding",
        "returned_model_identity": {
            "identity_kind": "authorized_loopback_ollama",
            "provider": "ollama",
            "model_id": "bge-m3",
            "returned_identity": "bge-m3:latest@sha256-test",
            "endpoint_scope": "LOOPBACK_ONLY",
            "model_manifest_raw_sha256": raw_hash_for("manifest"),
            "model_blob_hashes": sorted([raw_hash_for("blob-a"), raw_hash_for("blob-b")]),
        },
        "vector_origin": {
            "origin_kind": "AUTHORIZED_LOOPBACK_OLLAMA",
            "generator_id": "research-configuration-loopback-batch-embed-v1",
            "generator_hash": hash_for("generator"),
            "embedding_input_contract_hash": hash_for("input-contract"),
            "generated_by_model": True,
            "hash_derived": False,
            "random_derived": False,
        },
        "embedding_dimension": 3,
        "embedding_normalization": "l2",
        "similarity_metric": "dot_product",
        "field_registry_hash": hash_for("registry"),
        "source_pack_raw_sha256": raw_hash_for("source-pack"),
        "rights": rights(),
        "execution_boundary": {
            "generation_mode": "authorized_loopback_precompute",
            "provider": "ollama",
            "model": "bge-m3",
            "endpoint_scope": "LOOPBACK_ONLY",
            "generation_authorization_required": True,
            "runtime_mode": "precomputed_read_only",
            "runtime_model_calls_allowed": False,
            "runtime_network_calls_allowed": False,
        },
        "semantic_shadow_eligible": False,
        "qualification_state": "PENDING_TERMINAL_RECEIPT",
        "eligibility_effective_only_with_terminal_receipt": True,
        "production_eligible": False,
        "hash_kind": "rfc8785_jcs_sha256",
    }
    return make_hashed_payload(payload)


def semantic_overlay() -> dict:
    profile = semantic_profile()
    vectors = {"field-001": [1.0, 0.0, 0.0], "field-002": [0.0, 1.0, 0.0]}
    records = []
    for field in source_fields():
        source_hash = typed_payload_hash(field)
        embedding = vectors[field["field_id"]]
        records.append(
            {
                "overlay_vector_id": semantic_overlay_vector_id(
                    profile_id=profile["profile_id"],
                    profile_content_hash=profile["content_hash"],
                    field_id=field["field_id"],
                    source_record_hash=source_hash,
                    embedding=embedding,
                ),
                "field_id": field["field_id"],
                "paper_id": field["paper_id"],
                "card_id": field["card_id"],
                "field_path": field["field_path"],
                "source_record_hash": source_hash,
                "source_text_hash": typed_payload_hash({"text": field["text"]}),
                "provenance_state": field["provenance_state"],
                "embedding": embedding,
            }
        )
    payload = {
        "object_type": "SemanticFieldEmbeddingOverlay",
        "schema_version": "1.0",
        "overlay_id": "semantic-overlay:sample",
        "revision": 1,
        "profile_id": profile["profile_id"],
        "profile_content_hash": profile["content_hash"],
        "source_pack_raw_sha256": profile["source_pack_raw_sha256"],
        "field_registry_hash": profile["field_registry_hash"],
        "embedding_dimension": profile["embedding_dimension"],
        "embedding_normalization": "l2",
        "similarity_metric": "dot_product",
        "record_count": len(records),
        "records": records,
        "rights": rights(),
        "production_eligible": False,
        "hash_kind": "rfc8785_jcs_sha256",
    }
    return make_hashed_payload(payload)


class SemanticFieldProfileTests(unittest.TestCase):
    def test_profile_and_overlay_bind_exact_semantic_identity(self) -> None:
        profile = semantic_profile()
        overlay = semantic_overlay()
        self.assertEqual(validate_semantic_field_profile(profile), profile)
        self.assertEqual(
            validate_semantic_field_embedding_overlay(
                overlay, profile=profile, source_records=source_fields()
            ),
            overlay,
        )

    def test_profile_rejects_non_loopback_fixture_or_production_drift(self) -> None:
        for mutate in (
            lambda item: item["returned_model_identity"].__setitem__("provider", "NONE"),
            lambda item: item["vector_origin"].__setitem__("hash_derived", True),
            lambda item: item.__setitem__("production_eligible", True),
        ):
            payload = deepcopy(semantic_profile())
            payload.pop("content_hash")
            mutate(payload)
            with self.assertRaises(FieldVectorError):
                validate_semantic_field_profile(make_hashed_payload(payload))

    def test_overlay_rejects_source_hash_dimension_and_l2_drift(self) -> None:
        profile = semantic_profile()
        for field_name, replacement in (
            ("source_record_hash", hash_for("forged-source")),
            ("embedding", [1.0, 1.0, 0.0]),
        ):
            payload = deepcopy(semantic_overlay())
            payload.pop("content_hash")
            payload["records"][0][field_name] = replacement
            with self.assertRaises(FieldVectorError):
                validate_semantic_field_embedding_overlay(
                    make_hashed_payload(payload),
                    profile=profile,
                    source_records=source_fields(),
                )


if __name__ == "__main__":
    unittest.main()
