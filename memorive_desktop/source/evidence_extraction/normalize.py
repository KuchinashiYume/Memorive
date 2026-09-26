"""EVIDENCE_EXTRACTION · 原文引用归一化(source_anchor v3「第一刀」;承之八「机器逐字符验真」)。

`quote` 与 `chunk` 原文做 substring 验真**前**,对二者**对称**归一化 —— 只抹平 PDF 抽取的排版
噪音,**绝不碰实词 / 数字 / 短横(除行末断词)/ 大小写 / 词序**。故一切「改了实词」的漂移
(`except≠after`、`5.2≠5.3`、`5.2≠5.20`、`increased≠decreased`、`mg≠μg`、漏 `not` / 漏否定)
归一化后仍**判不匹配**、当场挡下。

白名单**此七项、别无其他**(顺序有要害,见下):
  ① Unicode **NFKC**:全角→半角、连字 `ﬁ ﬂ`→`fi fl`、微符 `µ`(U+00B5)→`μ`(U+03BC)、
     上下标数字规整、全角空格 `　`→` `、不换行空格 NBSP→` ` 等;
  ④ **弯引号 / 撇号直化**:`‘ ’ ‛ ′`→`'`、`“ ” „ ‟ ″`→`"`(NFKC 不做此映射,须显式);
  ⑦ **Markdown emphasis / 邻接排版噪音**:`_term_` / `*term*` / `**term**` / `***term***` 的成对包裹符→空;
     折叠强调符移除后遗留的闭合标点前 / 左括号后空格,及仅在左括号内 `<`/`>` 到数字的空格;
     只认不贴字母数字的成对 delimiter,故 `snake_case`、`__init__`、`2*3`、`x**2`、`* list item` 不动;
  ⑥ **HTML 标签抹除**(07-09 修四):`<br>` / `<br/>` / `<sub>` / `</i>` 等 DOCUMENT_PROCESSING 表格 / PDF 抽取残留的 HTML 标签 →
     **空格**(那类是排版噪音、非内容;抹掉后忠实 quote 就能对上、留作证据,优于降级)。**只匹配真标签**
     (`<` 后紧跟字母的 `</?字母…>`),故数学 `COD < 100`、`5<10`、`x > y`(`<`/`>` 后是空格 / 数字)**一律不碰**;
  ③ **行末连字符断词**:`-` **紧跟**换行(中间无空格)→ 去连字去换行(把断开的词接回);
     **仅行末断词**——复合词 `trans-port`(短横后接字母)、数值范围 `5.2-5.3`(短横后接数字)一律不碰;
  ② **连续空白折叠**:连续空白(含换行 / 制表 / 各类 Unicode 空白)折成**单个 ASCII 空格**;
  ⑤ 首尾 **trim**。

**顺序要害**:
  · ① 最先 —— 先把全角 / 连字规整(全角短横 `－`→`-` 后才可能被 ③ 认出行末断词);
  · ③ ⑥ 必须在 ② **之前** —— ② 会把换行 / 标签处折成空格;⑥ 把标签换成空格后交 ② 折叠。
  · ④⑦ 与 ②③⑥ 无先后依赖(排版字符变换),置于 ① 之后即可。
  · ⚠ ⑥ **只抹标签本身、不抹标签内容**(`H<sub>2</sub>O`→`H 2 O`;`�`=U+FFFD 是真丢字符、**不在此列、抹不掉**,仍走「烂码源降级不杀卡」)。

负例钉死靠**不做**的事:不小写化、不删尾零、不动任何字母 / 数字 / 非行末短横 / 非 emphasis 字面标记。

**两个有意为之的边界(承第一刀外部审核 F4;本轮只挑明、不加保护逻辑)**:
  · **有意不折大小写**:合法首字母大写的 quote(句首 `The …`)与原文小写处对不上会误判 fail —— 由 reanchor 兜
    (重抄照抄原文大小写即过);折大小写则会放松「改了大小写」这类漂移的辨识,故不折。
  · **NFKC 有意塌缩上标 / 下标数字**:如 `10⁵`(U+2075)→`105`、`x₂`→`x2`、单位 `d⁻¹`→`d-1` —— 是「数值不可弄错」
    上的一个窄口子(上标 `10⁵` 与普通 `105` 归一化后同形)。**⚠ 07-09 更正**:07-08 曾注「极罕见」——**被 F2 证伪**:真实
    烂码语料里模型常把单位忠实写成上标(`L d⁻¹`)、而 DOCUMENT_PROCESSING 抽取把原文烂成 `L d�1`(U+FFFD),NFKC 塌 `d⁻¹`→`d-1` ≠ `d�1`
    → 忠实 quote 反判不匹配(Spagni2012 即此)。**这正是 by_field 对『烂码源』降级不杀卡(_source_corrupted)的主动因之一**;
    非模型臆造、归 DOCUMENT_PROCESSING 抽取质量债(债务表已记)。要不要加保护(塌缩前探测上下标)待按频次定夺。
"""
from __future__ import annotations

import re
import unicodedata

# ④ 弯引号 / 撇号 → 直引号(NFKC 不含此映射,须显式;含常见 OCR 的 prime `′ ″`)。
_CURLY = str.maketrans({
    "‘": "'", "’": "'", "‛": "'", "′": "'",           # ‘ ’ ‛ ′
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',  # “ ” „ ‟ ″
})
# ⑦ Markdown emphasis:只去成对包裹符。星号须以同长度闭合;单下划线不与字母数字/下划线相邻,
# 避免把数学、通配、列表、snake_case、dunder 或未闭合标记当排版噪音。
_MD_STAR_EMPH = re.compile(
    r"(?<![\w*])(\*{1,3})(?=\S)(.+?)(?<=\S)\1(?![\w*])")
_MD_UNDERSCORE_EMPH = re.compile(
    r"(?<![\w_])_(?!_)(?=\S)(.+?)(?<=\S)(?<!_)_(?![\w_])")
_SPACE_BEFORE_CLOSING_PUNCT = re.compile(r"\s+([,.;:%)\]\}])")
_SPACE_AFTER_OPEN_PUNCT = re.compile(r"([\(\[\{])\s+")
_SPACE_AFTER_PAREN_COMPARE = re.compile(r"(?<=[\(\[])([<>])\s+(?=\d)")
# ⑥ HTML 标签(DOCUMENT_PROCESSING 表格 / PDF 抽取残留,如 `<br>`)→ 空格。**只匹配真标签**:`<` 或 `</` 后**紧跟字母**、再接标签名 /
#    属性(非 `<>` 字符)到 `>`。故 `< 100`(空格)、`<10`(数字)、`x>y`(`<` 后非字母)等数学 / 比较符**不匹配、不碰**。
_HTML_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9]*[^<>]*>")
# ③ 行末断词:连字符**紧跟**(可含回车)换行 —— 连字符与换行之间无空格才算行末断词。
#    只匹配 ASCII 连字符 `-`(全角 `－` 经 ① NFKC 已归为 `-`);短横后是字母 / 数字的一律不匹配(不碰复合词 / 数值范围)。
_LINE_HYPHEN = re.compile(r"-\r?\n")
# ② 连续空白折叠(\s 在 str 正则下含各类 Unicode 空白)。
_WS = re.compile(r"\s+")
# FINAL-EVIDENCE_EXTRACTION-005/FINAL2-EVIDENCE_EXTRACTION-016:只为科学单位乘积恢复 `·` 相邻 PDF 空格；
# 至少一个单位项必须带指数。指数允许 PDF/Markdown 的显式逐字 `^`，
# 避免把普通正文 / 关系表达式 `A · B` 扩成可忽略空格。
_UNIT_EXPONENT_TEXT = r"(?:\^)?[−⁻-](?:[0-9⁰¹²³⁴⁵⁶⁷⁸⁹]+)"
_UNIT_ATOM_TEXT = rf"[A-Za-zμµ]+(?:{_UNIT_EXPONENT_TEXT})?"
_SCIENTIFIC_UNIT_PRODUCT = re.compile(
    rf"(?<![A-Za-zμµ]){_UNIT_ATOM_TEXT}(?:\s*·\s*{_UNIT_ATOM_TEXT})+(?![A-Za-zμµ])"
)
_SCIENTIFIC_UNIT_EXPONENT = re.compile(_UNIT_EXPONENT_TEXT)
# FINAL2-EVIDENCE_EXTRACTION-013:只识别数值型百分比 / 千分比 token；是否可删空格还须由下方
# `_cjk_percentage_pdf_whitespace_offsets` 确认 token 左 / 右有 CJK 上下文。不含任何论文、指标或数值白名单。
_PERCENTAGE_EXPRESSION = re.compile(
    r"(?:[<>≥≤~约]\s*)?\d+(?:\.\d+)?"
    r"(?:\s*(?:±|~|～|–|—|→|-)\s*\d+(?:\.\d+)?)?"
    r"\s*(?:%|％|‰)"
)


def _is_cjk_character(char: str) -> bool:
    """只识别 CJK 汉字；不得借 PDF 空格恢复跨英文词界、数字或标点。"""
    if not char:
        return False
    codepoint = ord(char)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x20000 <= codepoint <= 0x2FA1F
    )


def _is_non_cjk_unicode_letter_or_number(char: str) -> bool:
    """识别中文正文中可自然嵌入的 Unicode 字母 / 数字 token 边界。

    只用 Unicode 字符类别，不枚举人名、变量、论文或“合法字符”白名单；
    字符本身从不被改写。
    """
    return bool(char) and not _is_cjk_character(char) and char.isalnum()


def _is_cjk_unicode_token_boundary(left: str, right: str) -> bool:
    """是否为 CJK 与 Unicode 字母 / 数字 token 的直接混排边界。"""
    return (
        _is_cjk_character(left)
        and _is_non_cjk_unicode_letter_or_number(right)
    ) or (
        _is_non_cjk_unicode_letter_or_number(left)
        and _is_cjk_character(right)
    )


def _cjk_percentage_pdf_whitespace_offsets(text: str) -> set[int]:
    """返回中文上下文中百分比表达式周边 / 内部的可删 PDF 空格。

    只有 token 左侧或右侧非空字符为 CJK（或 token 自带中文限定词）时才放行；
    英文词界、普通数字和非百分比符号一律不动。
    """
    removable: set[int] = set()
    for match in _PERCENTAGE_EXPRESSION.finditer(text):
        left_space_start = match.start()
        while left_space_start > 0 and text[left_space_start - 1].isspace():
            left_space_start -= 1
        left = text[left_space_start - 1] if left_space_start > 0 else ""

        right_space_end = match.end()
        while right_space_end < len(text) and text[right_space_end].isspace():
            right_space_end += 1
        right = text[right_space_end] if right_space_end < len(text) else ""

        has_cjk_context = (
            _is_cjk_character(left)
            or _is_cjk_character(right)
            or any(_is_cjk_character(char) for char in match.group(0))
        )
        if not has_cjk_context:
            continue
        if _is_cjk_character(left):
            removable.update(range(left_space_start, match.start()))
        if _is_cjk_character(right):
            removable.update(range(match.end(), right_space_end))
        removable.update(
            offset
            for offset in range(match.start(), match.end())
            if text[offset].isspace()
        )
    return removable


def _normalize_cjk_pdf_whitespace(text: str) -> tuple[str, list[int]]:
    """删除 CJK 词内 / 混排 token 边界 / 中文百分比窄域 PDF 空格。"""
    percentage_whitespace = _cjk_percentage_pdf_whitespace_offsets(text)
    normalized, offsets = [], []
    index = 0
    while index < len(text):
        if not text[index].isspace():
            normalized.append(text[index])
            offsets.append(index)
            index += 1
            continue
        run_end = index + 1
        while run_end < len(text) and text[run_end].isspace():
            run_end += 1
        left = text[index - 1] if index > 0 else ""
        right = text[run_end] if run_end < len(text) else ""
        is_cjk_word_space = _is_cjk_character(left) and _is_cjk_character(right)
        is_cjk_unicode_token_space = _is_cjk_unicode_token_boundary(left, right)
        is_cjk_percentage_space = all(
            offset in percentage_whitespace for offset in range(index, run_end)
        )
        if not (
            is_cjk_word_space
            or is_cjk_unicode_token_space
            or is_cjk_percentage_space
        ):
            for offset in range(index, run_end):
                normalized.append(text[offset])
                offsets.append(offset)
        index = run_end
    return "".join(normalized), offsets


def _scientific_unit_pdf_whitespace_offsets(text: str) -> set[int]:
    """返回可删除的 `·` 相邻空格偏移；只认含指数的拉丁 / μ 单位乘积。"""
    removable: set[int] = set()
    for match in _SCIENTIFIC_UNIT_PRODUCT.finditer(text):
        if _SCIENTIFIC_UNIT_EXPONENT.search(match.group(0)) is None:
            continue
        index = match.start()
        while index < match.end():
            if not text[index].isspace():
                index += 1
                continue
            run_end = index + 1
            while run_end < match.end() and text[run_end].isspace():
                run_end += 1
            left = text[index - 1] if index > match.start() else ""
            right = text[run_end] if run_end < match.end() else ""
            if left == "·" or right == "·":
                removable.update(range(index, run_end))
            index = run_end
    return removable


def _normalize_cjk_and_scientific_unit_pdf_whitespace(text: str) -> tuple[str, list[int]]:
    """删除 CJK 词内 / 混排 token 边界 / 百分比 / 科学单位窄域 PDF 空格。"""
    unit_whitespace = _scientific_unit_pdf_whitespace_offsets(text)
    percentage_whitespace = _cjk_percentage_pdf_whitespace_offsets(text)
    normalized, offsets = [], []
    index = 0
    while index < len(text):
        if not text[index].isspace():
            normalized.append(text[index])
            offsets.append(index)
            index += 1
            continue
        run_end = index + 1
        while run_end < len(text) and text[run_end].isspace():
            run_end += 1
        left = text[index - 1] if index > 0 else ""
        right = text[run_end] if run_end < len(text) else ""
        is_cjk_word_space = _is_cjk_character(left) and _is_cjk_character(right)
        is_cjk_unicode_token_space = _is_cjk_unicode_token_boundary(left, right)
        is_unit_dot_space = all(offset in unit_whitespace for offset in range(index, run_end))
        is_cjk_percentage_space = all(
            offset in percentage_whitespace for offset in range(index, run_end)
        )
        if not (
            is_cjk_word_space
            or is_cjk_unicode_token_space
            or is_unit_dot_space
            or is_cjk_percentage_space
        ):
            for offset in range(index, run_end):
                normalized.append(text[offset])
                offsets.append(offset)
        index = run_end
    return "".join(normalized), offsets


def recover_unique_cjk_pdf_quote(quote: str, chunk_text: str) -> str | None:
    """CJK 词内 / 混排 token 边界 / 百分比空格去除后唯一回填原文。"""
    normalized_quote, _ = _normalize_cjk_pdf_whitespace(quote)
    normalized_chunk, offsets = _normalize_cjk_pdf_whitespace(chunk_text)
    if (
        not normalized_quote
        or sum(not char.isspace() for char in normalized_quote) < 8
    ):
        return None
    match_start = normalized_chunk.find(normalized_quote)
    if match_start < 0 or normalized_chunk.find(normalized_quote, match_start + 1) >= 0:
        return None
    match_end = match_start + len(normalized_quote) - 1
    if match_end >= len(offsets):
        return None
    candidate = chunk_text[offsets[match_start]:offsets[match_end] + 1]
    candidate_normalized, _ = _normalize_cjk_pdf_whitespace(candidate)
    if candidate_normalized != normalized_quote or candidate not in chunk_text:
        return None
    return candidate


def recover_unique_scientific_unit_pdf_quote(quote: str, chunk_text: str) -> str | None:
    """CJK/混排 token/科学单位空格去除后唯一命中时，回填真实原文切片。"""
    normalizer = _normalize_cjk_and_scientific_unit_pdf_whitespace
    normalized_quote, _ = normalizer(quote)
    normalized_chunk, offsets = normalizer(chunk_text)
    if (
        not normalized_quote
        or sum(not char.isspace() for char in normalized_quote) < 8
    ):
        return None
    match_start = normalized_chunk.find(normalized_quote)
    if match_start < 0 or normalized_chunk.find(normalized_quote, match_start + 1) >= 0:
        return None
    match_end = match_start + len(normalized_quote) - 1
    if match_end >= len(offsets):
        return None
    candidate = chunk_text[offsets[match_start]:offsets[match_end] + 1]
    candidate_normalized, _ = normalizer(candidate)
    if candidate_normalized != normalized_quote or candidate not in chunk_text:
        return None
    return candidate


def normalize(s: str) -> str:
    """对称归一化(白名单六项;见模块 docstring)。非字符串按空串处理(防御)。"""
    if not isinstance(s, str):
        return ""
    s = unicodedata.normalize("NFKC", s)   # ① NFKC(最先)
    s = s.translate(_CURLY)                # ④ 弯引号直化
    s = _MD_STAR_EMPH.sub(r"\2", s)       # ⑦ Markdown * / ** / *** 成对 emphasis
    s = _MD_UNDERSCORE_EMPH.sub(r"\1", s) # ⑦ Markdown _ 成对 emphasis
    s = _SPACE_BEFORE_CLOSING_PUNCT.sub(r"\1", s)  # ⑦ 强调符旁闭合标点空格
    s = _SPACE_AFTER_OPEN_PUNCT.sub(r"\1", s)      # ⑦ 左括号后空格
    s = _SPACE_AFTER_PAREN_COMPARE.sub(r"\1", s)   # ⑦ 仅括号内 > 3 → >3
    s = _LINE_HYPHEN.sub("", s)            # ③ 行末断词(必在 ② 之前)
    s = _HTML_TAG.sub(" ", s)              # ⑥ HTML 标签 → 空格(必在 ② 之前;只抹真标签、不碰数学 <>)
    return _WS.sub(" ", s).strip()         # ② 连续空白 → 单个 ASCII 空格 + ⑤ trim


def quote_in_text(quote: str, chunk_text: str) -> bool:
    """substring 验真:`quote` 归一化后是否为 `chunk_text` 归一化文本的严格子串。
    空 quote(或归一化后为空)一律 False(空串是任何串的子串,须显式挡掉,避免空 quote 蒙混)。"""
    nq = normalize(quote)
    return bool(nq) and nq in normalize(chunk_text)
