"""Loaded by every Python process that the page check starts (run.py puts this folder first on PYTHONPATH).

When MEMORIVE_PAGE_CHECK_OFFLINE is 1, a connection to any address other than this computer fails at once, so a
run records the page as it looks without network answers, the same in every run.
"""
import os

if os.environ.get("MEMORIVE_PAGE_CHECK_OFFLINE") == "1":
    import socket

    _connect = socket.socket.connect

    def _this_computer_only(self, address):
        if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "::1", "localhost"):
            raise OSError("page check: network limited to this computer")
        return _connect(self, address)

    socket.socket.connect = _this_computer_only
