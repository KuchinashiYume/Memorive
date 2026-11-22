"""The browser extension as the desktop runs it: prepare the extension for the user's browser, tell whether the
browser has loaded it, and bring the conversations it records into the session library.

ProductApi builds one BrowserCompanionDesktop at startup and forwards to it the window's
browser_companion_status, install_browser_companion, confirm_browser_companion_setup,
open_browser_companion_folder, sync_browser_sessions_from_downloads and import_browser_sessions_file calls
(memorive/desktop/api/browser_companion.py). The extension saves each recording, and an identity receipt when it
starts, into the browser's download folder ("Memorive会话桥接"); refreshing the sessions page hands each recording
to the sessions controller (memorive/sessions/controller/). Its own state is in the BROWSER_COMPANION profile
folder (setup_v1.json, session-library); the prepared extension is under AppData/Local/Memorive/Bridge.

The site sync of earlier versions is gone; the files it wrote still import (saved_site_settings).
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any, Callable
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

    def saved_site_settings(self) -> dict[str, Any] | None:
        """The site list the site sync of earlier versions saved, or None: the sessions controller checks the
        custom-site files that sync wrote against it on import (site_profiles.saved_site_settings).
        Compatibility reader for those files; keep."""
        from memorive.sessions.companion.site_profiles import saved_site_settings
        return saved_site_settings(self.profile_path("BROWSER_COMPANION"))

    def _browser_companion_package(self) -> tuple[Path, set[str], str]:
        source = (self.source_root / "memorive" / "sessions" / "companion" / "extension").resolve()
        try:
            source.relative_to(self.source_root)
        except ValueError as error:
            raise RuntimeError("BROWSER_COMPANION_SOURCE_ESCAPE") from error
        # Paths relative to the extension folder; the three languages live in _locales/<language>/.
        required = {
            "manifest.json",
            "background.js",
            "content.js",
            "popup.html",
            "popup.css",
            "popup.js",
            "README.txt",
            "site_profiles.js",
            "recording.js", "browser_identity.js", "i18n.js",
            "_locales/en/messages.json", "_locales/ja/messages.json", "_locales/zh_CN/messages.json",
        }
        if not source.is_dir() or {
            path.relative_to(source).as_posix() for path in source.rglob("*") if path.is_file()
        } != required:
            raise RuntimeError("BROWSER_COMPANION_PACKAGE_INVALID")
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
        # chrome.runtime.id into its identity receipt instead.
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
        """The newest identity receipt of the loaded extension (kept under session-library), or None.

        The extension saves one when its service worker starts with a new version (background.js
        writeIdentityReceipt). A receipt counts only for the prepared version, saved after the extension was
        prepared or updated, by the same extension id and browser when those are known, so an older file left
        in Downloads cannot stand for the extension now loaded."""

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
                if not isinstance(payload, dict):
                    continue
                extension_id = payload.get("extension_id")
                browser = payload.get("browser_family")
                observed_at = payload.get("observed_at")
                if (
                    payload.get("schema_version") != "MEMORIVE_BROWSER_COMPANION_IDENTITY_V1"
                    or not self._browser_companion_extension_id_valid(extension_id)
                    or (expected_extension_id and extension_id != expected_extension_id)
                    or payload.get("extension_version") != current_extension_version
                    or browser not in FAMILIES
                    or (expected_browser is not None and browser != expected_browser)
                    or not isinstance(observed_at, str)
                    or not observed_at.strip()
                ):
                    continue
                retained_path = self._retain_browser_session_artifact(
                    path, payload_bytes
                )
                return {
                    "path": retained_path,
                    "sha256": hashlib.sha256(payload_bytes).hexdigest().upper(),
                    "extension_id": extension_id,
                    "extension_version": current_extension_version,
                    "observed_at": observed_at,
                    "browser_family": browser,
                }
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
        return None

    def refresh_prepared_package(self) -> dict[str, Any]:
        """At startup, copy a newer extension over the prepared one, keeping the setup. The browser keeps
        running the old copy until the user reloads the extension on its extensions page."""
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
        if (
            state.get("package_sha256") == package_sha256
            and self._browser_companion_installed_version(destination) == current_version
            and not path_changed
        ):
            return {"updated": False, "status": "CURRENT"}
        destination.mkdir(parents=True, exist_ok=True)
        for name in sorted(required):
            (destination / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / name, destination / name)
        state["package_sha256"] = package_sha256
        if path_changed:
            state.update(extension_path=str(destination), path_changed=True,
                         prepared_at_unix=int(time.time()), extension_id=None,
                         extension_connected=False, connected_browser=None)
            for key in ('identity_receipt_sha256', 'identity_observed_at'):
                state.pop(key, None)
        state["browser_reload_required"] = True
        state["package_updated_at_unix"] = int(time.time())
        state["package_extension_version"] = current_version
        self.write_json_file(state_path, state)
        return {
            "updated": True,
            "browser_reload_required": True,
            "status": "UPDATED",
        }

    def browser_companion_status(self) -> dict[str, Any]:
        """The extension's setup as the settings and sessions pages show it. It counts as connected
        (bridge_identity_verified, configured) once the browser has saved an identity receipt for the prepared
        package; update_available means a newer extension waits to be prepared."""
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
        import_verified = bool(state.get("import_verified"))
        # A receipt counts only when saved after the package was prepared or updated. Old setup files carry no
        # timestamps; their own modification time is then the lower bound.
        try:
            state_file_mtime = int(path.stat().st_mtime)
        except OSError:
            state_file_mtime = None
        freshness_floor = max(
            (
                value
                for value in (state.get("prepared_at_unix"), state.get("package_updated_at_unix"))
                if isinstance(value, int)
            ),
            default=state_file_mtime,
        )
        identity_receipt = self._latest_browser_companion_identity_receipt(
            current_extension_version=identity["extension_version"],
            expected_extension_id=(
                state.get("extension_id")
                if self._browser_companion_extension_id_valid(state.get("extension_id"))
                else None
            ),
            prepared_at_unix=freshness_floor,
            expected_browser=browser_family(state.get("preferred_browser")),
        )
        connected = bool(identity_receipt and package_current)
        if connected:
            state.update(
                {
                    "extension_connected": True,
                    "extension_id": identity_receipt["extension_id"],
                    "identity_receipt_sha256": identity_receipt["sha256"],
                    "identity_observed_at": identity_receipt["observed_at"],
                    "connected_browser": identity_receipt["browser_family"],
                    "browser_reload_required": False,
                }
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            self.write_json_file(path, state)
        return {
            "schema_version": "DesktopBrowserCompanionStatus-v4",
            "manual_import_available": True,
            "manual_capture_available": bool(prepared and package_current),
            "manual_import_observed": import_verified,
            "prepared": prepared,
            "extension_path": state.get("extension_path"),
            "path_changed": state.get("path_changed", False),
            "user_confirmed": bool(state.get("user_confirmed")),
            "import_verified": import_verified,
            "extension_connected": connected,
            "bridge_identity_verified": connected,
            "configured": connected,
            "package_current": package_current,
            "update_available": prepared and not package_current,
            "browser_reload_required": bool(prepared and package_current and not connected),
            "connected_browser": state.get("connected_browser"),
            "selected_browser": browser_family(state.get("preferred_browser")) or browser_family(state.get("connected_browser")),
            "browser_options": self._browser_options(),
            "extension_version": identity["extension_version"],
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
            "status": (
                "EXTENSION_IDENTITY_VERIFIED"
                if connected
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

    def install_browser_companion(self, preferred_browser: str | None = None) -> dict[str, Any]:
        """Copy the extension into the folder for the chosen browser and, unless that browser already runs this
        very package, open the folder and the browser's extensions page: the user loads the folder the first time
        and reloads the extension after an update. Another browser or another folder (where the unpacked
        extension gets a new id) needs a fresh load."""
        source, required, package_sha256 = self._browser_companion_package()
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
        prior_browser = browser_family(prior_state.get("preferred_browser") or prior_state.get("connected_browser"))
        target = preferred_browser or prior_state.get("preferred_browser") or browser_family(prior_state.get("connected_browser"))
        browsers = self._browser_executables()
        selected = select_browser(browsers, target) if browsers or target else None
        selected_family = browser_family(selected[0]) if selected else None
        destination = self._browser_companion_export_root() / (selected_family.lower() if selected_family else "browser")
        prior_path = prior_state.get("extension_path")
        path_changed = bool(prior_path and Path(prior_path).resolve() != destination.resolve())
        switched_browser = path_changed or bool(selected_family and selected_family != prior_browser)
        self._check_browser_companion_destination(destination)
        prior_destination_version = self._browser_companion_installed_version(destination)
        destination.mkdir(parents=True, exist_ok=True)
        for name in sorted(required):
            (destination / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / name, destination / name)
        current_version = json.loads((destination / "manifest.json").read_text(encoding="utf-8")).get("version")
        if not isinstance(current_version, str) or not current_version.strip():
            raise RuntimeError("BROWSER_COMPANION_MANIFEST_VERSION_MISSING")
        current_version = current_version.strip()
        prior_hashes = prior_state.get("imported_bundle_sha256s", [])
        if not isinstance(prior_hashes, list):
            prior_hashes = []
        if switched_browser:
            for key in ("extension_id", "connected_browser", "identity_receipt_sha256", "identity_observed_at"):
                prior_state.pop(key, None)
        prior_prepared = bool(prior_state.get("prepared") and not switched_browser)
        prior_connected_browser = prior_state.get("connected_browser")
        if prior_connected_browser not in FAMILIES:
            prior_connected_browser = None
        prior_extension_id = prior_state.get("extension_id")
        if not self._browser_companion_extension_id_valid(prior_extension_id):
            prior_extension_id = None
        package_unchanged = bool(
            prior_prepared
            and prior_destination_version == current_version
            and prior_state.get("package_sha256") == package_sha256
        )
        current_identity = None if path_changed else self._latest_browser_companion_identity_receipt(
            current_extension_version=current_version,
            expected_extension_id=prior_extension_id,
            expected_browser=selected_family,
            prepared_at_unix=max((value for value in (
                prior_state.get("prepared_at_unix"),
                prior_state.get("package_updated_at_unix"),
            ) if isinstance(value, int)), default=None),
        )
        if prior_prepared and prior_extension_id is None and current_identity:
            prior_extension_id = current_identity["extension_id"]
        already_configured = bool(
            package_unchanged
            and current_identity
            and current_identity.get("extension_id") == prior_extension_id
        )
        # Loaded before: the browser knows the folder, so an update only needs a reload there.
        loaded_before = bool(prior_prepared and prior_extension_id)
        now = int(time.time())
        next_state = {
            **prior_state,
            "schema_version": "DesktopBrowserCompanionSetupState-v1",
            "package_sha256": package_sha256,
            "prepared": True,
            "user_confirmed": bool(prior_state.get("user_confirmed")),
            "extension_connected": already_configured,
            "import_verified": bool(prior_state.get("import_verified")),
            "imported_bundle_sha256s": [
                value for value in prior_hashes if isinstance(value, str) and value
            ][-256:],
            "browser_reload_required": bool(loaded_before and not already_configured),
            "prepared_at_unix": (
                prior_state["prepared_at_unix"]
                if package_unchanged and isinstance(prior_state.get("prepared_at_unix"), int)
                else now
            ),
            "path_changed": path_changed,
            "connected_browser": prior_connected_browser,
            "preferred_browser": selected_family,
            "extension_path": str(destination),
            "extension_id": prior_extension_id,
            "package_extension_version": current_version,
        }
        if prior_prepared and not package_unchanged:
            next_state["package_updated_at_unix"] = now
        # On a new install nothing has made the companion's profile folder yet.
        setup_path.parent.mkdir(parents=True, exist_ok=True)
        self.write_json_file(setup_path, next_state)
        folder_opened = False
        browser_manager_opened = False
        selected_browser = None
        if self.system_effects_enabled and os.name == "nt" and not already_configured:
            subprocess.Popen(["explorer.exe", str(destination)], shell=False, close_fds=True)
            folder_opened = True
            if selected:
                selected_browser, executable, manager_url = selected
                subprocess.Popen([str(executable), manager_url], shell=False, close_fds=True)
                browser_manager_opened = True
        return {
            "schema_version": "DesktopBrowserCompanionPreparationReceipt-v2",
            "extension_path": str(destination),
            "folder_opened": folder_opened,
            "browser_manager_opened": browser_manager_opened,
            "selected_browser": selected_browser,
            "supported_browsers": [row[1] for row in BROWSERS],
            "selected_browser_family": selected_family,
            "package_unchanged": package_unchanged,
            "already_configured": already_configured,
            "manual_load_required": not loaded_before,
            "path_changed": path_changed,
            "extension_id": prior_extension_id,
            "status": (
                "ALREADY_CONFIGURED"
                if already_configured
                else "AWAITING_EXTENSION_RELOAD"
                if loaded_before
                else "AWAITING_EXTENSION_LOAD"
            ),
        }

    def confirm_browser_companion_setup(self) -> dict[str, Any]:
        status = self.browser_companion_status()
        if not status["prepared"]:
            raise RuntimeError("BROWSER_COMPANION_NOT_PREPARED")
        if not status["extension_connected"]:
            raise RuntimeError("BROWSER_COMPANION_HANDSHAKE_NOT_FOUND")
        return {**status, "schema_version": "DesktopBrowserCompanionConfirmationReceipt-v2"}

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

    def _clear_retained_download(self, source: Path, retained: Path, payload_bytes: bytes) -> bool:
        """Remove a bridge file from the browser's download folder once Memorive keeps an identical copy, so
        captures move out of Downloads instead of piling up there. Only files in the extension's own
        "Memorive会话桥接" download folder go; Memorive's own folder and folders the user granted stay as
        they are, and so does a file whose copy is missing or differs."""
        managed_root = self._browser_session_managed_root().resolve(strict=False)
        source = source.resolve(strict=False)
        if source.parent.name != "Memorive会话桥接" or source.is_relative_to(managed_root):
            return False
        try:
            if windows_io_path(retained).read_bytes() != payload_bytes:
                return False
            windows_io_path(source).unlink()
        except OSError:
            return False  # still open in the browser, or already gone: try again on the next refresh
        return True

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
        """Import the recordings in the download folders (and the session files of the earlier site sync, whose
        names carry a site), keep a copy of each under session-library and move it out of Downloads."""
        candidates: dict[Path, None] = {}
        for root in self._browser_session_download_roots():
            for folder in (root, root / "Memorive会话桥接"):
                folder_io = windows_io_path(folder)
                if not folder_io.is_dir():
                    continue
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
        ignored_files = 0
        moved_downloads = 0
        imported_files = 0
        imported_sessions = 0
        retained_files: set[Path] = set()
        failures: list[dict[str, str]] = []
        for source in sorted(candidates, key=lambda value: (windows_io_path(value).stat().st_mtime_ns, value.name)):
            try:
                source_io = windows_io_path(source)
                size = source_io.stat().st_size
                if size <= 0 or size > 128 * 1024 * 1024:
                    raise ValueError("BROWSER_SESSION_IMPORT_SIZE_INVALID")
                payload_bytes = source_io.read_bytes()
                digest = hashlib.sha256(payload_bytes).hexdigest().upper()
                if digest in imported_hashes:
                    retained = self._retain_browser_session_artifact(source, payload_bytes)
                    moved_downloads += self._clear_retained_download(source, retained, payload_bytes)
                    continue
                payload = json.loads(payload_bytes.decode("utf-8-sig"))
                if isinstance(intake_since, int):
                    generated = payload.get("generated_at") if isinstance(payload, dict) else None
                    try:
                        generated_at = datetime.fromisoformat(str(generated).replace("Z", "+00:00"))
                        current_capture = generated_at.tzinfo is not None and generated_at.timestamp() >= intake_since
                    except (TypeError, ValueError):
                        current_capture = False
                    if not current_capture:
                        ignored_files += 1
                        continue
                retained = self._retain_browser_session_artifact(source, payload_bytes)
                retained_files.add(retained)
                receipt = self.sessions.import_session_file(source)
                if receipt.get("status") != "PASS":
                    raise ValueError("BROWSER_SESSION_IMPORT_NOT_ACCEPTED")
                moved_downloads += self._clear_retained_download(source, retained, payload_bytes)
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
            "moved_download_count": moved_downloads,
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
