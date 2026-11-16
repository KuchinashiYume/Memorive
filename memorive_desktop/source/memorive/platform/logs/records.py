"""The two structured records of the runtime log, both written through logger.log().

log_accounting(): each model call through the gateway writes one record_type='accounting' record
  with eight fields, told apart by task_type. The gateway sums them up; the log only stores them.
log_reason(): checks, spot checks, rollbacks and lifecycle changes write what happened and why
  (record_type='reason'); a change of the analysis execution mode uses event_category='exec_mode_switch'.

task_type and event_category take only the listed values; the fields get a basic shape check.
"""
from __future__ import annotations

from memorive.platform.logs.logger import log

# The task types of accounting records: analysis is the research analysis, verify a card review that
# checks transcription, verify_judgment a card review judgment by a second model. Cloud and local
# embeddings are both 'embed'; model_id, quota_account and context.backend tell them apart.
TASK_TYPES = ("distill", "embed", "rerank", "ocr", "analysis", "verify", "verify_judgment")

# The categories of reason records, so the decision log and model evaluation can sort them.
REASON_CATEGORIES = ("verify", "spot_check", "rollback", "lifecycle", "exec_mode_switch")

# The four keys tokens must have; the gateway decides what total counts, the log only stores it.
_TOKEN_KEYS = ("prompt", "completion", "reasoning", "total")

# Prefixes of real credentials (lower case; the value is lowered first). quota_account holds a quota
# alias, an environment variable name or a quota pool name, never a key, token or PAT.
_SECRET_PREFIXES = ("sk-", "ghp_", "gho_", "github_pat_", "xox", "pat-")


def log_accounting(task_type: str, model_id: str, *,
                   prompt_version: str | None = None,
                   tokens: dict | None = None,        # {prompt, completion, reasoning, total}
                   est_cost: dict | None = None,      # {value, currency: "CNY"}
                   quota_account: str | None = None,  # an alias or variable name, never a real key
                   retries: int = 0,
                   cache_hit: bool = False,
                   module: str = "MODEL_GATEWAY",
                   event: str | None = None,
                   context: dict | None = None) -> dict:
    """Write one accounting record (record_type='accounting', progress level) with its eight fields.
    The log stores it as given; summing costs and enforcing the spending limit are the gateway's job."""
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

    data = {                       # all eight keys, even when a value is None, so totals add up
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
               event_category: str | None = None,   # one of REASON_CATEGORIES
               target: str | None = None,           # what it is about: a paper id, a card, a batch
               context: dict | None = None) -> dict:
    """Write one reason record (record_type='reason', progress level): what happened and why, for
    checks, spot checks, rollbacks and lifecycle changes. It is debugging context only; the card
    state log is kept by memorive/documents/card_states. reason must not be empty."""
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


# ── checks ──────────────────────────────────────────────────
def _normalize_prompt_version(pv):
    """A missing or empty prompt_version becomes 'n/a'; anything but a string is refused."""
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
    """Refuse a quota_account that looks like a real credential; it may hold only an alias, a
    variable name or a quota pool name."""
    if quota_account is None:
        return
    if not isinstance(quota_account, str):
        raise ValueError("quota_account 应为字符串(额度别名 / 环境变量名 / 额度池名)")
    v = quota_account.strip().lower()
    if v.startswith(_SECRET_PREFIXES) or len(v) > 64:
        raise ValueError(
            "quota_account 疑似真实凭据;只记账户别名 / 环境变量名 / 额度池名,"
            "绝不记 key / token / PAT(承设计文档 §1.5)"
        )


def _is_nonneg_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x >= 0


def _is_nonneg_num_or_none(x):
    return x is None or _is_nonneg_num(x)
