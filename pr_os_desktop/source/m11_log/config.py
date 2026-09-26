"""PR-OS P01/T02/M11 · M11 日志落盘路径解析。

默认落库外运维区 (承 D2: 日志属运维数据、不进嵌入/检索区、库外不入 git);
可经环境变量 PROS_M11_LOG_DIR 覆盖 (便携 / 测试用)。路径解析独立于 logger 核心,
换落点只改这一处。
"""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_LOG_DIR = r"G:\PR-OS-运维\日志"      # 库外运维区 (承 D2)
LOG_FILENAME = "log.txt"
ENV_LOG_DIR = "PROS_M11_LOG_DIR"             # 覆盖默认落点的环境变量名


def get_log_path() -> Path:
    """当前生效的 log.txt 路径 (默认库外运维区,可经 PROS_M11_LOG_DIR 覆盖)。"""
    return Path(os.environ.get(ENV_LOG_DIR, DEFAULT_LOG_DIR)) / LOG_FILENAME
