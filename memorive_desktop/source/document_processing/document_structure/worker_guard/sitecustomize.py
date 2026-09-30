"""Inherited by every Python process in the isolated local inference job."""
import ipaddress
import os
import sys

def _audit(event, args):
    if event not in {'socket.connect', 'socket.getaddrinfo'}:
        return
    address = args[1] if event == 'socket.connect' else args[0]
    host = address[0] if isinstance(address, tuple) else address
    if isinstance(host, bytes):
        host = host.decode('ascii', errors='strict')
    if host in {'localhost', None}:
        return
    try:
        allowed = ipaddress.ip_address(host).is_loopback
    except ValueError:
        allowed = False
    if not allowed:
        path = os.environ.get('E10_NETWORK_DENIAL_LOG')
        if path:
            with open(path, 'a', encoding='utf-8') as stream:
                stream.write(event + ':non_loopback_denied\n')
        raise PermissionError('E10_WORKER_NON_LOOPBACK_NETWORK_DENIED')

if os.environ.get('E10_LOCAL_WORKER') == '1':
    sys.addaudithook(_audit)
