"""The analysis records: from a context pack and a question, suggestions in four fixed sections
(suggestions, not conclusions). Literature support and AI general knowledge are kept apart, every
literature sentence names its source, and a disclaimer ends the analysis. Job folders pickle
AnalysisResult (memorive/platform/pickle_compat.py reads old ones).

These hold by construction, not by the model remembering them:
- the four section labels and the closing disclaimer are rendered by this module;
- each literature sentence's source_id is rebuilt by the module from the given blocks; the model
  gives only source_chunk_id, the module adds paper_id and field;
- a sentence whose source_chunk_id is not among the given blocks is moved out of the literature
  section into flagged_unsourced.
"""
from __future__ import annotations

from dataclasses import dataclass

from memorive.platform.language.source_preservation import normalize_source_language

# The closing disclaimer, added by this module, not by the model.
DISCLAIMER = "以上为建议，判断与撰写请自行完成。"

# The four section labels, rendered by this module so literature and AI knowledge are clearly apart.
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
    return _LOCALIZED_TEXT.get(key, _LOCALIZED_TEXT["en"])["disclaimer"]


def _localized(language: str | None) -> dict[str, str]:
    key = "zh" if language is None else normalize_source_language(language)
    return _LOCALIZED_TEXT.get(key, _LOCALIZED_TEXT["en"])


@dataclass(frozen=True)
class LiteratureClaim:
    """A sentence of the literature section, with its source_id ({chunk_id, paper_id, field}, rebuilt
    by the module from the given blocks)."""
    text: str
    source_id: dict          # {"chunk_id", "paper_id", "field"}; field may be None
    origin: str = "unknown"
    facet_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class FlaggedClaim:
    """A sentence whose source_chunk_id is missing or not among the given blocks. It is moved out of
    the literature section for a person to check."""
    text: str
    claimed_chunk_id: str | None    # the chunk_id the model gave; None if none


@dataclass(frozen=True)
class PossiblyIrrelevantClaim:
    """A sentence that may be off topic: its source_chunk_id resolves to a real block, but the content
    is about another field or system than the question. Unlike FlaggedClaim (a source that does not
    resolve), it carries the real source_id; a person decides whether to use it. It is only sorted
    apart, never declared unusable."""
    text: str
    source_id: dict                 # {"chunk_id", "paper_id", "field"}, rebuilt; field may be None
    reason: str | None = None       # the model's reason it is off topic, for the person; optional


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
    """An analysis: suggestions in four fixed sections (each may be empty) and the closing disclaimer."""
    question: str
    literature_support: tuple        # tuple[LiteratureClaim], each with its rebuilt source_id
    ai_general_knowledge: tuple      # tuple[str]
    risks_and_uncertainties: tuple   # tuple[str]
    recheck_locations: tuple         # tuple[str]
    flagged_unsourced: tuple         # tuple[FlaggedClaim], moved out of the literature section
    disclaimer: str                  # always DISCLAIMER
    analysis_model: str              # the model that answered
    analyzed_at: str                 # when, local time to the minute
    possibly_irrelevant: tuple = ()  # tuple[PossiblyIrrelevantClaim]; a default, so older pickles still load
    coverage_status: str = "not_assessed"
    missing_facets: tuple[str, ...] = ()
    answer_complete: bool | None = None
    study_limitations: tuple[str, ...] = ()
    source_internal_inconsistencies: tuple[str, ...] = ()
    retrieval_limitations: tuple[str, ...] = ()
    quality_metrics: dict | None = None
    coverage_assessment_ref: str | None = None
    coverage_assessment_hash: str | None = None
    # DATA-OWNERSHIP: immutable local provenance, never accepted from model output.
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
        """The analysis as people read it: the four sections, literature and AI knowledge clearly apart,
        and the disclaimer."""
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
