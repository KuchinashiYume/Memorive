from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Mapping

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .contracts import (
    base_object,
    canonical_sha256,
    immutable_copy,
    utc_now,
    validate_initialization_object,
)
from .runner import ProcessRun


class OutputNormalizer:
    VERSION = "EXECUTION_CORE_NORMALIZATION_V1"

    def __init__(
        self,
        core_schema: Mapping[str, Any],
        *,
        clock: Callable[[], str] = utc_now,
    ):
        self._core_schema = immutable_copy(core_schema)
        self._clock = clock

    def normalize(
        self,
        process_run: ProcessRun,
        *,
        logical_role: str,
        payload_schema_ref: str,
        payload_schema: Mapping[str, Any],
    ) -> dict[str, Any]:
        receipt = validate_initialization_object(
            process_run.receipt, "ProcessExecutionReceipt", self._core_schema
        )
        schema_copy = immutable_copy(payload_schema)
        schema_hash = canonical_sha256(schema_copy)
        raw_hash = hashlib.sha256(process_run.stdout).hexdigest().upper()
        payload: dict[str, Any] = {}
        warnings: list[str] = []
        error_code: str | None = None

        if not receipt["process_started"]:
            error_code = receipt.get("error_code") or "WORKSPACE_POLICY_VIOLATION"
        elif receipt.get("timeout"):
            error_code = "PROCESS_TIMEOUT"
        elif receipt.get("extensions", {}).get("raw_truncated"):
            error_code = receipt.get("error_code") or "WORKSPACE_POLICY_VIOLATION"
        elif receipt.get("exit_code") != 0:
            error_code = receipt.get("error_code") or "PROCESS_EXIT_NONZERO"
        else:
            try:
                text = process_run.stdout.decode("utf-8", errors="strict")
                decoded = json.loads(text)
                if not isinstance(decoded, dict):
                    raise ValueError("normalized payload must be a JSON object")
                Draft202012Validator.check_schema(schema_copy)
                errors = sorted(
                    Draft202012Validator(schema_copy).iter_errors(decoded),
                    key=lambda item: (list(item.absolute_path), item.message),
                )
                if errors:
                    for item in errors[:20]:
                        path = "/".join(str(part) for part in item.absolute_path) or "$"
                        warnings.append(f"{path}: {item.message}")
                    error_code = "OUTPUT_SCHEMA_INVALID"
                else:
                    payload = immutable_copy(decoded)
            except UnicodeDecodeError as exc:
                warnings.append(f"UTF8_DECODE_ERROR:{exc.start}")
                error_code = "OUTPUT_SCHEMA_INVALID"
            except json.JSONDecodeError as exc:
                warnings.append(f"JSON_DECODE_ERROR:{exc.pos}")
                error_code = "OUTPUT_SCHEMA_INVALID"
            except (SchemaError, ValueError) as exc:
                warnings.append(type(exc).__name__)
                error_code = "OUTPUT_SCHEMA_INVALID"

        result = base_object(
            "NormalizedResult",
            f"normalizedresult_{receipt['job_id']}_{receipt['attempt_id']}",
            producer="Execution_EVIDENCE_ADAPTER",
            single_writer="Execution_EVIDENCE_ADAPTER",
            consumers=("RUNTIME_LOG", "ARTIFACT_REGISTRY", "CALLING_MODULE"),
            clock=self._clock,
            source_evidence_refs=(receipt["object_id"],),
            extensions={
                "raw_stdout_bytes": len(process_run.stdout),
                "raw_stderr_bytes": len(process_run.stderr),
                "model_repair_attempted": False,
            },
        )
        result.update(
            {
                "job_id": receipt["job_id"],
                "attempt_id": receipt["attempt_id"],
                "logical_role": logical_role,
                "payload": payload,
                "payload_schema_ref": payload_schema_ref,
                "payload_schema_hash": schema_hash,
                "payload_valid": error_code is None,
                "source_output_hash": raw_hash,
                "normalization_version": self.VERSION,
                "warnings": warnings,
                "error_code": error_code,
            }
        )
        return validate_initialization_object(result, "NormalizedResult", self._core_schema)
