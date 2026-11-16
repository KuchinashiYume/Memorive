"""Cleaning, the third step of the pipeline.

Removes headers, footers, broken lines and noise outside tables from [RawMD] and writes [CleanMD].
It keeps the page markers <!-- memorive:page:N -->, the typed source markers, section headings,
tables (as whole blocks), figure captions and quotes: what sources are anchored to and what gives
the structure. It only removes noise and joins lines; no quality judgment, no summary, no model.

Headers and footers are removed cautiously: only short lines at the top or bottom of a page that
repeat across pages; table rows, headings, captions and references are never removed; each kind
removed is counted in clean_report.removed_repeated_headers. Lines are joined only inside plain
paragraphs, never across a page marker, heading, table, list, caption, quote or formula; better too
few joins than a wrong one. Tables across pages are chunking's job, when only a page marker sits
between the two halves.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from memorive.documents.front_matter import split_front_matter
from memorive.platform.logs import log, log_error

from memorive.documents.processing.config import library_root, now_iso
from memorive.documents.processing.document_conversion.diagnostics import parse_source_marker
from memorive.documents.processing.errors import DocumentProcessingError
from memorive.platform.naming import display_title, file_name, folder_name

_PAGE_RE = re.compile(r"^\s*<!--\s*memorive:page:(\d+)\s*-->\s*$")
_PAGENUM_RE = re.compile(r"^\s*\d{1,4}\s*$")
_HEADING_RE = re.compile(r"^\s*#{1,6}\s+(?P<body>.*?)\s*$")
_JOURNAL_RUNNING_HEADER_RE = re.compile(
    r"^.{1,80}\bet\s+al\.\s*/\s*.+?\s+\d{1,4}\s*"
    r"\(\d{4}\)\s*\d{1,5}\s*[-–—]\s*\d{1,5}\s*$",
    re.IGNORECASE,
)
_CAPTION_RE = re.compile(r"^\s*(Fig\.?|Figure|Table)\s*\d", re.I)
_REF_RE = re.compile(r"^\s*\[\d+\]")
# Whole publisher masthead lines only (anchored and specific). References, table rows, headings and
# captions are protected by _is_protected as well. There is no DOI or ISSN pattern on its own: DOIs in
# the text and references, and standard numbers in methods, must stay; a masthead footer's DOI and
# ISSN sit on the copyright line and go with it.
_WHOLE_LINE_BOILERPLATE = [
    re.compile(r"^\s*Contents lists available at ", re.I),   # a journal database line
    re.compile(r"^\s*journal homepage:", re.I),              # a journal's homepage line
]
_COPYRIGHT_SUFFIX_RE = re.compile(
    r"\s*©\s*\d{4}[^\n]*Elsevier[^\n]*$",
    re.IGNORECASE,
)
_MASTHEAD_PREFIX_RE = re.compile(
    r"^\s*(?:\d{4}-\d{3}[\dXx]/|.*\bsee\s+front\s+matter\b)",
    re.IGNORECASE,
)
_ZONE = 2                # lines at the top and bottom of a page that may be a header or footer
# The longest line that may be a header or footer: running headers are short, text sentences long, so
# a long text line repeated across pages is never removed. Set by environment.
_HEADER_MAX_LEN = int(os.environ.get("MEMORIVE_DOCUMENT_PROCESSING_HEADER_MAX_LEN") or 80)
_SENT_END = tuple(".!?:;)]}”’\"'")   # sentence-ending punctuation (does the next line continue it?)


def _is_protected(line: str) -> bool:
    """Lines that are never removed: table rows, headings, page markers, captions, references."""
    s = line.strip()
    if not s:
        return False
    if s.startswith("|") or s.startswith("#"):
        return True
    if (
        _PAGE_RE.match(line)
        or parse_source_marker(line) is not None
        or _CAPTION_RE.match(s)
        or _REF_RE.match(s)
    ):
        return True
    return False


def _norm(line: str) -> str:
    """A line normalized for comparing across pages: trimmed, without emphasis marks, single spaces, lower case."""
    s = re.sub(r"[_*]", "", line).strip().lower()
    return re.sub(r"\s+", " ", s)


def _classify_boundary_noise(
    line: str,
    *,
    in_header_zone: bool,
    in_footer_zone: bool,
    is_repeated: bool,
) -> str | None:
    """Classify high-confidence boundary noise without inspecting language."""
    if not (in_header_zone or in_footer_zone):
        return None
    stripped = line.strip()
    if not stripped:
        return None
    heading_match = _HEADING_RE.fullmatch(line)
    heading_body = (
        heading_match.group("body").strip()
        if heading_match is not None
        else None
    )
    page_number_candidate = (
        heading_body if heading_body is not None else stripped
    )
    if (
        _PAGENUM_RE.fullmatch(page_number_candidate)
        and (
            in_footer_zone
            or (in_header_zone and heading_body is not None)
        )
    ):
        return "page_number"
    if (
        heading_body is not None
        and _JOURNAL_RUNNING_HEADER_RE.fullmatch(heading_body)
    ):
        return "running_header_footer"
    if is_repeated and not _is_protected(line):
        return "running_header_footer"
    return None


def _strip_publisher_boilerplate(
    line: str,
) -> tuple[str, str | None]:
    """Remove an audited copyright suffix while retaining preceding prose."""
    match = _COPYRIGHT_SUFFIX_RE.search(line)
    if match is None:
        return line, None
    prefix = line[:match.start()].rstrip()
    suffix = line[match.start():].strip()
    if not prefix:
        return "", suffix
    if _MASTHEAD_PREFIX_RE.match(prefix):
        return "", line.strip()
    return prefix, suffix


def _split_frontmatter(text: str):
    parts = split_front_matter(text)
    if parts is None or parts[0]:
        raise DocumentProcessingError("RawMD 缺 frontmatter(--- 开头);DocumentClean 需要 DocumentConvert 产物。")
    fm = yaml.safe_load(parts[1]) or {}
    body = parts[2].lstrip("\n")
    return fm, body


def clean(rawmd_path, *, target: str = "sandbox") -> Path:
    """Clean [RawMD] into [CleanMD]; returns the [CleanMD] path."""
    rawmd_path = Path(rawmd_path)
    if not rawmd_path.exists():
        log_error("M1c", "RawMD 不存在", context={
            "step": "clean", "error": f"RawMD 不存在: {rawmd_path}", "source": str(rawmd_path)})
        raise DocumentProcessingError(f"RawMD 不存在: {rawmd_path}")

    fm, body = _split_frontmatter(rawmd_path.read_text(encoding="utf-8"))
    lines = body.splitlines()

    # ── the top and bottom lines of each page (the only place headers and footers are removed) ──
    marker_pos = [i for i, l in enumerate(lines) if _PAGE_RE.match(l)]
    seg_bounds = marker_pos + [len(lines)]
    header_zone, footer_zone = set(), set()
    for k in range(len(marker_pos)):
        start, end = marker_pos[k] + 1, seg_bounds[k + 1]
        ne = [i for i in range(start, end) if lines[i].strip()]
        for i in ne[:_ZONE]:
            header_zone.add(i)
        for i in ne[-_ZONE:]:
            footer_zone.add(i)

    # ── lines repeated across pages (short, not protected, in those places) ──
    from collections import Counter
    zone_idx = header_zone | footer_zone
    counts = Counter(_norm(lines[i]) for i in zone_idx
                     if lines[i].strip() and not _is_protected(lines[i])
                     and len(lines[i].strip()) <= _HEADER_MAX_LEN)
    n_pages = max(len(marker_pos), 1)
    thresh = max(2, (n_pages + 1) // 2)
    repeated = {k for k, c in counts.items() if c >= thresh and k}

    removed = {"running_header_footer": [], "page_number": [], "publisher_boilerplate": []}
    kept = []
    for i, l in enumerate(lines):
        s = l.strip()
        if s:
            # publisher boilerplate: removed everywhere (the patterns are specific enough)
            if (
                not _is_protected(l)
                and any(
                    p.search(l) for p in _WHOLE_LINE_BOILERPLATE
                )
            ):
                removed["publisher_boilerplate"].append(s)
                continue
            l, removed_suffix = _strip_publisher_boilerplate(l)
            if removed_suffix is not None:
                removed["publisher_boilerplate"].append(
                    removed_suffix
                )
                s = l.strip()
                if not s:
                    continue
            # Page numbers and running headers, at the top and bottom only. A heading that is only a
            # page number or a full journal running header is not protected as a heading; other
            # headings and Unicode names or variables are left alone.
            noise_kind = _classify_boundary_noise(
                l,
                in_header_zone=i in header_zone,
                in_footer_zone=i in footer_zone,
                is_repeated=_norm(l) in repeated,
            )
            if noise_kind is not None:
                removed[noise_kind].append(s)
                continue
        kept.append(l)

    # ── hyphens and broken lines inside paragraphs (never across markers or structure) ──
    cleaned = _dehyphenate_join(kept)

    # Converter comments (not memorive's, such as pymupdf's picture-text markers) stay in CleanMD for
    # tracing and are removed by chunking; here they are only counted.
    converter_artifacts = sorted(set(
        c.strip() for c in re.findall(r"<!--.*?-->", "\n".join(cleaned), re.S) if "memorive:" not in c))

    clean_report = {
        "removed_repeated_headers": removed,
        "removed_counts": {k: len(v) for k, v in removed.items()},
        "converter_artifact_comments": converter_artifacts,
        "page_markers": len(marker_pos),
        "typed_source_markers": sum(
            parse_source_marker(line) is not None for line in cleaned
        ),
    }

    # ── write [CleanMD] (RawMD's front matter, cleaned_at, clean_report) ──
    paper_id = fm["paper_id"]
    title = fm["title"]
    disp = display_title(title)
    folder = library_root(target) / folder_name(paper_id, disp)
    out = folder / file_name("CleanMD", paper_id, disp, "md")
    new_fm = {k: fm.get(k) for k in (
        "paper_id", "title", "data_ownership", "doc_type", "original_format",
        "review_status", "convert_receipt_id", "source_sha256",
        "converter_profile_id") if k in fm}
    new_fm["source_rawmd"] = rawmd_path.name        # starts with [, so safe_dump quotes it
    new_fm["cleaned_at"] = now_iso()
    new_fm["clean_report"] = clean_report
    fm_text = yaml.safe_dump(new_fm, allow_unicode=True, sort_keys=False)   # safe_dump quotes and nests; nothing is assembled by hand
    out.write_text("---\n" + fm_text + "---\n" + "\n".join(cleaned).strip() + "\n", encoding="utf-8")

    log("M1c", "清洗完成", data={
        "paper_id": paper_id, "cleanmd": str(out),
        "removed_counts": clean_report["removed_counts"], "page_markers": len(marker_pos)})
    return out


def _dehyphenate_join(lines: list) -> list:
    """Join a word broken by a hyphen at the line end, and a sentence broken inside a paragraph (the
    line does not end a sentence and the next starts in lower case), across a blank line. Never across
    a page marker, heading, table, list, caption, quote or formula; better too few joins than a wrong one."""
    def joinable(l):
        s = l.strip()
        return bool(s) and not (
            s.startswith(("|", "#", ">", "-", "*", "+", "$"))
            or _PAGE_RE.match(l)
            or parse_source_marker(l) is not None
            or _CAPTION_RE.match(s)
            or _REF_RE.match(s)
        )

    out = []
    i = 0
    while i < len(lines):
        cur = lines[i]
        if joinable(cur):
            s = cur.rstrip()
            # the next non-empty line
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and joinable(lines[j]):
                nxt = lines[j].lstrip()
                if s.endswith("-") and s[-2:-1].isalpha():        # a word broken by a hyphen
                    out.append(s[:-1] + nxt)
                    i = j + 1
                    continue
                # A broken sentence: the line must end with a letter (a broken word), so a line ending
                # in a URL or a symbol (https://, E =) is not joined with a space that would spoil it.
                if not s.endswith(_SENT_END) and s[-1:].isalpha() and nxt[:1].islower():
                    out.append(s + " " + nxt)
                    i = j + 1
                    continue
        out.append(cur)
        i += 1
    return out
