"""Source-language preservation for user-facing literature artifacts.

The Core data mainline carries the source language.  This module provides a
small deterministic policy seam for Desktop: detect the dominant CJK language and
reject model- or template-added Han text in a non-Han source before an artifact
is written.  Exact source passages remain legal, so an English paper may still
quote a Chinese title that is genuinely present in the supplied source.
"""
from __future__ import annotations

import re
from collections.abc import Iterable


_HAN_CHAR_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_HAN_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_ESCAPED_UNICODE_RUN_RE = re.compile(r"(?:\\u[0-9a-fA-F]{4})+")
_KANA_RE = re.compile(r"[\u3040-\u30ff]")
_SOURCE_HAN_MAIN_RATIO = 0.20
_LANGUAGE_ALIASES = {
    "zh": "zh",
    "zh-cn": "zh",
    "zh-hans": "zh",
    "chinese": "zh",
    "ja": "ja",
    "ja-jp": "ja",
    "japanese": "ja",
    "en": "en",
    "en-us": "en",
    "en-gb": "en",
    "english": "en",
}


class SourceLanguageDriftError(RuntimeError):
    """A user-facing artifact contains foreign Han text absent from its source."""


def normalize_source_language(value: str | None) -> str:
    """Return the supported language code; unknown languages use neutral English UI."""

    folded = str(value or "").strip().casefold().replace("_", "-")
    return _LANGUAGE_ALIASES.get(folded, "en")


def language_display_name(value: str | None) -> str:
    return {"zh": "Chinese", "ja": "Japanese", "en": "English"}[
        normalize_source_language(value)
    ]


def _join_text(value: str | Iterable[object]) -> str:
    if isinstance(value, str):
        return value
    return "\n".join(str(item or "") for item in value)


def detect_source_language(value: str | Iterable[object]) -> str:
    """Detect zh/ja/en from source content, never from a model response or filename."""

    text = _join_text(value)
    if _KANA_RE.search(text):
        return "ja"
    units = [char for char in text if not char.isspace()]
    if not units:
        return "en"
    han_ratio = len(_HAN_CHAR_RE.findall(text)) / len(units)
    return "zh" if han_ratio >= _SOURCE_HAN_MAIN_RATIO else "en"


def validate_generated_language(
    *,
    source_text: str,
    generated_text: str,
    source_language: str,
    artifact_name: str,
) -> None:
    """Fail closed on newly introduced Han text for an English/non-Han source.

    This guard is provenance based rather than a blanket character ban.  A Han
    run is accepted only when that exact run exists in the supplied source.
    Chinese and Japanese sources are not subjected to this non-Han guard.
    """

    language = normalize_source_language(source_language)
    if language != "en":
        return
    source = str(source_text or "")
    generated = str(generated_text or "")
    compact_source = re.sub(r"\s+", "", source)
    spans = [match.group(0) for match in _HAN_RUN_RE.finditer(generated)]
    for match in _ESCAPED_UNICODE_RUN_RE.finditer(generated):
        try:
            decoded = match.group(0).encode("ascii").decode("unicode_escape")
        except UnicodeError:
            continue
        spans.extend(item.group(0) for item in _HAN_RUN_RE.finditer(decoded))

    violations: list[str] = []
    for span in spans:
        if span in source or span in compact_source:
            continue
        if span not in violations:
            violations.append(span)
    if violations:
        preview = ", ".join(violations[:6])
        raise SourceLanguageDriftError(
            f"SOURCE_LANGUAGE_DRIFT:{artifact_name}:source=en:added_han={preview}"
        )
