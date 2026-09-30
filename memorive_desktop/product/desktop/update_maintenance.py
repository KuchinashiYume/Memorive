"""Explicit offline update maintenance; never constructs ProductApi or schedulers."""
from __future__ import annotations
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import uuid


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def owned_path(path: str | Path) -> Path:
    value = Path(path).absolute()
    for p in [value, *value.parents]:
        if p.exists() and (p.is_symlink() or getattr(p.stat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('UPDATE_DATA_REPARSE_FORBIDDEN')
    return value.resolve()


def inventory(root: Path) -> dict[str, dict]:
    result = {}
    if root.exists():
        for parent, dirs, files in os.walk(root, followlinks=False):
            for name in dirs + files:
                owned_path(Path(parent)/name)
            for name in files:
                p = Path(parent)/name
                result[p.relative_to(root).as_posix()] = dict(size=p.stat().st_size, sha256=digest(p))
    return result


def sqlite_file(path: Path) -> bool:
    with path.open('rb') as stream:
        return stream.read(16) == b'SQLite format 3\x00'


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.new')
    with tmp.open('x', encoding='utf8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def network_forbidden(event, args):
    if event in ('socket.connect', 'socket.connect_ex', 'socket.getaddrinfo', 'subprocess.Popen', 'os.system'):
        raise RuntimeError('UPDATE_MAINTENANCE_SIDE_EFFECT_FORBIDDEN')


def validate_request(request_path: Path) -> tuple[dict, Path, Path, Path]:
    request_path = owned_path(request_path)
    request = json.loads(request_path.read_text('utf8'))
    if request.get('schema') != 'MemoriveUpdateMaintenance-v1':
        raise ValueError('UPDATE_MAINTENANCE_REQUEST_INVALID')
    operation = request_path.parent
    import re
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', request.get('operation_id', '')):
        raise ValueError('UPDATE_OPERATION_INVALID')
    root = owned_path(request['program_root'])
    profile = owned_path(request['state_root'])
    data = owned_path(request['data_root'])
    if operation != root/'updates'/request['operation_id'] or request_path.name != 'maintenance-request.json':
        raise ValueError('UPDATE_MAINTENANCE_SCOPE_INVALID')
    marker = json.loads((root/'install-owner.json').read_text('utf8'))
    if marker.get('owner') != 'Memorive-INSTALLER-TRIAL-V1' or owned_path(marker.get('root', '')) != root:
        raise ValueError('UPDATE_MAINTENANCE_OWNER_INVALID')
    if profile == data or not profile.is_relative_to(data):
        raise ValueError('UPDATE_MAINTENANCE_DATA_SCOPE_INVALID')
    if profile == root or profile.is_relative_to(root) or root.is_relative_to(profile):
        raise ValueError('UPDATE_PROGRAM_DATA_OVERLAP')
    binding = json.loads((root/'updates/binding.json').read_text('utf8'))
    if owned_path(binding['state_root']) != profile or owned_path(binding['data_root']) != owned_path(request['data_root']):
        raise ValueError('UPDATE_MAINTENANCE_BINDING_MISMATCH')
    executable = Path(sys.executable).resolve()
    plan = json.loads((operation/'transaction.json').read_text('utf8'))
    version = owned_path(plan['new_version'])
    if (plan.get('schema') != 'MemoriveUpdateTransaction-v1'
            or plan.get('operation_id') != request['operation_id']
            or version.parent != root/'versions'
            or owned_path(request['target_executable']) != version/'app/Memorive.exe'):
        raise ValueError('UPDATE_MAINTENANCE_TRANSACTION_MISMATCH')
    if not getattr(sys, 'frozen', False) or str(executable).casefold() != str(owned_path(request['target_executable'])).casefold() or digest(executable) != request['target_exe_sha256'].lower():
        raise ValueError('UPDATE_MAINTENANCE_EXE_MISMATCH')
    return request, root, profile, operation


def backup(profile: Path, operation: Path) -> dict:
    destination = operation/'data-backup'
    if destination.exists():
        raise ValueError('UPDATE_BACKUP_ALREADY_EXISTS')
    before = inventory(profile)
    destination.mkdir()
    databases = {name for name in before if name.endswith(('.sqlite3', '.sqlite', '.db')) and sqlite_file(profile/name)}
    sqlite_sidecars = {name+suffix for name in databases for suffix in ('-wal', '-shm', '-journal')}
    for name in before:
        if name in sqlite_sidecars:
            continue
        source = profile/name
        target = destination/name
        target.parent.mkdir(parents=True, exist_ok=True)
        if name in databases:
            with closing(sqlite3.connect(source.as_uri()+'?mode=ro', uri=True)) as src, closing(sqlite3.connect(target)) as dst:
                src.backup(dst)
                if dst.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                    raise ValueError('UPDATE_SQLITE_INTEGRITY_FAILED')
        else:
            shutil.copy2(source, target)
    after = inventory(profile)
    # SQLite backup may create/delete read-lock SHM state; durable bytes must not change.
    if {k:v for k,v in before.items() if k not in sqlite_sidecars} != {k:v for k,v in after.items() if k not in sqlite_sidecars}:
        raise ValueError('UPDATE_DATA_CHANGED_DURING_BACKUP')
    receipt = dict(schema='MemoriveUpdateBackup-v1', state_root=str(profile), files=inventory(destination),
                   source_before=before, source_after=after, consistent_sqlite=sorted(databases),
                   omitted_sqlite_sidecars=sorted(sqlite_sidecars & set(before)), external_materials_untouched=True)
    write(operation/'data-backup.json', receipt)
    return receipt


def migrate(profile: Path, operation: Path) -> dict:
    if (operation/'migration-complete.json').exists():
        receipt = json.loads((operation/'migration-complete.json').read_text('utf8'))
        if receipt['after'] != inventory(profile):
            raise ValueError('UPDATE_NEW_WRITES_BLOCK_RECOVERY')
        return receipt
    if not (operation/'data-backup.json').exists():
        raise ValueError('UPDATE_BACKUP_REQUIRED')
    prepared_path = operation/'migration-prepared.json'
    staging = owned_path(profile.with_name(profile.name+'.update-migrate-'+operation.name))
    retained = owned_path(profile.with_name(profile.name+'.update-before-'+operation.name))
    if prepared_path.exists():
        prepared = json.loads(prepared_path.read_text('utf8'))
    else:
        if staging.exists() or retained.exists():
            raise ValueError('UPDATE_MIGRATION_STAGING_REQUIRES_REVIEW')
        before = inventory(profile)
        write(operation/'migration-boundary.json', dict(before=before, status='PREPARING_COPY'))
        if profile.exists():
            shutil.copytree(profile, staging)
        else:
            staging.mkdir(parents=True)
        result = migrate_contents(staging)
        if inventory(profile) != before:
            raise ValueError('UPDATE_DATA_CHANGED_DURING_MIGRATION')
        prepared = dict(schema='MemorivePreparedMigration-v1', before=before,
                        after=inventory(staging), result=result, original_existed=profile.exists())
        write(prepared_path, prepared)
    # Migrate an isolated copy first. Durable before/after inventories make a
    # crash at either rename distinguishable from unexplained user writes.
    if retained.exists():
        if profile.exists():
            if inventory(profile) != prepared['after']:
                raise ValueError('UPDATE_NEW_WRITES_BLOCK_RECOVERY')
        else:
            if inventory(staging) != prepared['after']:
                raise ValueError('UPDATE_MIGRATION_STAGING_INVALID')
            os.replace(staging, profile)
    elif profile.exists() and inventory(profile) == prepared['after'] and not staging.exists():
        pass
    else:
        if inventory(profile) != prepared['before'] or inventory(staging) != prepared['after']:
            raise ValueError('UPDATE_NEW_WRITES_BLOCK_RECOVERY')
        write(operation/'migration-swap.json', dict(stage='PREPARED', state_root=str(profile)))
        if profile.exists():
            os.replace(profile, retained)
        os.replace(staging, profile)
    receipt = dict(prepared['result'], after=prepared['after'], retained_original=str(retained))
    write(operation/'migration-complete.json', receipt)
    return receipt


def migrate_contents(profile: Path) -> dict:
    """Only the isolated migration copy is writable in this function."""
    from memorive_settings.store import SettingsStore
    from memorive_folder_management import ProfileFolderManager
    folders = ProfileFolderManager(profile/'profile')
    settings_root = folders.path_for('SETTINGS')
    migrated = []
    if (settings_root/'settings.json').exists():
        old = json.loads((settings_root/'settings.json').read_text('utf8'))
        updated = SettingsStore(settings_root).load(recover_corruption=False)
        migrated.append(dict(kind='existing_settings_loader', before_schema=old.get('schema_version'),
                             after_schema=updated.get('schema_version')))
    workspace = folders.path_for('WORKSPACE')/'research_workspace'
    if (workspace/'research_workspace_v1.sqlite3').exists():
        from memorive_research_workspace.store import Store
        Store(workspace)  # schema bootstrap only; no workspace model, archive loop or indexing.
        migrated.append(dict(kind='existing_research_store_bootstrap', lifecycle_schema=2))
    checks = []
    for name in inventory(profile):
        path = profile/name
        if name.endswith(('.sqlite3', '.sqlite', '.db')) and sqlite_file(path):
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
                if db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                    raise ValueError('UPDATE_SQLITE_INTEGRITY_FAILED')
            checks.append(name)
    receipt = dict(status='PASS', migration_id='E11-existing-schemas-v1', migrations=migrated,
                   sqlite_checks=checks, after=inventory(profile), model_calls=0, network_calls=0,
                   source_scan=0, report_generation=0, archive_maintenance=0)
    return receipt


def restore(profile: Path, operation: Path) -> dict:
    plan = json.loads((operation/'transaction.json').read_text('utf8'))
    if plan.get('writes_opened') or (operation/'writes-opened.json').exists():
        raise ValueError('UPDATE_NEW_WRITES_BLOCK_RECOVERY')
    receipt = json.loads((operation/'data-backup.json').read_text('utf8'))
    backup_root = operation/'data-backup'
    if inventory(backup_root) != receipt['files']:
        raise ValueError('UPDATE_BACKUP_HASH_MISMATCH')
    recovery_path = operation/'restore-progress.json'
    recovery = json.loads(recovery_path.read_text('utf8')) if recovery_path.exists() else None
    retained = profile.with_name(profile.name+'.update-failed-'+operation.name)
    staging = profile.with_name(profile.name+'.update-restore-'+operation.name)
    if recovery is not None:
        if recovery.get('schema') != 'MemoriveRestoreProgress-v1' or recovery.get('state_root') != str(profile):
            raise ValueError('UPDATE_RECOVERY_BINDING_INVALID')
        if recovery['stage'] == 'COMPLETE':
            if inventory(profile) != receipt['files']:
                raise ValueError('UPDATE_NEW_WRITES_BLOCK_RECOVERY')
            return dict(status='RESTORED_BEFORE_WRITES', retained=str(retained), files=len(receipt['files']))
        if inventory(staging) != receipt['files'] and not (profile.exists() and inventory(profile) == receipt['files']):
            raise ValueError('UPDATE_RECOVERY_STAGING_INVALID')
        if profile.exists() and inventory(profile) == receipt['files'] and not staging.exists():
            write(recovery_path, dict(recovery, stage='COMPLETE'))
            return dict(status='RESTORED_BEFORE_WRITES', retained=str(retained), files=len(receipt['files']))
        if retained.exists():
            if profile.exists() and inventory(profile) != receipt['files']:
                raise ValueError('UPDATE_NEW_WRITES_BLOCK_RECOVERY')
            if not profile.exists():
                os.replace(staging, profile)
            write(recovery_path, dict(recovery, stage='COMPLETE'))
            return dict(status='RESTORED_BEFORE_WRITES', retained=str(retained), files=len(receipt['files']))
    expected_path = operation/'migration-complete.json'
    if expected_path.exists():
        expected = json.loads(expected_path.read_text('utf8'))['after']
        if inventory(profile) != expected:
            raise ValueError('UPDATE_NEW_WRITES_BLOCK_RECOVERY')
    elif (operation/'migration-prepared.json').exists():
        prepared = json.loads((operation/'migration-prepared.json').read_text('utf8'))
        current = inventory(profile)
        original = profile.with_name(profile.name+'.update-before-'+operation.name)
        safe_gap = not profile.exists() and original.exists() and inventory(original) == prepared['before']
        if current not in (prepared['before'], prepared['after']) and not safe_gap:
            raise ValueError('UPDATE_NEW_WRITES_BLOCK_RECOVERY')
    else:
        # A killed legacy process could reopen without E11's startup gate. Never
        # infer that unexplained bytes are merely an interrupted migration.
        boundary = operation/'migration-boundary.json'
        expected = json.loads(boundary.read_text('utf8'))['before'] if boundary.exists() else receipt['source_after']
        if inventory(profile) != expected:
            raise ValueError('UPDATE_UNSEALED_MIGRATION_REQUIRES_RECOVERY_REVIEW')
    # No deletion of original files: move the owned stopped profile aside and
    # rebuild an exact snapshot. Keep post-migration state for manual recovery.
    if retained.exists():
        raise ValueError('UPDATE_RECOVERY_RETAINED_PATH_EXISTS')
    if not staging.exists():
        shutil.copytree(backup_root, staging)
    if inventory(staging) != receipt['files']:
        raise ValueError('UPDATE_RECOVERY_STAGING_INVALID')
    write(recovery_path, dict(schema='MemoriveRestoreProgress-v1', state_root=str(profile), stage='PREPARED'))
    if profile.exists():
        os.replace(profile, retained)
    os.replace(staging, profile)
    write(recovery_path, dict(schema='MemoriveRestoreProgress-v1', state_root=str(profile), stage='COMPLETE'))
    return dict(status='RESTORED_BEFORE_WRITES', retained=str(retained), files=len(receipt['files']))


def verify(profile: Path, operation: Path) -> dict:
    before = inventory(profile)
    migration = json.loads((operation/'migration-complete.json').read_text('utf8'))
    if migration['after'] != before:
        raise ValueError('UPDATE_DATA_CHANGED_AFTER_MIGRATION')
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
    required = ('bundle_integrated.html', 'bundle_assistant.html', 'release_identity_binding.json', 'source/facade_method_binding.json')
    for relative in required:
        if not (root/relative).is_file():
            raise ValueError('UPDATE_RUNTIME_RESOURCE_MISSING')
    # Import contract boundaries without constructing services, model gateways,
    # archival/index workers, or generating fresh user reports.
    import memorive_settings.store
    import memorive_research_workspace.store
    import memorive_folder_management
    import product_identity
    if inventory(profile) != before:
        raise ValueError('UPDATE_VERIFY_MUTATED_DATA')
    return dict(status='PASS', kind='OFFLINE_RESOURCE_IMPORT_AND_MIGRATED_DATA',
                executable_sha256=digest(Path(sys.executable)), model_calls=0,
                network_calls=0, writable_data_untouched=True, gui_qualified=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--memo-update-maintenance', type=Path, required=True)
    parser.add_argument('--action', choices=('backup', 'migrate', 'restore', 'verify'), required=True)
    args = parser.parse_args()
    sys.addaudithook(network_forbidden)
    request, root, profile, operation = validate_request(args.memo_update_maintenance)
    result = None
    try:
        if args.action == 'backup':
            result = backup(profile, operation)
        elif args.action == 'migrate':
            result = migrate(profile, operation)
        elif args.action == 'restore':
            result = restore(profile, operation)
        else:
            result = verify(profile, operation)
        write(operation/('maintenance-'+args.action+'.json'), dict(status='PASS', result=result))
        return 0
    except Exception as exc:
        write(operation/('maintenance-'+args.action+'.json'), dict(status='FAIL', error=str(exc)))
        return 1
