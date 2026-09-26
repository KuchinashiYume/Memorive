"""Strict public metadata TLS, scoped independently from model/API transports."""
from dataclasses import dataclass
import ctypes
from datetime import datetime, timezone
import hashlib
import os
import ipaddress
import socket
from pathlib import Path
import re
import ssl
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener, getproxies, proxy_bypass
from .model_validation import UrllibHttpTransport, _NoRedirect

ECB_ENDPOINT = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
PUBLIC_ROOTS_PATH = ((Path(sys._MEIPASS) / 'source/memorive_settings/public_roots.pem')
    if getattr(sys, 'frozen', False) else Path(__file__).with_name('public_roots.pem'))
PUBLIC_ROOTS_SHA256 = 'bbc7e9c01d7551bb8a159b5dedd989b8ee3ce105aff522b68eb1b01bf854cab0'
_LIVEBENCH_BASE = 'https://raw.githubusercontent.com/LiveBench/new-livebench/main/'
LIVEBENCH_GITHUB_API = 'https://api.github.com/repos/LiveBench/new-livebench'
GITHUB_API_VERSION = '2026-03-10'


class MetadataRateLimitError(ValueError):
    def __init__(self, retry_after):
        super().__init__('EXTERNAL_METADATA_RATE_LIMITED')
        self.retry_after = retry_after


def metadata_rate_limit(response, *, now=None):
    """Return a durable backoff projection for an explicit HTTP limit."""
    status = getattr(response, 'status_code', None)
    evidence = getattr(response, 'transport_evidence', {}) or {}
    headers = evidence.get('response_headers') or {}
    remaining = headers.get('x-ratelimit-remaining')
    if status not in {403, 429} or (status == 403 and remaining != '0'):
        return None
    current = int(time.time() if now is None else now)
    reset = headers.get('x-ratelimit-reset')
    retry_seconds = headers.get('retry-after')
    try:
        retry_epoch = int(reset) if reset is not None else current + int(retry_seconds)
    except (TypeError, ValueError):
        retry_epoch = current + 60
    retry_epoch = max(current + 1, retry_epoch)
    return {
        'status': 'RATE_LIMITED',
        'reason': 'EXTERNAL_METADATA_RATE_LIMITED',
        'retry_after': datetime.fromtimestamp(retry_epoch, timezone.utc).isoformat(),
        'rate_limit_reset': retry_epoch,
    }


def is_livebench_github_metadata(url):
    return url == LIVEBENCH_GITHUB_API + '/commits/main' or bool(re.fullmatch(
        re.escape(LIVEBENCH_GITHUB_API) + r'/contents/(?:src/lib/constants\.js|public/categories_\d{4}_\d{2}_\d{2}\.json|public/table_\d{4}_\d{2}_\d{2}\.csv)\?ref=[0-9a-f]{40}', url))

def is_builtin_public_metadata(url):
    from .reference_pricing import is_endpoint_url
    if is_endpoint_url(url):
        return True
    if is_livebench_github_metadata(url):
        return True
    if url in {'https://epoch.ai/data/benchmark_data.zip', 'https://openrouter.ai/api/v1/models',
               _LIVEBENCH_BASE + 'src/lib/constants.js'}:
        return True
    return bool(re.fullmatch(re.escape(_LIVEBENCH_BASE) +
        r'public/(?:categories_\d{4}_\d{2}_\d{2}\.json|table_\d{4}_\d{2}_\d{2}\.csv)', url))

def public_metadata_context():
    # Byte-exact Mozilla public roots, with distribution/license provenance.
    # Preserve OS roots and strict defaults; never install roots system-wide.
    try:
        bundle = PUBLIC_ROOTS_PATH.read_bytes()
    except OSError as error:
        raise ValueError('EXTERNAL_METADATA_PUBLIC_ROOTS_UNAVAILABLE') from error
    if hashlib.sha256(bundle).hexdigest() != PUBLIC_ROOTS_SHA256:
        raise ValueError('EXTERNAL_METADATA_PUBLIC_ROOTS_INTEGRITY')
    context = ssl.create_default_context()
    context.load_verify_locations(cadata=bundle.decode('ascii'))
    return context

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

class PublicMetadataHttpTransport(UrllibHttpTransport):
    @staticmethod
    def owns_public_route(url):
        return is_builtin_public_metadata(url) or url == ECB_ENDPOINT

    def _public_route(self, url, mode, address):
        if not self.owns_public_route(url):
            raise ValueError('EXTERNAL_METADATA_ENDPOINT_NOT_PINNED')
        host = urlsplit(url).hostname
        from .network_compat import resolve_proxy
        proxy = resolve_proxy(url, mode, address)
        if proxy:
            # CONNECT sends the exact allowlisted hostname to the configured
            # proxy. Local Fake-IP answers are not the destination of this route.
            return proxy, 'PINNED_HTTPS_PROXY_REMOTE_DNS'
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses:
            raise ValueError('EXTERNAL_METADATA_DNS_FAILED')
        ips = [ipaddress.ip_address(x[4][0]) for x in addresses]
        fake_range = ipaddress.ip_network('198.18.0.0/15')
        if any(not ip.is_global and ip not in fake_range for ip in ips):
            raise ValueError('EXTERNAL_SOURCE_NONPUBLIC_ADDRESS')
        if any(ip in fake_range for ip in ips):
            # TUN Fake-IP is a routing token, not a private source endpoint.
            # Only exact built-in HTTPS URLs enter here; TLS still verifies
            # their original hostname and no redirects/credentials are allowed.
            return '', 'PINNED_HTTPS_TUN_FAKE_IP'
        return '', 'PUBLIC_DNS_DIRECT'

    def _public_request(self, **kwargs):
        if kwargs['method'] != 'GET' or kwargs.get('body') is not None:
            raise ValueError('EXTERNAL_METADATA_PUBLIC_GET_REQUIRED')
        headers = kwargs.get('headers', {})
        for name, value in headers.items():
            if name.lower() == 'x-github-api-version' and is_livebench_github_metadata(kwargs['url']):
                if value != GITHUB_API_VERSION:
                    raise ValueError('EXTERNAL_METADATA_API_VERSION_INVALID')
                continue
            if name.lower() not in {'accept', 'user-agent'}:
                raise ValueError('EXTERNAL_METADATA_PUBLIC_CREDENTIAL_FORBIDDEN')
            if not isinstance(value, str) or '\r' in value or '\n' in value:
                raise ValueError('EXTERNAL_METADATA_HEADER_INVALID')
        limit = kwargs.get('max_response_bytes')
        if limit is not None and (type(limit) is not int or limit <= 0):
            raise ValueError('API_RESPONSE_LIMIT_INVALID')
        mode = kwargs['proxy_mode']
        address, route = self._public_route(kwargs['url'], mode, kwargs['proxy_address'])
        # Resolve the route once: no second SYSTEM lookup / bypass decision may
        # silently convert the validated CONNECT request into a direct request.
        opener = build_opener(ProxyHandler({}), _NoRedirect(), HTTPSHandler(context=public_metadata_context()))
        request = Request(kwargs['url'], headers=dict(headers), method='GET')
        if address:
            parts = urlsplit(address)
            request.add_unredirected_header('Host', urlsplit(kwargs['url']).netloc)
            request.set_proxy(parts.netloc, parts.scheme)
        evidence = {'id': 'OPENSSL_OS_AND_MOZILLA_PUBLIC_ROOTS', 'tls_verified': True,
            'hostname_verified': True, 'redirects_followed': False, 'proxy_mode': mode,
            'destination_policy': route, 'proxy_used': bool(address),
            'public_roots_sha256': PUBLIC_ROOTS_SHA256, 'public_roots_version': 'certifi 2026.6.17'}
        def response_value(response, status):
            payload = response.read() if limit is None else response.read(limit + 1)
            if limit is not None and len(payload) > limit:
                raise ValueError('EXTERNAL_RESPONSE_ENVELOPE_EXCEEDED')
            details = dict(evidence)
            if is_livebench_github_metadata(kwargs['url']):
                safe_headers = {}
                for key in ('X-RateLimit-Limit', 'X-RateLimit-Remaining', 'X-RateLimit-Reset', 'Retry-After', 'X-GitHub-Api-Version-Selected'):
                    value = getattr(response, 'headers', {}).get(key)
                    if isinstance(value, str) and re.fullmatch(r'[0-9-]+', value):
                        safe_headers[key.lower()] = value
                details.update(api_version=GITHUB_API_VERSION, response_headers=safe_headers)
            return MetadataResponse(status, payload, details)
        try:
            with opener.open(request, timeout=kwargs['timeout_seconds']) as response:
                return response_value(response, int(response.status))
        except HTTPError as error:
            with error:
                return response_value(error, int(error.code))

    @staticmethod
    def _curl_path():
        folder = ctypes.create_unicode_buffer(32768)
        length = ctypes.windll.kernel32.GetSystemDirectoryW(folder, len(folder))
        if not 0 < length < len(folder):
            raise ValueError("EXTERNAL_METADATA_OS_TRANSPORT_UNAVAILABLE")
        path = Path(folder.value) / "curl.exe"
        if not path.is_file():
            raise ValueError("EXTERNAL_METADATA_OS_TRANSPORT_UNAVAILABLE")
        return path

    @staticmethod
    def _proxy(mode, address, hostname):
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

    def request(self, **kwargs):
        if is_builtin_public_metadata(kwargs['url']):
            return self._public_request(**kwargs)
        if os.name != "nt" or kwargs["url"] != ECB_ENDPOINT:
            return super().request(**kwargs)
        if kwargs["method"] != "GET" or kwargs.get("body") is not None:
            raise ValueError("EXTERNAL_METADATA_PUBLIC_GET_REQUIRED")
        headers = kwargs.get("headers", {})
        if any(k.lower() not in {"accept", "user-agent"} for k in headers):
            raise ValueError("EXTERNAL_METADATA_PUBLIC_CREDENTIAL_FORBIDDEN")
        timeout = kwargs["timeout_seconds"]
        if type(timeout) is not int or not 1 <= timeout <= 600:
            raise ValueError("EXTERNAL_METADATA_TIMEOUT_INVALID")
        limit = kwargs.get("max_response_bytes")
        if limit is not None and (type(limit) is not int or limit <= 0):
            raise ValueError("API_RESPONSE_LIMIT_INVALID")
        limit = min(limit or 262144, 262144)
        proxy, route = self._public_route(kwargs['url'], kwargs['proxy_mode'], kwargs['proxy_address'])
        args = [str(self._curl_path()), "--disable", "--silent", "--show-error",
                "--proto", "=https", "--proto-redir", "=https", "--max-redirs", "0", "--ca-native",
                "--connect-timeout", str(min(timeout,10)), "--max-time", str(timeout),
                "--max-filesize", str(limit), "--proxy", proxy, "--noproxy", ""]
        for name, value in headers.items():
            if not isinstance(value, str) or "\r" in value or "\n" in value:
                raise ValueError("EXTERNAL_METADATA_HEADER_INVALID")
            args.extend(["--header", name + ": " + value])
        args.extend(["--write-out", "\n%{http_code}|%{ssl_verify_result}", ECB_ENDPOINT])
        # Do not read curlrc or CA-file overrides; Schannel uses the trusted OS store.
        env = {k:v for k,v in os.environ.items() if k.upper() not in
               {"CURL_CA_BUNDLE", "SSL_CERT_FILE", "SSL_CERT_DIR"}}
        try:
            response = subprocess.run(args, capture_output=True, timeout=timeout+3,
                                      creationflags=subprocess.CREATE_NO_WINDOW, env=env, shell=False)
        except subprocess.TimeoutExpired as error:
            raise ValueError("EXTERNAL_METADATA_TIMEOUT") from error
        except OSError as error:
            raise ValueError("EXTERNAL_METADATA_OS_TRANSPORT_UNAVAILABLE") from error
        if response.returncode:
            codes = {28:"EXTERNAL_METADATA_TIMEOUT", 51:"EXTERNAL_METADATA_TLS_CERTIFICATE",
                     58:"EXTERNAL_METADATA_TLS_CERTIFICATE", 60:"EXTERNAL_METADATA_TLS_CERTIFICATE",
                     77:"EXTERNAL_METADATA_TLS_CERTIFICATE", 35:"EXTERNAL_METADATA_TLS_CONNECTION",
                     63:"EXTERNAL_RESPONSE_ENVELOPE_EXCEEDED", 5:"EXTERNAL_METADATA_PROXY_UNAVAILABLE",
                     6:"EXTERNAL_METADATA_DNS_FAILED", 7:"EXTERNAL_METADATA_CONNECTION_FAILED"}
            raise ValueError(codes.get(response.returncode, "EXTERNAL_METADATA_FETCH_FAILED"))
        body, marker, suffix = response.stdout.rpartition(b"\n")
        try:
            status, verified = map(int, suffix.split(b"|"))
        except (ValueError, TypeError) as error:
            raise ValueError("EXTERNAL_METADATA_TRANSPORT_RESULT_INVALID") from error
        if not marker or not 100 <= status <= 599 or verified != 0:
            raise ValueError("EXTERNAL_METADATA_TLS_CERTIFICATE")
        if len(body) > limit:
            raise ValueError("EXTERNAL_RESPONSE_ENVELOPE_EXCEEDED")
        return MetadataResponse(status, body, {"id":"WINDOWS_SCHANNEL", "tls_verified":True,
                                               "hostname_verified":True, "destination_policy":route,
                                               "proxy_used":bool(proxy), "redirects_followed":False,
                                               "proxy_mode":kwargs["proxy_mode"]})
