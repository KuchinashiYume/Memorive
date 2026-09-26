"""Read-only Core adapters for the ARTIFACT_REGISTRY candidate registry."""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from .errors import AdapterError, FrontmatterParseError
from .registry_v2 import ArtifactRegistry
from .schema_v2 import (
    build_envelope,
    legacy_artifact_id,
    new_artifact_id,
    parent_link,
    sha256_file,
    unresolved_requirement,
)

_FENCE = re.compile(r"(?m)^---[ \t]*\r?$")
_TAGS = ("PDF", "RawMD", "CleanMD", "Chunks", "Card", "Analysis")
_TYPES = {
    "PDF": "source_pdf",
    "RawMD": "raw_markdown",
    "CleanMD": "clean_markdown",
    "Chunks": "chunks_jsonl",
    "Card": "card_markdown",
    "Analysis": "analysis_markdown",
}


@dataclass(frozen=True)
class AdaptationResult:
    envelopes: tuple[dict[str, Any], ...]
    mapping_summary: dict[str, Any]

    def by_type(self, artifact_type: str) -> dict[str, Any]:
        hits = [x for x in self.envelopes if x["artifact_type"] == artifact_type]
        if len(hits) != 1:
            raise KeyError(f"expected one {artifact_type!r} envelope, found {len(hits)}")
        return copy.deepcopy(hits[0])


def read_frontmatter(path: Path | str) -> dict[str, Any]:
    """Parse the first two standalone fences without mutating the source file."""

    source = Path(path)
    text = source.read_text(encoding="utf-8")
    fences = list(_FENCE.finditer(text))
    if len(fences) < 2 or fences[0].start() != 0:
        raise FrontmatterParseError(
            f"frontmatter requires two standalone fences at file start: {source}"
        )
    try:
        value = yaml.safe_load(text[fences[0].end() : fences[1].start()])
    except yaml.YAMLError as exc:
        raise FrontmatterParseError(f"invalid YAML frontmatter: {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise FrontmatterParseError(f"frontmatter must be a mapping: {source}")
    return value


def find_fixture_files(folder: Path | str) -> dict[str, Path]:
    """Find one file per semantic role; the role is not inferred as lineage."""

    root = Path(folder)
    if not root.is_dir():
        raise AdapterError(f"fixture folder does not exist: {root}")
    result: dict[str, Path] = {}
    for tag in _TAGS:
        hits = sorted(path for path in root.iterdir() if path.is_file() and path.name.startswith(f"[{tag}]"))
        if len(hits) != 1:
            raise AdapterError(f"expected exactly one [{tag}] fixture, found {len(hits)}")
        result[tag] = hits[0]
    return result


class P1LegacyAdapter:
    """Adapt one authoritative CORE six-file chain without rewriting it."""

    def __init__(
        self,
        *,
        authority_run_ref: str,
        registered_at: str,
        release_manifest_ref: str,
    ) -> None:
        self.authority_run_ref = authority_run_ref
        self.registered_at = registered_at
        self.release_manifest_ref = release_manifest_ref

    def adapt_bonakdarpour(self, folder: Path | str) -> AdaptationResult:
        files = find_fixture_files(folder)
        hashes = {tag: sha256_file(path) for tag, path in files.items()}
        raw_fm = read_frontmatter(files["RawMD"])
        clean_fm = read_frontmatter(files["CleanMD"])
        card_fm = read_frontmatter(files["Card"])
        analysis_fm = read_frontmatter(files["Analysis"])
        paper_id = _required_text(raw_fm, "paper_id", files["RawMD"])
        for label, value in (
            ("CleanMD.paper_id", clean_fm.get("paper_id")),
            ("Card.source_anchor.paper_id", (card_fm.get("source_anchor") or {}).get("paper_id")),
            ("Analysis.paper_id", analysis_fm.get("paper_id")),
        ):
            if value != paper_id:
                raise AdapterError(f"{label} does not match RawMD paper_id {paper_id!r}")

        ids = {
            tag: legacy_artifact_id(
                run_ref=self.authority_run_ref,
                artifact_type=_TYPES[tag],
                content_hash=hashes[tag],
                identity_scope=f"{paper_id}:{tag}",
            )
            for tag in _TAGS
        }
        common_evidence = [self.release_manifest_ref]

        pdf = build_envelope(
            artifact_id=ids["PDF"],
            artifact_type=_TYPES["PDF"],
            content_hash=hashes["PDF"],
            locator_path=files["PDF"],
            registered_at=self.registered_at,
            run_ref=self.authority_run_ref,
            paper_ids=[paper_id],
            created_at=None,
            legacy_import=True,
            evidence_refs=common_evidence + [str(files["PDF"])],
            metadata={"legacy_role": "PDF", "original_format": "pdf"},
        )

        raw = build_envelope(
            artifact_id=ids["RawMD"],
            artifact_type=_TYPES["RawMD"],
            content_hash=hashes["RawMD"],
            locator_path=files["RawMD"],
            registered_at=self.registered_at,
            run_ref=self.authority_run_ref,
            paper_ids=[paper_id],
            created_at=_timestamp_or_none(raw_fm.get("converted_at"), files["RawMD"]),
            unresolved_parent_requirements=[
                unresolved_requirement(
                    "exact_pdf_parent",
                    "CORE_RAWMD_HAS_FORMAT_BUT_NO_EXACT_PDF_ARTIFACT_REF",
                    evidence_refs=[str(files["RawMD"])],
                    candidate_artifact_ids=[ids["PDF"]],
                    search_hints=["same folder and filename are not sufficient evidence"],
                )
            ],
            legacy_import=True,
            evidence_refs=common_evidence + [str(files["RawMD"])],
            metadata={
                "legacy_role": "RawMD",
                "convert_engine": raw_fm.get("convert_engine"),
                "original_format": raw_fm.get("original_format"),
            },
        )

        clean_links: list[dict[str, Any]] = []
        clean_unresolved: list[dict[str, Any]] = []
        source_rawmd = clean_fm.get("source_rawmd")
        if source_rawmd == files["RawMD"].name:
            clean_links.append(
                parent_link(
                    parent_artifact_id=ids["RawMD"],
                    parent_content_hash=hashes["RawMD"],
                    relation="cleaned_from",
                    evidence_kind="frontmatter_field",
                    source_field="source_rawmd",
                    evidence_ref=str(files["CleanMD"]),
                    details={"value": source_rawmd},
                )
            )
        else:
            clean_unresolved.append(
                unresolved_requirement(
                    "rawmd_parent",
                    "SOURCE_RAWMD_DOES_NOT_MATCH_REGISTERED_FIXTURE",
                    evidence_refs=[str(files["CleanMD"])],
                    search_hints=[str(source_rawmd)],
                )
            )
        clean = build_envelope(
            artifact_id=ids["CleanMD"],
            artifact_type=_TYPES["CleanMD"],
            content_hash=hashes["CleanMD"],
            locator_path=files["CleanMD"],
            registered_at=self.registered_at,
            run_ref=self.authority_run_ref,
            paper_ids=[paper_id],
            source_artifact_ids=[ids["RawMD"]] if clean_links else [],
            created_at=_timestamp_or_none(clean_fm.get("cleaned_at"), files["CleanMD"]),
            parent_artifacts=clean_links,
            unresolved_parent_requirements=clean_unresolved,
            legacy_import=True,
            evidence_refs=common_evidence + [str(files["CleanMD"])],
            metadata={"legacy_role": "CleanMD", "source_rawmd": source_rawmd},
        )

        chunks = _read_chunks(files["Chunks"], expected_paper_id=paper_id)
        source_files = sorted({item["source_file"] for item in chunks})
        chunks_links: list[dict[str, Any]] = []
        chunks_unresolved: list[dict[str, Any]] = []
        if source_files == [files["CleanMD"].name]:
            chunks_links.append(
                parent_link(
                    parent_artifact_id=ids["CleanMD"],
                    parent_content_hash=hashes["CleanMD"],
                    relation="chunked_from",
                    evidence_kind="jsonl_member_field",
                    source_field="source_file",
                    evidence_ref=str(files["Chunks"]),
                    details={"member_count": len(chunks), "unique_values": source_files},
                )
            )
        else:
            chunks_unresolved.append(
                unresolved_requirement(
                    "cleanmd_parent",
                    "CHUNKS_SOURCE_FILE_NOT_UNIQUE_OR_NOT_REGISTERED",
                    evidence_refs=[str(files["Chunks"])],
                    search_hints=source_files,
                )
            )
        chunks_envelope = build_envelope(
            artifact_id=ids["Chunks"],
            artifact_type=_TYPES["Chunks"],
            schema_ref="chunk_schema_version:2",
            content_hash=hashes["Chunks"],
            locator_path=files["Chunks"],
            registered_at=self.registered_at,
            run_ref=self.authority_run_ref,
            paper_ids=[paper_id],
            source_artifact_ids=[ids["CleanMD"]] if chunks_links else [],
            created_at=_timestamp_or_none(
                _single_or_none(item.get("created_at") for item in chunks),
                files["Chunks"],
            ),
            parent_artifacts=chunks_links,
            unresolved_parent_requirements=chunks_unresolved,
            legacy_import=True,
            evidence_refs=common_evidence + [str(files["Chunks"])],
            metadata={
                "legacy_role": "Chunks",
                "chunk_count": len(chunks),
                "chunk_ids": [item["chunk_id"] for item in chunks],
                "member_chunk_hashes": {
                    item["chunk_id"]: item["chunk_hash"] for item in chunks
                },
            },
        )

        chunk_by_id = {item["chunk_id"]: item for item in chunks}
        anchors = _card_anchors(card_fm, files["Card"])
        missing_anchor_ids = sorted(
            {item["chunk_id"] for item in anchors if item["chunk_id"] not in chunk_by_id}
        )
        quote_mismatches = sorted(
            item["chunk_id"]
            for item in anchors
            if item.get("quote")
            and item["chunk_id"] in chunk_by_id
            and item["quote"] not in chunk_by_id[item["chunk_id"]]["text"]
        )
        card_links: list[dict[str, Any]] = []
        card_unresolved = [
            unresolved_requirement(
                "producing_run",
                "CARD_HAS_NO_RUN_OR_FROZEN_MANIFEST_ARTIFACT_REF",
                evidence_refs=[str(files["Card"])],
            )
        ]
        card_conflicts: list[dict[str, Any]] = []
        if not missing_anchor_ids and not quote_mismatches:
            card_links.append(
                parent_link(
                    parent_artifact_id=ids["Chunks"],
                    parent_content_hash=hashes["Chunks"],
                    relation="distilled_from_chunks",
                    evidence_kind="source_anchor_resolution",
                    source_field="source_anchor.by_field[].chunk_id+quote",
                    evidence_ref=str(files["Card"]),
                    details={
                        "anchor_entries": len(anchors),
                        "unique_chunk_ids": len({x["chunk_id"] for x in anchors}),
                        "nonempty_quotes": sum(bool(x.get("quote")) for x in anchors),
                    },
                )
            )
        else:
            card_unresolved.append(
                unresolved_requirement(
                    "source_chunks",
                    "CARD_SOURCE_ANCHORS_DO_NOT_FULLY_RESOLVE",
                    evidence_refs=[str(files["Card"]), str(files["Chunks"])],
                    candidate_artifact_ids=[ids["Chunks"]],
                    search_hints=missing_anchor_ids + quote_mismatches,
                )
            )
            if quote_mismatches:
                card_conflicts.append(
                    {
                        "requirement": "source_chunks",
                        "reason_code": "CARD_QUOTE_CONFLICTS_WITH_CHUNK_TEXT",
                        "candidate_artifact_ids": [ids["Chunks"]],
                        "evidence_refs": [str(files["Card"]), str(files["Chunks"])],
                    }
                )
        card = build_envelope(
            artifact_id=ids["Card"],
            artifact_type=_TYPES["Card"],
            schema_ref=f"contracts/card_schema.md#schema_version={card_fm.get('schema_version')}",
            content_hash=hashes["Card"],
            locator_path=files["Card"],
            registered_at=self.registered_at,
            run_ref=None,
            paper_ids=[paper_id],
            source_artifact_ids=[ids["Chunks"]] if card_links else [],
            created_at=None,
            parent_artifacts=card_links,
            unresolved_parent_requirements=card_unresolved,
            conflicting_candidates=card_conflicts,
            legacy_import=True,
            evidence_refs=common_evidence + [str(files["Card"]), str(files["Chunks"])],
            metadata={
                "legacy_role": "Card",
                "review_status_snapshot": card_fm.get("review_status"),
                "review_status_owner": "KNOWLEDGE_ADMISSION",
                "schema_version": card_fm.get("schema_version"),
                "anchor_entries": len(anchors),
                "missing_anchor_ids": missing_anchor_ids,
                "quote_mismatches": quote_mismatches,
            },
        )

        context_pack = analysis_fm.get("context_pack")
        if not isinstance(context_pack, dict):
            raise AdapterError(f"Analysis context_pack must be a mapping: {files['Analysis']}")
        context_ids = context_pack.get("chunk_ids")
        if not isinstance(context_ids, list) or not all(
            isinstance(value, str) and value for value in context_ids
        ):
            raise AdapterError(f"Analysis context_pack.chunk_ids invalid: {files['Analysis']}")
        missing_context_ids = sorted(value for value in context_ids if value not in chunk_by_id)
        analysis_links: list[dict[str, Any]] = []
        analysis_unresolved = [
            unresolved_requirement(
                "exact_card_version",
                "ANALYSIS_HAS_NO_CARD_ARTIFACT_ID_OR_HASH",
                evidence_refs=[str(files["Analysis"])],
                candidate_artifact_ids=[ids["Card"]],
                search_hints=["paper_id equality cannot prove an exact Card version"],
            ),
            unresolved_requirement(
                "independent_context_pack_artifact",
                "CONTEXT_PACK_IS_EMBEDDED_NOT_INDEPENDENTLY_PERSISTED",
                evidence_refs=[str(files["Analysis"])],
            ),
        ]
        if not missing_context_ids:
            analysis_links.append(
                parent_link(
                    parent_artifact_id=ids["Chunks"],
                    parent_content_hash=hashes["Chunks"],
                    relation="analyzed_from_context_chunks",
                    evidence_kind="embedded_context_pack",
                    source_field="context_pack.chunk_ids",
                    evidence_ref=str(files["Analysis"]),
                    details={"chunk_ids": context_ids},
                )
            )
        else:
            analysis_unresolved.append(
                unresolved_requirement(
                    "source_chunks",
                    "ANALYSIS_CONTEXT_CHUNKS_DO_NOT_FULLY_RESOLVE",
                    evidence_refs=[str(files["Analysis"]), str(files["Chunks"])],
                    candidate_artifact_ids=[ids["Chunks"]],
                    search_hints=missing_context_ids,
                )
            )
        analysis_run_ref = analysis_fm.get("test_run_id")
        if not isinstance(analysis_run_ref, str) or not analysis_run_ref.strip():
            analysis_run_ref = None
            analysis_unresolved.append(
                unresolved_requirement(
                    "producing_run",
                    "ANALYSIS_TEST_RUN_ID_MISSING",
                    evidence_refs=[str(files["Analysis"])],
                )
            )
        analysis = build_envelope(
            artifact_id=ids["Analysis"],
            artifact_type=_TYPES["Analysis"],
            content_hash=hashes["Analysis"],
            locator_path=files["Analysis"],
            registered_at=self.registered_at,
            run_ref=analysis_run_ref,
            paper_ids=list(context_pack.get("paper_ids") or [paper_id]),
            source_artifact_ids=[ids["Chunks"]] if analysis_links else [],
            created_at=_timestamp_or_none(
                analysis_fm.get("analyzed_at"), files["Analysis"]
            ),
            parent_artifacts=analysis_links,
            unresolved_parent_requirements=analysis_unresolved,
            legacy_import=True,
            evidence_refs=common_evidence + [str(files["Analysis"]), str(files["Chunks"])],
            metadata={
                "legacy_role": "Analysis",
                "source_artifact_type": analysis_fm.get("artifact_type"),
                "analysis_model": analysis_fm.get("analysis_model"),
                "prompt_version": analysis_fm.get("prompt_version"),
                "embedded_context_pack": True,
                "context_chunk_ids": context_ids,
                "missing_context_chunk_ids": missing_context_ids,
                "estimated_cost": analysis_fm.get("estimated_cost"),
            },
        )

        envelopes = (pdf, raw, clean, chunks_envelope, card, analysis)
        summary = {
            "paper_id": paper_id,
            "artifact_count": len(envelopes),
            "registered_types": [item["artifact_type"] for item in envelopes],
            "context_pack_artifact_registered": False,
            "card_anchor_entries": len(anchors),
            "card_missing_anchor_ids": missing_anchor_ids,
            "card_quote_mismatches": quote_mismatches,
            "analysis_context_chunk_ids": context_ids,
            "analysis_missing_context_chunk_ids": missing_context_ids,
            "resolved_relations": 4,
            "overall_lineage": "partial",
        }
        return AdaptationResult(envelopes=envelopes, mapping_summary=summary)


def register_adaptation(
    registry: ArtifactRegistry, result: AdaptationResult
) -> list[str]:
    """Register an adaptation in dependency order without changing sources."""

    return [registry.register(envelope) for envelope in result.envelopes]


def build_open_type_envelope(
    path: Path | str,
    *,
    artifact_type: str,
    registered_at: str,
    run_ref: str | None,
    evidence_refs: Iterable[str],
    paper_ids: Iterable[str] = (),
    legacy_import: bool = False,
    identity_scope: str | None = None,
) -> dict[str, Any]:
    """Register any persistent open type, including archive receipts.

    This function does not infer publication semantics from an artifact type.
    """

    source = Path(path)
    digest = sha256_file(source)
    if legacy_import:
        if not run_ref or not identity_scope:
            raise AdapterError("legacy open-type registration requires run_ref and identity_scope")
        artifact_id = legacy_artifact_id(
            run_ref=run_ref,
            artifact_type=artifact_type,
            content_hash=digest,
            identity_scope=identity_scope,
        )
    else:
        artifact_id = new_artifact_id()
    return build_envelope(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        content_hash=digest,
        locator_path=source,
        registered_at=registered_at,
        run_ref=run_ref,
        paper_ids=paper_ids,
        legacy_import=legacy_import,
        evidence_refs=evidence_refs,
        metadata={},
    )


def build_successor_envelope(
    old_envelope: Mapping[str, Any],
    new_path: Path | str,
    *,
    registered_at: str,
    run_ref: str,
    evidence_ref: str,
) -> dict[str, Any]:
    """Create a new version linked by ``supersedes``; old bytes stay immutable."""

    new_hash = sha256_file(new_path)
    old_hash = old_envelope["content_hash"]["value"]
    if new_hash == old_hash:
        raise AdapterError("successor bytes are unchanged; use idempotent registration")
    link = parent_link(
        parent_artifact_id=old_envelope["artifact_id"],
        parent_content_hash=old_hash,
        relation="supersedes",
        evidence_kind="authorized_version_event",
        source_field="supersedes",
        evidence_ref=evidence_ref,
        details={},
    )
    return build_envelope(
        artifact_id=new_artifact_id(),
        artifact_type=old_envelope["artifact_type"],
        schema_ref=old_envelope.get("schema_ref"),
        content_hash=new_hash,
        locator_path=new_path,
        registered_at=registered_at,
        run_ref=run_ref,
        paper_ids=old_envelope["source_scope"]["paper_ids"],
        source_artifact_ids=[old_envelope["artifact_id"]],
        parent_artifacts=[link],
        legacy_import=False,
        evidence_refs=[evidence_ref],
        metadata={"version_relation": "supersedes"},
    )


def _read_chunks(path: Path, *, expected_paper_id: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AdapterError(f"invalid Chunks JSONL at line {line_no}: {path}") from exc
        if not isinstance(value, dict):
            raise AdapterError(f"Chunks line {line_no} is not an object: {path}")
        for field in ("paper_id", "chunk_id", "source_file", "chunk_hash", "text"):
            _required_text(value, field, path)
        if value["paper_id"] != expected_paper_id:
            raise AdapterError(f"Chunks paper_id mismatch at line {line_no}: {path}")
        if value["chunk_id"] in seen_ids:
            raise AdapterError(f"duplicate chunk_id {value['chunk_id']!r}: {path}")
        seen_ids.add(value["chunk_id"])
        records.append(value)
    if not records:
        raise AdapterError(f"Chunks file is empty: {path}")
    return records


def _card_anchors(card_fm: Mapping[str, Any], path: Path) -> list[dict[str, Any]]:
    source_anchor = card_fm.get("source_anchor")
    if not isinstance(source_anchor, dict) or not isinstance(source_anchor.get("by_field"), dict):
        raise AdapterError(f"Card source_anchor.by_field missing: {path}")
    anchors: list[dict[str, Any]] = []
    for field, values in source_anchor["by_field"].items():
        if not isinstance(values, list):
            raise AdapterError(f"Card anchor field {field!r} must be a list: {path}")
        for value in values:
            if not isinstance(value, dict):
                raise AdapterError(f"Card anchor entry must be an object: {path}")
            _required_text(value, "chunk_id", path)
            item = dict(value)
            item["card_field"] = field
            anchors.append(item)
    if not anchors:
        raise AdapterError(f"Card has no source anchors: {path}")
    return anchors


def _required_text(mapping: Mapping[str, Any], field: str, path: Path) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value.strip():
        raise AdapterError(f"{field} must be non-empty: {path}")
    return value


def _single_or_none(values: Iterable[Any]) -> Any:
    unique = {value for value in values if value is not None}
    return next(iter(unique)) if len(unique) == 1 else None


def _timestamp_or_none(value: Any, path: Path) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        return value
    raise AdapterError(f"timestamp must be ISO text or datetime: {path}: {value!r}")
