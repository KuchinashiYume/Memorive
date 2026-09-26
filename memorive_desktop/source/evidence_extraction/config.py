"""Memorive EVIDENCE-EXTRACTION/EVIDENCE_EXTRACTION · EVIDENCE_EXTRACTION 蒸馏配置(单一配置源;env 可覆盖)。承 D4(默认 deepseek-v4-pro,整条不分工)+ card_schema v3。"""
from __future__ import annotations

import os
from datetime import datetime

# MODEL_GATEWAY 槽名(蒸馏引擎选型/endpoint/默认档 deepseek-v4-pro 全在 MODEL_GATEWAY models.yaml,EVIDENCE_EXTRACTION 不重定义)。
DISTILL_TASK = "distill"
CORE_JSON_RECOVERY_PROMPT_MODULE = "distill_core_json_recovery"
CORE_ANCHOR_REPAIR_PROMPT_MODULE = "distill_anchor_repair"

# 落盘卡片遵循的 Schema 版本。
# v3(source_anchor v3「第一刀」,2026-07-08):**key_data 每条挂逐字原文 quote**、蒸馏当场 substring 验真。
# v4(source_anchor v3「第二刀」,2026-07-08):**by_field 升 per-锚点对象列表** `[{chunk_id, quote, section_page}, …]`
#   (v2/v3 是 `{section_page, chunk_ids:[…]}`)——高风险内容字段(key_results/author_conclusion/boundary_conditions)
#   每锚点挂支撑内容的逐字 quote、走同一 substring 验真;综合字段(research_question/research_object/method)quote 允许 null。
#   **读端按形态分派**(更稳):by_field 值是 dict → v2/v3 旧形态读 `chunk_ids`;是 list → v4 读元素 `chunk_id`;
#   RETRIEVAL/EVIDENCE_REVIEW 现有读法已形态容忍,老 v2/v3 卡不删不改照常可读。版本号供迁移/消歧。
# v5(分级信任准入·上半,2026-07-09):**每字段 / 每 key_data 一个信用档 credibility**
#   (verified / anchor_uncertain / flagged + note;EVIDENCE_REVIEW 判 FAIL 不再整卡 quarantine → 卡带 per-field 档进 active)。
#   顶层 `field_credibility`(6 核心字段)+ 每条 key_data 加 credibility/credibility_note/danger;
#   EVIDENCE_EXTRACTION 建卡时初始 `unknown`(EVIDENCE_REVIEW 未跑),grade_and_admit 落档。**旧 v1–v4 卡缺 credibility → 读端当 unknown**(形态兼容)。
# v6(单轮异源返修,2026-07-13):新增 completion_status + omissions。EVIDENCE_EXTRACTION 初始卡为 pending_review/[]；
#   Sonnet 初审 → DeepSeek 单批返修 → Sonnet 只复核变更项后，最终卡可把无法可信恢复的单项删除，
#   以 tombstone 留下原位置/原值/report 链。旧 v1-v5 仍按既有形态读取，不强制迁移。
CARD_SCHEMA_VERSION = 6

# 输出 token 上限:蒸馏须留足,**绝不用 deepseek adapter 默认 16**(承 Initialization 留痕:Pro 推理型须留足)。env 可配。
# 默认 8192:整篇卡片(7 字段·原文语言,承 v6.3 之七不翻译)+ 推理型开销较大,4096 实测会截断致 JSON 不完整。**长文献可临时调大 MEMORIVE_EVIDENCE_EXTRACTION_MAX_TOKENS**。
CARD_CREATE_MAX_TOKENS = int(os.environ.get("MEMORIVE_EVIDENCE_EXTRACTION_CARD_CREATE_MAX_TOKENS") or 8192)
DISTILL_MAX_TOKENS = CARD_CREATE_MAX_TOKENS  # compatibility alias

# 截断升档重试上限:首次输出被截断(finish_reason=length)→ **只升档重试一次**至此值(硬顶、绝不无限升,防烧钱/死循环)。
# 重试仍截断 → fail-closed CardParseError;再不够属将来的分段蒸馏(挂接位、未实现)。env 可配 MEMORIVE_EVIDENCE_EXTRACTION_MAX_TOKENS_RETRY。
CARD_CREATE_MAX_TOKENS_RETRY = int(
    os.environ.get("MEMORIVE_EVIDENCE_EXTRACTION_CARD_CREATE_MAX_TOKENS_RETRY") or 16384
)
DISTILL_MAX_TOKENS_RETRY = CARD_CREATE_MAX_TOKENS_RETRY  # compatibility alias

# Whole-Card regeneration is a distinct request and accounting category.  The
# current atomic closure forbids this route, but the budget remains explicit so
# a future authorized regenerate never borrows the create or repair allowance.
CARD_REGENERATE_MAX_TOKENS = int(
    os.environ.get("MEMORIVE_EVIDENCE_EXTRACTION_CARD_REGENERATE_MAX_TOKENS") or 16384
)

# 核心字段假 chunk_id 的唯一一次定向返修。只提交受影响字段、假锚点和冻结候选 chunks，
# 不重新提交全文或重做整卡；MODEL_GATEWAY 自身冻结的 provider retry 规则仍照常生效。
CORE_ANCHOR_REPAIR_MAX_TOKENS = int(
    os.environ.get("MEMORIVE_EVIDENCE_EXTRACTION_ANCHOR_REPAIR_MAX_TOKENS") or 4096
)

# 输入字符上限:拼接 chunks 超此值 → 受控 ChunksTooLargeError、**不静默截断**(分段留将来)。env 可配。
# 默认 250_000:标定 = deepseek-v4-pro 上下文 1M token(承 D4·决策_D4:50)——25 万字符 ≈ ~6 万 token ≈ 上下文 **~6%**,
#   覆盖常见长综述(含参考文献,如 15 页综述 ~12 万字符)留 **~2 倍余量**;单篇蒸馏满档输入成本 **~¥0.19**。
# ⚠ 旧值 120_000 **无标定依据**——照 Configuration 测试篇 Zhou2026 6.3 万字正文取 ~2 倍圆整护栏(操作日志_2026-07-03:109)、
#   从未压测参考文献多的综述;致一篇普遍长度 15 页综述(Hoang2022、12.1 万字符)顶爆 0.8%(Integration 第 1 步实测 120,957)。
#   **判定:标定失误、非模型限制**,2026-07-08 修订 120k→250k(回 Configuration 补;DECISION_LOG DDL 候选)。
# **仍守 fail-closed**:>25 万字符照旧 ChunksTooLargeError 挡下、交 Quality 分段蒸馏(挂接位、未实现);长文献可临时再调大 MEMORIVE_EVIDENCE_EXTRACTION_INPUT_MAX_CHARS。
INPUT_MAX_CHARS = int(os.environ.get("MEMORIVE_EVIDENCE_EXTRACTION_INPUT_MAX_CHARS") or 250_000)

# ── key_data 关键数据抽取器(Configuration 步2)─────────────────────────────────────
# 独立 prompt 模块(独立版本化,不碰核心 distill/v6);**复用 DISTILL_TASK 走 MODEL_GATEWAY distill 槽**
# (同 deepseek-v4-pro、守 D4「整条不分工」、不新增模型槽)。独立 token 预算:key_data 逐条列表可长,
# 与核心 7 字段分开各留各的预算,降截断风险。env 可配。
# v5 起每条 key_data 多产一条逐字 quote(占输出 token;v4 已把 key_data 收「少而准」净影响大概率可控,
# 跑一遍若截断再上调 MEMORIVE_EVIDENCE_EXTRACTION_KEY_DATA_MAX_TOKENS,别硬改 committed 默认)。
KEY_DATA_PROMPT_MODULE = "distill_key_data"
KEY_DATA_RECOVERY_PROMPT_MODULE = "distill_key_data_recovery"
KEY_DATA_MAX_TOKENS = int(os.environ.get("MEMORIVE_EVIDENCE_EXTRACTION_KEY_DATA_MAX_TOKENS") or 8192)
KEY_DATA_MAX_TOKENS_RETRY = int(os.environ.get("MEMORIVE_EVIDENCE_EXTRACTION_KEY_DATA_MAX_TOKENS_RETRY") or 16384)

# ── key_data 原文引用 quote(source_anchor v3「第一刀」;承之八「机器逐字符验真」)──────────
# 每条 key_data 挂逐字原文 quote,蒸馏当场做 substring 验真(归一化后须为其 chunk 原文严格子串)。
# quote 字符上限用于重锚 prompt 约束与 `quote_long` 观测；真实子串即使超长也接受，不触发重抄或 fail-closed。
# 非子串仍按 `_resolve_quote` 走 targeted 重抄与既定分派。env 可配。
KEY_DATA_QUOTE_MAX_CHARS = int(os.environ.get("MEMORIVE_EVIDENCE_EXTRACTION_KEY_DATA_QUOTE_MAX_CHARS") or 300)
# quote 重抄(targeted 重 prompt)输出 token 上限:只抄支撑一条数据的一句原文,预算小即可(便宜)。env 可配。
KEY_DATA_REANCHOR_MAX_TOKENS = int(os.environ.get("MEMORIVE_EVIDENCE_EXTRACTION_KEY_DATA_REANCHOR_MAX_TOKENS") or 1024)

# ── 三附属抽取器(Configuration 步3:comparison_context / author_limitations_outlook / terms / citations)─────
# **合并一次**经 MODEL_GATEWAY 调 distill 槽(同 deepseek-v4-pro、不新增模型槽);独立 prompt 模块 distill_aux + 独立 token 预算。
# Core 只抽存不投入使用;**低风险**:整体失败/截断 → warn(detail 级)+ 落不带 aux 的卡,不拖垮核心+key_data。
# schema 按 card_schema v3 现字段(7 字段比较 / {quote,chunk_id} 局限展望 / {term,definition}·{ref,relation}),不升版。
AUX_PROMPT_MODULE = "distill_aux"
AUX_MAX_TOKENS = int(os.environ.get("MEMORIVE_EVIDENCE_EXTRACTION_AUX_MAX_TOKENS") or 8192)
AUX_MAX_TOKENS_RETRY = int(os.environ.get("MEMORIVE_EVIDENCE_EXTRACTION_AUX_MAX_TOKENS_RETRY") or 16384)

# 单次 HTTP 超时(秒):蒸馏为推理型长调用,经 payload["timeout"] 传给 MODEL_GATEWAY deepseek adapter
# (adapter 默认仍 30、向后兼容;EVIDENCE_EXTRACTION 传此值)。env 可配。
HTTP_TIMEOUT = int(os.environ.get("MEMORIVE_EVIDENCE_EXTRACTION_HTTP_TIMEOUT") or 90)


def now_minute() -> str:
    """带时区、到分钟(与 DocumentEmbed embedded_at 同风格;distilled_at 用)。
    ⚠ 锁死分钟精度、勿改带秒:带秒的完整 ISO 时间戳会被 PyYAML safe_load 解析成 datetime 对象,
       破坏所有按字符串消费 distilled_at 的下游(分钟精度因缺秒字段而 round-trip 回 str)。"""
    return datetime.now().astimezone().isoformat(timespec="minutes")
