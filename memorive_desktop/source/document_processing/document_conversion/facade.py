"""Single guarded facade for Intake document conversion."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

import yaml

from .adapters import adapt_document
from .models import (
    ConversionAmendment,
    ConversionCandidate,
    ConversionReceipt,
    ConversionResult,
    DocumentConversionError,
    ExecutionStatus,
    FormatVerdict,
    RecognitionStatus,
    SelectionDecision,
    SelectionStatus,
    canonical_json,
    now_iso,
    stable_identifier,
)
from .registry import CANONICAL_FORMATS, probe_document


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _safe_paper_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise DocumentConversionError("PAPER_ID_INVALID", "paper_id must be a safe 1..128 ASCII identifier")
    return value


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def compare_candidates(candidates: list[ConversionCandidate]) -> SelectionDecision:
    if not candidates:
        return SelectionDecision(None, SelectionStatus.NO_OUTPUT.value, None, None, ("NO_CANDIDATE",))
    primary = candidates[0]

    def usable(item: ConversionCandidate) -> bool:
        return (
            item.execution_status == ExecutionStatus.COMPLETED.value
            and item.format_verdict in {FormatVerdict.SUPPORTED.value, FormatVerdict.DEGRADED.value}
            and not item.hard_failures
            and bool(item.output_sha256)
        )

    if usable(primary):
        return SelectionDecision(
            primary.candidate_id,
            SelectionStatus.PRIMARY_SELECTED.value,
            primary.candidate_id,
            None,
            ("PRIMARY_USABLE_RETAINED",),
        )
    qualified_fallbacks = [candidate for candidate in candidates[1:] if usable(candidate)]
    if qualified_fallbacks:
        fallback = qualified_fallbacks[0]
        return SelectionDecision(
            fallback.candidate_id,
            SelectionStatus.FALLBACK_SELECTED.value,
            primary.candidate_id,
            fallback.candidate_id,
            ("PRIMARY_UNUSABLE", "ONE_QUALIFIED_FALLBACK_SELECTED"),
        )
    return SelectionDecision(
        None,
        SelectionStatus.NO_OUTPUT.value,
        primary.candidate_id,
        None,
        ("NO_QUALIFIED_OUTPUT",),
    )


class DocumentConversionFacade:
    """Candidate-only facade; every write is contained under ``allowed_root``."""

    def __init__(self, *, allowed_root: str | Path, profile_id: str = "document_formats_local_v1") -> None:
        self.allowed_root = Path(allowed_root).resolve()
        self.allowed_root.mkdir(parents=True, exist_ok=True)
        self.profile_id = profile_id

    def probe(self, source: str | Path):
        return probe_document(source)

    def _guard_target(self, target_root: str | Path) -> Path:
        target = Path(target_root).resolve()
        if not _inside(target, self.allowed_root):
            raise DocumentConversionError(
                "TARGET_OUTSIDE_ALLOWED_ROOT",
                f"target root escapes allowed root: {target}",
                details={"allowed_root": str(self.allowed_root), "target_root": str(target)},
            )
        return target

    def convert_document(
        self,
        source: Any,
        *,
        paper_id: str,
        title: str,
        target_root: str | Path,
        data_ownership: str | None = None,
        document_profile: str = "literature",
    ) -> ConversionResult:
        paper_id = _safe_paper_id(paper_id)
        source_path = Path(source.path if hasattr(source, "path") else source)
        source_meta = getattr(source, "meta", None)
        ownership = data_ownership or getattr(source_meta, "data_ownership", None)
        if ownership not in {"self", "entrusted"}:
            raise DocumentConversionError("DATA_OWNERSHIP_REQUIRED", "data_ownership must be self or entrusted")
        target = self._guard_target(target_root)
        probe = probe_document(source_path).require_match()
        final_root = target / paper_id
        if final_root.exists():
            raise DocumentConversionError("TARGET_ALREADY_EXISTS", f"refuse to overwrite candidate: {final_root}")
        target.mkdir(parents=True, exist_ok=True)
        temporary = target / f".intake-{paper_id}-{uuid.uuid4().hex}"
        temporary.mkdir()
        try:
            prefix = "PDF" if probe.detected_format == "pdf" else "Original"
            original = temporary / f"[{prefix}] {paper_id}{probe.path.suffix.lower()}"
            shutil.copy2(probe.path, original)
            if _sha256(original) != probe.source_sha256:
                raise DocumentConversionError("ORIGINAL_COPY_HASH_MISMATCH", "preserved original hash mismatch")

            payload = adapt_document(probe, workdir=temporary)
            if payload.hard_failures:
                raise DocumentConversionError(
                    "ADAPTER_HARD_FAILURE",
                    "adapter returned hard failures",
                    details={"hard_failures": list(payload.hard_failures)},
                )
            body_sha = hashlib.sha256(payload.text.encode("utf-8")).hexdigest().upper()
            config_projection = {
                "profile_id": self.profile_id,
                "format_key": probe.detected_format,
                "engine": payload.engine,
                "anchor_kind": payload.anchor_kind,
                "network": "DENY_ALL",
                "fallback_limit": 1,
            }
            config_hash = hashlib.sha256(canonical_json(config_projection).encode("utf-8")).hexdigest().upper()
            receipt_id = stable_identifier(
                "IntakeR-",
                {
                    "source_sha256": probe.source_sha256,
                    "output_sha256": body_sha,
                    "profile_id": self.profile_id,
                    "config_hash": config_hash,
                },
            )
            frontmatter = {
                "paper_id": paper_id,
                "title": title,
                "data_ownership": ownership,
                "doc_type": document_profile,
                "original_format": probe.detected_format,
                "review_status": "pending",
                "convert_engine": payload.engine,
                "converted_at": now_iso(),
                "convert_receipt_id": receipt_id,
                "source_sha256": probe.source_sha256,
                "converter_profile_id": self.profile_id,
            }
            rawmd = temporary / f"[RawMD] {paper_id}.md"
            rawmd.write_text(
                "---\n"
                + yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False)
                + "---\n"
                + payload.text.rstrip()
                + "\n",
                encoding="utf-8",
            )
            rawmd_sha = _sha256(rawmd)
            receipt = ConversionReceipt(
                receipt_id=receipt_id,
                created_at=now_iso(),
                source_path_projection=probe.path.name,
                source_sha256=probe.source_sha256,
                source_size=probe.source_size,
                detected_format=probe.detected_format or "unknown",
                declared_extension=probe.declared_extension,
                signature_match=probe.signature_match,
                data_ownership=ownership,
                document_profile=document_profile,
                converter_profile_id=self.profile_id,
                engine=payload.engine,
                engine_version=payload.engine_version,
                runtime_version=platform.python_version(),
                config_hash=config_hash,
                execution_status=ExecutionStatus.COMPLETED.value,
                format_verdict=payload.format_verdict,
                selection_status=payload.selection_status,
                recognition_status=payload.recognition_status,
                output_sha256=body_sha,
                rawmd_sha256=rawmd_sha,
                cleanmd_sha256=None,
                anchor_kind=payload.anchor_kind,
                anchor_count=len(payload.anchor_map),
                anchor_map=payload.anchor_map,
                hard_failures=payload.hard_failures,
                degradation_signals=payload.degradation_signals,
                warnings=payload.warnings,
                primary_receipt_id=(
                    stable_identifier(
                        "IntakeR-",
                        {
                            "source_sha256": probe.source_sha256,
                            "profile_id": self.profile_id,
                            "candidate_engine": payload.primary_engine,
                            "candidate_status": "failed",
                        },
                    )
                    if payload.selection_status == SelectionStatus.FALLBACK_SELECTED.value
                    else None
                ),
                fallback_receipt_id=(
                    receipt_id
                    if payload.selection_status == SelectionStatus.FALLBACK_SELECTED.value
                    else None
                ),
                selection_reason=payload.selection_reason,
                legacy_bridge_receipt=payload.legacy_bridge_receipt,
                image_frame_manifest=(payload.image_frame_manifest.to_dict() if payload.image_frame_manifest else None),
                nondeterminism_class=payload.nondeterminism_class,
                evidence_refs=(f"source:{probe.source_sha256}", f"profile:{self.profile_id}"),
            )
            receipt_path = temporary / f"[ConversionReceipt] {paper_id}.json"
            receipt_path.write_text(json.dumps(receipt.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            if payload.selection_status == SelectionStatus.FALLBACK_SELECTED.value:
                selection_trace = {
                    "schema_version": "DocumentFormatsCandidateSelectionTrace-v1",
                    "source_sha256": probe.source_sha256,
                    "primary_receipt_id": receipt.primary_receipt_id,
                    "primary_engine": payload.primary_engine,
                    "primary_execution_status": "completed",
                    "primary_format_verdict": "unsupported",
                    "primary_hard_failures": ["EMPTY_TEXT_OUTPUT"],
                    "fallback_receipt_id": receipt.fallback_receipt_id,
                    "fallback_engine": payload.fallback_engine,
                    "fallback_selected": True,
                    "selection_reason": payload.selection_reason,
                }
                (temporary / f"[CandidateSelectionTrace] {paper_id}.json").write_text(
                    json.dumps(selection_trace, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            os.replace(temporary, final_root)
        except Exception as exc:
            if temporary.exists():
                failed_root = target / f".failed-{paper_id}-{uuid.uuid4().hex}"
                os.replace(temporary, failed_root)
                if isinstance(exc, DocumentConversionError):
                    exc.details.setdefault("failed_candidate_root", str(failed_root))
            raise
        return ConversionResult(
            output_root=final_root,
            rawmd_path=final_root / rawmd.name,
            original_path=final_root / original.name,
            receipt_path=final_root / receipt_path.name,
            receipt=receipt,
        )

    def diagnose_conversion(self, result: ConversionResult) -> dict[str, Any]:
        return {
            "receipt_id": result.receipt.receipt_id,
            "hard_failures": list(result.receipt.hard_failures),
            "degradation_signals": list(result.receipt.degradation_signals),
            "warnings": list(result.receipt.warnings),
            "review_required": bool(result.receipt.hard_failures or result.receipt.degradation_signals),
        }

    def amend_conversion(
        self,
        *,
        receipt: ConversionReceipt,
        new_output_sha256: str,
        decision: str,
        reason: str,
        evidence_refs: list[str],
        decision_maker: str,
        amendment_root: str | Path,
    ) -> Path:
        root = self._guard_target(amendment_root)
        root.mkdir(parents=True, exist_ok=True)
        amendment = ConversionAmendment.create(
            supersedes_receipt_id=receipt.receipt_id,
            old_output_sha256=receipt.output_sha256 or "0" * 64,
            new_output_sha256=new_output_sha256,
            decision=decision,
            reason=reason,
            evidence_refs=evidence_refs,
            decision_maker=decision_maker,
        )
        path = root / f"[ConversionAmendment] {amendment.amendment_id}.json"
        if path.exists():
            raise DocumentConversionError("AMENDMENT_ALREADY_EXISTS", f"refuse overwrite: {path}")
        path.write_text(json.dumps(amendment.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path


def convert_document(source: Any, **kwargs) -> ConversionResult:
    allowed_root = kwargs.pop("allowed_root")
    return DocumentConversionFacade(allowed_root=allowed_root).convert_document(source, **kwargs)


def diagnose_conversion(result: ConversionResult) -> dict[str, Any]:
    return {
        "receipt_id": result.receipt.receipt_id,
        "hard_failures": list(result.receipt.hard_failures),
        "degradation_signals": list(result.receipt.degradation_signals),
    }
