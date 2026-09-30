"""Memorive EVIDENCE-REVIEW/EVIDENCE_REVIEW · EVIDENCE_REVIEW 校核错误类型(Verification)。

分两层(承 Verification 第 1 步草案④):
- **系统性错误**(卡不可读 / YAML 解析失败 / chunk 库不可读或与卡不符)→ raise 这些受控异常,
  调用方应捕获(而非任其冒泡成 traceback);机械层在这类错误下**无法继续校验**。
- **锚点级问题**(断链 / 形态不明)→ **不 raise**,记入 `MechanicalResult.problems`。
"""
from __future__ import annotations

from knowledge_admission import DecisionError


class EvidenceReviewError(Exception):
    """EVIDENCE_REVIEW 校核所有受控错误的基类。"""


class CardUnreadable(EvidenceReviewError):
    """卡文件不可读(不存在 / IO 错)。系统性 → raise。"""


class CardParseError(EvidenceReviewError):
    """卡 frontmatter 缺失 / YAML 解析失败 / 非 mapping。系统性 → raise。"""


class ChunkStoreError(EvidenceReviewError):
    """chunk 库定位 / 读取 / 解析失败,或库 paper_id 与卡不符。系统性 → raise。"""


class BatchContractExhausted(DecisionError):
    """整卡某阶段的唯一技术输出重试已耗尽；外层不得再重跑整卡。"""


class OwnershipFailClosed(EvidenceReviewError):
    """归属安全阀挡下(Integration第2步④ + 复审③):卡 / 数据 **非明确 self**(entrusted / 缺失 / 未知)+ 本地 EVIDENCE_REVIEW 校核槽未就绪 →
    fail-closed,**绝不把他人托付 / 归属不明数据送 Haiku(搬运类)或 GPT(判断类)境外云**(手动订阅档亦不可绕过)。
    系统性 / 策略性 → raise(非 DecisionError:不重试、不 admit)。
    ⚠ **异类同名警告(复审[5]/[7]·记债)**:本类是 `EvidenceReviewError` 子类,与 `research_analysis.OwnershipFailClosed`(`AnalysisError`
    子类)**同名但不同类、不同继承树**——理念平行(都是归属 fail-closed)、**但非同一异常**。跨 RESEARCH_ANALYSIS+EVIDENCE_REVIEW 的编排器若用单个
    `except OwnershipFailClosed` 只会 catch 到 import 的那一个、漏另一个;须**两个都 catch**。统一为一个共享归属异常 +
    共享归属阀(触 RESEARCH_ANALYSIS)属**债**、另开(承复审 altitude:三处近乎复制的「非 self→fail-closed」阀值得抽共享机制)。"""
