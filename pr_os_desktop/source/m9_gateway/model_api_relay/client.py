from __future__ import annotations

import ipaddress
import json
import socket
import time
from typing import Any, Callable, Mapping
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from m9_gateway.execution_core.contracts import canonical_sha256

from .contracts import (
    PROFILE_FIELDS,
    RelayContractError,
    ResolvedEndpointProfile,
    validate_credential_binding,
    validate_endpoint_profile,
)
from .protocols import RelayProtocolRegistry


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _default_opener():
    return urllib.request.build_opener(_NoRedirect())


def _public_resolution(host: str, port: int, resolver) -> tuple[str, ...]:
    try:
        rows = resolver(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise RelayContractError("DNS_RESOLUTION_FAILED", [host]) from exc
    addresses = []
    for row in rows:
        address = row[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise RelayContractError("DNS_ADDRESS_INVALID") from exc
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            raise RelayContractError("DNS_PRIVATE_ADDRESS_FORBIDDEN", [host])
        addresses.append(ip.compressed)
    if not addresses:
        raise RelayContractError("DNS_NO_ADDRESS", [host])
    return tuple(sorted(set(addresses)))


class SafeRelayClient:
    """Single-attempt, no-redirect, bounded relay HTTP client.

    Credential values exist only while constructing the request header.  They
    are never returned, hashed, measured or included in an exception.
    """

    def __init__(
        self,
        *,
        protocols: RelayProtocolRegistry | None = None,
        opener=None,
        environ: Mapping[str, str] | None = None,
        resolver: Callable[..., Any] = socket.getaddrinfo,
        monotonic: Callable[[], float] = time.monotonic,
        timeout_seconds: int = 60,
        competing_credential_refs: tuple[str, ...] = (),
    ):
        self.protocols = protocols or RelayProtocolRegistry()
        self.opener = opener or _default_opener()
        self.environ = environ if environ is not None else __import__("os").environ
        self.resolver = resolver
        self.monotonic = monotonic
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
            raise RelayContractError("TIMEOUT_INVALID")
        self.timeout_seconds = timeout_seconds
        self.competing_credential_refs = tuple(competing_credential_refs)

    def invoke(self, resolved: ResolvedEndpointProfile, slot, payload: dict, *, capability: str = "chat") -> dict[str, Any]:
        profile = resolved.profile
        checked_profile = validate_endpoint_profile(
            {key: value for key, value in profile.items() if key in PROFILE_FIELDS}
        )
        if checked_profile["profile_fingerprint"] != profile.get("profile_fingerprint"):
            raise RelayContractError("PROFILE_FINGERPRINT_DRIFT")
        checked_binding = validate_credential_binding(resolved.credential_binding, checked_profile)
        if capability not in profile["capabilities"]:
            raise RelayContractError("PROFILE_CAPABILITY_MISMATCH", [capability])
        credential_ref = checked_binding["credential_ref"]
        competitors = sorted(
            ref for ref in self.competing_credential_refs
            if ref != credential_ref and isinstance(self.environ.get(ref), str) and self.environ.get(ref)
        )
        if competitors:
            raise RelayContractError("COMPETING_CREDENTIAL_BINDING", competitors)
        secret = self.environ.get(credential_ref)
        if not isinstance(secret, str) or not secret:
            raise RelayContractError("CREDENTIAL_NOT_PRESENT", [credential_ref])
        parsed_origin = urlsplit(profile["origin"])
        if profile["transport_kind"] != "SELF_HOSTED_LOOPBACK_RELAY":
            addresses = _public_resolution(
                parsed_origin.hostname or "",
                parsed_origin.port or 443,
                self.resolver,
            )
        else:
            addresses = (parsed_origin.hostname or "127.0.0.1",)
        prepared = self.protocols.build_request(profile["protocol_family"], profile, payload)
        url = profile["origin"] + prepared.path
        parsed_url = urlsplit(url)
        url_origin = f"{parsed_url.scheme}://{parsed_url.netloc}".rstrip("/")
        if url_origin != profile["origin"] or not parsed_url.path.startswith("/"):
            raise RelayContractError("REQUEST_ORIGIN_ESCAPE")
        body = json.dumps(prepared.body, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(url, data=body, method="POST")
        request.add_header("Content-Type", "application/json")
        allowed_headers = {name.lower() for name in resolved.protocol_manifest["allowed_headers"]}
        for name, value in prepared.headers.items():
            if name.lower() not in allowed_headers:
                raise RelayContractError("PROTOCOL_HEADER_NOT_ALLOWED", [name])
            request.add_header(name, value)
        if checked_binding["auth_scheme"] == "Bearer":
            request.add_header(checked_binding["header_name"], f"Bearer {secret}")
        else:
            request.add_header(checked_binding["header_name"], secret)
        started = self.monotonic()
        try:
            if hasattr(self.opener, "open"):
                response_context = self.opener.open(request, timeout=self.timeout_seconds)
            else:
                response_context = self.opener(request, timeout=self.timeout_seconds)
            with response_context as response:
                status_value = getattr(response, "status", None)
                status = int(status_value if status_value is not None else response.getcode())
                content_type = response.headers.get("Content-Type", "")
                response_headers = dict(response.headers.items()) if hasattr(response.headers, "items") else dict(response.headers)
                limit = resolved.protocol_manifest["max_response_bytes"]
                raw = response.read(limit + 1)
        except urllib.error.HTTPError as exc:
            if 300 <= exc.code < 400:
                raise RelayContractError("REDIRECT_FORBIDDEN", [str(exc.code)]) from None
            raise RelayContractError(f"HTTP_{exc.code}") from None
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, (TimeoutError, socket.timeout)):
                code = "TRANSPORT_TIMEOUT"
            elif isinstance(reason, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
                code = "TRANSPORT_DISCONNECT"
            else:
                code = "TRANSPORT_ERROR"
            raise RelayContractError(code, [type(reason).__name__]) from None
        except (TimeoutError, socket.timeout):
            raise RelayContractError("TRANSPORT_TIMEOUT") from None
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            raise RelayContractError("TRANSPORT_DISCONNECT") from None
        latency_ms = max(0.0, (self.monotonic() - started) * 1000.0)
        if len(raw) > resolved.protocol_manifest["max_response_bytes"]:
            raise RelayContractError("RESPONSE_OVERSIZE")
        expected_count = None
        if capability == "embedding":
            expected_count = len(payload.get("input") or [])
        elif capability == "rerank":
            expected_count = len(payload.get("documents") or [])
        parsed = self.protocols.parse_response(
            profile["protocol_family"], profile, status, content_type, raw,
            expected_count=expected_count,
            response_headers=response_headers,
            output_schema=payload.get("output_schema") or payload.get("response_schema"),
        )
        if capability == "vision_ocr":
            # Preserve the existing production OCR normalization contract when
            # the same SiliconFlow model is reached through a relay profile.
            from m9_gateway.providers.siliconflow import _normalize_deepseek_ocr_markdown

            parsed["text"] = _normalize_deepseek_ocr_markdown(parsed.get("text"))
        return {
            **parsed,
            "task_type": slot.task_type,
            "provider": profile["endpoint_operator"],
            "profile_id": profile["profile_id"],
            "profile_fingerprint": profile["profile_fingerprint"],
            "protocol_family": profile["protocol_family"],
            "transport_kind": profile["transport_kind"],
            "claimed_upstream_provider": profile["claimed_upstream_provider"],
            "route": profile["route_id"],
            "region": profile["region"],
            "egress": profile["egress"],
            "resolved_address_set_hash": canonical_sha256(list(addresses)),
            "request_body_hash": canonical_sha256(prepared.body),
            "response_body_hash": __import__("hashlib").sha256(raw).hexdigest().upper(),
            "latency_ms": latency_ms,
        }
