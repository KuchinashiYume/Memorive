"""M8_log —— M8 自有的按年权威状态账本(承 Step 0 契约 §三:M8_log 属 T5、M11 不建 M8_log)。

每次成功的 review_status 转移 append 一条:日期 / 数据ID / 原→新 / 触发源 / 依据 五字段齐。
落盘:JSONL、按年分文件 M8_log_YYYY.jsonl、库外运维区。

「复用 M11 log_reason 契约」= 借形状、不借落盘:
  借 —— reason 必填非空 + event_category 硬枚举的**字段契约形状** + 「唯一 _append 入口 + 硬编码 append」的
        append-only 纪律;
  不借 —— 不调 M11 的 log() / _append(那落 M11 单文件 log.txt),不把 M8_log 降格成 M11 一条 reason 记录。
        M8_log 是 M8 自有账本、自有落点、自有 _append(账本归属是 M8 的)。

append-only 是**模块纪律**:模块内只提供 append + read、无覆盖 / 清空入口、唯一落盘点硬编码 append 模式。
本模块只承诺「模块内 append-only、无覆盖入口」——**不**承诺挡住模块外以覆盖模式另开文件写入
(库外不入 git、拦不住外部进程)。
隔离边界:属运维数据、不进 M1 嵌入 / 不进 M3 检索(与 M11 日志、T3 向量库同区)。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from .errors import M8LogAppendError

DEFAULT_M8_LOG_DIR = r"G:\PR-OS-运维\M8状态账本"   # 库外运维区(与 M11 日志、T3 向量库同区)
ENV_M8_LOG_DIR = "PROS_M8_LOG_DIR"                  # 覆盖默认落点(便携 / 测试)
EVENT_CATEGORIES = ("review_status",)               # 借 log_reason 的 event_category 形状;本步只此一类


def _log_dir() -> Path:
    return Path(os.environ.get(ENV_M8_LOG_DIR, DEFAULT_M8_LOG_DIR))


def _log_path(year: int) -> Path:
    return _log_dir() / f"M8_log_{year}.jsonl"      # 按年分文件


def preflight() -> None:
    """pre-flight:确保 M8_log 目录存在且可写。**在 write_status 之前调**,把「目录缺失 / 不可写」
    挡在状态改动之前(留痕缺口窗口只剩罕见磁盘错)。不可建 / 不可写 → M8LogAppendError。"""
    d = _log_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise M8LogAppendError(f"M8_log 目录不可建({d}): {e}") from e
    if not os.access(d, os.W_OK):
        raise M8LogAppendError(f"M8_log 目录不可写: {d}")


def append_transition(*, data_id: str, from_status: str, to_status: str,
                      trigger: str, reason: str, event_category: str = "review_status") -> dict:
    """append 一条 M8_log(五字段齐 + 借形状字段)。reason 必填非空(借 log_reason 契约)。
    唯一落盘点 = _append(硬编码 append、无覆盖入口)。返回落盘的那条 entry。"""
    for _name, _val in (("data_id", data_id), ("from_status", from_status),
                        ("to_status", to_status), ("trigger", trigger), ("reason", reason)):
        if not (isinstance(_val, str) and _val.strip()):
            raise M8LogAppendError(f"{_name} 必填非空字符串(权威账本入参防呆;reason 非空 = 借 log_reason 契约): {_val!r}")
    if event_category not in EVENT_CATEGORIES:
        raise M8LogAppendError(f"未知 event_category: {event_category!r}(应为 {EVENT_CATEGORIES})")
    now = datetime.now().astimezone()
    entry = {"ts": now.isoformat(timespec="seconds"), "module": "M8", "record_type": "reason",
             "event_category": event_category, "data_id": data_id,
             "from": from_status, "to": to_status, "trigger": trigger, "reason": reason}
    _append(_log_path(now.year), json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def _append(path: Path, line: str) -> None:
    """唯一落盘点。硬编码 append 模式,不提供任何覆盖 / 截断路径(模块内 append-only 核心,承 M11 同款纪律)。
    **所有落盘失败统一收成 M8LogAppendError**:真实磁盘错 / ACL 拒绝 / 目标是目录(IsADirectoryError)等 OSError、
    以及写后收缩,都走 M8LogAppendError —— 兜住 pre-flight(win32 os.access 弱)漏过的情形,完成审计缺口契约:
    append 失败绝不静默(由 transition 暴露「状态已变、留痕失败」)。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        before = path.stat().st_size if path.exists() else 0
        with open(path, "a", encoding="utf-8") as f:     # ← 唯一写路径、硬编码 append、无覆盖入口
            f.write(line)
        after = path.stat().st_size
    except OSError as e:
        raise M8LogAppendError(f"M8_log 落盘失败({type(e).__name__}): {path}: {e}") from e
    if after < before:                                   # 部分防呆:写后变小 = 疑截断
        raise M8LogAppendError(f"append-only 被破坏:写后文件变小,疑截断: {path}")


def read_records(year: int) -> list[dict]:
    """读某年 M8_log 全部记录(只读;供校核视图 / 自测)。文件不存在 → 空列表。
    坏行 / 半行(如崩溃时半写的末行)→ 跳过,不抛未受控 JSONDecodeError。"""
    path = _log_path(year)
    if not path.exists():
        return []
    out: list[dict] = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue                                 # 跳过坏行(容错;不 crash 读取器)
    return out
