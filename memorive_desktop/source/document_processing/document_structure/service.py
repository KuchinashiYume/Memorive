"""Transactional sidecar writer and integrity reader used by Intake and CLI."""
from __future__ import annotations

from pathlib import Path
import re

from .contract import (MAX_JSON_BYTES, RECEIPT, SCHEMA, canonical, digest, fail,
                       file_sha, hash_value, read_json, structure_id, validate_structure)
from .providers import marker_chunks, native_pdf


def validate_options(mode: str, marker_chunks_path=None):
    if not isinstance(mode, str) or mode not in {"none", "native_pdf", "marker_chunks"}:
        fail("STRUCTURE_MODE_INVALID", "Structure mode must be none, native_pdf or marker_chunks")
    if (mode == "marker_chunks") != (marker_chunks_path is not None):
        fail("STRUCTURE_OPTIONS_INVALID", "Marker candidate path is required only for marker_chunks")


def _write_json(path: Path, value: dict):
    raw = canonical(value) + b"\n"
    if len(raw) > MAX_JSON_BYTES:
        fail("STRUCTURE_OUTPUT_TOO_LARGE", "Structure output exceeds 32 MiB")
    with path.open("xb") as stream:
        stream.write(raw)


def write_structure(*, source: Path, source_sha256: str, rawmd: Path,
                    paper_id: str, conversion_receipt_id: str, mode: str,
                    marker_chunks_path: Path | None = None, allow_marker_page_fallback: bool = False) -> dict:
    """Write inside the facade's unpublished transaction directory only."""
    validate_options(mode, marker_chunks_path)
    if not isinstance(paper_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", paper_id):
        fail("PAPER_ID_INVALID", "Invalid paper_id for structure artifacts")
    if source.parent.resolve() != rawmd.parent.resolve():
        fail("STRUCTURE_ARTIFACT_PATH_INVALID", "Source and RawMD must belong to one unpublished bundle")
    if mode == "none":
        fail("STRUCTURE_MODE_INVALID", "No structure was requested")
    source_sha = hash_value(source_sha256)
    if file_sha(source) != source_sha:
        fail("STRUCTURE_SOURCE_MISMATCH", "Preserved source changed before structure extraction")
    candidate_raw = None
    if mode == "native_pdf":
        value = native_pdf(source)
    else:
        value, candidate_raw = marker_chunks(source, source_sha, Path(marker_chunks_path), allow_page_fallback=allow_marker_page_fallback)
    if file_sha(source) != source_sha:
        fail("STRUCTURE_SOURCE_MISMATCH", "Preserved source changed during structure extraction")
    config_hash = digest(canonical({"engine": value["engine"], "version": value["engine_version"],
                                    "options": value["config"]}))
    provider_hash = digest(canonical(value))
    for ordinal, block in enumerate(value["blocks"]):
        block["physical_page"] = block["page_index"] + 1
        block["reading_order"] = ordinal
        block["block_id"] = "Block-" + digest(canonical(
            [source_sha, provider_hash, config_hash, block["upstream_id"]]))[:32]
    value.update({"schema_version": SCHEMA, "source_sha256": source_sha,
                  "rawmd_sha256": file_sha(rawmd), "config_sha256": config_hash,
                  "provider_output_sha256": provider_hash,
                  "review_status": "CANDIDATE_NOT_ADMITTED",
                  "rawmd_relationship": "AUXILIARY_CANDIDATE_DOES_NOT_REPLACE_RAWMD"})
    value["structure_id"] = structure_id(value)
    validate_structure(value)
    directory = rawmd.parent
    structure_path = directory / f"[DocumentStructure] {paper_id}.json"
    receipt_path = directory / f"[StructureReceipt] {paper_id}.json"
    _write_json(structure_path, value)
    files = {"source": {"name": source.name, "sha256": source_sha},
             "rawmd": {"name": rawmd.name, "sha256": value["rawmd_sha256"]},
             "structure": {"name": structure_path.name, "sha256": file_sha(structure_path)}}
    if candidate_raw is not None:
        candidate = directory / f"[MarkerChunks] {paper_id}.json"
        with candidate.open("xb") as stream:
            stream.write(candidate_raw)
        files["marker_chunks"] = {"name": candidate.name, "sha256": digest(candidate_raw)}
    receipt = {"schema_version": RECEIPT, "mode": mode,
               "structure_id": value["structure_id"], "conversion_receipt_id": conversion_receipt_id,
               "files": files, "source_sha256": source_sha, "rawmd_sha256": value["rawmd_sha256"],
               "config_sha256": config_hash, "status": "STRUCTURE_CANDIDATE_WRITTEN",
               "marker_execution_verified": False,
               "model_calls_in_this_conversion": 0, "external_http_calls_in_this_conversion": 0,
               "page_count": len(value["pages"]), "block_count": len(value["blocks"]),
               "table_count": sum(len(b["tables"]) for b in value["blocks"]),
               "warnings": value["warnings"]}
    _write_json(receipt_path, receipt)
    return {"structure_path": structure_path, "receipt_path": receipt_path,
            "structure_sha256": files["structure"]["sha256"],
            "receipt_sha256": file_sha(receipt_path)}


def _local_file(root: Path, name) -> Path:
    if (not isinstance(name, str) or not name or name in {".", ".."}
            or any(c in name for c in '/\\:\x00') or name.endswith((" ", "."))):
        fail("STRUCTURE_ARTIFACT_PATH_INVALID", "Artifact must be a local sibling filename")
    path = (root / name).resolve()
    if path.parent != root:
        fail("STRUCTURE_ARTIFACT_PATH_INVALID", "Artifact resolves outside its bundle")
    if not path.is_file():
        fail("STRUCTURE_ARTIFACT_MISSING", "Required structure artifact is missing")
    return path


def verify_bundle(receipt_path: str | Path) -> dict:
    receipt_path = Path(receipt_path).resolve()
    receipt, _ = read_json(receipt_path)
    if receipt.get("schema_version") != RECEIPT:
        fail("STRUCTURE_SCHEMA_UNSUPPORTED", "Unsupported structure receipt")
    files = receipt.get("files")
    expected = {"source", "rawmd", "structure"}
    if receipt.get("mode") == "marker_chunks":
        expected.add("marker_chunks")
    elif receipt.get("mode") != "native_pdf":
        fail("STRUCTURE_MODE_INVALID", "Invalid structure receipt mode")
    if not isinstance(files, dict) or set(files) != expected:
        fail("STRUCTURE_RECEIPT_INVALID", "Receipt artifact set is incomplete")
    paths = {}
    for role, item in files.items():
        if not isinstance(item, dict):
            fail("STRUCTURE_RECEIPT_INVALID", "Invalid artifact descriptor")
        path = _local_file(receipt_path.parent, item.get("name"))
        if file_sha(path) != hash_value(item.get("sha256")):
            fail("STRUCTURE_ARTIFACT_CHANGED", f"Structure evidence changed: {role}")
        paths[role] = path
    if len(set(paths.values())) != len(paths):
        fail("STRUCTURE_RECEIPT_INVALID", "Artifact roles must refer to distinct files")
    value, _ = read_json(paths["structure"])
    validate_structure(value)
    for key, expected_value in (("structure_id", value["structure_id"]),
                                 ("source_sha256", files["source"]["sha256"].lower()),
                                 ("rawmd_sha256", files["rawmd"]["sha256"].lower()),
                                 ("config_sha256", value["config_sha256"])):
        if receipt.get(key) != expected_value or value.get(key) != expected_value:
            fail("STRUCTURE_RECEIPT_MISMATCH", "Structure and receipt binding differs")
    if "marker_chunks" in paths:
        envelope, _ = read_json(paths["marker_chunks"])
        if (hash_value(envelope.get("source_sha256")) != value["source_sha256"]
                or digest(canonical(envelope.get("chunks"))) != value.get("upstream_payload_sha256")):
            fail("STRUCTURE_RECEIPT_MISMATCH", "Marker candidate binding differs")
    return {"status": "INTEGRITY_VERIFIED", "structure_id": value["structure_id"],
            "page_count": len(value["pages"]), "block_count": len(value["blocks"]),
            "review_status": value["review_status"], "marker_execution_verified": False,
            "qualification": "BYTE_BINDING_ONLY_NOT_EXTRACTION_ACCURACY_OR_AUTHENTICITY"}
