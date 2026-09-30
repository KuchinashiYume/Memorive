"""Memorive RUNTIME-LOG/RUNTIME_LOG · RUNTIME_LOG 日志 / Debug —— 系统黑匣子。

对外正式入口只有 log();log_error() 是 log(..., is_error=True) 的便捷通道 (非第二套机制)。
业务写入: from runtime_log import log[, log_error]。
排错抽取: from runtime_log import extract_module_logs(按模块名抽出一段日志)。
两契约:   from runtime_log import log_accounting(记账·契约①) / log_reason(原因记录·契约②)。
格式与契约见
contracts/Core_最小可用闭环/10_模块契约/Memorive_RUNTIME_LOG_RUNTIME_LOG_日志设计.md。

(Logger 类属内部 / 测试注入用,不在公共入口;业务模块勿直接实例化——
 需要注入路径的测试可 from runtime_log.logger import Logger。)
"""
from .logger import log, log_error
from .reader import extract_module_logs
from .records import log_accounting, log_reason

__all__ = ["log", "log_error", "extract_module_logs", "log_accounting", "log_reason"]
