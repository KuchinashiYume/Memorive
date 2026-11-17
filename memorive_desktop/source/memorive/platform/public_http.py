"""Public HTTP helpers for fetching public metadata over HTTPS without credentials.

The settings' public metadata transport (memorive/desktop/settings/metadata_transport.py) and research
discovery's metadata transport (memorive/research/discovery/transport.py) both use them:
MetadataResponse (status, body and how the request went), MetadataRateLimitError, metadata_error_code
and transient_metadata_error (the error code of a failed fetch and whether it is worth trying again
later), curl_path (curl.exe in the Windows system folder) and curl_proxy (its --proxy value from the
proxy setting).
"""
from dataclasses import dataclass
import ctypes
from pathlib import Path
import re
import socket
import ssl
import subprocess
from urllib.parse import urlsplit
from urllib.request import getproxies, proxy_bypass


class MetadataRateLimitError(ValueError):
    def __init__(self, retry_after):
        super().__init__('EXTERNAL_METADATA_RATE_LIMITED')
        self.retry_after = retry_after


def metadata_error_code(error):
    cause = getattr(error, "reason", error)
    if isinstance(error, MetadataRateLimitError):
        return "EXTERNAL_METADATA_RATE_LIMITED"
    if isinstance(cause, ssl.SSLCertVerificationError):
        return "EXTERNAL_METADATA_TLS_CERTIFICATE"
    if isinstance(cause, (TimeoutError, subprocess.TimeoutExpired)):
        return "EXTERNAL_METADATA_TIMEOUT"
    if isinstance(cause, socket.gaierror):
        return 'EXTERNAL_METADATA_DNS_FAILED'
    if isinstance(cause, ConnectionError):
        return 'EXTERNAL_METADATA_CONNECTION_FAILED'
    if isinstance(cause, ssl.SSLError):
        return "EXTERNAL_METADATA_TLS_CONNECTION"
    import re
    if isinstance(error, ValueError) and re.fullmatch(r"[A-Z0-9_]+", str(error)):
        return str(error)
    return "EXTERNAL_METADATA_FETCH_FAILED"


def transient_metadata_error(reason):
    return reason in {'EXTERNAL_METADATA_TIMEOUT', 'EXTERNAL_METADATA_DNS_FAILED',
        'EXTERNAL_METADATA_CONNECTION_FAILED', 'EXTERNAL_METADATA_TLS_CONNECTION',
        'EXTERNAL_METADATA_FETCH_FAILED'} or bool(
        re.fullmatch(r'EXTERNAL_METADATA_HTTP_(408|425|429|5[0-9]{2})', reason))


@dataclass(frozen=True)
class MetadataResponse:
    status_code: int
    body: bytes
    transport_evidence: dict


def curl_path():
    """curl.exe in the Windows system folder; public metadata requests go through it (also research's transport.py)."""
    folder = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetSystemDirectoryW(folder, len(folder))
    if not 0 < length < len(folder):
        raise ValueError("EXTERNAL_METADATA_OS_TRANSPORT_UNAVAILABLE")
    path = Path(folder.value) / "curl.exe"
    if not path.is_file():
        raise ValueError("EXTERNAL_METADATA_OS_TRANSPORT_UNAVAILABLE")
    return path


def curl_proxy(mode, address, hostname):
    """The --proxy value for curl from the proxy setting: '' for none, the system's or the user's address."""
    if mode == "NONE":
        return ""
    if mode == "SYSTEM":
        address = "" if proxy_bypass(hostname) else getproxies().get("https", "")
    elif mode != "CUSTOM":
        raise ValueError("API_VALIDATION_PROXY_MODE_INVALID")
    if address:
        parts = urlsplit(address)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
            raise ValueError("API_VALIDATION_PROXY_INVALID")
    return address
