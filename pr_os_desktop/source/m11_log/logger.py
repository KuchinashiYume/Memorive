"""M11 日志统一写入入口 + 双级机制骨架。

对外正式入口只一个: 模块级 log();log_error() 是 log(..., is_error=True) 的便捷通道,
**非第二套写入机制**。各模块经它写,由入口统一补时间戳、序列化 (JSONL)、append 落盘——
各模块不得各写各的落盘逻辑;唯一落盘点是 _append()。

本步 (T2 第1步) 只做骨架: 写通一条 + 出错自动升详细级。
分区抽取 (第2步) / 记账 + 原因记录 (第3步) / append-only 硬拦截 (第4步) 留挂接位,
见下方 [第N步] 注释;骨架不用重构即可挂上。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .config import get_log_path

LEVELS = ("progress", "detail")                    # 进度级 (给人) / 详细级 (给 AI, 出错自动升)
RECORD_TYPES = ("event", "accounting", "reason")   # event / 记账 / 原因 (后两者见 records.py)
_ERROR_CONTEXT_KEYS = ("step", "error")            # 错误 context 必含: 哪一步 + 什么错 (模块名在顶层 module)


class AppendOnlyViolation(RuntimeError):
    """append-only 不变式被破坏 (写入后文件竟变小 = 疑发生截断 / 覆盖)。

    注: 这只是**部分防呆**——只抓得到"文件变小",抓不到"用 w 覆盖但新内容更长"。
    append-only 的核心保障是: 唯一 _append() + 硬编码 open(.., "a") + 无覆盖 / 清空 API +
    extract 禁 out_path=源 log;完整历史不被改写由"前缀不变"测试证明 (见第 4 步演示)。
    """


class Logger:
    """统一写入入口的实体。**内部 / 测试注入用**——业务模块勿直接实例化,一律经模块级
    log() / log_error();持有落盘路径,便于测试注入临时路径 (不导出到包公共入口)。"""

    def __init__(self, log_path: Path | None = None):
        self.log_path = log_path or get_log_path()

    def log(self, module: str, event: str, *, level: str = "progress",
            context: dict | None = None, reason: str | None = None,
            record_type: str = "event", data: dict | None = None,
            is_error: bool = False) -> dict:
        """写一条日志。补 ts、序列化、append;返回落盘的那条 entry。

        出错自动升级 (承修订B, 机器可判定): is_error=True 即把该条强制升 detail 级,
        不靠 event 文本关键词猜错误、不需人工切档。
        错误日志必须带非空 context (定位所需),否则拒写 (承修订: 自带上下文)。
        """
        if is_error:
            level = "detail"                       # ← 出错自动升详细级 (唯一升级触发口)
        _validate(module, event, level, record_type)
        if is_error:
            _validate_error_context(context)   # 强化: 非空 dict 且含非空 step + error (自带上下文达标)
        entry = self._build_entry(module, event, level, record_type, context, reason, data)
        self._append(_to_jsonl(entry))
        return entry

    def log_error(self, module: str, event: str, *, context: dict | None = None,
                  reason: str | None = None) -> dict:
        """错误事件便捷通道 (承修订B): 等价 log(..., is_error=True),**非第二套写入机制**。
        context 必填非空 dict (校验见 log)。"""
        return self.log(module, event, context=context, reason=reason, is_error=True)

    # ── 内部 ────────────────────────────────────────────────
    def _build_entry(self, module, event, level, record_type, context, reason, data) -> dict:
        entry = {
            "ts": _now_iso(),          # ← 入口统一补时间戳,调用方不碰
            "module": module,          # ← 分区键 (第2步按它抽取)
            "level": level,
            "record_type": record_type,
            "event": event,
        }
        if context is not None:
            entry["context"] = context
        if reason is not None:         # [第3步] 原因记录 (record_type='reason') 主用此位
            entry["reason"] = reason
        if data is not None:           # [第3步] 记账 8 字段 / 原因扩展走 data 结构化块
            entry["data"] = data
        return entry

    def _append(self, line: str) -> None:
        """唯一落盘点。硬编码 append 模式,不提供任何覆盖 / 截断路径 (append-only 核心)。"""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)   # 确保运维目录在
        before = self.log_path.stat().st_size if self.log_path.exists() else 0
        with open(self.log_path, "a", encoding="utf-8") as f:     # ← 唯一写路径、硬编码 "a"、无覆盖入口
            f.write(line)
        # 部分防呆 (非完整证明): 文件只增不减,写后变小即疑截断 / 覆盖 → 抛。
        # 抓不到"w 覆盖但更长"的情形;完整历史不被改写靠"结构性无覆盖 API + 前缀不变测试"。
        after = self.log_path.stat().st_size
        if after < before:
            raise AppendOnlyViolation(
                f"append-only 被破坏: 写后文件变小 ({before}→{after} 字节),疑覆盖 / 截断")


# ── 模块级唯一入口 (镜像 m9_gateway.call 的用法) ──────────────
_default: Logger | None = None


def _get_default() -> Logger:
    global _default
    current_path = get_log_path()
    if _default is None or _default.log_path != current_path:
        _default = Logger(current_path)
    return _default


def log(module: str, event: str, *, level: str = "progress", context: dict | None = None,
        reason: str | None = None, record_type: str = "event", data: dict | None = None,
        is_error: bool = False) -> dict:
    """M11 唯一对外写入口。业务: from m11_log import log; log("M1b", "开始处理第3篇")"""
    return _get_default().log(module, event, level=level, context=context, reason=reason,
                              record_type=record_type, data=data, is_error=is_error)


def log_error(module: str, event: str, *, context: dict | None = None,
              reason: str | None = None) -> dict:
    """错误事件便捷入口 (自动升 detail, context 必填非空)。业务: from m11_log import log_error"""
    return _get_default().log_error(module, event, context=context, reason=reason)


# ── 辅助 ────────────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")   # 2026-07-01T14:32:05+08:00


def _to_jsonl(entry: dict) -> str:
    return json.dumps(entry, ensure_ascii=False) + "\n"   # 中文不转义,日志可读


def _validate(module: str, event: str, level: str, record_type: str) -> None:
    if not isinstance(module, str) or not module.strip():
        raise ValueError("module 必须是非空字符串 (不能为空或纯空白)")
    if not isinstance(event, str) or not event.strip():
        raise ValueError("event 必须是非空字符串 (不能为空或纯空白)")
    if level not in LEVELS:
        raise ValueError(f"未知 level: {level!r} (应为 {LEVELS})")
    if record_type not in RECORD_TYPES:
        raise ValueError(f"未知 record_type: {record_type!r} (应为 {RECORD_TYPES})")


def _validate_error_context(context) -> None:
    """错误日志 context 达标校验 (自带上下文): 非空 dict 且含非空 step + error。
    module 在顶层已保证;错误若涉及外部模型 / API / 文件 / DB,建议再补
    call / dependency / file / paper_id / chunk_id / input_summary 等 (非硬性)。"""
    if not (isinstance(context, dict) and context):
        raise ValueError(
            "错误日志 (is_error / log_error) 必须提供非空 context (dict);见设计文档 §1.5。")
    missing = [k for k in _ERROR_CONTEXT_KEYS
               if not (isinstance(context.get(k), str) and context.get(k).strip())]
    if missing:
        raise ValueError(
            f"错误日志 context 必含非空 {list(_ERROR_CONTEXT_KEYS)} (缺 / 空: {missing});"
            "达到「陌生 AI 光看一条即可定位: 哪个模块(module)+ 第几步(step)+ 什么错(error)」;"
            "涉外部依赖时建议再补 call / dependency / file / paper_id / chunk_id / input_summary。")
