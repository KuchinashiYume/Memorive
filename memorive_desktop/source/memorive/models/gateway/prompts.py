"""The prompt registry: each module's prompt templates, stored by module name and version.

Each version is one Markdown file, prompts/<module>/v<N>.md, with a small front matter (module,
version, created_at, note) and the template. A change is a new v(N+1).md; an old version is never
overwritten, so every prompt sent can be traced. Three operations: get, add a version, list the history.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

_FM = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)   # front matter, then the template
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

    # ── internal ──────────────────────────────────────────
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

    # ── the three operations ──────────────────────────────
    def latest_version(self, module: str) -> int | None:
        vs = self._versions(module)
        return vs[-1] if vs else None

    def get(self, module: str, version: int | None = None) -> PromptTemplate:
        """Get a template; version=None gets the latest."""
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
        """Add a version: the next number, a new file; older versions stay as they are."""
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
        """All versions in order."""
        return [self._read(module, v) for v in self._versions(module)]

    def modules(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())
