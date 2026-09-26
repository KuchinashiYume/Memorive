from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
from urllib.parse import urlsplit

from common import (HEADER, OPERATIONS, MESSAGE_KINDS, EVENT_KINDS, TEST_PACK_ID,
    build_suite_catalog, encode, identifier, safe_test_cases)
from manager import build_info, discover_builds


def make_server(manager, port=8877, show_window=None, close_window=None):
    root = Path(__file__).resolve().parent

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code, value, mime='application/json; charset=utf-8'):
            data = value if isinstance(value, bytes) else encode(value)
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def allowed(self, api=False):
            origin = f'http://127.0.0.1:{server.server_port}'
            return (self.headers.get('Host') == f'127.0.0.1:{server.server_port}' and self.headers.get('Origin') in {None, origin}
                and self.headers.get('Sec-Fetch-Site') not in {'cross-site', 'same-site'} and (not api or self.headers.get(HEADER) == '1'))

        def do_GET(self):
            parsed = urlsplit(self.path)
            route = parsed.path
            browser_asset = route.startswith('/api/expression-assets/') and not parsed.query
            if not self.allowed(route.startswith('/api/') and not browser_asset):
                return self.send(403, {'error_code': 'LOCAL_ORIGIN_REQUIRED'})
            icon_path = root / 'memorive_test_console_icon.png'
            if not icon_path.is_file():
                icon_path = root.parent / 'assets' / 'memorive_test_console_icon.png'
            assets = {'/': (root / 'index.html', 'text/html; charset=utf-8'), '/app.js': (root / 'app.js', 'text/javascript; charset=utf-8'), '/app.css': (root / 'app.css', 'text/css; charset=utf-8'), '/memorive_test_console_icon.png': (icon_path, 'image/png')}
            if route in assets:
                path, mime = assets[route]
                return self.send(200, path.read_bytes(), mime)
            if route == '/api/snapshot':
                return self.send(200, manager.snapshot())
            if route == '/api/catalog':
                return self.send(200, {'builds': discover_builds(), 'operations': OPERATIONS,
                    'message_kinds': MESSAGE_KINDS, 'event_kinds': EVENT_KINDS,
                    'suite_presets': build_suite_catalog(list(OPERATIONS), connected=True),
                    'test_pack': {'test_pack_id': TEST_PACK_ID, 'automatic_attachment': True,
                        'automatic_execution': False, 'safe_batch_case_count': len(safe_test_cases())}})
            if route == '/api/contract':
                return self.send(200, json.loads((root / 'bridge.schema.json').read_text(encoding='utf-8')))
            if browser_asset:
                name = route.removeprefix('/api/expression-assets/')
                if __import__('re').fullmatch(r'[a-z][a-z0-9_-]{0,63}\.svg', name) is None:
                    return self.send(404, {'error_code': 'EXPRESSION_ASSET_NOT_ALLOWED'})
                try:
                    data, mime = manager.expression_asset(name[:-4])
                    return self.send(200, data, mime)
                except Exception as error:
                    code = str(error) if str(error) == 'EXPRESSION_ASSET_NOT_ALLOWED' else 'EXPRESSION_ASSET_UNAVAILABLE'
                    return self.send(404, {'error_code': code})
            if route in {'/api/events', '/api/commands'}:
                key = route.split('/')[-1]
                return self.send(200, {key: manager.snapshot()[key]})
            if route == '/api/suites':
                value = manager.snapshot()
                return self.send(200, {'suites': value['suites'], 'active_suite_id': value['active_suite_id'],
                    'latest_review_bundle': value['latest_review_bundle']})
            if route.startswith('/api/suites/'):
                target = route.split('/')[-1]
                suite = next((row for row in manager.snapshot()['suites'] if row['suite_id'] == target), None)
                return self.send(200 if suite else 404, suite or {'error_code': 'TEST_SUITE_NOT_FOUND'})
            if route.startswith('/api/commands/'):
                target = route.split('/')[-1]
                record = next((r for r in manager.snapshot()['commands'] if target in {r['command_id'], r['request_id'], r.get('bridge_command_id')}), None)
                return self.send(200 if record else 404, record or {'error_code': 'COMMAND_NOT_FOUND'})
            self.send(404, {'error_code': 'NOT_FOUND'})

        def do_POST(self):
            if not self.allowed(True):
                return self.send(403, {'error_code': 'LOCAL_ORIGIN_REQUIRED'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 256 * 1024 or self.headers.get_content_type() != 'application/json':
                    raise ValueError('JSON_BODY_REQUIRED')
                value = json.loads(self.rfile.read(length))
                if not isinstance(value, dict):
                    raise ValueError('BODY_OBJECT_REQUIRED')
                if self.path == '/api/session/start':
                    if set(value) - {'mode', 'build_path', 'executable_path'} or 'mode' not in value:
                        raise ValueError('START_FIELDS_INVALID')
                    mode = value['mode']
                    executable_path = value.get('executable_path', value.get('build_path'))
                    if mode == 'release':
                        mode = 'build'
                    result = manager.start(mode, executable_path)
                elif self.path == '/api/release/inspect':
                    if set(value) != {'executable_path'}:
                        raise ValueError('INSPECT_FIELDS_INVALID')
                    result = build_info(value['executable_path'])
                elif self.path == '/api/session/end':
                    if value:
                        raise ValueError('END_FIELDS_INVALID')
                    result = manager.end()
                elif self.path == '/api/commands':
                    result = manager.submit(value)
                elif self.path == '/api/suites':
                    result = manager.start_suite(value)
                elif self.path == '/api/suites/cancel':
                    result = manager.cancel_suite(value)
                elif self.path == '/api/window/show' and show_window:
                    show_window()
                    result = {'status': 'SHOWN'}
                elif self.path == '/api/window/close' and close_window:
                    close_window()
                    result = {'status': 'CLOSE_REQUESTED'}
                else:
                    return self.send(404, {'error_code': 'NOT_FOUND'})
                self.send(200, result)
            except Exception as error:
                message = str(error)
                code = message if __import__('re').fullmatch(r'[A-Z][A-Z0-9_]{0,120}', message) else type(error).__name__
                self.send(400, {'error_code': code})

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server
