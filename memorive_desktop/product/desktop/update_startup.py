"""Bind an activated update to its existing profile before any user writes."""
from __future__ import annotations
import atexit
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys

_lease = None
_active = None


def acquire():
    global _lease, _active
    if _lease is not None or not getattr(sys, 'frozen', False):
        return _active
    from product_identity import _owned_path, _binding_json, _same_path, BINDING, INSTALL_OWNER
    executable = _owned_path(sys.executable)
    version = executable.parent.parent
    program = version.parent.parent
    if version.parent.name != 'versions':
        program = executable.parent.with_name(executable.parent.name+'.managed')
    home = program/'updates'
    if not home.exists():
        return None
    binding = _binding_json(home/'binding.json')
    data = _owned_path(binding.get('data_root'))
    profile = _owned_path(binding.get('state_root'))
    if binding.get('schema') != 'MemoriveUpdateBinding-v1' or not _same_path(binding.get('program_root'), program) or not profile.is_relative_to(data):
        raise ValueError('UPDATE_STARTUP_BINDING_INVALID')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    # Shared read handles coexist across app/service/agent processes. The
    # maintenance helper requires an exclusive read/write handle on this file.
    lock = kernel.CreateFileW(str(data/'.memorive-update.lock'), 0x80000000, 1, None, 4, 0x80, None)
    if lock == wintypes.HANDLE(-1).value:
        raise RuntimeError('UPDATE_MAINTENANCE_IN_PROGRESS')
    try:
        if (home/'activation-gate.json').exists():
            raise RuntimeError('UPDATE_RECOVERY_REQUIRED')
        owner = _binding_json(program/'install-owner.json')
        if owner.get('owner') != INSTALL_OWNER or not _same_path(owner.get('root'), program):
            raise ValueError('UPDATE_INSTALL_OWNER_INVALID')
        if not (home/'active-profile.json').exists():
            # Binding/checking an existing installation does not activate a new
            # profile. Keep the shared lease but preserve its original selector.
            if not _same_path(binding.get('source_app'), executable.parent):
                raise ValueError('UPDATE_SOURCE_BINDING_MISMATCH')
            _lease = lock
            atexit.register(kernel.CloseHandle, lock)
            return None
        active = _binding_json(home/'active-profile.json')
        current = _binding_json(program/'current.json')
        if owner.get('owner') != INSTALL_OWNER or active.get('schema') != 'MemoriveActiveUpdateProfile-v1':
            raise ValueError('UPDATE_ACTIVE_PROFILE_INVALID')
        for record in (current, active):
            if record.get('package_id') != BINDING['package_id'] or not _same_path(record.get('version'), version) or not _same_path(record.get('data_root'), data):
                raise ValueError('UPDATE_ACTIVE_VERSION_MISMATCH')
        if not _same_path(active.get('state_root'), profile):
            raise ValueError('UPDATE_ACTIVE_PROFILE_MISMATCH')
        import re
        operation_id = active.get('operation_id', '')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', operation_id):
            raise ValueError('UPDATE_OPERATION_INVALID')
        operation = _owned_path(home/operation_id)
        plan = _binding_json(operation/'transaction.json')
        if plan.get('stage') != 'COMPLETE' or plan.get('target_package_id') != BINDING['package_id']:
            raise RuntimeError('UPDATE_RECOVERY_REQUIRED')
        # O_EXCL makes this a monotonic latch. Never rewrite it on later starts.
        try:
            with (operation/'writes-opened.json').open('x', encoding='utf8') as stream:
                json.dump(dict(schema='MemoriveUpdateWrites-v1', package_id=BINDING['package_id'], pid=os.getpid()), stream)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            pass
        _active = dict(status='READY', selection_mode='VERIFIED_UPDATE_CONTINUATION',
                       package_id=BINDING['package_id'], state_root=str(profile), program_root=str(program), data_root=str(data))
        _lease = lock
        atexit.register(kernel.CloseHandle, lock)
        return _active
    except BaseException:
        kernel.CloseHandle(lock)
        raise


def profile_selection():
    active = acquire()
    return (Path(active['state_root']), dict(active)) if active else (None, None)


def initialize_language(state: Path, data: Path | None):
    """Apply an installer default only when no settings have ever been created."""
    if data is None or (state/'profile/settings/settings.json').exists():
        return
    from product_identity import _binding_json, _same_path, INSTALL_OWNER
    marker = data/'installer-default-language.json'
    if not marker.exists():
        return
    value = _binding_json(marker)
    if value.get('owner') != INSTALL_OWNER or not _same_path(value.get('data_root'), data) or value.get('language') not in ('zh-CN','en-US','ja-JP'):
        raise ValueError('INSTALLER_DEFAULT_LANGUAGE_INVALID')
    from memorive_settings.store import SettingsStore
    from memorive_settings.contracts import default_settings
    from memorive_folder_management import ProfileFolderManager
    folders = ProfileFolderManager(state/'profile')
    def defaults():
        settings = default_settings(workspace_root=str(folders.path_for('WORKSPACE')),
                                    artifact_root=str(folders.path_for('ARTIFACTS')))
        settings['preferences']['language'] = value['language']
        return settings
    SettingsStore(folders.path_for('SETTINGS'), default_factory=defaults).load(recover_corruption=False)
