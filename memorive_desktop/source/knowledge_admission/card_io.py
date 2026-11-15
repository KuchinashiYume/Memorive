"""A card's review state is the review_status field of its front matter; there is no separate store.

Reading parses the front matter. Writing changes only the value on the review_status line: every
other byte of the file stays as it was, line endings included. The new text goes to a temporary
file first, is read back, and only then replaces the card.

The front matter runs from a "---" line at the very start of the file to the next line that is
only "---" (trailing spaces and a CR allowed), so a value such as "title: A---B" does not end it.
Cards are written by yaml.dump, so review_status is a plain unquoted value; any other form is
refused rather than rewritten.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import yaml

from .errors import CardStateError
from .states import STATES

_FENCE = re.compile(r"(?m)^---[ \t]*\r?$")
# Group 3 keeps the end of the line, so a CRLF card stays CRLF.
_STATUS_LINE = re.compile(r"^(review_status:[ \t]*)([A-Za-z_]+)([ \t]*\r?)$", re.MULTILINE)


def _read_text(card_path: Path) -> str:
    """The card's text with its line endings as they are on disk."""
    return card_path.read_bytes().decode("utf-8")


def _split_frontmatter(card_path: Path, text: str) -> tuple[str, str, str]:
    """(opening fence, front matter, the rest), which joined give the text back byte for byte."""
    fences = list(_FENCE.finditer(text))
    if len(fences) < 2 or fences[0].start() != 0:
        raise CardStateError(f"卡片 frontmatter 非法(需以独占一行 --- 开头、且有闭合 ---): {card_path}")
    f1, f2 = fences[0], fences[1]
    return text[:f1.end()], text[f1.end():f2.start()], text[f2.start():]


def _front_matter(card_path: Path) -> dict:
    _pre, front, _body = _split_frontmatter(card_path, _read_text(card_path))
    fm = yaml.safe_load(front)
    if not isinstance(fm, dict):
        raise CardStateError(f"卡片 frontmatter 解析非 dict: {card_path}")
    return fm


def read_status(card_path: Path) -> str:
    """The card's review_status. CardStateError when it is missing or not a review state."""
    status = _front_matter(card_path).get("review_status")
    if status not in STATES:
        raise CardStateError(f"review_status 缺失/不在三态枚举({status!r}): {card_path}")
    return status


def write_status(card_path: Path, new_status: str) -> None:
    """Set review_status to new_status, changing nothing else in the file.

    The card needs exactly one plain review_status line. The new text is read back from the
    temporary file before it replaces the card; that refuses a new_status that is not a review
    state, and a card whose front matter has another, quoted review_status that would win."""
    pre, front, body = _split_frontmatter(card_path, _read_text(card_path))
    matches = list(_STATUS_LINE.finditer(front))
    if len(matches) != 1:
        raise CardStateError(
            f"frontmatter 内无引号 review_status 行非唯一(找到 {len(matches)} 行): {card_path}")
    new_text = pre + _STATUS_LINE.sub(rf"\g<1>{new_status}\g<3>", front, count=1) + body
    fd, tmp = tempfile.mkstemp(dir=card_path.parent, prefix=".knowledge_admission_tmp_", suffix=".m8tmp")
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(new_text.encode("utf-8"))
        if read_status(tmp_path) != new_status:
            raise CardStateError(f"临时文件复核不符(期望 {new_status}): {card_path}")
        os.replace(tmp_path, card_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def read_data_id(card_path: Path) -> str:
    """The card's source_anchor.paper_id. There is no fallback to the file name: a card without
    one raises CardStateError."""
    anchor = _front_matter(card_path).get("source_anchor")
    pid = anchor.get("paper_id") if isinstance(anchor, dict) else None
    if not (isinstance(pid, str) and pid.strip()):
        raise CardStateError(f"source_anchor.paper_id 缺失 / 空 / 非字符串({pid!r}): {card_path}")
    return pid
