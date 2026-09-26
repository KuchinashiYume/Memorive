from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from m1_pipeline.naming import display_title, file_name, folder_name


POLICY_RELATIVE_PATH = Path("contracts") / "t12" / "P08FolderManagementPolicy.json"
_SAFE_PAPER_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")


def windows_io_path(path: Path | str) -> Path:
    """Use Windows' extended namespace without changing the physical location.

    Literature titles occur in both folder and artifact names. Their paths can
    exceed MAX_PATH even under a normal user profile. Do not require a machine
    registry change or shorten a user's document identity to make I/O work.
    Resolve first so dot components and relative paths retain normal semantics.
    """
    resolved = Path(path).resolve(strict=False)
    if os.name != "nt":
        return resolved
    prefix = os.sep * 2 + "?" + os.sep
    rendered = str(resolved)
    if rendered.startswith(prefix):
        return resolved
    if rendered.startswith(os.sep * 2):
        return Path(prefix + "UNC" + os.sep + rendered[2:])
    return Path(prefix + rendered)


@dataclass(frozen=True)
class FolderManagementPolicy:
    path: Path
    payload: Mapping[str, Any]
    sha256: str


def _policy_path() -> Path:
    explicit = os.environ.get("PROS_P08_FOLDER_POLICY_PATH")
    if explicit:
        return Path(explicit).resolve(strict=True)
    return (Path(__file__).resolve().parents[2] / POLICY_RELATIVE_PATH).resolve(strict=True)


def _validate_policy(payload: Any) -> Mapping[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("FOLDER_POLICY_INVALID_ROOT")
    if payload.get("schema_version") != "P08FolderManagementPolicy-v1":
        raise ValueError("FOLDER_POLICY_SCHEMA_INVALID")
    if payload.get("revision") != "r1.0" or payload.get("effective_from_build") != 77:
        raise ValueError("FOLDER_POLICY_REVISION_INVALID")
    if payload.get("deletion_policy") != "NEVER_AUTOMATIC":
        raise ValueError("FOLDER_POLICY_DELETION_BOUNDARY_INVALID")
    settings = payload.get("settings_policy")
    if not isinstance(settings, dict) or settings.get("mutation_during_reorganization") != "FORBIDDEN":
        raise ValueError("FOLDER_POLICY_SETTINGS_BOUNDARY_INVALID")
    profile = payload.get("profile_directories")
    if not isinstance(profile, dict) or not profile or len(set(profile.values())) != len(profile):
        raise ValueError("FOLDER_POLICY_PROFILE_DIRECTORIES_INVALID")
    if any(
        not isinstance(key, str)
        or not key
        or not isinstance(value, str)
        or not value
        or "/" in value
        or "\\" in value
        or value in {".", ".."}
        for key, value in profile.items()
    ):
        raise ValueError("FOLDER_POLICY_PROFILE_DIRECTORY_PATH_INVALID")
    artifacts = payload.get("document_artifacts")
    if (
        not isinstance(artifacts, dict)
        or artifacts.get("layout") != "ONE_PAPER_ONE_FOLDER_FLAT"
        or artifacts.get("naming_authority") != "m1_pipeline.naming"
        or not isinstance(artifacts.get("allowed_kinds"), dict)
    ):
        raise ValueError("FOLDER_POLICY_DOCUMENT_LAYOUT_INVALID")
    build = payload.get("build_outputs")
    if (
        not isinstance(build, dict)
        or build.get("revision_suffix_forbidden") is not True
        or build.get("current_and_previous_visible") != 2
    ):
        raise ValueError("FOLDER_POLICY_BUILD_LAYOUT_INVALID")
    return payload


def load_folder_management_policy(path: Path | str | None = None) -> FolderManagementPolicy:
    resolved = Path(path).resolve(strict=True) if path is not None else _policy_path()
    raw = resolved.read_bytes()
    payload = _validate_policy(json.loads(raw.decode("utf-8")))
    return FolderManagementPolicy(
        path=resolved,
        payload=payload,
        sha256=hashlib.sha256(raw).hexdigest().upper(),
    )


def _paper_identity(paper_id: str, title: str) -> tuple[str, str]:
    if not isinstance(paper_id, str) or _SAFE_PAPER_ID.fullmatch(paper_id) is None:
        raise ValueError("FOLDER_POLICY_PAPER_ID_INVALID")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("FOLDER_POLICY_DOCUMENT_TITLE_INVALID")
    rendered = display_title(title)
    if not rendered:
        raise ValueError("FOLDER_POLICY_DOCUMENT_TITLE_EMPTY_AFTER_SANITIZE")
    return paper_id, rendered


def document_folder(root: Path | str, *, paper_id: str, title: str) -> Path:
    identifier, rendered = _paper_identity(paper_id, title)
    return Path(root).resolve(strict=False) / folder_name(identifier, rendered)


def document_artifact_path(
    root: Path | str,
    *,
    artifact_kind: str,
    paper_id: str,
    title: str,
) -> Path:
    loaded = load_folder_management_policy()
    kinds = loaded.payload["document_artifacts"]["allowed_kinds"]
    if artifact_kind not in kinds:
        raise ValueError(f"FOLDER_POLICY_ARTIFACT_KIND_NOT_ALLOWED:{artifact_kind}")
    identifier, rendered = _paper_identity(paper_id, title)
    extension = kinds[artifact_kind]
    return (
        Path(root).resolve(strict=False)
        / folder_name(identifier, rendered)
        / file_name(artifact_kind, identifier, rendered, extension)
    )


class ProfileFolderManager:
    """Resolve declared product folders without creating undeclared peers."""

    def __init__(self, profile_root: Path | str, *, policy: FolderManagementPolicy | None = None):
        self.profile_root = Path(profile_root).resolve(strict=False)
        self.policy = policy or load_folder_management_policy()
        self._directories = dict(self.policy.payload["profile_directories"])
        protected = self.policy.payload["settings_policy"]["protected_logical_directories"]
        self._protected_ids = frozenset(str(value) for value in protected)

    def path_for(self, logical_directory: str) -> Path:
        relative = self._directories.get(logical_directory)
        if relative is None:
            raise ValueError(f"FOLDER_POLICY_UNKNOWN_PROFILE_DIRECTORY:{logical_directory}")
        target = (self.profile_root / relative).resolve(strict=False)
        if not target.is_relative_to(self.profile_root):
            raise ValueError("FOLDER_POLICY_PROFILE_PATH_ESCAPE")
        if logical_directory in {"PHASE1_RUNTIME", "PHASE1_JOBS", "PHASE1_TASK_CONTROLS"}:
            return windows_io_path(target)
        return target

    def is_reorganization_protected(self, path: Path | str) -> bool:
        target = Path(path).resolve(strict=False)
        return any(
            target == root or target.is_relative_to(root)
            for root in (self.path_for(identifier) for identifier in self._protected_ids)
        )
