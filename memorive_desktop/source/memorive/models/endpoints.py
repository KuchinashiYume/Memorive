"""Model names and API endpoints: the patterns a model name must match, the API protocols and the
rule for an API base URL (public HTTPS only), and how a provider's settings map to its model-list
URL and authentication header. Shared by the model runner (validation/) and the workflow exam
(memorive/exams/runner/); the settings (memorive/desktop/settings/contracts.py) and
external sources (memorive/desktop/settings/external_sources.py) check endpoints with the same rule.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import ipaddress
import json
import re
from typing import Any, Mapping
import unicodedata
from urllib.parse import urlsplit, urlunsplit


API_PROTOCOLS = frozenset({"AUTO", "OPENAI_COMPATIBLE", "ANTHROPIC", "GEMINI"})


def normalize_external_api_base_url(value: Any) -> str:
    """Validate a user-authorized public HTTPS API base without resolving DNS."""
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError("MODEL_API_BASE_URL_INVALID")
    normalized = value.strip().rstrip("/")
    if normalized != value.rstrip("/") or not normalized:
        raise ValueError("MODEL_API_BASE_URL_INVALID")
    if any(character.isspace() or ord(character) < 32 for character in normalized):
        raise ValueError("MODEL_API_BASE_URL_INVALID")
    if "\\" in normalized:
        raise ValueError("MODEL_API_BASE_URL_INVALID")
    try:
        parsed = urlsplit(normalized)
        port = parsed.port
    except ValueError as error:
        raise ValueError("MODEL_API_BASE_URL_INVALID") from error
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("MODEL_API_BASE_URL_INVALID")
    host = parsed.hostname.casefold().rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        raise ValueError("MODEL_API_BASE_URL_INVALID")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("MODEL_API_BASE_URL_INVALID")
    netloc = parsed.hostname
    if ":" in netloc and not netloc.startswith("["):
        netloc = f"[{netloc}]"
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit(("https", netloc, parsed.path.rstrip("/"), "", ""))


def json_sha256(value: Any) -> str:
    """SHA-256 (upper-case hex) of value as compact JSON with sorted keys. The command-line request's
    model allowlist and argv hashes and the workflow exam catalog use it."""
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest().upper()


SAFE_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
SAFE_THINKING = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
SAFE_SHA256 = re.compile(r"^[A-F0-9]{64}$")
SAFE_REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_:.-]{0,159}$")


@dataclass(frozen=True)
class _ApiProfile:
    provider_family: str
    url: str
    auth_header: str
    extra_headers: Mapping[str, str]


def _compact(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _catalog_url(base_url: str, protocol: str) -> str:
    parsed = urlsplit(base_url)
    path = parsed.path.rstrip("/")
    lowered = path.casefold()
    if lowered.endswith("/models"):
        catalog_path = path
    elif protocol == "OPENAI_COMPATIBLE" and lowered.endswith("/chat/completions"):
        catalog_path = f"{path[:-len('/chat/completions')]}/models"
    elif protocol == "OPENAI_COMPATIBLE" and lowered.endswith("/responses"):
        catalog_path = f"{path[:-len('/responses')]}/models"
    elif protocol == "ANTHROPIC" and lowered.endswith("/messages"):
        catalog_path = f"{path[:-len('/messages')]}/models"
    elif path:
        catalog_path = f"{path}/models"
    else:
        version = "v1beta" if protocol == "GEMINI" else "v1"
        catalog_path = f"/{version}/models"
    if protocol == "GEMINI":
        query = "pageSize=1000"
    elif protocol == "ANTHROPIC":
        query = "limit=1000"
    else:
        query = ""
    return urlunsplit((parsed.scheme, parsed.netloc, catalog_path, query, ""))


def _api_profile(service: Mapping[str, Any]) -> _ApiProfile | None:
    provider = service.get("provider")
    if not isinstance(provider, str):
        return None
    protocol = service.get("api_protocol", "AUTO")
    base_url = service.get("api_base_url", "")
    if protocol != "AUTO" or base_url:
        if (
            not isinstance(protocol, str)
            or protocol not in API_PROTOCOLS
            or protocol == "AUTO"
        ):
            raise ValueError("API_ENDPOINT_INVALID")
        base_url = normalize_external_api_base_url(base_url)
        if protocol == "OPENAI_COMPATIBLE":
            return _ApiProfile(
                protocol,
                _catalog_url(base_url, protocol),
                "Authorization",
                {},
            )
        if protocol == "ANTHROPIC":
            return _ApiProfile(
                protocol,
                _catalog_url(base_url, protocol),
                "x-api-key",
                {"anthropic-version": "2023-06-01"},
            )
        if protocol == "GEMINI":
            return _ApiProfile(
                protocol,
                _catalog_url(base_url, protocol),
                "x-goog-api-key",
                {},
            )
        raise ValueError("API_ENDPOINT_INVALID")
    identity = _compact(provider)
    if re.fullmatch(r"deepseek(?:apikey[a-z0-9]*)?", identity):
        return _ApiProfile(
            "OPENAI_COMPATIBLE", "https://api.deepseek.com/models", "Authorization", {}
        )
    if re.fullmatch(
        r"(?:anthropic|claude)(?:apikey[a-z0-9]*)?", identity
    ):
        return _ApiProfile(
            "ANTHROPIC",
            "https://api.anthropic.com/v1/models?limit=1000",
            "x-api-key",
            {"anthropic-version": "2023-06-01"},
        )
    if re.fullmatch(r"openai(?:apikey[a-z0-9]*)?", identity):
        return _ApiProfile(
            "OPENAI", "https://api.openai.com/v1/models", "Authorization", {}
        )
    if re.fullmatch(r"(?:gemini|googleai)(?:apikey[a-z0-9]*)?", identity):
        return _ApiProfile(
            "GEMINI",
            "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000",
            "x-goog-api-key",
            {},
        )
    if re.fullmatch(r"(?:siliconflow|硅基流动|硅基)(?:apikey[a-z0-9]*)?", identity):
        return _ApiProfile(
            "OPENAI_COMPATIBLE",
            "https://api.siliconflow.cn/v1/models",
            "Authorization",
            {},
        )
    return None
