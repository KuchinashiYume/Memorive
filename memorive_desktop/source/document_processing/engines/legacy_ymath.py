"""受限 legacy YMath PDF 字体恢复。

只接受审计过的六个 YMath/ZMath CFF 字体程序。字体名、程序 SHA-256、
rawdict 输入字符和 texttrace glyph id 必须同时匹配；未知项全部拒绝。
恢复只在内存中重建带页标的 Markdown，不改源 PDF、不调用 OCR。
"""
from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_PAGE_MARKER = "<!-- memorive:page:{page} -->"
_SUPERSCRIPT_FONT = "YMath-Pack-FIve"
_ZMATH_FONT = "ZMath-Pack-Six"
_ALLOWED_SUPERSCRIPT_CHARS = frozenset(" ~,0123456789[]a")
_ALLOWED_WHITESPACE_CONTROLS = frozenset({"\t", "\n", "\r"})
_FONT_SHA256 = {
    "YMath-Pack-One": (
        "f56bee104cec1e0a42894bd3a2fc76eef756d88a3dc13df50f2002a9da9258ca"
    ),
    "YMath-Pack-Two": (
        "74910b78dfb25632b36b480ba094f3a7982447f06f0b1825976181248071a0f3"
    ),
    "YMath-Pack-THree": (
        "d167edd0e342c43a9076927727aa010654993d350668d1969c5fd71daadf0323"
    ),
    "YMath-Pack-Four": (
        "faa340041e9834079709e9d59f1ea903d9965c03ee0f76afb044c26da116a8a1"
    ),
    "YMath-Pack-FIve": (
        "1d77ff0844259711c4cb50a7975e23575a2ed64203d3ad38566962ce22c976a1"
    ),
    "ZMath-Pack-Six": (
        "063385b9f2292e394907ba420bc67877b98ad40ad5b66358310c839f8e6f0ab8"
    ),
}


@dataclass(frozen=True)
class _GlyphRule:
    trace_unicode: int
    glyph_id: int
    replacement: str


_GLYPH_RULES = {
    "YMath-Pack-One": {
        "†": _GlyphRule(0x2020, 75, "ff"),
        "\u008d": _GlyphRule(0xFFFD, 76, "\u0301"),
        "\u008e": _GlyphRule(0xFFFD, 77, "\u0308"),
        "•": _GlyphRule(0x2022, 78, "i"),
        "¡": _GlyphRule(0x00A1, 79, "°"),
        "È": _GlyphRule(0x00C8, 80, "–"),
        "Ï": _GlyphRule(0x00CF, 81, "'"),
        "Ð": _GlyphRule(0x00D0, 82, "fi"),
        "Ñ": _GlyphRule(0x00D1, 83, "fl"),
        "Ó": _GlyphRule(0x00D3, 84, "\u0327"),
        "ü": _GlyphRule(0x00FC, 85, "\u0302"),
    },
    "YMath-Pack-Two": {
        "È": _GlyphRule(0x00C8, 60, "–"),
        "É": _GlyphRule(0x00C9, 61, "."),
        "Ï": _GlyphRule(0x00CF, 62, "'"),
        "Ñ": _GlyphRule(0x00D1, 63, "fl"),
    },
    "YMath-Pack-THree": {
        "†": _GlyphRule(0x2020, 57, "ff"),
    },
    "YMath-Pack-Four": {
        "8": _GlyphRule(0x0038, 1, "\u0303"),
        "@": _GlyphRule(0x0040, 2, "′"),
        "[": _GlyphRule(0x005B, 3, ">"),
        "w": _GlyphRule(0x0077, 4, "–"),
        "x": _GlyphRule(0x0078, 5, "="),
    },
    "YMath-Pack-FIve": {
        "[": _GlyphRule(0x005B, 12, "−"),
        "]": _GlyphRule(0x005D, 13, "+"),
        "~": _GlyphRule(0x007E, 15, "−"),
    },
    "ZMath-Pack-Six": {
        "`": _GlyphRule(0x0060, 1, "+"),
        "a": _GlyphRule(0x0061, 2, "a"),
        "b": _GlyphRule(0x0062, 3, "b"),
    },
}


class LegacyYMathRecoveryError(ValueError):
    """输入像审计过的 YMath PDF，但无法无歧义恢复。"""


@dataclass(frozen=True)
class LegacyYMathRecovery:
    markdown: str
    pages_total: int
    mapped_chars: int
    synthetic_chars: int
    passthrough_chars: int
    superscript_runs: int
    superscript_chars: int
    suppressed_accent_spaces: int
    font_count: int
    image_blocks_skipped: int
    content_sha256: str


@dataclass(frozen=True)
class _LineRecovery:
    text: str
    mapped_chars: int
    synthetic_chars: int
    passthrough_chars: int
    superscript_runs: int
    superscript_chars: int
    suppressed_accent_spaces: int


def _origin_key(
    font: str,
    origin: Any,
) -> tuple[str, tuple[float, float]]:
    values = tuple(round(float(value), 3) for value in origin)
    if len(values) != 2:
        raise LegacyYMathRecoveryError(
            f"font {font}: invalid character origin {origin!r}"
        )
    return font, values


def _trace_by_origin(
    page: Any,
) -> dict[tuple[str, tuple[float, float]], tuple[int, int]]:
    trace: dict[tuple[str, tuple[float, float]], tuple[int, int]] = {}
    for span in page.get_texttrace():
        font = str(span.get("font") or "")
        for item in span.get("chars", []):
            key = _origin_key(font, item[2])
            value = int(item[0]), int(item[1])
            previous = trace.get(key)
            if previous is not None and previous != value:
                raise LegacyYMathRecoveryError(
                    f"font {font}: conflicting texttrace at {key[1]}"
                )
            trace[key] = value
    return trace


def _last_character(groups: list[str]) -> str:
    for group in reversed(groups):
        if group:
            return group[-1]
    return ""


def _replace_nonspace(value: str, replacement: str) -> str:
    first_content = len(value) - len(value.lstrip())
    last_content = len(value.rstrip())
    return value[:first_content] + replacement + value[last_content:]


def _remove_last_character(groups: list[str], expected: str) -> None:
    for index in range(len(groups) - 1, -1, -1):
        if not groups[index]:
            continue
        if groups[index][-1] != expected:
            raise LegacyYMathRecoveryError(
                f"expected trailing {expected!r}, got {groups[index][-1]!r}"
            )
        groups[index] = groups[index][:-1]
        return
    raise LegacyYMathRecoveryError(
        f"expected trailing {expected!r}, got empty context"
    )


def _render_pack_five_span(
    raw_value: str,
    rendered_value: str,
    *,
    previous: str,
    page_number: int,
) -> tuple[str, int]:
    raw_core = raw_value.strip()
    if not raw_core:
        return rendered_value, 0

    if raw_core in {"[", "]"}:
        return rendered_value, 0
    if raw_core == "a":
        if previous:
            raise LegacyYMathRecoveryError(
                f"page {page_number} font {_SUPERSCRIPT_FONT}: "
                f"unaudited footnote context after {previous!r}"
            )
        return _replace_nonspace(rendered_value, "^a"), 1
    if raw_core == "3" and previous == "m":
        return _replace_nonspace(rendered_value, "^3"), 1
    if raw_core == "~3" and previous == "m":
        return _replace_nonspace(rendered_value, "^−3"), 2
    if raw_core == "~1" and previous in {"g", "y"}:
        return _replace_nonspace(rendered_value, "^−1"), 2
    if raw_core == "2~" and previous == "S":
        return _replace_nonspace(rendered_value, "^2−"), 2
    if raw_core == "42~" and previous == "O":
        return _replace_nonspace(rendered_value, "4^2−"), 2
    if raw_core == "4~" and previous == "O":
        return _replace_nonspace(rendered_value, "4^−"), 1
    if any(value in raw_core for value in "~[]"):
        raise LegacyYMathRecoveryError(
            f"page {page_number} font {_SUPERSCRIPT_FONT}: "
            f"unaudited Pack-Five semantic {raw_core!r} after {previous!r}"
        )
    return rendered_value, 0


def _render_zmath_span(
    raw_value: str,
    rendered_value: str,
    *,
    parts: list[str],
    page_number: int,
) -> tuple[str, int]:
    raw_core = raw_value.strip()
    if raw_core in {"a", "b"}:
        return _replace_nonspace(rendered_value, f"^{raw_core}"), 1
    if raw_core == "`":
        context = "".join(parts)
        if context.endswith("NH4"):
            return _replace_nonspace(rendered_value, "^+"), 1
        for element, charge in (("Cr3", "3"), ("Cr6", "6")):
            if context.endswith(element):
                _remove_last_character(parts, charge)
                return _replace_nonspace(
                    rendered_value,
                    f"^{charge}+",
                ), 2
    raise LegacyYMathRecoveryError(
        f"page {page_number} font {_ZMATH_FONT}: "
        f"unaudited semantic {raw_core!r} after {''.join(parts)[-8:]!r}"
    )


def _is_forbidden_control(value: str) -> bool:
    return (
        unicodedata.category(value) == "Cc"
        and value not in _ALLOWED_WHITESPACE_CONTROLS
    )


def _render_line(
    line: dict,
    trace_by_origin: dict[
        tuple[str, tuple[float, float]], tuple[int, int]
    ],
    *,
    page_number: int,
) -> _LineRecovery:
    parts: list[str] = []
    mapped_chars = 0
    synthetic_chars = 0
    passthrough_chars = 0
    superscript_runs = 0
    superscript_chars = 0
    suppressed_accent_spaces = 0

    for span in line.get("spans", []):
        font = str(span.get("font") or "")
        rules = _GLYPH_RULES.get(font, {})
        rendered: list[str] = []
        raw_rendered: list[str] = []
        for char in span.get("chars", []):
            value = str(char.get("c") or "")
            if len(value) != 1:
                raise LegacyYMathRecoveryError(
                    f"page {page_number} font {font}: "
                    f"char length {len(value)} is not 1"
                )
            if char.get("synthetic") is True:
                synthetic_chars += 1
            previous = _last_character(parts + rendered)
            if (
                char.get("synthetic") is True
                and value == " "
                and previous
                and unicodedata.combining(previous)
            ):
                suppressed_accent_spaces += 1
                continue
            if (
                font == _SUPERSCRIPT_FONT
                and value not in _ALLOWED_SUPERSCRIPT_CHARS
            ):
                raise LegacyYMathRecoveryError(
                    f"page {page_number} font {font}: "
                    f"unexpected superscript character U+{ord(value):04X}"
                )

            rule = rules.get(value)
            if rule is not None:
                key = _origin_key(font, char.get("origin", ()))
                trace = trace_by_origin.get(key)
                if trace is None:
                    raise LegacyYMathRecoveryError(
                        f"page {page_number} font {font}: "
                        f"missing texttrace for U+{ord(value):04X}"
                    )
                trace_unicode, glyph_id = trace
                if trace_unicode != rule.trace_unicode:
                    raise LegacyYMathRecoveryError(
                        f"page {page_number} font {font}: trace U+"
                        f"{trace_unicode:04X} != U+{rule.trace_unicode:04X}"
                    )
                if glyph_id != rule.glyph_id:
                    raise LegacyYMathRecoveryError(
                        f"page {page_number} font {font}: "
                        f"glyph id {glyph_id} != {rule.glyph_id}"
                )
                rendered.append(rule.replacement)
                raw_rendered.append(value)
                mapped_chars += 1
            else:
                if value == "\ufffd" or _is_forbidden_control(value):
                    raise LegacyYMathRecoveryError(
                        f"page {page_number} font {font}: "
                        f"unmapped suspicious U+{ord(value):04X}"
                    )
                if (
                    font in _GLYPH_RULES
                    and ord(value) > 0x7E
                ):
                    raise LegacyYMathRecoveryError(
                        f"page {page_number} font {font}: "
                        f"unaudited non-ASCII U+{ord(value):04X}"
                    )
                rendered.append(value)
                raw_rendered.append(value)
                passthrough_chars += 1

        span_text = "".join(rendered)
        if font == _SUPERSCRIPT_FONT:
            span_text, explicit_chars = _render_pack_five_span(
                "".join(raw_rendered),
                span_text,
                previous=_last_character(parts),
                page_number=page_number,
            )
            if explicit_chars:
                superscript_runs += 1
                superscript_chars += explicit_chars
        elif font == _ZMATH_FONT:
            span_text, explicit_chars = _render_zmath_span(
                "".join(raw_rendered),
                span_text,
                parts=parts,
                page_number=page_number,
            )
            superscript_runs += 1
            superscript_chars += explicit_chars
        parts.append(span_text)

    return _LineRecovery(
        text=unicodedata.normalize("NFC", "".join(parts).rstrip()),
        mapped_chars=mapped_chars,
        synthetic_chars=synthetic_chars,
        passthrough_chars=passthrough_chars,
        superscript_runs=superscript_runs,
        superscript_chars=superscript_chars,
        suppressed_accent_spaces=suppressed_accent_spaces,
    )


def _font_program_hashes(document: Any) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for page in document:
        for item in page.get_fonts(full=True):
            xref = int(item[0])
            span_font = str(item[3]).split("+", 1)[-1]
            if span_font not in _FONT_SHA256:
                continue
            buffer = document.extract_font(xref)[3]
            digest = hashlib.sha256(buffer).hexdigest()
            previous = hashes.get(span_font)
            if previous is not None and previous != digest:
                raise LegacyYMathRecoveryError(
                    f"conflicting font program for {span_font}"
                )
            hashes[span_font] = digest
    return hashes


def _validate_font_program_hashes(actual: dict[str, str]) -> bool:
    if not actual:
        return False
    if set(actual) != set(_FONT_SHA256):
        missing = sorted(set(_FONT_SHA256) - set(actual))
        extra = sorted(set(actual) - set(_FONT_SHA256))
        raise LegacyYMathRecoveryError(
            f"incomplete audited YMath font set: "
            f"missing={missing}, extra={extra}"
        )
    mismatched = sorted(
        font for font, digest in actual.items()
        if digest != _FONT_SHA256[font]
    )
    if mismatched:
        raise LegacyYMathRecoveryError(
            f"YMath font program hash mismatch: {mismatched}"
        )
    return True


def _recover_document(
    document: Any,
    *,
    font_count: int,
) -> LegacyYMathRecovery:
    import pymupdf

    page_texts: list[str] = []
    mapped_chars = 0
    synthetic_chars = 0
    passthrough_chars = 0
    superscript_runs = 0
    superscript_chars = 0
    suppressed_accent_spaces = 0
    image_blocks_skipped = 0

    for page_number, page in enumerate(document, start=1):
        trace = _trace_by_origin(page)
        raw = page.get_text(
            "rawdict",
            flags=int(pymupdf.TEXTFLAGS_TEXT),
        )
        recovered_blocks: list[str] = []
        for block in raw.get("blocks", []):
            if block.get("type") != 0:
                image_blocks_skipped += 1
                continue
            recovered_lines: list[str] = []
            for line in block.get("lines", []):
                result = _render_line(
                    line,
                    trace,
                    page_number=page_number,
                )
                recovered_lines.append(result.text)
                mapped_chars += result.mapped_chars
                synthetic_chars += result.synthetic_chars
                passthrough_chars += result.passthrough_chars
                superscript_runs += result.superscript_runs
                superscript_chars += result.superscript_chars
                suppressed_accent_spaces += (
                    result.suppressed_accent_spaces
                )
            block_text = "\n".join(recovered_lines).strip()
            if block_text:
                recovered_blocks.append(block_text)
        page_texts.append("\n\n".join(recovered_blocks))

    if mapped_chars == 0:
        raise LegacyYMathRecoveryError(
            "audited YMath fonts found but no characters were mapped"
        )

    parts: list[str] = []
    for page_number, text in enumerate(page_texts, start=1):
        parts.extend([_PAGE_MARKER.format(page=page_number), text, ""])
    markdown = "\n\n".join(parts).rstrip() + "\n"

    if "\ufffd" in markdown:
        raise LegacyYMathRecoveryError(
            "recovered text still contains U+FFFD"
        )
    forbidden = sum(_is_forbidden_control(char) for char in markdown)
    if forbidden:
        raise LegacyYMathRecoveryError(
            f"recovered text still contains {forbidden} controls"
        )
    nonspace = "".join(markdown.split())
    readable_count = sum(
        unicodedata.category(char)[:1] in {"L", "N"}
        for char in nonspace
    )
    readable_ratio = readable_count / max(len(nonspace), 1)
    if readable_ratio < 0.50:
        raise LegacyYMathRecoveryError(
            f"recovered letter/number ratio too low: {readable_ratio:.4%}"
        )

    return LegacyYMathRecovery(
        markdown=markdown,
        pages_total=len(page_texts),
        mapped_chars=mapped_chars,
        synthetic_chars=synthetic_chars,
        passthrough_chars=passthrough_chars,
        superscript_runs=superscript_runs,
        superscript_chars=superscript_chars,
        suppressed_accent_spaces=suppressed_accent_spaces,
        font_count=font_count,
        image_blocks_skipped=image_blocks_skipped,
        content_sha256=hashlib.sha256(
            markdown.encode("utf-8")
        ).hexdigest(),
    )


def recover_legacy_ymath_pdf(
    path: Path,
) -> LegacyYMathRecovery | None:
    """只读恢复审计过的 YMath PDF；不适用时返回 None。"""
    import pymupdf

    document = pymupdf.open(path)
    try:
        programs = _font_program_hashes(document)
        if not _validate_font_program_hashes(programs):
            return None
        return _recover_document(document, font_count=len(programs))
    finally:
        document.close()
