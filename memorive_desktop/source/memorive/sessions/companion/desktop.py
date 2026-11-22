"""The browser companion as the desktop runs it: install, reload and sync the extension, and bring
browser sessions into the session library.

ProductApi builds one BrowserCompanionDesktop at startup and forwards to it the window's
browser_companion_*, *_browser_companion*, sync_browser_sessions_from_downloads and
import_browser_sessions_file calls. The extension writes into the browser's download folder;
the sync reads its reports there and hands each session file to the sessions controller
(memorive/sessions/controller/). Its own state is in the BROWSER_COMPANION profile folder (setup_v1.json,
the prepared extension, session-library).
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any, Callable, Mapping
import uuid

from memorive.platform.windows_paths import windows_io_path

from memorive.sessions.companion.browsers import BROWSERS, FAMILIES, discover as discover_browsers, family as browser_family, select as select_browser


class BrowserCompanionDesktop:
    """profile_root and source_root are the app's folders; sessions is the sessions controller;
    profile_path resolves a profile folder; write_json_file is ProductApi's JSON writer;
    effective_directory resolves the folders the user chose; system_effects_enabled is False in
    tests that must not start a browser or open a folder; window is the main window once attached
    (ProductApi.attach_window), used for the file picker."""

    def __init__(self, *, profile_root: Path, source_root: Path, sessions: Any,
                 profile_path: Callable[[str], Path], write_json_file: Callable[[Path, dict], None],
                 effective_directory: Callable[[str], Path], system_effects_enabled: bool, window: Any = None):
        self.profile_root = profile_root
        self.source_root = source_root
        self.sessions = sessions
        self.profile_path = profile_path
        self.write_json_file = write_json_file
        self.effective_directory = effective_directory
        self.system_effects_enabled = system_effects_enabled
        self.window = window

    def browser_companion_sites(self) -> dict[str, Any]:
        from memorive.sessions.companion.site_profiles import SiteProfiles, default_sites, normalize_sites, profile_hash, site_authorizations, OFFICIAL_WEB_SITES, BUILTINS
        catalog = [dict(provider=key, name=name, hosts=list(hosts), adapter=key if key in BUILTINS else 'generic')
                   for key, name, hosts, _proof in OFFICIAL_WEB_SITES]
        if self.profile_root is None:
            rows = normalize_sites(default_sites())
            return dict(revision=0, sites=rows, profile_sha256=profile_hash(rows), site_authorizations=site_authorizations(rows), official_site_catalog=catalog)
        state = SiteProfiles(self.profile_path('BROWSER_COMPANION')).get()
        return dict(state, site_authorizations=site_authorizations(state['sites']), official_site_catalog=catalog)

    def save_browser_companion_sites(self, sites, expected_revision):
        from memorive.sessions.companion.site_profiles import SiteProfiles
        # Reconcile a newly arrived terminal report before inspecting the
        # durable request marker. A completed request is not an active worker.
        self.browser_companion_status()
        state_path = self._browser_companion_state_path()
        if state_path.is_file():
            running = json.loads(state_path.read_text(encoding='utf-8'))
            if running.get('sync_requested_mode'):
                raise ValueError('BROWSER_SYNC_ALREADY_RUNNING')
        result = SiteProfiles(self.profile_path('BROWSER_COMPANION')).save(sites, expected_revision)
        refreshed = self.refresh_prepared_package()
        return dict(result, package_refresh=refreshed)

    def _browser_enabled_sites(self):
        from memorive.sessions.companion.site_profiles import active_sites
        return active_sites(self.browser_companion_sites()['sites'])

    def _browser_companion_package(self) -> tuple[Path, set[str], str]:
        source = (self.source_root / "memorive" / "sessions" / "companion" / "extension").resolve()
        try:
            source.relative_to(self.source_root)
        except ValueError as error:
            raise RuntimeError("BROWSER_COMPANION_SOURCE_ESCAPE") from error
        required = {
            "manifest.json",
            "background.js",
            "content.js",
            "popup.html",
            "popup.css",
            "popup.js",
            "runner.html",
            "runner.js",
            "README.txt",
            "site_profiles.js",
            "recording.js", "browser_identity.js",
        }
        if not source.is_dir() or {path.name for path in source.iterdir() if path.is_file()} != required:
            raise RuntimeError("BROWSER_COMPANION_PACKAGE_INVALID")
        from memorive.sessions.companion.site_profiles import configured_extension, default_sites, profile_hash
        settings = self.browser_companion_sites()
        if settings['profile_sha256'] != profile_hash(default_sites()):
            source = configured_extension(source, self._browser_companion_export_root()/'packages', settings['sites'])
        digest = hashlib.sha256()
        for name in sorted(required):
            digest.update(name.encode("utf-8"))
            digest.update(b"\x00")
            digest.update((source / name).read_bytes())
        return source, required, digest.hexdigest().upper()

    def _browser_companion_export_root(self) -> Path:
        # Browser-facing files are public generated assets, not the private state.
        # Use a short, stable, per-profile namespace outside sandbox nesting.
        owner = hashlib.sha256(str(self.profile_root.resolve()).casefold().encode()).hexdigest()[:10]
        return (Path.home() / "AppData" / "Local" / "Memorive" / "Bridge" / owner).resolve()

    def _check_browser_companion_destination(self, destination: Path) -> None:
        resolved = destination.resolve()
        # Permit only this profile's old directory or its own short export root.
        roots = (self.profile_path("BROWSER_COMPANION").resolve(), self._browser_companion_export_root())
        if not any(resolved.is_relative_to(root) and resolved != root for root in roots):
            raise RuntimeError("BROWSER_COMPANION_DESTINATION_ESCAPE")

    def open_browser_companion_site(self, url, preferred_browser=None):
        from memorive.sessions.companion.site_profiles import normalize_sites, official_web_identity, GENERIC, BUILTINS
        identity = official_web_identity(url)
        if not identity:
            raise ValueError("BROWSER_SITE_NOT_VERIFIED_OFFICIAL")
        row = normalize_sites([dict(slot=1, name=identity['name'], url=url,
            adapter=identity['provider'] if identity['provider'] in BUILTINS else 'generic',
            enabled=True, selectors=GENERIC)])[0]
        selected = select_browser(self._browser_executables(), preferred_browser)
        command = [str(selected[1]), row['url']]
        if self.system_effects_enabled:
            subprocess.Popen(command, shell=False, close_fds=True)
        return dict(status='OPENED' if self.system_effects_enabled else 'READY', url=row['url'], browser=selected[0])

    def open_browser_companion_folder(self):
        setup_path = self._browser_companion_state_path()
        state = json.loads(setup_path.read_text(encoding='utf8')) if setup_path.is_file() else {}
        if not state.get('extension_path'):
            raise ValueError('BROWSER_COMPANION_NOT_PREPARED')
        destination = Path(state['extension_path'])
        self._check_browser_companion_destination(destination)
        if not (destination / 'manifest.json').is_file():
            raise ValueError('BROWSER_COMPANION_NOT_PREPARED')
        if self.system_effects_enabled and os.name == 'nt':
            subprocess.Popen(['explorer.exe', str(destination)], shell=False, close_fds=True)
        return dict(path=str(destination))

    def _browser_companion_state_path(self) -> Path:
        return self.profile_path("BROWSER_COMPANION") / "setup_v1.json"

    def _browser_companion_manifest_projection(self) -> dict[str, str]:
        source, _required, _package_sha256 = self._browser_companion_package()
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        version = manifest.get("version")
        if not isinstance(version, str) or not version.strip():
            raise RuntimeError("BROWSER_COMPANION_MANIFEST_VERSION_MISSING")
        # An unpacked extension keeps a browser/profile-specific id.  Do not
        # force a manifest key during an upgrade: adding one would change the
        # identity of already loaded 3.1 installations.  The extension writes
        # chrome.runtime.id into its signed-shape local sync report instead.
        return {"extension_version": version.strip()}

    @staticmethod
    def _browser_companion_installed_version(destination: Path) -> str | None:
        manifest_path = destination / "manifest.json"
        if not manifest_path.is_file():
            return None
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        version = manifest.get("version") if isinstance(manifest, dict) else None
        return version.strip() if isinstance(version, str) and version.strip() else None

    def _latest_browser_companion_sync_report(
        self,
        *,
        prepared_at_unix: int | None = None,
        expected_sync_mode: str | None = None,
        expected_browser: str | None = None,
        expected_extension_id: str | None = None,
    ) -> dict[str, Any] | None:
        candidates: list[Path] = []
        for root in self._browser_session_download_roots():
            for folder in (root, root / "Memorive会话桥接"):
                if not folder.is_dir():
                    continue
                candidates.extend(
                    path.resolve()
                    for path in folder.glob("memorive-session-library-sync-report-*.json")
                    if path.is_file()
                )
        required_fields = {
            "schema_version", "source", "extension_version", "extension_id", "browser_family",
            "generated_at", "trigger", "status", "providers",
            "cookie_value_reads", "browser_storage_value_reads",
            "hidden_provider_api_calls", "remote_session_mutations",
        }
        enriched_fields = {
            "per_provider_limit", "sync_mode", "active_provider_count",
            "provider_parallelism", "maximum_capture_windows",
            "per_provider_capture_mode",
        }
        dedicated_window_fields = {"window_mode", "user_tab_reused"}
        optional_terminal_fields = {"error_code", "hard_timeout_ms"}
        required_provider_fields = {
            "provider_id", "status", "indexed_count", "captured_count",
            "failed_count", "error_code",
        }
        enriched_provider_fields = {
            "candidate_count", "capture_target", "body_read_count",
            "unchanged_count", "skipped_candidate_count",
            "skipped_error_codes", "safe_diagnostic",
        }
        for path in sorted(
            candidates,
            key=lambda value: (value.stat().st_mtime_ns, value.name),
            reverse=True,
        ):
            try:
                stat_result = path.stat()
                if stat_result.st_size <= 0 or stat_result.st_size > 1024 * 1024:
                    continue
                if prepared_at_unix and stat_result.st_mtime < prepared_at_unix:
                    continue
                payload_bytes = path.read_bytes()
                payload = json.loads(payload_bytes.decode("utf-8"))
                if isinstance(payload, dict) and (
                    (expected_browser is not None and payload.get("browser_family") != expected_browser)
                    or (expected_extension_id is not None and payload.get("extension_id") != expected_extension_id)
                ):
                    continue
                payload_fields = set(payload) if isinstance(payload, dict) else set()
                if (
                    not isinstance(payload, dict)
                    or not required_fields.issubset(payload_fields)
                    or not payload_fields.issubset(
                        required_fields
                        | enriched_fields
                        | dedicated_window_fields
                        | optional_terminal_fields | {'site_profile_sha256'}
                    )
                ):
                    continue
                has_enriched_report = bool(payload_fields & enriched_fields)
                if has_enriched_report and not enriched_fields.issubset(payload_fields):
                    continue
                has_dedicated_window_contract = bool(
                    payload_fields & dedicated_window_fields
                )
                if (
                    has_dedicated_window_contract
                    and not dedicated_window_fields.issubset(payload_fields)
                ):
                    continue
                if (
                    payload.get("schema_version") != "MEMORIVE_BROWSER_SESSION_SYNC_REPORT_V2"
                    or payload.get("source") != "MEMORIVE_BROWSER_COMPANION_RUNNER_V4_DRAFT"
                    or not isinstance(payload.get("extension_version"), str)
                    or not self._browser_companion_semver_at_least(
                        payload.get("extension_version"), (0, 0, 0)
                    )
                    or not isinstance(payload.get("extension_id"), str)
                    or len(payload.get("extension_id")) != 32
                    or any(character not in "abcdefghijklmnop" for character in payload["extension_id"])
                    or payload.get("browser_family") not in FAMILIES | {"CHROMIUM"}
                    or not isinstance(payload.get("generated_at"), str)
                    or not isinstance(payload.get("trigger"), str)
                    or payload.get("status") not in {"COMPLETE", "ERROR"}
                    or any(
                        payload.get(field) != 0
                        for field in (
                            "cookie_value_reads", "browser_storage_value_reads",
                            "hidden_provider_api_calls", "remote_session_mutations",
                        )
                    )
                ):
                    continue
                if has_enriched_report:
                    if (
                        not isinstance(payload.get("per_provider_limit"), int)
                        or not 1 <= payload["per_provider_limit"] <= 50
                        or payload.get("sync_mode")
                        not in {"PREFLIGHT", "FULL", "INCREMENTAL"}
                        or (
                            payload["sync_mode"] == "PREFLIGHT"
                            and payload["per_provider_limit"] != 2
                        )
                        or (
                            payload["sync_mode"] == "FULL"
                            and payload["per_provider_limit"] <= 2
                        )
                        or (
                            payload["sync_mode"] == "INCREMENTAL"
                            and payload["per_provider_limit"] != 10
                        )
                        or payload.get("active_provider_count") != len(self._browser_enabled_sites())
                        or not isinstance(payload.get("provider_parallelism"), bool)
                        or (
                            "error_code" in payload
                            and not isinstance(payload.get("error_code"), str)
                        )
                        or (
                            "hard_timeout_ms" in payload
                            and (
                                not isinstance(payload.get("hard_timeout_ms"), int)
                                or not 1_000
                                <= payload["hard_timeout_ms"]
                                <= 5_400_000
                            )
                        )
                    ):
                        continue
                    dedicated_window_contract = bool(
                        has_dedicated_window_contract
                        and (payload.get("window_mode"), payload.get("per_provider_capture_mode"), payload.get("provider_parallelism")) in (
                            ("DEDICATED_MINIMIZED", "DEDICATED_MINIMIZED_WINDOW_OWNED_TABS", True),
                            ("DEDICATED_VISIBLE", "DEDICATED_VISIBLE_WINDOW_OWNED_TABS", False),
                        )
                        and payload.get("user_tab_reused") is False
                        and payload.get("maximum_capture_windows") == 1
                    )
                    legacy_window_contract = bool(
                        not has_dedicated_window_contract
                        and payload.get("maximum_capture_windows") == 3
                        and payload.get("per_provider_capture_mode")
                        in {
                            "SEQUENTIAL_REUSED_SINGLE_WINDOW",
                            "DEEPSEEK_FOREGROUND_REUSED_TAB__GEMINI_KIMI_ISOLATED_REUSED_WINDOW",
                        }
                    )
                    requires_dedicated_window = (
                        self._browser_companion_semver_at_least(
                            payload["extension_version"], (3, 4, 8)
                        )
                    )
                    if (
                        requires_dedicated_window
                        and not dedicated_window_contract
                    ) or (
                        not requires_dedicated_window
                        and not (
                            legacy_window_contract
                            or dedicated_window_contract
                        )
                    ):
                        continue
                if expected_sync_mode and payload.get("sync_mode") != expected_sync_mode:
                    continue
                providers = payload.get("providers")
                expected_providers = {row['provider_id'] for row in self._browser_enabled_sites()}
                if (self._browser_companion_semver_at_least(payload.get('extension_version'),(3,6,0))
                        and payload.get('site_profile_sha256') != self.browser_companion_sites()['profile_sha256']):
                    continue
                if not isinstance(providers, list) or not expected_providers or len(providers) != len(expected_providers):
                    continue
                provider_projection: dict[str, dict[str, Any]] = {}
                valid = True
                for row in providers:
                    row_fields = set(row) if isinstance(row, dict) else set()
                    if (
                        not isinstance(row, dict)
                        or not required_provider_fields.issubset(row_fields)
                        or not row_fields.issubset(
                            required_provider_fields | enriched_provider_fields
                        )
                    ):
                        valid = False
                        break
                    provider_id = row.get("provider_id")
                    if provider_id not in expected_providers or provider_id in provider_projection:
                        valid = False
                        break
                    if (
                        not isinstance(row.get("status"), str)
                        or not isinstance(row.get("error_code"), str)
                        or any(
                            not isinstance(row.get(field), int) or row[field] < 0
                            for field in ("indexed_count", "captured_count", "failed_count")
                        )
                    ):
                        valid = False
                        break
                    if any(
                        field in row
                        and (
                            not isinstance(row.get(field), int)
                            or row[field] < 0
                        )
                        for field in (
                            "candidate_count", "capture_target", "body_read_count",
                            "unchanged_count", "skipped_candidate_count",
                        )
                    ):
                        valid = False
                        break
                    if (
                        "skipped_error_codes" in row
                        and (
                            not isinstance(row.get("skipped_error_codes"), list)
                            or any(
                                not isinstance(value, str)
                                for value in row["skipped_error_codes"]
                            )
                        )
                    ):
                        valid = False
                        break
                    if (
                        "safe_diagnostic" in row
                        and row.get("safe_diagnostic") is not None
                        and not isinstance(row.get("safe_diagnostic"), dict)
                    ):
                        valid = False
                        break
                    provider_projection[provider_id] = dict(row)
                if not valid or set(provider_projection) != expected_providers:
                    continue
                return {
                    "path": path,
                    "sha256": hashlib.sha256(payload_bytes).hexdigest().upper(),
                    "browser_family": payload["browser_family"],
                    "extension_version": payload["extension_version"],
                    "extension_id": payload["extension_id"],
                    "generated_at": payload["generated_at"],
                    "status": payload["status"],
                    "sync_mode": payload.get("sync_mode"),
                    "per_provider_limit": payload.get("per_provider_limit"),
                    "provider_parallelism": payload.get("provider_parallelism"),
                    "maximum_capture_windows": payload.get(
                        "maximum_capture_windows"
                    ),
                    "per_provider_capture_mode": payload.get(
                        "per_provider_capture_mode"
                    ),
                    "window_mode": payload.get("window_mode"),
                    "user_tab_reused": payload.get("user_tab_reused"),
                    "providers": provider_projection,
                    "site_profile_sha256": payload.get('site_profile_sha256'),
                }
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError):
                continue
        return None

    @staticmethod
    def _browser_companion_extension_id_valid(value: Any) -> bool:
        return bool(
            isinstance(value, str)
            and len(value) == 32
            and all(character in "abcdefghijklmnop" for character in value)
        )

    def _latest_browser_companion_identity_receipt(
        self,
        *,
        current_extension_version: str,
        expected_extension_id: str | None = None,
        expected_browser: str | None = None,
        prepared_at_unix: int | None = None,
    ) -> dict[str, Any] | None:
        """Read and retain the newest fail-closed bridge identity receipt."""

        candidates: list[Path] = []
        for root in self._browser_session_download_roots():
            for folder in (root, root / "Memorive会话桥接"):
                if not folder.is_dir():
                    continue
                candidates.extend(
                    path.resolve()
                    for path in folder.glob("memorive-browser-companion-identity-*.json")
                    if path.is_file()
                )
        required_fields = {
            "schema_version",
            "extension_id",
            "extension_version",
            "observed_at",
            "lifecycle_reason",
            "trigger_protocol",
            "preflight_limit",
            "full_limit",
            "incremental_limit",
            "providers",
            "cookie_value_reads",
            "browser_storage_value_reads",
            "hidden_provider_api_calls",
        }
        for path in sorted(
            candidates,
            key=lambda value: (value.stat().st_mtime_ns, value.name),
            reverse=True,
        ):
            try:
                stat_result = path.stat()
                if stat_result.st_size <= 0 or stat_result.st_size > 1024 * 1024:
                    continue
                if prepared_at_unix and stat_result.st_mtime < prepared_at_unix:
                    continue
                payload_bytes = path.read_bytes()
                payload = json.loads(payload_bytes.decode("utf-8"))
                if (not isinstance(payload, dict) or not required_fields.issubset(payload)
                        or set(payload) - required_fields - {'site_profile_sha256', 'browser_family'}):
                    continue
                extension_id = payload.get("extension_id")
                extension_version = payload.get("extension_version")
                if (
                    payload.get("schema_version")
                    != "MEMORIVE_BROWSER_COMPANION_IDENTITY_V1"
                    or not self._browser_companion_extension_id_valid(extension_id)
                    or (
                        isinstance(expected_extension_id, str)
                        and expected_extension_id
                        and extension_id != expected_extension_id
                    )
                    or extension_version != current_extension_version
                    or (self._browser_companion_semver_at_least(extension_version, (3, 10, 1))
                        and payload.get("browser_family") not in FAMILIES)
                    or (expected_browser is not None and payload.get("browser_family") != expected_browser)
                    or not self._browser_companion_semver_at_least(
                        extension_version, (3, 4, 0)
                    )
                    or not isinstance(payload.get("observed_at"), str)
                    or not payload["observed_at"].strip()
                    or not isinstance(payload.get("lifecycle_reason"), str)
                    or not payload["lifecycle_reason"].strip()
                    or payload.get("trigger_protocol")
                    != "HTTPS_APPROVED_FRAGMENT_V3"
                    or payload.get("preflight_limit") != 2
                    or payload.get("full_limit") != 50
                    or payload.get("incremental_limit") != 10
                    or payload.get("providers")
                    != [row['provider_id'] for row in self._browser_enabled_sites()]
                    or (self._browser_companion_semver_at_least(extension_version,(3,6,0))
                        and payload.get('site_profile_sha256') != self.browser_companion_sites()['profile_sha256'])
                    or any(
                        payload.get(field) != 0
                        for field in (
                            "cookie_value_reads",
                            "browser_storage_value_reads",
                            "hidden_provider_api_calls",
                        )
                    )
                ):
                    continue
                retained_path = self._retain_browser_session_artifact(
                    path, payload_bytes
                )
                return {
                    "path": retained_path,
                    "sha256": hashlib.sha256(payload_bytes).hexdigest().upper(),
                    "extension_id": extension_id,
                    "extension_version": extension_version,
                    "observed_at": payload["observed_at"],
                    "lifecycle_reason": payload["lifecycle_reason"],
                    "browser_family": payload.get("browser_family"),
                    "trigger_protocol": payload["trigger_protocol"],
                    "incremental_limit": payload["incremental_limit"],
                }
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError):
                continue
        return None

    @staticmethod
    def _browser_companion_semver_at_least(
        version: Any,
        minimum: tuple[int, int, int],
    ) -> bool:
        if not isinstance(version, str):
            return False
        match = re.fullmatch(
            r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
            r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
            r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?",
            version,
        )
        if not match:
            return False
        core = tuple(int(match.group(index)) for index in (1, 2, 3))
        if core != minimum:
            return core > minimum
        # A prerelease has lower precedence than the corresponding stable floor.
        return match.group(4) is None

    @staticmethod
    def _browser_companion_full_sync_gate(
        handshake: Mapping[str, Any] | None,
        *,
        current_extension_version: str,
        enabled_providers=None,
        site_profile_sha256=None,
    ) -> dict[str, Any]:
        minimum_version = (3, 5, 0)
        report_version = handshake.get("extension_version") if handshake else None
        version_eligible = BrowserCompanionDesktop._browser_companion_semver_at_least(
            report_version,
            minimum_version,
        )
        identity_matches = bool(
            handshake
            and report_version == current_extension_version
        )
        report_contract_matches = bool(
            handshake
            and version_eligible
            and identity_matches
            and handshake.get("sync_mode") == "PREFLIGHT"
            and handshake.get("per_provider_limit") == 2
            and (site_profile_sha256 is None or handshake.get('site_profile_sha256') == site_profile_sha256)
        )
        report_complete = bool(
            report_contract_matches and handshake.get("status") == "COMPLETE"
        )
        provider_rows = handshake.get("providers", {}) if handshake else {}
        providers: dict[str, dict[str, Any]] = {}
        for provider in (enabled_providers if enabled_providers is not None else ("deepseek", "gemini", "kimi")):
            row = provider_rows.get(provider, {}) if isinstance(provider_rows, Mapping) else {}
            captured_count = row.get("captured_count", 0)
            if not isinstance(captured_count, int) or captured_count < 0:
                captured_count = 0
            failed_count = row.get("failed_count", 0)
            if not isinstance(failed_count, int) or failed_count < 0:
                failed_count = 0
            diagnostic = row.get("safe_diagnostic")
            diagnostic_text = (
                json.dumps(diagnostic, ensure_ascii=True, sort_keys=True)
                if isinstance(diagnostic, Mapping)
                else ""
            )
            human_verification = "HUMAN_VERIFICATION" in (
                f"{row.get('error_code', '')} {diagnostic_text}".upper()
            )
            provider_passes = bool(
                report_contract_matches
                and captured_count > 0
                and failed_count == 0
                and not human_verification
            )
            eligible = report_complete and provider_passes
            providers[provider] = {
                "captured_count": captured_count,
                "failed_count": failed_count,
                "human_verification_detected": human_verification,
                "eligible": eligible,
                "status": (
                    "HUMAN_VERIFICATION_BLOCKED"
                    if human_verification
                    else "PREFLIGHT_FAILED"
                    if report_contract_matches and failed_count > 0
                    else "PREFLIGHT_PASS"
                    if provider_passes
                    else "NO_CAPTURED_CONVERSATIONS"
                    if report_contract_matches
                    else "PREFLIGHT_REQUIRED"
                ),
            }
        eligible = bool(providers) and report_complete and all(row["eligible"] for row in providers.values())
        return {
            "minimum_extension_version": "3.5.0",
            "current_extension_version": current_extension_version,
            "report_extension_version": report_version,
            "version_eligible": version_eligible,
            "identity_matches": identity_matches,
            "report_sync_mode": handshake.get("sync_mode") if handshake else None,
            "report_status": handshake.get("status") if handshake else None,
            "report_per_provider_limit": (
                handshake.get("per_provider_limit") if handshake else None
            ),
            "report_generated_at": handshake.get("generated_at") if handshake else None,
            "report_sha256": handshake.get("sha256") if handshake else None,
            "providers": providers,
            "eligible": eligible,
        }

    def _browser_companion_incremental_cursor(self) -> dict[str, Any]:
        """Build a privacy-safe cursor from the local three-provider archive."""

        session_controller = self.sessions
        archive = getattr(session_controller, "session_archive", None)
        provider_ids = [row['provider_id'] for row in self._browser_enabled_sites()]
        empty_counts = {provider_id: 0 for provider_id in provider_ids}
        if archive is None or not hasattr(archive, "list_sessions"):
            return {
                "schema_version": "DesktopBrowserCompanionIncrementalCursorProjection-v1",
                "provider_counts": empty_counts,
                "known_session_count": 0,
                "overlap_fingerprint_count": 0,
                "eligible": False,
                "raw_session_ids_in_token": False,
                "raw_private_content_in_token": False,
            }
        try:
            cursor_module = importlib.import_module(
                "memorive.sessions.companion.incremental_cursor"
            )
            provider_counts = {
                provider_id: len(
                    archive.list_sessions(
                        provider=provider_id,
                        limit=50,
                        search="",
                    )
                )
                for provider_id in provider_ids
            }
            if not provider_ids or not all(provider_counts.values()):
                return {
                    "schema_version": "DesktopBrowserCompanionIncrementalCursorProjection-v1",
                    "provider_counts": provider_counts,
                    "known_session_count": sum(provider_counts.values()),
                    "overlap_fingerprint_count": 0,
                    "eligible": False,
                    "raw_session_ids_in_token": False,
                    "raw_private_content_in_token": False,
                }
            built = cursor_module.build_incremental_cursor(archive, providers=provider_ids)
        except (ImportError, AttributeError, OSError, RuntimeError, ValueError):
            built = None
        if not built:
            return {
                "schema_version": "DesktopBrowserCompanionIncrementalCursorProjection-v1",
                "provider_counts": empty_counts,
                "known_session_count": 0,
                "overlap_fingerprint_count": 0,
                "eligible": False,
                "raw_session_ids_in_token": False,
                "raw_private_content_in_token": False,
            }
        cursor_payload = built["payload"]
        cursor_providers = cursor_payload["providers"]
        provider_counts = {
            provider_id: len(row["known_session_hashes"])
            for provider_id, row in cursor_providers.items()
        }
        cursor_token = built["token"]
        return {
            "schema_version": "DesktopBrowserCompanionIncrementalCursorProjection-v1",
            "cursor_schema_version": cursor_payload["schema_version"],
            "providers": cursor_providers,
            "provider_counts": provider_counts,
            "known_session_count": built["known_session_count"],
            "overlap_fingerprint_count": built["overlap_fingerprint_count"],
            "cursor_token": cursor_token,
            "cursor_token_sha256": hashlib.sha256(
                cursor_token.encode("ascii")
            ).hexdigest().upper(),
            "eligible": True,
            "raw_session_ids_in_token": False,
            "raw_private_content_in_token": False,
        }

    def refresh_prepared_package(self) -> dict[str, Any]:
        """Refresh an already prepared unpacked extension without resetting setup."""
        state_path = self._browser_companion_state_path()
        if not state_path.is_file():
            return {"updated": False, "status": "NOT_PREPARED"}
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"updated": False, "status": "STATE_UNAVAILABLE"}
        if not isinstance(state, dict) or not state.get("prepared"):
            return {"updated": False, "status": "NOT_PREPARED"}
        source, required, package_sha256 = self._browser_companion_package()
        current_version = self._browser_companion_manifest_projection()[
            "extension_version"
        ]
        prepared_path = state.get("extension_path")
        destination = (
            Path(prepared_path) if isinstance(prepared_path, str) and prepared_path
            else self.profile_path("BROWSER_COMPANION") / "extension-v1"
        ).resolve()
        self._check_browser_companion_destination(destination)
        short_destination = self._browser_companion_export_root() / (browser_family(state.get('preferred_browser') or state.get('connected_browser')) or 'browser').lower()
        path_changed = destination != short_destination
        if path_changed:
            destination = short_destination
        installed_version = self._browser_companion_installed_version(destination)
        if installed_version is None:
            prior_state_version = state.get("package_extension_version")
            installed_version = (
                prior_state_version.strip()
                if isinstance(prior_state_version, str)
                and prior_state_version.strip()
                else None
            )
        version_changed = bool(
            installed_version and installed_version != current_version
        )
        capture_repair_required = bool(
            state.get("capture_repair_preflight_required") or version_changed
            or state.get('site_profile_sha256') != self.browser_companion_sites()['profile_sha256']
        )
        if (
            state.get("package_sha256") == package_sha256
            and installed_version == current_version
            and not path_changed
        ):
            return {
                "updated": False,
                "capture_repair_preflight_required": capture_repair_required,
                "status": "CURRENT",
            }
        destination.mkdir(parents=True, exist_ok=True)
        for name in sorted(required):
            shutil.copy2(source / name, destination / name)
        state["package_sha256"] = package_sha256
        if path_changed:
            state.update(extension_path=str(destination), path_changed=True,
                         prepared_at_unix=int(time.time()), extension_id=None,
                         extension_connected=False, connected_browser=None)
            for key in ('identity_receipt_sha256', 'identity_observed_at'):
                state.pop(key, None)
        state['site_profile_sha256'] = self.browser_companion_sites()['profile_sha256']
        # The files on disk are now current, but Chromium keeps the already
        # loaded service worker and runner until the extension/browser reloads.
        state["browser_reload_required"] = True
        state["package_updated_at_unix"] = int(time.time())
        state["package_extension_version"] = current_version
        state["capture_repair_preflight_required"] = capture_repair_required
        if capture_repair_required:
            state["capture_repair_target_version"] = current_version
        self.write_json_file(state_path, state)
        return {
            "updated": True,
            "browser_reload_required": state["browser_reload_required"],
            "capture_repair_preflight_required": capture_repair_required,
            "status": "UPDATED",
        }

    def browser_companion_status(self) -> dict[str, Any]:
        _source, _required, package_sha256 = self._browser_companion_package()
        identity = self._browser_companion_manifest_projection()
        path = self._browser_companion_state_path()
        state: dict[str, Any] = {}
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    state = loaded
            except (OSError, json.JSONDecodeError):
                state = {}
        package_current = state.get("package_sha256") == package_sha256
        prepared = bool(state.get("prepared"))
        user_confirmed = bool(state.get("user_confirmed"))
        import_verified = bool(state.get("import_verified"))
        freshness_values = [
            state.get("prepared_at_unix"),
            state.get("package_updated_at_unix"),
            state.get("bridge_reload_requested_at_unix"),
        ]
        # Legacy setup files did not carry a preparation timestamp.  Never let
        # an unrelated, older report already present in Downloads impersonate
        # a fresh extension handshake for a newly created profile; the setup
        # state file's own mtime is the conservative lower bound in that case.
        try:
            state_file_mtime = int(path.stat().st_mtime)
        except OSError:
            state_file_mtime = None
        freshness_floor = max(
            (value for value in freshness_values if isinstance(value, int)),
            default=state_file_mtime,
        )
        identity_receipt = self._latest_browser_companion_identity_receipt(
            current_extension_version=identity["extension_version"],
            expected_extension_id=(
                state.get("extension_id")
                if self._browser_companion_extension_id_valid(
                    state.get("extension_id")
                )
                else None
            ),
            prepared_at_unix=freshness_floor,
            expected_browser=browser_family(state.get("preferred_browser")),
        )
        report_identity = {
            "expected_browser": browser_family(state.get("preferred_browser")),
            "expected_extension_id": (identity_receipt or {}).get("extension_id"),
        }
        latest_report = self._latest_browser_companion_sync_report(
            prepared_at_unix=freshness_floor, **report_identity
        )
        latest_preflight_report = self._latest_browser_companion_sync_report(
            prepared_at_unix=freshness_floor,
            expected_sync_mode="PREFLIGHT", **report_identity,
        )
        bridge_identity_verified = bool(identity_receipt and package_current)
        handshake = (
            latest_report
            if (
                bridge_identity_verified
                and latest_report
                and latest_report.get("extension_id")
                == identity_receipt.get("extension_id")
                and latest_report.get("extension_version")
                == identity_receipt.get("extension_version")
            )
            else None
        )
        preflight_handshake = (
            latest_preflight_report
            if (
                bridge_identity_verified
                and latest_preflight_report
                and latest_preflight_report.get("extension_id")
                == identity_receipt.get("extension_id")
                and latest_preflight_report.get("extension_version")
                == identity_receipt.get("extension_version")
            )
            else None
        )
        full_sync_gate = self._browser_companion_full_sync_gate(
            preflight_handshake,
            current_extension_version=identity["extension_version"],
            enabled_providers=[row['provider_id'] for row in self._browser_enabled_sites()],
            site_profile_sha256=(self.browser_companion_sites()['profile_sha256']
                if self._browser_companion_semver_at_least(identity['extension_version'],(3,6,0)) else None),
        )
        preflight_passed = bool(full_sync_gate["eligible"])
        preflight_status = (
            "PASS"
            if preflight_passed
            else "FAIL"
            if preflight_handshake
            else "NOT_RUN"
        )
        if state.get("sync_requested_mode") in {'PREFLIGHT','INCREMENTAL','FULL'}:
            requested_handshake = preflight_handshake if state['sync_requested_mode']=='PREFLIGHT' else handshake
            previous_report_sha256 = state.get(
                "sync_requested_previous_report_sha256"
            )
            current_report_sha256 = (
                requested_handshake.get("sha256")
                if requested_handshake and requested_handshake.get('sync_mode') == state['sync_requested_mode']
                else None
            )
            if (
                not current_report_sha256
                or current_report_sha256 == previous_report_sha256
            ):
                if state['sync_requested_mode']=='PREFLIGHT':
                    preflight_status = "RUNNING"
            else:
                state.pop("sync_requested_mode", None)
                state.pop("sync_requested_at_unix", None)
                state.pop("sync_requested_previous_report_sha256", None)
        incremental_cursor = self._browser_companion_incremental_cursor()
        extension_connected = bridge_identity_verified
        configured = extension_connected
        capture_repair_required = bool(
            state.get("capture_repair_preflight_required")
        )
        capture_repair_target = state.get("capture_repair_target_version")
        capture_repair_completed = bool(
            capture_repair_required
            and capture_repair_target == identity["extension_version"]
            and bridge_identity_verified
            and full_sync_gate["eligible"]
        )
        if capture_repair_completed:
            capture_repair_required = False
            state.update(
                {
                    "capture_repair_preflight_required": False,
                    "capture_repair_completed_version": identity[
                        "extension_version"
                    ],
                    "capture_repair_report_sha256": full_sync_gate[
                        "report_sha256"
                    ],
                    "capture_repair_completed_at_unix": int(time.time()),
                }
            )
        if capture_repair_required:
            full_sync_gate = {
                **full_sync_gate,
                "eligible": False,
                "capture_repair_preflight_required": True,
            }
        incremental_sync_eligible = bool(
            incremental_cursor["eligible"] and not capture_repair_required
        )
        browser_reload_required = bool(
            prepared
            and package_current
            and (
                state.get("browser_reload_required")
                or not bridge_identity_verified
            )
        )
        if extension_connected:
            state.update(
                {
                    "extension_connected": True,
                    "extension_id": identity_receipt["extension_id"],
                    "identity_receipt_sha256": identity_receipt["sha256"],
                    "identity_observed_at": identity_receipt["observed_at"],
                    "connected_browser": identity_receipt.get("browser_family") or state.get("connected_browser"),
                    "browser_reload_required": False,
                }
            )
            if handshake:
                state.update(
                    {
                        "connected_browser": handshake["browser_family"],
                        "last_sync_report_sha256": handshake["sha256"],
                        "last_sync_report_generated_at": handshake["generated_at"],
                    }
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            self.write_json_file(path, state)
        return {
            "schema_version": "DesktopBrowserCompanionStatus-v3",
            "manual_import_available": True,
            "manual_capture_available": bool(prepared and package_current),
            "manual_import_observed": bool(import_verified or incremental_cursor["known_session_count"]),
            "automatic_sync_connection": "VERIFIED" if bridge_identity_verified else "UNCONFIRMED",
            "prepared": prepared,
            "extension_path": state.get("extension_path"),
            "path_changed": state.get("path_changed", False),
            "user_confirmed": user_confirmed,
            "import_verified": import_verified,
            "extension_connected": extension_connected,
            "bridge_identity_verified": bridge_identity_verified,
            "preflight_eligible": bool(
                prepared and package_current and bridge_identity_verified and self._browser_enabled_sites()
            ),
            "configured": configured,
            "package_current": package_current,
            "update_available": prepared and not package_current,
            "bridge_reload_required": False if extension_connected else browser_reload_required,
            "browser_reload_required": False if extension_connected else browser_reload_required,
            "providers": [row['provider_id'] for row in self._browser_enabled_sites()],
            "site_settings": self.browser_companion_sites(),
            "provider_sync": handshake["providers"] if handshake else {},
            "preflight_status": preflight_status,
            "preflight_report_sha256": (
                preflight_handshake.get("sha256")
                if preflight_handshake
                else None
            ),
            "latest_sync_mode": handshake.get("sync_mode") if handshake else None,
            "latest_per_provider_limit": (
                handshake.get("per_provider_limit") if handshake else None
            ),
            "latest_sync_report_sha256": (
                handshake.get("sha256") if handshake else None
            ),
            "next_sync_mode": (
                "INCREMENTAL" if incremental_sync_eligible else "PREFLIGHT"
            ),
            "incremental_sync_eligible": incremental_sync_eligible,
            "capture_repair_preflight_required": capture_repair_required,
            "capture_repair_target_version": capture_repair_target,
            "archive_provider_counts": incremental_cursor["provider_counts"],
            "incremental_known_session_count": incremental_cursor["known_session_count"],
            "incremental_overlap_fingerprint_count": incremental_cursor[
                "overlap_fingerprint_count"
            ],
            "full_sync_eligible": full_sync_gate["eligible"],
            "full_sync_gate": full_sync_gate,
            "connected_browser": state.get("connected_browser"),
            "selected_browser": browser_family(state.get("preferred_browser")) or browser_family(state.get("connected_browser")),
            "browser_options": self._browser_options(),
            "extension_version": identity["extension_version"],
            "identity_extension_version": (
                identity_receipt["extension_version"] if identity_receipt else None
            ),
            "report_extension_version": (
                latest_report["extension_version"] if latest_report else None
            ),
            "identity_receipt_sha256": (
                identity_receipt["sha256"] if identity_receipt else None
            ),
            "identity_observed_at": (
                identity_receipt["observed_at"] if identity_receipt else None
            ),
            "extension_id": (
                identity_receipt["extension_id"]
                if identity_receipt
                else state.get("extension_id")
            ),
            "cookie_permission": False,
            "browser_storage_read": False,
            "hidden_provider_api": False,
            "status": (
                "EXTENSION_IDENTITY_VERIFIED"
                if configured
                else "PACKAGE_UPDATE_REQUIRED"
                if prepared and not package_current
                else "AWAITING_EXTENSION_IDENTITY"
                if prepared and package_current
                else "NOT_CONFIGURED"
            ),
        }

    @staticmethod
    def _browser_executables() -> list[tuple[str, Path, str]]:
        return discover_browsers()

    def _browser_options(self) -> list[dict[str, Any]]:
        available = {browser_family(row[0]) for row in self._browser_executables()}
        return [{"id": key, "name": label, "installed": key in available}
                for key, label, _exe, _manager, _suffix in BROWSERS]

    def _launch_browser_companion_reload(
        self, preferred: str | None = None
    ) -> dict[str, Any]:
        """Open the public HTTPS marker consumed only by bridge 3.3.9+."""

        sites = self._browser_enabled_sites()
        trigger_url = (self._browser_sync_trigger('memorive-browser-companion-reload-v1')
                       .replace('?memorive_bridge_sync=v1#', '?memorive_bridge_reload=v1#')) if sites else None
        browsers = self._browser_executables()
        if not browsers:
            return {
                "schema_version": "DesktopBrowserCompanionReloadLaunchReceipt-v1",
                "selected_browser": None,
                "trigger_transport": "HTTPS_APPROVED_FRAGMENT_RELOAD",
                "trigger_url": trigger_url,
                "process_id": None,
                "status": "SUPPORTED_BROWSER_NOT_FOUND",
            }
        selected = select_browser(browsers, preferred)
        selected_browser, executable, _manager_url = selected
        # All sites disabled: use only the browser's local extension manager.
        trigger_url = trigger_url or _manager_url
        process_id = None
        if self.system_effects_enabled and os.name == "nt":
            process = subprocess.Popen(
                [str(executable), trigger_url], shell=False, close_fds=True
            )
            process_id = process.pid
        return {
            "schema_version": "DesktopBrowserCompanionReloadLaunchReceipt-v1",
            "selected_browser": selected_browser,
            "trigger_transport": "HTTPS_APPROVED_FRAGMENT_RELOAD",
            "trigger_url": trigger_url,
            "process_id": process_id,
            "status": (
                "BRIDGE_RELOAD_TRIGGERED" if process_id else "BRIDGE_RELOAD_READY"
            ),
        }

    def install_browser_companion(self, preferred_browser: str | None = None) -> dict[str, Any]:
        if not self._browser_enabled_sites():
            return {"status": "BROWSER_NO_VERIFIED_OFFICIAL_SITES", "prepared": False}
        source, required, package_sha256 = self._browser_companion_package()
        setup_path = self._browser_companion_state_path()
        try:
            prior_selection = json.loads(setup_path.read_text(encoding="utf-8")) if setup_path.is_file() else {}
        except (OSError, ValueError):
            prior_selection = {}
        if not isinstance(prior_selection, dict):
            prior_selection = {}
        target = preferred_browser or prior_selection.get("preferred_browser") or browser_family(prior_selection.get("connected_browser"))
        browsers = self._browser_executables()
        selected = select_browser(browsers, target) if browsers or target else None
        selected_family = browser_family(selected[0]) if selected else None
        switched_browser = bool(selected_family and selected_family != browser_family(prior_selection.get("preferred_browser") or prior_selection.get("connected_browser")))
        destination = self._browser_companion_export_root() / (selected_family.lower() if selected_family else "browser")
        prior_path = prior_selection.get("extension_path")
        path_changed = bool(prior_path and Path(prior_path).resolve() != destination.resolve())
        # Relocation changes Chromium's unpacked extension ID; require a fresh
        # load/receipt instead of accepting an old path's identity.
        switched_browser = switched_browser or path_changed
        self._check_browser_companion_destination(destination)
        prior_destination_version = self._browser_companion_installed_version(
            destination
        )
        destination.mkdir(parents=True, exist_ok=True)
        for name in sorted(required):
            shutil.copy2(source / name, destination / name)
        manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("manifest_version") != 3 or "cookies" in manifest.get("permissions", []):
            raise RuntimeError("BROWSER_COMPANION_MANIFEST_BOUNDARY_INVALID")
        setup_path = self._browser_companion_state_path()
        prior_state: dict[str, Any] = {}
        setup_path_io = windows_io_path(setup_path)
        if setup_path_io.is_file():
            try:
                loaded = json.loads(setup_path_io.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    prior_state = loaded
            except (OSError, json.JSONDecodeError):
                prior_state = {}
        prior_hashes = prior_state.get("imported_bundle_sha256s", [])
        if not isinstance(prior_hashes, list):
            prior_hashes = []
        if switched_browser:
            for key in ("extension_id", "connected_browser", "identity_receipt_sha256", "identity_observed_at", "sync_requested_mode", "sync_requested_at_unix", "sync_requested_previous_report_sha256"):
                prior_state.pop(key, None)
        prior_prepared = bool(prior_state.get("prepared") and not switched_browser)
        prior_connected_browser = prior_state.get("connected_browser")
        prior_extension_id = prior_state.get("extension_id")
        current_version = manifest.get("version")
        if not isinstance(current_version, str) or not current_version.strip():
            raise RuntimeError("BROWSER_COMPANION_MANIFEST_VERSION_MISSING")
        current_version = current_version.strip()
        prior_state_version = prior_state.get("package_extension_version")
        prior_version = prior_destination_version or (
            prior_state_version.strip()
            if isinstance(prior_state_version, str) and prior_state_version.strip()
            else None
        )
        package_unchanged = bool(
            prior_prepared
            and prior_destination_version == current_version
            and prior_state.get("package_sha256") == package_sha256
        )
        version_changed = bool(prior_version and prior_version != current_version)
        package_changed = bool(prior_prepared and not package_unchanged)
        capture_repair_required = bool(
            prior_state.get("capture_repair_preflight_required")
            or version_changed
            or package_changed
        )
        if prior_connected_browser not in FAMILIES:
            prior_connected_browser = None
        if not self._browser_companion_extension_id_valid(prior_extension_id):
            prior_extension_id = None
        current_identity = self._latest_browser_companion_identity_receipt(
            current_extension_version=current_version,
            expected_extension_id=prior_extension_id,
            expected_browser=selected_family,
            prepared_at_unix=max((value for value in (
                prior_state.get("prepared_at_unix"),
                prior_state.get("package_updated_at_unix"),
                prior_state.get("bridge_reload_requested_at_unix"),
            ) if isinstance(value, int)), default=None),
        )
        if path_changed:
            current_identity = None
        if prior_prepared and prior_extension_id is None and current_identity:
            prior_extension_id = current_identity["extension_id"]
        bridge_identity_current = bool(
            current_identity
            and current_identity.get("extension_id") == prior_extension_id
        )
        auto_reload_eligible = bool(
            prior_prepared
            and self._browser_companion_extension_id_valid(prior_extension_id)
        )
        reload_needed = bool(
            auto_reload_eligible
            and (not package_unchanged or not bridge_identity_current)
        )
        now = int(time.time())
        prepared_at_unix = (
            prior_state.get("prepared_at_unix")
            if package_unchanged
            and isinstance(prior_state.get("prepared_at_unix"), int)
            else now
        )
        next_state = {
            **prior_state,
            "schema_version": "DesktopBrowserCompanionSetupState-v1",
            "package_sha256": package_sha256,
            "prepared": True,
            "user_confirmed": bool(prior_state.get("user_confirmed")),
            "extension_connected": bridge_identity_current,
            "import_verified": bool(prior_state.get("import_verified")),
            "imported_bundle_sha256s": [
                value for value in prior_hashes if isinstance(value, str) and value
            ][-256:],
            "browser_reload_required": reload_needed,
            "prepared_at_unix": prepared_at_unix,
            "path_changed": path_changed,
            "connected_browser": prior_connected_browser,
            "preferred_browser": selected_family,
            "extension_path": str(destination),
            "extension_id": prior_extension_id,
            "package_extension_version": current_version,
            "capture_repair_preflight_required": capture_repair_required,
            "capture_repair_target_version": (
                current_version if capture_repair_required else None
            ),
        }
        if package_changed:
            next_state["package_updated_at_unix"] = now
        # On a new install nothing has made the companion's profile folder yet (saving the site list does).
        setup_path.parent.mkdir(parents=True, exist_ok=True)
        self.write_json_file(
            setup_path,
            next_state,
        )
        folder_opened = False
        browser_manager_opened = False
        selected_browser = None
        reload_receipt: dict[str, Any] | None = None
        if self.system_effects_enabled and os.name == "nt":
            if reload_needed:
                reload_receipt = self._launch_browser_companion_reload(
                    selected_family
                )
                selected_browser = reload_receipt.get("selected_browser")
                refreshed_state = json.loads(setup_path.read_text(encoding="utf-8"))
                refreshed_state["bridge_reload_requested_at_unix"] = int(time.time())
                refreshed_state["bridge_reload_requested_for"] = selected_browser
                self.write_json_file(setup_path, refreshed_state)
            elif not (package_unchanged and bridge_identity_current):
                subprocess.Popen(["explorer.exe", str(destination)], shell=False, close_fds=True)
                folder_opened = True
                if selected:
                    selected_browser, executable, manager_url = selected
                    subprocess.Popen([str(executable), manager_url], shell=False, close_fds=True)
                    browser_manager_opened = True
        return {
            "schema_version": "DesktopBrowserCompanionPreparationReceipt-v1",
            "extension_path": str(destination),
            "folder_opened": folder_opened,
            "browser_manager_opened": browser_manager_opened,
            "selected_browser": selected_browser,
            "supported_browsers": [row[1] for row in BROWSERS],
            "selected_browser_family": selected_family,
            "providers": [row['provider_id'] for row in self._browser_enabled_sites()],
            "package_unchanged": package_unchanged,
            "already_configured": bool(
                package_unchanged and bridge_identity_current
            ),
            "manual_load_required": not auto_reload_eligible,
            "path_changed": path_changed,
            "cookie_permission": False,
            "browser_storage_read": False,
            "hidden_provider_api": False,
            "one_time_setup": True,
            "extension_id": prior_extension_id,
            "bridge_reload_requested": bool(
                reload_receipt and reload_receipt.get("process_id")
            ),
            "capture_repair_preflight_required": capture_repair_required,
            "reload_receipt": reload_receipt,
            "status": (
                reload_receipt["status"]
                if reload_receipt
                else "ALREADY_CONFIGURED"
                if package_unchanged and bridge_identity_current
                else "AWAITING_EXTENSION_LOAD"
                if not auto_reload_eligible
                else "AWAITING_EXTENSION_IDENTITY"
            ),
        }

    def confirm_browser_companion_setup(self) -> dict[str, Any]:
        status = self.browser_companion_status()
        if not status["prepared"]:
            raise RuntimeError("BROWSER_COMPANION_NOT_PREPARED")
        if not status["extension_connected"]:
            raise RuntimeError("BROWSER_COMPANION_HANDSHAKE_NOT_FOUND")
        return {**status, "schema_version": "DesktopBrowserCompanionConfirmationReceipt-v2"}

    def reload_browser_companion(self) -> dict[str, Any]:
        """Reload the unpacked bridge without starting any provider sync."""

        status = self.browser_companion_status()
        if not status["prepared"]:
            raise RuntimeError("BROWSER_COMPANION_NOT_PREPARED")
        path = self._browser_companion_state_path()
        state = json.loads(path.read_text(encoding="utf-8"))
        state["browser_reload_required"] = True
        state["extension_connected"] = False
        state["bridge_reload_requested_at_unix"] = int(time.time())
        preferred = status.get("selected_browser") or status.get("connected_browser")
        self.write_json_file(path, state)
        receipt = self._launch_browser_companion_reload(preferred)
        if receipt["status"] == "SUPPORTED_BROWSER_NOT_FOUND":
            raise RuntimeError("SUPPORTED_BROWSER_NOT_FOUND")
        state["bridge_reload_requested_for"] = receipt["selected_browser"]
        self.write_json_file(path, state)
        return {
            **receipt,
            "expected_extension_version": status["extension_version"],
            "bridge_identity_verified": False,
            "browser_reload_required": True,
        }

    def run_browser_companion_reload(self) -> dict[str, Any]:
        """UI-facing alias for the explicit bridge self-reload action."""

        return self.reload_browser_companion()

    @staticmethod
    def _browser_companion_sync_window_contract() -> dict[str, Any]:
        """Describe the extension-owned window boundary for every sync launch."""

        return {
            "window_mode": "DEDICATED_VISIBLE",
            "dedicated_window_focused": False,
            "user_tab_reused": False,
            "user_tabs_queried": False,
            "user_tabs_closed": False,
            "maximum_capture_windows": 1,
            "per_provider_capture_mode": (
                "DEDICATED_VISIBLE_WINDOW_OWNED_TABS"
            ),
            "trigger_tab_created_once": True,
            "trigger_tab_lifecycle": (
                "EXTENSION_MIGRATES_THEN_CLOSES_TRIGGER_ONLY"
            ),
        }

    def _launch_browser_companion_sync_fragment(
        self,
        status: Mapping[str, Any],
        *,
        trigger_url: str,
        sync_mode: str,
        per_provider_limit: int,
        trigger_transport: str,
        incremental_cursor: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        browsers = self._browser_executables()
        if not browsers:
            raise RuntimeError("SUPPORTED_BROWSER_NOT_FOUND")
        preferred = status.get("selected_browser") or status.get("connected_browser")
        selected = select_browser(browsers, preferred)
        selected_browser, executable, _manager_url = selected
        process_id = None
        if self.system_effects_enabled and os.name == "nt":
            process = subprocess.Popen(
                [str(executable), trigger_url], shell=False, close_fds=True
            )
            process_id = process.pid
        receipt = {
            "schema_version": "DesktopBrowserCompanionSyncLaunchReceipt-v2",
            "selected_browser": selected_browser,
            "extension_id": status["extension_id"],
            "trigger_transport": trigger_transport,
            "trigger_url": trigger_url,
            "sync_mode": sync_mode,
            "per_provider_limit": per_provider_limit,
            "providers": [row['provider_id'] for row in self._browser_enabled_sites()],
            "site_profile_sha256": self.browser_companion_sites()['profile_sha256'],
            "process_id": process_id,
            **self._browser_companion_sync_window_contract(),
            "status": (
                "FULL_SYNC_TRIGGERED"
                if process_id and sync_mode == "FULL"
                else "FULL_SYNC_READY"
                if sync_mode == "FULL"
                else "INCREMENTAL_SYNC_TRIGGERED"
                if process_id and sync_mode == "INCREMENTAL"
                else "INCREMENTAL_SYNC_READY"
                if sync_mode == "INCREMENTAL"
                else "SYNC_TRIGGERED"
                if process_id
                else "SYNC_READY"
            ),
        }
        if sync_mode == "INCREMENTAL" and incremental_cursor:
            receipt.update(
                {
                    "cursor_schema_version": incremental_cursor.get(
                        "cursor_schema_version"
                    ),
                    "cursor_token_sha256": incremental_cursor.get(
                        "cursor_token_sha256"
                    ),
                    "known_session_count": incremental_cursor.get(
                        "known_session_count"
                    ),
                    "overlap_fingerprint_count": incremental_cursor.get(
                        "overlap_fingerprint_count"
                    ),
                    "archive_provider_counts": incremental_cursor.get(
                        "provider_counts"
                    ),
                    "raw_session_ids_in_trigger": False,
                    "raw_private_content_in_trigger": False,
                }
            )
        if process_id and self.profile_root is not None:
            state_path = self._browser_companion_state_path()
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                state = {}
            if isinstance(state, dict):
                state["sync_requested_mode"] = sync_mode
                state["sync_requested_at_unix"] = int(time.time())
                state["sync_requested_previous_report_sha256"] = status.get(
                    "latest_sync_report_sha256"
                )
                self.write_json_file(state_path, state)
        return receipt

    def run_browser_companion_test_read(self) -> dict[str, Any]:
        return self.run_browser_companion_sync('PREFLIGHT')

    def _browser_sync_trigger(self, suffix):
        rows = self._browser_enabled_sites()
        if not rows:
            raise ValueError('BROWSER_NO_ENABLED_SITES')
        entry = next((row['url'] for row in rows if row['provider_id']=='gemini'), rows[0]['url'])
        # No disabled provider page is opened even as a one-shot trigger.
        return entry + '?memorive_bridge_sync=v1#' + suffix

    def run_browser_companion_sync(self, requested_mode=None) -> dict[str, Any]:
        """Launch preflight first, then a privacy-safe bounded incremental sync."""

        status = self.browser_companion_status()
        if not self._browser_enabled_sites():
            return {'status':'BROWSER_NO_ENABLED_SITES','sync_mode':requested_mode or 'INCREMENTAL'}
        if requested_mode not in (None,'PREFLIGHT','INCREMENTAL'):
            raise ValueError('BROWSER_SYNC_MODE_INVALID')
        if not status["prepared"]:
            preparation = self.install_browser_companion()
            return {
                "schema_version": "DesktopBrowserCompanionSyncLaunchReceipt-v2",
                "extension_connected": False,
                "preparation": preparation,
                "sync_mode": "PREFLIGHT",
                "per_provider_limit": 2,
                **self._browser_companion_sync_window_contract(),
                "status": "PREPARATION_REQUIRED",
            }
        if (
            not status.get("bridge_identity_verified")
            or status.get("preflight_eligible") is not True
        ):
            return {
                "schema_version": "DesktopBrowserCompanionSyncLaunchReceipt-v3",
                "extension_connected": False,
                "bridge_identity_verified": False,
                "bridge_reload_required": bool(
                    status.get("bridge_reload_required")
                ),
                "expected_extension_version": status.get("extension_version"),
                "sync_mode": "PREFLIGHT",
                "per_provider_limit": 2,
                **self._browser_companion_sync_window_contract(),
                "status": "BRIDGE_IDENTITY_REQUIRED",
            }
        if requested_mode == 'INCREMENTAL' and status.get("capture_repair_preflight_required"):
            return {'status':'PREFLIGHT_REQUIRED','sync_mode':'INCREMENTAL'}
        if requested_mode == 'PREFLIGHT' or status.get("capture_repair_preflight_required"):
            return self._launch_browser_companion_sync_fragment(
                status,
                trigger_url=self._browser_sync_trigger('memorive-browser-session-preflight-2-v5'),
                sync_mode="PREFLIGHT",
                per_provider_limit=2,
                trigger_transport="HTTPS_APPROVED_FRAGMENT_PREFLIGHT",
            )
        incremental_cursor = self._browser_companion_incremental_cursor()
        if incremental_cursor["eligible"]:
            cursor_token = incremental_cursor["cursor_token"]
            return self._launch_browser_companion_sync_fragment(
                status,
                trigger_url=self._browser_sync_trigger(f'memorive-browser-session-incremental-v2:{cursor_token}'),
                sync_mode="INCREMENTAL",
                per_provider_limit=10,
                trigger_transport="HTTPS_APPROVED_FRAGMENT_INCREMENTAL",
                incremental_cursor=incremental_cursor,
            )
        if requested_mode == 'INCREMENTAL':
            return {'status':'INCREMENTAL_BASELINE_REQUIRED','sync_mode':'INCREMENTAL'}
        return self._launch_browser_companion_sync_fragment(
            status,
            trigger_url=self._browser_sync_trigger('memorive-browser-session-preflight-2-v5'),
            sync_mode="PREFLIGHT",
            per_provider_limit=2,
            trigger_transport="HTTPS_APPROVED_FRAGMENT_PREFLIGHT",
        )

    def run_browser_companion_full_sync(self) -> dict[str, Any]:
        """Launch full sync only after a current capability report passes every provider gate."""

        status = self.browser_companion_status()
        full_sync_gate = status.get("full_sync_gate", {})
        if (
            not status.get("prepared")
            or not status.get("extension_connected")
            or not status.get("bridge_identity_verified")
            or status.get("full_sync_eligible") is not True
        ):
            return {
                "schema_version": "DesktopBrowserCompanionSyncLaunchReceipt-v2",
                "extension_connected": bool(status.get("extension_connected")),
                "sync_mode": "FULL",
                "per_provider_limit": 50,
                "preflight_gate_passed": False,
                "full_sync_gate": full_sync_gate,
                "capture_repair_preflight_required": bool(
                    status.get("capture_repair_preflight_required")
                ),
                **self._browser_companion_sync_window_contract(),
                "status": "FULL_SYNC_PREFLIGHT_REQUIRED",
            }
        receipt = self._launch_browser_companion_sync_fragment(
            status,
            trigger_url=self._browser_sync_trigger('memorive-browser-session-full-v4'),
            sync_mode="FULL",
            per_provider_limit=50,
            trigger_transport="HTTPS_APPROVED_FRAGMENT_FULL",
        )
        receipt["preflight_gate_passed"] = True
        receipt["full_sync_gate"] = full_sync_gate
        return receipt

    def restart_browser_companion(self) -> dict[str, Any]:
        """Compatibility alias; reload only the unpacked extension service worker."""

        return self.reload_browser_companion()

    def _browser_session_managed_root(self) -> Path:
        """Return the app-owned durable intake next to the user's Memorive state."""

        destination = (
            self.profile_path("BROWSER_COMPANION") / "session-library"
        ).resolve(strict=False)
        try:
            destination.relative_to(self.profile_root.resolve(strict=False))
        except ValueError as error:
            raise RuntimeError("BROWSER_SESSION_MANAGED_ROOT_ESCAPE") from error
        return destination

    def _retain_browser_session_artifact(
        self, source: Path, payload_bytes: bytes
    ) -> Path:
        """Keep a durable app-owned copy without overwriting a conflicting file."""

        managed_root = self._browser_session_managed_root()
        windows_io_path(managed_root).mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(payload_bytes).hexdigest().lower()
        destination = managed_root / source.name
        destination_io = windows_io_path(destination)
        if destination_io.is_file():
            if hashlib.sha256(destination_io.read_bytes()).hexdigest().lower() == digest:
                return destination
            destination = managed_root / f"{source.stem}-{digest[:12]}{source.suffix}"
            destination_io = windows_io_path(destination)
        if not destination_io.exists():
            temporary = managed_root / f".session.{uuid.uuid4().hex}.tmp"
            temporary_io = windows_io_path(temporary)
            temporary_io.write_bytes(payload_bytes)
            os.replace(temporary_io, destination_io)
        return destination

    def _browser_session_download_roots(self) -> list[Path]:
        """Resolve the app-owned intake first, then compatible browser downloads."""
        candidates: list[Path] = [self._browser_session_managed_root()]
        # A profile can consume its own intake without discovering other data.
        # Setup restores the legacy known-folder discovery only for the plugin
        # output subfolder. General Downloads remain outside automatic scope.
        # Explicit profile-scoped directory grants remain supported.
        setup = {}
        setup_path = self._browser_companion_state_path()
        if setup_path.is_file():
            try:
                value = json.loads(setup_path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    setup = value
            except (OSError, ValueError):
                pass
        grants = setup.get("authorized_import_sources", [])
        if isinstance(grants, list):
            for grant in grants:
                if (isinstance(grant, dict) and grant.get("authorized") is True
                    and grant.get("kind") == "DIRECTORY" and
                    isinstance(grant.get("path"), str) and
                    Path(grant["path"]).is_absolute()):
                    path = Path(grant["path"])
                    path_io = windows_io_path(path)
                    if path_io.is_dir() and not (getattr(path_io.stat(),"st_file_attributes",0) & 0x400):
                        candidates.append(path)
        legacy_roots: list[Path] = []
        configured = os.environ.get("MEMORIVE_BROWSER_SESSION_IMPORT_DIR")
        if configured:
            legacy_roots.append(Path(configured).expanduser())
        else:
            legacy_roots.append(Path.home() / "Downloads")
            for environment_name in ("USERPROFILE", "OneDrive", "OneDriveConsumer"):
                if root := os.environ.get(environment_name):
                    legacy_roots.append(Path(root) / "Downloads")
            if os.name == "nt":
                try:
                    import winreg

                    with winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
                    ) as key:
                        raw, _kind = winreg.QueryValueEx(
                            key, "{374DE290-123F-4565-9164-39C4925E467B}"
                        )
                        if isinstance(raw, str) and raw.strip():
                            legacy_roots.append(Path(os.path.expandvars(raw)).expanduser())
                except (OSError, ImportError):
                    pass
        if configured:
            candidates.extend(path for path in legacy_roots if path.is_absolute())
        elif setup.get("prepared") is True:
            candidates.extend(path / "Memorive会话桥接" for path in legacy_roots if path.is_absolute())
        resolved: list[Path] = []
        seen: set[Path] = set()
        for candidate in candidates:
            try:
                value = candidate.resolve(strict=False)
                value_io = windows_io_path(value)
            except OSError:
                continue
            if value_io.is_dir() and value not in seen:
                seen.add(value)
                resolved.append(value)
        return resolved

    def sync_browser_sessions_from_downloads(self) -> dict[str, Any]:
        """Import browser bundles and retain them under Memorive managed state."""
        candidates: dict[Path, None] = {}
        reports: dict[Path, None] = {}
        for root in self._browser_session_download_roots():
            for folder in (root, root / "Memorive会话桥接"):
                folder_io = windows_io_path(folder)
                if not folder_io.is_dir():
                    continue
                for path_io in folder_io.glob("memorive-session-library-sync-report-*.json"):
                    if path_io.is_file():
                        reports[(folder / path_io.name).resolve(strict=False)] = None
                for path_io in folder_io.glob("memorive-session-library-*.json"):
                    if (
                        path_io.is_file()
                        and re.fullmatch(
                            r"memorive-session-library-(?:deepseek|gemini|kimi|universal|web-[a-f0-9]{16})-.+\.json",
                            path_io.name,
                            flags=re.IGNORECASE,
                        )
                    ):
                        candidates[(folder / path_io.name).resolve(strict=False)] = None

        setup_path = self._browser_companion_state_path()
        setup_state: dict[str, Any] = {}
        setup_path_io = windows_io_path(setup_path)
        if setup_path_io.is_file():
            try:
                loaded = json.loads(setup_path_io.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    setup_state = loaded
            except (OSError, json.JSONDecodeError):
                setup_state = {}
        prior_hashes = setup_state.get("imported_bundle_sha256s", [])
        imported_hashes = {
            value for value in prior_hashes if isinstance(value, str) and value
        }
        # Automatic intake belongs to this configured bridge, not every old
        # test export that happens to remain in the Downloads folder.
        intake_since = setup_state.get("prepared_at_unix") if setup_state.get("prepared") else None
        site_profile = setup_state.get("site_profile_sha256") if setup_state.get("prepared") else None
        ignored_files = 0
        imported_files = 0
        imported_sessions = 0
        retained_files: set[Path] = set()
        failures: list[dict[str, str]] = []
        for report in reports:
            try:
                report_bytes = windows_io_path(report).read_bytes()
                if 0 < len(report_bytes) <= 1024 * 1024:
                    retained_files.add(
                        self._retain_browser_session_artifact(report, report_bytes)
                    )
            except OSError:
                continue
        for source in sorted(candidates, key=lambda value: (windows_io_path(value).stat().st_mtime_ns, value.name)):
            try:
                source_io = windows_io_path(source)
                size = source_io.stat().st_size
                if size <= 0 or size > 128 * 1024 * 1024:
                    raise ValueError("BROWSER_SESSION_IMPORT_SIZE_INVALID")
                payload_bytes = source_io.read_bytes()
                digest = hashlib.sha256(payload_bytes).hexdigest().upper()
                if digest in imported_hashes:
                    continue
                payload = json.loads(payload_bytes.decode("utf-8-sig"))
                if isinstance(intake_since, int) or site_profile:
                    generated = payload.get("generated_at") if isinstance(payload, dict) else None
                    try:
                        generated_at = datetime.fromisoformat(str(generated).replace("Z", "+00:00"))
                        current_capture = generated_at.tzinfo is not None and (
                            not isinstance(intake_since, int) or generated_at.timestamp() >= intake_since
                        )
                    except (TypeError, ValueError):
                        current_capture = False
                    if not current_capture or (site_profile and payload.get("site_profile_sha256") != site_profile):
                        ignored_files += 1
                        continue
                retained_files.add(
                    self._retain_browser_session_artifact(source, payload_bytes)
                )
                receipt = self.sessions.import_session_file(source)
                if receipt.get("status") != "PASS":
                    raise ValueError("BROWSER_SESSION_IMPORT_NOT_ACCEPTED")
                imported_hashes.add(digest)
                imported_files += 1
                imported_sessions += int(receipt.get("imported_count", 0))
                # Save each accepted file so interruption does not replay the batch.
                if setup_path_io.is_file():
                    latest_state = json.loads(setup_path_io.read_text(encoding="utf-8"))
                    if isinstance(latest_state, dict):
                        setup_state.update(latest_state)
                imported_hashes.update(setup_state.get("imported_bundle_sha256s", []))
                setup_state.update(import_verified=True,
                    imported_bundle_sha256s=sorted(imported_hashes)[-256:],
                    verified_at_unix=int(time.time()))
                self.write_json_file(setup_path, setup_state)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                failures.append(
                    {
                        "source_name": source.name,
                        "error_code": (
                            re.sub(
                                r"[^A-Z0-9_]+",
                                "_",
                                (str(error) or type(error).__name__).upper(),
                            ).strip("_")[:120]
                            or type(error).__name__.upper()
                        ),
                    }
                )

        if imported_files:
            _package, _required, package_sha256 = self._browser_companion_package()
            setup_state.update(
                {
                    "schema_version": "DesktopBrowserCompanionSetupState-v1",
                    "package_sha256": setup_state.get("package_sha256", package_sha256),
                    "prepared": bool(setup_state.get("prepared")),
                    "user_confirmed": False,
                    "import_verified": True,
                    "imported_bundle_sha256s": sorted(imported_hashes)[-256:],
                    "verified_at_unix": int(time.time()),
                }
            )
            windows_io_path(setup_path.parent).mkdir(parents=True, exist_ok=True)
            self.write_json_file(setup_path, setup_state)
        return {
            "schema_version": "DesktopBrowserSessionLibraryAutoImportReceipt-v1",
            "candidate_file_count": len(candidates),
            "ignored_historical_or_other_profile_count": ignored_files,
            "imported_file_count": imported_files,
            "imported_session_count": imported_sessions,
            "managed_artifact_count": len(retained_files),
            "managed_directory": str(self._browser_session_managed_root()),
            "failure_count": len(failures),
            "failures": failures,
            "remote_session_mutations": 0,
            "cookie_value_reads": 0,
            "browser_storage_value_reads": 0,
            "status": "PASS" if not failures else "PASS_WITH_REJECTED_FILES",
        }

    def import_browser_sessions_file(self) -> dict[str, Any]:
        if self.window is None:
            raise RuntimeError("PRODUCT_WINDOW_NOT_ATTACHED")
        from webview import FileDialog
        managed = self._browser_session_managed_root()
        downloads = Path.home() / "Downloads"
        selected = self.window.create_file_dialog(
            FileDialog.OPEN,
            directory=str(
                managed
                if managed.is_dir()
                else downloads
                if downloads.is_dir()
                else self.effective_directory("workspace_root")
            ),
            allow_multiple=False,
            file_types=("JSON files (*.json)",),
        )
        if not selected:
            return {
                "schema_version": "DesktopBrowserSessionFileImportReceipt-v1",
                "status": "CANCELLED",
            }
        selected_path = selected if isinstance(selected, str) else selected[0]
        source = Path(selected_path).resolve(strict=False)
        source_io = windows_io_path(source)
        if not source_io.is_file() or source.suffix.casefold() != ".json":
            raise ValueError("BROWSER_SESSION_IMPORT_FILE_INVALID")
        size = source_io.stat().st_size
        if size <= 0 or size > 128 * 1024 * 1024:
            raise ValueError("BROWSER_SESSION_IMPORT_SIZE_INVALID")
        receipt = self.sessions.import_session_file(source)
        setup_path = self._browser_companion_state_path()
        if receipt.get("status") == "PASS":
            retained_path = self._retain_browser_session_artifact(
                source, source_io.read_bytes()
            )
            _package, _required, package_sha256 = self._browser_companion_package()
            setup_path.parent.mkdir(parents=True, exist_ok=True)
            setup_state = (
                json.loads(setup_path.read_text(encoding="utf-8"))
                if setup_path.is_file()
                else {
                    "schema_version": "DesktopBrowserCompanionSetupState-v1",
                    "package_sha256": package_sha256,
                    "prepared": False,
                    "prepared_at_unix": None,
                }
            )
            setup_state["package_sha256"] = package_sha256
            setup_state["import_verified"] = True
            setup_state["user_confirmed"] = False
            imported_hashes = setup_state.get("imported_bundle_sha256s", [])
            if not isinstance(imported_hashes, list):
                imported_hashes = []
            imported_hashes.append(hashlib.sha256(source_io.read_bytes()).hexdigest().upper())
            setup_state["imported_bundle_sha256s"] = sorted(
                {value for value in imported_hashes if isinstance(value, str) and value}
            )[-256:]
            setup_state["verified_at_unix"] = int(time.time())
            self.write_json_file(setup_path, setup_state)
            receipt = {
                **receipt,
                "managed_copy": str(retained_path),
            }
        return {
            "schema_version": "DesktopBrowserSessionFileImportReceipt-v1",
            **receipt,
            "source_name": source.name,
            "source_sha256": hashlib.sha256(source_io.read_bytes()).hexdigest().upper(),
            "source_bytes": size,
        }
