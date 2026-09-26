from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import hashlib
import json
import os
import re
import secrets
import stat
import sys
import threading
import time
import uuid

from bridge_client import BridgeClient
from common import (EXPRESSION_EVENT_IDS, OPERATIONS, PROTOCOL, build_suite_catalog, build_suite_plan,
    build_test_attachment, cleanup_failure, delete_session, digest, encode,
    identifier, now, read_json, validate_command, write_json)
from process_owner import OwnedProcess, process_identity

DISCONNECT_FAILURE_THRESHOLD = 3
RECOVERY_SUCCESS_THRESHOLD = 2
SNAPSHOT_INTERVAL_SECONDS = 3.0
BRIDGE_BACKOFF_MAX_SECONDS = 12.0
EXPRESSION_POLL_INTERVAL_SECONDS = .25
EXPRESSION_BACKOFF_MAX_SECONDS = 4.0

# Only user-authored configuration is copied into a disposable build session.
# Runtime state, messages, inbox content, libraries, receipts, locks and queued
# jobs are deliberately absent from this list.
LOCAL_PROFILE_FILES = (
    'settings/settings.json',
    'settings/user_preferences.json',
    'settings/research_preferences.json',
    'settings/leaderboard_preferences.json',
    'settings/external_data/external_sources.json',
    'ui/assistant_preferences_v3.json',
)
LOCAL_PROFILE_FILE_MAX_BYTES = 1024 * 1024

CAPABILITY_RELATIVE_PATHS = (
    'memorive-console-capabilities.json',
    '_internal/product/desktop/memorive-console-capabilities.json',
    '_internal/memorive-console-capabilities.json',
)
RELEASE_IDENTITY_RELATIVE_PATHS = (
    'release_identity_binding.json',
    '_internal/product/desktop/release_identity_binding.json',
    '_internal/release_identity_binding.json',
)
CAPABILITY_SCHEMAS = {
    'Memorive-ConsoleBuildCapabilities-v1',
}
RELEASE_IDENTITY_SCHEMAS = {'DesktopReleaseIdentityBinding-v1'}


def local_profile_root():
    local = os.environ.get('LOCALAPPDATA')
    if not local:
        return None
    candidates = (
        Path(local) / 'Memorive' / 'desktop-review' / 'state' / 'profile',
        Path(local) / 'Memorive' / 'profile',
    )
    return next((path.resolve() for path in candidates if path.is_dir()), candidates[0].resolve())


def seed_local_profile(data_root, source_profile=None):
    """Copy an exact settings allowlist into this session's disposable profile."""
    data_root = Path(data_root).resolve(strict=True)
    source = Path(source_profile).resolve() if source_profile is not None else local_profile_root()
    receipt = {
        'schema_version': 'Memorive-ConsoleLocalProfileSeed-v1',
        'status': 'NOT_FOUND',
        'copied_file_count': 0,
        'copied_files': [],
        'skipped_files': [],
        'automatic_execution': False,
        'source_profile_mutated': False,
    }
    if source is None or not source.is_dir():
        return receipt
    destination_root = data_root / 'state' / 'profile'
    source_hashes = {}
    for relative in LOCAL_PROFILE_FILES:
        source_path = source / Path(relative)
        if not source_path.exists():
            continue
        try:
            info = source_path.lstat()
            resolved = source_path.resolve(strict=True)
            if (not stat.S_ISREG(info.st_mode) or source_path.is_symlink()
                    or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
                    or not resolved.is_relative_to(source)
                    or info.st_size > LOCAL_PROFILE_FILE_MAX_BYTES):
                raise ValueError('PROFILE_SEED_FILE_REJECTED')
            payload = source_path.read_bytes()
            json.loads(payload.decode('utf-8'))
            destination = destination_root / Path(relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise ValueError('PROFILE_SEED_DESTINATION_EXISTS')
            destination.write_bytes(payload)
            checksum = hashlib.sha256(payload).hexdigest().upper()
            source_hashes[relative] = checksum
            receipt['copied_files'].append({
                'relative_path': relative.replace('\\', '/'),
                'bytes': len(payload),
                'sha256': checksum,
            })
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            receipt['skipped_files'].append({
                'relative_path': relative.replace('\\', '/'),
                'reason': str(error) if str(error).startswith('PROFILE_SEED_') else type(error).__name__,
            })
    changed = []
    for relative, expected in source_hashes.items():
        try:
            actual = hashlib.sha256((source / Path(relative)).read_bytes()).hexdigest().upper()
        except OSError:
            actual = None
        if actual != expected:
            changed.append(relative.replace('\\', '/'))
    if changed:
        for relative in changed:
            target = destination_root / Path(relative)
            if target.exists():
                target.unlink()
        receipt['copied_files'] = [row for row in receipt['copied_files'] if row['relative_path'] not in changed]
        receipt['skipped_files'].extend({'relative_path': relative, 'reason': 'PROFILE_SEED_SOURCE_CHANGED'} for relative in changed)
    receipt['copied_file_count'] = len(receipt['copied_files'])
    receipt['status'] = ('SEEDED' if receipt['copied_file_count'] and not receipt['skipped_files']
        else 'PARTIAL' if receipt['copied_file_count'] else 'NO_COMPATIBLE_SETTINGS')
    return receipt


def _regular_file_inside(root, relative):
    """Return an exact allowlisted regular file without following file reparse points."""
    candidate = root / Path(relative)
    try:
        info = candidate.lstat()
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None
    if (not stat.S_ISREG(info.st_mode) or candidate.is_symlink()
            or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or not resolved.is_relative_to(root)):
        return None
    return resolved


def _first_release_file(root, relative_paths):
    return next((found for relative in relative_paths
                 if (found := _regular_file_inside(root, relative)) is not None), None)


def _validated_release_identity(value):
    if not isinstance(value, dict) or value.get('schema_version') not in RELEASE_IDENTITY_SCHEMAS:
        raise ValueError('BUILD_IDENTITY_INVALID')
    release_version = value.get('release_version')
    display_version = value.get('display_version')
    if (not isinstance(release_version, str) or re.fullmatch(r'[1-9]\d*\.\d{2}', release_version) is None
            or display_version != f'v{release_version}'
            or value.get('main_executable') != 'Memorive.exe'
            or not isinstance(value.get('channel'), str) or not value['channel'].strip()
            or type(value.get('release_authorized')) is not bool
            or value.get('acceptance_verdict') not in {'PASS', 'FAIL', 'NOT_ASSESSED'}):
        raise ValueError('BUILD_IDENTITY_INVALID')
    inventory = value.get('first_party_exe_inventory')
    if inventory is not None and (not isinstance(inventory, list) or 'Memorive.exe' not in inventory):
        raise ValueError('BUILD_IDENTITY_INVALID')
    build_id = value.get('build_id')
    if build_id is not None and (not isinstance(build_id, str) or not build_id.strip()):
        raise ValueError('BUILD_IDENTITY_INVALID')
    return value


def build_info(path):
    original = Path(path)
    try:
        info = original.lstat()
        path = original.resolve(strict=True)
    except OSError as error:
        raise ValueError('BUILD_EXECUTABLE_REQUIRED') from error
    if (not stat.S_ISREG(info.st_mode) or original.is_symlink()
            or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or path.name.lower() != 'memorive.exe'):
        raise ValueError('BUILD_EXECUTABLE_REQUIRED')
    root = path.parent.resolve(strict=True)
    identity_path = _first_release_file(root, RELEASE_IDENTITY_RELATIVE_PATHS)
    if identity_path is None:
        raise ValueError('BUILD_IDENTITY_REQUIRED')
    try:
        identity = _validated_release_identity(read_json(identity_path))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError('BUILD_IDENTITY_INVALID') from error
    manifest_path = _first_release_file(root, CAPABILITY_RELATIVE_PATHS)
    manifest = {}
    if manifest_path is not None:
        try:
            manifest = read_json(manifest_path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError('BUILD_CAPABILITIES_INVALID') from error
        if not isinstance(manifest, dict):
            raise ValueError('BUILD_CAPABILITIES_INVALID')
    capability_build_id = manifest.get('build_id') if isinstance(manifest.get('build_id'), str) else None
    release_build_id = identity.get('build_id')
    warnings = []
    if release_build_id and capability_build_id and release_build_id != capability_build_id:
        warnings.append('CAPABILITY_BUILD_ID_MISMATCH')
    supported = (manifest.get('schema_version') in CAPABILITY_SCHEMAS
        and manifest.get('protocol_version') == PROTOCOL
        and manifest.get('launch_transport') == 'ENV_V1'
        and manifest.get('session_mode') == 'EPHEMERAL_ONLY')
    return {
        'path': str(path),
        'product_name': 'Memorive',
        'label': identity['display_version'],
        'identity_verified': True,
        'identity_path': str(identity_path),
        'bridge_declared': supported,
        'manifest_path': str(manifest_path) if manifest_path is not None else None,
        'protocol_version': manifest.get('protocol_version'),
        'build_id': release_build_id,
        'capability_build_id': capability_build_id,
        'release_version': identity['release_version'],
        'display_version': identity['display_version'],
        'channel': identity['channel'],
        'arch': identity.get('arch'),
        'release_authorized': identity['release_authorized'],
        'acceptance_verdict': identity['acceptance_verdict'],
        'compatibility_warnings': warnings,
        'status': 'READY_TO_CONNECT' if supported else 'WAITING_FOR_VERSION_INTERFACE',
    }


def discover_builds():
    """Discover only co-packaged Memorive releases; drag-and-drop handles other locations."""
    application_root = (Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False)
        else Path(__file__).resolve().parent)
    candidates = (
        application_root / 'Memorive.exe',
        application_root / 'Memorive' / 'Memorive.exe',
        application_root.parent / 'Memorive.exe',
        application_root.parent / 'Memorive' / 'Memorive.exe',
    )
    rows = []
    for path in dict.fromkeys(candidates):
        try:
            rows.append(build_info(path))
        except ValueError:
            continue
    return sorted(rows, key=lambda row: (row['release_version'], row['path']), reverse=True)


class Manager:
    def __init__(self, root):
        self.root = Path(root).resolve()
        for name in ['sessions', 'owned_sessions', 'receipts', 'monitor']:
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.lifecycle_lock = threading.RLock()
        self.cancel_start = threading.Event()
        self.stop = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='console-command')
        self.suite_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='console-suite')
        self.owner = self.client = self.session = None
        self.state = 'WAITING_FOR_BUILD'
        self.error = None
        self.cleanup_error = None
        self.commands = {}
        self.events = []
        self.native = {}
        self.console_access_required = False
        self.console_access_enabled = False
        self.expression_catalog = {}
        self.expression_state = {}
        self.last_cleanup = None
        self.capabilities = []
        self.test_attachment = build_test_attachment()
        self.suites = {}
        self.active_suite_id = None
        self.latest_review_bundle = None
        self.event_cursor = 0
        self.epoch = 0
        self._next_bridge_probe_at = 0.0
        self._next_snapshot_probe_at = 0.0
        self._next_expression_probe_at = 0.0
        self._next_event_poll_at = 0.0
        self.connection_monitor = {}
        self.native_projection_monitor = {}
        self.expression_monitor = {}
        self._reset_connection_monitor('NOT_CONNECTED')
        self._reset_native_projection_monitor('NOT_CONNECTED')
        self._reset_expression_monitor('NOT_CONNECTED')
        self.recover()
        self.thread = threading.Thread(target=self._poll, daemon=True)
        self.thread.start()

    def recover(self):
        for path in (self.root / 'owned_sessions').glob('review-*.json'):
            row = read_json(path)
            if row.get('status') == 'CLEANED':
                continue
            if process_identity(int(row['owner_pid'])) == row.get('owner_identity'):
                raise RuntimeError('ANOTHER_CONSOLE_SESSION_IS_ACTIVE')
            try:
                self.last_cleanup = delete_session(self.root, row['session_id'])
            except Exception as error:
                self.cleanup_error = cleanup_failure(error)
                self.state, self.error = 'CLEANUP_FAILED', self.cleanup_error['error_code']
                write_json(self.root / 'receipts' / path.name, {'session_id': row['session_id'], 'batch_id': row['session_id'],
                    'status': 'CLEANUP_FAILED', 'trigger': 'STARTUP_RECOVERY', 'completed_at': now(),
                    **self.cleanup_error, 'remaining_data_not_claimed_clean': True})
                raise RuntimeError('PREVIOUS_SESSION_CLEANUP_REQUIRED') from error

    def snapshot(self):
        with self.lock:
            business_ready = bool(self.session) and (
                not self.console_access_required or self.console_access_enabled
            )
            return deepcopy({'schema_version': 'Memorive-IndependentConsoleSnapshot-v1', 'console_root': str(self.root), 'observed_at': now(),
                'state': self.state, 'error_code': self.error, 'session': self.session,
                'connected_to_memorive': self.state in {'CONNECTED', 'WAITING_FOR_ACCESS'},
                'reference_only': bool(self.session and self.session['mode'] == 'reference'), 'supported_operations': self.capabilities,
                'console_access_required': self.console_access_required,
                'console_access_enabled': self.console_access_enabled,
                'business_operations_enabled': business_ready,
                'automatic_execution': False, 'commands': list(self.commands.values()), 'events': self.events[-200:],
                'native': self.native, 'test_attachment': self.test_attachment,
                'suite_catalog': build_suite_catalog(self.capabilities, business_ready),
                'suites': list(self.suites.values()), 'active_suite_id': self.active_suite_id,
                'latest_review_bundle': self.latest_review_bundle,
                'expression_catalog': self.expression_catalog,
                'expression_state': self.expression_state,
                'expression_monitor': self.expression_monitor,
                'last_cleanup': self.last_cleanup, 'cleanup_error': self.cleanup_error,
                'connection_monitor': self.connection_monitor,
                'native_projection_monitor': self.native_projection_monitor,
                'monitor_read_only': True})

    @staticmethod
    def _error_code(error):
        rendered = str(error)
        return rendered if __import__('re').fullmatch(r'[A-Z][A-Z0-9_]{0,100}', rendered) else type(error).__name__

    def _reset_connection_monitor(self, health='NOT_CONNECTED'):
        with self.lock:
            self.connection_monitor = {
                'schema_version': 'Memorive-ConsoleConnectionMonitor-v1',
                'health': health,
                'consecutive_failures': 0,
                'consecutive_successes': 0,
                'disconnect_failure_threshold': DISCONNECT_FAILURE_THRESHOLD,
                'recovery_success_threshold': RECOVERY_SUCCESS_THRESHOLD,
                'last_success_at': None,
                'last_failure_at': None,
                'last_failure_error': None,
                'last_transition_at': now(),
                'session_probe_route': '/events',
                'owned_process_alive': None,
                'network_backoff_seconds': 0.0,
            }
            self._next_bridge_probe_at = 0.0

    def _reset_native_projection_monitor(self, health='NOT_CONNECTED'):
        with self.lock:
            self.native_projection_monitor = {
                'schema_version': 'Memorive-ConsoleNativeProjectionMonitor-v1',
                'health': health,
                'consecutive_failures': 0,
                'failure_count': 0,
                'last_success_at': None,
                'last_failure_at': None,
                'last_failure_error': None,
                'last_transition_at': now(),
                'stale_since': None,
                'source_observed_at': None,
                'probe_interval_seconds': SNAPSHOT_INTERVAL_SECONDS,
                'probe_backoff_seconds': 0.0,
                'probe_deferred': False,
                'probe_deferred_reason': None,
            }
            self._next_snapshot_probe_at = 0.0

    def _reset_expression_monitor(self, health='NOT_CONNECTED'):
        with self.lock:
            self.expression_monitor = {
                'schema_version': 'Memorive-ConsoleExpressionMonitor-v1',
                'health': health,
                'consecutive_failures': 0,
                'last_success_at': None,
                'last_failure_at': None,
                'last_failure_error': None,
                'last_transition_at': now(),
                'poll_interval_ms': int(EXPRESSION_POLL_INTERVAL_SECONDS * 1000),
                'poll_backoff_seconds': 0.0,
                'route': '/expressions/state',
                'one_request_in_flight': True,
            }
            self._next_expression_probe_at = 0.0

    @staticmethod
    def _validated_expression_catalog(value):
        if not isinstance(value, dict) or value.get('schema_version') != 'Memorive-ExpressionConsole-v1':
            raise ValueError('EXPRESSION_CATALOG_INVALID')
        events = value.get('events')
        if not isinstance(events, list) or not events:
            raise ValueError('EXPRESSION_CATALOG_INVALID')
        identifiers = []
        for row in events:
            event_id = row.get('event_id') if isinstance(row, dict) else None
            if (event_id not in EXPRESSION_EVENT_IDS or row.get('group') not in {
                'static_asset', 'native_pose', 'native_animation', 'web_animation'
            } or type(row.get('animated')) is not bool or not isinstance(row.get('motion_ms'), int)):
                raise ValueError('EXPRESSION_CATALOG_INVALID')
            identifiers.append(event_id)
        if len(set(identifiers)) != len(identifiers):
            raise ValueError('EXPRESSION_CATALOG_INVALID')
        if (value.get('one_probe_at_a_time') is not True
                or value.get('model_calls_allowed') is not False
                or value.get('business_event_injected') is not False
                or value.get('poll_interval_ms') != 250):
            raise ValueError('EXPRESSION_CATALOG_SAFETY_INVALID')
        return deepcopy(value)

    @staticmethod
    def _validated_expression_state(value):
        if not isinstance(value, dict) or value.get('schema_version') != 'Memorive-ExpressionConsole-v1':
            raise ValueError('EXPRESSION_STATE_INVALID')
        if type(value.get('access_enabled')) is not bool:
            raise ValueError('EXPRESSION_ACCESS_STATE_INVALID')
        if value.get('active') is not None and not isinstance(value.get('active'), dict):
            raise ValueError('EXPRESSION_STATE_INVALID')
        if not isinstance(value.get('history'), list) or not isinstance(value.get('events'), list):
            raise ValueError('EXPRESSION_STATE_INVALID')
        if value.get('preview_only') is not True or value.get('business_event_injected') is not False:
            raise ValueError('EXPRESSION_STATE_SAFETY_INVALID')
        return deepcopy(value)

    def _set_console_access_locked(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('EXPRESSION_ACCESS_STATE_INVALID')
        changed = self.console_access_enabled != enabled
        self.console_access_enabled = enabled
        if not self.session or self.session.get('mode') == 'reference' or not self.console_access_required:
            return changed
        if enabled:
            if self.state == 'WAITING_FOR_ACCESS':
                self.state = 'CONNECTED'
                self.error = None
            self._next_snapshot_probe_at = 0.0
            self.test_attachment = build_test_attachment(self.capabilities, connected=True)
        else:
            if self.state in {'CONNECTED', 'WAITING_FOR_ACCESS'}:
                self.state = 'WAITING_FOR_ACCESS'
                self.error = None
            if self.active_suite_id and self.active_suite_id in self.suites:
                self.suites[self.active_suite_id]['cancel_requested'] = True
            self.native_projection_monitor['health'] = 'WAITING_FOR_ACCESS'
            self.native_projection_monitor['consecutive_failures'] = 0
            self.native_projection_monitor['probe_backoff_seconds'] = 0.0
            self.native_projection_monitor['probe_deferred'] = True
            self.native_projection_monitor['probe_deferred_reason'] = 'CONSOLE_ACCESS_DISABLED'
            self._next_snapshot_probe_at = time.monotonic() + SNAPSHOT_INTERVAL_SECONDS
            self.test_attachment = build_test_attachment(self.capabilities, connected=True)
            self.test_attachment['status'] = 'WAITING_FOR_ACCESS'
            self.test_attachment['execution_enabled'] = False
        return changed

    def _apply_expression_state(self, value):
        state = self._validated_expression_state(value)
        with self.lock:
            self.expression_state = state
            self._set_console_access_locked(state['access_enabled'])
            monitor = self.expression_monitor
            previous = monitor['health']
            monitor['health'] = 'CURRENT'
            monitor['consecutive_failures'] = 0
            monitor['last_success_at'] = now()
            monitor['last_failure_error'] = None
            monitor['poll_backoff_seconds'] = 0.0
            if previous != 'CURRENT':
                monitor['last_transition_at'] = now()
            self._next_expression_probe_at = time.monotonic() + EXPRESSION_POLL_INTERVAL_SECONDS
        return state

    def _poll_expression_once(self):
        with self.lock:
            epoch, client, state = self.epoch, self.client, self.state
            supported = 'expression.trigger' in self.capabilities and 'expression.cancel' in self.capabilities
            due = time.monotonic() >= self._next_expression_probe_at
        if not client or not supported or state not in {
            'CONNECTED', 'WAITING_FOR_ACCESS', 'REFERENCE_ONLY', 'DISCONNECTED'
        } or not due:
            return False
        try:
            value = client.expression_state()
            if epoch != self.epoch:
                return False
            self._apply_expression_state(value)
            return True
        except Exception as error:
            with self.lock:
                if epoch != self.epoch:
                    return False
                monitor = self.expression_monitor
                previous = monitor['health']
                monitor['health'] = 'STALE'
                monitor['consecutive_failures'] += 1
                monitor['last_failure_at'] = now()
                monitor['last_failure_error'] = self._error_code(error)
                delay = min(EXPRESSION_BACKOFF_MAX_SECONDS, .25 * (2 ** min(monitor['consecutive_failures'], 4)))
                monitor['poll_backoff_seconds'] = delay
                self._next_expression_probe_at = time.monotonic() + delay
                if previous != 'STALE':
                    monitor['last_transition_at'] = now()
            return False

    def expression_asset(self, asset_id):
        if not isinstance(asset_id, str) or __import__('re').fullmatch(r'[a-z][a-z0-9_-]{0,63}', asset_id) is None:
            raise ValueError('EXPRESSION_ASSET_NOT_ALLOWED')
        with self.lock:
            session = deepcopy(self.session)
            events = deepcopy(self.expression_catalog.get('events', []))
        allowed = {row.get('asset') for row in events if isinstance(row, dict) and isinstance(row.get('asset'), str)}
        if asset_id not in allowed or not session or session.get('mode') != 'build':
            raise ValueError('EXPRESSION_ASSET_NOT_ALLOWED')
        root = (Path(session['build']['path']).resolve().parent / '_internal' / 'assets' / 'illustrations').resolve(strict=True)
        path = (root / (asset_id + '.svg')).resolve(strict=True)
        if path.parent != root or path.stat().st_size > 512 * 1024:
            raise ValueError('EXPRESSION_ASSET_NOT_ALLOWED')
        return path.read_bytes(), 'image/svg+xml'

    def _mark_native_projection_success(self, value):
        with self.lock:
            monitor = self.native_projection_monitor
            previous_health = monitor['health']
            monitor['health'] = 'CURRENT'
            monitor['consecutive_failures'] = 0
            monitor['last_success_at'] = now()
            monitor['stale_since'] = None
            monitor['source_observed_at'] = value.get('observed_at')
            monitor['probe_backoff_seconds'] = 0.0
            monitor['probe_deferred'] = False
            monitor['probe_deferred_reason'] = None
            self._next_snapshot_probe_at = time.monotonic() + SNAPSHOT_INTERVAL_SECONDS
            if previous_health != 'CURRENT':
                monitor['last_transition_at'] = now()

    def _mark_native_projection_failure(self, error):
        with self.lock:
            monitor = self.native_projection_monitor
            previous_health = monitor['health']
            occurred_at = now()
            monitor['health'] = 'STALE'
            monitor['consecutive_failures'] += 1
            monitor['failure_count'] += 1
            monitor['last_failure_at'] = occurred_at
            monitor['last_failure_error'] = self._error_code(error)
            if previous_health != 'STALE':
                monitor['stale_since'] = occurred_at
                monitor['last_transition_at'] = occurred_at

    def _mark_connection_ready(self):
        with self.lock:
            self._reset_connection_monitor('STABLE')
            self.connection_monitor['consecutive_successes'] = 1
            self.connection_monitor['last_success_at'] = now()
            self.connection_monitor['owned_process_alive'] = True

    def _mark_poll_success(self):
        with self.lock:
            monitor = self.connection_monitor
            previous_health = monitor['health']
            monitor['consecutive_failures'] = 0
            monitor['consecutive_successes'] = min(
                RECOVERY_SUCCESS_THRESHOLD,
                monitor['consecutive_successes'] + 1,
            )
            monitor['last_success_at'] = now()
            monitor['owned_process_alive'] = True
            monitor['network_backoff_seconds'] = 0.0
            self._next_bridge_probe_at = 0.0
            if self.state == 'DISCONNECTED':
                if monitor['consecutive_successes'] < RECOVERY_SUCCESS_THRESHOLD:
                    if previous_health != 'RECOVERING':
                        monitor['last_transition_at'] = now()
                    monitor['health'] = 'RECOVERING'
                    return
                self.state = ('REFERENCE_ONLY' if self.session['mode'] == 'reference' else
                    'WAITING_FOR_ACCESS' if self.console_access_required and not self.console_access_enabled else 'CONNECTED')
                self.error = None
                monitor['health'] = 'STABLE'
                monitor['last_transition_at'] = now()
                return
            if previous_health == 'DEGRADED' and monitor['consecutive_successes'] < RECOVERY_SUCCESS_THRESHOLD:
                monitor['health'] = 'DEGRADED'
                return
            if previous_health != 'STABLE':
                monitor['last_transition_at'] = now()
            monitor['health'] = 'STABLE'
            self.error = None

    def _mark_poll_failure(self, error, owned_process_alive=None):
        with self.lock:
            monitor = self.connection_monitor
            code = self._error_code(error)
            previous_health = monitor['health']
            monitor['consecutive_successes'] = 0
            monitor['consecutive_failures'] += 1
            monitor['last_failure_at'] = now()
            monitor['last_failure_error'] = code
            monitor['owned_process_alive'] = owned_process_alive
            if owned_process_alive is True:
                # A route timeout is ambiguous on local-release because /snapshot,
                # /events and /session/end share the product bridge lock.  The
                # Job-owned process handle is independent evidence that the
                # launched build still exists, so retain the session and expose
                # degraded transport instead of claiming a process disconnect.
                monitor['health'] = 'DEGRADED'
                self.error = None
                delay = min(BRIDGE_BACKOFF_MAX_SECONDS, float(2 ** min(monitor['consecutive_failures'], 3)))
                monitor['network_backoff_seconds'] = delay
                self._next_bridge_probe_at = time.monotonic() + delay
            elif self.state == 'DISCONNECTED' or monitor['consecutive_failures'] >= DISCONNECT_FAILURE_THRESHOLD:
                self.state = 'DISCONNECTED'
                self.error = code
                monitor['health'] = 'DISCONNECTED'
            else:
                monitor['health'] = 'DEGRADED'
                self.error = None
            if monitor['health'] != previous_health:
                monitor['last_transition_at'] = now()

    @staticmethod
    def _owned_process_alive(owner):
        if owner is None:
            return None
        try:
            return owner.poll() is None
        except Exception:
            return None

    def publish(self):
        # Snapshot and replace are one serialized publication.  This keeps the
        # poll, command, suite and lifecycle paths from replacing the same
        # monitor target concurrently.
        with self.lock:
            write_json(self.root / 'monitor' / 'latest_snapshot.json', self.snapshot())

    def _await_product_runtime(self, client, mode, deadline):
        while True:
            if self.cancel_start.is_set():
                raise RuntimeError('START_CANCELLED')
            if self.owner and self.owner.poll() is not None:
                raise RuntimeError('BRIDGE_PROCESS_EXITED_BEFORE_READY')
            if time.monotonic() >= deadline:
                raise RuntimeError('PRODUCT_RUNTIME_READY_TIMEOUT')
            try:
                value = client.snapshot()
            except OSError:
                time.sleep(.1)
                continue
            runtime_state = value.get('product_runtime_state')
            if mode == 'build' and runtime_state == 'INITIALIZING':
                time.sleep(.1)
                continue
            if mode == 'build' and runtime_state not in {None, 'READY'}:
                raise RuntimeError('PRODUCT_RUNTIME_NOT_READY')
            break
        if value.get('automatic_execution') is not False or value.get('automatic_execution_locked') is not True:
            raise RuntimeError('AUTOMATIC_EXECUTION_GUARD_LOST')
        return value

    def start(self, mode, build_path=None, timeout=300):
        if mode not in {'build', 'reference'}:
            raise ValueError('SESSION_MODE_INVALID')
        with self.lifecycle_lock:
            with self.lock:
                if self.session or self.state == 'CLEANUP_FAILED':
                    raise ValueError('END_CURRENT_SESSION_FIRST')
                reference_path = (Path(sys.executable).resolve() if getattr(sys, 'frozen', False)
                    else Path(__file__).resolve().parent / 'reference_bridge.py')
                info = build_info(build_path) if mode == 'build' else {'path': str(reference_path), 'label': '接口参考端', 'bridge_declared': True}
                if not info['bridge_declared']:
                    raise ValueError('BUILD_BRIDGE_NOT_IMPLEMENTED')
                self.cancel_start.clear()
                self.epoch += 1
                self.state, self.error = 'CONNECTING', None
                self._reset_connection_monitor('CONNECTING')
                self._reset_native_projection_monitor('CONNECTING')
                self._reset_expression_monitor('CONNECTING')
                self.cleanup_error = None
                self.commands, self.events, self.native = {}, [], {}
                self.expression_catalog, self.expression_state = {}, {}
                self.console_access_required = False
                self.console_access_enabled = False
                self.capabilities, self.event_cursor = [], 0
                self.test_attachment = build_test_attachment()
                self.suites, self.active_suite_id = {}, None
                self.latest_review_bundle = None
                session_id = 'review-' + datetime.now().strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:12]
                data_root = self.root / 'sessions' / session_id
                data_root.mkdir(exist_ok=False)
                write_json(data_root / '.console-session.json', {'owner': 'MEMORIVE_INDEPENDENT_CONSOLE', 'session_id': session_id})
                profile_seed = (seed_local_profile(data_root) if mode == 'build' else {
                    'schema_version': 'Memorive-ConsoleLocalProfileSeed-v1',
                    'status': 'NOT_APPLICABLE', 'copied_file_count': 0,
                    'copied_files': [], 'skipped_files': [],
                    'automatic_execution': False, 'source_profile_mutated': False,
                })
                sha = digest(info['path'])
                self.session = {'session_id': session_id, 'mode': mode, 'build': info, 'build_sha256': sha,
                    'data_root': str(data_root), 'started_at': now(), 'disposition': 'DISCARD_ON_CLOSE',
                    'local_profile_seed': profile_seed}
                registry = {**self.session, 'owner_pid': os.getpid(), 'owner_identity': process_identity(os.getpid()), 'status': 'OWNED'}
                write_json(self.root / 'owned_sessions' / (session_id + '.json'), registry)
                self.publish()
            try:
                token = secrets.token_hex(32)
                descriptor_path = data_root / 'bridge-descriptor.json'
                allowed = {'SYSTEMROOT', 'WINDIR', 'PATH', 'PATHEXT', 'COMSPEC', 'SYSTEMDRIVE', 'PROGRAMFILES', 'PROGRAMFILES(X86)',
                    'COMMONPROGRAMFILES', 'COMMONPROGRAMFILES(X86)', 'PROGRAMDATA', 'PUBLIC', 'PROCESSOR_ARCHITECTURE', 'NUMBER_OF_PROCESSORS'}
                environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
                for name in ['localappdata', 'appdata', 'temp']:
                    (data_root / name).mkdir()
                environment.update(LOCALAPPDATA=str(data_root / 'localappdata'), APPDATA=str(data_root / 'appdata'), TEMP=str(data_root / 'temp'), TMP=str(data_root / 'temp'),
                    PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1', MEMORIVE_TEST_CONSOLE_PROTOCOL=PROTOCOL,
                    MEMORIVE_TEST_CONSOLE_SESSION_ID=session_id, MEMORIVE_TEST_CONSOLE_DATA_ROOT=str(data_root),
                    MEMORIVE_TEST_CONSOLE_DESCRIPTOR_PATH=str(descriptor_path), MEMORIVE_TEST_CONSOLE_TOKEN=token,
                    MEMORIVE_TEST_CONSOLE_BUILD_SHA256=sha, MEMORIVE_TEST_CONSOLE_AUTO_EXECUTION='DISABLED')
                if mode == 'reference' and getattr(sys, 'frozen', False):
                    # The reference bridge is another mode of this same one-file application.
                    # Retain only PyInstaller's documented private process-level handshake so
                    # the child reuses the live extraction and does not start a competing copy.
                    for key in ('_PYI_ARCHIVE_FILE', '_PYI_APPLICATION_HOME_DIR',
                            '_PYI_PARENT_PROCESS_LEVEL', '_PYI_SPLASH_IPC'):
                        if key in os.environ:
                            environment[key] = os.environ[key]
                if mode == 'build':
                    command = [info['path']]
                elif getattr(sys, 'frozen', False):
                    command = [sys.executable, '--reference-bridge']
                else:
                    command = [str(Path(sys.executable).with_name('pythonw.exe')), str(reference_path)]
                self.owner = OwnedProcess(command, environment, data_root)
                self.session['pid'] = self.owner.pid
                deadline = time.monotonic() + timeout
                while not descriptor_path.exists():
                    if self.cancel_start.is_set():
                        raise RuntimeError('START_CANCELLED')
                    if self.owner.poll() is not None:
                        raise RuntimeError('BRIDGE_PROCESS_EXITED_BEFORE_READY')
                    if time.monotonic() >= deadline:
                        raise RuntimeError('BRIDGE_START_TIMEOUT')
                    time.sleep(.1)
                self.client = BridgeClient(read_json(descriptor_path), token, session_id, sha, 'MEMORIVE_BUILD' if mode == 'build' else 'REFERENCE_BRIDGE')
                handshake = self.client.handshake(data_root)
                if self.cancel_start.is_set():
                    raise RuntimeError('START_CANCELLED')
                capabilities = handshake['operations']
                expression_catalog = {}
                expression_state = {}
                if {'expression.trigger', 'expression.cancel'}.issubset(capabilities):
                    expression_catalog = self._validated_expression_catalog(self.client.expression_catalog())
                    expression_state = self._validated_expression_state(self.client.expression_state())
                access_required = handshake['console_access_required']
                access_enabled = handshake['console_access_enabled']
                if expression_state:
                    access_enabled = expression_state['access_enabled']
                native = {}
                if not access_required or access_enabled:
                    native = self._await_product_runtime(self.client, mode, deadline)
                with self.lock:
                    self.capabilities = capabilities
                    self.console_access_required = access_required
                    self.console_access_enabled = access_enabled
                    self.expression_catalog = expression_catalog
                    self.expression_state = expression_state
                    self.test_attachment = build_test_attachment(self.capabilities, connected=True)
                    self.session['handshake'] = {key: handshake[key] for key in [
                        'protocol_version', 'implementation_kind', 'automatic_execution_locked',
                        'all_writes_inside_data_root', 'cleanup_supported',
                        'console_access_required', 'console_access_enabled'
                    ]}
                    self.state = ('REFERENCE_ONLY' if mode == 'reference' else
                        'WAITING_FOR_ACCESS' if access_required and not access_enabled else 'CONNECTED')
                    self.native = native
                    if native and (self.native.get('automatic_execution') is not False or self.native.get('automatic_execution_locked') is not True):
                        raise RuntimeError('AUTOMATIC_EXECUTION_GUARD_LOST')
                    self._mark_connection_ready()
                    if expression_state:
                        self.expression_monitor['health'] = 'CURRENT'
                        self.expression_monitor['last_success_at'] = now()
                        self._next_expression_probe_at = time.monotonic() + EXPRESSION_POLL_INTERVAL_SECONDS
                    else:
                        self._reset_expression_monitor('UNSUPPORTED')
                    if native:
                        self._mark_native_projection_success(native)
                    elif access_required and not access_enabled:
                        self._set_console_access_locked(False)
                    self.publish()
                return self.snapshot()
            except Exception as error:
                code = str(error) if __import__('re').fullmatch(r'[A-Z][A-Z0-9_]{0,100}', str(error)) else type(error).__name__
                self.end('START_FAILED')
                self.error = code
                self.publish()
                raise RuntimeError(code) from None

    def submit(self, value, suite_id=None):
        validate_command(value)
        with self.lock:
            if self.state not in {'CONNECTED', 'REFERENCE_ONLY', 'WAITING_FOR_ACCESS'}:
                raise ValueError('BUILD_NOT_CONNECTED')
            if self.state == 'WAITING_FOR_ACCESS' and value['operation'] not in {'diagnostics', 'expression.cancel'}:
                raise ValueError('CONSOLE_ACCESS_DISABLED')
            if self.active_suite_id and suite_id != self.active_suite_id:
                raise ValueError('TEST_SUITE_RUNNING')
            if value['operation'] not in self.capabilities:
                raise ValueError('OPERATION_NOT_SUPPORTED_BY_BUILD')
            request_id = value['request_id']
            fingerprint = hashlib.sha256(encode(value)).hexdigest()
            if request_id in self.commands:
                previous = self.commands[request_id]
                if previous['request_sha256'] != fingerprint:
                    raise ValueError('IDEMPOTENCY_CONFLICT')
                return deepcopy({**previous, 'replayed': True})
            execution_value = deepcopy(value)
            import_staging = None
            if value['operation'] == 'inbox.import':
                execution_value, import_staging = self._stage_import(execution_value)
            record = {'request_id': request_id, 'command_id': 'console-' + uuid.uuid4().hex, 'operation': value['operation'],
                'state': 'QUEUED', 'request_sha256': fingerprint, 'created_at': now(), 'session_id': self.session['session_id'], 'reference_only': self.session['mode'] == 'reference'}
            if import_staging is not None:
                record['import_staging'] = import_staging
            self.commands[request_id] = record
            self.pool.submit(self._command, execution_value, self.epoch, self.client)
            return deepcopy(record)

    @staticmethod
    def _suite_identifiers(value):
        allowed = {'message_id', 'job_id', 'item_id', 'report_id', 'artifact_id', 'run_id'}
        found = {}
        stack = [value]
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                for key, item in current.items():
                    if key in allowed and isinstance(item, str):
                        try:
                            found.setdefault(key, identifier(item))
                        except ValueError:
                            pass
                    elif isinstance(item, (dict, list)):
                        stack.append(item)
            elif isinstance(current, list):
                stack.extend(current)
        return found

    def _materialize_suite_case(self, suite_id, case, resolved):
        value = deepcopy(case)
        params = value.pop('params')
        for name, binding in value.pop('bindings', {}).items():
            source = resolved.get(binding['from_case'], {})
            if binding['identifier'] not in source:
                raise ValueError('DEPENDENCY_IDENTIFIER_NOT_FOUND')
            params[name] = source[binding['identifier']]
        fixture = value.pop('fixture', None)
        if fixture:
            if fixture != {'type': 'session_text', 'name': 'console-suite-import.txt'}:
                raise ValueError('TEST_SUITE_FIXTURE_INVALID')
            session_root = Path(self.session['data_root']).resolve(strict=True)
            fixture_root = self._ensure_owned_directory(session_root, ('console-suite-inputs', suite_id))
            fixture_path = fixture_root / fixture['name']
            fixture_path.write_text(
                'Memorive console one-click test fixture\n'
                f'suite_id={suite_id}\n'
                'automatic_execution=false\n',
                encoding='utf-8',
            )
            params['paths'] = [str(fixture_path)]
        return {
            'request_id': suite_id + '.' + case['case_id'],
            'operation': case['operation'],
            'params': params,
            'allow_model_calls': False,
        }

    def _wait_suite_command(self, suite_id, command_id, epoch, timeout=300):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                suite = self.suites.get(suite_id)
                if epoch != self.epoch or not suite or suite.get('cancel_requested'):
                    return None
                record = next((row for row in self.commands.values() if row['command_id'] == command_id), None)
                if record and record['state'] in {'SUCCEEDED', 'FAILED', 'CANCELLED'}:
                    return deepcopy(record)
            if self.stop.wait(.05):
                return None
        return {'command_id': command_id, 'state': 'FAILED', 'error_code': 'COMMAND_TIMEOUT'}

    def start_suite(self, value):
        if not isinstance(value, dict) or set(value) != {'preset_id'} or not isinstance(value['preset_id'], str):
            raise ValueError('TEST_SUITE_FIELDS_INVALID')
        plan = build_suite_plan(value['preset_id'])
        with self.lock:
            if self.state not in {'CONNECTED', 'REFERENCE_ONLY'}:
                raise ValueError('BUILD_NOT_CONNECTED')
            if self.active_suite_id:
                raise ValueError('TEST_SUITE_RUNNING')
            if any(case['allow_model_calls'] is not False or OPERATIONS[case['operation']]['model'] is not False for case in plan):
                raise RuntimeError('TEST_SUITE_MODEL_BOUNDARY_INVALID')
            suite_id = 'suite-' + datetime.now().strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:10]
            preset = next(row for row in build_suite_catalog(self.capabilities, connected=True) if row['preset_id'] == value['preset_id'])
            suite = {
                'schema_version': 'Memorive-ConsoleSuiteRun-v1',
                'suite_id': suite_id,
                'preset_id': value['preset_id'],
                'title': preset['title'],
                'state': 'QUEUED',
                'verdict': 'NOT_ASSESSED',
                'session_id': self.session['session_id'],
                'build_label': self.session['build']['label'],
                'build_sha256': self.session['build_sha256'],
                'created_at': now(),
                'automatic_execution': False,
                'model_calls_allowed': False,
                'progress': {'completed': 0, 'total': len(plan), 'current_case_id': None},
                'summary': {'total': len(plan), 'passed': 0, 'failed': 0, 'skipped': 0},
                'cases': [],
                'cancel_requested': False,
                'review_bundle': None,
                'cleanup': {'status': 'PENDING_SESSION_CLOSE'},
            }
            self.suites[suite_id] = suite
            self.active_suite_id = suite_id
            epoch = self.epoch
            self.publish()
            self.suite_pool.submit(self._run_suite, suite_id, plan, epoch)
            return deepcopy(suite)

    def cancel_suite(self, value):
        if not isinstance(value, dict) or set(value) != {'suite_id'}:
            raise ValueError('TEST_SUITE_CANCEL_FIELDS_INVALID')
        identifier(value['suite_id'])
        with self.lock:
            suite = self.suites.get(value['suite_id'])
            if not suite:
                raise ValueError('TEST_SUITE_NOT_FOUND')
            if suite['state'] not in {'QUEUED', 'RUNNING'}:
                return deepcopy(suite)
            suite['cancel_requested'] = True
            suite['state'] = 'CANCEL_REQUESTED'
            self.publish()
            return deepcopy(suite)

    @staticmethod
    def _suite_case_receipt(case, state, started, record=None, error_code=None):
        row = {
            'case_id': case['case_id'],
            'operation': case['operation'],
            'state': state,
            'duration_ms': max(0, round((time.monotonic() - started) * 1000)),
        }
        if record:
            row['command_id'] = record.get('command_id')
            if record.get('bridge_command_id'):
                row['bridge_command_id'] = record['bridge_command_id']
            value = record.get('result', {'state': record.get('state'), 'error_code': record.get('error_code')})
            row['result_sha256'] = hashlib.sha256(encode(value)).hexdigest()
            identifiers = Manager._suite_identifiers(value)
            if identifiers:
                row['identifiers'] = identifiers
        if error_code:
            row['error_code'] = error_code
        return row

    def _cancel_remaining_suite_cases(self, suite, plan):
        finished = {case['case_id'] for case in suite['cases']}
        for case in plan:
            if case['case_id'] not in finished:
                suite['cases'].append({
                    'case_id': case['case_id'], 'operation': case['operation'],
                    'state': 'SKIPPED', 'duration_ms': 0, 'error_code': 'TEST_SUITE_CANCELLED',
                })

    def _run_suite(self, suite_id, plan, epoch):
        resolved = {}
        with self.lock:
            suite = self.suites.get(suite_id)
            if not suite or epoch != self.epoch:
                return
            suite['state'] = 'RUNNING'
            suite['started_at'] = now()
            self.publish()
        for case in plan:
            started = time.monotonic()
            with self.lock:
                suite = self.suites.get(suite_id)
                if not suite or epoch != self.epoch:
                    return
                if suite.get('cancel_requested'):
                    break
                suite['progress']['current_case_id'] = case['case_id']
                supported = case['operation'] in self.capabilities
                self.publish()
            if not supported:
                result = self._suite_case_receipt(case, 'SKIPPED', started, error_code='OPERATION_NOT_SUPPORTED_BY_BUILD')
            else:
                try:
                    command = self._materialize_suite_case(suite_id, case, resolved)
                    record = self.submit(command, suite_id=suite_id)
                    record = self._wait_suite_command(suite_id, record['command_id'], epoch)
                    if record is None:
                        break
                    if record['state'] == 'SUCCEEDED':
                        result = self._suite_case_receipt(case, 'PASSED', started, record)
                        resolved[case['case_id']] = result.get('identifiers', {})
                    else:
                        result = self._suite_case_receipt(
                            case, 'FAILED', started, record,
                            record.get('error_code') or 'COMMAND_' + record['state'],
                        )
                except Exception as error:
                    result = self._suite_case_receipt(case, 'SKIPPED', started, error_code=self._error_code(error))
            with self.lock:
                suite = self.suites.get(suite_id)
                if not suite or epoch != self.epoch:
                    return
                suite['cases'].append(result)
                suite['progress']['completed'] = len(suite['cases'])
                suite['summary']['passed'] += result['state'] == 'PASSED'
                suite['summary']['failed'] += result['state'] == 'FAILED'
                suite['summary']['skipped'] += result['state'] == 'SKIPPED'
                self.publish()
        with self.lock:
            suite = self.suites.get(suite_id)
            if not suite or epoch != self.epoch:
                return
            cancelled = suite.get('cancel_requested')
            if cancelled:
                self._cancel_remaining_suite_cases(suite, plan)
                suite['summary'] = {
                    'total': len(plan),
                    'passed': sum(row['state'] == 'PASSED' for row in suite['cases']),
                    'failed': sum(row['state'] == 'FAILED' for row in suite['cases']),
                    'skipped': sum(row['state'] == 'SKIPPED' for row in suite['cases']),
                }
                suite['state'], suite['verdict'] = 'CANCELLED', 'NOT_ASSESSED'
            else:
                suite['state'] = 'COMPLETED'
                suite['verdict'] = 'PASS' if not suite['summary']['failed'] and not suite['summary']['skipped'] else 'FAIL'
            suite['progress'] = {'completed': len(suite['cases']), 'total': len(plan), 'current_case_id': None}
            suite['completed_at'] = now()
            suite.pop('cancel_requested', None)
            self.active_suite_id = None
            self._write_suite_review(suite)
            self.publish()

    def _write_suite_review(self, suite):
        directory = self.root / 'receipts' / 'suites'
        json_path = directory / (suite['suite_id'] + '.json')
        markdown_path = directory / (suite['suite_id'] + '.md')
        receipt = {
            'schema_version': 'Memorive-ConsoleCodexReview-v1',
            'suite_id': suite['suite_id'],
            'preset_id': suite['preset_id'],
            'title': suite['title'],
            'state': suite['state'],
            'verdict': suite['verdict'],
            'build_label': suite['build_label'],
            'build_sha256': suite['build_sha256'],
            'session_id': suite['session_id'],
            'created_at': suite['created_at'],
            'completed_at': suite.get('completed_at'),
            'automatic_execution': False,
            'model_calls_allowed': False,
            'business_content_recorded': False,
            'summary': deepcopy(suite['summary']),
            'cases': deepcopy(suite['cases']),
            'cleanup': deepcopy(suite.get('cleanup', {'status': 'PENDING_SESSION_CLOSE'})),
        }
        lines = [
            '# Memorive 控制台 · Codex 审核包', '',
            f"- Suite: `{receipt['suite_id']}` / `{receipt['preset_id']}`",
            f"- Build: `{receipt['build_label']}` / `{receipt['build_sha256']}`",
            f"- Verdict: **{receipt['verdict']}**",
            f"- Cases: {receipt['summary']['passed']} PASS / {receipt['summary']['failed']} FAIL / {receipt['summary']['skipped']} SKIP / {receipt['summary']['total']} TOTAL",
            '- Model calls: `DISABLED`', '- Inbox automatic execution: `DISABLED`',
            f"- Cleanup: `{receipt['cleanup']['status']}`", '',
            '| Case | Operation | Result | Error | Result SHA256 |',
            '|---|---|---:|---|---|',
        ]
        for row in receipt['cases']:
            lines.append('| {case_id} | {operation} | {state} | {error} | {digest} |'.format(
                case_id=row['case_id'], operation=row['operation'], state=row['state'],
                error=row.get('error_code', ''), digest=row.get('result_sha256', ''),
            ))
        directory.mkdir(parents=True, exist_ok=True)
        write_json(json_path, receipt)
        markdown_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        write_json(directory / 'latest.json', receipt)
        suite['review_bundle'] = {'json_path': str(json_path), 'markdown_path': str(markdown_path)}
        self.latest_review_bundle = deepcopy(suite['review_bundle'])

    @staticmethod
    def _reject_reparse_chain(source):
        for path in [*reversed(source.parents), source]:
            try:
                info = path.lstat()
            except FileNotFoundError:
                if path == source:
                    raise ValueError('IMPORT_STAGE_SOURCE_NOT_FOUND') from None
                raise ValueError('IMPORT_STAGE_PARENT_NOT_FOUND') from None
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
                raise ValueError('IMPORT_STAGE_REPARSE_REJECTED')

    @staticmethod
    def _ensure_owned_directory(data_root, relative_parts):
        cursor = data_root
        for part in relative_parts:
            cursor = cursor / part
            if cursor.exists():
                info = cursor.lstat()
                if not stat.S_ISDIR(info.st_mode):
                    raise ValueError('IMPORT_STAGE_DESTINATION_NOT_DIRECTORY')
                if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
                    raise ValueError('IMPORT_STAGE_DESTINATION_REPARSE_REJECTED')
            else:
                cursor.mkdir()
        resolved = cursor.resolve(strict=True)
        if not resolved.is_relative_to(data_root):
            raise ValueError('IMPORT_STAGE_DESTINATION_ESCAPE')
        return resolved

    @staticmethod
    def _copy_import_source(source, target):
        raw = os.fspath(source)
        normalized = raw.replace('/', '\\')
        if normalized.startswith('\\\\?\\') or normalized.startswith('\\\\.\\'):
            raise ValueError('IMPORT_STAGE_DEVICE_PATH_REJECTED')
        try:
            source_info = source.lstat()
        except FileNotFoundError:
            raise ValueError('IMPORT_STAGE_SOURCE_NOT_FOUND') from None
        Manager._reject_reparse_chain(source)
        if not stat.S_ISREG(source_info.st_mode):
            raise ValueError('IMPORT_STAGE_FILE_REQUIRED')
        # The caller creates one exclusive slot per source file.  Keep the
        # temporary basename to one character so an otherwise valid final
        # path is never made longer during the atomic copy-and-replace step.
        # Exclusive creation still fails closed if that owned slot is touched.
        temporary = target.with_name('~' if target.name != '~' else '!')
        hasher = hashlib.sha256()
        size = 0
        try:
            with source.open('rb') as reader, temporary.open('xb') as writer:
                opened = os.fstat(reader.fileno())
                if (opened.st_dev, opened.st_ino) != (source_info.st_dev, source_info.st_ino):
                    raise ValueError('IMPORT_STAGE_SOURCE_CHANGED')
                while True:
                    chunk = reader.read(1024 * 1024)
                    if not chunk:
                        break
                    writer.write(chunk)
                    hasher.update(chunk)
                    size += len(chunk)
                writer.flush()
                os.fsync(writer.fileno())
                finished = os.fstat(reader.fileno())
                if (finished.st_size, finished.st_mtime_ns) != (opened.st_size, opened.st_mtime_ns):
                    raise ValueError('IMPORT_STAGE_SOURCE_CHANGED')
            os.replace(temporary, target)
        except BaseException:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return {'bytes': size, 'sha256': hasher.hexdigest()}

    @staticmethod
    def _remove_incomplete_stage(path):
        if not path.exists():
            return
        for child in sorted(path.rglob('*'), key=lambda item: len(item.parts), reverse=True):
            info = child.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
                raise ValueError('IMPORT_STAGE_CLEANUP_REPARSE_REJECTED')
            child.rmdir() if stat.S_ISDIR(info.st_mode) else child.unlink()
        path.rmdir()

    def _stage_import(self, value):
        data_root = Path(self.session['data_root']).resolve(strict=True)
        stage_container = self._ensure_owned_directory(
            data_root,
            ('state', 'profile', 'inbox_source', 'console-staged'),
        )
        batch_id = 'console-import-' + hashlib.sha256(value['request_id'].encode('utf-8')).hexdigest()[:16] + '-' + uuid.uuid4().hex[:8]
        temporary = stage_container / ('.' + batch_id + '.tmp')
        final = stage_container / batch_id
        temporary.mkdir(exist_ok=False)
        copied = []
        try:
            for index, raw in enumerate(value['params']['paths']):
                source = Path(raw)
                slot = temporary / f'{index:02d}'
                slot.mkdir()
                target = slot / source.name
                receipt = self._copy_import_source(source, target)
                copied.append({
                    'name': source.name,
                    'bytes': receipt['bytes'],
                    'sha256': receipt['sha256'],
                    'staged_relative_path': (final / f'{index:02d}' / source.name).relative_to(data_root).as_posix(),
                })
            os.replace(temporary, final)
        except BaseException:
            self._remove_incomplete_stage(temporary)
            raise
        execution = deepcopy(value)
        execution['params']['paths'] = [str(data_root / item['staged_relative_path']) for item in copied]
        staging = {
            'schema_version': 'Memorive-ConsoleImportStagingReceipt-v1',
            'status': 'STAGED',
            'batch_id': batch_id,
            'staged_root_relative': final.relative_to(data_root).as_posix(),
            'source_paths_recorded': False,
            'source_files_modified': False,
            'cleanup': 'SESSION_CLOSE',
            'files': copied,
        }
        return execution, staging

    def _command(self, value, epoch, client):
        with self.lock:
            waiting_allowed = value['operation'] in {'diagnostics', 'expression.cancel'}
            if epoch != self.epoch or self.state not in ({'CONNECTED', 'REFERENCE_ONLY', 'WAITING_FOR_ACCESS'} if waiting_allowed else {'CONNECTED', 'REFERENCE_ONLY'}):
                return
            record = self.commands[value['request_id']]
            record['state'] = 'RUNNING'
        try:
            result = client.command(value)
            if result.get('request_id') != value['request_id']:
                raise ValueError('COMMAND_REQUEST_ID_MISMATCH')
            while result.get('state') in {'QUEUED', 'RUNNING'}:
                if epoch != self.epoch or self.stop.wait(.25):
                    return
                result = client.read_command(result['command_id'])
            if result.get('state') not in {'SUCCEEDED', 'FAILED', 'CANCELLED'}:
                raise ValueError('COMMAND_RESULT_STATE_INVALID')
            if result.get('request_id') != value['request_id']:
                raise ValueError('COMMAND_REQUEST_ID_MISMATCH')
            with self.lock:
                if epoch != self.epoch:
                    return
                record.update(state=result['state'], bridge_command_id=result.get('command_id'), result=result, updated_at=now())
                native_error = result.get('error_code')
                if result['state'] == 'FAILED' and isinstance(native_error, str) and __import__('re').fullmatch(r'[A-Z][A-Z0-9_]{0,120}', native_error):
                    # Preserve the build-owned terminal error in the command and
                    # suite receipt.  It remains a failure and is never retried.
                    record['error_code'] = native_error
        except Exception as error:
            with self.lock:
                if epoch == self.epoch:
                    record.update(state='FAILED', error_code=str(error) if __import__('re').fullmatch(r'[A-Z][A-Z0-9_]{0,100}', str(error)) else type(error).__name__, updated_at=now())
        self.publish()

    def _poll_once(self):
        with self.lock:
            epoch, client, state, owner = self.epoch, self.client, self.state, self.owner
            next_bridge_probe_at = self._next_bridge_probe_at
        if not client or state not in {'CONNECTED', 'WAITING_FOR_ACCESS', 'REFERENCE_ONLY', 'DISCONNECTED'}:
            return False
        owned_process_alive = self._owned_process_alive(owner)
        if owned_process_alive is False:
            with self.lock:
                if epoch == self.epoch:
                    self._mark_poll_failure(RuntimeError('BRIDGE_PROCESS_EXITED'), False)
            return False
        if owned_process_alive is True and time.monotonic() < next_bridge_probe_at:
            return True

        # Probe the light route first.  local-release serializes every bridge route,
        # so sending /events after a timed-out /snapshot can create a queue that
        # also blocks native shutdown.
        entries = None
        cursor = self.event_cursor
        try:
            batch = client.events(cursor)
            entries = batch.get('events')
            if not isinstance(entries, list):
                raise ValueError('EVENTS_INVALID')
            for event in entries:
                if type(event.get('sequence')) is not int or event['sequence'] <= cursor:
                    raise ValueError('EVENT_SEQUENCE_NOT_MONOTONIC')
                cursor = event['sequence']
        except Exception as error:
            with self.lock:
                if epoch != self.epoch:
                    return False
                self._mark_native_projection_failure(error)
                self._mark_poll_failure(error, owned_process_alive)
            return False

        with self.lock:
            if epoch != self.epoch:
                return False
            self.events = (self.events + entries)[-200:]
            self.event_cursor = cursor
            self._mark_poll_success()
            if self.console_access_required and not self.console_access_enabled:
                self.native_projection_monitor['health'] = 'WAITING_FOR_ACCESS'
                self.native_projection_monitor['probe_deferred'] = True
                self.native_projection_monitor['probe_deferred_reason'] = 'CONSOLE_ACCESS_DISABLED'
                self._next_snapshot_probe_at = time.monotonic() + SNAPSHOT_INTERVAL_SECONDS
                return True
            business_work_active = bool(self.active_suite_id) or any(
                row.get('state') in {'QUEUED', 'RUNNING'} for row in self.commands.values()
            )
            if business_work_active:
                # local-release serializes command/status routes and /snapshot under
                # one server lock.  Keep the cheap liveness/event channel active,
                # but do not let a projection read delay a business receipt.
                self.native_projection_monitor['probe_deferred'] = True
                self.native_projection_monitor['probe_deferred_reason'] = 'ACTIVE_BUSINESS_WORK'
                self._next_snapshot_probe_at = time.monotonic() + SNAPSHOT_INTERVAL_SECONDS
                return True
            self.native_projection_monitor['probe_deferred'] = False
            self.native_projection_monitor['probe_deferred_reason'] = None
            snapshot_due = time.monotonic() >= self._next_snapshot_probe_at
        if not snapshot_due:
            return True

        value = None
        snapshot_error = None
        try:
            value = client.snapshot()
            if value.get('automatic_execution') is not False or value.get('automatic_execution_locked') is not True:
                raise RuntimeError('AUTOMATIC_EXECUTION_GUARD_LOST')
        except Exception as error:
            snapshot_error = error
        if snapshot_error is not None and str(snapshot_error) == 'AUTOMATIC_EXECUTION_GUARD_LOST':
            if epoch == self.epoch:
                try:
                    self.end('AUTOMATIC_EXECUTION_GUARD_LOST')
                except Exception:
                    pass
                with self.lock:
                    self.error = 'AUTOMATIC_EXECUTION_GUARD_LOST'
                    self.connection_monitor['last_failure_error'] = 'AUTOMATIC_EXECUTION_GUARD_LOST'
                    self.connection_monitor['last_failure_at'] = now()
            return False
        if snapshot_error is not None and str(snapshot_error) == 'CONSOLE_ACCESS_DISABLED':
            with self.lock:
                if epoch == self.epoch:
                    self._set_console_access_locked(False)
            return True
        with self.lock:
            if epoch != self.epoch:
                return False
            if value is not None:
                if type(value.get('console_access_enabled')) is bool:
                    self._set_console_access_locked(value['console_access_enabled'])
                self.native = value
                self._mark_native_projection_success(value)
            elif snapshot_error is not None:
                self._mark_native_projection_failure(snapshot_error)
                if owned_process_alive is True:
                    failures = self.native_projection_monitor['consecutive_failures']
                    delay = min(BRIDGE_BACKOFF_MAX_SECONDS, float(2 ** min(failures, 3)))
                    self.native_projection_monitor['probe_backoff_seconds'] = delay
                    self._next_snapshot_probe_at = time.monotonic() + delay
                    # Give a server handler that outlived the client timeout a
                    # chance to release local-release's shared route lock.
                    self.connection_monitor['network_backoff_seconds'] = delay
                    self._next_bridge_probe_at = time.monotonic() + delay
        return True

    def _poll(self):
        while not self.stop.wait(EXPRESSION_POLL_INTERVAL_SECONDS):
            self._poll_expression_once()
            if time.monotonic() >= self._next_event_poll_at:
                self._poll_once()
                self._next_event_poll_at = time.monotonic() + .7
            self.publish()

    def end(self, reason='USER_END'):
        self.cancel_start.set()
        with self.lifecycle_lock:
            with self.lock:
                if not self.session:
                    return {'state': self.state, 'last_cleanup': self.last_cleanup}
                if self.active_suite_id and self.active_suite_id in self.suites:
                    self.suites[self.active_suite_id]['cancel_requested'] = True
                self.epoch += 1
                self.state = 'CLOSING'
                session, client, owner = deepcopy(self.session), self.client, self.owner
                if client and hasattr(client, 'begin_close'):
                    client.begin_close()
                self.publish()
            native_ack = False
            native_state = None
            native_error = None
            native_attempts = 0
            if client:
                # Retry only an explicit QUIESCING lifecycle receipt.  Transport
                # errors are retained and are not retried, so an unknown request
                # cannot be piled behind the build's shared lock.
                for native_attempts in range(1, 4):
                    try:
                        result = client.end()
                        native_state = result.get('state')
                        native_ack = native_state == 'QUIESCED' and result.get('all_work_stopped') is True
                        if native_ack or native_state != 'QUIESCING':
                            break
                        time.sleep(.25)
                    except Exception as error:
                        native_error = self._error_code(error)
                        break
            if owner:
                owner.close()
            self.owner, self.client = None, None
            try:
                receipt = delete_session(self.root, session['session_id'])
                receipt.update(trigger=reason, build_sha256=session['build_sha256'], build_label=session['build']['label'],
                    reference_only=session['mode'] == 'reference', native_quiesce_ack=native_ack,
                    native_quiesce_state=native_state, native_quiesce_error=native_error,
                    native_quiesce_attempts=native_attempts,
                    executable_unchanged=digest(session['build']['path']) == session['build_sha256'])
                write_json(self.root / 'receipts' / (session['session_id'] + '.json'), receipt)
                with self.lock:
                    for suite in self.suites.values():
                        if suite['session_id'] != session['session_id']:
                            continue
                        if suite['state'] not in {'COMPLETED', 'CANCELLED'}:
                            plan = build_suite_plan(suite['preset_id'])
                            self._cancel_remaining_suite_cases(suite, plan)
                            suite['summary'] = {
                                'total': len(plan),
                                'passed': sum(row['state'] == 'PASSED' for row in suite['cases']),
                                'failed': sum(row['state'] == 'FAILED' for row in suite['cases']),
                                'skipped': sum(row['state'] == 'SKIPPED' for row in suite['cases']),
                            }
                            suite['state'], suite['verdict'] = 'CANCELLED', 'NOT_ASSESSED'
                            suite['progress'] = {'completed': len(plan), 'total': len(plan), 'current_case_id': None}
                            suite['completed_at'] = now()
                        suite.pop('cancel_requested', None)
                        suite['cleanup'] = {
                            'status': receipt['status'],
                            'remaining_entries': receipt['remaining_entries'],
                            'business_content_retained': receipt['business_content_retained'],
                            'completed_at': receipt['completed_at'],
                        }
                        self._write_suite_review(suite)
                    self.active_suite_id = None
                    self.last_cleanup = receipt
                    self.session, self.native, self.commands, self.events, self.capabilities = None, {}, {}, [], []
                    self.expression_catalog, self.expression_state = {}, {}
                    self.console_access_required = False
                    self.console_access_enabled = False
                    self.test_attachment = build_test_attachment()
                    self.state = 'CLEANED'
                    self.error = 'AUTOMATIC_EXECUTION_GUARD_LOST' if reason == 'AUTOMATIC_EXECUTION_GUARD_LOST' else None
                    self._reset_connection_monitor('NOT_CONNECTED')
                    self._reset_native_projection_monitor('NOT_CONNECTED')
                    self._reset_expression_monitor('NOT_CONNECTED')
                    self.cleanup_error = None
                    self.publish()
                return receipt
            except Exception as error:
                with self.lock:
                    self.cleanup_error = cleanup_failure(error)
                    self.state, self.error = 'CLEANUP_FAILED', self.cleanup_error['error_code']
                    self.native, self.commands, self.events = {}, {}, []
                    self.expression_state = {}
                    write_json(self.root / 'receipts' / (session['session_id'] + '.json'), {
                        'session_id': session['session_id'], 'batch_id': session['session_id'], 'status': 'CLEANUP_FAILED',
                        'trigger': reason, 'completed_at': now(), **self.cleanup_error, 'remaining_data_not_claimed_clean': True})
                    for suite in self.suites.values():
                        if suite['session_id'] == session['session_id']:
                            suite['cleanup'] = {'status': 'CLEANUP_FAILED', **self.cleanup_error}
                            self._write_suite_review(suite)
                    self.publish()
                raise RuntimeError('CLEANUP_FAILED') from error

    def close(self):
        self.end('CONSOLE_WINDOW_CLOSE')
        self.stop.set()
        self.thread.join(timeout=6)
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.suite_pool.shutdown(wait=False, cancel_futures=True)
