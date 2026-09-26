"""MODEL_GATEWAY Fallback Policy + 依赖健康检查四类报错(改动⑦)。

- 瞬时错(网络/超时/429/5xx)先自动重试(≤MAX_RETRIES 次、退避);仍失败 → transient_exhausted、提示人
  (承拍板:耗尽仍归第④类,不误标②服务下线)。
- 配置类(key / 服务 / 分析校核)不重试、立即分类挡下。
- analysis / verify 的失败用户层归③(RESEARCH_ANALYSIS/EVIDENCE_REVIEW 暂停),但 context 保留底层 root_class(承要求 A)。
- 每次失败在 RUNTIME_LOG 留一条降级痕(log_error,自带上下文):retry 痕(action=retry)与最终挡下痕
  (action=block)区分(承要求 B);给人的是受控 DependencyError 四段文案(承要求 C,非崩溃)。
- 换备用:留 fallback_to 钩子、本步不建真实切换逻辑(无已验证备用槽,承拍板)。
"""
from __future__ import annotations

import os
import time
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError

from runtime_log import log_error

from .errors import ApiKeyMissing, DependencyError, HumanSlot, SlotDisabled

MAX_RETRIES = 2                          # 最多重试 2 次 = 共 3 次尝试
MAX_ATTEMPTS = MAX_RETRIES + 1
_BACKOFF = (0.5, 1.0)                     # 生产退避(秒),对应第 1 / 2 次重试后
_ENV_SLEEP = "MEMORIVE_MODEL_GATEWAY_RETRY_SLEEP"       # 测试可设 0 关闭等待(承拍板)

# 底层根因(不看 task_type)
ROOT_AUTH_OR_QUOTA = "auth_or_quota"
ROOT_SERVICE_UNAVAILABLE = "service_unavailable"
ROOT_SLOT_DISABLED = "slot_disabled"
ROOT_TRANSIENT = "transient"
ROOT_TRANSIENT_EXHAUSTED = "transient_exhausted"

# 用户四类(给人的)
CLASS_KEY = "key_or_quota"                              # ①
CLASS_SERVICE = "service_unavailable"                   # ②
CLASS_ANALYSIS_VERIFY = "analysis_verify_unavailable"   # ③
CLASS_TRANSIENT_EXHAUSTED = "transient_exhausted"       # ④(耗尽)

# ── 安全阀红线:归属保护(承 ServiceContracts 第 3 步)────────────────────────────
# 他人托付数据本地嵌入失败,**绝不兜底到云端**(否则等于把须严格本地的数据偷偷上云,违 D0)。
# 现无真实换备用逻辑(fallback_to 仅留钩子);此表把红线**写死**——将来任何换备用扩展,
# 在真正切换前必须先查 is_fallback_forbidden(src, dst),命中即拒、不得映射。
FORBIDDEN_FALLBACK = frozenset({("embed_local", "embed_cloud")})


def is_fallback_forbidden(src: str, dst: str) -> bool:
    """换备用是否被红线禁止(embed_local→embed_cloud = 他人数据偷偷上云,永远禁)。"""
    return (src, dst) in FORBIDDEN_FALLBACK


def ownership_local_message(task_type: str, slot, reason: str) -> str:
    """归属保护友好文案(四要素:哪个模块 + 哪个依赖 + 什么错 + 怎么处理);明写不上云。
    DocumentEmbed 见 ready:false 挡下时,经 RUNTIME_LOG detail 记它(不含任何 chunk 文本 / 凭据)。"""
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
        # fallback_to 换备用:留钩子,本步不建真实切换(无已验证备用槽)。
        # ⚠ 将来真建切换时:切换前必先查 is_fallback_forbidden(src,dst),挡下 embed_local→embed_cloud。

    def run(self, task_type: str, slot, dispatch_fn):
        """包住一次 dispatch:瞬时错重试、配置类分类挡下,每次失败留 RUNTIME_LOG 痕。
        成功 → (result, retries_used);最终失败 → 抛受控 DependencyError(不崩)。"""
        dep = _dependency(slot)
        step = f"{task_type} 调用({dep})"
        for attempt in range(1, self.max_attempts + 1):
            try:
                return dispatch_fn(), attempt - 1
            except HumanSlot:
                raise                       # 最终判断=人,不属依赖失败,原样放行
            except Exception as exc:
                root = _root_class(exc)
                if root == ROOT_TRANSIENT and attempt < self.max_attempts:
                    _record(task_type, dep, step, exc, root, action="retry",
                            attempt=attempt, max_attempts=self.max_attempts)
                    self._sleep(attempt)
                    continue
                if root == ROOT_TRANSIENT:  # 瞬时但重试耗尽 → 仍归④(不误标②)
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


# ── 分类 ────────────────────────────────────────────────────
def _root_class(exc) -> str:
    if isinstance(exc, SlotDisabled):
        return ROOT_SLOT_DISABLED
    if isinstance(exc, ApiKeyMissing):
        return ROOT_AUTH_OR_QUOTA
    if isinstance(exc, HTTPError):          # 注意:HTTPError 是 URLError 子类,须先判
        if exc.code in (401, 402, 403):
            return ROOT_AUTH_OR_QUOTA
        if exc.code in (404, 501):
            return ROOT_SERVICE_UNAVAILABLE
        if exc.code in (408, 409, 425, 429) or 500 <= exc.code <= 599:
            return ROOT_TRANSIENT           # 仅超时/冲突/限流/5xx 视作瞬时
        return ROOT_SERVICE_UNAVAILABLE     # 其余 4xx 是请求/契约错误，一次挡下
    if isinstance(exc, (URLError, TimeoutError, ConnectionError, IncompleteRead)):
        return ROOT_TRANSIENT
    return ROOT_SERVICE_UNAVAILABLE         # 未知 → 保守当服务问题


def _user_class(root: str, task_type: str) -> str:
    if task_type in ("analysis", "verify", "verify_judgment"):
        return CLASS_ANALYSIS_VERIFY        # ③(RESEARCH_ANALYSIS/EVIDENCE_REVIEW 暂停;底层 root 仍保留在 RUNTIME_LOG context,承要求 A)
    return {
        ROOT_AUTH_OR_QUOTA: CLASS_KEY,
        ROOT_SERVICE_UNAVAILABLE: CLASS_SERVICE,
        ROOT_SLOT_DISABLED: CLASS_SERVICE,
        ROOT_TRANSIENT_EXHAUSTED: CLASS_TRANSIENT_EXHAUSTED,
    }.get(root, CLASS_SERVICE)


# ── 留痕 & 文案 ─────────────────────────────────────────────
def _record(task_type, dep, step, exc, root_class, *, action, attempt, max_attempts, error_class=None):
    event = (f"[{task_type}] 依赖瞬时失败,自动重试" if action == "retry"
             else f"[{task_type}] 依赖不可用,已挡下")
    context = {
        "step": step,                                # RUNTIME_LOG 错误 context 必含
        "error": f"{type(exc).__name__}: {exc}",     # RUNTIME_LOG 错误 context 必含
        "dependency": dep,
        "task_type": task_type,
        "root_class": root_class,                    # 底层根因(承要求 A)
        "action": action,                            # retry / block(承要求 B)
        "attempt": attempt,
        "max_attempts": max_attempts,
    }
    if error_class is not None:
        context["error_class"] = error_class         # 用户四类(仅 block 有终值)
    log_error("MODEL_GATEWAY", event, context=context)          # 自动 detail 级、自带上下文;绝不静默


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
