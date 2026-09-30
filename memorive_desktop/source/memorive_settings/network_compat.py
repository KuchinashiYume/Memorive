"""Use the receiving computer's proxy and trusted TLS roots."""
import ctypes
from ctypes import wintypes as w
import os
import socket
import ssl
from urllib.request import getproxies, getproxies_environment, proxy_bypass
from urllib.parse import urlsplit


def system_auto_proxy(url):
    if os.name != 'nt':
        return None
    api = ctypes.WinDLL('winhttp', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    class Config(ctypes.Structure):
        _fields_ = [('auto',w.BOOL),('pac',ctypes.c_void_p),('proxy',ctypes.c_void_p),('bypass',ctypes.c_void_p)]
    class Options(ctypes.Structure):
        _fields_ = [('flags',w.DWORD),('detect',w.DWORD),('url',w.LPCWSTR),('reserved',ctypes.c_void_p),('reserved2',w.DWORD),('login',w.BOOL)]
    class Proxy(ctypes.Structure):
        _fields_ = [('access',w.DWORD),('proxy',ctypes.c_void_p),('bypass',ctypes.c_void_p)]
    api.WinHttpGetIEProxyConfigForCurrentUser.argtypes=[ctypes.POINTER(Config)]
    api.WinHttpGetIEProxyConfigForCurrentUser.restype=w.BOOL
    api.WinHttpOpen.argtypes=[w.LPCWSTR,w.DWORD,w.LPCWSTR,w.LPCWSTR,w.DWORD]
    api.WinHttpOpen.restype=ctypes.c_void_p
    api.WinHttpSetTimeouts.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int]
    api.WinHttpGetProxyForUrl.argtypes=[ctypes.c_void_p,w.LPCWSTR,ctypes.POINTER(Options),ctypes.POINTER(Proxy)]
    api.WinHttpGetProxyForUrl.restype=w.BOOL
    api.WinHttpCloseHandle.argtypes=[ctypes.c_void_p]
    kernel.GlobalFree.argtypes=[ctypes.c_void_p]
    kernel.GlobalFree.restype=ctypes.c_void_p
    config=Config();handle=None;result=Proxy()
    try:
        if not api.WinHttpGetIEProxyConfigForCurrentUser(ctypes.byref(config)):
            return None
        pac=ctypes.wstring_at(config.pac) if config.pac else None
        if not pac and not config.auto:
            return None
        handle=api.WinHttpOpen('Memorive/1.01',1,None,None,0)
        if not handle:
            raise ValueError('API_SYSTEM_PROXY_UNAVAILABLE')
        api.WinHttpSetTimeouts(handle,5000,5000,10000,10000)
        options=Options(2 if pac else 1,0 if pac else 3,pac,None,0,False)
        if not api.WinHttpGetProxyForUrl(handle,url,ctypes.byref(options),ctypes.byref(result)):
            error=ctypes.get_last_error()
            # Windows often enables automatic discovery on networks without a PAC.
            if not pac and error==12180:
                return None
            raise ValueError('API_SYSTEM_PROXY_RESOLUTION_FAILED')
        if result.access==1 or not result.proxy:
            return ''
        value=ctypes.wstring_at(result.proxy)
        entries=value.replace(';',' ').split()
        for entry in entries:
            if entry.lower().startswith('https='):
                return entry.split('=',1)[1]
        return next((s for s in entries if '=' not in s), '')
    finally:
        if handle:api.WinHttpCloseHandle(handle)
        for pointer in (config.pac,config.proxy,config.bypass,result.proxy,result.bypass):
            if pointer:kernel.GlobalFree(pointer)


def resolve_proxy(url, mode, address):
    if mode=='NONE':return ''
    if mode=='SYSTEM':
        host=urlsplit(url).hostname
        explicit=getproxies_environment().get('https','')
        # Explicit env/system proxy retains normal bypass semantics. If absent,
        # resolve a Windows PAC rather than incorrectly assuming direct access.
        if explicit:
            address='' if proxy_bypass(host) else explicit
        else:
            automatic=system_auto_proxy(url)
            if automatic is not None:
                address=automatic
            else:
                address='' if proxy_bypass(host) else getproxies().get('https','')
    elif mode!='CUSTOM':
        raise ValueError('API_VALIDATION_PROXY_MODE_INVALID')
    if not address:return ''
    if '://' not in address:address='http://'+address
    parts=urlsplit(address)
    if parts.scheme not in ('http','https') or not parts.hostname or parts.query or parts.fragment:
        raise ValueError('API_VALIDATION_PROXY_INVALID')
    return address


def tls_context():
    # Reuse the integrity-checked public roots already distributed with Memo.
    # OS/company roots remain present and hostname verification remains on.
    from .metadata_transport import public_metadata_context
    return public_metadata_context()


def error_code(error):
    cause=getattr(error,'reason',error)
    if isinstance(cause,ssl.SSLCertVerificationError):return 'API_TLS_CERTIFICATE_FAILED'
    if isinstance(cause,ssl.SSLError):return 'API_TLS_CONNECTION_FAILED'
    if isinstance(cause,socket.gaierror):return 'API_DNS_FAILED'
    if isinstance(cause,TimeoutError):return 'API_CONNECTION_TIMEOUT'
    if isinstance(cause,ConnectionRefusedError):return 'API_CONNECTION_REFUSED'
    if isinstance(error,ValueError) and str(error).startswith(('API_SYSTEM_PROXY_','API_VALIDATION_PROXY_')):
        return str(error)
    return 'API_NETWORK_OR_RESPONSE_FAILURE'
