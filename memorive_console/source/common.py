from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import stat
import time
import uuid
import tempfile
from contextlib import contextmanager
from copy import deepcopy

PROTOCOL = '1.0'
HEADER = 'X-Memorive-Console'
SESSION_PATTERN = re.compile(r'review-[0-9]{8}T[0-9]{6}-[a-f0-9]{12}')
OPERATIONS = {
    'message.test': {'title': '消息测试', 'required': ['kind', 'title', 'body'], 'optional': [], 'model': False},
    'event.inject': {'title': '模拟事件', 'required': ['event', 'note'], 'optional': [], 'model': False},
    'report.run': {'title': '日报 / 周报 / 月报', 'required': ['period'], 'optional': [], 'model': False},
    'discovery.recommend': {'title': '外部文献发现推荐文章', 'required': ['topic', 'count'], 'optional': [], 'model': True},
    'inbox.import': {'title': '导入测试文件', 'required': ['paths'], 'optional': [], 'model': False},
    'inbox.dispatch': {'title': '手动执行收件项', 'required': ['item_id'], 'optional': [], 'model': True},
    'task.pause': {'title': '暂停任务', 'required': ['job_id'], 'optional': [], 'model': False},
    'task.resume': {'title': '继续任务', 'required': ['job_id'], 'optional': [], 'model': True},
    'task.cancel': {'title': '取消任务', 'required': ['job_id'], 'optional': [], 'model': False},
    'task.retry': {'title': '重试任务', 'required': ['job_id'], 'optional': [], 'model': True},
    'message.confirm': {'title': '确认消息', 'required': ['message_id'], 'optional': [], 'model': False},
    'message.star': {'title': '消息星标', 'required': ['message_id'], 'optional': [], 'model': False},
    'diagnostics': {'title': '产品诊断', 'required': [], 'optional': [], 'model': False},
    'expression.trigger': {'title': '表情预览', 'required': ['event_id', 'duration_ms'], 'optional': [], 'model': False},
    'expression.cancel': {'title': '停止表情预览', 'required': [], 'optional': [], 'model': False},
    'research.qa.simulate': {'title': '研究问答样例', 'required': ['question', 'answer', 'citations'],
        'optional': [], 'model': False, 'extension': True},
}
MESSAGE_KINDS = ['info', 'success', 'warning', 'error', 'daily', 'weekly', 'monthly', 'report_failed', 'discovery', 'imported', 'review', 'cancelled', 'stalled', 'target_missing']
EVENT_KINDS = ['import', 'complete', 'fail', 'review', 'cancel', 'stall']
EXPRESSION_EVENT_IDS = frozenset(
    ['asset.' + value for value in ['neutral', 'writing', 'question', 'wink', 'shovel', 'one_eye', 'injury', 'sleepy', 'empty', 'success']]
    + ['frame.' + value for value in ['idle', 'blink-open', 'blink-closed', 'interaction', 'working', 'success', 'save-success', 'error']]
    + ['motion.' + value for value in ['idle', 'drag', 'sleep', 'blink', 'interaction', 'wake', 'message', 'working', 'attention', 'success', 'save-success', 'error']]
    + ['animation.' + value for value in ['home-question', 'logo-compress', 'logo-shake']]
)
TEST_PACK_ID = 'Memorive-ConsoleBuiltInTestPack-v1'
TEST_SUITE_PACK_ID = 'Memorive-ConsoleTestSuites-v1'
JSON_REPLACE_ATTEMPTS = 25
JSON_REPLACE_DELAY_SECONDS = .04
JSON_REPLACE_RETRY_WINERRORS = {5, 32, 33}


def _suite_case(case_id, operation, params=None, *, bindings=None, fixture=None):
    row = {'case_id': case_id, 'operation': operation, 'params': params or {}, 'allow_model_calls': False}
    if bindings:
        row['bindings'] = bindings
    if fixture:
        row['fixture'] = fixture
    return row


def _suite_case_library():
    cases = {
        'diagnostics': _suite_case('diagnostics', 'diagnostics'),
    }
    for kind in MESSAGE_KINDS:
        cases['message-' + kind] = _suite_case('message-' + kind, 'message.test', {
            'kind': kind,
            'title': '控制台测试 · ' + kind,
            'body': '此消息仅属于当前临时审核会话。',
        })
    cases['message-star'] = _suite_case('message-star', 'message.star', bindings={
        'message_id': {'from_case': 'message-info', 'identifier': 'message_id'},
    })
    cases['message-confirm'] = _suite_case('message-confirm', 'message.confirm', bindings={
        'message_id': {'from_case': 'message-info', 'identifier': 'message_id'},
    })
    for event in EVENT_KINDS:
        cases['event-' + event] = _suite_case('event-' + event, 'event.inject', {
            'event': event,
            'note': '由控制台内置测试包注入的临时事件。',
        })
    cases['task-pause-imported'] = _suite_case('task-pause-imported', 'task.pause', bindings={
        'job_id': {'from_case': 'event-import', 'identifier': 'job_id'},
    })
    cases['task-cancel-stalled'] = _suite_case('task-cancel-stalled', 'task.cancel', bindings={
        'job_id': {'from_case': 'event-stall', 'identifier': 'job_id'},
    })
    for period in ['daily', 'weekly', 'monthly']:
        cases['report-' + period] = _suite_case('report-' + period, 'report.run', {'period': period})
    cases['inbox-import'] = _suite_case('inbox-import', 'inbox.import', fixture={
        'type': 'session_text', 'name': 'console-suite-import.txt',
    })
    cases['research-qa-sample'] = _suite_case('research-qa-sample', 'research.qa.simulate', {
        'question': '这项研究解决了什么问题？',
        'answer': '这是用于验证研究问答组件、消息与桌宠联动的临时样例答案。',
        'citations': 'Demo 2026 · console://temporary-source',
    })
    return cases


def _suite_definitions():
    message_ids = ['message-' + kind for kind in MESSAGE_KINDS]
    event_ids = ['event-' + event for event in EVENT_KINDS]
    full = (['diagnostics'] + message_ids + ['message-star', 'message-confirm'] + event_ids
        + ['task-pause-imported', 'task-cancel-stalled', 'report-daily', 'report-weekly', 'report-monthly', 'inbox-import'])
    return {
        'full_safe': {
            'title': '一键全量回归',
            'description': '消息、事件、报告、导入、状态操作与诊断',
            'case_ids': full,
            'recommended': True,
        },
        'smoke': {
            'title': '快速冒烟',
            'description': '连接、消息、事件与日报的最短闭环',
            'case_ids': ['diagnostics', 'message-info', 'message-error', 'event-complete', 'event-fail', 'report-daily'],
        },
        'messages': {
            'title': '消息联动',
            'description': '14 类消息及星标、确认',
            'case_ids': message_ids + ['message-star', 'message-confirm'],
        },
        'workflow': {
            'title': '事件与导入',
            'description': '6 类事件、失败任务取消与临时导入',
            'case_ids': ['diagnostics'] + event_ids + ['task-pause-imported', 'task-cancel-stalled', 'inbox-import'],
        },
        'documents': {
            'title': '报告产物',
            'description': '日报、周报与月报',
            'case_ids': ['report-daily', 'report-weekly', 'report-monthly'],
        },
        'diagnostic_scenarios': {
            'title': '诊断场景',
            'description': '消息、研究问答及成功/失败流程的排查样例',
            'case_ids': ['message-info', 'message-error', 'event-import', 'event-complete',
                'event-fail', 'research-qa-sample'],
        },
    }


def build_suite_plan(preset_id):
    definitions = _suite_definitions()
    if preset_id not in definitions:
        raise ValueError('TEST_SUITE_UNKNOWN')
    library = _suite_case_library()
    plan = [deepcopy(library[case_id]) for case_id in definitions[preset_id]['case_ids']]
    if any(case['allow_model_calls'] is not False or OPERATIONS[case['operation']]['model'] is not False for case in plan):
        raise RuntimeError('TEST_SUITE_MODEL_BOUNDARY_INVALID')
    return plan


def build_suite_catalog(supported=None, connected=False):
    supported = set(supported or [])
    rows = []
    for preset_id, definition in _suite_definitions().items():
        plan = build_suite_plan(preset_id)
        by_id = {case['case_id']: case for case in plan}
        operations = list(dict.fromkeys(case['operation'] for case in plan))
        available = sum(case['operation'] in supported and all(
            by_id[binding['from_case']]['operation'] in supported
            for binding in case.get('bindings', {}).values()
        ) for case in plan) if connected else 0
        missing = [operation for operation in operations if operation not in supported] if connected else operations
        rows.append({
            'preset_id': preset_id,
            'title': definition['title'],
            'description': definition['description'],
            'recommended': definition.get('recommended', False),
            'case_count': len(plan),
            'available_case_count': available,
            'operations': operations,
            'missing_operations': missing,
            'status': 'WAITING_FOR_BUILD' if not connected else 'READY' if not missing else 'PARTIAL',
            'model_calls_allowed': False,
            'automatic_execution': False,
        })
    return rows


def safe_test_cases():
    """Tests that can be queued together without model or inbox execution."""
    cases = []
    for kind in MESSAGE_KINDS:
        cases.append({'case_id': 'message-' + kind, 'operation': 'message.test',
            'params': {'kind': kind, 'title': '控制台测试 · ' + kind,
                'body': '此消息仅属于当前临时审核会话。'}, 'allow_model_calls': False})
    for event in EVENT_KINDS:
        cases.append({'case_id': 'event-' + event, 'operation': 'event.inject',
            'params': {'event': event, 'note': '由控制台内置测试包注入的临时事件。'},
            'allow_model_calls': False})
    for period in ['daily', 'weekly', 'monthly']:
        cases.append({'case_id': 'report-' + period, 'operation': 'report.run',
            'params': {'period': period}, 'allow_model_calls': False})
    cases.append({'case_id': 'diagnostics', 'operation': 'diagnostics',
        'params': {}, 'allow_model_calls': False})
    cases.append({'case_id': 'research-qa-sample', 'operation': 'research.qa.simulate',
        'params': {'question': '这项研究解决了什么问题？',
            'answer': '这是用于验证研究问答组件、消息与桌宠联动的临时样例答案。',
            'citations': 'Demo 2026 · console://temporary-source'},
        'allow_model_calls': False})
    return cases


def build_test_attachment(supported=None, connected=False):
    """Mount compatible tests after handshake without running any command."""
    supported = list(supported or [])
    if not connected:
        return {'test_pack_id': TEST_PACK_ID, 'status': 'WAITING_FOR_BUILD',
            'automatic_attachment': True, 'automatic_execution': False,
            'model_calls_allowed_in_batch': False, 'safe_batch_case_count': 0,
            'safe_batch_operations': [], 'safe_batch_cases': [],
            'one_click_suite_pack_id': TEST_SUITE_PACK_ID, 'one_click_case_count': 0,
            'one_click_operations': [],
            'manual_operations': [],
            'missing_operations': [name for name, specification in OPERATIONS.items()
                if not specification.get('extension')],
            'optional_missing_operations': [name for name, specification in OPERATIONS.items()
                if specification.get('extension')]}
    cases = [case for case in safe_test_cases() if case['operation'] in supported]
    safe_operations = [operation for operation in OPERATIONS
        if operation in supported and any(case['operation'] == operation for case in cases)]
    suite_plan = build_suite_plan('full_safe')
    planned_suite_operations = {case['operation'] for case in suite_plan}
    suite_operations = [operation for operation in OPERATIONS if operation in supported and operation in planned_suite_operations]
    one_click_count = next(row['available_case_count'] for row in build_suite_catalog(supported, connected=True)
        if row['preset_id'] == 'full_safe')
    manual = [operation for operation in OPERATIONS if operation in supported and operation not in suite_operations]
    missing = [operation for operation, specification in OPERATIONS.items()
        if operation not in supported and not specification.get('extension')]
    optional_missing = [operation for operation, specification in OPERATIONS.items()
        if operation not in supported and specification.get('extension')]
    return {'test_pack_id': TEST_PACK_ID,
        'status': 'ATTACHED' if not missing else 'PARTIAL' if supported else 'NO_SUPPORTED_TESTS',
        'automatic_attachment': True, 'automatic_execution': False,
        'model_calls_allowed_in_batch': False,
        'safe_batch_case_count': len(cases), 'safe_batch_operations': safe_operations,
        'safe_batch_cases': cases, 'manual_operations': manual,
        'one_click_suite_pack_id': TEST_SUITE_PACK_ID, 'one_click_case_count': one_click_count,
        'one_click_operations': suite_operations,
        'missing_operations': missing, 'optional_missing_operations': optional_missing}


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(',', ':')).encode('utf-8')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f'.{os.getpid()}.{uuid.uuid4().hex}.tmp')
    try:
        temp.write_bytes(encode(value))
        for attempt in range(JSON_REPLACE_ATTEMPTS):
            try:
                os.replace(temp, path)
                return
            except PermissionError as error:
                retryable = os.name == 'nt' and getattr(error, 'winerror', None) in JSON_REPLACE_RETRY_WINERRORS
                if not retryable or attempt == JSON_REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(JSON_REPLACE_DELAY_SECONDS)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}', value):
        raise ValueError('IDENTIFIER_INVALID')
    return value


def validate_command(value):
    if not isinstance(value, dict) or set(value) != {'request_id', 'operation', 'params', 'allow_model_calls'}:
        raise ValueError('COMMAND_FIELDS_INVALID')
    identifier(value['request_id'])
    if type(value['allow_model_calls']) is not bool:
        raise ValueError('MODEL_PERMISSION_BOOLEAN_REQUIRED')
    operation = value['operation']
    if operation not in OPERATIONS:
        raise ValueError('OPERATION_UNKNOWN')
    specification = OPERATIONS[operation]
    params = value['params']
    if not isinstance(params, dict) or set(params) != set(specification['required']):
        raise ValueError('PARAMETER_FIELDS_INVALID')
    if specification['model'] and value['allow_model_calls'] is not True:
        raise ValueError('MANUAL_MODEL_PERMISSION_REQUIRED')
    if len(encode(params)) > 128 * 1024:
        raise ValueError('PARAMETERS_TOO_LARGE')
    if re.search(r'(?<![A-Za-z0-9_])(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|Bearer\s+\S+)', encode(params).decode()):
        raise ValueError('CREDENTIAL_VALUE_NOT_ALLOWED')
    if operation.startswith('expression.'):
        if value['allow_model_calls'] is not False:
            raise ValueError('EXPRESSION_MODEL_PERMISSION_FORBIDDEN')
        if operation == 'expression.trigger':
            if params.get('event_id') not in EXPRESSION_EVENT_IDS:
                raise ValueError('EXPRESSION_EVENT_UNKNOWN')
            duration = params.get('duration_ms')
            if type(duration) is not int or not 500 <= duration <= 5000:
                raise ValueError('EXPRESSION_DURATION_INVALID')
        return value
    for key, item in params.items():
        if key in {'job_id', 'item_id', 'message_id'}:
            identifier(item)
        elif key == 'paths':
            if not isinstance(item, list) or not 1 <= len(item) <= 32 or any(not isinstance(p, str) or not Path(p).is_absolute() for p in item):
                raise ValueError('ABSOLUTE_IMPORT_PATHS_REQUIRED')
        elif key == 'count':
            if type(item) is not int or not 1 <= item <= 20:
                raise ValueError('COUNT_INVALID')
        elif not isinstance(item, str) or len(item) > 3500:
            raise ValueError('TEXT_PARAMETER_INVALID')
    if operation == 'message.test' and (params['kind'] not in MESSAGE_KINDS or not params['title'].strip() or not params['body'].strip()):
        raise ValueError('MESSAGE_INVALID')
    if operation == 'message.test' and len(params['title']) > 180:
        raise ValueError('MESSAGE_TITLE_TOO_LONG')
    if operation == 'discovery.recommend' and not params['topic'].strip():
        raise ValueError('DISCOVERY_TOPIC_REQUIRED')
    if operation == 'event.inject' and params['event'] not in EVENT_KINDS:
        raise ValueError('EVENT_INVALID')
    if operation == 'report.run' and params['period'] not in {'daily', 'weekly', 'monthly'}:
        raise ValueError('REPORT_PERIOD_INVALID')
    if operation == 'research.qa.simulate' and any(not params[key].strip() for key in ('question', 'answer')):
        raise ValueError('RESEARCH_QA_INVALID')
    return value


def _no_reparse(path):
    info = path.lstat()
    if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
        raise CleanupPathError('REPARSE_POINT_CLEANUP_BLOCKED', path)


class CleanupPathError(ValueError):
    def __init__(self, code, path):
        super().__init__(code)
        self.cleanup_path = str(path)


def cleanup_failure(error):
    """Retain a useful cause without recording business content or a traceback."""
    cause, seen = error, set()
    while cause.__cause__ is not None and id(cause) not in seen:
        seen.add(id(cause))
        cause = cause.__cause__
    code = str(cause)
    if not re.fullmatch(r'[A-Z][A-Z0-9_]{0,120}', code):
        code = 'CLEANUP_FILE_IN_USE' if isinstance(cause, PermissionError) else type(cause).__name__
    result = {'error_code': code, 'error_type': type(cause).__name__}
    path = getattr(cause, 'cleanup_path', None) or getattr(cause, 'filename', None)
    if path:
        result['path'] = str(path)
    if getattr(cause, 'winerror', None) is not None:
        result['winerror'] = cause.winerror
    return result


@contextmanager
def _cleanup_guard(path):
    # Startup recovery and the old host's watchdog may overlap. A Windows
    # mutex is released even when its process crashes; it leaves no lock file.
    if os.name != 'nt':
        yield
        return
    import ctypes
    from ctypes import wintypes as W
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, W.BOOL, W.LPCWSTR]
    kernel.CreateMutexW.restype = W.HANDLE
    kernel.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
    kernel.ReleaseMutex.argtypes = [W.HANDLE]
    kernel.CloseHandle.argtypes = [W.HANDLE]
    name = 'Local\\Memorive-Console-Cleanup-' + hashlib.sha256(os.path.normcase(str(path)).encode()).hexdigest()
    handle = kernel.CreateMutexW(None, False, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    acquired = False
    try:
        result = kernel.WaitForSingleObject(handle, 15000)
        if result not in (0, 0x80):
            raise RuntimeError('CLEANUP_BUSY')
        acquired = True
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def _internal_link(path, root, info):
    tag = getattr(info, 'st_reparse_tag', None)
    if tag not in {getattr(stat, 'IO_REPARSE_TAG_MOUNT_POINT', 0xA0000003), getattr(stat, 'IO_REPARSE_TAG_SYMLINK', 0xA000000C)}:
        raise CleanupPathError('REPARSE_POINT_CLEANUP_BLOCKED', path)
    try:
        target = path.resolve(strict=False)
    except (OSError, RuntimeError) as error:
        raise CleanupPathError('LINK_TARGET_UNRESOLVED', path) from error
    if not target.is_relative_to(root):
        raise CleanupPathError('EXTERNAL_LINK_CLEANUP_BLOCKED', path)


def _cleanup_plan(root, marker_name):
    nodes, links, stack = [], [], [root]
    while stack:
        parent = stack.pop()
        _no_reparse(parent)
        with os.scandir(parent) as entries:
            for entry in entries:
                path = Path(entry.path)
                info = entry.stat(follow_symlinks=False)
                if getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
                    _internal_link(path, root, info)
                    links.append(path)
                elif path == root / marker_name:
                    continue
                else:
                    nodes.append(path)
                    if stat.S_ISDIR(info.st_mode):
                        stack.append(path)
    return nodes, links


def _check_cleanup_parents(path, root):
    # Re-check the lexical chain immediately before each removal, so a
    # directory link is never used as an ancestor of an unlink/rmdir call.
    parent = path.parent
    if not parent.is_relative_to(root):
        raise CleanupPathError('CLEANUP_OWNERSHIP_MISMATCH', path)
    while True:
        _no_reparse(parent)
        if parent == root:
            break
        parent = parent.parent


def _clear_owned_tree(root, marker_name):
    marker = root / marker_name
    _no_reparse(root)
    _no_reparse(marker)
    marker_value = read_json(marker)
    removed = unlinked = 0
    for attempt in range(25):
        try:
            nodes, links = _cleanup_plan(root, marker_name)
            # Finish the entire safety preflight before deleting anything.
            # Remove known internal links themselves, without entering them.
            for path in links:
                _check_cleanup_parents(path, root)
                info = path.lstat()
                _internal_link(path, root, info)
                if info.st_file_attributes & stat.FILE_ATTRIBUTE_DIRECTORY:
                    os.rmdir(path)
                else:
                    os.unlink(path)
                removed += 1
                unlinked += 1
            for path in sorted(nodes, key=lambda item: len(item.parts), reverse=True):
                _check_cleanup_parents(path, root)
                _no_reparse(path)
                info = path.lstat()
                if stat.S_ISDIR(info.st_mode):
                    os.rmdir(path)
                else:
                    if getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_READONLY:
                        os.chmod(path, stat.S_IWRITE)
                    os.unlink(path)
                removed += 1
            # Keep ownership evidence until the payload is gone. A failure
            # halfway through deletion can then be retried on the next close.
            _no_reparse(root)
            _no_reparse(marker)
            os.unlink(marker)
            try:
                os.rmdir(root)
            except OSError:
                if os.path.lexists(root):
                    _no_reparse(root)
                    if not os.path.lexists(marker):
                        write_json(marker, marker_value)
                raise
            return {'removed_entry_count': removed + 1, 'unlinked_internal_links': unlinked}
        except OSError as error:
            missing = Path(error.filename) if isinstance(error, FileNotFoundError) and error.filename else None
            vanished_child = missing is not None and missing not in {root, marker} and missing.is_relative_to(root)
            if vanished_child:
                # WebView may remove a cache leaf after enumeration. Re-plan
                # only while the original non-reparse ownership is intact.
                _no_reparse(root)
                _no_reparse(marker)
                if read_json(marker) != marker_value:
                    raise CleanupPathError('CLEANUP_MARKER_MISMATCH', marker) from error
            retryable = vanished_child or isinstance(error, PermissionError) or getattr(error, 'winerror', None) in {5, 32, 33, 145}
            if not retryable or attempt == 24:
                raise
            time.sleep(.2)
    raise RuntimeError('CLEANUP_NOT_COMPLETE')


def delete_session(root, session_id):
    """Delete only this console's registered, explicitly disposable session.

    Build evidence and user profiles cannot pass these path and ownership checks.
    Internal cache links are removed themselves, never traversed.
    """
    root = Path(root).resolve()
    if not SESSION_PATTERN.fullmatch(session_id):
        raise ValueError('SESSION_ID_INVALID')
    with _cleanup_guard(root / 'sessions' / session_id):
        return _delete_session_locked(root, session_id)


def _delete_session_locked(root, session_id):
    registry_path = root / 'owned_sessions' / (session_id + '.json')
    _no_reparse(registry_path)
    registry = read_json(registry_path)
    sessions_root = root / 'sessions'
    path = sessions_root / session_id
    _no_reparse(sessions_root)
    if path.resolve().parent != sessions_root.resolve() or registry.get('data_root') != str(path.resolve()) or registry.get('session_id') != session_id or registry.get('disposition') != 'DISCARD_ON_CLOSE':
        raise ValueError('CLEANUP_OWNERSHIP_MISMATCH')
    receipt = {'schema_version': 'Memorive-ConsoleCleanup-v1', 'session_id': session_id, 'batch_id': session_id, 'completed_at': now(), 'data_root': str(path), 'status': 'CLEANED', 'remaining_entries': 0, 'business_content_retained': False}
    if os.path.lexists(path):
        _no_reparse(path)
        _no_reparse(path / '.console-session.json')
        marker = read_json(path / '.console-session.json')
        if marker.get('session_id') != session_id or marker.get('owner') != 'MEMORIVE_INDEPENDENT_CONSOLE':
            raise ValueError('CLEANUP_MARKER_MISMATCH')
        receipt.update(_clear_owned_tree(path, '.console-session.json'))
        if os.path.lexists(path):
            raise RuntimeError('CLEANUP_NOT_COMPLETE')
    write_json(root / 'receipts' / (session_id + '.json'), receipt)
    write_json(registry_path, {**registry, 'status': 'CLEANED', 'completed_at': now()})
    return receipt


def create_ui_profile(root, owner_pid, owner_identity):
    """Short WebView path; no business data. Registered for exact cleanup."""
    profile_id = 'Memorive-Console-' + uuid.uuid4().hex[:12]
    parent = Path(tempfile.gettempdir()).resolve()
    path = parent / profile_id
    path.mkdir(exist_ok=False)
    write_json(path / '.console-ui.json', {'profile_id': profile_id, 'owner': 'MEMORIVE_INDEPENDENT_CONSOLE'})
    write_json(Path(root) / 'ui_profiles' / (profile_id + '.json'), {'profile_id': profile_id, 'data_root': str(path), 'temp_parent': str(parent),
        'owner_pid': owner_pid, 'owner_identity': owner_identity, 'status': 'OWNED', 'disposition': 'DISCARD_ON_CLOSE'})
    return profile_id, path


def delete_ui_profile(root, profile_id):
    root = Path(root).resolve()
    if not re.fullmatch(r'Memorive-Console-[a-f0-9]{12}', profile_id):
        raise ValueError('UI_PROFILE_ID_INVALID')
    with _cleanup_guard(root / 'ui_profiles' / profile_id):
        return _delete_ui_profile_locked(root, profile_id)


def _delete_ui_profile_locked(root, profile_id):
    registry_path = root / 'ui_profiles' / (profile_id + '.json')
    _no_reparse(registry_path)
    registry = read_json(registry_path)
    path = Path(registry['data_root'])
    if path.parent != Path(tempfile.gettempdir()).resolve() or path.parent != Path(registry['temp_parent']) or path.name != profile_id:
        raise ValueError('UI_PROFILE_PATH_INVALID')
    counts = {}
    if os.path.lexists(path):
        _no_reparse(path)
        _no_reparse(path / '.console-ui.json')
        if read_json(path / '.console-ui.json') != {'profile_id': profile_id, 'owner': 'MEMORIVE_INDEPENDENT_CONSOLE'}:
            raise ValueError('UI_PROFILE_MARKER_INVALID')
        counts = _clear_owned_tree(path, '.console-ui.json')
    receipt = {'profile_id': profile_id, 'status': 'CLEANED', 'remaining_entries': 0, 'business_content_retained': False, 'completed_at': now(), **counts}
    write_json(registry_path, {**registry, 'status': 'CLEANED'})
    write_json(root / 'receipts' / (profile_id + '.json'), receipt)
    return receipt
