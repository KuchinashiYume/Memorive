"""证据复核读取卡片用：取出卡片开头 --- … --- 之间的 YAML 头信息。

卡片读不了时抛 CardUnreadable；没有头信息、YAML 解析失败或解析结果不是映射时抛 CardParseError。
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from .errors import CardParseError, CardUnreadable

_FENCE = re.compile(r"(?m)^---[ \t]*\r?$")   # 稳健 fence(仿 knowledge_admission card_io),不用裸 split("---")


def _read_frontmatter(card_path: Path) -> dict:
    try:
        raw = card_path.read_bytes().decode("utf-8")
    except OSError as e:
        raise CardUnreadable(f"卡不可读: {card_path}({type(e).__name__}: {e})") from e
    fences = list(_FENCE.finditer(raw))
    if len(fences) < 2 or fences[0].start() != 0:
        raise CardParseError(f"卡 frontmatter 非法(需以 --- … --- 开头): {card_path}")
    try:
        fm = yaml.safe_load(raw[fences[0].end():fences[1].start()])
    except yaml.YAMLError as e:
        raise CardParseError(f"卡 frontmatter YAML 解析失败: {card_path}({e})") from e
    if not isinstance(fm, dict):
        raise CardParseError(f"卡 frontmatter 非 mapping: {card_path}")
    return fm
