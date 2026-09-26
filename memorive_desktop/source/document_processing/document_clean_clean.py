"""DocumentClean 清洗结构化 —— 传送带第三节。

对 [RawMD] 修页眉页脚 / 断行 / 表外噪音,输出 [CleanMD](ServiceContracts 执行层中间产物)。
**保留**:页标记 `<!-- memorive:page:N -->`、DOCUMENT-FORMATS typed source marker、章节标题、
表格(原子块)、图注、引用块——这些是
来源锚点与结构的物理基础。守边界:只删噪音、合断行、保锚点;**不判质量(EVIDENCE_REVIEW)、不摘要、不调模型**。

删页眉页脚保守(承第2步约束):只在**页首/页尾短行区**做跨页重复检测;表格行 / 章节标题 /
图注 / 参考文献条目一律不参与删除;删掉的每类记 `clean_report.removed_repeated_headers`。
断行合并**只在普通段落内部**,不跨页标记 / 标题 / 表格 / 列表 / 图注 / 引用 / 公式;宁可少合并、不错并。
跨页表的识别 / 合并 / pagespan **不在此做**,留 DocumentChunk(那时两半表间只剩纯页标记,才判得准)。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from runtime_log import log, log_error

from .config import library_root, now_iso
from .document_conversion.diagnostics import parse_source_marker
from .errors import DocumentProcessingError
from .naming import display_title, file_name, folder_name

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
# 只匹配出版社 masthead 的完整行(锚定 / 够特异)。参考文献条目、表格行、章节标题、图注
# 另受 _is_protected 保护。**不单列 DOI / ISSN 正则**——正文 / 参考文献里的合法 DOI、方法里的
# 标准号不得误删;masthead 页脚的 DOI+ISSN 与 © 版权同在一行,靠版权行整行匹配即连带删除。
_WHOLE_LINE_BOILERPLATE = [
    re.compile(r"^\s*Contents lists available at ", re.I),   # 行首:期刊数据库标识行
    re.compile(r"^\s*journal homepage:", re.I),              # 行首:期刊主页行
]
_COPYRIGHT_SUFFIX_RE = re.compile(
    r"\s*©\s*\d{4}[^\n]*Elsevier[^\n]*$",
    re.IGNORECASE,
)
_MASTHEAD_PREFIX_RE = re.compile(
    r"^\s*(?:\d{4}-\d{3}[\dXx]/|.*\bsee\s+front\s+matter\b)",
    re.IGNORECASE,
)
_ZONE = 2                # 页首/页尾各取几行作页眉/页脚候选区(保守)
# 页眉/页脚候选行长度上限:运行页眉/页脚天然是短行,正文句子长。只把"短行"当页眉候选,
# 即使某长正文行在分区里跨页重复也不会被误删(对抗审查 over_delete 加固)。env 可调。
_HEADER_MAX_LEN = int(os.environ.get("MEMORIVE_DOCUMENT_PROCESSING_HEADER_MAX_LEN") or 80)
_SENT_END = tuple(".!?:;)]}”’\"'")   # 句末标点(判是否续行)


def _is_protected(line: str) -> bool:
    """一律不参与删除的行:表格 / 标题 / 页标记 / 图表题注 / 参考文献条目。"""
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
    """归一化用于跨页重复比较:去首尾空白 / 去强调下划线星号 / 折叠空白 / 小写。"""
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
    if not text.startswith("---"):
        raise DocumentProcessingError("RawMD 缺 frontmatter(--- 开头);DocumentClean 需要 DocumentConvert 产物。")
    parts = text.split("---", 2)
    fm = yaml.safe_load(parts[1]) or {}
    body = parts[2].lstrip("\n")
    return fm, body


def clean(rawmd_path, *, target: str = "sandbox") -> Path:
    """清洗 [RawMD] → [CleanMD]。返回 [CleanMD] 路径。"""
    rawmd_path = Path(rawmd_path)
    if not rawmd_path.exists():
        log_error("M1c", "RawMD 不存在", context={
            "step": "clean", "error": f"RawMD 不存在: {rawmd_path}", "source": str(rawmd_path)})
        raise DocumentProcessingError(f"RawMD 不存在: {rawmd_path}")

    fm, body = _split_frontmatter(rawmd_path.read_text(encoding="utf-8"))
    lines = body.splitlines()

    # ── 页首/页尾候选区(仅在此做页眉页脚删除)──
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

    # ── 跨页重复检测(候选区内、非受保护行、且是"短行")──
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
            # 出版社 boilerplate:全局删(模式够明确、安全)
            if (
                not _is_protected(l)
                and any(
                    p.search(l) for p in _WHOLE_LINE_BOILERPLATE
                )
            ):
                removed["publisher_boilerplate"].append(s); continue
            l, removed_suffix = _strip_publisher_boilerplate(l)
            if removed_suffix is not None:
                removed["publisher_boilerplate"].append(
                    removed_suffix
                )
                s = l.strip()
                if not s:
                    continue
            # 页码 + 运行页眉:仅在页首/页尾区检测。Markdown 标题中的纯页码、
            # 完整期刊运行页眉可以越过“标题保护”；普通标题、Unicode 姓名/变量不参与。
            noise_kind = _classify_boundary_noise(
                l,
                in_header_zone=i in header_zone,
                in_footer_zone=i in footer_zone,
                is_repeated=_norm(l) in repeated,
            )
            if noise_kind is not None:
                removed[noise_kind].append(s); continue
        kept.append(l)

    # ── 去连字符 + 保守段内断行合并(不跨页标记/结构)──
    cleaned = _dehyphenate_join(kept)

    # 记录转换器注释(非 memorive;如 pymupdf 的 picture-text 标记):CleanMD 保留它们(溯源),
    # 由 DocumentChunk 出库时统一剥离(承补点一)。此处只登记、不删。
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

    # ── 落 [CleanMD](frontmatter 承 RawMD + cleaned_at + clean_report)──
    paper_id = fm["paper_id"]; title = fm["title"]
    disp = display_title(title)
    folder = library_root(target) / folder_name(paper_id, disp)
    out = folder / file_name("CleanMD", paper_id, disp, "md")
    new_fm = {k: fm.get(k) for k in (
        "paper_id", "title", "data_ownership", "doc_type", "original_format",
        "review_status", "convert_receipt_id", "source_sha256",
        "converter_profile_id") if k in fm}
    new_fm["source_rawmd"] = rawmd_path.name        # 值以 [ 开头,须靠 safe_dump 自动加引号
    new_fm["cleaned_at"] = now_iso()
    new_fm["clean_report"] = clean_report
    fm_text = yaml.safe_dump(new_fm, allow_unicode=True, sort_keys=False)   # 自动引号/嵌套,不手拼
    out.write_text("---\n" + fm_text + "---\n" + "\n".join(cleaned).strip() + "\n", encoding="utf-8")

    log("M1c", "清洗完成", data={
        "paper_id": paper_id, "cleanmd": str(out),
        "removed_counts": clean_report["removed_counts"], "page_markers": len(marker_pos)})
    return out


def _dehyphenate_join(lines: list) -> list:
    """① 行尾连字符 word-\\n word → 合并;② 段内句中断裂(上行非句末、下行小写续)跨空行合并。
    绝不跨页标记 / 标题 / 表格 / 列表 / 图注 / 引用 / 公式。宁可少合并、不错并。"""
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
            # 找下一个非空行
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and joinable(lines[j]):
                nxt = lines[j].lstrip()
                if s.endswith("-") and s[-2:-1].isalpha():        # 连字符续词
                    out.append(s[:-1] + nxt); i = j + 1; continue
                # 句中断裂续行:上行须以**字母**结尾(是被折断的词),避免把 URL / 符号结尾行
                # (如 https:// 、E = )误接一个空格毁掉内容(对抗审查 line_merge 加固)。
                if not s.endswith(_SENT_END) and s[-1:].isalpha() and nxt[:1].islower():
                    out.append(s + " " + nxt); i = j + 1; continue
        out.append(cur)
        i += 1
    return out
