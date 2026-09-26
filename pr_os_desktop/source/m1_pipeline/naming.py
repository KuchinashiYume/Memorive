"""命名规范落地(承 D2《文献库命名规范》+ 第 0 步契约 §一)+ paper_id 人工确认表。

五段共用:非法字符处理 / 标题截断 / paper_id / 文件夹名 / 文件名。
PaperIdRegistry:碰撞消歧走人工确认、确认后稳定绑定、重跑不变(承确认 4,不按导入顺序自动定)。
"""
from __future__ import annotations

import re
from pathlib import Path

from .config import now_iso, root_for
from .errors import M1Error

# Windows 禁止的九个字符里: `:`→' -' 、`/`→'_' ,其余(\ * ? " < > |)删除
_ILLEGAL_REMOVE = set('\\*?"<>|')
TITLE_MAX = 60


def sanitize_title(title: str) -> str:
    """非法字符处理(承 D2):冒号→空格连字符、斜杠→下划线、其余非法字符删除、折叠空白。"""
    title = title.replace(":", " -").replace("/", "_")
    title = "".join(c for c in title if c not in _ILLEGAL_REMOVE)
    return re.sub(r"\s+", " ", title).strip()


def truncate_title(title: str, limit: int = TITLE_MAX) -> str:
    """超长按词边界截 ~limit 字符 + '…'(完整标题另存卡片 title 字段,不丢)。"""
    if len(title) <= limit:
        return title
    cut = title[:limit].rsplit(" ", 1)[0].rstrip()
    return (cut or title[:limit].rstrip()) + "…"


def display_title(title: str) -> str:
    """落文件夹/文件名用的标题:先净化非法字符,再截断。"""
    return truncate_title(sanitize_title(title))


def make_paper_id(surname: str, year: str | int, suffix: str = "") -> str:
    """paper_id = 姓氏 + 年份(+ 碰撞后缀);不带方括号。姓氏罗马化/拼音判定人工在上游定。"""
    return f"{surname}{year}{suffix}"


def folder_name(paper_id: str, display: str) -> str:
    """文件夹名:[paper_id] 标题(display 已净化+截断)。"""
    return f"[{paper_id}] {display}"


def file_name(kind: str, paper_id: str, display: str, ext: str) -> str:
    """文件名:[类型][paper_id] 标题.ext(三段式,承 D2)。"""
    return f"[{kind}][{paper_id}] {display}.{ext}"


class PaperIdRegistry:
    """paper_id 人工确认表(承确认 4)。

    落位:{root(target)}/paper_id_registry.yaml —— 持久、可人工审阅(.yaml)。
    sandbox 的 registry 随沙盒可整体删;正式 vault 的 registry 应纳入版本控制。
    语义:
      - ensure():同 paper_id 已绑到「不同」文件夹 → 碰撞,抛错要求人工消歧(a/b),
                 绝不按导入顺序自动加后缀;
      - commit():落盘成功后确认绑定(paper_id→folder),重跑同目标即幂等、编号不变。
    """

    def __init__(self, target: str = "sandbox", path: Path | None = None):
        self.path = Path(path) if path else (root_for(target) / "paper_id_registry.yaml")
        self._data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            import yaml
            return yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        return {}

    def ensure(self, paper_id: str, folder: Path) -> None:
        bound = self._data.get(paper_id)
        if bound and Path(bound.get("folder", "")) != Path(folder):
            raise M1Error(
                f"paper_id 碰撞:'{paper_id}' 已绑定 {bound['folder']};新目标 {folder} 不同。"
                "请人工消歧(如加 a/b 后缀)并经 registry 确认——不按导入顺序自动定。")

    def commit(self, paper_id: str, folder: Path, *, confirmed: bool = True) -> None:
        self._data[paper_id] = {
            "folder": str(folder), "confirmed": confirmed, "bound_at": now_iso()}
        import yaml
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            yaml.safe_dump(self._data, allow_unicode=True, sort_keys=True), encoding="utf-8")
