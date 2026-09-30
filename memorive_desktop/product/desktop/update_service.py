"""Desktop update controller. Public metadata only; no model gateway or credentials."""
from __future__ import annotations
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import threading
import uuid

METHODS = frozenset('updates.'+name for name in ('status', 'check', 'prepare', 'download', 'cancel', 'apply', 'recover', 'preference'))


def current_release_view(receipt, current_version, current_package):
    """Project persisted discovery onto this running release without network or writes."""
    if not isinstance(receipt, dict):
        return receipt
    value = dict(receipt)
    state = value.get('state')
    if state not in ('AVAILABLE', 'UP_TO_DATE') and not (state == 'NO_COMPATIBLE_UPDATE' and value.get('error') == 'UPDATE_SAME_VERSION_DIFFERENT_PACKAGE'):
        return value
    version, package = value.get('release_version'), value.get('package_id')
    if not version or not package:
        return value
    def order(text):
        if not isinstance(text, str) or re.fullmatch(r'(?:[0-9]+\.[0-9]{2,}|[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)', text) is None:
            raise ValueError('UPDATE_VERSION_INVALID')
        parts = tuple(map(int, text.split('.')))
        return parts + (0,) * (4 - len(parts))
    try:
        cached, current = order(version), order(current_version)
    except ValueError:
        return value
    if cached < current or (cached == current and package == current_package):
        value.update(state='UP_TO_DATE', error=None, total=0, downloaded=0,
                     full_fallback=False, consent_required=False, fallback_reason=None)
    elif cached == current:
        value.update(state='NO_COMPATIBLE_UPDATE', error='UPDATE_SAME_VERSION_DIFFERENT_PACKAGE')
    elif state != 'AVAILABLE':
        value.update(state='IDLE', error=None, checked_at=0)
    return value


class UpdateService:
    def __init__(self, api):
        self.api = api
        self.lock = threading.RLock()
        self.future = None
        self.error = None
        self.activity = None
        self.state = api._state_dir/'updates'
        self.receipt = self.state/'desktop-update.json'
        self.record = json.loads(self.receipt.read_text('utf8')) if self.receipt.exists() else dict(automatic_enabled=True, operation_id=None)

    def _save(self):
        from update_maintenance import write
        write(self.receipt, self.record)

    def _context(self):
        if not getattr(sys, 'frozen', False):
            raise ValueError('UPDATE_PACKAGED_APPLICATION_REQUIRED')
        from product_identity import _owned_path, _binding_json
        exe = _owned_path(sys.executable)
        helper = _owned_path(Path(getattr(sys, '_MEIPASS'))/'update/Memorive.Update.exe')
        helper_binding = _binding_json(helper.parent/'helper-identity.json')
        with helper.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != helper_binding.get('helper_sha256'):
            raise ValueError('UPDATE_HELPER_IDENTITY_MISMATCH')
        if exe.parent.name == 'app' and exe.parent.parent.parent.name == 'versions':
            root = exe.parent.parent.parent.parent
            current = _binding_json(root/'current.json')
            data = _owned_path(current['data_root'])
            portable = False
        else:
            data = _owned_path(exe.parent/'sandbox_profile')
            root = _owned_path(exe.parent.with_name(exe.parent.name+'.managed'))
            portable = True
        if not self.api._state_dir.resolve().is_relative_to(data):
            raise ValueError('UPDATE_EXPLICIT_INSTANCE_DATA_REQUIRED')
        return helper, root, data, exe.parent, portable

    def _preferences(self):
        settings_path = self.api._state_dir/'profile/settings/settings.json'
        return json.loads(settings_path.read_text('utf8'))['settings']['preferences'] if settings_path.exists() else {}

    def _command(self, action, **values):
        helper, root, data, source, portable = self._context()
        preferences = self._preferences()
        language = preferences.get('language', 'en-US')
        if language not in ('zh-CN', 'en-US', 'ja-JP'):
            language = 'en-US'
        command = [str(helper), '--json', '--action', action, '--root', str(root), '--language', language]
        for name, value in values.items():
            if value is True:
                command.append('--'+name.replace('_', '-'))
            elif value not in (False, None):
                command.extend(['--'+name.replace('_', '-'), str(value)])
        if action == 'bind':
            command += ['--data', str(data), '--state', str(self.api._state_dir), '--source', str(source)]
            if portable:
                command.append('--portable')
            # The helper enforces the exact installer test root for test trust.
            if 'Memorive-Installer-Tests' in root.parts:
                command.append('--sandbox')
        if action in ('check', 'prepare', 'download'):
            from update_maintenance import write
            policy = self.state/'public-network-policy.json'
            write(policy, dict(proxy_mode=preferences.get('proxy_mode', 'SYSTEM'),
                               proxy_address=preferences.get('proxy_address', ''),
                               maximum_requests=12, maximum_response_bytes=5*1024**3))
            command += ['--network-policy', str(policy)]
        return command

    def _execute(self, action, **values):
        command = self._command(action, **values)
        # This is local helper IPC, not a hidden model execution. The real update
        # commit/recovery path below always opens its native progress window.
        _, program, _, _, _ = self._context()
        receipt_root=self.state if action=='bind' else program/'updates/ipc-receipts'
        receipt_root.mkdir(parents=True, exist_ok=True)
        receipt=receipt_root/('helper-'+uuid.uuid4().hex+'.json')
        errors=receipt.with_suffix('.stderr')
        with receipt.open('x', encoding='utf8') as output, errors.open('x', encoding='utf8') as diagnostics:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=output,
                                    stderr=diagnostics, creationflags=subprocess.CREATE_NO_WINDOW,
                                    check=False, timeout=3600)
        payload = json.loads(receipt.read_text('utf8'))
        if result.returncode or payload.get('status') != 'PASS':
            raise RuntimeError(payload.get('error', 'UPDATE_HELPER_FAILED'))
        return payload['result']

    def _ensure_binding(self):
        _, root, _, _, _ = self._context()
        if not (root/'updates/binding.json').exists():
            self._execute('bind')

    def _start(self, action, **values):
        with self.lock:
            if self.future is not None and self.future.is_alive():
                return self.status()
            self.activity = action
            self.error = None
            def work():
                try:
                    self._ensure_binding()
                    result = self._execute(action, **values)
                    with self.lock:
                        self.record['last_result'] = result
                        if action == 'check':
                            self.record['release'] = result
                        self._save()
                    return result
                except Exception as error:
                    with self.lock:
                        self.error = str(error) if str(error).startswith('UPDATE_') else 'UPDATE_HELPER_FAILED'
                        self.record['last_error'] = self.error
                        self._save()
                    return None
            self.future = threading.Thread(target=work, name='memorive-update', daemon=True)
            self.future.start()
            return self.status()

    def status(self):
        with self.lock:
            value = dict(self.record, activity=self.activity, busy=self.future is not None and self.future.is_alive(), error=self.error)
            try:
                _, root, _, _, _ = self._context()
                operation = self.record.get('operation_id')
                file = root/'updates'/operation/'transaction.json' if operation else root/'updates/discovery/check.json'
                if file.exists():
                    value['update'] = json.loads(file.read_text('utf8'))
                else:
                    value['update'] = dict(state='IDLE')
            except ValueError as error:
                value['error'] = str(error)
            from product_identity import BINDING
            for key in ('update', 'release', 'last_result'):
                if key in value:
                    value[key] = current_release_view(value[key], BINDING['release_version'], BINDING['package_id'])
            return value

    def _release_orphan(self):
        operation = self.record.get('operation_id')
        if not operation or (self.future is not None and self.future.is_alive()):
            return
        _, root, _, _, _ = self._context()
        if (root/'updates'/operation/'transaction.json').exists():
            return
        try:
            result = self._execute('release-orphan', operation=operation)
        except (RuntimeError, OSError):
            # A live helper owns the creation lock, or validation failed. Keep
            # the reference and gate; a subsequent check can safely try again.
            return
        if self.record.get('operation_id') == operation:
            self.record = result['record']

    def call(self, method, params):
        # Serialize admission with worker completion and orphan reconciliation.
        with self.lock:
            return self._call_locked(method, params)

    def _call_locked(self, method, params):
        action = method.removeprefix('updates.')
        allowed = {'status': set(), 'check': {'automatic'}, 'prepare': {'target'}, 'download': {'allow_full'},
                   'cancel': set(), 'apply': set(), 'recover': set(), 'preference': {'enabled'}}
        if action not in allowed or set(params)-allowed[action]:
            raise ValueError('UPDATE_FIELDS_INVALID')
        if action == 'status':
            return self.status()
        if action == 'preference':
            if not isinstance(params.get('enabled'), bool):
                raise ValueError('UPDATE_PREFERENCE_INVALID')
            self.record['automatic_enabled'] = params['enabled']
            self._save()
            return self.status()
        if action == 'check':
            automatic = params.get('automatic', False)
            if not isinstance(automatic, bool):
                raise ValueError('UPDATE_AUTOMATIC_INVALID')
            if automatic and not self.record['automatic_enabled']:
                return self.status()
            if self.future is not None and self.future.is_alive():
                return self.status()
            self._release_orphan()
            if self.record.get('operation_id'):
                current=self.status().get('update',{})
                if current.get('stage') not in ('COMPLETE','FAILED','CANCELLED','ROLLED_BACK'):
                    if automatic:
                        return self.status()
                    raise ValueError('UPDATE_OPERATION_ALREADY_ACTIVE')
                self.record['operation_id']=None
                self._save()
            return self._start('check', automatic=automatic)
        if action == 'prepare':
            import re
            target = params.get('target')
            if not isinstance(target, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', target):
                raise ValueError('UPDATE_TARGET_INVALID')
            if self.future is not None and self.future.is_alive():
                return self.status()
            self._release_orphan()
            if self.record.get('operation_id'):
                current = self.status().get('update', {})
                if current.get('stage') not in ('COMPLETE', 'FAILED', 'CANCELLED', 'ROLLED_BACK'):
                    raise ValueError('UPDATE_OPERATION_ALREADY_ACTIVE')
            self.record['operation_id'] = 'update-'+uuid.uuid4().hex
            self.record['prepare_request'] = dict(operation_id=self.record['operation_id'], target=target)
            self._save()
            return self._start('prepare', operation=self.record['operation_id'], target=target)
        operation = self.record.get('operation_id')
        if not operation:
            raise ValueError('UPDATE_OPERATION_REQUIRED')
        if action == 'cancel':
            return self._execute('cancel', operation=operation)
        if action in ('apply', 'recover'):
            command = self._command(action, operation=operation)
            command.remove('--json')
            # An independent visible process survives normal app exit. It waits
            # for this exact instance and its service, never kills running work.
            child = subprocess.Popen(command, cwd=str(Path(command[0]).parent), close_fds=True)
            return dict(status='WAITING_FOR_APPLICATION_EXIT', helper_pid=child.pid,
                        operation_id=operation, forced_kill=False)
        allow_full = params.get('allow_full', False)
        if not isinstance(allow_full, bool):
            raise ValueError('UPDATE_FULL_CONSENT_INVALID')
        return self._start('download', operation=operation, allow_full=allow_full)

