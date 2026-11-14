"""Memorive EVIDENCE-EXTRACTION/EVIDENCE_EXTRACTION · EVIDENCE_EXTRACTION 蒸馏。

把一篇 Clean MD 单篇、忠于原文地提炼成核心卡片(双层知识库上层)。

对外:
    from evidence_extraction import distill
    r = distill(cleanmd_path_or_folder, target="sandbox")   # → DistillResult(status/card_path/…)

核心 7 字段抽取骨架(Configuration 第 1 步):经 MODEL_GATEWAY 调 deepseek-v4-pro(不直连 SDK)、Prompt 内建 R1–R9、
每核心内容字段挂真实 chunk_id(by_field 贯穿锚点)、归属红线挡 entrusted/缺失、产物 review_status=pending。
"""
from .distill import DistillResult, distill

__all__ = ["distill", "DistillResult"]
