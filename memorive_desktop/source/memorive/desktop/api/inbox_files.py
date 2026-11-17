"""Picked and dropped files and inbox items: InboxFilesMixin.
"""
from __future__ import annotations

import base64
import hashlib
import mimetypes
import os
from pathlib import Path
import shutil
import stat
import subprocess
from typing import Any
import uuid
from memorive.platform.windows_paths import windows_io_path


class InboxFilesMixin:
    """Files the window picks or drops, and the inbox items it previews, locates and opens."""

    def _effective_directory(self, role: str) -> Path:
        if role not in {"workspace_root", "artifact_root", "external_library"}:
            raise ValueError("DIRECTORY_ROLE_INVALID")
        state = self._service.call("settings.get_state", {})
        settings = state.get("settings") if isinstance(state, dict) else None
        directories = settings.get("directories") if isinstance(settings, dict) else None
        value: Any = None
        if isinstance(directories, dict):
            if role == "external_library":
                external = directories.get("external_library")
                if isinstance(external, dict) and external.get("enabled"):
                    value = external.get("root")
            else:
                value = directories.get(role)
        if isinstance(value, str) and value:
            raw = Path(value)
            try:
                raw_io = windows_io_path(raw)
                if (
                    raw.is_absolute()
                    and raw_io.exists()
                    and raw_io.is_dir()
                    and not raw_io.is_symlink()
                    and not self._is_reparse_point(raw_io)
                ):
                    return raw.resolve(strict=False)
            except OSError:
                pass
        fallback = self._profile_path(
            "ARTIFACTS" if role == "artifact_root" else "WORKSPACE"
        )
        windows_io_path(fallback).mkdir(parents=True, exist_ok=True)
        return fallback.resolve(strict=False)

    def pick_inbox_files(self) -> dict[str, Any]:
        if self._window is None:
            raise RuntimeError("PRODUCT_WINDOW_NOT_ATTACHED")
        from webview import FileDialog
        selected = self._window.create_file_dialog(
            FileDialog.OPEN,
            directory=str(self._effective_directory("workspace_root")),
            allow_multiple=True,
            file_types=(
                "Supported files (*.txt;*.md;*.json;*.pdf;*.docx;*.xlsx;*.pptx;*.png;*.jpg;*.jpeg;*.tif;*.tiff)",
                "All files (*.*)",
            ),
        )
        if not selected:
            return {
                "schema_version": "DesktopAssistantInboxPickerReceipt-v2",
                "paths": [],
                "path_count": 0,
                "source_allowlist_enforced_by_service": True,
                "status": "CANCELLED",
            }
        return self.stage_inbox_paths([str(path) for path in selected])

    def pick_research_files(self) -> dict[str, Any]:
        """Keep source bundles together; research attachments own durable copying."""
        if self._window is None:
            raise RuntimeError("PRODUCT_WINDOW_NOT_ATTACHED")
        from webview import FileDialog
        selected = self._window.create_file_dialog(
            FileDialog.OPEN,
            directory=str(self._effective_directory("workspace_root")),
            allow_multiple=True,
            file_types=(
                "Research attachments (*.pdf;*.md;*.txt;*.json;*.docx;*.png;*.jpg;*.jpeg;*.webp)",
                "All files (*.*)",
            ),
        )
        paths = [str(path) for path in selected] if selected else []
        return {"paths": paths, "path_count": len(paths),
                "status": "SELECTED" if paths else "CANCELLED"}

    @staticmethod
    def _is_reparse_point(path: Path) -> bool:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))

    def stage_inbox_paths(self, paths: list[str]) -> dict[str, Any]:
        if not isinstance(paths, list) or not 1 <= len(paths) <= 32:
            raise ValueError("INBOX_STAGE_PATH_COUNT_INVALID")
        accepted: list[tuple[Path, Path]] = []
        for value in paths:
            if not isinstance(value, str) or not value:
                raise ValueError("INBOX_STAGE_PATH_INVALID")
            candidate = Path(value).resolve(strict=False)
            candidate_io = windows_io_path(candidate)
            if (
                not candidate_io.is_file()
                or candidate_io.is_symlink()
                or self._is_reparse_point(candidate_io)
            ):
                raise ValueError("INBOX_STAGE_SOURCE_NOT_REGULAR_FILE")
            size = candidate_io.stat().st_size
            if size <= 0 or size > 64 * 1024 * 1024:
                raise ValueError("INBOX_STAGE_SOURCE_SIZE_INVALID")
            accepted.append((candidate, candidate_io))
        batch_id = "stage_" + uuid.uuid4().hex
        batch_root = (self._inbox_source_root / batch_id).resolve(strict=False)
        if not batch_root.is_relative_to(self._inbox_source_root.resolve(strict=False)):
            raise RuntimeError("INBOX_STAGE_ROOT_ESCAPE")
        windows_io_path(batch_root).mkdir(parents=False, exist_ok=False)
        staged: list[str] = []
        hashes: list[str] = []
        used_names: set[str] = set()
        for index, (source, source_io) in enumerate(accepted):
            target_name = source.name
            if target_name.casefold() in used_names:
                target_name = f"{source.stem}_{index + 1}{source.suffix}"
            used_names.add(target_name.casefold())
            target = batch_root / target_name
            target_io = windows_io_path(target)
            shutil.copy2(source_io, target_io, follow_symlinks=False)
            staged.append(str(target))
            hashes.append(hashlib.sha256(target_io.read_bytes()).hexdigest().upper())
        return {
            "schema_version": "DesktopAssistantInboxPickerReceipt-v2",
            "stage_id": batch_id,
            "paths": staged,
            "path_count": len(staged),
            "sha256s": hashes,
            "source_files_moved_or_deleted": 0,
            "source_allowlist_enforced_by_service": True,
            "original_names_preserved": True,
            "status": "SELECTED",
        }

    def _inbox_item_path(self, item_id: str) -> tuple[dict[str, Any], Path]:
        if not isinstance(item_id, str) or not item_id:
            raise ValueError("INBOX_ITEM_ID_INVALID")
        detail = self._service.call("inbox.item_detail", {"item_id": item_id})
        locator = detail.get("bound_locator")
        if not isinstance(locator, str) or not locator or ".." in Path(locator).parts:
            raise ValueError("INBOX_ITEM_NOT_BOUND")
        path = (self._inbox_root / locator).resolve(strict=False)
        if not path.is_relative_to(self._inbox_root.resolve(strict=False)):
            raise ValueError("INBOX_ITEM_PATH_ESCAPE")
        path_io = windows_io_path(path)
        if not path_io.is_file() or path_io.is_symlink() or self._is_reparse_point(path_io):
            raise ValueError("INBOX_ITEM_FILE_INVALID")
        observed = hashlib.sha256(path_io.read_bytes()).hexdigest().upper()
        if observed != detail.get("content_sha256"):
            raise ValueError("INBOX_ITEM_HASH_MISMATCH")
        return detail, path

    def get_inbox_preview(self, item_id: str) -> dict[str, Any]:
        detail, path = self._inbox_item_path(item_id)
        extension = str(detail.get("extension") or "").lower()
        common = {
            "schema_version": "DesktopAssistantInboxPreviewProjection-v1",
            "item": detail,
            "content_sha256": detail["content_sha256"],
            "sample": bool(detail.get("sample")),
            "read_only": True,
            "status": "PASS",
        }
        if extension in {".docx", ".pptx", ".xlsx"}:
            return {**common, "mode": "PROPERTY_ONLY", "mime_type": None}
        if extension in {".txt", ".md", ".json"}:
            maximum = 256 * 1024
            raw = windows_io_path(path).read_bytes()
            decoded = raw[:maximum].decode("utf-8-sig", errors="replace")
            return {
                **common,
                "mode": "TEXT",
                "mime_type": "application/json" if extension == ".json" else "text/plain",
                "text": decoded,
                "truncated": len(raw) > maximum,
            }
        if extension == ".pdf":
            raw = windows_io_path(path).read_bytes()
            return {
                **common,
                "mode": "PDF",
                "mime_type": "application/pdf",
                "data_base64": base64.b64encode(raw).decode("ascii"),
            }
        if extension in {".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
            raw = windows_io_path(path).read_bytes()
            return {
                **common,
                "mode": "IMAGE",
                "mime_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                "data_base64": base64.b64encode(raw).decode("ascii"),
            }
        return {**common, "mode": "UNSUPPORTED", "mime_type": None}

    def locate_inbox_item(self, item_id: str) -> dict[str, Any]:
        detail, path = self._inbox_item_path(item_id)
        process = subprocess.Popen(
            ["explorer.exe", "/select,", str(path)],
            shell=False,
            close_fds=True,
        )
        return {
            "schema_version": "DesktopAssistantInboxLocateReceipt-v1",
            "item_id": detail["item_id"],
            "process_id": process.pid,
            "selected_controlled_copy": True,
            "status": "OPENED",
        }

    def open_inbox_item(self, item_id: str) -> dict[str, Any]:
        detail, path = self._inbox_item_path(item_id)
        os.startfile(path, "open")
        return {
            "schema_version": "DesktopAssistantInboxExternalOpenReceipt-v1",
            "item_id": detail["item_id"],
            "opened_controlled_copy": True,
            "status": "OPENED",
        }

    def get_inbox_fixture_paths(self) -> dict[str, Any]:
        paths = [self._fixture_root / "sample.docx", self._fixture_root / "sample.png"]
        if not all(path.is_file() and path.resolve().is_relative_to(self._fixture_root.resolve()) for path in paths):
            raise RuntimeError("SYNTHETIC_INBOX_FIXTURE_MISSING")
        return {
            "schema_version": "WorkLogSyntheticFixtureLocatorProjection-v1",
            "paths": [str(path.resolve()) for path in paths],
            "sha256s": [hashlib.sha256(path.read_bytes()).hexdigest().upper() for path in paths],
            "synthetic_only": True,
            "status": "PASS",
        }
