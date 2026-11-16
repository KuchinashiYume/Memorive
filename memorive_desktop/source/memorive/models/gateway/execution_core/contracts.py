from __future__ import annotations

from copy import deepcopy
import datetime as dt
import hashlib
import json
import re
from typing import Any, Callable, Mapping, Sequence

from jsonschema import Draft202012Validator


SHA256_PATTERN = re.compile(r"^[0-9A-F]{64}$")
Clock = Callable[[], str]


class ContractViolation(ValueError):
    """Raised when an object cannot satisfy the accepted Initialization contract."""

    def __init__(self, code: str, details: Sequence[str] | None = None):
        self.code = code
        self.details = tuple(details or ())
        suffix = f": {'; '.join(self.details)}" if self.details else ""
        super().__init__(f"{code}{suffix}")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json_bytes(value: Any) -> bytes:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ContractViolation("CANONICAL_JSON_INVALID", [str(exc)]) from exc
    return rendered.encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest().upper()


def bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def immutable_copy(value: Any) -> Any:
    """Return a JSON-domain copy, rejecting runtime-only object types."""

    return json.loads(canonical_json_bytes(value).decode("utf-8"))


def object_ref(value: Mapping[str, Any]) -> str:
    object_id = value.get("object_id")
    revision = value.get("revision")
    if not isinstance(object_id, str) or not object_id:
        raise ContractViolation("OBJECT_REF_ID_INVALID")
    if not isinstance(revision, str) or not revision:
        raise ContractViolation("OBJECT_REF_REVISION_INVALID")
    return f"{object_id}@{revision}"


def require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ContractViolation("SHA256_TYPE_INVALID", [field])
    normalized = value.upper()
    if not SHA256_PATTERN.fullmatch(normalized):
        raise ContractViolation("SHA256_FORMAT_INVALID", [field])
    return normalized


def definition_schema(core_schema: Mapping[str, Any], object_type: str) -> dict[str, Any]:
    definitions = core_schema.get("$defs")
    if not isinstance(definitions, Mapping) or object_type not in definitions:
        raise ContractViolation("Initialization_DEFINITION_NOT_FOUND", [object_type])
    schema: dict[str, Any] = {
        "$schema": core_schema.get(
            "$schema", "https://json-schema.org/draft/2020-12/schema"
        ),
        "$ref": f"#/$defs/{object_type}",
        "$defs": deepcopy(dict(definitions)),
    }
    return schema


def validate_initialization_object(
    value: Mapping[str, Any],
    object_type: str,
    core_schema: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractViolation("Initialization_OBJECT_TYPE_INVALID", [object_type])
    instance = immutable_copy(value)
    schema = definition_schema(core_schema, object_type)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    errors = sorted(
        validator.iter_errors(instance),
        key=lambda error: (
            tuple(str(part) for part in error.absolute_path),
            error.message,
        ),
    )
    if errors:
        details = []
        for error in errors[:20]:
            path = "/".join(str(part) for part in error.absolute_path) or "$"
            details.append(f"{path}: {error.message}")
        raise ContractViolation("Initialization_SCHEMA_INVALID", details)
    if instance.get("object_type") != object_type:
        raise ContractViolation("Initialization_OBJECT_DISCRIMINATOR_MISMATCH", [object_type])
    return instance


def base_object(
    object_type: str,
    object_id: str,
    *,
    producer: str,
    single_writer: str,
    consumers: Sequence[str],
    clock: Clock = utc_now,
    source_evidence_refs: Sequence[str] = (),
    extensions: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not object_id:
        raise ContractViolation("OBJECT_ID_EMPTY")
    return {
        "object_type": object_type,
        "object_id": object_id,
        "revision": "candidate001",
        "producer": producer,
        "single_writer": single_writer,
        "consumers": list(consumers),
        "created_at": clock(),
        "supersedes_ref": None,
        "source_evidence_refs": list(source_evidence_refs),
        "extensions": immutable_copy(extensions or {}),
    }
