"""EVIDENCE_REVIEW 机械层:引用 ID 校验(Verification 第 1 步)。

只校形式(chunk_id 对不对得上)、不校内容;纯规则、不调模型、零额度。
按锚点**实际形态**分派(`schema_version` 仅辅助提示、不作主依据,承 §0 拍板):
  · by_field.{f} = {section_page, chunk_ids:[str…]}(v2)→ 逐个字符串 resolve;
  · by_field.{f} = {chunk_id, quote}(v3 单对象)或 [{chunk_id, quote}…](v3 列表)
    → 逐对象取 chunk_id resolve(quote 只认存在、**不校 substring**,留之八);
  · 形态不明 / 矛盾 → malformed_anchor,**fail-closed、绝不猜**。
遍历 source_anchor.by_field 下**实际存在的所有字段** + key_data[](不写死 6 字段,防扩展漏检)。
跨篇**不靠前缀**:resolve 用「是否在本篇 chunk 库集合」判定,非 resolve 即 broken_link
(单篇一文件夹,跨篇 chunk_id 本就不在集合;detail 注「断链或跨篇」,不用前缀制造假阳性)。
系统性错误(卡不可读 / YAML 解析失败 / chunk 库不可读 / 库 paper_id 与卡不符)→ raise EvidenceReviewError;
锚点级问题(断链 / 形态不明)→ 记入 MechanicalResult.problems、**不 raise**。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from .errors import CardParseError, CardUnreadable, ChunkStoreError
from .problems import MechanicalResult, Problem

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


def _load_chunk_ids(card_path: Path) -> tuple[set, str | None]:
    """定位卡同目录唯一 [Chunks]*.jsonl,载入全部 chunk_id 集合 + 库 paper_id。
    定位 / 读取 / 解析失败 → ChunkStoreError(系统性、raise)。"""
    folder = card_path.parent
    matches = sorted(p for p in folder.glob("*.jsonl") if p.name.startswith("[Chunks]"))
    if not matches:
        raise ChunkStoreError(f"未找到 [Chunks]*.jsonl(卡同目录: {folder})")
    if len(matches) > 1:
        raise ChunkStoreError(f"卡同目录有多个 [Chunks]*.jsonl,无法确定: {[m.name for m in matches]}")
    ids: set = set()
    store_paper: str | None = None
    try:
        for line in matches[0].read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            cid = rec.get("chunk_id")
            if isinstance(cid, str) and cid:
                ids.add(cid)
            if store_paper is None:
                store_paper = rec.get("paper_id")
    except (OSError, json.JSONDecodeError) as e:
        raise ChunkStoreError(f"chunk 库不可读 / 解析失败: {matches[0]}({type(e).__name__}: {e})") from e
    return ids, store_paper


class _Checker:
    def __init__(self, chunk_ids: set):
        self.chunk_ids = chunk_ids
        self.problems: list = []
        self.checked = self.resolved = self.broken = self.malformed = 0

    def mal(self, location, field, detail, chunk_id=None):
        self.malformed += 1
        self.problems.append(Problem(source="mechanical", severity="error",
                                     kind="malformed_anchor", location=location,
                                     field=field, chunk_id=chunk_id, detail=detail))

    def resolve(self, cid, location, field):
        if not isinstance(cid, str) or not cid.strip():
            self.mal(location, field, "chunk_id 空 / 缺失 / 非字符串,无法 resolve",
                     chunk_id=cid if isinstance(cid, str) else None)
            return
        self.checked += 1
        if cid in self.chunk_ids:
            self.resolved += 1
        else:
            self.broken += 1
            self.problems.append(Problem(source="mechanical", severity="error",
                                         kind="broken_link", location=location, field=field,
                                         chunk_id=cid,
                                         detail="chunk_id 未在本篇 chunk 库中(断链或跨篇)"))

    def by_field(self, fname, anchors):
        base = f"by_field.{fname}"
        if isinstance(anchors, dict):
            cids = anchors.get("chunk_ids")
            if isinstance(cids, list):                       # v2:{section_page, chunk_ids:[str…]}
                for i, cid in enumerate(cids):
                    self.resolve(cid, f"{base}.chunk_ids[{i}]", fname)
            elif "chunk_id" in anchors:                      # v3 单对象:{chunk_id, quote}
                self.resolve(anchors.get("chunk_id"), f"{base}.chunk_id", fname)
            else:
                self.mal(base, fname, "by_field 锚点形态不明(dict 既无 chunk_ids 列表也无 chunk_id),fail-closed")
        elif isinstance(anchors, list):                      # v3 列表:[{chunk_id, quote}…]
            for i, el in enumerate(anchors):
                if isinstance(el, dict) and "chunk_id" in el:
                    self.resolve(el.get("chunk_id"), f"{base}[{i}].chunk_id", fname)
                else:
                    self.mal(f"{base}[{i}]", fname, "v3 锚点元素非 {chunk_id,…} 对象,fail-closed、不猜")
        else:
            self.mal(base, fname, f"by_field 锚点形态不明({type(anchors).__name__}),fail-closed、不猜")

    def result(self) -> MechanicalResult:
        return MechanicalResult(ok=not self.problems, problems=self.problems,
                                checked=self.checked, resolved=self.resolved,
                                broken=self.broken, malformed=self.malformed)


def mechanical_check(card_path) -> MechanicalResult:
    """校一张卡的所有 chunk_id 引用是否 resolve。返回 MechanicalResult;
    系统性错误 raise EvidenceReviewError(CardUnreadable / CardParseError / ChunkStoreError)。"""
    card_path = Path(card_path)
    fm = _read_frontmatter(card_path)                        # raise on 系统性
    chunk_ids, store_paper = _load_chunk_ids(card_path)      # raise on 系统性

    sa = fm.get("source_anchor")
    card_paper = sa.get("paper_id") if isinstance(sa, dict) else None
    if store_paper and card_paper and store_paper != card_paper:   # ③ 元数据比对,防加载错库致大批假阳性
        raise ChunkStoreError(
            f"chunk 库 paper_id={store_paper!r} 与卡 source_anchor.paper_id={card_paper!r} 不符(疑加载错库)")

    ck = _Checker(chunk_ids)
    by_field = sa.get("by_field") if isinstance(sa, dict) else None
    if isinstance(by_field, dict):
        for fname, anchors in by_field.items():              # ① 遍历实际存在的所有字段,不写死 6 个
            ck.by_field(fname, anchors)
    else:
        ck.mal("source_anchor.by_field", None, "source_anchor.by_field 缺失或非映射,无法校锚点")

    kd = fm.get("key_data")
    if isinstance(kd, list):
        for i, entry in enumerate(kd):
            cid = entry.get("chunk_id") if isinstance(entry, dict) else None
            ck.resolve(cid, f"key_data[{i}].chunk_id", "key_data")

    return ck.result()
