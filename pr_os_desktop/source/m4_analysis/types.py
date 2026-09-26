"""PR-OS P01/T08/M04 · M4 分析共享数据结构(T8 第 1 步立)。

承 T8 第 0 步契约 §一/§二/§三/§四:M4 拿 Context Pack + 问题 → 固定四段分析建议
(建议性、非结论)。融合铁律(文献支持 ⊥ AI 通识)+ source_id 强制 + 越界防线 + 免责语。

命门落法(结构性保证、不靠模型自觉):
- 四段固定标签 + 末尾免责语由**模块渲染**强制,不依赖模型每次记得(§二/§四)。
- 文献支持每句的 source_id 由**模块据权威块重建**(§三);模型只给 source_chunk_id、
  模块补全 paper_id/field。
- 无效/假 source_chunk_id(不在给定块内)的句子**移出文献支持段**、进 flagged_unsourced、
  绝不留在文献支持段(§二命门 + Q2 fail-closed)。
"""
from __future__ import annotations

from dataclasses import dataclass

from pr_os_source_language import normalize_source_language

# 末尾固定免责语(§四:模块强制附、不依赖模型)。
DISCLAIMER = "以上为建议，判断与撰写请自行完成。"

# 四段固定标签(§二融合铁律:两块一眼可分;模块渲染、非模型产)。
LABEL_LITERATURE = "【你的文献支持，附引用】"
LABEL_AI_GENERAL = "【AI 补充通识，未经你的库验证，请核实】"
LABEL_RISKS = "【风险与不确定性】"
LABEL_RECHECK = "【建议回查原文的位置】"
LABEL_FLAGGED = "【⚠ 疑似无源/假源：已移出文献支持段、需人工核】"
LABEL_POSSIBLY_IRRELEVANT = "【⚠ 疑似不相关：来源在库、但与本问题领域不符，需人工判断是否采用】"
LABEL_SOURCE_INCONSISTENCIES = "【来源内部不一致】"
LABEL_RETRIEVAL_LIMITATIONS = "【检索局限】"

_LOCALIZED_TEXT = {
    "zh": {
        "disclaimer": DISCLAIMER,
        "question": "问题：",
        "literature": LABEL_LITERATURE,
        "ai_general": LABEL_AI_GENERAL,
        "risks": LABEL_RISKS,
        "recheck": LABEL_RECHECK,
        "flagged": LABEL_FLAGGED,
        "irrelevant": LABEL_POSSIBLY_IRRELEVANT,
        "inconsistencies": LABEL_SOURCE_INCONSISTENCIES,
        "retrieval": LABEL_RETRIEVAL_LIMITATIONS,
        "none": "（无）",
        "no_literature": "（无文献支持内容）",
        "unlocated": "未定位",
        "source": "来源",
        "invalid_source": "模型挂的无效来源",
        "criterion": "判据",
    },
    "en": {
        "disclaimer": "These are suggestions only; final judgment and writing remain your responsibility.",
        "question": "Question:",
        "literature": "【Literature support with citations】",
        "ai_general": "【AI general knowledge not verified against your library】",
        "risks": "【Risks and uncertainties】",
        "recheck": "【Suggested source locations to recheck】",
        "flagged": "【Unresolved or invalid source claims requiring human review】",
        "irrelevant": "【Possibly irrelevant source material requiring human review】",
        "inconsistencies": "【Internal source inconsistencies】",
        "retrieval": "【Retrieval limitations】",
        "none": "(None)",
        "no_literature": "(No literature-supported content)",
        "unlocated": "unlocated",
        "source": "Source",
        "invalid_source": "Invalid source claimed by model",
        "criterion": "Reason",
    },
    "ja": {
        "disclaimer": "以上は提案です。最終的な判断と執筆は利用者自身が行ってください。",
        "question": "質問：",
        "literature": "【文献に基づく内容（引用付き）】",
        "ai_general": "【資料庫で未検証の AI 一般知識】",
        "risks": "【リスクと不確実性】",
        "recheck": "【原文で再確認する箇所】",
        "flagged": "【出典未確認または無効：人による確認が必要】",
        "irrelevant": "【関連性が低い可能性：人による確認が必要】",
        "inconsistencies": "【出典内の不一致】",
        "retrieval": "【検索上の制約】",
        "none": "（なし）",
        "no_literature": "（文献に基づく内容なし）",
        "unlocated": "位置未特定",
        "source": "出典",
        "invalid_source": "モデルが示した無効な出典",
        "criterion": "理由",
    },
}


def disclaimer_for(language: str | None) -> str:
    """Return the system disclaimer in the source language."""

    key = "zh" if language is None else normalize_source_language(language)
    return _LOCALIZED_TEXT[key]["disclaimer"]


def _localized(language: str | None) -> dict[str, str]:
    key = "zh" if language is None else normalize_source_language(language)
    return _LOCALIZED_TEXT[key]


@dataclass(frozen=True)
class LiteratureClaim:
    """文献支持段的一句:必挂 source_id(模块据权威块重建的 {chunk_id,paper_id,field})。"""
    text: str
    source_id: dict          # {"chunk_id","paper_id","field"};field 可 None
    origin: str = "unknown"
    facet_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class FlaggedClaim:
    """疑似无源/假源:模型挂的 source_chunk_id 不在给定块内(或没挂)。
    移出文献支持段、需人工核(承 Q2 fail-closed 向命门)。"""
    text: str
    claimed_chunk_id: str | None    # 模型挂的(无效)chunk_id;None=模型没挂


@dataclass(frozen=True)
class PossiblyIrrelevantClaim:
    """疑似不相关(A·修跨篇混入·M4 相关性闸兜底):source_chunk_id **能解析**(真在库、非假源),
    但内容与本问题领域/研究对象不符(跨篇/跨域)。**区别于 FlaggedClaim**(假源、解析不出):本类挂
    **模块据权威块重建的真 source_id**(可溯源),交人工判断是否采用;仅归类、绝不替用户下「不能用」结论。"""
    text: str
    source_id: dict                 # {"chunk_id","paper_id","field"}(据权威块重建;field 可 None)——能解析
    reason: str | None = None       # 模型给的「为何判为离题」(研究不同体系),供人工参考;可缺


@dataclass(frozen=True)
class AnalysisRequestEnvelope:
    """Local control-plane binding; never serialized into the provider prompt."""
    envelope_ref: str
    envelope_hash: str
    context_pack_ref: str
    context_pack_hash: str
    ownership_snapshot: dict
    ownership_binding_status: str
    required_route_class: str
    preflight_status: str
    preflight_error_codes: tuple[str, ...] = ()


def _bullets(items, none_text: str) -> list:
    return [f"- {x}" for x in items] if items else [none_text]


@dataclass(frozen=True)
class AnalysisResult:
    """M4 产物:固定四段分析建议(建议性、非结论)。四段结构恒在(各段可空)、末尾免责语恒在。"""
    question: str
    literature_support: tuple        # tuple[LiteratureClaim](每句挂重建 source_id)
    ai_general_knowledge: tuple      # tuple[str]
    risks_and_uncertainties: tuple   # tuple[str]
    recheck_locations: tuple         # tuple[str]
    flagged_unsourced: tuple         # tuple[FlaggedClaim](无效/假源、已移出文献支持段)
    disclaimer: str                  # 恒 = DISCLAIMER(模块强制)
    analysis_model: str              # 留痕:实际模型/档(承 §五;双档区分留第 2 步)
    analyzed_at: str                 # 留痕:产出时间戳(本地、到分钟)
    possibly_irrelevant: tuple = ()  # A(修跨篇混入):tuple[PossiblyIrrelevantClaim](真源但离题、单列疑似不相关);尾加默认=非破坏
    coverage_status: str = "not_assessed"
    missing_facets: tuple[str, ...] = ()
    answer_complete: bool | None = None
    study_limitations: tuple[str, ...] = ()
    source_internal_inconsistencies: tuple[str, ...] = ()
    retrieval_limitations: tuple[str, ...] = ()
    quality_metrics: dict | None = None
    coverage_assessment_ref: str | None = None
    coverage_assessment_hash: str | None = None
    # P06/T08: immutable local provenance, never accepted from model output.
    analysis_ref: str | None = None
    analysis_hash: str | None = None
    input_context_pack_ref: str | None = None
    input_context_pack_hash: str | None = None
    ownership_snapshot: dict | None = None
    ownership_binding_status: str = "legacy_missing"
    ownership_legacy_binding_status: str | None = None
    ownership_request_envelope_ref: str | None = None
    ownership_request_envelope_hash: str | None = None
    ownership_required_route_class: str | None = None
    ownership_preflight_status: str | None = None
    ownership_propagation_receipt: dict | None = None
    review_limitations: tuple[str, ...] = ()

    def render(self, *, language: str | None = None) -> str:
        """渲染人读的固定四段 + 免责语(§二两块一眼可分 + §四免责语强制)。"""
        labels = _localized(language)
        L = [f"{labels['question']} {self.question}", "", labels["literature"]]
        if self.literature_support and any(c.facet_ids for c in self.literature_support):
            grouped = {}
            for claim in self.literature_support:
                facet = claim.facet_ids[0] if claim.facet_ids else "unmapped"
                grouped.setdefault(facet, []).append(claim)
            for facet, claims in grouped.items():
                L.append(f"### {facet}")
                for i, c in enumerate(claims, 1):
                    s = c.source_id or {}
                    field = s.get("field")
                    field_disp = field if field is not None else labels["unlocated"]
                    src = f"{s.get('paper_id')} · {field_disp} · {s.get('chunk_id')}"
                    L.append(f"{i}. {c.text}  [{labels['source']}: {src} | origin={c.origin}]")
        elif self.literature_support:
            for i, c in enumerate(self.literature_support, 1):
                s = c.source_id or {}
                field = s.get("field")
                field_disp = field if field is not None else labels["unlocated"]
                src = f"{s.get('paper_id')} · {field_disp} · {s.get('chunk_id')}"
                L.append(f"{i}. {c.text}  [{labels['source']}: {src}]")
        else:
            L.append(labels["no_literature"])
        L += ["", labels["ai_general"]] + _bullets(
            self.ai_general_knowledge, labels["none"]
        )
        displayed_study_limits = (
            self.study_limitations
            if self.study_limitations
            else self.risks_and_uncertainties
        )
        L += ["", labels["risks"]] + _bullets(
            displayed_study_limits, labels["none"]
        )
        review_limits = getattr(self, "review_limitations", ()) or ()
        if review_limits:
            heading = {"zh":"【未解决的卡片审核事项】", "ja":"【未解決のカード審査事項】"}.get(language, "【Unresolved Card review qualifications】")
            L += ["", heading] + _bullets(review_limits, labels["none"])
        if self.source_internal_inconsistencies:
            L += ["", labels["inconsistencies"]] + _bullets(
                self.source_internal_inconsistencies, labels["none"]
            )
        if self.retrieval_limitations:
            L += ["", labels["retrieval"]] + _bullets(
                self.retrieval_limitations, labels["none"]
            )
        L += ["", labels["recheck"]] + _bullets(
            self.recheck_locations, labels["none"]
        )
        if self.flagged_unsourced:
            L += ["", labels["flagged"]]
            for c in self.flagged_unsourced:
                cid = c.claimed_chunk_id if c.claimed_chunk_id is not None else labels["none"]
                L.append(f"- {c.text}  [{labels['invalid_source']}: {cid}]")
        if self.possibly_irrelevant:
            L += ["", labels["irrelevant"]]
            for c in self.possibly_irrelevant:
                s = c.source_id or {}
                field = s.get("field")
                field_disp = field if field is not None else labels["unlocated"]
                src = f"{s.get('paper_id')} · {field_disp} · {s.get('chunk_id')}"
                reason = f" | {labels['criterion']}: {c.reason}" if c.reason else ""
                L.append(f"- {c.text}  [{labels['source']}: {src}{reason}]")
        L += ["", f"—— {self.disclaimer}",
              f"（analysis_model={self.analysis_model} · analyzed_at={self.analyzed_at}）"]
        return "\n".join(L)
