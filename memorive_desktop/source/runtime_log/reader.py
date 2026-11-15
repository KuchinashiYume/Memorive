"""RUNTIME_LOG 按模块抽取 —— 从 log.txt 里按 module 字段筛出单模块日志段。

用途:排错时只把某模块(如 DocumentConvert)那一段单独取出发给 AI,不必倒整份日志。
保持轻量:只做「按 module 字段筛出一段」,不是检索引擎
(无索引 / 查询语言 / 时间范围 / 告警);读源日志**逐行流式读**,适配长期日志。
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import get_log_path


def extract_module_logs(module: str, *, log_path: Path | str | None = None,
                        out_path: Path | str | None = None) -> list[str]:
    """按 module 字段筛出该模块的所有日志行(保持原文件顺序,原样 JSONL 字符串)。

    module   : 目标模块名,**精确匹配** module 字段(不会把 'DOCUMENT_PROCESSING' 误匹配 'DocumentConvert')。
    log_path : 源日志,默认当前生效的 log.txt(可配置)。
    out_path : 给定则把筛出的行另存到该文件(单独发 AI 用);不给则只返回。
               **不得等于源 log.txt**(否则会把正式日志覆盖成抽取子集,拒之以守 append-only)。
    返回:筛出的原始行列表(不含换行)。源文件不存在时返回 []。
    """
    if not isinstance(module, str) or not module.strip():
        raise ValueError("module 必须是非空字符串(不能为空或纯空白)")

    src = Path(log_path) if log_path else get_log_path()
    dst = Path(out_path) if out_path is not None else None
    if dst is not None and dst.resolve() == src.resolve():
        raise ValueError(
            "out_path 不能等于源 log.txt(避免把正式日志覆盖成抽取子集;守 append-only 红线)"
        )

    picked: list[str] = []
    if src.exists():
        with src.open("r", encoding="utf-8") as f:      # 逐行流式读,适配长期日志
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue                            # 跳过异常行(正常写入不会出现)
                if entry.get("module") == module:       # ← 精确按 module 字段筛
                    picked.append(line)

    if dst is not None:
        dst.parent.mkdir(parents=True, exist_ok=True)   # 写出前建父目录
        dst.write_text("\n".join(picked) + ("\n" if picked else ""), encoding="utf-8")

    return picked
