# -*- coding: utf-8 -*-
"""normalize() / quote_in_text() 正负例单测(source_anchor v3 第一刀;承之八)。

**负例钉死**(改了实词/数字 → 归一化后仍判不匹配,这是防臆造的命门):
  except≠after、5.2≠5.3、5.2≠5.20、increased≠decreased、mg≠μg、漏 not、漏否定。
**正例**(仅排版噪音差异 → 归一化后判匹配):
  行末连字符断词、连字 ﬁ、多空格 / 换行折叠、弯引号 vs 直引号、全角 vs 半角、微符 µ/μ 统一。
**真实 chunk 校准**:嵌入 Integration 真实文献(Spagni2012 / Zhou2026)逐字原句 —— 正例(逐字子串过)、
  负例(该句改一词 / 改一数即不过)。真实全量 chunk 校准另见沙盒 smoke。

直接跑:`python memorive/documents/card_distillation/tests/test_normalize.py` → 打印正负例逐条 PASS/FAIL(正全绿 / 负全红即通过)。
pytest 亦可发现 test_* 函数。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str((Path(__file__).resolve().parents[3] / "source")))   # 允许直接 `python ...test_normalize.py`
from memorive.documents.card_distillation.normalize import normalize, quote_in_text   # noqa: E402


# ── 负例:改了实词 / 数字,归一化后**仍不匹配**(quote_in_text 必 False)──────────────
# 每条 = (说明, chunk 原文, 篡改后的 quote)
NEGATIVES = [
    ("except≠after",       "stable except under anaerobic conditions",  "stable after under anaerobic conditions"),
    ("5.2≠5.3",            "an OLR of 5.2 g COD/L/d was applied",        "an OLR of 5.3 g COD/L/d was applied"),
    ("5.2≠5.20(不删尾零)", "the value was 5.2 mg/L at steady state",     "the value was 5.20 mg/L at steady state"),
    ("increased≠decreased","the permeate flux increased over time",     "the permeate flux decreased over time"),
    ("mg≠μg",              "dosed at 35 mg/L NaCl",                      "dosed at 35 μg/L NaCl"),
    ("漏 not(破否定连续性)","methane production was not inhibited",       "methane production was inhibited"),
    ("漏否定(中文)",       "甲烷产量未受到明显抑制",                      "甲烷产量受到明显抑制"),
    ("大小写不折叠",        "Removal efficiency reached 95%",             "removal efficiency reached 95%"),
]

# ── 正例:仅排版噪音差异,归一化后**匹配**(quote_in_text 必 True)──────────────────
# 每条 = (说明, chunk 原文[含噪音], quote[干净或另一种噪音])
POSITIVES = [
    ("多空格折叠",          "removal of 95.2%   was    observed",         "removal of 95.2% was observed"),
    ("换行折叠",            "removal of 95.2%\nwas observed",             "removal of 95.2% was observed"),
    ("行末连字符断词",      "ion trans-\nport across the membrane",       "ion transport across the membrane"),
    ("连字 ﬁ(NFKC)",       "membrane ﬁltration was efficient",       "membrane filtration was efficient"),
    ("弯引号→直引号",       "the authors’ model of “fouling”", "the authors' model of \"fouling\""),
    ("全角→半角(NFKC)",    "removal was ９５％ at day 41",     "removal was 95% at day 41"),
    ("微符 µ/μ 统一(NFKC)", "dosed at 35 µg/L trace",                 "dosed at 35 μg/L trace"),
    ("首尾 trim",           "  colour removal reached 91%  ",             "colour removal reached 91%"),
    ("HTML <br> 抹为空格",  "considered highly<br>efficient for",         "considered highly efficient for"),
    ("HTML 标签抹除(闭合)", "the H<sub>2</sub>O and <i>in situ</i> test", "the H 2 O and in situ test"),
    ("Markdown 下划线强调", "the abundance of _Proteobacteria_ reached 45.0%", "the abundance of Proteobacteria reached 45.0%"),
    ("Markdown 星号强调",   "the **anaerobic** phase remained stable",       "the anaerobic phase remained stable"),
    ("Markdown 比较符强调", "_Treponema_ ( _>_ 0.25%)",                       "Treponema (>0.25%)"),
    ("Markdown 术语后标点", "_Desulfovibrio_ , _Syntrophobacter_ , _GP19_",   "Desulfovibrio, Syntrophobacter, GP19"),
]

# ── 真实 chunk 校准(Integration 文献逐字原句;正例逐字过、负例改一词/数即不过)────────────────
# Spagni2012#c0045 逐字原句(Integration 暴露的脱色率场景):
_SPAGNI = "During the first two months, applying a low azo dye load, the colour removal was comprised between 91% and 95%."
# Spagni2012#c0008 逐字(含复合词短横 nitrogen-nitrogen,须**不**被行末断词误接):
_SPAGNI_HYPHEN = "they are characterised by one or more nitrogen-nitrogen double bond"
# Zhou2026#c0012 逐字(含数值范围短横 41-268,须**不**被误接;steady-state 复合词短横亦然):
_ZHOU = "During the steady-state stage after acclimation (day 41-268)"

REAL_POSITIVES = [
    ("Spagni 逐字子串",              _SPAGNI, "the colour removal was comprised between 91% and 95%"),
    ("Spagni 逐字+换行噪音",         _SPAGNI, "the colour removal was comprised\nbetween 91% and 95%"),
    ("Spagni 复合词短横不误接",       _SPAGNI_HYPHEN, "one or more nitrogen-nitrogen double bond"),
    ("Zhou 数值范围短横 41-268 保留",  _ZHOU, "steady-state stage after acclimation (day 41-268)"),
]
REAL_NEGATIVES = [
    ("Spagni 95%→55%(数值臆造)",     _SPAGNI, "the colour removal was comprised between 91% and 55%"),
    ("Spagni between→after(改写)",    _SPAGNI, "the colour removal was comprised after 91% and 95%"),
    ("Zhou 41-268→41-269(改一数)",    _ZHOU, "steady-state stage after acclimation (day 41-269)"),
]


# ── pytest 可发现的断言 ──────────────────────────────────────────────
def test_negatives_stay_mismatched():
    for desc, chunk, quote in NEGATIVES + REAL_NEGATIVES:
        assert not quote_in_text(quote, chunk), f"负例应不匹配却匹配了:{desc}"


def test_positives_match_through_noise():
    for desc, chunk, quote in POSITIVES + REAL_POSITIVES:
        assert quote_in_text(quote, chunk), f"正例应匹配却没匹配:{desc}"


def test_empty_quote_guard():
    # 空 quote(或归一化后为空)绝不蒙混:空串是任何串的子串,须显式挡掉
    assert not quote_in_text("", "any chunk text")
    assert not quote_in_text("   \n  ", "any chunk text")


def test_normalize_never_touches_words_digits_case():
    # normalize 绝不碰字母 / 数字 / 大小写 / 非行末短横:改了实词的两串归一化后仍不同
    assert normalize("5.2-5.3 range") == "5.2-5.3 range"          # 数值范围短横保留
    assert normalize("Except") != normalize("except")            # 不折大小写
    assert normalize("decreased") != normalize("increased")      # 不碰实词


def test_html_stripped_but_math_lt_gt_preserved():
    # ⑥ HTML 标签抹为空格
    assert normalize("highly<br>efficient") == "highly efficient"
    assert normalize("a<br/>b<br />c") == "a b c"
    # ⚠ 数学 / 比较符 `<` `>` 绝不当标签抹掉(`<` 后是空格 / 数字、或 `>` 单独出现)
    assert normalize("COD < 100 mg/L") == "COD < 100 mg/L"       # `< ` 后空格 → 非标签
    assert normalize("ratio 5<10 holds") == "ratio 5<10 holds"   # `<` 后数字 → 非标签
    assert normalize("flux > 40 kPa") == "flux > 40 kPa"         # 单独 `>` → 非标签
    assert not quote_in_text("flux > 40 kPa", "the flux < 40 kPa here")   # < vs > 仍判不匹配


def test_markdown_emphasis_stripped_without_touching_literal_markers():
    assert normalize("_Proteobacteria_") == "Proteobacteria"
    assert normalize("**anaerobic**") == "anaerobic"
    assert normalize("snake_case") == "snake_case"
    assert normalize("__init__") == "__init__"
    assert normalize("2*3") == "2*3"
    assert normalize("x**2") == "x**2"
    assert normalize("* list item") == "* list item"


# ── 直接运行:打印逐条 PASS/FAIL(正全绿 / 负全红即通过)──────────────────
def _run() -> int:
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
    bad = 0
    print("===== 负例(必 False:改了实词/数字 → 判不匹配)=====")
    for desc, chunk, quote in NEGATIVES + REAL_NEGATIVES:
        ok = (quote_in_text(quote, chunk) is False)
        bad += 0 if ok else 1
        print(f"  {'✅红(不匹配)' if ok else '❌漏(竟匹配)'}  {desc}")
    print("\n===== 正例(必 True:仅排版噪音 → 判匹配)=====")
    for desc, chunk, quote in POSITIVES + REAL_POSITIVES:
        ok = (quote_in_text(quote, chunk) is True)
        bad += 0 if ok else 1
        print(f"  {'✅绿(匹配)' if ok else '❌错(竟不匹配)'}  {desc}")
    print("\n===== 边界 =====")
    for desc, res in (("空 quote 挡掉", not quote_in_text("", "x")),
                      ("纯空白 quote 挡掉", not quote_in_text("  \n ", "x")),
                      ("5.2-5.3 短横保留", normalize("5.2-5.3 range") == "5.2-5.3 range")):
        bad += 0 if res else 1
        print(f"  {'✅' if res else '❌'}  {desc}")
    print(f"\n{'✅ 全部通过(正全绿、负全红)' if bad == 0 else f'❌ {bad} 条不符'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(_run())
