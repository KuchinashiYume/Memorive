"""KNOWLEDGE_ADMISSION 三态与合法转移表(逐字承 contracts/Core_最小可用闭环/10_模块契约/Memorive_KNOWLEDGE_ADMISSION_KNOWLEDGE_ADMISSION_模块契约与骨架设计.md §一;严格对齐 card_schema review_status)。

只做 review_status 这一根轴的三态。archive 属 ARTIFACT_REGISTRY Usage 另一轴、不在本枚举(守卫当非法挡)。
"""
from __future__ import annotations

# 三态 = review_status 轴的**全部**合法值(与 card_schema.md 第二节严格一致,不多不少;没有第四个)。
PENDING = "pending"
ACTIVE = "active"
QUARANTINED = "quarantined"
STATES = frozenset({PENDING, ACTIVE, QUARANTINED})

# 合法转移表(承契约 §一②③):from_state → 允许的 to_state 集合。
#  · pending→active / pending→quarantined : 主转移(经判定源触发)
#  · active→quarantined                   : 事后隔离,机制归 KNOWLEDGE_ADMISSION「合法、不报非法」;触发时机归 ARTIFACT_REGISTRY
#  · quarantined→*                        : Core 终态(再审属未来,不在此)
_LEGAL: dict[str, frozenset] = {
    PENDING: frozenset({ACTIVE, QUARANTINED}),
    ACTIVE: frozenset({QUARANTINED}),
    QUARANTINED: frozenset(),
}

# Core **启用执行**的转移(主转移之二)。active→quarantined 虽 is_legal 但不在此:
# transition() 对它抛 TransitionNotEnabledError(机制合法、Core 不发起、时机归 ARTIFACT_REGISTRY)。
_Core_ENABLED: frozenset = frozenset({(PENDING, ACTIVE), (PENDING, QUARANTINED)})


def is_legal(from_state: str, to_state: str) -> bool:
    """(from,to) 是否 review_status 轴上一条合法转移(认得目标态、边在表内)。
    to 不在三态枚举(如 archive)或 from 未知 → False;同态自转(to∉_LEGAL[from])→ False。"""
    if from_state not in STATES or to_state not in STATES:
        return False
    return to_state in _LEGAL.get(from_state, frozenset())


def is_core_enabled(from_state: str, to_state: str) -> bool:
    """(from,to) 是否 Core 启用执行。仅两条主转移;active→quarantined 合法但不启用。"""
    return (from_state, to_state) in _Core_ENABLED
