"""M8 状态机核心:守卫 + 转移执行 + 判定接口(占位/手动)。

Phase 1 边界:只做「给定卡 + 转移请求 → 校验 → 合法且启用则改 review_status、否则挡下报错」。
判定源占位/手动(真实 R1–R9 判定归 M6/T6);**本步已接入 M8_log 留痕**:成功 write_status() 后 append 到
M8 自有 M8_log_YYYY.jsonl(见 m8_log);自动分配/失败重跑/连错升级仍归第 3 步;不碰 M13;不直连 SDK。
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path

from . import m8_log
from .card_io import read_data_id, read_status, write_status
from .errors import (IllegalTransitionError, M8Error, MissingBasisError,
                     TransitionNotEnabledError, TriggerNotAcceptedError)
from .states import ACTIVE, PENDING, QUARANTINED, is_legal, is_phase1_enabled


class Trigger(str, Enum):
    """review_status 转移触发源。Phase 1 执行路径接受 **MANUAL / PLACEHOLDER / AUTO_RULE**(第 3 步 AUTO_RULE 转正)。
    AUTO_RULE 语义 = 「M8 自动流转器触发转移」、**不表示"已完成真实判定"**(判定仍由 decision_source 给)。
    M6_VERDICT(T6 校核)**T6 第 2 步已激活**、执行路径接受(加入 _ACCEPTED_TRIGGERS;M6 校核结果驱动放行/隔离)。
    注:M7 解除降权**不在此枚举**——它改 DerivedPenalty(治理轴/权重)、不改准入态,是 M8_log 治理轴
    触发源(第 2 步),绝不接成一次放行(承 v6.4 之一 / 补丁③)。"""
    MANUAL = "我手动"
    PLACEHOLDER = "占位"          # 仅离线自测(配 placeholder 判定源);非正式默认
    AUTO_RULE = "自动规则"        # 第 3 步自动分配触发(已转正;= M8 自动流转器触发、非"已完成判定")
    M6_VERDICT = "M6校核结果"     # T6 第 2 步激活:M6 校核结果驱动放行/隔离(承决策 B)
    GRADED_ADMIT = "分级信任准入"  # v6.4 之四·上半:M6 判 FAIL 不再整卡 quarantine → 卡带 per-field 信用档进 active
                                   # (M6 flag 只落信用档、不驱动 quarantine;quarantine 只留灾难/手动。承分级信任设计)


class Verdict(Enum):
    """判定接口的结论(占位 / 手动 / 将来 M6 都产这个;M8 只据此转移、不关心谁算的)。"""
    PASS = "pass"
    FAIL = "fail"


_ACCEPTED_TRIGGERS = frozenset({Trigger.MANUAL, Trigger.PLACEHOLDER, Trigger.AUTO_RULE,
                               Trigger.M6_VERDICT, Trigger.GRADED_ADMIT})


def transition(card_path: Path, to_status: str, *, trigger: Trigger, reason: str) -> str:
    """受守卫的状态转移:读现态 → 校验(合法性 + Phase 1 启用)→ 合法且启用则改 review_status、否则挡下报错。
    · 缺依据(reason 空 / trigger 非 Trigger)                → MissingBasisError
    · 触发源非可接受集合(Trigger 之外 / 未来预留未启用)    → TriggerNotAcceptedError
    · 根本非法(未经 pending 直 active / active→pending / 目标态非三态如 archive / 同态自转)
                                                            → IllegalTransitionError
    · 合法但 Phase 1 不启用(active→quarantined)            → TransitionNotEnabledError
    任一挡下**状态不被改动**。返回新状态。
    (M8_log 落痕第 2 步:此处已收齐 trigger+reason 作依据、留 hook。)"""
    # reason 守卫在最前(读卡 / 读 data_id / 写状态之前):reason 非 str(如 123)→ MissingBasisError、不碰卡
    # (堵 `not reason or reason.strip()` 对 reason=123 走 .strip() 抛非受控 AttributeError)。
    if not isinstance(reason, str) or not reason.strip():
        raise MissingBasisError(f"转移缺依据(reason 须为非空字符串): {card_path}")
    if not isinstance(trigger, Trigger):
        raise MissingBasisError(f"转移缺触发源(trigger 须为 Trigger 成员): {card_path}")
    if trigger not in _ACCEPTED_TRIGGERS:
        raise TriggerNotAcceptedError(
            f"触发源 {trigger.value} 非可接受集合"
            f"(我手动 / 占位 / 自动流转 / M6校核结果): {card_path}")
    current = read_status(card_path)                       # 权威读现态(缺/坏 → CardStateError)
    if not is_legal(current, to_status):                  # archive / active→pending / 同态自转 → 非法
        raise IllegalTransitionError(f"非法转移 {current}→{to_status}(守卫挡下、状态未改): {card_path}")
    if not is_phase1_enabled(current, to_status):         # 合法但 Phase 1 不启用(active→quarantined)
        raise TransitionNotEnabledError(
            f"转移 {current}→{to_status} 机制合法、Phase 1 不启用、触发时机归 M13: {card_path}")
    data_id = read_data_id(card_path)                     # 数据 ID(严格:缺 paper_id → CardStateError、状态不改)
    m8_log.preflight()                                    # pre-flight:M8_log 目录不可写 → M8LogAppendError、状态不改
    write_status(card_path, to_status)                    # —— 状态改动分界线:上面任一挡下,状态未变 ——
    # 留痕(第 2 步):M8 自有账本、借 log_reason 契约不借 M11 落盘。act-then-log:write 已成功;
    # 若下面 append 失败 → 抛 M8LogAppendError,暴露「状态已变、留痕失败」的审计缺口,绝不静默;
    # 本步不做事务回滚(状态已改、留痕缺,须人看;pre-flight 已把常见的目录问题挡在改动之前)。
    m8_log.append_transition(data_id=data_id, from_status=current, to_status=to_status,
                             trigger=trigger.value, reason=reason)
    return to_status


# ── 判定接口(占位/手动)—— 真实 R1–R9 判定归 M6(T6);此处只「拿到通过/未通过就据此转移」──

def apply_verdict(card_path: Path, verdict: Verdict, *, trigger: Trigger, reason: str) -> str:
    """把一个「通过/未通过」判定作用到一张 **pending** 卡:PASS→active、FAIL→quarantined。
    (M8 只据判定结论转移、不关心判定是谁算的;非 pending 卡 → IllegalTransitionError。)"""
    # 类型守卫:verdict 非 Verdict 成员(如字符串 "pass")→ 受控挡下、状态不变、不碰卡。
    # (否则 `verdict is Verdict.PASS` 对 "pass" 为假 → 误走 FAIL → 卡被 quarantined、反向放错。)
    # 用受控基类 M8Error(本步只动 card_io/machine、不新增 errors 类型;可后续加专用 InvalidVerdictError)。
    if not isinstance(verdict, Verdict):
        raise M8Error(f"判定结论须为 Verdict 成员(得到 {verdict!r}),受控挡下、状态不变、不碰卡: {card_path}")
    current = read_status(card_path)
    if current != PENDING:
        raise IllegalTransitionError(f"apply_verdict 只作用 pending 卡,当前 {current}: {card_path}")
    to_status = ACTIVE if verdict is Verdict.PASS else QUARANTINED
    return transition(card_path, to_status, trigger=trigger, reason=reason)


def manual_approve(card_path: Path, *, reason: str) -> str:
    """人工入口:我确认通过 → pending 转 active(trigger=我手动)。"""
    return apply_verdict(card_path, Verdict.PASS, trigger=Trigger.MANUAL, reason=reason)


def manual_reject(card_path: Path, *, reason: str) -> str:
    """人工入口:我确认不通过 → pending 转 quarantined(trigger=我手动)。"""
    return apply_verdict(card_path, Verdict.FAIL, trigger=Trigger.MANUAL, reason=reason)


def placeholder_pass_verdict(card_path: Path) -> Verdict:
    """⚠ **仅离线自测用**:固定返回 PASS,让 Phase 1 跑通状态流转的形状。
    **绝非正式放行通道、绝非默认判定源**——正式放行 = 手动(manual_*)或将来 M6;固定 PASS 会无条件
    放行任何卡,误当默认即破「active ≠ 已校验」。故:命名带 placeholder 自证、**不进 __init__ 公共入口**、
    配 Trigger.PLACEHOLDER(亦仅自测)。"""
    return Verdict.PASS
