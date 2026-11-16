"""The paper id registry: which folder each paper_id is bound to, confirmed by a person.

document_convert_convert.py and research_recommendations' card promotion use it. The ids and names
themselves come from memorive/platform/naming.py.
"""
from __future__ import annotations

from pathlib import Path

from memorive.documents.processing.config import now_iso, root_for
from memorive.documents.processing.errors import DocumentProcessingError


class PaperIdRegistry:
    """The confirmed paper_id table.

    Kept at {root(target)}/paper_id_registry.yaml, a file a person can read and review. The sandbox
    registry goes with the sandbox; the registry of a real vault belongs under version control.
    - ensure(): a paper_id already bound to a different folder is a collision; it raises and asks
      for a person to tell them apart (an a/b suffix), never adding a suffix by import order;
    - commit(): after the files are written, binds paper_id to the folder; running the same target
      again changes nothing.
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
            raise DocumentProcessingError(
                f"paper_id 碰撞:'{paper_id}' 已绑定 {bound['folder']};新目标 {folder} 不同。"
                "请人工消歧(如加 a/b 后缀)并经 registry 确认——不按导入顺序自动定。")

    def commit(self, paper_id: str, folder: Path, *, confirmed: bool = True) -> None:
        self._data[paper_id] = {
            "folder": str(folder), "confirmed": confirmed, "bound_at": now_iso()}
        import yaml
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            yaml.safe_dump(self._data, allow_unicode=True, sort_keys=True), encoding="utf-8")
