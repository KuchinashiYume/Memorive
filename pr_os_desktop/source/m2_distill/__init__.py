"""PR-OS P01/T04/M02 · M2 蒸馏。

把一篇 Clean MD 单篇、忠于原文地提炼成核心卡片(双层知识库上层)。

对外:
    from m2_distill import distill
    r = distill(cleanmd_path_or_folder, target="sandbox")   # → DistillResult(status/card_path/…)

核心 7 字段抽取骨架(T4 第 1 步):经 M9 调 deepseek-v4-pro(不直连 SDK)、Prompt 内建 R1–R9、
每核心内容字段挂真实 chunk_id(by_field 贯穿锚点)、归属红线挡 entrusted/缺失、产物 review_status=pending。
契约见 项目文档/Phase01_最小可用闭环/10_模块契约/PR-OS_P01_T04_M02_模块契约与骨架设计.md;
字段见 项目文档/card_schema.md(v2)。
"""
from .distill import DistillResult, distill

__all__ = ["distill", "DistillResult"]
