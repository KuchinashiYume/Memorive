"""Offline tests for the LOCAL-RETRIEVAL create-only vector generation manager."""

from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
import sys
import tempfile
import unittest


REPO_ROOT = (Path(__file__).resolve().parents[3] / "source")
sys.path.insert(0, str(REPO_ROOT))
FIXED_TIME = "2026-08-14T14:00:00Z"
FIXED_CLOCK = lambda: FIXED_TIME

from document_processing.vector_generation import GenerationError, IsolatedChromaGeneration, assert_generation_compatible, validate_generation_contract
from memorive.models.gateway.local_embedding import build_compatibility_manifest
class FakeCollection:
    def __init__(self, name: str, metadata: dict):
        self.name = name
        self.metadata = dict(metadata)
        self.rows: dict[str, dict] = {}

    def add(self, *, ids, embeddings, documents, metadatas) -> None:
        for chunk_id, embedding, document, metadata in zip(
            ids, embeddings, documents, metadatas
        ):
            if chunk_id in self.rows:
                raise ValueError("duplicate id")
            self.rows[chunk_id] = {
                "embedding": list(embedding),
                "document": document,
                "metadata": dict(metadata),
            }

    def count(self) -> int:
        return len(self.rows)

    def get(self, *, ids=None, include=None) -> dict:
        requested = list(self.rows) if ids is None else ids
        found = [chunk_id for chunk_id in requested if chunk_id in self.rows]
        return {
            "ids": found,
            "metadatas": [self.rows[chunk_id]["metadata"] for chunk_id in found],
        }

    def delete(self, *, where) -> None:
        doomed = [
            chunk_id
            for chunk_id, row in self.rows.items()
            if all(row["metadata"].get(key) == value for key, value in where.items())
        ]
        for chunk_id in doomed:
            del self.rows[chunk_id]

    def query(self, *, query_embeddings, n_results, include) -> dict:
        query = list(query_embeddings[0])
        ordered = sorted(
            self.rows.items(),
            key=lambda item: sum(
                (left - right) ** 2
                for left, right in zip(query, item[1]["embedding"])
            ),
        )[:n_results]
        return {
            "ids": [[chunk_id for chunk_id, _ in ordered]],
            "distances": [[
                sum((left - right) ** 2 for left, right in zip(query, row["embedding"]))
                for _, row in ordered
            ]],
            "metadatas": [[row["metadata"] for _, row in ordered]],
        }


class FakeClient:
    def __init__(self):
        self.collections: dict[str, FakeCollection] = {}

    def list_collections(self):
        return list(self.collections)

    def create_collection(self, *, name: str, metadata: dict) -> FakeCollection:
        if name in self.collections:
            raise ValueError("collection exists")
        collection = FakeCollection(name, metadata)
        self.collections[name] = collection
        return collection

    def delete_collection(self, *, name: str) -> None:
        del self.collections[name]


def manifest(root: Path, *, runtime_version: str = "0.32.9") -> dict:
    return build_compatibility_manifest(
        manifest_id=f"vector_manifest_{runtime_version.replace('.', '_')}",
        runtime_version=runtime_version,
        executable_sha256="0" * 64,
        endpoint="http://127.0.0.1:11434/api/embed",
        requested_model="bge-m3:latest",
        canonical_model="bge-m3",
        model_manifest_sha256="1" * 64,
        model_layer_digest="2" * 64,
        dimension=3,
        chroma_version="1.0-test",
        generation_root=root,
        reranker_mode="OFF",
        source_evidence_refs=("fixture://local_retrieval-authority",),
        clock=FIXED_CLOCK,
    )


CHUNKS = (
    {"chunk_id": "chunk_alpha", "text": "synthetic alpha"},
    {"chunk_id": "chunk_beta", "text": "synthetic beta"},
)
VECTORS = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))


class VectorGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        # The product refuses a generation root with non-ASCII characters (Chroma cannot use
        # such paths), and these tests put that root under the system temp folder.
        if any(ord(character) > 127 for character in tempfile.gettempdir()):
            self.skipTest("the temp folder path has non-ASCII characters; Chroma paths must be ASCII")
        self.temporary = tempfile.TemporaryDirectory(prefix="vector_generation_")
        self.allowed_root = Path(self.temporary.name) / "allowed"
        self.allowed_root.mkdir()
        self.generation_root = self.allowed_root / "generation001"
        self.client = FakeClient()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def manager(self, *, root: Path | None = None, protected_roots=()):
        return IsolatedChromaGeneration(
            allowed_root=self.allowed_root,
            generation_root=root or self.generation_root,
            protected_roots=protected_roots,
            client_factory=lambda **_kwargs: self.client,
            clock=FIXED_CLOCK,
        )

    def test_build_verify_query_and_explicit_collection_only_rollback(self) -> None:
        manager = self.manager()
        contract = manager.build(
            generation_id="gen001",
            parent_generation_id=None,
            compatibility_manifest=manifest(self.generation_root),
            chunks=CHUNKS,
            vectors=VECTORS,
        )
        validate_generation_contract(contract)
        self.assertEqual(contract["state"], "CLOSED_VERIFIED")
        self.assertEqual(contract["member_count"], 2)
        self.assertEqual(contract["write_verification"]["member_mismatch_count"], 0)
        result = manager.query((1.0, 0.0, 0.0), n_results=1)
        self.assertEqual(result["ids"], [["chunk_alpha"]])
        with self.assertRaises(GenerationError) as caught:
            manager.query((2.0, 0.0, 0.0), n_results=1)
        self.assertEqual(caught.exception.code, "GENERATION_QUERY_VECTOR_INVALID")
        receipt = manager.rollback(reason="offline acceptance fixture")
        self.assertEqual(receipt["result"], "PASS")
        self.assertFalse(receipt["collection_exists_after"])
        self.assertFalse(receipt["generation_directory_deleted"])
        self.assertTrue(self.generation_root.is_dir())
        self.assertEqual(self.client.list_collections(), [])

    def test_manifest_root_mismatch_fails_before_generation_creation(self) -> None:
        manager = self.manager()
        other = self.allowed_root / "other-generation"
        with self.assertRaises(GenerationError) as caught:
            manager.build(
                generation_id="gen002",
                parent_generation_id=None,
                compatibility_manifest=manifest(other),
                chunks=CHUNKS,
                vectors=VECTORS,
            )
        self.assertEqual(caught.exception.code, "GENERATION_ROOT_MANIFEST_MISMATCH")
        self.assertFalse(self.generation_root.exists())
        self.assertFalse(other.exists())

    def test_protected_overlap_is_rejected_at_construction(self) -> None:
        with self.assertRaises(GenerationError) as caught:
            self.manager(protected_roots=(self.generation_root,))
        self.assertEqual(caught.exception.code, "GENERATION_ROOT_PROTECTED_OVERLAP")
        self.assertFalse(self.generation_root.exists())

    def test_injected_failure_rolls_back_only_current_collection(self) -> None:
        manager = self.manager()
        with self.assertRaises(GenerationError) as caught:
            manager.build(
                generation_id="gen003",
                parent_generation_id=None,
                compatibility_manifest=manifest(self.generation_root),
                chunks=CHUNKS,
                vectors=VECTORS,
                fail_after_add=True,
            )
        self.assertEqual(caught.exception.code, "INJECTED_FAILURE_AFTER_ADD")
        self.assertEqual(caught.exception.receipt["result"], "PASS")
        self.assertFalse(caught.exception.receipt["collection_residual"])
        self.assertFalse(caught.exception.receipt["generation_directory_deleted"])
        self.assertTrue(self.generation_root.is_dir())
        self.assertEqual(self.client.list_collections(), [])

    def test_contract_tamper_and_extra_field_fail_closed(self) -> None:
        contract = self.manager().build(
            generation_id="gen004",
            parent_generation_id=None,
            compatibility_manifest=manifest(self.generation_root),
            chunks=CHUNKS,
            vectors=VECTORS,
        )
        changed = deepcopy(contract)
        changed["member_count"] = 3
        with self.assertRaises(GenerationError):
            validate_generation_contract(changed)
        extra = deepcopy(contract)
        extra["unapproved"] = True
        with self.assertRaises(GenerationError) as caught:
            validate_generation_contract(extra)
        self.assertEqual(caught.exception.code, "GENERATION_CONTRACT_SCHEMA_INVALID")

    def test_generation_compatibility_mismatch_is_rejected(self) -> None:
        contract = self.manager().build(
            generation_id="gen005",
            parent_generation_id=None,
            compatibility_manifest=manifest(self.generation_root),
            chunks=CHUNKS,
            vectors=VECTORS,
        )
        changed_manifest = manifest(self.generation_root, runtime_version="0.33.0")
        with self.assertRaises(GenerationError) as caught:
            assert_generation_compatible(contract, changed_manifest)
        self.assertEqual(caught.exception.code, "GENERATION_COMPATIBILITY_MISMATCH")

    def test_invalid_vector_fails_before_create(self) -> None:
        with self.assertRaises(GenerationError) as caught:
            self.manager().build(
                generation_id="gen006",
                parent_generation_id=None,
                compatibility_manifest=manifest(self.generation_root),
                chunks=CHUNKS,
                vectors=((1.0, 0.0), (0.0, 1.0)),
            )
        self.assertEqual(caught.exception.code, "GENERATION_VECTOR_DIMENSION_MISMATCH")
        self.assertFalse(self.generation_root.exists())

        with self.assertRaises(GenerationError) as caught:
            self.manager().build(
                generation_id="gen007",
                parent_generation_id=None,
                compatibility_manifest=manifest(self.generation_root),
                chunks=CHUNKS,
                vectors=((math.nan, 0.0, 0.0), (0.0, 1.0, 0.0)),
            )
        self.assertEqual(caught.exception.code, "GENERATION_VECTOR_NONFINITE")
        self.assertFalse(self.generation_root.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
