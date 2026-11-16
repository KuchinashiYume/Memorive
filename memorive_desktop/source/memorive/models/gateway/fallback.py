"""What the gateway does when a model call fails, and the four error classes shown to the user.

- A transient error (network, timeout, 429, 5xx) is retried up to MAX_RETRIES times with back-off;
  if it still fails, the class is transient_exhausted (class 4), not "service down".
- Configuration errors (key, service, the analysis or review model) are not retried; the call
  stops at once with its class.
- For analysis and card review, the user sees class 3 (analysis or review paused); the log context
  keeps the underlying root_class.
- Every failure writes an error entry to the runtime log: action=retry for a retry, action=block
  for the final stop. The user gets a DependencyError with a four-part message, not a crash.
- Switching to a backup slot is not built; there is no verified backup slot.
"""
from __future__ import annotations

import os
import time
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError

from memorive.platform.logs import log_error

from memorive.models.gateway.errors import ApiKeyMissing, DependencyError, HumanSlot, SlotDisabled

MAX_RETRIES = 2                          # 3 attempts in all
MAX_ATTEMPTS = MAX_RETRIES + 1
_BACKOFF = (0.5, 1.0)                     # seconds to wait after the first and second attempt
_ENV_SLEEP = "MEMORIVE_MODEL_GATEWAY_RETRY_SLEEP"       # tests set it to 0 to skip the waits

# The underlying cause, whatever the task type
ROOT_AUTH_OR_QUOTA = "auth_or_quota"
ROOT_SERVICE_UNAVAILABLE = "service_unavailable"
ROOT_SLOT_DISABLED = "slot_disabled"
ROOT_TRANSIENT = "transient"
ROOT_TRANSIENT_EXHAUSTED = "transient_exhausted"

# The four classes the user sees
CLASS_KEY = "key_or_quota"                              # 1
CLASS_SERVICE = "service_unavailable"                   # 2
CLASS_ANALYSIS_VERIFY = "analysis_verify_unavailable"   # 3
CLASS_TRANSIENT_EXHAUSTED = "transient_exhausted"       # 4: retries used up

# ── data ownership ────────────────────────────────────────────────────────────
# When local embedding of entrusted data fails, it never falls back to the cloud: that data must stay
# on this computer. There is no backup switching yet; any future switch must ask
# is_fallback_forbidden(src, dst) first and refuse a forbidden pair.
FORBIDDEN_FALLBACK = frozenset({("embed_local", "embed_cloud")})


def is_fallback_forbidden(src: str, dst: str) -> bool:
    """Whether switching from src to dst is forbidden: embed_local to embed_cloud would send data
    entrusted to the user to the cloud, so it never happens."""
    return (src, dst) in FORBIDDEN_FALLBACK


def ownership_local_message(task_type: str, slot, reason: str) -> str:
    """The message about data ownership when local embedding is not ready: which module, which
    dependency, what went wrong, what to do; it says plainly that nothing goes to the cloud. Document
    embedding logs it at detail level when it stops (no chunk text, no credentials)."""
    model = getattr(slot, "model_id", None) or "本地模型"
    return (f"[MODEL_GATEWAY/{task_type}] 归属保护:他人托付数据须本地嵌入,但本地 Ollama({model})未就绪"
            f"({reason})。已挡下、未上云。请启动 Ollama 并 `ollama pull {model}` 后重试;"
            f"**绝不改走云端**,这批他人数据本次不嵌入。")


class FallbackPolicy:
    def __init__(self, backoff=_BACKOFF, *, max_retries: int = MAX_RETRIES):
        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0:
            raise ValueError("max_retries must be a non-negative integer")
        self.backoff = tuple(backoff)
        if max_retries and not self.backoff:
            raise ValueError("backoff must contain at least one delay when retries are enabled")
        self.max_retries = max_retries
        self.max_attempts = max_retries + 1
        # Switching to a backup slot is not built. A future switch must check
        # is_fallback_forbidden(src, dst) first, which refuses embed_local -> embed_cloud.

    def run(self, task_type: str, slot, dispatch_fn):
        """Run one request: retry transient errors, stop at configuration errors, log every failure.
        Returns (result, retries_used); a final failure raises DependencyError."""
        dep = _dependency(slot)
        step = f"{task_type} 调用({dep})"
        for attempt in range(1, self.max_attempts + 1):
            try:
                return dispatch_fn(), attempt - 1
            except HumanSlot:
                raise                       # a human slot is not a dependency failure
            except Exception as exc:
                root = _root_class(exc)
                if root == ROOT_TRANSIENT and attempt < self.max_attempts:
                    _record(task_type, dep, step, exc, root, action="retry",
                            attempt=attempt, max_attempts=self.max_attempts)
                    self._sleep(attempt)
                    continue
                if root == ROOT_TRANSIENT:  # retries used up: class 4, not "service down"
                    root = ROOT_TRANSIENT_EXHAUSTED
                error_class = _user_class(root, task_type)
                _record(task_type, dep, step, exc, root, action="block",
                        error_class=error_class, attempt=attempt, max_attempts=self.max_attempts)
                raise DependencyError(error_class, root, task_type,
                                      _message(task_type, dep, error_class, root, exc,
                                               max_attempts=self.max_attempts)) from exc

    def _sleep(self, attempt: int) -> None:
        override = os.environ.get(_ENV_SLEEP)
        secs = float(override) if override is not None else self.backoff[min(attempt - 1, len(self.backoff) - 1)]
        if secs > 0:
            time.sleep(secs)


# ── classes ─────────────────────────────────────────────────
def _root_class(exc) -> str:
    if isinstance(exc, SlotDisabled):
        return ROOT_SLOT_DISABLED
    if isinstance(exc, ApiKeyMissing):
        return ROOT_AUTH_OR_QUOTA
    if isinstance(exc, HTTPError):          # HTTPError is a URLError, so it comes first
        if exc.code in (401, 402, 403):
            return ROOT_AUTH_OR_QUOTA
        if exc.code in (404, 501):
            return ROOT_SERVICE_UNAVAILABLE
        if exc.code in (408, 409, 425, 429) or 500 <= exc.code <= 599:
            return ROOT_TRANSIENT           # timeouts, conflicts, rate limits and 5xx only
        return ROOT_SERVICE_UNAVAILABLE     # other 4xx are request errors: stop at once
    if isinstance(exc, (URLError, TimeoutError, ConnectionError, IncompleteRead)):
        return ROOT_TRANSIENT
    return ROOT_SERVICE_UNAVAILABLE         # anything else counts as a service problem


def _user_class(root: str, task_type: str) -> str:
    if task_type in ("analysis", "verify", "verify_judgment"):
        return CLASS_ANALYSIS_VERIFY        # 3; the log context keeps the root class
    return {
        ROOT_AUTH_OR_QUOTA: CLASS_KEY,
        ROOT_SERVICE_UNAVAILABLE: CLASS_SERVICE,
        ROOT_SLOT_DISABLED: CLASS_SERVICE,
        ROOT_TRANSIENT_EXHAUSTED: CLASS_TRANSIENT_EXHAUSTED,
    }.get(root, CLASS_SERVICE)


# ── log entries and messages ────────────────────────────────
def _record(task_type, dep, step, exc, root_class, *, action, attempt, max_attempts, error_class=None):
    event = (f"[{task_type}] 依赖瞬时失败,自动重试" if action == "retry"
             else f"[{task_type}] 依赖不可用,已挡下")
    context = {
        "step": step,                                # required in an error entry
        "error": f"{type(exc).__name__}: {exc}",     # required in an error entry
        "dependency": dep,
        "task_type": task_type,
        "root_class": root_class,                    # the underlying cause
        "action": action,                            # retry or block
        "attempt": attempt,
        "max_attempts": max_attempts,
    }
    if error_class is not None:
        context["error_class"] = error_class         # the user class, set when the call stops
    log_error("MODEL_GATEWAY", event, context=context)


def _message(task_type, dep, error_class, root_class, exc, *, max_attempts: int = MAX_ATTEMPTS) -> str:
    brief = _errbrief(exc)
    if error_class == CLASS_KEY:
        return (f"[MODEL_GATEWAY/{task_type}] 依赖 {dep} 的 key 失效或欠费({brief});"
                f"请更新对应环境变量的 key 或充值后重试,本环节已暂停。")
    if error_class == CLASS_SERVICE:
        return (f"[MODEL_GATEWAY/{task_type}] 依赖 {dep} 服务不可用 / 接口变更({brief});"
                f"请检查 endpoint 或切到备用后端(D3 可插拔),本环节已暂停。")
    if error_class == CLASS_ANALYSIS_VERIFY:
        return (f"[MODEL_GATEWAY/{task_type}] 分析 / 校核模型(API)不可用(底层:{root_class});"
                f"RESEARCH_ANALYSIS / EVIDENCE_REVIEW 暂停(受影响:{task_type} 产出);可改手动档(Claude Code 订阅)或待 API 就绪。")
    if error_class == CLASS_TRANSIENT_EXHAUSTED:
        return (f"[MODEL_GATEWAY/{task_type}] 依赖 {dep} 连续 {max_attempts} 次网络 / 超时失败,"
                f"已自动重试无果,请检查网络或稍后重试。")
    return f"[MODEL_GATEWAY/{task_type}] 依赖 {dep} 调用失败({brief})。"


def _errbrief(exc) -> str:
    return f"HTTP {exc.code}" if isinstance(exc, HTTPError) else type(exc).__name__


def _dependency(slot) -> str:
    prov = slot.provider or "?"
    return f"{prov}/{slot.model_id}" if slot.model_id else prov
