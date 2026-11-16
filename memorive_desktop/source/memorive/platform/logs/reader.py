"""One module's lines from log.txt, picked by the module field.

For troubleshooting: the lines of one module (DocumentConvert, say) can be handed to an AI without
the whole log. It only filters by module; there is no index, query language or time range. The log
is read line by line, so a long log is fine.
"""
from __future__ import annotations

import json
from pathlib import Path

from memorive.platform.logs.config import get_log_path


def extract_module_logs(module: str, *, log_path: Path | str | None = None,
                        out_path: Path | str | None = None) -> list[str]:
    """All log lines of one module, in file order, as the JSON lines they are.

    module   : the module name, matched exactly against the module field.
    log_path : the log to read; by default the log.txt in use.
    out_path : if given, the lines are also written to this file. It may not be the log itself,
               which would replace the log with the extract.
    Returns the lines without line breaks; [] when the log does not exist.
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
        with src.open("r", encoding="utf-8") as f:      # line by line, for long logs
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue                            # a malformed line (normal writes make none)
                if entry.get("module") == module:
                    picked.append(line)

    if dst is not None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text("\n".join(picked) + ("\n" if picked else ""), encoding="utf-8")

    return picked
