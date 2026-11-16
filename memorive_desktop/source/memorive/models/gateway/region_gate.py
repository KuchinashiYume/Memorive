"""The region check before an overseas API is called: whether this computer's exit IP is in mainland China.

Pure logic: no configuration, no SDK, no key, no logging. It takes a SlotConfig and always returns a
dict; it does not raise to say "blocked". gateway.ModelGateway looks up the slot, and call, embed,
rerank and ocr all require this check's result before they send an overseas request.

Premises:
- Only an allowed HTTPS geo source is used, and the geo URL and the provider's endpoint must
  resolve, through urllib.request's default opener, to the same explicit proxy route. A direct or
  TUN route, a PAC split route, a bypass or an unknown system route cannot prove the same exit, so
  the check fails closed. Before the request is sent, the receipt is checked again against the
  current proxy state; a changed route blocks it.
- A passed region does not mean the key works, has quota or a subscription; the connection test
  after the check finds that out.

It only judges the region: no key checks, balances, subscriptions, error classes or proxy
switching. Domestic APIs are skipped. An overseas request may be sent only when ok is true and
gated is false.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from datetime import datetime
from urllib.parse import urlsplit

# The overseas providers, by slot.provider in models.yaml: GPT is openai, Gemini google, Claude
# anthropic. A new overseas provider is added here. Domestic (deepseek, siliconflow), local (ollama,
# chroma), human and placeholder slots are not in the set and are skipped.
_OVERSEAS_PROVIDERS = frozenset({"openai", "google", "anthropic"})

# The geo source: only the fixed allowlist; the environment variable can choose one, not add one.
_ENV_GEO_URL = "MEMORIVE_MODEL_GATEWAY_GEO_URL"     # chooses the source without a code change
_DEFAULT_GEO_URL = None  # fail closed: an explicit HTTPS source is required
_GEO_TIMEOUT = 3                     # seconds, enough for a round trip through a proxy
_TRUSTED_GEO_SOURCES = {
    "https://www.cloudflare.com/cdn-cgi/trace": "CLOUDFLARE_CDN_CGI_TRACE_V1",
}

# reason values for code to compare; the user-facing text goes into message.
_R_SKIP = "domestic_skip"
_R_OK = "region_ok"
_R_BLOCKED = "region_cn_blocked"
_R_UNKNOWN = "region_unknown"


def _now_iso() -> str:
    """Local time with its offset, to the second, as the card state log and the runtime log write it
    (datetime.now().astimezone(), not UTC), so one log never mixes two time forms."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _is_overseas(provider: str | None) -> bool:
    return (provider or "") in _OVERSEAS_PROVIDERS


def _geo_url() -> str | None:
    return os.environ.get(_ENV_GEO_URL) or _DEFAULT_GEO_URL


def _normalized_https_url(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        parts = urlsplit(raw.strip())
        hostname = parts.hostname
        port_number = parts.port
    except (TypeError, ValueError):
        return None
    if (
        parts.scheme.lower() != "https"
        or not hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        return None
    host = hostname.lower()
    port = "" if port_number in (None, 443) else f":{port_number}"
    path = parts.path or "/"
    return f"https://{host}{port}{path}"


def _trusted_geo_source() -> dict | None:
    url = _normalized_https_url(_geo_url())
    if url not in _TRUSTED_GEO_SOURCES:
        return None
    source_name = _TRUSTED_GEO_SOURCES[url]
    return {
        "url": url,
        "host": urlsplit(url).hostname,
        "geo_source_id": hashlib.sha256(f"{source_name}|{url}".encode("utf-8")).hexdigest().upper(),
    }


def _proxy_route_id(url: str) -> str | None:
    """Return a credential-free identity for the explicit proxy consumed by urllib.

    A direct/TUN path is intentionally NOT attestable here and returns None. Proxy
    credentials are neither read into the descriptor nor returned in evidence.
    """
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        scheme = parts.scheme.lower()
    except (TypeError, ValueError):
        return None
    try:
        if urllib.request.proxy_bypass(host):
            return None
    except Exception:
        return None
    try:
        proxies = urllib.request.getproxies()
        raw_proxy = proxies.get(scheme) or proxies.get("all")
    except Exception:
        return None
    if not raw_proxy:
        return None
    try:
        candidate = raw_proxy if "://" in raw_proxy else f"http://{raw_proxy}"
        proxy = urlsplit(candidate)
        proxy_hostname = proxy.hostname
        proxy_port = proxy.port
    except (TypeError, ValueError):
        return None
    if not proxy_hostname:
        return None
    descriptor = {
        "mode": "EXPLICIT_PROXY",
        "scheme": (proxy.scheme or "http").lower(),
        "host": proxy_hostname.lower(),
        "port": proxy_port,
    }
    raw = json.dumps(descriptor, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest().upper()


def _parse_region(body: str) -> str | None:
    """The two-letter country code from a geo reply, upper case, or None. Reads JSON (ip-api.com's
    countryCode, ipinfo.io's country) and line traces (Cloudflare cdn-cgi/trace, loc=XX)."""
    body = (body or "").strip()
    try:
        data = json.loads(body)
        if isinstance(data, dict):
            # countryCode first (ip-api's country is a full name such as "China"); country only when
            # there is no code field (ipinfo)
            code = data.get("countryCode") or data.get("country_code") or data.get("country")
            if isinstance(code, str) and code.strip():
                normalized = code.strip().upper()
                if normalized in {"CHINA", "PEOPLE'S REPUBLIC OF CHINA", "PRC"}:
                    return "CN"
                return normalized if len(normalized) == 2 and normalized.isalpha() else None
    except Exception:
        pass
    for line in body.splitlines():           # Cloudflare trace: a line loc=XX
        if line.startswith("loc="):
            code = line[4:].strip().upper()
            return code if len(code) == 2 and code.isalpha() else None
    return None


def _route_context(slot) -> dict:
    source = _trusted_geo_source()
    endpoint = _normalized_https_url(getattr(slot, "endpoint", "") or "")
    geo_route_id = _proxy_route_id(source["url"]) if source else None
    provider_route_id = _proxy_route_id(endpoint) if endpoint else None
    same_explicit_route = bool(
        source and endpoint and geo_route_id and provider_route_id and geo_route_id == provider_route_id
    )
    seed = {
        "task_type": _slot_task(slot),
        "provider": getattr(slot, "provider", "") or "",
        "provider_endpoint": endpoint,
        "geo_source_id": source["geo_source_id"] if source else None,
        "egress_route_id": geo_route_id if same_explicit_route else None,
    }
    binding = hashlib.sha256(
        json.dumps(seed, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest().upper()
    return {
        "valid": same_explicit_route,
        "geo_provider": source["host"] if source else None,
        "geo_source_id": source["geo_source_id"] if source else None,
        "geo_url": source["url"] if source else None,
        "provider_endpoint": endpoint,
        "egress_route_id": geo_route_id if same_explicit_route else None,
        "route_binding": binding,
    }


def _route_binding(slot) -> str:
    return _route_context(slot)["route_binding"]


def _default_probe(slot) -> dict:
    """Look up the exit IP's country code and return evidence bound to the route.

    Uses urllib.request.urlopen's default opener, the same as the provider adapters (the same
    HTTP(S)_PROXY or system proxy), so the geo source and the API share the exit. Any failure (no URL,
    not HTTPS, URLError, timeout, unreadable reply, no country code) returns evidence that does not
    authorize the call. Never raises; never returns the full IP, only the country code."""
    context = _route_context(slot)
    url = context["geo_url"]
    if not context["valid"] or not url:
        return {"country_code": None, "geo_provider": None, "transport": None,
                "geo_source_id": context["geo_source_id"],
                "egress_route_id": context["egress_route_id"],
                "route_binding": context["route_binding"]}
    parts = urlsplit(url)
    host = parts.hostname
    try:
        with urllib.request.urlopen(url, timeout=_GEO_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", "replace")
        return {"country_code": _parse_region(body), "geo_provider": host, "transport": "https",
                "geo_source_id": context["geo_source_id"],
                "egress_route_id": context["egress_route_id"],
                "route_binding": context["route_binding"]}
    except Exception:
        return {"country_code": None, "geo_provider": host, "transport": "https",
                "geo_source_id": context["geo_source_id"],
                "egress_route_id": context["egress_route_id"],
                "route_binding": context["route_binding"]}


def _slot_task(slot) -> str:
    return getattr(slot, "task_type", None) or "?"


def _slot_api(slot) -> str:
    return getattr(slot, "provider", None) or _slot_task(slot) or "该境外 API"


def _gated_message(slot, region: str) -> str:
    api = _slot_api(slot)
    model = getattr(slot, "model_id", None) or api
    return (
        f"[MODEL_GATEWAY/region_gate/{_slot_task(slot)}] 区域预检:当前出口 IP 属中国大陆({region}),"
        f"{api}({model})不对中国大陆开放;为避免明知被地区拦仍撞一次 403 再误判 key,"
        f"已挡下、未发起该 API 请求。请开加速器 TUN/代理、把出口切到支持地区(如日本 JP、新加坡 SG)后重试。"
        f"(注:本门只判出口区域,区域通过 ≠ key 有效。)"
    )


def _pass_message(slot, region: str) -> str:
    return (
        f"[MODEL_GATEWAY/region_gate/{_slot_task(slot)}] 区域预检通过(出口 {region},非中国大陆);"
        f"注:区域通过 ≠ key 有效,key 是否可调由后续实测判定。"
    )


def _unknown_message(slot) -> str:
    return (
        f"[MODEL_GATEWAY/region_gate/{_slot_task(slot)}] 区域预检:未确认出口区域,因此未放行测试;"
        f"请配置可信 HTTPS geo 源并取得与当前 slot route 绑定的二字母国家码后重试。"
    )


def preflight_for_slot(slot, *, probe=None) -> dict:
    """The check for one SlotConfig, steps A to E below; always returns a dict, never raises to say
    "mainland" or "unknown".

    probe: the exit lookup, (slot) -> evidence dict, replacing _default_probe (tests pass a fake
           one so no network is used).

    The dict has at least ok, gated, region, reason, message, skipped, provider, geo_provider and
    checked_at; for an overseas slot also transport, route_binding and evidence_valid. It holds no
    key and no full IP: region is a country code, geo_provider a host.
    """
    provider = getattr(slot, "provider", None)
    checked_at = _now_iso()

    # A: not overseas (domestic, local, human, placeholder): skip, without a lookup.
    if not _is_overseas(provider):
        return {"ok": True, "gated": False, "region": None, "reason": _R_SKIP,
                "message": None, "skipped": True, "provider": provider,
                "geo_provider": None, "checked_at": checked_at}

    # B: look up the exit region (probe, by default _default_probe).
    evidence = (probe or _default_probe)(slot)
    if not isinstance(evidence, dict):
        evidence = {}
    region = evidence.get("country_code")
    geo_provider = evidence.get("geo_provider")
    transport = str(evidence.get("transport") or "").lower()
    context = _route_context(slot)
    expected_route_binding = context["route_binding"]
    observed_route_binding = str(evidence.get("route_binding") or "").upper()
    evidence_valid = (
        context["valid"]
        and isinstance(region, str)
        and len(region) == 2
        and region.isalpha()
        and region == region.upper()
        and transport == "https"
        and evidence.get("geo_provider") == context["geo_provider"]
        and evidence.get("geo_source_id") == context["geo_source_id"]
        and evidence.get("egress_route_id") == context["egress_route_id"]
        and observed_route_binding == expected_route_binding
    )
    base = {"skipped": False, "provider": provider,
            "geo_provider": geo_provider, "checked_at": checked_at,
            "transport": transport or None,
            "geo_source_id": context["geo_source_id"],
            "egress_route_id": context["egress_route_id"],
            "route_binding": expected_route_binding,
            "evidence_valid": evidence_valid}

    if not evidence_valid:
        return {**base, "ok": False, "gated": True, "region": None,
                "reason": _R_UNKNOWN, "message": _unknown_message(slot)}

    # C: mainland China blocks the call (no API request is made).
    if region == "CN":
        return {**base, "ok": False, "gated": True, "region": "CN",
                "reason": _R_BLOCKED, "message": _gated_message(slot, region)}

    # D: any other country code passes (SG, TW, JP, HK, US ...); only "CN" blocks. There is no list
    #   of allowed countries, which would block valid exits. Still open: confirming with the allowed
    #   HTTPS geo source that HK and TW exits report HK and TW, not CN (SG was confirmed on two
    #   networks).
    return {**base, "ok": True, "gated": False, "region": region,
            "reason": _R_OK, "message": _pass_message(slot, region)}


def validate_dispatch_receipt(slot, receipt: dict) -> bool:
    """Rebind a preflight receipt to the proxy state immediately before dispatch."""
    if not _is_overseas(getattr(slot, "provider", None)):
        return bool(receipt.get("ok") and receipt.get("skipped") and not receipt.get("gated"))
    context = _route_context(slot)
    return bool(
        context["valid"]
        and receipt.get("ok")
        and not receipt.get("gated")
        and receipt.get("evidence_valid")
        and receipt.get("geo_provider") == context["geo_provider"]
        and receipt.get("geo_source_id") == context["geo_source_id"]
        and receipt.get("egress_route_id") == context["egress_route_id"]
        and receipt.get("route_binding") == context["route_binding"]
    )
