from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
from typing import Any, Iterable, Mapping
import uuid

from .policy import document_artifact_path, load_folder_management_policy


_ARTIFACT_KIND_BY_ID = {
    "core-source-document": "PDF",
    "core-raw-document": "RawMD",
    "core-clean-document": "CleanMD",
    "core-chunks": "Chunks",
    "core-card": "Card",
    "core-context-pack": "ContextPack",
    "core-analysis": "Analysis",
    "core-judgment-review": "Judgment",
    "core-human-review": "HumanReview",
}
_LEGACY_DEVELOPMENT_PAPER_ID = re.compile(r"DesktopB[0-9]+_([A-Fa-f0-9]{16})")
_SHA256 = re.compile(r"[A-F0-9]{64}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _canonical_paper_id(paper_id: str, source_sha256: str | None) -> str:
    source_digest = str(source_sha256 or "").upper()
    if _SHA256.fullmatch(source_digest):
        return "DOC_" + source_digest[:16]
    match = _LEGACY_DEVELOPMENT_PAPER_ID.fullmatch(str(paper_id or ""))
    if match is not None:
        return "DOC_" + match.group(1).upper()
    return str(paper_id or "")


def _document_title(title: str) -> str:
    rendered = str(title or "").strip()
    if rendered.casefold().endswith(".pdf"):
        rendered = rendered[:-4].rstrip()
    return rendered


def _relative_source(artifact_root: Path, binding: Mapping[str, Any]) -> tuple[str, Path]:
    relative = str(binding.get("relative_path") or "").replace("\\", "/")
    parts = relative.split("/")
    if (
        not relative
        or relative.startswith("/")
        or any(part in {"", ".", ".."} or ":" in part for part in parts)
    ):
        raise ValueError("FOLDER_MIGRATION_SOURCE_RELATIVE_PATH_INVALID")
    target = artifact_root.joinpath(*parts).resolve(strict=True)
    if (
        not target.is_relative_to(artifact_root)
        or not target.is_file()
        or target.is_symlink()
        or bool(target.stat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    ):
        raise ValueError("FOLDER_MIGRATION_SOURCE_PATH_INVALID")
    return relative, target


def _controlled_external_source(
    source_document: Mapping[str, Any],
) -> tuple[Path, Path, str]:
    source_root_value = str(source_document.get("source_root") or "")
    private_path_value = str(source_document.get("private_path") or "")
    expected_sha256 = str(source_document.get("sha256") or "").upper()
    if not source_root_value or not private_path_value or _SHA256.fullmatch(expected_sha256) is None:
        raise ValueError("FOLDER_MIGRATION_EXTERNAL_SOURCE_BINDING_INVALID")
    source_root = Path(source_root_value).resolve(strict=True)
    candidate = Path(private_path_value)
    try:
        attributes = candidate.lstat()
        target = candidate.resolve(strict=True)
    except OSError as error:
        raise ValueError("FOLDER_MIGRATION_EXTERNAL_SOURCE_UNAVAILABLE") from error
    reparse_mask = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if (
        not source_root.is_dir()
        or source_root.is_symlink()
        or candidate.is_symlink()
        or bool(getattr(attributes, "st_file_attributes", 0) & reparse_mask)
        or not target.is_relative_to(source_root)
        or not target.is_file()
        or target.suffix.casefold() != ".pdf"
    ):
        raise ValueError("FOLDER_MIGRATION_EXTERNAL_SOURCE_PATH_INVALID")
    if _sha256(target) != expected_sha256:
        raise RuntimeError("FOLDER_MIGRATION_EXTERNAL_SOURCE_HASH_MISMATCH")
    return source_root, target, expected_sha256


def _atomic_copy_verified(source: Path, target: Path, expected_sha256: str) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if not target.is_file() or target.is_symlink() or _sha256(target) != expected_sha256:
            raise RuntimeError("FOLDER_MIGRATION_CANONICAL_CONTENT_CONFLICT")
        return "REUSED"
    temporary = target.parent / f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        with source.open("rb") as reader, temporary.open("xb") as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
        if _sha256(temporary) != expected_sha256:
            raise RuntimeError("FOLDER_MIGRATION_COPY_HASH_MISMATCH")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    if _sha256(target) != expected_sha256:
        raise RuntimeError("FOLDER_MIGRATION_TARGET_HASH_MISMATCH")
    return "COPIED"


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    if path.exists() and path.read_bytes() == encoded:
        return
    temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def ensure_legacy_core_projection(
    *,
    artifact_root: Path | str,
    job_id: str,
    paper_id: str,
    title: str,
    bindings: Iterable[Mapping[str, Any]],
    source_sha256: str | None = None,
    source_document: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Copy verified legacy artifacts into the canonical flat document folder.

    This is a compatibility projection, not a destructive migration: every
    original file and every durable Core binding remains in place.  Library
    consumers can use ``canonical_path_by_artifact_id`` immediately, while old
    runs remain reproducible through their original locators.
    """

    root = Path(artifact_root).resolve(strict=True)
    if not root.is_dir() or root.is_symlink():
        raise ValueError("FOLDER_MIGRATION_ARTIFACT_ROOT_INVALID")
    canonical_id = _canonical_paper_id(paper_id, source_sha256)
    canonical_title = _document_title(title)
    policy = load_folder_management_policy()
    entries: list[dict[str, Any]] = []
    canonical_by_id: dict[str, str] = {}
    source_projection: dict[str, Any] | None = None

    # Current-format jobs already own an immutable folder manifest. Validate
    # and return their bindings; running legacy migration would overwrite it.
    bindings = [dict(item) for item in bindings if isinstance(item, Mapping)]
    current_manifests = [item for item in bindings
                         if item.get("artifact_id") == "core-folder-manifest"]
    if current_manifests:
        if len(current_manifests) != 1:
            raise ValueError("FOLDER_PROJECTION_MANIFEST_COUNT_INVALID")
        verified = {}
        for binding in bindings:
            artifact_id = str(binding.get("artifact_id") or "")
            if artifact_id not in _ARTIFACT_KIND_BY_ID and artifact_id != "core-folder-manifest":
                raise ValueError(f"FOLDER_MIGRATION_ARTIFACT_KIND_UNKNOWN:{artifact_id}")
            if artifact_id in verified:
                raise ValueError("FOLDER_PROJECTION_ARTIFACT_DUPLICATE")
            relative, source = _relative_source(root, binding)
            observed = _sha256(source)
            if str(binding.get("sha256") or "").upper() != observed:
                raise RuntimeError("FOLDER_MIGRATION_SOURCE_HASH_MISMATCH")
            verified[artifact_id] = (source, observed)
        manifest_path = verified["core-folder-manifest"][0]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (manifest.get("schema_version") != "DesktopDocumentFolderManifest-v1"
                or manifest.get("job_id") != str(job_id)
                or manifest.get("paper_id") != canonical_id):
            raise ValueError("FOLDER_PROJECTION_MANIFEST_IDENTITY_MISMATCH")
        manifest_files = manifest.get("files")
        if not isinstance(manifest_files, list):
            raise ValueError("FOLDER_PROJECTION_MANIFEST_FILES_INVALID")
        declared = {item.get("artifact_id"): item for item in manifest_files if isinstance(item, dict)}
        if (len(declared) != len(manifest_files)
                or set(declared) != set(verified) - {"core-folder-manifest"}):
            raise ValueError("FOLDER_PROJECTION_MANIFEST_BINDINGS_MISMATCH")
        for artifact_id, item in declared.items():
            source, observed = verified[artifact_id]
            if (source.parent != manifest_path.parent or item.get("name") != source.name
                    or str(item.get("sha256") or "").upper() != observed
                    or item.get("bytes") != source.stat().st_size):
                raise ValueError("FOLDER_PROJECTION_MANIFEST_FILE_MISMATCH")
        return {
            "schema_version": "DesktopLegacyCoreFolderProjectionReceipt-v1",
            "job_id": str(job_id), "paper_id": canonical_id,
            "display_title": canonical_title, "document_folder": str(manifest_path.parent),
            "manifest_path": str(manifest_path), "manifest_sha256": _sha256(manifest_path),
            "canonical_path_by_artifact_id": {key: str(value[0]) for key, value in verified.items()},
            "source_document": None, "artifact_count": len(verified),
            "original_files_preserved": True, "settings_files_touched": 0, "status": "PASS",
        }

    if source_document is not None:
        source_root, source, expected_sha256 = _controlled_external_source(source_document)
        canonical = document_artifact_path(
            root,
            artifact_kind="PDF",
            paper_id=canonical_id,
            title=canonical_title,
        )
        _atomic_copy_verified(source, canonical, expected_sha256)
        canonical_relative = canonical.relative_to(root).as_posix()
        canonical_by_id["core-source-document"] = str(canonical)
        source_projection = {
            "artifact_id": "core-source-document",
            "artifact_kind": "PDF",
            "source_root": str(source_root),
            "source_relative_path": source.relative_to(source_root).as_posix(),
            "canonical_relative_path": canonical_relative,
            "canonical_file_name": canonical.name,
            "bytes": source.stat().st_size,
            "sha256": expected_sha256,
            "copy_state": "PRESENT_VERIFIED",
            "original_preserved": True,
        }
        entries.append(dict(source_projection))

    normalized_bindings = sorted(
        (dict(binding) for binding in bindings if isinstance(binding, Mapping)),
        key=lambda binding: str(binding.get("artifact_id") or ""),
    )
    for binding in normalized_bindings:
        artifact_id = str(binding.get("artifact_id") or "")
        artifact_kind = _ARTIFACT_KIND_BY_ID.get(artifact_id)
        if artifact_kind is None:
            raise ValueError(f"FOLDER_MIGRATION_ARTIFACT_KIND_UNKNOWN:{artifact_id}")
        source_relative, source = _relative_source(root, binding)
        observed_sha256 = _sha256(source)
        expected_sha256 = str(binding.get("sha256") or "").upper()
        if _SHA256.fullmatch(expected_sha256) is None or expected_sha256 != observed_sha256:
            raise RuntimeError("FOLDER_MIGRATION_SOURCE_HASH_MISMATCH")
        canonical = document_artifact_path(
            root,
            artifact_kind=artifact_kind,
            paper_id=canonical_id,
            title=canonical_title,
        )
        _atomic_copy_verified(source, canonical, observed_sha256)
        canonical_relative = canonical.relative_to(root).as_posix()
        canonical_by_id[artifact_id] = str(canonical)
        entries.append(
            {
                "artifact_id": artifact_id,
                "artifact_kind": artifact_kind,
                "source_relative_path": source_relative,
                "canonical_relative_path": canonical_relative,
                "bytes": source.stat().st_size,
                "sha256": observed_sha256,
                "copy_state": "PRESENT_VERIFIED",
                "original_preserved": True,
            }
        )

    document_folder = document_artifact_path(
        root,
        artifact_kind="FolderManifest",
        paper_id=canonical_id,
        title=canonical_title,
    ).parent
    document_folder.mkdir(parents=True, exist_ok=True)
    manifest_path = document_artifact_path(
        root,
        artifact_kind="FolderManifest",
        paper_id=canonical_id,
        title=canonical_title,
    )
    manifest = {
        "schema_version": "DesktopLegacyCoreFolderProjectionManifest-v1",
        "job_id": str(job_id),
        "paper_id": canonical_id,
        "display_title": canonical_title,
        "strategy": "COPY_VERIFIED_KEEP_ORIGINAL_AND_BINDINGS",
        "document_folder": document_folder.relative_to(root).as_posix(),
        "entries": entries,
        "original_files_preserved": True,
        "durable_core_bindings_changed": False,
        "settings_files_touched": 0,
        "folder_management_policy_sha256": policy.sha256,
        "raw_private_content_included": False,
    }
    _atomic_json(manifest_path, manifest)
    return {
        "schema_version": "DesktopLegacyCoreFolderProjectionReceipt-v1",
        "job_id": str(job_id),
        "paper_id": canonical_id,
        "display_title": canonical_title,
        "document_folder": str(document_folder),
        "manifest_path": str(manifest_path),
        "manifest_sha256": _sha256(manifest_path),
        "canonical_path_by_artifact_id": canonical_by_id,
        "source_document": source_projection,
        "artifact_count": len(entries),
        "original_files_preserved": True,
        "settings_files_touched": 0,
        "status": "PASS",
    }
