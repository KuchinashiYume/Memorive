"""P05/T11 isolated Chroma generation contract and bounded manager."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import re
import stat
from typing import Any, Callable, Mapping, Sequence

from jsonschema import Draft202012Validator

from .m1e_embed import (
    CANON_EMBED_MODEL,
    _check_duplicates,
    _check_rebuild_conflict,
    _vector_metadata,
    _write_atomic,
)
from m9_gateway.execution_core.contracts import canonical_sha256, utc_now
from m9_gateway.local_embedding import validate_compatibility_manifest


SCHEMA_VERSION = "P05_T11_VECTOR_INDEX_GENERATION_V1"
SAFE_GENERATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
SAFE_COLLECTION_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,61}[A-Za-z0-9]$")
SCHEMA_PATH = (
    Path(__file__).resolve().parent
    / "schemas"
    / "p05_t11_vector_index_generation_v1.schema.json"
)


class GenerationError(RuntimeError):
    def __init__(self, code: str, receipt: Mapping[str, Any] | None = None):
        self.code = code
        self.receipt = copy.deepcopy(dict(receipt or {}))
        super().__init__(code)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _is_reparse(path: Path) -> bool:
    try:
        return path.is_symlink() or bool(
            getattr(path.lstat(), "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        )
    except OSError:
        return False


def _has_reparse_component(path: Path) -> bool:
    candidate = path.absolute()
    for cursor in (candidate, *candidate.parents):
        if cursor.exists() and _is_reparse(cursor):
            return True
    return False


def _path_sha256(path: Path) -> str:
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest().upper()


def _identity_seed(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(item)
        for key, item in value.items()
        if key not in {"created_at", "closed_at", "contract_identity_sha256"}
    }


def validate_generation_contract(value: Mapping[str, Any]) -> dict[str, Any]:
    accepted = json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(accepted),
        key=lambda item: (list(item.absolute_path), item.message),
    )
    if errors:
        details = [
            f"{'/'.join(str(part) for part in item.absolute_path) or '$'}: {item.message}"
            for item in errors[:20]
        ]
        raise GenerationError("GENERATION_CONTRACT_SCHEMA_INVALID", {"details": details})
    if accepted["write_verification"] != {
        "expected_count": accepted["member_count"],
        "observed_count": accepted["member_count"],
        "member_mismatch_count": 0,
        "collection_metadata_match": True,
    }:
        raise GenerationError("GENERATION_WRITE_VERIFICATION_INVALID")
    path = Path(accepted["collection_path"])
    if (
        not path.is_absolute()
        or any(ord(character) > 127 for character in str(path))
        or accepted["collection_path_sha256"] != _path_sha256(path)
    ):
        raise GenerationError("GENERATION_COLLECTION_PATH_INVALID")
    identity = canonical_sha256(_identity_seed(accepted))
    if accepted["contract_identity_sha256"] != identity:
        raise GenerationError("GENERATION_CONTRACT_IDENTITY_MISMATCH")
    return accepted


def _validate_members(
    chunks: Sequence[Mapping[str, Any]],
    vectors: Sequence[Sequence[float]],
    *,
    dimension: int,
    normalization_min: float,
    normalization_max: float,
) -> tuple[list[dict[str, str]], list[list[float]], str]:
    if not chunks or len(chunks) != len(vectors):
        raise GenerationError("GENERATION_MEMBER_COUNT_MISMATCH")
    normalized_chunks: list[dict[str, str]] = []
    normalized_vectors: list[list[float]] = []
    seen: set[str] = set()
    member_projection: list[dict[str, str]] = []
    for chunk, vector in zip(chunks, vectors):
        if not isinstance(chunk, Mapping) or set(chunk) != {"chunk_id", "text"}:
            raise GenerationError("GENERATION_CHUNK_FIELDS_INVALID")
        chunk_id = chunk["chunk_id"]
        text = chunk["text"]
        if (
            not isinstance(chunk_id, str)
            or not chunk_id
            or not isinstance(text, str)
            or not text
            or chunk_id in seen
        ):
            raise GenerationError("GENERATION_CHUNK_VALUE_INVALID")
        seen.add(chunk_id)
        if not isinstance(vector, Sequence) or isinstance(vector, (str, bytes)) or len(vector) != dimension:
            raise GenerationError("GENERATION_VECTOR_DIMENSION_MISMATCH")
        values: list[float] = []
        for item in vector:
            if (
                isinstance(item, bool)
                or not isinstance(item, (int, float))
                or not math.isfinite(float(item))
            ):
                raise GenerationError("GENERATION_VECTOR_NONFINITE")
            values.append(float(item))
        norm = math.sqrt(sum(item * item for item in values))
        if not normalization_min <= norm <= normalization_max:
            raise GenerationError("GENERATION_VECTOR_NORMALIZATION_MISMATCH")
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest().upper()
        vector_hash = canonical_sha256(values)
        normalized_chunks.append({"chunk_id": chunk_id, "text": text})
        normalized_vectors.append(values)
        member_projection.append(
            {"chunk_id": chunk_id, "text_sha256": text_hash, "vector_sha256": vector_hash}
        )
    member_hash = canonical_sha256(sorted(member_projection, key=lambda item: item["chunk_id"]))
    return normalized_chunks, normalized_vectors, member_hash


class IsolatedChromaGeneration:
    """Single-generation writer; it never opens a protected or parent store."""

    def __init__(
        self,
        *,
        allowed_root: str | Path,
        generation_root: str | Path,
        protected_roots: Sequence[str | Path],
        client_factory: Callable[..., Any] | None = None,
        clock: Callable[[], str] = utc_now,
    ):
        supplied_allowed = Path(allowed_root)
        if not supplied_allowed.exists() or not supplied_allowed.is_dir():
            raise GenerationError("GENERATION_ALLOWED_ROOT_MISSING")
        self.allowed_root = supplied_allowed.resolve(strict=True)
        self.generation_root = Path(generation_root).resolve(strict=False)
        if (
            self.generation_root == self.allowed_root
            or not _is_within(self.generation_root, self.allowed_root)
            or any(ord(character) > 127 for character in str(self.generation_root))
            or _has_reparse_component(self.generation_root)
        ):
            raise GenerationError("GENERATION_ROOT_OUTSIDE_ALLOWLIST")
        for protected in protected_roots:
            protected_path = Path(protected).resolve(strict=False)
            if _is_within(self.generation_root, protected_path) or _is_within(protected_path, self.generation_root):
                raise GenerationError("GENERATION_ROOT_PROTECTED_OVERLAP")
        self._client_factory = client_factory
        self._clock = clock
        self._client: Any | None = None
        self._collection: Any | None = None
        self._collection_name: str | None = None
        self._contract: dict[str, Any] | None = None

    @staticmethod
    def _collection_names(client: Any) -> list[str]:
        names = []
        for item in client.list_collections():
            name = item if isinstance(item, str) else getattr(item, "name", None)
            if isinstance(name, str):
                names.append(name)
        return sorted(names)

    def _client_for_root(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory(path=str(self.generation_root))
        import chromadb
        from chromadb.config import Settings

        return chromadb.PersistentClient(
            path=str(self.generation_root),
            settings=Settings(anonymized_telemetry=False),
        )

    def build(
        self,
        *,
        generation_id: str,
        parent_generation_id: str | None,
        compatibility_manifest: Mapping[str, Any],
        chunks: Sequence[Mapping[str, Any]],
        vectors: Sequence[Sequence[float]],
        readers: Sequence[str] = ("M03", "M14"),
        fail_after_add: bool = False,
    ) -> dict[str, Any]:
        if not SAFE_GENERATION_ID.fullmatch(generation_id):
            raise GenerationError("GENERATION_ID_INVALID")
        if parent_generation_id is not None and (
            not SAFE_GENERATION_ID.fullmatch(parent_generation_id)
            or parent_generation_id == generation_id
        ):
            raise GenerationError("GENERATION_PARENT_INVALID")
        manifest = validate_compatibility_manifest(compatibility_manifest)
        if manifest["model"]["canonical_model"] != CANON_EMBED_MODEL:
            raise GenerationError("GENERATION_M1E_MODEL_CONTRACT_MISMATCH")
        if self.generation_root != Path(manifest["chroma"]["generation_root"]).resolve(strict=False):
            raise GenerationError("GENERATION_ROOT_MANIFEST_MISMATCH")
        normalized_chunks, normalized_vectors, member_hash = _validate_members(
            chunks,
            vectors,
            dimension=manifest["model"]["dimension"],
            normalization_min=manifest["limits"]["normalization_min"],
            normalization_max=manifest["limits"]["normalization_max"],
        )
        collection_name = f"p05_t11_{generation_id.lower()}"
        if not SAFE_COLLECTION_NAME.fullmatch(collection_name):
            raise GenerationError("GENERATION_COLLECTION_NAME_INVALID")
        if self.generation_root.exists():
            raise GenerationError("GENERATION_CREATE_ONLY_COLLISION")
        self.generation_root.mkdir(parents=False, exist_ok=False)
        rollback_receipt: dict[str, Any] = {}
        try:
            self._client = self._client_for_root()
            before_names = self._collection_names(self._client)
            if collection_name in before_names:
                raise GenerationError("GENERATION_COLLECTION_CREATE_ONLY_COLLISION")
            self._collection = self._client.create_collection(
                name=collection_name,
                metadata={
                    "hnsw:space": "l2",
                    "generation_id": generation_id,
                    "vector_compatibility_key_sha256": manifest[
                        "vector_compatibility_key_sha256"
                    ],
                },
            )
            self._collection_name = collection_name
            ids = [item["chunk_id"] for item in normalized_chunks]
            texts = [item["text"] for item in normalized_chunks]
            paper_id = "P05_T11_PUBLIC_SAFE_SYNTHETIC"
            embedded_at = self._clock()
            metadatas = []
            for item in normalized_chunks:
                record = {
                    "paper_id": paper_id,
                    "chunk_id": item["chunk_id"],
                    "text": item["text"],
                    "chunk_schema_version": "P05_T11_FIXTURE_V1",
                }
                metadata = _vector_metadata(
                    record,
                    ownership="self",
                    provider_model_id=manifest["model"]["requested_model"],
                    backend="local",
                    embedded_at=embedded_at,
                    run_id=generation_id,
                )
                metadata.update(
                    {
                        "text_sha256": hashlib.sha256(
                            item["text"].encode("utf-8")
                        ).hexdigest().upper(),
                        "generation_id": generation_id,
                        "vector_compatibility_key_sha256": manifest[
                            "vector_compatibility_key_sha256"
                        ],
                    }
                )
                metadatas.append(metadata)
            _check_rebuild_conflict(self._collection, collection_name, paper_id)
            _check_duplicates(self._collection, ids, paper_id)
            _write_atomic(
                self._collection,
                ids,
                normalized_vectors,
                metadatas,
                texts,
                generation_id,
                paper_id,
            )
            if fail_after_add:
                raise GenerationError("INJECTED_FAILURE_AFTER_ADD")
            observed_count = int(self._collection.count())
            observed = self._collection.get(ids=ids, include=["metadatas"])
            observed_ids = list(observed.get("ids") or [])
            observed_meta = list(observed.get("metadatas") or [])
            mismatches = len(set(ids).symmetric_difference(observed_ids))
            metadata_match = len(observed_meta) == len(ids) and all(
                isinstance(item, Mapping)
                and item.get("generation_id") == generation_id
                and item.get("vector_compatibility_key_sha256")
                == manifest["vector_compatibility_key_sha256"]
                for item in observed_meta
            )
            if observed_count != len(ids) or mismatches or not metadata_match:
                raise GenerationError("GENERATION_WRITE_VERIFY_FAILED")
            value: dict[str, Any] = {
                "schema_version": SCHEMA_VERSION,
                "contract_id": f"vectorindexgeneration_{generation_id}",
                "generation_id": generation_id,
                "parent_generation_id": parent_generation_id,
                "state": "CLOSED_VERIFIED",
                "compatibility_manifest_ref": manifest["manifest_id"],
                "vector_compatibility_key_sha256": manifest[
                    "vector_compatibility_key_sha256"
                ],
                "collection_name": collection_name,
                "collection_path": str(self.generation_root),
                "collection_path_sha256": _path_sha256(self.generation_root),
                "distance_metric": "L2",
                "dimension": manifest["model"]["dimension"],
                "normalization": manifest["model"]["normalization"],
                "output_dtype": manifest["model"]["output_dtype"],
                "member_count": len(ids),
                "member_set_sha256": member_hash,
                "writer": "P05_T11_VECTOR_GENERATION_MANAGER",
                "readers": list(readers),
                "created_at": self._clock(),
                "closed_at": self._clock(),
                "write_verification": {
                    "expected_count": len(ids),
                    "observed_count": observed_count,
                    "member_mismatch_count": mismatches,
                    "collection_metadata_match": metadata_match,
                },
                "rollback_policy": {
                    "mode": "DELETE_CURRENT_COLLECTION_ONLY",
                    "delete_parent_generation": False,
                    "delete_generation_directory": False,
                    "receipt_required": True,
                },
            }
            value["contract_identity_sha256"] = canonical_sha256(_identity_seed(value))
            self._contract = validate_generation_contract(value)
            return copy.deepcopy(self._contract)
        except Exception as exc:
            rollback_receipt = self._rollback_after_failure(generation_id, collection_name, exc)
            if isinstance(exc, GenerationError):
                raise GenerationError(exc.code, rollback_receipt) from exc
            raise GenerationError("GENERATION_BUILD_FAILED", rollback_receipt) from exc

    def query(self, vector: Sequence[float], *, n_results: int = 1) -> dict[str, Any]:
        if self._collection is None or self._contract is None:
            raise GenerationError("GENERATION_NOT_READY_FOR_QUERY")
        if (
            isinstance(vector, (str, bytes))
            or isinstance(n_results, bool)
            or not isinstance(n_results, int)
            or n_results <= 0
            or any(
                isinstance(item, bool) or not isinstance(item, (int, float))
                for item in vector
            )
        ):
            raise GenerationError("GENERATION_QUERY_VECTOR_INVALID")
        values = [float(item) for item in vector]
        norm = math.sqrt(sum(item * item for item in values))
        if (
            len(values) != self._contract["dimension"]
            or any(not math.isfinite(item) for item in values)
            or not 0.999 <= norm <= 1.001
        ):
            raise GenerationError("GENERATION_QUERY_VECTOR_INVALID")
        return self._collection.query(
            query_embeddings=[values],
            n_results=n_results,
            include=["distances", "metadatas"],
        )

    def rollback(self, *, reason: str) -> dict[str, Any]:
        if self._client is None or self._collection_name is None:
            raise GenerationError("GENERATION_NOT_READY_FOR_ROLLBACK")
        before = self._collection_names(self._client)
        existed = self._collection_name in before
        if existed:
            self._client.delete_collection(name=self._collection_name)
        after = self._collection_names(self._client)
        if self._collection_name in after:
            raise GenerationError("GENERATION_ROLLBACK_RESIDUAL")
        receipt = {
            "schema_version": "P05_T11_VECTOR_INDEX_ROLLBACK_V1",
            "generation_id": self._contract["generation_id"] if self._contract else None,
            "collection_name": self._collection_name,
            "reason": reason,
            "collection_existed_before": existed,
            "collection_exists_after": False,
            "generation_directory_deleted": False,
            "parent_generation_deleted": False,
            "rolled_back_at": self._clock(),
            "result": "PASS",
        }
        self._collection = None
        return receipt

    def _rollback_after_failure(
        self, generation_id: str, collection_name: str, exc: Exception
    ) -> dict[str, Any]:
        before: list[str] = []
        after: list[str] = []
        rollback_error: str | None = None
        if self._client is not None:
            try:
                before = self._collection_names(self._client)
                if collection_name in before:
                    self._client.delete_collection(name=collection_name)
                after = self._collection_names(self._client)
            except Exception as rollback_exc:
                rollback_error = type(rollback_exc).__name__
        residual = collection_name in after if self._client is not None else False
        return {
            "schema_version": "P05_T11_VECTOR_INDEX_FAILURE_ROLLBACK_V1",
            "generation_id": generation_id,
            "collection_name": collection_name,
            "failure_code": exc.code if isinstance(exc, GenerationError) else type(exc).__name__,
            "collection_names_before_rollback": before,
            "collection_names_after_rollback": after,
            "collection_residual": residual,
            "rollback_error_type": rollback_error,
            "generation_directory_deleted": False,
            "result": "PASS" if not residual and rollback_error is None else "RECOVERY_REQUIRED",
            "recorded_at": self._clock(),
        }


def assert_generation_compatible(
    contract: Mapping[str, Any], compatibility_manifest: Mapping[str, Any]
) -> None:
    accepted_contract = validate_generation_contract(contract)
    manifest = validate_compatibility_manifest(compatibility_manifest)
    if (
        accepted_contract["vector_compatibility_key_sha256"]
        != manifest["vector_compatibility_key_sha256"]
        or accepted_contract["dimension"] != manifest["model"]["dimension"]
        or accepted_contract["normalization"] != manifest["model"]["normalization"]
        or accepted_contract["output_dtype"] != manifest["model"]["output_dtype"]
    ):
        raise GenerationError("GENERATION_COMPATIBILITY_MISMATCH")
