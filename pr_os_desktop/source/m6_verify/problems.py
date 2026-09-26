"""M6 问题清单结构(T6 第 1 步立;后续步骤 R1–R9 搬运类 / GPT 异源复用同一形状)。

Problem 通用形状(承第 1 步草案④):source / severity / kind / location / field / chunk_id / detail。
机械层产出 source='mechanical'、severity='error';将来搬运类 R1–R9 用 source='transcription'、
判断类 GPT 异源用 source='gpt'(本步只落 mechanical)。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Problem:
    source: str                    # 'mechanical'(本步)/ 将来 'transcription'(R1–R9)/ 'gpt'(异源)
    severity: str                  # 'error' /(将来可扩 'warn' 等)
    kind: str                      # 'broken_link' / 'malformed_anchor'(本步两类)
    location: str                  # 精确路径:by_field.key_results.chunk_ids[2] / key_data[5].chunk_id
    detail: str                    # 人读说明
    field: str | None = None       # key_results / research_object / 'key_data' / None
    chunk_id: str | None = None    # 涉事 chunk_id(空 / 非串时 None)
    quote: str | None = None       # 搬运类 R 违规的原文短摘(**只存、不做 substring 校验**,v3 之八另办);机械层恒 None


@dataclass
class MechanicalResult:
    ok: bool                       # = problems 为空
    problems: list                 # list[Problem]
    checked: int                   # 尝试 resolve 的 chunk_id 个数(不含 malformed)
    resolved: int                  # resolve 成功数
    broken: int                    # broken_link 数
    malformed: int                 # malformed_anchor 数
