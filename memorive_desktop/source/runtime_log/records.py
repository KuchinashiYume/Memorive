"""RUNTIME_LOG 两个对外契约的结构化写入 —— 都复用 logger.log(),不另起子系统。

契约①·记账 log_accounting(): MODEL_GATEWAY 每次经网关调模型,记一条 record_type='accounting' 的 8 字段记录,
  按 task_type 可区分(下游 MODEL_GATEWAY 自行汇总;RUNTIME_LOG 只存不算、不做红线拦截)。
契约②·原因 log_reason(): 校核/抽检/回滚/生命周期变化写「什么事+为什么」,record_type='reason';
  为 v6.3 分析执行模式切换的原因留 event_category='exec_mode_switch'。

RUNTIME_LOG 是契约接口、不收自由字符串: task_type / event_category 硬枚举,字段做最低结构校验。
"""
from __future__ import annotations

from .logger import log

# 记账任务类型:硬枚举(承第 0 步契约①)。analysis=RESEARCH_ANALYSIS 分析, verify=EVIDENCE_REVIEW 搬运类校核, verify_judgment=EVIDENCE_REVIEW 判断类 GPT 异源(Verification 第 3 步)。
# 云 / 本地嵌入不在此区分——都记 'embed',差异落 model_id / quota_account / context.backend。
TASK_TYPES = ("distill", "embed", "rerank", "ocr", "analysis", "verify", "verify_judgment")

# 原因类别:RUNTIME_LOG 契约②自有的小词汇表,供 DECISION_LOG/MODEL_EVALUATION 归类路由(承设计文档 §三)。
REASON_CATEGORIES = ("verify", "spot_check", "rollback", "lifecycle", "exec_mode_switch")

# tokens 必含的四个键(承设计文档 §二 注 D:total 口径由上游 MODEL_GATEWAY 定,RUNTIME_LOG 只存)。
_TOKEN_KEYS = ("prompt", "completion", "reasoning", "total")

# 疑似真实凭据前缀(小写;匹配前先 .lower(),故大小写不敏感)。
# quota_account 只记额度别名 / 环境变量名 / 额度池名,绝不记真实 key / token / PAT。
_SECRET_PREFIXES = ("sk-", "ghp_", "gho_", "github_pat_", "xox", "pat-")


def log_accounting(task_type: str, model_id: str, *,
                   prompt_version: str | None = None,
                   tokens: dict | None = None,        # {prompt, completion, reasoning, total}
                   est_cost: dict | None = None,      # {value, currency: "CNY"}
                   quota_account: str | None = None,  # 额度别名 / 环境变量名 / 额度池名——绝不记真实 key
                   retries: int = 0,
                   cache_hit: bool = False,
                   module: str = "MODEL_GATEWAY",
                   event: str | None = None,
                   context: dict | None = None) -> dict:
    """契约①·记账:写一条 record_type='accounting' 的 8 字段记录(progress 级)。
    RUNTIME_LOG 只如实存;成本汇总与 ¥200 红线强制都在 MODEL_GATEWAY。字段做最低结构校验。"""
    if task_type not in TASK_TYPES:
        raise ValueError(f"未知 task_type: {task_type!r}(契约硬枚举,应为 {TASK_TYPES})")
    if not isinstance(model_id, str) or not model_id.strip():
        raise ValueError("model_id 必须是非空字符串(如实记实际档,承 v6.3 双档留痕)")
    prompt_version = _normalize_prompt_version(prompt_version)
    _validate_tokens(tokens)
    _validate_est_cost(est_cost)
    if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
        raise ValueError("retries 必须是非负整数")
    if not isinstance(cache_hit, bool):
        raise ValueError("cache_hit 必须是 bool")
    _guard_no_secret(quota_account)

    data = {                       # 8 字段齐(某项为 None 也保留键,便于下游统一汇总)
        "task_type": task_type,
        "model_id": model_id,
        "prompt_version": prompt_version,
        "tokens": tokens,
        "est_cost": est_cost,
        "quota_account": quota_account,
        "retries": retries,
        "cache_hit": cache_hit,
    }
    return log(module, event or f"{task_type} 调用记账 · {model_id}",
               level="progress", record_type="accounting", data=data, context=context)


def log_reason(module: str, event: str, reason: str, *,
               event_category: str | None = None,   # 见 REASON_CATEGORIES(含 exec_mode_switch,承 v6.3)
               target: str | None = None,           # 对象:paper_id / card / 批次
               context: dict | None = None) -> dict:
    """契约②·原因记录:写一条 record_type='reason' 带「为什么」的记录(progress 级)。
    供校核 / 抽检 / 回滚 / 生命周期变化;RUNTIME_LOG 只记 debug 上下文与原因,不建 KNOWLEDGE_ADMISSION_log(权威账本属 Intake)。
    module / event 沿用 log() 的非空校验;reason 必填非空;event_category 硬枚举。"""
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reason 必须是非空字符串(原因记录必须写明为什么)")
    if event_category is not None and event_category not in REASON_CATEGORIES:
        raise ValueError(f"未知 event_category: {event_category!r}(应为 {REASON_CATEGORIES} 或 None)")

    data: dict = {}
    if event_category is not None:
        data["event_category"] = event_category
    if target is not None:
        data["target"] = target
    return log(module, event, level="progress", reason=reason,
               record_type="reason", data=data or None, context=context)


# ── 内部校验 ────────────────────────────────────────────────
def _normalize_prompt_version(pv):
    """prompt_version 缺省 / 空 → 'n/a'(不随意缺失);给了非字符串则报错。"""
    if pv is None or (isinstance(pv, str) and not pv.strip()):
        return "n/a"
    if not isinstance(pv, str):
        raise ValueError("prompt_version 应为字符串或 None(缺省记 'n/a')")
    return pv


def _validate_tokens(tokens):
    if tokens is None:
        return
    if not isinstance(tokens, dict):
        raise ValueError("tokens 必须是 dict 或 None")
    missing = [k for k in _TOKEN_KEYS if k not in tokens]
    if missing:
        raise ValueError(f"tokens 缺键 {missing};须含 {_TOKEN_KEYS}(值为非负数字或 None)")
    for k in _TOKEN_KEYS:
        if not _is_nonneg_num_or_none(tokens[k]):
            raise ValueError(f"tokens['{k}'] 必须是非负数字或 None(得到 {tokens[k]!r})")


def _validate_est_cost(est_cost):
    if est_cost is None:
        return
    if not isinstance(est_cost, dict) or set(est_cost) != {"value", "currency"}:
        raise ValueError("est_cost 必须是 {value, currency}")
    if est_cost["currency"] != "CNY":
        raise ValueError("est_cost.currency 固定为 'CNY'(与 ¥200 红线同口径)")
    if not _is_nonneg_num(est_cost["value"]):
        raise ValueError("est_cost.value 必须是非负数字")


def _guard_no_secret(quota_account):
    """防呆:quota_account 疑似真实凭据即拒(承设计文档 §1.5:只记别名 / 环境变量名 / 额度池名)。"""
    if quota_account is None:
        return
    if not isinstance(quota_account, str):
        raise ValueError("quota_account 应为字符串(额度别名 / 环境变量名 / 额度池名)")
    v = quota_account.strip().lower()                 # 先 lower,大小写不敏感匹配
    if v.startswith(_SECRET_PREFIXES) or len(v) > 64:
        raise ValueError(
            "quota_account 疑似真实凭据;只记账户别名 / 环境变量名 / 额度池名,"
            "绝不记 key / token / PAT(承设计文档 §1.5)"
        )


def _is_nonneg_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x >= 0


def _is_nonneg_num_or_none(x):
    return x is None or _is_nonneg_num(x)
