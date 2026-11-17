"""QualityNewRerankerTextProductionExamExecutor: the reranker text exam's production form.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import statistics
from typing import Any, Mapping, Sequence
from memorive.exams.common import _load_json, _sha256
from memorive.exams.reranker.text_executor import QualityRerankerTextExamExecutor
from memorive.exams.reranker.new_text_executor import QualityNewRerankerTextExamExecutor


class QualityNewRerankerTextProductionExamExecutor(
    QualityNewRerankerTextExamExecutor
):
    """English-production primary track plus an independent multilingual stress track.

    The frozen Quality pack is reused unchanged.  Language mode is a hard sampling
    dimension so the production score cannot be dominated by a multilingual
    diagnostic mix that does not resemble the observed Core workload.
    """

    EXECUTOR_REF = "Quality_NEW_Quality_RERANKER_TEXT_PRODUCTION_EXECUTOR_V2"
    MODE = "LIVE_Quality_NEW_RERANKER_PRODUCTION_LANGUAGE_SUCCESSOR"
    MAIN_CALLS = 22
    REPEAT_CALLS = 2
    MODEL_CALLS = MAIN_CALLS + REPEAT_CALLS
    SAMPLE_REVISION = "Quality_NEW_RERANKER_PRODUCTION_18_STRESS_4_REPEAT_2_V1"
    SCORING_PROJECTION_REVISION = (
        "Quality_NEW_RERANKER_ENGLISH_PRODUCTION_PRIMARY_MULTILINGUAL_STRESS_V1"
    )
    PRODUCTION_TRACK = "production_primary"
    ADVERSARIAL_TRACK = "multilingual_adversarial"
    FAMILIES = ("RR1", "RR2", "RR3", "RR4", "RR5", "RR6", "RR7")

    @classmethod
    def _pick_items(
        cls,
        items: Sequence[Mapping[str, Any]],
        *,
        form_id: str,
        slot: int,
        track: str,
        language_mode: str,
        family_id: str,
        count: int,
        excluded_ids: set[str],
    ) -> list[dict[str, Any]]:
        members = [
            deepcopy(dict(row))
            for row in items
            if row.get("language_mode") == language_mode
            and row.get("family_id") == family_id
            and str(row.get("item_id")) not in excluded_ids
        ]
        members.sort(
            key=lambda row: hashlib.sha256(
                (
                    f"{cls.SAMPLE_REVISION}|{form_id}|{slot}|{track}|"
                    f"{language_mode}|{family_id}|{row['item_id']}"
                ).encode("utf-8")
            ).hexdigest()
        )
        if len(members) < count:
            raise ValueError("RERANKER_LANGUAGE_TRACK_PARTITION_INVALID")
        selected = members[:count]
        for row in selected:
            row["_exam_track"] = track
            excluded_ids.add(str(row["item_id"]))
        return selected

    def _sample_manifest(self, slot: int) -> dict[str, Any]:
        forms = {
            form_id: _load_json(
                self._reference_pack_root / "forms" / f"form_{form_id}.json"
            )
            for form_id in ("A", "B")
        }
        selected: dict[str, list[dict[str, Any]]] = {}
        production: dict[str, list[dict[str, Any]]] = {}
        adversarial: dict[str, list[dict[str, Any]]] = {}
        for form_id in ("A", "B"):
            source_items = forms[form_id]["items"]
            excluded: set[str] = set()
            production_rows: list[dict[str, Any]] = []
            for family_id in self.FAMILIES:
                production_rows.extend(
                    self._pick_items(
                        source_items,
                        form_id=form_id,
                        slot=slot,
                        track=self.PRODUCTION_TRACK,
                        language_mode="en_en",
                        family_id=family_id,
                        count=1,
                        excluded_ids=excluded,
                    )
                )
            production_rows.extend(
                self._pick_items(
                    source_items,
                    form_id=form_id,
                    slot=slot,
                    track=self.PRODUCTION_TRACK,
                    language_mode="en_en",
                    family_id="RR1",
                    count=1,
                    excluded_ids=excluded,
                )
            )
            production_rows.extend(
                self._pick_items(
                    source_items,
                    form_id=form_id,
                    slot=slot,
                    track=self.PRODUCTION_TRACK,
                    language_mode="zh_en",
                    family_id="RR6",
                    count=1,
                    excluded_ids=excluded,
                )
            )
            adversarial_rows = self._pick_items(
                source_items,
                form_id=form_id,
                slot=slot,
                track=self.ADVERSARIAL_TRACK,
                language_mode="en_zh",
                family_id="RR3",
                count=1,
                excluded_ids=excluded,
            ) + self._pick_items(
                source_items,
                form_id=form_id,
                slot=slot,
                track=self.ADVERSARIAL_TRACK,
                language_mode="zh_zh",
                family_id="RR5",
                count=1,
                excluded_ids=excluded,
            )
            order = {
                str(row["item_id"]): index for index, row in enumerate(source_items)
            }
            production_rows.sort(key=lambda row: order[str(row["item_id"])])
            adversarial_rows.sort(key=lambda row: order[str(row["item_id"])])
            combined = production_rows + adversarial_rows
            combined.sort(key=lambda row: order[str(row["item_id"])])
            if (
                len(production_rows) != 9
                or len(adversarial_rows) != 2
                or len(combined) != 11
                or {str(row["family_id"]) for row in production_rows}
                != set(self.FAMILIES)
            ):
                raise ValueError("RERANKER_PRODUCTION_SAMPLE_PARTITION_INVALID")
            production[form_id] = production_rows
            adversarial[form_id] = adversarial_rows
            selected[form_id] = combined

        repeats = [
            {
                "form_id": form_id,
                "item_id": str(
                    next(
                        row
                        for row in production[form_id]
                        if row["family_id"] == "RR7"
                    )["item_id"]
                ),
            }
            for form_id in ("A", "B")
        ]
        value = {
            "schema_version": "WorkflowRerankerLanguageTrackSampleManifest-v1",
            "sample_revision": self.SAMPLE_REVISION,
            "sample_slot": slot,
            "main_items_by_form": {
                form_id: [str(row["item_id"]) for row in selected[form_id]]
                for form_id in ("A", "B")
            },
            "production_primary_items_by_form": {
                form_id: [str(row["item_id"]) for row in production[form_id]]
                for form_id in ("A", "B")
            },
            "multilingual_adversarial_items_by_form": {
                form_id: [str(row["item_id"]) for row in adversarial[form_id]]
                for form_id in ("A", "B")
            },
            "repeat_items": repeats,
            "main_call_count": self.MAIN_CALLS,
            "repeat_call_count": self.REPEAT_CALLS,
            "production_primary_main_call_count": 18,
            "multilingual_adversarial_main_call_count": 4,
            "production_primary_language_modes": {"en_en": 16, "zh_en": 2},
            "multilingual_adversarial_language_modes": {"en_zh": 2, "zh_zh": 2},
            "primary_score_track": self.PRODUCTION_TRACK,
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
        plan.pop("plan_sha256", None)
        plan.update(
            production_primary_main_calls=18,
            multilingual_adversarial_main_calls=4,
            production_primary_language_modes={"en_en": 16, "zh_en": 2},
            multilingual_adversarial_language_modes={"en_zh": 2, "zh_zh": 2},
            primary_score_track=self.PRODUCTION_TRACK,
            language_distribution_status="Core_PRODUCTION_ALIGNED_SUCCESSOR",
            multilingual_track_affects_primary_score=False,
            total_standard_model_calls_not_increased=True,
        )
        plan["plan_sha256"] = _sha256(plan)
        return plan

    @staticmethod
    def _sample_score(
        *,
        tool: Any,
        selected: Mapping[str, Sequence[Mapping[str, Any]]],
        gold_by_item: Mapping[str, Mapping[str, Any]],
        rankings: Mapping[str, Sequence[str]],
        repeats: Mapping[str, Sequence[Sequence[str]]],
    ) -> dict[str, Any]:
        production = {
            form_id: [
                row
                for row in selected[form_id]
                if row.get("_exam_track")
                == QualityNewRerankerTextProductionExamExecutor.PRODUCTION_TRACK
            ]
            for form_id in ("A", "B")
        }
        adversarial = {
            form_id: [
                row
                for row in selected[form_id]
                if row.get("_exam_track")
                == QualityNewRerankerTextProductionExamExecutor.ADVERSARIAL_TRACK
            ]
            for form_id in ("A", "B")
        }
        primary = QualityRerankerTextExamExecutor._sample_score(
            tool=tool,
            selected=production,
            gold_by_item=gold_by_item,
            rankings=rankings,
            repeats=repeats,
        )
        adversarial_scores: list[dict[str, Any]] = []
        for form_id in ("A", "B"):
            for item in adversarial[form_id]:
                item_id = str(item["item_id"])
                row = tool.item_score(
                    list(rankings[item_id]), dict(gold_by_item[item_id])
                )
                row["form_id"] = form_id
                row["language_mode"] = str(item["language_mode"])
                adversarial_scores.append(row)
        language_mode_means = {
            language_mode: statistics.fmean(
                row["raw_score"]
                for row in adversarial_scores
                if row["language_mode"] == language_mode
            )
            for language_mode in ("en_zh", "zh_zh")
        }
        adversarial_mean = statistics.fmean(
            row["raw_score"] for row in adversarial_scores
        )
        return {
            **primary,
            "schema_version": "WorkflowRerankerProductionSuccessorScore-v1",
            "score_semantics": (
                "ENGLISH_DOMINANT_PRODUCTION_PRIMARY_WITH_SEPARATE_MULTILINGUAL_STRESS"
            ),
            "primary_score_track": (
                QualityNewRerankerTextProductionExamExecutor.PRODUCTION_TRACK
            ),
            "production_primary": primary,
            "multilingual_adversarial": {
                "status": "SCORED_DESCRIPTIVE_ONLY",
                "score_exact": round(adversarial_mean, 6),
                "score_semantics": (
                    "RAW_ITEM_MEAN_DIAGNOSTIC_NOT_COMPARABLE_TO_PRODUCTION_PRIMARY"
                ),
                "item_count": len(adversarial_scores),
                "language_mode_means": {
                    key: round(value, 6)
                    for key, value in language_mode_means.items()
                },
                "item_scores": adversarial_scores,
                "quality_verdict": "NOT_ASSESSED",
                "affects_primary_score": False,
            },
        }

    def _result_extensions(
        self,
        *,
        plan: Mapping[str, Any],
        score_result: Mapping[str, Any],
    ) -> dict[str, Any]:
        adversarial = score_result["multilingual_adversarial"]
        return {
            "reason": "Quality_RERANKER_PRODUCTION_LANGUAGE_SUCCESSOR_COMPLETED",
            "primary_score_track": self.PRODUCTION_TRACK,
            "production_primary_score_exact": score_result["selection_score"],
            "production_primary_raw_score": score_result["raw_score"],
            "production_primary_main_calls": plan["production_primary_main_calls"],
            "production_primary_language_modes": plan[
                "production_primary_language_modes"
            ],
            "multilingual_adversarial_score_exact": adversarial["score_exact"],
            "multilingual_adversarial_main_calls": plan[
                "multilingual_adversarial_main_calls"
            ],
            "multilingual_adversarial_language_modes": plan[
                "multilingual_adversarial_language_modes"
            ],
            "multilingual_track_affects_primary_score": False,
            "language_distribution_status": plan["language_distribution_status"],
            "model_fail_verdict": "NOT_ESTABLISHED",
            "model_fail_established": False,
        }
