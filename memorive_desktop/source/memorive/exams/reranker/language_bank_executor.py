"""QualityNewRerankerTextLanguageBankExamExecutor: the reranker text exam's language bank form.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import statistics
from typing import Any, Mapping, Sequence
from memorive.exams.retrieval_calibration import RERANKER_TWO_SLOT_POLICY
from memorive.exams.common import _load_json, _not_available, _sha256
from memorive.exams.reranker.text_executor import QualityRerankerTextExamExecutor
from memorive.exams.reranker.production_executor import QualityNewRerankerTextProductionExamExecutor


class QualityNewRerankerTextLanguageBankExamExecutor(
    QualityNewRerankerTextProductionExamExecutor
):
    """One slot of a two-slot role-adequacy series.

    Each 24-call run remains unassessed on its own.  Only an aggregate of the
    two frozen, disjoint slots may issue the current-configuration quality
    verdict; even that aggregate cannot establish a model-level failure.
    """

    EXECUTOR_REF = "Quality_NEW_Quality_RERANKER_TEXT_LANGUAGE_BANK_EXECUTOR_V3"
    MODE = "LIVE_Quality_NEW_RERANKER_LANGUAGE_BANK_SUCCESSOR"
    MAIN_CALLS = 22
    REPEAT_CALLS = 2
    MODEL_CALLS = 24
    # This successor is governed by the authorized physical-request ceiling.
    # Do not invent an additional monetary admission gate when none was given.
    API_BUDGET_CAP_CNY = None
    SAMPLE_REVISION = "Quality_NEW_RERANKER_EN18_ZH2_JA2_REPEAT2_V2"
    SCORING_PROJECTION_REVISION = (
        "Quality_NEW_RERANKER_RAW_ENGLISH_PRIMARY_INDEPENDENT_LANGUAGE_TRACKS_V2"
    )
    ENGLISH_TRACK = "english_production_primary"
    CHINESE_TRACK = "chinese_diagnostic"
    JAPANESE_TRACK = "japanese_diagnostic"

    @classmethod
    def _language_pick(
        cls,
        items: Sequence[Mapping[str, Any]],
        *,
        form_id: str,
        slot: int,
        language_mode: str,
        family_id: str,
        count: int,
        track: str,
        excluded: set[str],
    ) -> list[dict[str, Any]]:
        members = [
            deepcopy(dict(row))
            for row in items
            if row.get("language_mode") == language_mode
            and row.get("family_id") == family_id
            and str(row.get("item_id")) not in excluded
        ]
        members.sort(
            key=lambda row: hashlib.sha256(
                (
                    f"{cls.SAMPLE_REVISION}|{form_id}|{slot}|{language_mode}|"
                    f"{family_id}|{row['item_id']}"
                ).encode("utf-8")
            ).hexdigest()
        )
        if len(members) < count:
            raise ValueError("RERANKER_LANGUAGE_BANK_PARTITION_INVALID")
        selected = members[:count]
        for row in selected:
            row["_exam_track"] = track
            excluded.add(str(row["item_id"]))
        return selected

    def _sample_manifest(self, slot: int) -> dict[str, Any]:
        forms = {
            form_id: _load_json(
                self._reference_pack_root / "forms" / f"form_{form_id}.json"
            )
            for form_id in ("A", "B")
        }
        selected: dict[str, list[dict[str, Any]]] = {}
        english: dict[str, list[dict[str, Any]]] = {}
        chinese: dict[str, list[dict[str, Any]]] = {}
        japanese: dict[str, list[dict[str, Any]]] = {}
        for form_offset, form_id in enumerate(("A", "B")):
            source_items = forms[form_id]["items"]
            excluded: set[str] = set()
            english_rows: list[dict[str, Any]] = []
            for family_id in self.FAMILIES:
                english_rows.extend(
                    self._language_pick(
                        source_items,
                        form_id=form_id,
                        slot=slot,
                        language_mode="en_en",
                        family_id=family_id,
                        count=1,
                        track=self.ENGLISH_TRACK,
                        excluded=excluded,
                    )
                )
            for family_id in ("RR1", "RR6"):
                english_rows.extend(
                    self._language_pick(
                        source_items,
                        form_id=form_id,
                        slot=slot,
                        language_mode="en_en",
                        family_id=family_id,
                        count=1,
                        track=self.ENGLISH_TRACK,
                        excluded=excluded,
                    )
                )
            chinese_family = self.FAMILIES[(slot - 1 + form_offset) % 7]
            japanese_family = self.FAMILIES[(slot + 2 + form_offset) % 7]
            chinese_rows = self._language_pick(
                source_items,
                form_id=form_id,
                slot=slot,
                language_mode="zh_zh",
                family_id=chinese_family,
                count=1,
                track=self.CHINESE_TRACK,
                excluded=excluded,
            )
            japanese_rows = self._language_pick(
                source_items,
                form_id=form_id,
                slot=slot,
                language_mode="ja_ja",
                family_id=japanese_family,
                count=1,
                track=self.JAPANESE_TRACK,
                excluded=excluded,
            )
            order = {
                str(row["item_id"]): index
                for index, row in enumerate(source_items)
            }
            english_rows.sort(key=lambda row: order[str(row["item_id"])])
            combined = english_rows + chinese_rows + japanese_rows
            combined.sort(key=lambda row: order[str(row["item_id"])])
            if (
                len(english_rows) != 9
                or len(chinese_rows) != 1
                or len(japanese_rows) != 1
                or len(combined) != 11
                or {str(row["family_id"]) for row in english_rows}
                != set(self.FAMILIES)
            ):
                raise ValueError("RERANKER_LANGUAGE_BANK_SAMPLE_INVALID")
            english[form_id] = english_rows
            chinese[form_id] = chinese_rows
            japanese[form_id] = japanese_rows
            selected[form_id] = combined
        repeats = [
            {
                "form_id": form_id,
                "item_id": str(
                    next(
                        row
                        for row in english[form_id]
                        if row["family_id"] == "RR7"
                    )["item_id"]
                ),
            }
            for form_id in ("A", "B")
        ]
        value = {
            "schema_version": "WorkflowRerankerLanguageBankSampleManifest-v2",
            "sample_revision": self.SAMPLE_REVISION,
            "sample_slot": slot,
            "main_items_by_form": {
                form_id: [str(row["item_id"]) for row in selected[form_id]]
                for form_id in ("A", "B")
            },
            "english_primary_items_by_form": {
                form_id: [str(row["item_id"]) for row in english[form_id]]
                for form_id in ("A", "B")
            },
            "chinese_diagnostic_items_by_form": {
                form_id: [str(row["item_id"]) for row in chinese[form_id]]
                for form_id in ("A", "B")
            },
            "japanese_diagnostic_items_by_form": {
                form_id: [str(row["item_id"]) for row in japanese[form_id]]
                for form_id in ("A", "B")
            },
            "repeat_items": repeats,
            "main_call_count": self.MAIN_CALLS,
            "repeat_call_count": self.REPEAT_CALLS,
            "language_mode_counts": {"en_en": 18, "zh_zh": 2, "ja_ja": 2},
            "primary_score_track": self.ENGLISH_TRACK,
            "cross_language_aggregation_forbidden": True,
            "provider_received_gold": False,
        }
        value["sample_manifest_sha256"] = _sha256(value)
        value["_selected"] = selected
        return value

    def plan(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        plan = super().plan(node, target, context)
        if plan.get("status") != "READY":
            return plan
        if plan.get("sample_slot") not in {1, 2}:
            return _not_available(
                "WORKFLOW_MODEL_EXAM_RERANKER_TWO_SLOT_SERIES_REQUIRES_SLOT_1_OR_2"
            )
        plan.pop("plan_sha256", None)
        plan.update(
            reference_pack_id="Quality-NEW-MODEL_EVALUATION-RERANKER-LANGUAGE-BANK-172-V2",
            scoring_protocol_revision="reranker_text_language_tracks_r2.0",
            production_primary_main_calls=18,
            chinese_diagnostic_main_calls=2,
            japanese_diagnostic_main_calls=2,
            production_primary_language_modes={"en_en": 18},
            chinese_diagnostic_language_modes={"zh_zh": 2},
            japanese_diagnostic_language_modes={"ja_ja": 2},
            primary_score_track=self.ENGLISH_TRACK,
            primary_score_scale="RAW_0_TO_100",
            legacy_compressed_score_is_diagnostic_only=True,
            language_distribution_status="Core_ENGLISH_PRIMARY_ALIGNED_V2",
            multilingual_track_affects_primary_score=False,
            cross_language_aggregation_forbidden=True,
            total_standard_model_calls_not_increased=True,
            quality_threshold_defined=True,
            single_slot_quality_verdict_available=False,
            series_assessment_pending=True,
            role_adequacy_policy=deepcopy(RERANKER_TWO_SLOT_POLICY),
            required_distinct_sample_slots=2,
            aggregate_quality_fail_may_establish_model_fail=False,
        )
        plan["plan_sha256"] = _sha256(plan)
        return plan

    @staticmethod
    def _diagnostic_track_score(
        *,
        tool: Any,
        rows: Sequence[Mapping[str, Any]],
        form_id: str,
        gold_by_item: Mapping[str, Mapping[str, Any]],
        rankings: Mapping[str, Sequence[str]],
    ) -> list[dict[str, Any]]:
        scores: list[dict[str, Any]] = []
        for item in rows:
            item_id = str(item["item_id"])
            score = tool.item_score(
                list(rankings[item_id]), dict(gold_by_item[item_id])
            )
            score["form_id"] = form_id
            score["language_mode"] = str(item["language_mode"])
            scores.append(score)
        return scores

    @staticmethod
    def _sample_score(
        *,
        tool: Any,
        selected: Mapping[str, Sequence[Mapping[str, Any]]],
        gold_by_item: Mapping[str, Mapping[str, Any]],
        rankings: Mapping[str, Sequence[str]],
        repeats: Mapping[str, Sequence[Sequence[str]]],
    ) -> dict[str, Any]:
        english = {
            form_id: [
                row
                for row in selected[form_id]
                if row.get("_exam_track")
                == QualityNewRerankerTextLanguageBankExamExecutor.ENGLISH_TRACK
            ]
            for form_id in ("A", "B")
        }
        primary = QualityRerankerTextExamExecutor._sample_score(
            tool=tool,
            selected=english,
            gold_by_item=gold_by_item,
            rankings=rankings,
            repeats=repeats,
        )
        diagnostics: dict[str, Any] = {}
        for track, language_mode in (
            (
                QualityNewRerankerTextLanguageBankExamExecutor.CHINESE_TRACK,
                "zh_zh",
            ),
            (
                QualityNewRerankerTextLanguageBankExamExecutor.JAPANESE_TRACK,
                "ja_ja",
            ),
        ):
            item_scores: list[dict[str, Any]] = []
            for form_id in ("A", "B"):
                rows = [
                    row
                    for row in selected[form_id]
                    if row.get("_exam_track") == track
                ]
                item_scores.extend(
                    QualityNewRerankerTextLanguageBankExamExecutor._diagnostic_track_score(
                        tool=tool,
                        rows=rows,
                        form_id=form_id,
                        gold_by_item=gold_by_item,
                        rankings=rankings,
                    )
                )
            diagnostics[language_mode] = {
                "status": "SCORED_DESCRIPTIVE_ONLY",
                "raw_score": round(
                    statistics.fmean(row["raw_score"] for row in item_scores),
                    6,
                ),
                "item_count": len(item_scores),
                "item_scores": item_scores,
                "quality_verdict": "NOT_ASSESSED",
                "affects_primary_score": False,
            }
        raw_primary = float(primary["raw_score"])
        projected_forms = deepcopy(primary["forms"])
        for form_id in ("A", "B"):
            projected_forms[form_id]["legacy_compressed_display_score"] = (
                projected_forms[form_id]["selection_score"]
            )
            projected_forms[form_id]["selection_score"] = projected_forms[
                form_id
            ]["raw_score"]
        return {
            "schema_version": "WorkflowRerankerLanguageBankScore-v2",
            "status": "SCORED",
            "raw_score": round(raw_primary, 6),
            "selection_score": round(raw_primary, 6),
            "forms": projected_forms,
            "item_scores": primary["item_scores"],
            "score_semantics": "RAW_ENGLISH_PRODUCTION_PRIMARY_0_TO_100",
            "primary_score_track": (
                QualityNewRerankerTextLanguageBankExamExecutor.ENGLISH_TRACK
            ),
            "production_primary": primary,
            "legacy_compressed_display_score": primary["selection_score"],
            "language_diagnostics": diagnostics,
            "cross_language_aggregation_forbidden": True,
            "full_quality_score_comparison_eligible": False,
        }

    def _result_extensions(
        self,
        *,
        plan: Mapping[str, Any],
        score_result: Mapping[str, Any],
    ) -> dict[str, Any]:
        diagnostics = score_result["language_diagnostics"]
        return {
            "reason": "Quality_RERANKER_LANGUAGE_BANK_SUCCESSOR_COMPLETED",
            "primary_score_track": self.ENGLISH_TRACK,
            "production_primary_score_exact": score_result["raw_score"],
            "production_primary_raw_score": score_result["raw_score"],
            "legacy_compressed_display_score": score_result[
                "legacy_compressed_display_score"
            ],
            "production_primary_main_calls": plan[
                "production_primary_main_calls"
            ],
            "production_primary_language_modes": {"en_en": 18},
            "chinese_diagnostic_score_exact": diagnostics["zh_zh"][
                "raw_score"
            ],
            "japanese_diagnostic_score_exact": diagnostics["ja_ja"][
                "raw_score"
            ],
            "chinese_diagnostic_main_calls": 2,
            "japanese_diagnostic_main_calls": 2,
            "multilingual_track_affects_primary_score": False,
            "cross_language_aggregation_forbidden": True,
            "language_distribution_status": plan["language_distribution_status"],
            "model_fail_verdict": "NOT_ESTABLISHED",
            "model_fail_established": False,
            "quality_threshold_defined": True,
            "quality_verdict": "NOT_ASSESSED",
            "score_is_descriptive_within_category": True,
            "single_slot_quality_verdict_available": False,
            "series_assessment_pending": True,
            "role_adequacy_policy_revision": RERANKER_TWO_SLOT_POLICY[
                "revision"
            ],
            "required_distinct_sample_slots": 2,
            "aggregate_quality_fail_may_establish_model_fail": False,
        }
