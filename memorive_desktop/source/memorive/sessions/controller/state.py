"""The sessions page state file: SessionStateMixin.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping
import uuid
from memorive.sessions.controller.common import SessionProductError, _PUBLIC_FIELDS


class SessionStateMixin:
    """The page state file of the sessions page: its fields, migration and checks, and the source roots."""

    @staticmethod
    def _resolve_source_roots(source_roots: Mapping[str, Path] | None) -> dict[str, Path]:
        if source_roots is not None:
            if set(source_roots) != {"codex_home", "claude_home"}:
                raise SessionProductError("SESSION_SOURCE_ROOT_KEYS_INVALID")
            return {key: Path(value).expanduser().resolve() for key, value in source_roots.items()}
        home = Path.home()
        codex_value = os.environ.get("MEMORIVE_CODEX_HOME") or os.environ.get("CODEX_HOME")
        claude_value = os.environ.get("MEMORIVE_CLAUDE_HOME") or os.environ.get("CLAUDE_CONFIG_DIR")
        return {
            "codex_home": Path(codex_value).expanduser().resolve() if codex_value else (home / ".codex").resolve(),
            "claude_home": Path(claude_value).expanduser().resolve() if claude_value else (home / ".claude").resolve(),
        }

    @staticmethod
    def _source_root_selection_metadata(source_roots: Mapping[str, Path] | None) -> dict[str, str]:
        if source_roots is not None:
            return {"codex": "EXPLICIT_ISOLATED_SOURCE", "claude": "EXPLICIT_ISOLATED_SOURCE"}
        return {
            "codex": (
                "MEMORIVE_EXPLICIT_HOME" if os.environ.get("MEMORIVE_CODEX_HOME")
                else "CODEX_HOME" if os.environ.get("CODEX_HOME")
                else "USERPROFILE_DEFAULT"
            ),
            "claude": (
                "MEMORIVE_EXPLICIT_HOME" if os.environ.get("MEMORIVE_CLAUDE_HOME")
                else "CLAUDE_CONFIG_DIR" if os.environ.get("CLAUDE_CONFIG_DIR")
                else "USERPROFILE_DEFAULT"
            ),
        }

    @staticmethod
    def _public_source_root(path: Path) -> str:
        resolved = path.resolve(strict=False)
        user_home = Path.home().resolve(strict=False)
        try:
            relative = resolved.relative_to(user_home)
        except ValueError:
            return f"<LOCAL>/{resolved.name}"
        suffix = relative.as_posix()
        return "%USERPROFILE%" + ("/" + suffix if suffix else "")

    @staticmethod
    def _require_keys(params: Mapping[str, Any], required: set[str], optional: set[str] | None = None) -> None:
        optional = optional or set()
        keys = set(params)
        if not required <= keys or keys - required - optional:
            raise SessionProductError("SESSIONS_PRODUCT_PARAMS_INVALID")

    @staticmethod
    def _migrate_state(state: Mapping[str, Any]) -> dict[str, Any]:
        value = deepcopy(dict(state))
        if value.get("schema_version") == "SessionsSessionProjectionState-v2":
            value["schema_version"] = "SessionsSessionProjectionState-v3"
            value["hidden_projection_ids"] = []
            previous_filter = value.get("last_filter")
            if not isinstance(previous_filter, Mapping):
                previous_filter = {"source": "all", "search": ""}
            value["last_filter"] = {
                "source": str(previous_filter.get("source", "all")),
                "search": str(previous_filter.get("search", "")),
                "limit_per_source": 30,
            }
        if value.get("schema_version") == "SessionsSessionProjectionState-v3":
            current_filter = value.get("last_filter")
            if isinstance(current_filter, Mapping) and current_filter.get("limit_per_source") == 20:
                value["last_filter"] = {
                    "source": str(current_filter.get("source", "all")),
                    "search": str(current_filter.get("search", "")),
                    "limit_per_source": 30,
                }
        return value

    @staticmethod
    def _validate_state(state: Mapping[str, Any]) -> None:
        expected = {
            "schema_version",
            "revision",
            "sessions",
            "capture_jobs",
            "fault_history",
            "last_filter",
            "source_status",
            "hidden_projection_ids",
        }
        if set(state) != expected:
            raise SessionProductError("SESSION_STATE_FIELDS_INVALID")
        if state.get("schema_version") != "SessionsSessionProjectionState-v3":
            raise SessionProductError("SESSION_STATE_SCHEMA_INVALID")
        if not isinstance(state.get("revision"), int) or state["revision"] < 0:
            raise SessionProductError("SESSION_STATE_REVISION_INVALID")
        if not isinstance(state.get("sessions"), dict) or not isinstance(state.get("capture_jobs"), dict):
            raise SessionProductError("SESSION_STATE_COLLECTION_INVALID")
        if not isinstance(state.get("source_status"), dict):
            raise SessionProductError("SESSION_SOURCE_STATUS_INVALID")
        hidden = state.get("hidden_projection_ids")
        if (
            not isinstance(hidden, list)
            or not all(isinstance(value, str) and value for value in hidden)
            or len(hidden) != len(set(hidden))
        ):
            raise SessionProductError("SESSION_HIDDEN_PROJECTION_IDS_INVALID")
        last_filter = state.get("last_filter")
        if not isinstance(last_filter, Mapping) or set(last_filter) != {
            "source",
            "search",
            "limit_per_source",
        }:
            raise SessionProductError("SESSION_LAST_FILTER_INVALID")
        if last_filter.get("limit_per_source") not in {10, 20, 30, 50}:
            raise SessionProductError("SESSION_LIMIT_PER_SOURCE_INVALID")

    def _load(self) -> dict[str, Any]:
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self._validate_state(state)
        return state

    def _save(self, state: dict[str, Any]) -> None:
        state["revision"] = int(state.get("revision", -1)) + 1
        self._validate_state(state)
        temporary = self.state_path.with_name(
            f".{self.state_path.name}.{uuid.uuid4().hex}.next"
        )
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(
                    json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True)
                    + "\n"
                )
            temporary.replace(self.state_path)
        finally:
            # Only the unique temporary created by this call may be removed.
            # A legacy fixed-name .next file is left untouched and cannot
            # block the durable state projection anymore.
            temporary.unlink(missing_ok=True)
        self._metrics["local_projection_writes"] += 1

    @staticmethod
    def _write_private_json(path: Path, payload: Mapping[str, Any]) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.next")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(
                    json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
                    + "\n"
                )
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def _load_refinement_settings(self) -> dict[str, Any]:
        try:
            value = json.loads(self._refinement_settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SessionProductError("SESSION_REFINEMENT_SETTINGS_UNAVAILABLE") from error
        expected = {"schema_version", "profile_ref", "include_tasks", "include_risks"}
        if not isinstance(value, Mapping) or set(value) != expected:
            raise SessionProductError("SESSION_REFINEMENT_SETTINGS_FIELDS_INVALID")
        if value.get("schema_version") != "DesktopSessionRefinementSettings-v1":
            raise SessionProductError("SESSION_REFINEMENT_SETTINGS_SCHEMA_INVALID")
        if not isinstance(value.get("profile_ref"), str):
            raise SessionProductError("SESSION_REFINEMENT_PROFILE_INVALID")
        if not isinstance(value.get("include_tasks"), bool) or not isinstance(value.get("include_risks"), bool):
            raise SessionProductError("SESSION_REFINEMENT_OPTIONS_INVALID")
        return dict(value)

    @staticmethod
    def _public(row: Mapping[str, Any]) -> dict[str, Any]:
        result = {key: deepcopy(row[key]) for key in _PUBLIC_FIELDS if key in row}
        result.setdefault("deleted", False)
        result["raw_content_included"] = False
        result["private_content_included"] = False
        result["public_safe_projection"] = True
        return result

    @staticmethod
    def _path_within(path: Path, roots: Iterable[Path]) -> bool:
        resolved = path.resolve()
        for root in roots:
            try:
                resolved.relative_to(root.resolve())
                return True
            except ValueError:
                continue
        return False

    def _allowed_record_roots(self, provider_id: str) -> tuple[Path, ...]:
        if provider_id == "codex":
            home = self._source_roots["codex_home"]
            return ((home / "sessions").resolve(), (home / "archived_sessions").resolve())
        if provider_id == "claude":
            return ((self._source_roots["claude_home"] / "projects").resolve(),)
        return ()
