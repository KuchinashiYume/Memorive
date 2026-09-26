from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request

from common import PROTOCOL, encode, identifier


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        raise ValueError('BRIDGE_REDIRECT_REJECTED')


class BridgeClient:
    def __init__(self, descriptor, token, session_id, expected_build, implementation):
        if descriptor.get('protocol_version') != PROTOCOL or descriptor.get('host') != '127.0.0.1' or type(descriptor.get('port')) is not int or not 1 <= descriptor['port'] <= 65535:
            raise ValueError('BRIDGE_DESCRIPTOR_INVALID')
        if descriptor.get('session_id') != session_id:
            raise ValueError('BRIDGE_SESSION_MISMATCH')
        self.base = f"http://127.0.0.1:{descriptor['port']}/memorive/test-bridge/v1"
        self.token = token
        self.session_id = session_id
        self.expected_build = expected_build
        self.implementation = implementation
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        # urllib can return after its client timeout while local-release is still
        # completing the handler under its shared bridge lock.  Keep one
        # console request in flight and close the admission gate before the
        # native quiesce request is sent.
        self._request_lock = threading.Lock()
        self._closing = threading.Event()

    def begin_close(self):
        self._closing.set()

    def request(self, method, route, body=None, *, timeout=5, allow_while_closing=False):
        with self._request_lock:
            if self._closing.is_set() and not allow_while_closing:
                raise RuntimeError('BRIDGE_CLIENT_CLOSING')
            request = urllib.request.Request(self.base + route, data=None if body is None else encode(body), method=method,
                headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json', 'X-Memorive-Session': self.session_id})
            try:
                with self.opener.open(request, timeout=timeout) as response:
                    raw = response.read(2 * 1024 * 1024 + 1)
            except urllib.error.HTTPError as error:
                raw = error.read(2 * 1024 * 1024 + 1)
                if len(raw) <= 2 * 1024 * 1024:
                    try:
                        value = json.loads(raw)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        value = None
                    code = value.get('error_code') if isinstance(value, dict) and value.get('session_id') == self.session_id else None
                    if isinstance(code, str) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,120}', code):
                        raise RuntimeError(code) from None
                raise RuntimeError('BRIDGE_HTTP_' + str(error.code)) from None
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError('BRIDGE_RESPONSE_TOO_LARGE')
            value = json.loads(raw)
            if not isinstance(value, dict) or value.get('session_id') != self.session_id:
                raise ValueError('BRIDGE_RESPONSE_SESSION_MISMATCH')
            return value

    def handshake(self, data_root):
        value = self.request('POST', '/session/start', {'protocol_version': PROTOCOL, 'session_id': self.session_id,
            'data_root': str(data_root), 'build_sha256': self.expected_build, 'automatic_execution': False, 'close_policy': 'DISCARD_ON_CLOSE'})
        required = {'protocol_version': PROTOCOL, 'state': 'READY', 'implementation_kind': self.implementation,
            'build_sha256': self.expected_build, 'data_root': str(data_root), 'automatic_execution': False,
            'automatic_execution_locked': True, 'all_writes_inside_data_root': True, 'cleanup_supported': True}
        if any(value.get(k) != v for k, v in required.items()):
            raise ValueError('BRIDGE_SAFETY_HANDSHAKE_REJECTED')
        for key in ['automatic_execution', 'automatic_execution_locked', 'all_writes_inside_data_root', 'cleanup_supported']:
            if type(value.get(key)) is not bool:
                raise ValueError('BRIDGE_SAFETY_HANDSHAKE_REJECTED')
        capabilities = value.get('operations')
        if not isinstance(capabilities, list) or any(not isinstance(op, str) or op not in __import__('common').OPERATIONS for op in capabilities) or len(set(capabilities)) != len(capabilities):
            raise ValueError('BRIDGE_CAPABILITIES_INVALID')
        permission_fields = ('console_access_required', 'console_access_enabled')
        if any(key in value for key in permission_fields):
            if any(type(value.get(key)) is not bool for key in permission_fields):
                raise ValueError('BRIDGE_CONSOLE_ACCESS_HANDSHAKE_INVALID')
        else:
            # Bridge v1 builds before local-release have no user-controlled access
            # gate.  Preserve their established behavior without pretending
            # that a switch exists in those builds.
            value['console_access_required'] = False
            value['console_access_enabled'] = True
        return value

    def snapshot(self):
        return self.request('GET', '/snapshot')

    def command(self, value):
        return self.request('POST', '/commands', {**value, 'session_id': self.session_id})

    def read_command(self, command_id):
        return self.request('GET', '/commands/' + identifier(command_id))

    def events(self, after):
        return self.request('GET', '/events?after=' + str(int(after)) + '&limit=100')

    def expression_catalog(self):
        return self.request('GET', '/expressions/catalog', timeout=3)

    def expression_state(self):
        return self.request('GET', '/expressions/state', timeout=3)

    def end(self):
        self.begin_close()
        return self.request('POST', '/session/end', {'session_id': self.session_id, 'close_policy': 'DISCARD_ON_CLOSE'},
            timeout=15, allow_while_closing=True)
