"""Model names and API endpoints: the patterns a model name must match, and how a provider's
settings map to its model-list URL and authentication header. Shared by the model runner
(model_validation.py) and the workflow exam (workflow_exam_runner.py).
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping
import unicodedata
from urllib.parse import urlsplit, urlunsplit

from .contracts import API_PROTOCOLS, normalize_external_api_base_url


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
