"""M1d 切块 —— 传送带第四节。

把 [CleanMD] **按结构切块**(章节/段落/表格/图注自然边界),严禁定长硬切、严禁从表格或句子
中间截断。产出 `[Chunks][paper_id] 标题.jsonl`(一行一 chunk:text + 八项 metadata + chunk_schema_version + hash + type)。

跨页表护栏(承第2步命门):在 M1c 删完页眉页脚**之后**做——两半表间只剩纯页标记。判据 =
**表身份连续性**(相邻页两表块、列结构相同 且 下半页表头重复)→ 合并成一个 table chunk +
pagespan(page_start≠page_end),**写 M11 detail(不静默)**;不当成两张独立表放过。

出库字段去 HTML(承补点一/二/三):
  - **section_path**:只认「编号章节(1./2.1.)」+「白名单无编号学术章节(Abstract/References/…)」;
    其余未识别的首页标题/作者/单位/期刊信息一律归 **Front Matter**——不盲信转换器 heading,避免
    "作者名当章节路径"污染 T4 来源锚点。section_path **彻底无任何 HTML 标签(含 <br>)**。
  - **chunk text**:成对且内容为数字指数的 `<sup>…</sup>` 先转为显式 `^…`,防 `10<sup>15</sup>`
    被删标签后歧义粘成 `1015`;其余注释/标签仍按旧规则删除。**表格单元格 <br> 保留**(结构),
    段落/引用/图片文字里的 <br> 转空格;工程标记 pros:page/pagespan 不进 text、只算 metadata 页码。
  - **frontmatter 泄漏护栏**:切块前校验 CleanMD 以 --- frontmatter 开头,防 frontmatter 混入正文。
守边界:只切分 + 标类型,不判质量(M6)/ 不赋重要性(M7)/ 不摘要 / 不调模型。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml
from m11_log import log, log_error

from .config import library_root, now_iso
from .document_conversion.diagnostics import parse_source_marker
from .errors import M1Error
from .naming import display_title, file_name, folder_name

# chunk 结构版本(承 T3 第3步拍板③):由 M1d 产出、描述 **chunk 结构**的版本。
# M1d 是这字段的唯一定义者;M1e 只承带进向量 metadata、不当定义者(嵌入行为 ≠ chunk 结构)。
# 切块规则 / 出库字段结构性变更时 +1,便于将来整库重嵌时定位「某条向量按哪版 chunk 结构切」。
CHUNK_SCHEMA_VERSION = "7"

_PAGE_RE = re.compile(r"^\s*<!--\s*pros:page:(\d+)\s*-->\s*$")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_TAG_NO_BR = re.compile(r"</?(?!br\b)[a-zA-Z][a-zA-Z0-9]*(?:\s[^>]*)?/?>", re.I)  # 除 br 外所有标签
_ANY_TAG = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*(?:\s[^>]*)?/?>", re.I)            # 所有标签含 br
_SUP_PAIR = re.compile(r"<sup(?:\s[^<>]*)?>(.*?)</sup\s*>", re.I | re.S)
_NUMERIC_SUPERSCRIPT = re.compile(
    r"(?P<leading>\s*)(?P<value>[+\-−]?\d+(?:[.,]\d+)?)(?P<trailing>\s*)\Z"
)
_CAPTION_RE = re.compile(r"^\s*(Fig\.?|Figure|Table|图|表)\s*\d", re.I)

# 无编号但正常进 section_path 的学术章节白名单(其余未识别标题归 Front Matter)
_NAMED_SECTION = re.compile(
    r"^(abstract|keywords?|highlights|graphical\s+abstract|nomenclature|abbreviations|"
    r"introduction|background|related\s+work|literature\s+review|"
    r"materials?(\s+and\s+methods?)?|methods?|methodology|experimental(\s+.*)?|theory|"
    r"results?(\s+and\s+discussions?)?|discussions?|conclusions?|summary|"
    r"references?|bibliography|acknowledge?ments?|funding|"
    r"declarations?(\s+.*)?|conflict[s]?\s+of\s+interest|competing\s+interests?|"
    r"supplementary(\s+.*)?|appendix|appendices|author\s+contributions?|data\s+availability)"
    r"\b", re.I)

# 中文前后置章节名(无编号白名单)。`\b` 对中文不适用,故单列、按 `^` 前缀锚定匹配。
_NAMED_SECTION_ZH = re.compile(r"^(摘要|关键词|引言|绪论|结论|总结|目录|参考文献|致谢|附录|第[一二三四五六七八九十百零〇\d]+[章节篇])")

# frontmatter 字段名(泄漏护栏用):切块前必已剥 frontmatter,任何 chunk text 都不该含这些
FRONTMATTER_FIELDS = ("clean_report", "removed_repeated_headers", "running_header_footer",
                      "converter_artifact_comments", "removed_counts", "page_markers", "cleaned_at")


def _preserve_numeric_superscript(match: re.Match) -> str:
    """只把成对数字上标转成显式指数；非数字上标交既有标签删除规则，避免扩大语义改写。"""
    body = _ANY_TAG.sub("", match.group(1))
    numeric = _NUMERIC_SUPERSCRIPT.fullmatch(body)
    if not numeric:
        return match.group(0)
    return f"{numeric.group('leading')}^{numeric.group('value')}{numeric.group('trailing')}"


def _strip(s: str, *, keep_br: bool) -> str:
    """去 HTML 前保留成对数字上标的指数边界；再删注释与其余标签。
    keep_br=True(表格)保留 <br>(单元格换行,结构);keep_br=False 把 <br> 转空格。"""
    s = _COMMENT_RE.sub("", s)
    s = _SUP_PAIR.sub(_preserve_numeric_superscript, s)
    if keep_br:
        s = _TAG_NO_BR.sub("", s)
    else:
        s = _BR_RE.sub(" ", s)
        s = _ANY_TAG.sub("", s)
        from .chinese_structure import join_han_spaces
        s = join_han_spaces(s)
    return re.sub(r"[ \t]+", " ", s)


def _blocktype(lines: list) -> str:
    if _PAGE_RE.match(lines[0]):
        return "pagemarker"
    if parse_source_marker(lines[0]) is not None:
        return "sourcemarker"
    if any("Start of picture text" in l for l in lines):           # pymupdf 标:图片内 OCR 文字
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
    """切 [CleanMD] → [Chunks].jsonl。返回 jsonl 路径。"""
    p = Path(cleanmd_path)
    if not p.exists():
        log_error("M1d", "CleanMD 不存在", context={
            "step": "chunk", "error": f"CleanMD 不存在: {p}", "source": str(p)})
        raise M1Error(f"CleanMD 不存在: {p}")

    text = p.read_text(encoding="utf-8")
    # frontmatter 泄漏护栏:CleanMD 必以 --- frontmatter 开头(≥2 个 ---),否则拒切,防 frontmatter 当正文
    if not text.startswith("---") or text.count("---") < 2:
        log_error("M1d", "CleanMD frontmatter 非法", context={
            "step": "chunk",
            "error": "CleanMD 未以 --- frontmatter --- 开头;拒绝切块以防 frontmatter 混入正文。",
            "source": str(p)})
        raise M1Error("CleanMD frontmatter 非法(需 --- … --- 开头)")
    parts = text.split("---", 2)
    fm = yaml.safe_load(parts[1]) or {}
    from .chinese_structure import structure_lines, merge_wrapped_paragraphs, is_running_heading
    body = structure_lines(parts[2])
    paper_id = fm["paper_id"]
    ownership = fm.get("data_ownership")

    # ── 空行分块；两类工程 marker 强制形成独立状态块 ──
    raw, cur = [], []
    for line in body.splitlines():
        if _PAGE_RE.match(line) or parse_source_marker(line) is not None:
            if cur:
                raw.append(cur); cur = []
            raw.append([line])
            continue
        if line.strip() == "":
            if cur:
                raw.append(cur); cur = []
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

    # ── 标注 type / page / section(页标记与标题不成块,只更新状态)──
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
            page = int(_PAGE_RE.match(lines[0]).group(1)); continue
        if t == "sourcemarker":
            source_anchor = parse_source_marker(lines[0]); continue
        if t == "heading":
            htext = _strip(lines[0].lstrip("#").strip(), keep_br=False).strip()
            htext = re.sub(r"[*_]", "", htext).strip()             # 剥 markdown 强调符(承 _norm 同一 idiom):**2.2.1 AnMBR** → 2.2.1 AnMBR,否则编号正则被行首 * 挡住
            if is_running_heading(htext):
                continue
            m = re.match(r"^(\d+(?:\.\d+)*)\.?\s+(.*)", htext)
            if m and not ("." not in m.group(1) and len(m.group(1)) >= 4):   # ① 编号章节(裸 ≥4 位数字=年份/封面如「2026 届…」,退回未识别)
                num = m.group(1)
                while num_stack and not _is_anc(num_stack[-1][0], num):
                    num_stack.pop()
                num_stack.append((num, htext)); named = None
                if num in chinese_titles:
                    parts_num = num.split('.')
                    prefixes = ['.'.join(parts_num[:n]) for n in range(1,len(parts_num)+1)]
                    num_stack = [(n,chinese_titles[n]) for n in prefixes if n in chinese_titles]
            elif _NAMED_SECTION.match(htext) or _NAMED_SECTION_ZH.match(htext):   # ② 白名单无编号学术章节(英文 + 中文前后置)
                named = htext; num_stack = []
            # ③ 其余未识别标题(封面/声明/作者/单位/第X章行)→ 不当章节,section_path 保持(Front Matter / 上一章节)
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
    # ── 跨页表护栏:相邻表块(下块页更大)+ 列结构相同 且 下半页表头重复 → 合并 ──
    merged, spans, i = [], [], 0
    while i < len(blocks):
        b = blocks[i]
        nb = blocks[i + 1] if i + 1 < len(blocks) else None
        if (b["type"] == "table" and nb and nb["type"] == "table" and nb["page"] > b["page"]):
            a_rows, b_rows = _table_rows(b["lines"]), _table_rows(nb["lines"])
            # 表身份连续性:相邻页两表块、**列结构相同 且 下半页表头与上半页重复**。
            # pymupdf 跨页表会把表头重生到下半页 → 表头重复正是"同一张表"的连续性证据;
            # 只凭"列数相同"会误并两张恰好同列的不同表,故要求表头重复、取更稳一侧。
            if (a_rows and b_rows and _colcount(a_rows[0]) == _colcount(b_rows[0])
                    and _norm(b_rows[0]) == _norm(a_rows[0])):
                add = b_rows[2:] if len(b_rows) > 2 else []         # 丢弃 B 重复的表头 + 分隔行
                merged.append({"type": "table", "lines": b["lines"] + add,
                               "page": b["page"], "page_end": nb["page_end"],
                               "section": b["section"],
                               "source_anchor": b.get("source_anchor")})
                spans.append({"page_start": b["page"], "page_end": nb["page_end"], "section": b["section"]})
                i += 2; continue
        merged.append(b); i += 1

    for sp in spans:                                                # 不静默:每次保护合并写 M11 detail
        log("M1d", "检测到跨页结构、保护合并", is_error=True, context={
            "step": "crosspage_table_merge",
            "error": (f"跨页表 page {sp['page_start']}→{sp['page_end']}:相邻页两表块列结构相同、"
                      f"下半页表头重复 → 按表身份连续性合并为一个 table chunk,不拆、不当两张。"),
            "paper_id": paper_id, "section": sp["section"],
            "page_start": str(sp["page_start"]), "page_end": str(sp["page_end"])})

    # ── 生成 chunks(text 按 block_type 分叉 keep_br;section_path 已全剥;八项 + chunk_schema_version + hash + type)──
    from .native_section_alignment import build_native_section_index, align_numbered_section, align_bibliography
    native_index, native_receipt = ({}, {'status': 'NOT_APPLICABLE'})
    if chinese_titles and fm.get('original_format') == 'pdf':
        native_index, native_receipt = build_native_section_index(p.parent, fm.get('source_sha256'))
    section_corrections = []
    chunks, seq, created, srcname = [], 0, now_iso(), p.name
    for b in merged:
        txt = _strip("\n".join(b["lines"]), keep_br=(b["type"] == "table")).strip()
        if not txt:                                                # 纯注释/空块 → 跳过,不产空 chunk
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
            "chunk_schema_version": CHUNK_SCHEMA_VERSION,   # M1d 产出、描述 chunk 结构;M1e 只承带
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
    log("M1d", "切块完成", data={
        "paper_id": paper_id, "chunks": len(chunks),
        "crosspage_merges": len(spans), "chunks_file": str(out)})
    if native_receipt['status'] != 'NOT_APPLICABLE':
        log('M1d', '中文章节原文对齐', data={'paper_id': paper_id,
            'native_status': native_receipt['status'], 'corrections': section_corrections})
    return out
