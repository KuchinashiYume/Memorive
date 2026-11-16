"""A provider's model list: reading its pages (at most MAX_CATALOG_PAGES), following the next-page
cursor, and suggesting the closest names when the chosen model is not listed. verify_api_model in
runner.py uses it.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence
import unicodedata
from urllib.parse import urlencode, urlsplit, urlunsplit
from memorive.models.endpoints import SAFE_MODEL, _ApiProfile


MAX_CATALOG_PAGES = 10


MAX_CATALOG_CURSOR_CHARS = 2048


MAX_CATALOG_CHOICES = 50


def _catalog_document(body: bytes) -> Mapping[str, Any]:
    try:
        value = json.loads(body.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("API_CATALOG_RESPONSE_INVALID") from error
    if not isinstance(value, Mapping):
        raise ValueError("API_CATALOG_RESPONSE_INVALID")
    return value


def _catalog_model_ids(
    profile: _ApiProfile, value: Mapping[str, Any]
) -> list[str]:
    rows = value.get("models" if profile.provider_family == "GEMINI" else "data")
    if not isinstance(rows, list):
        raise ValueError("API_CATALOG_RESPONSE_INVALID")
    found: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        candidate = row.get("name" if profile.provider_family == "GEMINI" else "id")
        if not isinstance(candidate, str) or not candidate:
            continue
        if candidate.startswith("models/"):
            candidate = candidate[len("models/") :]
        if len(candidate) <= 256:
            found.append(candidate)
    return found


def _valid_catalog_cursor(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_CATALOG_CURSOR_CHARS
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("API_CATALOG_RESPONSE_INVALID")
    return value


def _next_catalog_url(
    profile: _ApiProfile,
    value: Mapping[str, Any],
) -> str | None:
    parsed = urlsplit(profile.url)
    if profile.provider_family == "ANTHROPIC":
        has_more = value.get("has_more", False)
        if not isinstance(has_more, bool):
            raise ValueError("API_CATALOG_RESPONSE_INVALID")
        if not has_more:
            return None
        cursor = _valid_catalog_cursor(value.get("last_id"))
        query = urlencode((("limit", "1000"), ("after_id", cursor)))
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))
    if profile.provider_family == "GEMINI":
        cursor_value = value.get("nextPageToken")
        if cursor_value is None or cursor_value == "":
            return None
        cursor = _valid_catalog_cursor(cursor_value)
        query = urlencode((("pageSize", "1000"), ("pageToken", cursor)))
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))
    return None


def _catalog_match(
    profile: _ApiProfile, configured: str, catalog: Sequence[str]
) -> str | None:
    configured_identity = unicodedata.normalize("NFKC", configured).strip()
    for candidate in catalog:
        if unicodedata.normalize("NFKC", candidate).strip() == configured_identity:
            return candidate
    return None


def _catalog_choices(catalog: Sequence[str]) -> list[str]:
    choices: list[str] = []
    seen: set[str] = set()
    for candidate in catalog:
        if candidate in seen or not SAFE_MODEL.fullmatch(candidate):
            continue
        seen.add(candidate)
        choices.append(candidate)
        if len(choices) >= MAX_CATALOG_CHOICES:
            break
    return choices


def _catalog_suggestion(configured: str, catalog: Sequence[str]) -> str | None:
    """Suggest only a live catalog ID that is an unambiguous identity prefix.

    This recovers display-version/tier suffixes such as
    ``DeepSeek-V4-Flash-0731-maximum`` without treating the alias as a valid
    API model ID.  The caller must still save the returned exact catalog ID.
    """

    configured_identity = unicodedata.normalize("NFKC", configured).strip().casefold()
    matches: list[tuple[int, str]] = []
    for candidate in catalog:
        candidate_identity = (
            unicodedata.normalize("NFKC", candidate).strip().casefold()
        )
        if configured_identity == candidate_identity:
            matches.append((len(candidate_identity), candidate))
            continue
        if not configured_identity.startswith(candidate_identity):
            continue
        remainder = configured_identity[len(candidate_identity) :]
        if remainder and remainder[0] in "-_:/":
            matches.append((len(candidate_identity), candidate))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0], reverse=True)
    if len(matches) > 1 and matches[0][0] == matches[1][0]:
        return None
    return matches[0][1]
