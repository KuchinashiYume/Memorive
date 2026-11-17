"""Library and work-log files in Explorer: ArtifactsMixin.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
from typing import Any


class ArtifactsMixin:
    """Opening and locating library and work-log files in Explorer."""

    def open_library_sample_artifact(self, artifact_id: str) -> dict[str, Any]:
        if not isinstance(artifact_id, str) or not artifact_id:
            raise ValueError("LIBRARY_SAMPLE_ARTIFACT_ID_INVALID")
        validation = self._library.call("library.validate_external_view", {"artifact_id": artifact_id})
        if validation.get("status") != "PASS_VALIDATED_NOT_LAUNCHED":
            raise RuntimeError(f"LIBRARY_SAMPLE_OPEN_BLOCKED:{validation.get('rejection_reason') or 'VALIDATION_FAILED'}")
        projection = self._library.call("library.get_artifact", {"artifact_id": artifact_id})
        artifact = projection.get("artifact")
        if not isinstance(artifact, dict):
            raise RuntimeError("LIBRARY_SAMPLE_ARTIFACT_PROJECTION_INVALID")
        relative = str(artifact.get("relative_path") or "").replace("\\", "/")
        relative_path = Path(relative)
        if not relative or relative_path.is_absolute() or any(part in {"", ".", ".."} for part in relative_path.parts):
            raise RuntimeError("LIBRARY_SAMPLE_RELATIVE_PATH_INVALID")
        fixture_root = (self._source_root.parent / "fixtures_integration" / "files").resolve(strict=True)
        target = fixture_root.joinpath(*relative_path.parts).resolve(strict=True)
        if not target.is_relative_to(fixture_root):
            raise RuntimeError("LIBRARY_SAMPLE_PATH_ESCAPE")
        if not target.is_file() or target.is_symlink() or self._is_reparse_point(target):
            raise RuntimeError("LIBRARY_SAMPLE_FILE_INVALID")
        observed_sha256 = hashlib.sha256(target.read_bytes()).hexdigest().upper()
        os.startfile(target, "open")
        return {
            "schema_version": "DesktopAssistantLibrarySampleOpenReceipt-v1",
            "artifact_id": artifact_id,
            "sample": True,
            "controlled_fixture": True,
            "content_sha256": observed_sha256,
            "opened_by_windows_default_application": True,
            "status": "OPENED",
        }

    def locate_library_refinement_artifact(self, artifact_id: str) -> dict[str, Any]:
        if not isinstance(artifact_id, str) or not artifact_id:
            raise ValueError("LIBRARY_REFINEMENT_ARTIFACT_ID_INVALID")
        target = self._library.resolve_refinement_readable_artifact(artifact_id)
        process = subprocess.Popen(
            ["explorer.exe", "/select,", str(target)],
            shell=False,
            close_fds=True,
        )
        return {
            "schema_version": "DesktopLibraryRefinementLocateReceipt-v1",
            "artifact_id": artifact_id,
            "process_id": process.pid,
            "selected_controlled_private_product": True,
            "review_open_recorded": False,
            "status": "OPENED",
        }

    def open_library_refinement_artifact(self, artifact_id: str) -> dict[str, Any]:
        if not isinstance(artifact_id, str) or not artifact_id:
            raise ValueError("LIBRARY_REFINEMENT_ARTIFACT_ID_INVALID")
        target = self._library.resolve_refinement_readable_artifact(artifact_id)
        try:
            os.startfile(target, "open")
        except OSError as error:
            if getattr(error, "winerror", None) not in {31, 1155}:
                raise
            # No Markdown association: open this same controlled .md in the
            # system text viewer, never fall back to exposing the JSON.
            viewer = Path(os.environ.get("WINDIR", "C:/Windows")) / "System32/notepad.exe"
            subprocess.Popen([str(viewer), str(target)], shell=False, close_fds=True)
        review_receipt = self._library.record_refinement_open(artifact_id)
        return {
            "schema_version": "DesktopLibraryRefinementExternalOpenReceipt-v1",
            "artifact_id": artifact_id,
            "opened_controlled_private_product": True,
            "review_open_recorded": True,
            "opened_at": review_receipt["opened_at"],
            "verified_draft_sha256": review_receipt["verified_draft_sha256"],
            "status": "OPENED",
        }

    def locate_library_core_artifact(self, artifact_id: str) -> dict[str, Any]:
        if not isinstance(artifact_id, str) or not artifact_id:
            raise ValueError("LIBRARY_Core_ARTIFACT_ID_INVALID")
        target = self._library.resolve_core_private_artifact(artifact_id)
        process = subprocess.Popen(
            ["explorer.exe", "/select,", str(target)],
            shell=False,
            close_fds=True,
        )
        return {
            "schema_version": "DesktopCoreArtifactLocateReceipt-v1",
            "artifact_id": artifact_id,
            "process_id": process.pid,
            "selected_controlled_core_artifact": True,
            "status": "OPENED",
        }

    def locate_library_core_source(self, artifact_id: str) -> dict[str, Any]:
        if not isinstance(artifact_id, str) or not artifact_id:
            raise ValueError("LIBRARY_Core_ARTIFACT_ID_INVALID")
        target = self._library.resolve_core_private_source(artifact_id)
        process = subprocess.Popen(
            ["explorer.exe", "/select,", str(target)],
            shell=False,
            close_fds=True,
        )
        return {
            "schema_version": "DesktopCoreSourceLocateReceipt-v1",
            "artifact_id": artifact_id,
            "process_id": process.pid,
            "selected_controlled_core_source": True,
            "status": "OPENED",
        }

    def open_structure_original(self, job_id: str) -> dict[str, Any]:
        import hashlib
        receipt = self._service.call('structure.original', {'job_id': job_id})
        target = Path(receipt['path'])
        with target.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != receipt['source_sha256']:
                raise ValueError('STRUCTURE_SOURCE_STALE')
        os.startfile(str(target), 'open')
        return {'status': 'OPENED', 'source_sha256': receipt['source_sha256']}

    def open_library_core_artifact(self, artifact_id: str) -> dict[str, Any]:
        if not isinstance(artifact_id, str) or not artifact_id:
            raise ValueError("LIBRARY_Core_ARTIFACT_ID_INVALID")
        target = self._library.resolve_core_private_artifact(artifact_id)
        os.startfile(target, "open")
        self._library.paper_review.open(artifact_id)
        return {
            "schema_version": "DesktopCoreArtifactExternalOpenReceipt-v1",
            "artifact_id": artifact_id,
            "opened_controlled_core_artifact": True,
            "status": "OPENED",
        }

    def open_work_log_sample_directory(self) -> dict[str, Any]:
        log_root = (self._state_dir / "work_log").resolve(strict=True)
        if not log_root.is_relative_to(self._state_dir.resolve(strict=True)) or not log_root.is_dir():
            raise RuntimeError("WORK_LOG_SAMPLE_DIRECTORY_INVALID")
        process = subprocess.Popen(["explorer.exe", str(log_root)], shell=False, close_fds=True)
        return {
            "schema_version": "DesktopAssistantWorkLogSampleDirectoryReceipt-v1",
            "sample": True,
            "controlled_projection_directory": True,
            "process_id": process.pid,
            "status": "OPENED",
        }

    def locate_work_log_sample_projection(self) -> dict[str, Any]:
        log_root = (self._state_dir / "work_log").resolve(strict=True)
        projection = (log_root / "work_log_projection.json").resolve(strict=True)
        if not projection.is_relative_to(log_root) or not projection.is_file():
            raise RuntimeError("WORK_LOG_SAMPLE_PROJECTION_INVALID")
        process = subprocess.Popen(
            ["explorer.exe", "/select,", str(projection)],
            shell=False,
            close_fds=True,
        )
        return {
            "schema_version": "DesktopAssistantWorkLogSampleProjectionLocateReceipt-v1",
            "sample": True,
            "selected_controlled_projection": True,
            "process_id": process.pid,
            "status": "OPENED",
        }
