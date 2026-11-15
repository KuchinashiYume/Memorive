"""MODEL_GATEWAY 境外 API 区域预检门(测前安全阀)——纯逻辑,不查 config / 不碰 SDK / 不读 key / 不 log。

它是 gateway.healthcheck() 安全阀的姊妹件:同型「先纯探测 → 返回结构化 dict、不抛异常
表达结论 → 调用方据结论挡下 / 放行」,只是探测对象从「本地 Ollama 是否就绪」换成
「本机出口 IP 是否在中国大陆」。判 config→slot 由 gateway.ModelGateway 负责；
call / embed / rerank / ocr 的真实 dispatch 前均强制消费本模块的结构化结论。
本模块只接 SlotConfig、恒返回 dict。

⚠ 语义前提(务必读):本门只接受 allowlist 内的 HTTPS geo source，并要求 geo URL 与
provider endpoint 由 urllib.request 默认 opener 解析到同一显式代理 route。DIRECT/TUN、
PAC split-route、bypass 或无法解析的 system route 均不可证明同出口，必须 fail closed。
区域 receipt 在真正 dispatch 前再次按当前 proxy state 复核，route 漂移即阻断。

⚠ 区域通过 ≠ key 有效:本门只回答「出口区域是否放行」,绝不代表 key 可用 / 有额度 /
已订阅;key 是否可调由放行后的 D0 / D4 连通性测试自行判定。

守克制:只做区域判断。不做 key 全量检查 / 余额 / 订阅 / 失败四档分类 / 自动切代理 /
注册表驱动;国内 API 自动 skip。境外请求只有 `ok=true && gated=false` 才能 dispatch。
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from datetime import datetime
from urllib.parse import urlsplit

# 境外 API provider 名单(硬编码;加境外 provider 在此加一行 = 预留空位,与 models.yaml slot.provider 对齐)。
# GPT→openai、Gemini→google、Claude→anthropic。国内(deepseek/siliconflow)、本地(ollama/chroma)、
# human、占位(TBD)均不在集合内 → 自动跳过、不进门。
_OVERSEAS_PROVIDERS = frozenset({"openai", "google", "anthropic"})

# geo 源:只允许冻结 allowlist；env 只能选择，不能扩大 source 集合。
_ENV_GEO_URL = "MEMORIVE_MODEL_GATEWAY_GEO_URL"     # 覆盖名:换源只改环境变量、不改代码
_DEFAULT_GEO_URL = None  # fail closed: an explicit HTTPS source is required
_GEO_TIMEOUT = 3                     # 秒(2–3s,偏 3s 容代理往返)
_TRUSTED_GEO_SOURCES = {
    "https://www.cloudflare.com/cdn-cgi/trace": "CLOUDFLARE_CDN_CGI_TRACE_V1",
}

# reason 机器可判枚举(调用方判定不靠中文串;中文只进 message)。
_R_SKIP = "domestic_skip"
_R_OK = "region_ok"
_R_BLOCKED = "region_cn_blocked"
_R_UNKNOWN = "region_unknown"


def _now_iso() -> str:
    """本地带时区、秒级 —— 对齐 KNOWLEDGE_ADMISSION_log `ts`(knowledge_admission_log.py)/ RUNTIME_LOG `_now_iso`(logger.py):
    项目一贯 `datetime.now().astimezone()`(本地 +08:00、非 UTC),同一处日志不出现两种口径。"""
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
    """从 geo 响应体取二字母国家码并大写;取不到返 None。兼容两类源:
    JSON(ip-api.com→countryCode / ipinfo.io→country)与行式 trace(cloudflare cdn-cgi/trace→loc=XX)。"""
    body = (body or "").strip()
    try:
        data = json.loads(body)
        if isinstance(data, dict):
            # 优先 countryCode(ip-api 的 country 是全名 "China"、不能先读);仅无 code 字段时(如 ipinfo)才用 country
            code = data.get("countryCode") or data.get("country_code") or data.get("country")
            if isinstance(code, str) and code.strip():
                normalized = code.strip().upper()
                if normalized in {"CHINA", "PEOPLE'S REPUBLIC OF CHINA", "PRC"}:
                    return "CN"
                return normalized if len(normalized) == 2 and normalized.isalpha() else None
    except Exception:
        pass
    for line in body.splitlines():           # cloudflare trace: 一行 loc=XX
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
    """查出口 IP 国家码并返回 route-bound evidence dict。

    ⚠ 用与各 provider adapter 相同的 urllib.request.urlopen **默认 opener**(继承同一
    HTTP(S)_PROXY / 系统代理)—— TUN 全局下 geo 与目标 API 同出口,判断才有意义。
    任何失败(无 URL / 非 HTTPS / URLError / 超时 / 解析 / 无国家码字段)均返回
    不足以授权的 evidence dict；
    不崩、不抛、**绝不返回完整 IP**(只国家码)。"""
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
    """纯核:接一个 SlotConfig,判 A/B/C/D/E,恒返回结构化 dict(绝不抛异常表达大陆 / 未知)。

    probe: 可注入的出口探测,签名 (slot) -> evidence dict,
           完全替代 _default_probe(离线单测注入假 probe、不打真网;缺省用 _default_probe)。

    返回 dict 至少包含:
      ok / gated / region / reason / message / skipped / provider / geo_provider / checked_at；
    境外槽另含 transport / route_binding / evidence_valid。
    (不含任何 key 值、不含完整 IP —— region / geo_provider 只到国家码 / host 级。)
    """
    provider = getattr(slot, "provider", None)
    checked_at = _now_iso()

    # A 判是否境外:非境外(国内 / 本地 / human / 占位 TBD)→ 直接跳过,不查 geo、不调 probe。
    if not _is_overseas(provider):
        return {"ok": True, "gated": False, "region": None, "reason": _R_SKIP,
                "message": None, "skipped": True, "provider": provider,
                "geo_provider": None, "checked_at": checked_at}

    # B 查出口区域(probe 可注入;缺省 _default_probe)。
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

    # C 中国大陆则挡下(不发起任何 API 请求)。
    if region == "CN":
        return {**base, "ok": False, "gated": True, "region": "CN",
                "reason": _R_BLOCKED, "message": _gated_message(slot, region)}

    # D 非中国大陆则放行:放行集 = **任何非大陆国家码**(SG/TW/JP/HK/US… 全放),仅 countryCode=="CN" 挡。
    #   不列死放行名单——列名单反会误挡合法非陆节点。
    #   ⚠ HK/TW 的 ip-api 码待在该节点实测确认(应为标准 ISO HK/TW、不并入 CN;SG 已两网实测,
    #     TW/JP/HK 待以获准 HTTPS geo 源确认 countryCode≠CN;承 07-04 HK/TW/MO 风险)——待办/债。
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
