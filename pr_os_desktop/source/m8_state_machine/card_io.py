"""状态内嵌于卡片:read/write [Card] frontmatter 的 review_status(不另建独立状态库)。

读 = 解析 frontmatter 取该字段;转移 = **只改该字段那一行的值**(其余 frontmatter/正文/| 块/行尾 逐字节保全,
承 M13 永不误改),经原子写(先验 tmp 再替换)落盘、落盘后自证。

frontmatter 定界:**只认独占一行的 `---`**(行锚定 fence),取前两个 fence 的偏移、切片拼接 →
pre/body 逐字节不变;含字面 `---` 的字段值(如 `title: A---B`)不再误断边界。opening fence 必须在 offset 0。
CRLF 安全:fence 与 review_status 行的正则都容忍并**保留**可选 \r(朴素 ^---$ / [ \t]*$ 在 CRLF 卡 finditer
命中 0、会挡死好卡)。I/O 走 bytes(read_bytes/decode + 二进制写)、**不做换行翻译**,保证行尾字节级保全
(CRLF 写后仍 CRLF)。

不变量(写死):卡片 review_status 恒为**无引号 plain scalar**(card.py 的 yaml.dump 保证)。
write_status 只认这一形态:带引号/异形/非唯一 → CardStateError(fail-closed、不腐蚀文件);不引 ruamel、不整帧 re-dump。

⚠ 债(全仓同源、本步不修):distill/_read_frontmatter、m1c、m1d、pipeline 仍用 split("---", 2),
含字面 `---` 的字段值在那些模块仍会误框。本步只修 M8 自己(它是准入闸门、误挡合法卡代价更高);
全仓统一处置登记为债(见 T5 完成存档「债」节;将来有 M12 补 DDL)。
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import yaml

from .errors import CardStateError
from .states import STATES

# 独占一行的 --- fence(CRLF 安全:容忍尾随空白与可选 \r;朴素 ^---$ 在 CRLF 卡命中 0)。
_FENCE = re.compile(r"(?m)^---[ \t]*\r?$")
# review_status 行:无引号 plain scalar;group3 捕获并保留行尾(可选 \r),surgical 改值不 churn 行尾。
_STATUS_LINE = re.compile(r"^(review_status:[ \t]*)([A-Za-z_]+)([ \t]*\r?)$", re.MULTILINE)


def _read_text(card_path: Path) -> str:
    """读原始文本、**不做换行翻译**(read_text 会 \\r\\n→\\n、破坏行尾字节保全)。"""
    return card_path.read_bytes().decode("utf-8")


def _split_frontmatter(card_path: Path, text: str) -> tuple[str, str, str]:
    """按**独占一行的 --- fence**定界 → (pre, front, body),pre + front + body == text(逐字节)。
    opening fence 必须在 offset 0;需至少两个 fence;否则 frontmatter 非法 → CardStateError。"""
    fences = list(_FENCE.finditer(text))
    if len(fences) < 2 or fences[0].start() != 0:
        raise CardStateError(f"卡片 frontmatter 非法(需以独占一行 --- 开头、且有闭合 ---): {card_path}")
    f1, f2 = fences[0], fences[1]
    return text[:f1.end()], text[f1.end():f2.start()], text[f2.start():]


def read_status(card_path: Path) -> str:
    """读卡片当前 review_status(权威解析)。缺字段 / 值不在三态 / 结构非法 → CardStateError。"""
    _pre, front, _body = _split_frontmatter(card_path, _read_text(card_path))
    fm = yaml.safe_load(front)
    if not isinstance(fm, dict):
        raise CardStateError(f"卡片 frontmatter 解析非 dict: {card_path}")
    status = fm.get("review_status")
    if status not in STATES:
        raise CardStateError(f"review_status 缺失/不在三态枚举({status!r}): {card_path}")
    return status


def write_status(card_path: Path, new_status: str) -> None:
    """把 review_status 那一行的值原地改成 new_status,**只动这一处**、其余字节(含行尾)不变;
    经原子写(先验 tmp 再替换)落盘 + 自证。(调用方保证 new_status 已过守卫。)"""
    pre, front, body = _split_frontmatter(card_path, _read_text(card_path))
    # 段1:显式核「恰好一行」无引号 review_status。0 或 >1 都挡、文件不动。
    matches = list(_STATUS_LINE.finditer(front))
    if len(matches) != 1:
        raise CardStateError(
            f"frontmatter 内无引号 review_status 行非唯一(找到 {len(matches)} 行): {card_path}")
    new_front = _STATUS_LINE.sub(rf"\g<1>{new_status}\g<3>", front, count=1)  # 保留前缀 + 行尾(含 \r)
    new_text = pre + new_front + body
    # 段2:原子写「先验 tmp 再替换」—— 二进制写不翻译行尾;坏 new_text 绝不先覆盖好原件(承 M13 永不误改)。
    fd, tmp = tempfile.mkstemp(dir=card_path.parent, prefix=".m8_tmp_", suffix=".m8tmp")
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(new_text.encode("utf-8"))
        if read_status(tmp_path) != new_status:            # 先验 tmp:确认临时文件已是新态
            raise CardStateError(f"临时文件复核不符(期望 {new_status}): {card_path}")
        os.replace(tmp_path, card_path)                    # 无误才原子替换正式卡(同盘)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    # 段3:替换后再读正式卡复核(双保险)。
    if read_status(card_path) != new_status:
        raise CardStateError(f"write_status 落盘复核不符(期望 {new_status}): {card_path}")


def read_data_id(card_path: Path) -> str:
    """数据 ID(**严格**):读 `source_anchor.paper_id`(D2 跨层稳定标识)。
    缺 / 空 / 类型不对 → CardStateError,**不回退文件名**(由调用方在 write_status 之前调,缺则状态不改)。"""
    _pre, front, _body = _split_frontmatter(card_path, _read_text(card_path))
    fm = yaml.safe_load(front)
    if not isinstance(fm, dict):
        raise CardStateError(f"卡片 frontmatter 解析非 dict: {card_path}")
    anchor = fm.get("source_anchor")
    pid = anchor.get("paper_id") if isinstance(anchor, dict) else None
    if not (isinstance(pid, str) and pid.strip()):
        raise CardStateError(f"source_anchor.paper_id 缺失 / 空 / 非字符串({pid!r}): {card_path}")
    return pid
