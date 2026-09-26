"""Cooperatively stop an owned urllib HTTP read without killing the provider.

The caller supplies its existing opener, preserving proxy/redirect policy.
Only the request's connections are closed. The original timeout, including
None, remains unchanged; cancellation is separate from a model timeout.
"""
from __future__ import annotations

import copy
import http.client
import socket
import threading
from types import SimpleNamespace
from urllib.request import HTTPHandler, HTTPSHandler, OpenerDirector
from urllib.error import HTTPError


def read_response(opener, request, *, timeout, interrupt=None, reader=None, read_error_response=False):
    def read(open_call):
        try:
            response = open_call(request, timeout=timeout)
        except HTTPError as error:
            if not read_error_response:
                raise
            response = error
        with response:
            if reader is not None:
                return reader(response)
            return SimpleNamespace(body=response.read(), status_code=getattr(response, 'status', 200))

    director = getattr(opener, '__self__', None)
    if interrupt is None or not isinstance(director, OpenerDirector) or request.type not in {'http','https'}:
        return read(opener)

    lock = threading.Lock()
    cancelled = threading.Event()
    finished = threading.Event()
    sockets = []
    outcome = {}

    def close_socket(sock):
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    def track(sock):
        with lock:
            sockets.append(sock)
            stopped = cancelled.is_set()
        if stopped:
            close_socket(sock)
            raise InterruptedError('REQUEST_STOPPED')
        return sock

    class OwnedConnection:
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            create_connection = self._create_connection
            def create(*args, **kwargs):
                if cancelled.is_set():
                    raise InterruptedError('REQUEST_STOPPED')
                return track(create_connection(*args, **kwargs))
            self._create_connection = create

    class Connection(OwnedConnection, http.client.HTTPConnection):
        pass

    class SecureConnection(OwnedConnection, http.client.HTTPSConnection):
        def connect(self):
            # Same TLS context and CONNECT target as HTTPSConnection. Register
            # the TLS socket before handshake so cancellation owns that wait too.
            http.client.HTTPConnection.connect(self)
            hostname = self._tunnel_host or self.host
            self.sock = track(self._context.wrap_socket(
                self.sock, server_hostname=hostname, do_handshake_on_connect=False))
            self.sock.do_handshake()

    class Handler(HTTPHandler):
        def http_open(self, req):
            return self.do_open(Connection, req)

    class SecureHandler(HTTPSHandler):
        def https_open(self, req):
            return self.do_open(SecureConnection, req, context=self._context)

    # Clone handlers because add_handler changes their parent. Mutating the
    # original opener would interfere with other concurrent task requests.
    local = OpenerDirector()
    for handler in director.handlers:
        if type(handler) is HTTPHandler:
            local.add_handler(Handler())
        elif type(handler) is HTTPSHandler:
            local.add_handler(SecureHandler(context=handler._context))
        elif isinstance(handler, (HTTPHandler, HTTPSHandler)):
            # An application-specific HTTP transport has its own semantics.
            return read(opener)
        else:
            local.add_handler(copy.copy(handler))
    local.addheaders = list(director.addheaders)

    def run():
        try:
            outcome['result'] = read(local.open)
        except BaseException as error:
            outcome['error'] = error
        finally:
            finished.set()

    interrupt()
    thread = threading.Thread(target=run, name='memo-owned-http')
    thread.start()
    try:
        while not finished.wait(0.1):
            interrupt()
    except BaseException:
        cancelled.set()
        with lock:
            owned_sockets = list(sockets)
        for sock in owned_sockets:
            close_socket(sock)
        # Once connect/read is unblocked, join before writing cancellation
        # receipts so no hidden request remains after the job has stopped.
        thread.join()
        raise
    thread.join()
    if 'error' in outcome:
        raise outcome['error']
    return outcome['result']
