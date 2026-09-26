"""Protocol reference peer. Never a product implementation or a model runner."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import hmac
import json
import os
import threading
from urllib.parse import urlsplit, parse_qs
import uuid

from common import EXPRESSION_EVENT_IDS, OPERATIONS, PROTOCOL, encode, now, validate_command, write_json


FRAME_ASSETS = {'idle': 'one_eye', 'blink-open': 'neutral', 'blink-closed': 'sleepy',
    'interaction': 'wink', 'working': 'shovel', 'success': 'writing',
    'save-success': 'success', 'error': 'injury'}
WEB_ASSETS = {'home-question': 'wink', 'logo-compress': 'one_eye', 'logo-shake': 'one_eye'}
MOTION_TIMES = {'blink': 3000, 'interaction': 1100, 'wake': 900, 'message': 700,
    'working': 900, 'attention': 1100, 'success': 1400, 'save-success': 1400, 'error': 900}


def expression_catalog():
    events = []
    for event_id in sorted(EXPRESSION_EVENT_IDS):
        prefix, name = event_id.split('.', 1)
        if prefix == 'asset':
            group, animated, motion_ms, asset = 'static_asset', False, 0, name
        elif prefix == 'frame':
            group, animated, motion_ms, asset = 'native_pose', False, 0, FRAME_ASSETS[name]
        elif prefix == 'motion':
            animated = name in MOTION_TIMES
            group, motion_ms, asset = ('native_animation' if animated else 'native_pose'), MOTION_TIMES.get(name, 0), None
        else:
            group, animated, motion_ms, asset = 'web_animation', True, {'home-question': 2460, 'logo-compress': 460, 'logo-shake': 480}[name], WEB_ASSETS[name]
        row = {'event_id': event_id, 'target': prefix, 'group': group, 'animated': animated,
            'motion_ms': motion_ms, 'label': {'zh-CN': name, 'en-US': name, 'ja-JP': name}}
        if asset:
            row['asset'] = asset
        events.append(row)
    return {'schema_version': 'Memorive-ExpressionConsole-v1', 'events': events,
        'duration_ms': {'min': 500, 'max': 5000, 'default': 1800, 'integer_only': True},
        'one_probe_at_a_time': True, 'preview_only': True, 'business_event_injected': False,
        'model_calls_allowed': False, 'history_limit': 64, 'poll_interval_ms': 250,
        'native_transform_reference_px': 90}


def main():
    session_id = os.environ['MEMORIVE_TEST_CONSOLE_SESSION_ID']
    root = Path(os.environ['MEMORIVE_TEST_CONSOLE_DATA_ROOT']).resolve()
    token = os.environ['MEMORIVE_TEST_CONSOLE_TOKEN']
    sha = os.environ['MEMORIVE_TEST_CONSOLE_BUILD_SHA256']
    state = {'messages': [], 'reports': [], 'jobs': [], 'inbox': [], 'research_qa': [], 'events': [], 'commands': {}, 'closing': False,
        'expressions': {'active': None, 'history': [], 'events': [], 'last_sequence': 0}}
    lock = threading.RLock()

    def event(kind, **fields):
        state['events'].append({'sequence': len(state['events']) + 1, 'event_type': kind, 'occurred_at': now(), 'reference_only': True, **fields})

    def snapshot():
        return {'session_id': session_id, 'observed_at': now(), 'automatic_execution': False, 'automatic_execution_locked': True,
            'reference_only': True, 'messages': state['messages'], 'reports': state['reports'], 'jobs': state['jobs'],
            'inbox': state['inbox'], 'research_qa': state['research_qa'], 'model_calls_started': 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, value):
            data = encode(value)
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def authorized(self):
            return (self.headers.get('Host') == f'127.0.0.1:{server.server_port}' and not self.headers.get('Origin')
                and hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token)
                and self.headers.get('X-Memorive-Session') == session_id)

        def do_GET(self):
            if not self.authorized():
                return self.send(403, {'session_id': session_id, 'error_code': 'AUTH_REQUIRED'})
            parsed = urlsplit(self.path)
            route = parsed.path.removeprefix('/memorive/test-bridge/v1')
            with lock:
                if route == '/expressions/catalog':
                    return self.send(200, {'session_id': session_id, **expression_catalog()})
                if route == '/expressions/state':
                    return self.send(200, {'session_id': session_id, 'schema_version': 'Memorive-ExpressionConsole-v1',
                        'renderer_ready': True, 'closing': state['closing'], 'access_enabled': True,
                        **state['expressions'], 'preview_only': True, 'business_event_injected': False})
                if route == '/snapshot':
                    return self.send(200, snapshot())
                if route == '/events':
                    query = parse_qs(parsed.query)
                    try:
                        after = int(query.get('after', ['0'])[0])
                    except ValueError:
                        return self.send(400, {'session_id': session_id, 'error_code': 'CURSOR_INVALID'})
                    return self.send(200, {'session_id': session_id, 'events': [r for r in state['events'] if r['sequence'] > after][:100]})
                if route.startswith('/commands/'):
                    command_id = route.split('/')[-1]
                    for row in state['commands'].values():
                        if row['command_id'] == command_id:
                            return self.send(200, row)
            self.send(404, {'session_id': session_id, 'error_code': 'NOT_FOUND'})

        def do_POST(self):
            if not self.authorized():
                return self.send(403, {'session_id': session_id, 'error_code': 'AUTH_REQUIRED'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 256 * 1024:
                    raise ValueError('BODY_SIZE_INVALID')
                body = json.loads(self.rfile.read(length))
                if body.get('session_id') != session_id:
                    raise ValueError('SESSION_MISMATCH')
                route = self.path.removeprefix('/memorive/test-bridge/v1')
                with lock:
                    if route == '/session/start':
                        if body.get('data_root') != str(root) or body.get('build_sha256') != sha or body.get('automatic_execution') is not False or body.get('close_policy') != 'DISCARD_ON_CLOSE':
                            raise ValueError('SESSION_REQUEST_REJECTED')
                        event('REFERENCE_SESSION_READY')
                        return self.send(200, {'session_id': session_id, 'protocol_version': PROTOCOL, 'state': 'READY', 'implementation_kind': 'REFERENCE_BRIDGE',
                            'build_sha256': sha, 'data_root': str(root), 'automatic_execution': False, 'automatic_execution_locked': True,
                            'all_writes_inside_data_root': True, 'cleanup_supported': True, 'operations': list(OPERATIONS),
                            'console_access_required': False, 'console_access_enabled': True})
                    if route == '/session/end':
                        state['closing'] = True
                        return self.send(200, {'session_id': session_id, 'state': 'QUIESCED', 'all_work_stopped': True, 'reference_only': True})
                    if route != '/commands' or state['closing']:
                        raise ValueError('SESSION_NOT_ACCEPTING_COMMANDS')
                    command = {key: value for key, value in body.items() if key != 'session_id'}
                    validate_command(command)
                    request_id, operation, params = command['request_id'], command['operation'], command['params']
                    previous = state['commands'].get(request_id)
                    fingerprint = __import__('hashlib').sha256(encode(command)).hexdigest()
                    if previous:
                        if previous['request_sha256'] != fingerprint:
                            raise ValueError('IDEMPOTENCY_CONFLICT')
                        return self.send(200, previous)
                    result = {'reference_only': True, 'actual_build_operation': False, 'model_calls_started': 0}
                    if operation == 'message.test':
                        message = {'message_id': 'ref-message-' + uuid.uuid4().hex[:10], 'title': '[接口自测] ' + params['title'],
                            'body': params['body'], 'kind': params['kind'], 'confirmed': False, 'starred': False}
                        state['messages'].append(message)
                        result['message'] = message
                    elif operation == 'event.inject':
                        event_state = {
                            'import': 'QUEUED', 'complete': 'SUCCEEDED', 'fail': 'FAILED',
                            'review': 'WAITING_REVIEW', 'cancel': 'CANCELLED', 'stall': 'STALLED',
                        }[params['event']]
                        job = {'job_id': 'ref-job-' + uuid.uuid4().hex[:10], 'state': event_state,
                            'simulated_event': params['event']}
                        state['jobs'].append(job)
                        result['job'] = job
                        event('SIMULATED_' + params['event'].upper(), note=params['note'], job_id=job['job_id'])
                    elif operation == 'report.run':
                        report = {'report_id': 'ref-report-' + uuid.uuid4().hex[:10], 'period': params['period'], 'title': '[接口自测] ' + params['period'] + ' 报告'}
                        (root / 'reference_reports').mkdir(exist_ok=True)
                        (root / 'reference_reports' / (report['report_id'] + '.md')).write_text('# 接口参考报告\n\n只用于验证临时文件清理，不是 memo build 生成的报告。\n', encoding='utf-8')
                        state['reports'].append(report)
                        result['report'] = report
                    elif operation == 'discovery.recommend':
                        result['articles'] = [{'title': '[接口自测] 推荐文章占位', 'topic': params['topic'], 'real_recommendation': False}]
                    elif operation == 'inbox.import':
                        items = [{'item_id': 'ref-item-' + uuid.uuid4().hex[:10], 'name': Path(p).name, 'state': 'QUEUED', 'actual_file_read': False} for p in params['paths']]
                        state['inbox'].extend(items)
                        result['items'] = items
                    elif operation == 'research.qa.simulate':
                        qa = {'qa_id': 'ref-qa-' + uuid.uuid4().hex[:10], 'question': params['question'],
                            'answer': params['answer'], 'citations': params['citations'],
                            'simulated': True, 'model_calls_started': 0}
                        state['research_qa'].append(qa)
                        result['research_qa'] = qa
                    elif operation in {'message.confirm', 'message.star'}:
                        message = next((m for m in state['messages'] if m['message_id'] == params['message_id']), None)
                        if message is None:
                            raise ValueError('MESSAGE_NOT_FOUND')
                        field = 'confirmed' if operation.endswith('confirm') else 'starred'
                        message[field] = True if field == 'confirmed' else not message[field]
                        result['message'] = message
                    elif operation.startswith('task.'):
                        job = next((row for row in state['jobs'] if row['job_id'] == params['job_id']), None)
                        if job is None:
                            raise ValueError('JOB_NOT_FOUND')
                        if operation == 'task.cancel':
                            job['state'] = 'CANCELLED'
                        elif operation == 'task.pause':
                            job['state'] = 'PAUSED'
                        elif operation in {'task.resume', 'task.retry'}:
                            job['state'] = 'QUEUED'
                        result.update(simulated_only=True, requested_action=operation, job=job)
                    elif operation == 'inbox.dispatch':
                        result.update(simulated_only=True, requested_action=operation, target=params)
                    elif operation == 'expression.trigger':
                        catalog_row = next(row for row in expression_catalog()['events'] if row['event_id'] == params['event_id'])
                        probe = {'probe_id': 'reference-expression-' + uuid.uuid4().hex, 'request_id': request_id,
                            'event_id': params['event_id'], 'target': catalog_row['target'], 'state': 'COMPLETED',
                            'duration_ms': max(params['duration_ms'], catalog_row['motion_ms'] + 250),
                            'accepted_at': now(), 'displayed': True, 'displayed_at': now(), 'restored': True,
                            'completed_at': now(), 'error_code': None, 'samples': []}
                        if catalog_row['group'] in {'native_pose', 'native_animation'}:
                            frame = FRAME_ASSETS.get(params['event_id'].split('.', 1)[1], 'idle')
                            probe['samples'].append({'visible': True, 'frame': frame, 'frame_sha256': 'REFERENCE_ONLY',
                                'animation': 'REFERENCE_ONLY', 'transform': [frame, 0, 0, 0, 1, 1], 'position_unchanged': True})
                        else:
                            probe['samples'].append({'visible': True, 'asset': catalog_row.get('asset'),
                                'current_asset': catalog_row.get('asset'), 'transform': 'none', 'animation_playing': False})
                        state['expressions']['history'].append(probe)
                        result.update(probe=probe, completed=True, preview_only=True, actual_build_operation=False)
                    elif operation == 'expression.cancel':
                        result.update(cancel_requested=False, probe_id=None, restoration_pending=False,
                            preview_only=True, actual_build_operation=False)
                    else:
                        result['diagnostics'] = {'protocol': PROTOCOL, 'implementation': 'REFERENCE_BRIDGE'}
                    record = {'session_id': session_id, 'request_id': request_id, 'command_id': 'ref-command-' + uuid.uuid4().hex,
                        'operation': operation, 'state': 'SUCCEEDED', 'request_sha256': fingerprint, 'result': result, 'completed_at': now()}
                    state['commands'][request_id] = record
                    event('REFERENCE_COMMAND_COMPLETED', command_id=record['command_id'], operation=operation)
                    write_json(root / 'reference-state.json', snapshot())
                    return self.send(200, record)
            except (ValueError, KeyError, TypeError) as error:
                self.send(400, {'session_id': session_id, 'error_code': str(error)})

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    write_json(Path(os.environ['MEMORIVE_TEST_CONSOLE_DESCRIPTOR_PATH']), {'protocol_version': PROTOCOL, 'host': '127.0.0.1', 'port': server.server_port,
        'session_id': session_id, 'pid': os.getpid(), 'implementation_kind': 'REFERENCE_BRIDGE', 'secret_value_recorded': False})
    server.serve_forever(poll_interval=.1)


if __name__ == '__main__':
    main()
