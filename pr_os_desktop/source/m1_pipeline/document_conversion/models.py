"""P06/T05 durable objects and orthogonal conversion state axes."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import platform
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any


class _ValueEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class ExecutionStatus(_ValueEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class FormatVerdict(_ValueEnum):
    SUPPORTED = "supported"
    DEGRADED = "degraded"
    UNSUPPORTED = "unsupported"


class SelectionStatus(_ValueEnum):
    PRIMARY_SELECTED = "primary_selected"
    FALLBACK_SELECTED = "fallback_selected"
    NOT_SELECTED = "not_selected"
    NO_OUTPUT = "no_output"


class RecognitionStatus(_ValueEnum):
    NOT_APPLICABLE = "not_applicable"
    NOT_RUN = "not_run"
    NOT_RUN_REQUIRES_T07 = "not_run_requires_t07"


class DocumentConversionError(RuntimeError):
    """Controlled, code-bearing T05 failure."""

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_identifier(prefix: str, value: object, length: int = 24) -> str:
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}{digest[:length]}"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class DocumentProbe:
    path: Path = field(repr=False, compare=False)
    source_sha256: str
    source_size: int
    declared_extension: str
    detected_format: str | None
    family: str | None
    status: str
    signature_match: bool
    error_code: str | None = None
    diagnostics: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path_projection": self.path.name,
            "source_sha256": self.source_sha256,
            "source_size": self.source_size,
            "declared_extension": self.declared_extension,
            "detected_format": self.detected_format,
            "family": self.family,
            "status": self.status,
            "signature_match": self.signature_match,
            "error_code": self.error_code,
            "diagnostics": list(self.diagnostics),
        }

    def require_match(self) -> "DocumentProbe":
        if self.status != "match":
            raise DocumentConversionError(
                self.error_code or "FORMAT_PROBE_FAILED",
                f"document probe did not match: {self.status}",
                details=self.to_dict(),
            )
        return self


@dataclass(frozen=True)
class ImageFrameManifest:
    manifest_id: str
    format_key: str
    frame_count: int
    frames: tuple[dict[str, Any], ...]
    source_sha256: str
    derivative_sha256: str | None
    transform: str
    recognition_status: str = RecognitionStatus.NOT_RUN_REQUIRES_T07.value

    def to_dict(self) -> dict[str, Any]:
        value = dataclasses.asdict(self)
        value["frames"] = [dict(item) for item in self.frames]
        return value


@dataclass(frozen=True)
class AdapterPayload:
    text: str
    anchor_kind: str
    anchor_map: tuple[dict[str, Any], ...]
    engine: str
    engine_version: str
    format_verdict: str
    warnings: tuple[str, ...] = ()
    degradation_signals: tuple[str, ...] = ()
    hard_failures: tuple[str, ...] = ()
    recognition_status: str = RecognitionStatus.NOT_APPLICABLE.value
    legacy_bridge_receipt: dict[str, Any] | None = None
    image_frame_manifest: ImageFrameManifest | None = None
    nondeterminism_class: str = "DETERMINISTIC_SEMANTIC_OUTPUT"
    selection_status: str = SelectionStatus.PRIMARY_SELECTED.value
    primary_engine: str | None = None
    fallback_engine: str | None = None
    selection_reason: str = "PRIMARY_PROFILE_COMPLETED_WITHOUT_HARD_FAILURE"


@dataclass(frozen=True)
class ConversionReceipt:
    receipt_id: str
    created_at: str
    source_path_projection: str
    source_sha256: str
    source_size: int
    detected_format: str
    declared_extension: str
    signature_match: bool
    data_ownership: str
    document_profile: str
    converter_profile_id: str
    engine: str
    engine_version: str
    runtime_version: str
    config_hash: str
    execution_status: str
    format_verdict: str
    selection_status: str
    recognition_status: str
    output_sha256: str | None
    rawmd_sha256: str | None
    cleanmd_sha256: str | None
    anchor_kind: str
    anchor_count: int
    anchor_map: tuple[dict[str, Any], ...]
    hard_failures: tuple[str, ...]
    degradation_signals: tuple[str, ...]
    warnings: tuple[str, ...]
    primary_receipt_id: str | None
    fallback_receipt_id: str | None
    selection_reason: str
    legacy_bridge_receipt: dict[str, Any] | None
    image_frame_manifest: dict[str, Any] | None
    nondeterminism_class: str
    evidence_refs: tuple[str, ...]
    schema_version: str = "P06T05ConversionReceipt-v1"

    def to_dict(self) -> dict[str, Any]:
        value = dataclasses.asdict(self)
        for key in ("anchor_map", "hard_failures", "degradation_signals", "warnings", "evidence_refs"):
            value[key] = list(value[key])
        return value

    @classmethod
    def minimal_for_test(cls, *, source_sha256: str, output_sha256: str, detected_format: str) -> "ConversionReceipt":
        identity = {
            "source_sha256": source_sha256,
            "output_sha256": output_sha256,
            "detected_format": detected_format,
            "profile": "test-v1",
        }
        return cls(
            receipt_id=stable_identifier("T05R-", identity),
            created_at="1970-01-01T00:00:00+00:00",
            source_path_projection="fixture",
            source_sha256=source_sha256,
            source_size=1,
            detected_format=detected_format,
            declared_extension=f".{detected_format}",
            signature_match=True,
            data_ownership="self",
            document_profile="test",
            converter_profile_id="test-v1",
            engine="test",
            engine_version="1",
            runtime_version=platform.python_version(),
            config_hash="3" * 64,
            execution_status=ExecutionStatus.COMPLETED.value,
            format_verdict=FormatVerdict.SUPPORTED.value,
            selection_status=SelectionStatus.PRIMARY_SELECTED.value,
            recognition_status=RecognitionStatus.NOT_APPLICABLE.value,
            output_sha256=output_sha256,
            rawmd_sha256=output_sha256,
            cleanmd_sha256=None,
            anchor_kind="line",
            anchor_count=1,
            anchor_map=({"kind": "line", "ordinal": 1},),
            hard_failures=(),
            degradation_signals=(),
            warnings=(),
            primary_receipt_id=None,
            fallback_receipt_id=None,
            selection_reason="test primary",
            legacy_bridge_receipt=None,
            image_frame_manifest=None,
            nondeterminism_class="DETERMINISTIC_SEMANTIC_OUTPUT",
            evidence_refs=("test",),
        )


@dataclass(frozen=True)
class ConversionAmendment:
    amendment_id: str
    created_at: str
    supersedes_receipt_id: str
    old_output_sha256: str
    new_output_sha256: str
    decision: str
    reason: str
    evidence_refs: tuple[str, ...]
    decision_maker: str
    schema_version: str = "P06T05ConversionAmendment-v1"

    @classmethod
    def create(
        cls,
        *,
        supersedes_receipt_id: str,
        old_output_sha256: str,
        new_output_sha256: str,
        decision: str,
        reason: str,
        evidence_refs: list[str],
        decision_maker: str,
    ) -> "ConversionAmendment":
        identity = {
            "supersedes_receipt_id": supersedes_receipt_id,
            "old_output_sha256": old_output_sha256,
            "new_output_sha256": new_output_sha256,
            "decision": decision,
            "reason": reason,
            "evidence_refs": evidence_refs,
            "decision_maker": decision_maker,
        }
        return cls(
            amendment_id=stable_identifier("T05A-", identity),
            created_at=now_iso(),
            supersedes_receipt_id=supersedes_receipt_id,
            old_output_sha256=old_output_sha256,
            new_output_sha256=new_output_sha256,
            decision=decision,
            reason=reason,
            evidence_refs=tuple(evidence_refs),
            decision_maker=decision_maker,
        )

    def to_dict(self) -> dict[str, Any]:
        value = dataclasses.asdict(self)
        value["evidence_refs"] = list(value["evidence_refs"])
        return value


@dataclass(frozen=True)
class ConversionResult:
    output_root: Path
    rawmd_path: Path
    original_path: Path
    receipt_path: Path
    receipt: ConversionReceipt

    @property
    def pdf_path(self) -> Path | None:
        """Read-only compatibility projection for existing PDF callers."""
        return self.original_path if self.original_path.name.startswith("[PDF]") else None


@dataclass(frozen=True)
class ConversionCandidate:
    candidate_id: str
    execution_status: str
    format_verdict: str
    hard_failures: list[str]
    output_sha256: str | None


@dataclass(frozen=True)
class SelectionDecision:
    selected_candidate_id: str | None
    selection_status: str
    primary_candidate_id: str | None
    fallback_candidate_id: str | None
    reason_codes: tuple[str, ...]
