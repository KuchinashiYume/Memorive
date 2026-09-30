"""受限 legacy C<number> PDF 字体恢复。

只服务 PyMuPDF 轻量沙盒引擎。部分旧 Elsevier PDF 没有 ToUnicode，
但 Type1 Encoding /Differences 仍保留 C<number> 字形名；PyMuPDF 1.28
rawdict 同时提供字体名、PDF 输入码和 synthetic 空格标记，因而可在不
OCR、不猜频率的前提下确定性恢复。

安全边界：
- 只接受审计过的 Adv* 字体名；
- C32..C126 按 ASCII，16 个特殊字形显式白名单；
- 未知字形、synthetic 缺失歧义、残留 U+FFFD/控制字符全部 fail-closed；
- 不写源 PDF、不落盘，只返回内存 Markdown。
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_PAGE_MARKER = "<!-- memorive:page:{page} -->"
_ENCODING_REF_RE = re.compile(r"/Encoding\s+(\d+)\s+\d+\s+R")
_DIFFERENCES_RE = re.compile(r"/Differences\s*\[(.*?)\]", re.DOTALL)
_GLYPH_TOKEN_RE = re.compile(r"/[^\s\[\]<>/]+|\d+")
_GLYPH_NAME_RE = re.compile(r"^C(\d+)(?:\._)?$")

_FONT_SHA256 = {
    "AdvPSTim": "a0683a3d382c554699af41efa1dc5e01127618df8eb21e49cbd3e5c4d457b6f8",
    "AdvPSTim-I": "fd6afaf6ee5d31da769b690b6f3ad684c0dd4b22380f8104b2b9bce5e26fd59b",
    "AdvPSTim-B": "06e62d731dafea9e91c87467b736d824982649e20131bf9bf1ec3495c3ca10e3",
    "AdvPSSym": "45904106fcf60e1ef6da753757363853dfec8e030647c98cf6d8707bf999cd3a",
    "AdvP4C4E74": "9fe93b93981d3a0cf287dfb705a4637dc2743438d1d0550e8b9420ae62388c88",
    "AdvP4C4E51": "8ec34025c74e4c7155927cb1495c59873c458c374fd8bd1b38d1cd07d06c0771",
    "AdvPSMP1": "1f2a96dd06b9fb29b0cd8cd67bc95d9343f2d9c2f7cb963eaa7bc8565f8d7f9f",
    "AdvPSMP10": "c2da00745993c0c997cd68421f571bde6f8f5ea53a0376cab5bfa0dd1fd6e606",
    "AdvSPSMI": "c69597b4c0b5993836564def6fef5633319fab2782743750d444f0ec8b3fd388",
    "AdvPSMPi6": "9cb4a527f6fc7b2a60b103d43157158de677780e8d17ebe33a3571ad37a1aa67",
    "AdvPSMSAKNOWLEDGE_FEEDBACK": "ee07fdf7eaab6024b3b845ab4d313c09fcb682aa8477ec6251e692bdc8273396",
}
_AUDITED_FONT_NAMES = frozenset(_FONT_SHA256)
_ENCODING_MAP_SHA256 = (
    "06781b61e297988e7fe89205d371ec787d98973da2a8055fca235ceb995a57b6"
)
_TRACE_RELATION_SHA256 = (
    "06b3a7f080363084199de82976430dde2bd51895e6226fa2889e8e56c5a97e44"
)
_SPECIAL_GLYPHS = {
    "C0": "−",
    "C1": "·",
    "C6": "±",
    "C24": "~",
    "C128": "ff",
    "C133": "…",
    "C134": "†",
    "C136": "=",
    "C139": "±",
    "C174": "fi",
    "C175": "fl",
    "C176": "°",
    "C177": "–",
    "C211": "©",
    "C212": "‘",
    "C213": "’",
}
_ALLOWED_WHITESPACE_CONTROLS = {"\t", "\n", "\r"}


class LegacyCFontRecoveryError(ValueError):
    """输入像 legacy C-font，但无法无歧义恢复。"""


@dataclass(frozen=True)
class LegacyCFontRecovery:
    markdown: str
    pages_total: int
    mapped_chars: int
    synthetic_chars: int
    passthrough_chars: int
    font_count: int
    image_blocks_skipped: int
    content_sha256: str


@dataclass(frozen=True)
class _LineRecovery:
    text: str
    mapped_chars: int
    synthetic_chars: int
    passthrough_chars: int


def _decode_glyph_name(name: str) -> str:
    special = _SPECIAL_GLYPHS.get(name)
    if special is not None:
        return special
    match = _GLYPH_NAME_RE.fullmatch(name)
    if not match:
        raise LegacyCFontRecoveryError(
            f"unsupported legacy glyph name: {name!r}"
        )
    value = int(match.group(1))
    if 32 <= value <= 126:
        return chr(value)
    raise LegacyCFontRecoveryError(
        f"legacy glyph requires explicit audited mapping: {name!r}"
    )


def _parse_differences(object_text: str) -> dict[int, str]:
    match = _DIFFERENCES_RE.search(object_text)
    if not match:
        raise LegacyCFontRecoveryError(
            "legacy Encoding has no /Differences array"
        )
    mapping: dict[int, str] = {}
    current_code: int | None = None
    for token in _GLYPH_TOKEN_RE.findall(match.group(1)):
        if token.isdigit():
            current_code = int(token)
            continue
        if current_code is None:
            raise LegacyCFontRecoveryError(
                f"glyph name before code in Differences: {token}"
            )
        mapping[current_code] = _decode_glyph_name(token[1:])
        current_code += 1
    if not mapping:
        raise LegacyCFontRecoveryError(
            "legacy Encoding produced an empty map"
        )
    return mapping


def _font_inventory(document: Any) -> list[tuple[int, str]]:
    seen: set[tuple[int, str]] = set()
    rows: list[tuple[int, str]] = []
    for page in document:
        for item in page.get_fonts(full=True):
            font_xref = int(item[0])
            span_font = str(item[3]).split("+", 1)[-1]
            key = (font_xref, span_font)
            if key in seen:
                continue
            seen.add(key)
            rows.append(key)
    return rows


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _font_program_hashes(document: Any) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for font_xref, span_font in _font_inventory(document):
        if span_font not in _AUDITED_FONT_NAMES:
            continue
        buffer = document.extract_font(font_xref)[3]
        digest = hashlib.sha256(buffer).hexdigest()
        previous = hashes.get(span_font)
        if previous is not None and previous != digest:
            raise LegacyCFontRecoveryError(
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
        raise LegacyCFontRecoveryError(
            "incomplete audited C-font set: "
            f"missing={missing}, extra={extra}"
        )
    mismatched = sorted(
        font
        for font, digest in actual.items()
        if digest != _FONT_SHA256[font]
    )
    if mismatched:
        raise LegacyCFontRecoveryError(
            f"C-font program hash mismatch: {mismatched}"
        )
    return True


def _build_font_maps(
    document: Any,
) -> dict[str, dict[int, str]] | None:
    font_hashes = _font_program_hashes(document)
    if not _validate_font_program_hashes(font_hashes):
        return None

    maps: dict[str, dict[int, str]] = {}
    for font_xref, span_font in _font_inventory(document):
        if span_font not in _AUDITED_FONT_NAMES:
            continue
        font_object = document.xref_object(
            font_xref, compressed=False
        )
        encoding_match = _ENCODING_REF_RE.search(font_object)
        if not encoding_match:
            continue
        encoding_object = document.xref_object(
            int(encoding_match.group(1)),
            compressed=False,
        )
        mapping = _parse_differences(encoding_object)
        existing = maps.get(span_font)
        if existing is not None and existing != mapping:
            raise LegacyCFontRecoveryError(
                f"conflicting maps for span font {span_font}"
            )
        maps[span_font] = mapping

    if set(maps) != set(_FONT_SHA256):
        missing = sorted(set(_FONT_SHA256) - set(maps))
        raise LegacyCFontRecoveryError(
            f"incomplete audited C-font Encoding set: missing={missing}"
        )
    canonical = {
        font: {
            str(code): value
            for code, value in sorted(mapping.items())
        }
        for font, mapping in sorted(maps.items())
    }
    digest = _canonical_sha256(canonical)
    if digest != _ENCODING_MAP_SHA256:
        raise LegacyCFontRecoveryError(
            "C-font Encoding map hash mismatch: "
            f"{digest} != {_ENCODING_MAP_SHA256}"
        )
    return maps


def _is_forbidden_control(value: str) -> bool:
    return (
        unicodedata.category(value) == "Cc"
        and value not in _ALLOWED_WHITESPACE_CONTROLS
    )


def _origin_key(
    font: str,
    origin: Any,
) -> tuple[str, tuple[float, float]]:
    values = tuple(round(float(value), 3) for value in origin)
    if len(values) != 2:
        raise LegacyCFontRecoveryError(
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
                raise LegacyCFontRecoveryError(
                    f"font {font}: conflicting texttrace at {key[1]}"
                )
            trace[key] = value
    return trace


def _render_line(
    line: dict,
    trace_by_origin: dict[
        tuple[str, tuple[float, float]], tuple[int, int]
    ],
    font_maps: dict[str, dict[int, str]],
    trace_relations: set[tuple[str, int, int, int]],
    *,
    page_number: int,
) -> _LineRecovery:
    parts: list[str] = []
    mapped_chars = 0
    synthetic_chars = 0
    passthrough_chars = 0
    for span in line.get("spans", []):
        font = str(span.get("font") or "")
        mapping = font_maps.get(font)
        for char in span.get("chars", []):
            value = str(char.get("c") or "")
            if len(value) != 1:
                raise LegacyCFontRecoveryError(
                    f"page {page_number} font {font}: "
                    f"char length {len(value)} is not 1"
                )
            if char.get("synthetic") is True:
                parts.append(value)
                synthetic_chars += 1
                continue
            code = ord(value)
            if mapping is not None and code in mapping:
                key = _origin_key(font, char.get("origin", ()))
                trace = trace_by_origin.get(key)
                if trace is None:
                    raise LegacyCFontRecoveryError(
                        f"page {page_number} font {font}: "
                        f"missing texttrace for raw code {code}"
                    )
                trace_unicode, glyph_id = trace
                if trace_unicode != 0xFFFD:
                    raise LegacyCFontRecoveryError(
                        f"page {page_number} font {font}: trace U+"
                        f"{trace_unicode:04X} != U+FFFD for raw code {code}"
                    )
                trace_relations.add(
                    (font, code, trace_unicode, glyph_id)
                )
                parts.append(mapping[code])
                mapped_chars += 1
                continue
            if value == "\ufffd" or _is_forbidden_control(value):
                raise LegacyCFontRecoveryError(
                    f"page {page_number} font {font}: "
                    f"unmapped suspicious U+{code:04X}"
                )
            parts.append(value)
            passthrough_chars += 1
    return _LineRecovery(
        text=unicodedata.normalize("NFC", "".join(parts).rstrip()),
        mapped_chars=mapped_chars,
        synthetic_chars=synthetic_chars,
        passthrough_chars=passthrough_chars,
    )


def _validate_trace_relations(
    relations: set[tuple[str, int, int, int]],
) -> None:
    canonical = [list(item) for item in sorted(relations)]
    digest = _canonical_sha256(canonical)
    if digest != _TRACE_RELATION_SHA256:
        raise LegacyCFontRecoveryError(
            "C-font raw-code/glyph relation hash mismatch: "
            f"{digest} != {_TRACE_RELATION_SHA256}"
        )


def _recover_document(
    document: Any,
    font_maps: dict[str, dict[int, str]],
) -> LegacyCFontRecovery:
    import pymupdf

    page_texts: list[str] = []
    mapped_chars = 0
    synthetic_chars = 0
    passthrough_chars = 0
    image_blocks_skipped = 0
    trace_relations: set[tuple[str, int, int, int]] = set()

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
                    font_maps,
                    trace_relations,
                    page_number=page_number,
                )
                recovered_lines.append(result.text)
                mapped_chars += result.mapped_chars
                synthetic_chars += result.synthetic_chars
                passthrough_chars += result.passthrough_chars
            block_text = "\n".join(recovered_lines).strip()
            if block_text:
                recovered_blocks.append(block_text)
        page_texts.append("\n\n".join(recovered_blocks))

    if mapped_chars == 0:
        raise LegacyCFontRecoveryError(
            "legacy font maps found but no characters were mapped"
        )
    _validate_trace_relations(trace_relations)

    raw_md_parts: list[str] = []
    for page_number, page_text in enumerate(page_texts, start=1):
        raw_md_parts.extend(
            [_PAGE_MARKER.format(page=page_number), page_text, ""]
        )
    markdown = "\n\n".join(raw_md_parts).rstrip() + "\n"

    if "\ufffd" in markdown:
        raise LegacyCFontRecoveryError(
            "recovered text still contains U+FFFD"
        )
    forbidden_count = sum(
        _is_forbidden_control(char) for char in markdown
    )
    if forbidden_count:
        raise LegacyCFontRecoveryError(
            f"recovered text still contains {forbidden_count} controls"
        )
    nonspace = "".join(markdown.split())
    letter_or_number_count = sum(
        unicodedata.category(char)[:1] in {"L", "N"}
        for char in nonspace
    )
    readable_ratio = letter_or_number_count / max(len(nonspace), 1)
    if readable_ratio < 0.50:
        raise LegacyCFontRecoveryError(
            f"recovered letter/number ratio too low: {readable_ratio:.4%}"
        )

    return LegacyCFontRecovery(
        markdown=markdown,
        pages_total=len(page_texts),
        mapped_chars=mapped_chars,
        synthetic_chars=synthetic_chars,
        passthrough_chars=passthrough_chars,
        font_count=len(font_maps),
        image_blocks_skipped=image_blocks_skipped,
        content_sha256=hashlib.sha256(
            markdown.encode("utf-8")
        ).hexdigest(),
    )


def recover_legacy_c_number_pdf(
    path: Path,
) -> LegacyCFontRecovery | None:
    """只读尝试恢复审计过的 Adv* C<number> PDF；不适用时返回 None。"""
    import pymupdf

    document = pymupdf.open(path)
    try:
        font_maps = _build_font_maps(document)
        if font_maps is None:
            return None
        return _recover_document(document, font_maps)
    finally:
        document.close()
