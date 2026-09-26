from __future__ import annotations

import ipaddress
import socket
import time
from typing import Any, Callable, Iterable
import urllib.error
import urllib.parse
import urllib.request


TRACE_URL = "https://www.cloudflare.com/cdn-cgi/trace"
_TRACE_HOST = "www.cloudflare.com"
_MAX_TRACE_BYTES = 16_384


def parse_cloudflare_trace(value: str) -> dict[str, str]:
    if not isinstance(value, str):
        raise ValueError("NETWORK_TRACE_INVALID")
    fields: dict[str, str] = {}
    for raw_line in value.splitlines():
        if "=" not in raw_line:
            continue
        key, item = raw_line.split("=", 1)
        if key in {"ip", "loc", "colo"} and key not in fields:
            fields[key] = item.strip()
    try:
        address = str(ipaddress.ip_address(fields.get("ip", "")))
    except ValueError as error:
        raise ValueError("NETWORK_TRACE_IP_INVALID") from error
    country = fields.get("loc", "")
    colo = fields.get("colo", "")
    if country and (len(country) != 2 or not country.isalpha()):
        raise ValueError("NETWORK_TRACE_COUNTRY_INVALID")
    if colo and (not 2 <= len(colo) <= 8 or not colo.isalnum()):
        raise ValueError("NETWORK_TRACE_COLO_INVALID")
    return {
        "egress_ip": address,
        "country_code": country.upper(),
        "colo": colo.upper(),
    }


def discover_local_addresses() -> list[str]:
    candidates: list[str] = []

    def add(value: str) -> None:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            return
        if address.is_unspecified or address.is_multicast or address.is_loopback:
            return
        rendered = str(address)
        if rendered not in candidates:
            candidates.append(rendered)

    try:
        route_probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            route_probe.connect(("1.1.1.1", 443))
            add(str(route_probe.getsockname()[0]))
        finally:
            route_probe.close()
    except OSError:
        pass
    try:
        for row in socket.getaddrinfo(socket.gethostname(), None, type=socket.SOCK_STREAM):
            add(str(row[4][0]))
    except OSError:
        pass
    return candidates


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise urllib.error.HTTPError(req.full_url, code, "NETWORK_REDIRECT_FORBIDDEN", headers, fp)


def _validate_proxy(mode: str, address: str) -> tuple[str, str]:
    accepted_mode = str(mode).upper()
    if accepted_mode not in {"SYSTEM", "NONE", "CUSTOM"}:
        raise ValueError("NETWORK_PROXY_MODE_INVALID")
    accepted_address = str(address).strip()
    if accepted_mode == "CUSTOM":
        parsed = urllib.parse.urlsplit(accepted_address)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise ValueError("NETWORK_PROXY_ADDRESS_INVALID")
        if parsed.username or parsed.password or parsed.fragment:
            raise ValueError("NETWORK_PROXY_ADDRESS_INVALID")
    return accepted_mode, accepted_address


def _open_url(
    request: urllib.request.Request,
    *,
    timeout: float,
    proxy_mode: str,
    proxy_address: str,
):
    if proxy_mode == "NONE":
        proxy_handler = urllib.request.ProxyHandler({})
    elif proxy_mode == "CUSTOM":
        proxy_handler = urllib.request.ProxyHandler({"http": proxy_address, "https": proxy_address})
    else:
        proxy_handler = urllib.request.ProxyHandler()
    opener = urllib.request.build_opener(proxy_handler, _NoRedirect())
    return opener.open(request, timeout=timeout)


def run_network_diagnostic(
    *,
    proxy_mode: str,
    proxy_address: str,
    timeout_seconds: int | float,
    open_url: Callable[..., Any] = _open_url,
    local_address_provider: Callable[[], Iterable[str]] = discover_local_addresses,
) -> dict[str, Any]:
    mode, address = _validate_proxy(proxy_mode, proxy_address)
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise ValueError("NETWORK_TIMEOUT_INVALID")
    timeout = float(timeout_seconds)
    if not 1.0 <= timeout <= 600.0:
        raise ValueError("NETWORK_TIMEOUT_INVALID")

    local_addresses: list[str] = []
    for candidate in local_address_provider():
        try:
            rendered = str(ipaddress.ip_address(str(candidate)))
        except ValueError:
            continue
        if rendered not in local_addresses:
            local_addresses.append(rendered)

    request = urllib.request.Request(
        TRACE_URL,
        method="GET",
        headers={
            "Accept": "text/plain",
            "Cache-Control": "no-store",
            "User-Agent": "PR-OS-Network-Diagnostic/1.0",
        },
    )
    started = time.monotonic()
    with open_url(
        request,
        timeout=timeout,
        proxy_mode=mode,
        proxy_address=address,
    ) as response:
        status_code = int(getattr(response, "status", 200))
        final_url = str(response.geturl())
        parsed_final = urllib.parse.urlsplit(final_url)
        if (
            parsed_final.scheme.lower() != "https"
            or (parsed_final.hostname or "").lower() != _TRACE_HOST
            or parsed_final.path != "/cdn-cgi/trace"
            or parsed_final.query
            or parsed_final.fragment
        ):
            raise ValueError("NETWORK_TRACE_FINAL_URL_INVALID")
        if status_code != 200:
            raise ValueError("NETWORK_TRACE_HTTP_STATUS_INVALID")
        raw = response.read(_MAX_TRACE_BYTES + 1)
    if len(raw) > _MAX_TRACE_BYTES:
        raise ValueError("NETWORK_TRACE_RESPONSE_TOO_LARGE")
    try:
        trace = parse_cloudflare_trace(raw.decode("ascii", errors="strict"))
    except UnicodeDecodeError as error:
        raise ValueError("NETWORK_TRACE_ENCODING_INVALID") from error
    elapsed_ms = max(0, int(round((time.monotonic() - started) * 1000)))
    return {
        "schema_version": "P08NetworkDiagnosticReceipt-v1",
        "status": "PASS",
        "local_ip": local_addresses[0] if local_addresses else None,
        "local_addresses": local_addresses,
        **trace,
        "proxy_mode": mode,
        "trace_endpoint": _TRACE_HOST,
        "elapsed_ms": elapsed_ms,
        "external_network_calls": 1,
        "provider_calls": 0,
        "external_model_calls": 0,
        "credential_value_reads": 0,
    }


__all__ = [
    "TRACE_URL",
    "discover_local_addresses",
    "parse_cloudflare_trace",
    "run_network_diagnostic",
]
