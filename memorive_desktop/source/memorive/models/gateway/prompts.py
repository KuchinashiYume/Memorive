"""MODEL_GATEWAY Prompt Registry:各模块 Prompt 模板按"模块名 + 版本号"版本化存储。

存储形态:每个版本一个 markdown 文件 —— prompts/<module>/v<N>.md,
含最小 frontmatter(module/version/created_at/note)+ 模板正文。
改版 = 写新的 v(N+1).md,旧版文件永不覆盖 → 天然可追溯(承项目"留痕"idiom)。
本步保持轻量:只做 取/改/查历史 三动作 + 占位示例;真 Prompt 内容与渲染引擎留 Configuration/Verification。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

_FM = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)   # frontmatter + 正文
_VN = re.compile(r"v(\d+)")


@dataclass(frozen=True)
class PromptTemplate:
    module: str
    version: int
    created_at: str
    note: str | None
    body: str


class PromptRegistry:
    def __init__(self, root: Path):
        self.root = Path(root)

    # ── 内部 ──────────────────────────────────────────────
    def _dir(self, module: str) -> Path:
        return self.root / module

    def _versions(self, module: str) -> list[int]:
        d = self._dir(module)
        if not d.is_dir():
            return []
        out = []
        for f in d.glob("v*.md"):
            m = _VN.fullmatch(f.stem)
            if m:
                out.append(int(m.group(1)))
        return sorted(out)

    def _read(self, module: str, version: int) -> PromptTemplate:
        text = (self._dir(module) / f"v{version}.md").read_text(encoding="utf-8")
        m = _FM.match(text)
        if m:
            meta = yaml.safe_load(m.group(1)) or {}
            body = m.group(2)
        else:
            meta, body = {}, text
        return PromptTemplate(
            module=module,
            version=version,
            created_at=str(meta.get("created_at", "")),
            note=meta.get("note"),
            body=body.strip("\n"),
        )

    # ── 三动作 ────────────────────────────────────────────
    def latest_version(self, module: str) -> int | None:
        vs = self._versions(module)
        return vs[-1] if vs else None

    def get(self, module: str, version: int | None = None) -> PromptTemplate:
        """① 取:version=None 取最新版。"""
        if version is None:
            version = self.latest_version(module)
            if version is None:
                raise KeyError(f"模块 {module!r} 无任何 Prompt 版本")
        return self._read(module, version)

    def get_or_none(self, module: str, version: int | None = None) -> PromptTemplate | None:
        try:
            return self.get(module, version)
        except (KeyError, FileNotFoundError):
            return None

    def set(self, module: str, body: str, note: str | None = None) -> PromptTemplate:
        """② 改:版本递增、写新文件,旧版不覆盖。"""
        nxt = (self.latest_version(module) or 0) + 1
        created = datetime.now().isoformat(timespec="seconds")
        d = self._dir(module)
        d.mkdir(parents=True, exist_ok=True)
        fm = {"module": module, "version": nxt, "created_at": created, "note": note}
        text = (
            "---\n"
            + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False)
            + "---\n"
            + body.rstrip("\n")
            + "\n"
        )
        (d / f"v{nxt}.md").write_text(text, encoding="utf-8")
        return PromptTemplate(module, nxt, created, note, body.strip("\n"))

    def history(self, module: str) -> list[PromptTemplate]:
        """③ 查历史:全部版本按序。"""
        return [self._read(module, v) for v in self._versions(module)]

    def modules(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())
