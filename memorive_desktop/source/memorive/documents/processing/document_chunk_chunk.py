"""Chunking, the fourth step of the pipeline.

Splits [CleanMD] along its structure (sections, paragraphs, tables, captions), never at a fixed
length and never inside a table or a sentence. Writes [Chunks][paper_id] title.jsonl, one chunk per
line: text, eight metadata fields, chunk_schema_version, hash and type.

Tables across pages: after cleaning has removed headers and footers, only a page marker sits between
the two halves. Two table blocks on neighbouring pages with the same columns, where the second
repeats the header, are one table: they become one table chunk with a page span (page_start !=
page_end), logged at detail level.

Fields leave without HTML:
  - section_path takes only numbered sections (1., 2.1.) and a list of unnumbered academic sections
    (Abstract, References ...); other headings on the first pages (title, authors, affiliations,
    journal) count as Front Matter, so an author's name never becomes a section path. section_path
    has no HTML tag at all, <br> included.
  - In chunk text, a pair <sup>...</sup> holding a number becomes an explicit ^..., so 10<sup>15</sup>
    does not turn into 1015; other comments and tags are removed. <br> stays inside table cells and
    becomes a space elsewhere. The page and page span markers stay out of the text and only set the
    page numbers.
  - CleanMD must start with its --- front matter, so the front matter never ends up in a chunk.
It only splits and labels; no quality judgment, no importance, no summary, no model.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml
from memorive.documents.front_matter import split_front_matter
from memorive.platform.logs import log, log_error

from memorive.documents.processing.config import library_root, now_iso
from memorive.documents.processing.document_conversion.diagnostics import parse_source_marker
from memorive.documents.processing.errors import DocumentProcessingError
from memorive.platform.naming import display_title, file_name, folder_name

# The version of the chunk structure. Chunking alone defines it; embedding only copies it into the
# vector metadata. It goes up by one when the chunking rules or the fields change, so after a library
# re-embed one can tell which chunk structure a vector came from.
CHUNK_SCHEMA_VERSION = "7"

# A page marker line: the lines after it are on physical page N (from 1). The research index
# (research/workspace/evidence.py) reads the same markers.
PAGE_MARKER = re.compile(r"^\s*<!--\s*memorive:page:(\d+)\s*-->\s*$")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_TAG_NO_BR = re.compile(r"</?(?!br\b)[a-zA-Z][a-zA-Z0-9]*(?:\s[^>]*)?/?>", re.I)  # every tag except br
_ANY_TAG = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*(?:\s[^>]*)?/?>", re.I)            # every tag, br included
_SUP_PAIR = re.compile(r"<sup(?:\s[^<>]*)?>(.*?)</sup\s*>", re.I | re.S)
_NUMERIC_SUPERSCRIPT = re.compile(
    r"(?P<leading>\s*)(?P<value>[+\-−]?\d+(?:[.,]\d+)?)(?P<trailing>\s*)\Z"
)
_CAPTION_RE = re.compile(r"^\s*(Fig\.?|Figure|Table|图|表)\s*\d", re.I)

# Unnumbered academic sections that go into section_path (other unknown headings are Front Matter)
_NAMED_SECTION = re.compile(
    r"^(abstract|keywords?|highlights|graphical\s+abstract|nomenclature|abbreviations|"
    r"introduction|background|related\s+work|literature\s+review|"
    r"materials?(\s+and\s+methods?)?|methods?|methodology|experimental(\s+.*)?|theory|"
    r"results?(\s+and\s+discussions?)?|discussions?|conclusions?|summary|"
    r"references?|bibliography|acknowledge?ments?|funding|"
    r"declarations?(\s+.*)?|conflict[s]?\s+of\s+interest|competing\s+interests?|"
    r"supplementary(\s+.*)?|appendix|appendices|author\s+contributions?|data\s+availability)"
    r"\b", re.I)

# Chinese section names; \b does not work for Chinese, so they are matched as a prefix with ^.
_NAMED_SECTION_ZH = re.compile(r"^(摘要|关键词|引言|绪论|结论|总结|目录|参考文献|致谢|附录|第[一二三四五六七八九十百零〇\d]+[章节篇])")

# Front matter field names: none of them may appear in a chunk, the front matter is removed first
FRONTMATTER_FIELDS = ("clean_report", "removed_repeated_headers", "running_header_footer",
                      "converter_artifact_comments", "removed_counts", "page_markers", "cleaned_at")


def _preserve_numeric_superscript(match: re.Match) -> str:
    """Turn only paired numeric superscripts into explicit exponents; other superscripts are removed as tags."""
    body = _ANY_TAG.sub("", match.group(1))
    numeric = _NUMERIC_SUPERSCRIPT.fullmatch(body)
    if not numeric:
        return match.group(0)
    return f"{numeric.group('leading')}^{numeric.group('value')}{numeric.group('trailing')}"


def _strip(s: str, *, keep_br: bool) -> str:
    """Keep numeric exponents first, then remove comments and the other tags. keep_br=True (tables)
    keeps <br> (a line break inside a cell); keep_br=False turns it into a space."""
    s = _COMMENT_RE.sub("", s)
    s = _SUP_PAIR.sub(_preserve_numeric_superscript, s)
    if keep_br:
        s = _TAG_NO_BR.sub("", s)
    else:
        s = _BR_RE.sub(" ", s)
        s = _ANY_TAG.sub("", s)
        from memorive.documents.processing.chinese_structure import join_han_spaces
        s = join_han_spaces(s)
    return re.sub(r"[ \t]+", " ", s)


def _blocktype(lines: list) -> str:
    if PAGE_MARKER.match(lines[0]):
        return "pagemarker"
    if parse_source_marker(lines[0]) is not None:
        return "sourcemarker"
    if any("Start of picture text" in l for l in lines):           # pymupdf's marker for OCR text inside a picture
        return "picture_text"
    s = lines[0].strip()
    if s.startswith("#"):
        return "heading"
    if s.startswith("|"):
        return "table"
    if _CAPTION_RE.match(s):
        return "figure_caption"
    if s.startswith(">"):
        return "blockquote"
    if re.match(r"^([-*+]|\d+\.)\s", s) or re.match(r'^[（(]?\d+[)）]', s):
        return "list"
    return "paragraph"


def _table_rows(lines):
    return [l for l in lines if l.strip().startswith("|")]


def _colcount(row):
    return row.count("|")


def _norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[_*]", "", s)).strip().lower()


def _is_anc(a, b):
    return b == a or b.startswith(a + ".")


def chunk(cleanmd_path, *, target: str = "sandbox") -> Path:
    """Split [CleanMD] into [Chunks].jsonl; returns its path."""
    p = Path(cleanmd_path)
    if not p.exists():
        log_error("DocumentChunk", "CleanMD 不存在", context={
            "step": "chunk", "error": f"CleanMD 不存在: {p}", "source": str(p)})
        raise DocumentProcessingError(f"CleanMD 不存在: {p}")

    text = p.read_text(encoding="utf-8")
    # CleanMD must start with its --- front matter ---, or it is not split
    parts = split_front_matter(text)
    if parts is None or parts[0]:
        log_error("DocumentChunk", "CleanMD frontmatter 非法", context={
            "step": "chunk",
            "error": "CleanMD 未以 --- frontmatter --- 开头;拒绝切块以防 frontmatter 混入正文。",
            "source": str(p)})
        raise DocumentProcessingError("CleanMD frontmatter 非法(需 --- … --- 开头)")
    fm = yaml.safe_load(parts[1]) or {}
    from memorive.documents.processing.chinese_structure import structure_lines, merge_wrapped_paragraphs, is_running_heading
    body = structure_lines(parts[2])
    paper_id = fm["paper_id"]
    ownership = fm.get("data_ownership")

    # ── blocks at blank lines; each page or page span marker is a block of its own ──
    raw, cur = [], []
    for line in body.splitlines():
        if PAGE_MARKER.match(line) or parse_source_marker(line) is not None:
            if cur:
                raw.append(cur)
                cur = []
            raw.append([line])
            continue
        if line.strip() == "":
            if cur:
                raw.append(cur)
                cur = []
        else:
            cur.append(line)
    if cur:
        raw.append(cur)

    # Two-column extraction may encounter a subsection before its parent title.
    # Resolve known Chinese numbered ancestors from this document, not memory.
    chinese_titles = {}
    for lines in raw:
        if _blocktype(lines) == 'heading':
            h = re.sub(r'[*_]', '', _strip(lines[0].lstrip('#').strip(), keep_br=False)).strip()
            m = re.match(r'^(\d+(?:\.\d+)*)\.?\s+(.+)', h)
            if m and re.search(r'[\u3400-\u9fff]', h) and not is_running_heading(h):
                chinese_titles.setdefault(m.group(1), h)

    # ── type, page and section of each block (markers and headings only update the state) ──
    blocks = []
    page = 0
    source_anchor = None
    num_stack, named = [], None

    def cur_section():
        if num_stack:
            return " > ".join(t for _, t in num_stack)
        if named:
            return named
        return "Front Matter"

    for lines in raw:
        t = _blocktype(lines)
        if t == "pagemarker":
            page = int(PAGE_MARKER.match(lines[0]).group(1))
            continue
        if t == "sourcemarker":
            source_anchor = parse_source_marker(lines[0])
            continue
        if t == "heading":
            htext = _strip(lines[0].lstrip("#").strip(), keep_br=False).strip()
            htext = re.sub(r"[*_]", "", htext).strip()             # without emphasis marks (**2.2.1 AnMBR** -> 2.2.1 AnMBR), or the number would not match
            if is_running_heading(htext):
                continue
            m = re.match(r"^(\d+(?:\.\d+)*)\.?\s+(.*)", htext)
            if m and not ("." not in m.group(1) and len(m.group(1)) >= 4):   # (1) a numbered section (a bare number of 4 or more digits is a year or a cover line, not a section)
                num = m.group(1)
                while num_stack and not _is_anc(num_stack[-1][0], num):
                    num_stack.pop()
                num_stack.append((num, htext))
                named = None
                if num in chinese_titles:
                    parts_num = num.split('.')
                    prefixes = ['.'.join(parts_num[:n]) for n in range(1,len(parts_num)+1)]
                    num_stack = [(n,chinese_titles[n]) for n in prefixes if n in chinese_titles]
            elif _NAMED_SECTION.match(htext) or _NAMED_SECTION_ZH.match(htext):   # (2) a listed unnumbered academic section (English or Chinese)
                named = htext
                num_stack = []
            # (3) any other heading (cover, statement, authors, affiliations, a chapter line) is not a
            #     section; section_path stays (Front Matter or the previous section)
            continue
        blocks.append({
            "type": t,
            "lines": lines,
            "page": page,
            "page_end": page,
            "section": cur_section(),
            "source_anchor": dict(source_anchor) if source_anchor else None,
        })

    blocks = merge_wrapped_paragraphs(blocks)
    # ── tables across pages: neighbouring table blocks (the second on a later page) with the same
    #    columns, the second repeating the header, are joined ──
    merged, spans, i = [], [], 0
    while i < len(blocks):
        b = blocks[i]
        nb = blocks[i + 1] if i + 1 < len(blocks) else None
        if (b["type"] == "table" and nb and nb["type"] == "table" and nb["page"] > b["page"]):
            a_rows, b_rows = _table_rows(b["lines"]), _table_rows(nb["lines"])
            # The same table continues: same columns and the header repeated on the next page. pymupdf
            # repeats a table's header on its second page, which is the evidence; the same number of
            # columns alone would join two different tables.
            if (a_rows and b_rows and _colcount(a_rows[0]) == _colcount(b_rows[0])
                    and _norm(b_rows[0]) == _norm(a_rows[0])):
                add = b_rows[2:] if len(b_rows) > 2 else []         # drop the second half's repeated header and separator row
                merged.append({"type": "table", "lines": b["lines"] + add,
                               "page": b["page"], "page_end": nb["page_end"],
                               "section": b["section"],
                               "source_anchor": b.get("source_anchor")})
                spans.append({"page_start": b["page"], "page_end": nb["page_end"], "section": b["section"]})
                i += 2
                continue
        merged.append(b)
        i += 1

    for sp in spans:                                                # every join is logged at detail level
        log("DocumentChunk", "检测到跨页结构、保护合并", is_error=True, context={
            "step": "crosspage_table_merge",
            "error": (f"跨页表 page {sp['page_start']}→{sp['page_end']}:相邻页两表块列结构相同、"
                      f"下半页表头重复 → 按表身份连续性合并为一个 table chunk,不拆、不当两张。"),
            "paper_id": paper_id, "section": sp["section"],
            "page_start": str(sp["page_start"]), "page_end": str(sp["page_end"])})

    # ── the chunks: text (keep_br by block type), the eight fields, chunk_schema_version, hash, type ──
    from memorive.documents.processing.native_section_alignment import build_native_section_index, align_numbered_section, align_bibliography
    native_index, native_receipt = ({}, {'status': 'NOT_APPLICABLE'})
    if chinese_titles and fm.get('original_format') == 'pdf':
        native_index, native_receipt = build_native_section_index(p.parent, fm.get('source_sha256'))
    section_corrections = []
    chunks, seq, created, srcname = [], 0, now_iso(), p.name
    for b in merged:
        txt = _strip("\n".join(b["lines"]), keep_br=(b["type"] == "table")).strip()
        if not txt:                                                # a block of comments only, or empty: no chunk
            continue
        seq += 1
        source_anchor_projection = b.get("source_anchor")
        if source_anchor_projection is None and b["page"] > 0:
            source_anchor_projection = {
                "kind": "page",
                "ordinal": b["page"],
                "label": None,
            }
        if source_anchor_projection is not None:
            source_anchor_projection = {
                **source_anchor_projection,
                "source_sha256": fm.get("source_sha256"),
                "converter_profile_id": fm.get("converter_profile_id"),
                "conversion_receipt_id": fm.get("convert_receipt_id"),
            }
        alignment = ((align_bibliography(b, txt, native_index)
                      or align_numbered_section(b, txt, native_index, chinese_titles))
                     if native_index else None)
        section_path = alignment['section'] if alignment else b['section']
        if alignment:
            section_corrections.append({'chunk_id': f'{paper_id}#c{seq:04d}',
                                        'previous_section': b['section'], **alignment})
        chunks.append({
            "paper_id": paper_id,
            "chunk_id": f"{paper_id}#c{seq:04d}",
            "page_start": b["page"],
            "page_end": b["page_end"],
            "section_path": section_path,
            "source_file": srcname,
            "data_ownership": ownership,
            "created_at": created,
            "chunk_schema_version": CHUNK_SCHEMA_VERSION,   # defined by chunking; embedding only copies it
            "block_type": alignment.get('block_type', b['type']) if alignment else b['type'],
            "source_anchor": source_anchor_projection,
            "chunk_hash": hashlib.sha1(txt.encode("utf-8")).hexdigest()[:12],
            "text": txt,
        })
        if alignment:
            chunks[-1]['section_resolution'] = {
                'method': 'EXACT_SOURCE_NATIVE_TEXT_ALIGNMENT',
                'source_sha256': fm.get('source_sha256'),
                'previous_section': b['section'], **alignment,
            }

    disp = display_title(fm["title"])
    out = library_root(target) / folder_name(paper_id, disp) / file_name(
        "Chunks", paper_id, disp, "jsonl")
    out.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in chunks),
                   encoding="utf-8")
    log("DocumentChunk", "切块完成", data={
        "paper_id": paper_id, "chunks": len(chunks),
        "crosspage_merges": len(spans), "chunks_file": str(out)})
    if native_receipt['status'] != 'NOT_APPLICABLE':
        log('DocumentChunk', '中文章节原文对齐', data={'paper_id': paper_id,
            'native_status': native_receipt['status'], 'corrections': section_corrections})
    return out
