"""QualityNewCardDistillerLanguageBankExamExecutor: the card distiller exam's language bank form.
"""
from __future__ import annotations

from copy import deepcopy
import itertools
import math
import statistics
from typing import Any, Mapping, Sequence
from memorive.exams.common import EXECUTOR_REF, _load_json, _sha256
from memorive.exams.card_distiller.policy import PASSING_SCORE
from memorive.exams.card_distiller.direct_executor import QualityCardDistillerDirectExamExecutor


class QualityNewCardDistillerLanguageBankExamExecutor(
    QualityCardDistillerDirectExamExecutor
):
    """Quality_new Card exam over a 100/36/36 language bank."""

    EXECUTOR_REF = (
        "Desktop_MODEL_EXAM_SUCCESSOR_Quality_CARD_DISTILLER_LANGUAGE_BANK_"
        "EXECUTOR_V7"
    )
    MODE = "LIVE_Quality_CARD_DISTILLER_LANGUAGE_BANK_SUCCESSOR"
    API_REQUEST_CAP = 25
    CLI_REQUEST_CAP = 25
    LOCAL_REQUEST_CAP = 25
    SAMPLE_ROTATION_SLOTS = 5
    SAMPLE_REVISION = "CARD_DISTILLER_EN36_ZH6_JA6_DUAL_FORM_V2"

    @staticmethod
    def _prune_pack(
        *,
        pack: Mapping[str, Any],
        gold_pack: Mapping[str, Any],
        selected_case_ids: set[str],
        case_chunk_by_id: Mapping[str, str],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        projected_pack = deepcopy(dict(pack))
        projected_gold = deepcopy(dict(gold_pack))
        all_case_ids = [str(value) for value in pack.get("case_ids", [])]
        kept_case_ids = [
            case_id for case_id in all_case_ids if case_id in selected_case_ids
        ]
        all_case_chunks = {
            case_chunk_by_id[case_id]
            for case_id in all_case_ids
            if case_id in case_chunk_by_id
        }
        selected_chunks = {
            case_chunk_by_id[case_id]
            for case_id in kept_case_ids
            if case_id in case_chunk_by_id
        }
        projected_pack["case_ids"] = kept_case_ids
        projected_pack["chunks"] = [
            deepcopy(row)
            for row in pack.get("chunks", [])
            if str(row.get("chunk_id")) not in all_case_chunks
            or str(row.get("chunk_id")) in selected_chunks
        ]
        projected_gold["case_ids"] = kept_case_ids
        projection = projected_gold["expected_projection"]
        projection["key_data_case_ids"] = [
            value
            for value in projection["key_data_case_ids"]
            if value in selected_case_ids
        ]
        for field in (
            "research_question",
            "research_object",
            "method",
            "key_results",
            "author_conclusion",
            "boundary_conditions",
        ):
            projection[field]["case_ids"] = [
                value
                for value in projection[field]["case_ids"]
                if value in selected_case_ids
            ]
            projection[field]["anchor_ids"] = [
                value
                for value in projection[field]["anchor_ids"]
                if value not in all_case_chunks or value in selected_chunks
            ]
        return projected_pack, projected_gold

    @classmethod
    def _best_case_subset(
        cls,
        *,
        rows: Sequence[Mapping[str, Any]],
        gold_by_id: Mapping[str, Mapping[str, Any]],
        count: int,
        language: str,
        form_id: str,
        slot: int,
    ) -> list[str]:
        case_ids = [str(row["case_id"]) for row in rows]
        candidates: list[tuple[tuple[Any, ...], list[str]]] = []
        for combination in itertools.combinations(case_ids, count):
            gold_rows = [gold_by_id[case_id] for case_id in combination]
            difficulties = {
                value: sum(row.get("difficulty") == value for row in gold_rows)
                for value in ("easy", "medium", "hard")
            }
            families = {str(row.get("family")) for row in gold_rows}
            if language in {"zh", "ja"}:
                if difficulties != {"easy": 1, "medium": 1, "hard": 1}:
                    continue
                if len(families) != count:
                    continue
            elif families != {
                "D1_NUMERIC_UNIT_COMPARATOR",
                "D2_TIME_SAMPLE_SCOPE",
                "D3_MULTI_OCCURRENCE",
                "D4_MISSING_STATUS",
                "D5_SOURCE_AS_PRINTED",
                "D6_CLEAN_DIRECT_EXTRACTION",
            }:
                continue
            imbalance = max(difficulties.values()) - min(difficulties.values())
            minimum = min(difficulties.values())
            digest = _sha256(
                {
                    "revision": cls.SAMPLE_REVISION,
                    "form_id": form_id,
                    "slot": slot,
                    "language": language,
                    "case_ids": combination,
                }
            )
            candidates.append(((imbalance, -minimum, digest), list(combination)))
        if not candidates:
            raise ValueError("CARD_DISTILLER_LANGUAGE_SAMPLE_PARTITION_INVALID")
        candidates.sort(key=lambda row: row[0])
        return candidates[0][1]

    @classmethod
    def _exam_inputs(
        cls,
        *,
        pack: Any,
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        sample_slot = cls._sample_slot(context)
        forms: dict[str, dict[str, Any]] = {}
        golds: dict[str, dict[str, Any]] = {}
        selection: dict[str, Any] = {}
        for form_id in ("A", "B"):
            source_form = _load_json(pack.member(f"forms/form_{form_id}.json"))
            source_gold = _load_json(
                pack.member(f"gold/form_{form_id}_gold.json")
            )
            gold_by_id = {
                str(row["case_id"]): row for row in source_gold["cases"]
            }
            form_case_by_id = {
                str(row["case_id"]): row for row in source_form["cases"]
            }
            case_chunk_by_id = {
                case_id: str(row["source_unit"]["chunk_id"])
                for case_id, row in form_case_by_id.items()
            }
            packs_by_language = {
                language: [
                    row
                    for row in source_form["packs"]
                    if row.get("source_language") == language
                ]
                for language in ("en", "zh", "ja")
            }
            gold_pack_by_id = {
                str(row["pack_id"]): row for row in source_gold["packs"]
            }
            english_packs = packs_by_language["en"]
            english_pair = [
                english_packs[(sample_slot - 1) % len(english_packs)],
                english_packs[sample_slot % len(english_packs)],
            ]
            english_rows = [
                form_case_by_id[str(case_id)]
                for pack_row in english_pair
                for case_id in pack_row["case_ids"]
            ]
            english_ids = cls._best_case_subset(
                rows=english_rows,
                gold_by_id=gold_by_id,
                count=18,
                language="en",
                form_id=form_id,
                slot=sample_slot,
            )
            zh_pack = packs_by_language["zh"][(sample_slot - 1) % 2]
            ja_pack = packs_by_language["ja"][
                (sample_slot - 1 + (0 if form_id == "A" else 1)) % 2
            ]
            zh_rows = [
                form_case_by_id[str(case_id)] for case_id in zh_pack["case_ids"]
            ]
            ja_rows = [
                form_case_by_id[str(case_id)] for case_id in ja_pack["case_ids"]
            ]
            zh_ids = cls._best_case_subset(
                rows=zh_rows,
                gold_by_id=gold_by_id,
                count=3,
                language="zh",
                form_id=form_id,
                slot=sample_slot,
            )
            ja_ids = cls._best_case_subset(
                rows=ja_rows,
                gold_by_id=gold_by_id,
                count=3,
                language="ja",
                form_id=form_id,
                slot=sample_slot,
            )
            selected_ids = set([*english_ids, *zh_ids, *ja_ids])
            selected_pack_ids = [
                str(row["pack_id"])
                for row in [*english_pair, zh_pack, ja_pack]
            ]
            projected_packs: list[dict[str, Any]] = []
            projected_gold_packs: list[dict[str, Any]] = []
            for source_pack in source_form["packs"]:
                pack_id = str(source_pack["pack_id"])
                if pack_id not in selected_pack_ids:
                    continue
                projected_pack, projected_gold = cls._prune_pack(
                    pack=source_pack,
                    gold_pack=gold_pack_by_id[pack_id],
                    selected_case_ids=selected_ids,
                    case_chunk_by_id=case_chunk_by_id,
                )
                projected_packs.append(projected_pack)
                projected_gold_packs.append(projected_gold)
            selected_cases = [
                deepcopy(row)
                for row in source_form["cases"]
                if str(row["case_id"]) in selected_ids
            ]
            selected_case_ids = [str(row["case_id"]) for row in selected_cases]
            selected_gold_cases = [
                deepcopy(row)
                for row in source_gold["cases"]
                if str(row["case_id"]) in selected_ids
            ]
            if (
                len(selected_cases) != 24
                or len(projected_packs) != 4
                or [str(row["case_id"]) for row in selected_gold_cases]
                != selected_case_ids
            ):
                raise ValueError("CARD_DISTILLER_LANGUAGE_SAMPLE_INVALID")
            form = deepcopy(source_form)
            form["packs"] = projected_packs
            form["cases"] = selected_cases
            form["successor_projection"] = {
                "revision": cls.SAMPLE_REVISION,
                "source_content_sha256": source_form.get("content_sha256"),
            }
            gold = deepcopy(source_gold)
            gold["packs"] = projected_gold_packs
            gold["cases"] = selected_gold_cases
            gold["successor_projection"] = {
                "revision": cls.SAMPLE_REVISION,
                "source_content_sha256": source_gold.get("content_sha256"),
            }
            forms[form_id] = form
            golds[form_id] = gold
            selection[form_id] = {
                "selected_pack_ids": selected_pack_ids,
                "selected_case_ids": selected_case_ids,
                "language_counts": {"en": 18, "zh": 3, "ja": 3},
            }
        manifest = {
            "schema_version": cls.SAMPLE_REVISION,
            "sample_slot": sample_slot,
            "rotation_slots": cls.SAMPLE_ROTATION_SLOTS,
            "selected_case_count_per_form": 24,
            "selected_pack_count_per_form": 4,
            "language_counts_per_form": {"en": 18, "zh": 3, "ja": 3},
            "primary_language": "en",
            "diagnostic_languages": ["zh", "ja"],
            "cross_language_aggregation_forbidden": True,
            "selection": selection,
            "gold_in_subject_request": False,
        }
        manifest["manifest_sha256"] = _sha256(manifest)
        return {"forms": forms, "golds": golds, "sample_metadata": manifest}

    @staticmethod
    def _language_score_from_details(
        *,
        protocol: Any,
        details: Sequence[Mapping[str, Any]],
        primary: bool,
    ) -> dict[str, Any]:
        if not details:
            raise ValueError("CARD_DISTILLER_LANGUAGE_SCORE_EMPTY")
        values = [float(row["score"]) for row in details]
        critical_count = sum(bool(row.get("critical_reasons")) for row in details)
        major_count = sum(bool(row.get("major_reasons")) for row in details)
        family_means: dict[str, float] = {}
        for family in protocol.FAMILIES:
            family_values = [
                float(row["score"])
                for row in details
                if row.get("family") == family
            ]
            if family_values:
                family_means[family] = statistics.fmean(family_values)
        if primary:
            if set(family_means) != set(protocol.FAMILIES):
                raise ValueError("CARD_DISTILLER_ENGLISH_FAMILY_COVERAGE_INVALID")
            hard = [
                float(row["score"])
                for row in details
                if row.get("difficulty") == "hard"
            ]
            if not hard:
                raise ValueError("CARD_DISTILLER_ENGLISH_HARD_SET_EMPTY")
            tail_count = max(1, math.ceil(len(values) * 0.10))
            layers = {
                "overall_mean": statistics.fmean(values),
                "hard_mean": statistics.fmean(hard),
                "weakest_family": min(family_means.values()),
                "bottom_ten_percent": statistics.fmean(
                    sorted(values)[:tail_count]
                ),
                "structure": 100.0,
            }
            contributions = {
                key: protocol.piecewise_contribution(key, layers[key])
                for key in (
                    "overall_mean",
                    "hard_mean",
                    "weakest_family",
                    "bottom_ten_percent",
                )
            }
            contributions["structure"] = 15.0
            score = sum(contributions.values())
            caps: list[str] = []
            if critical_count and score > 59.0:
                score = 59.0
                caps.append("CATASTROPHIC_SCIENTIFIC_FAILURE_CAP_59")
            if major_count and score >= 90.0:
                score = min(score, 89.99)
                caps.append("MAJOR_FINDING_CEILING_BELOW_90")
            return {
                "score": round(score, 2),
                "case_count": len(details),
                "critical_count": critical_count,
                "major_count": major_count,
                "family_means": {
                    key: round(value, 2) for key, value in family_means.items()
                },
                "layers": {key: round(value, 2) for key, value in layers.items()},
                "score_caps_applied": caps,
                "score_semantics": "RAW_CAPABILITY_WITHOUT_REPAIR_BURDEN",
            }
        return {
            "score": round(statistics.fmean(values), 2),
            "case_count": len(details),
            "critical_count": critical_count,
            "major_count": major_count,
            "family_means_present": {
                key: round(value, 2) for key, value in family_means.items()
            },
            "score_semantics": "DESCRIPTIVE_CASE_MEAN_ONLY",
        }

    def _stage_score_projection(
        self,
        *,
        protocol: Any,
        forms: Mapping[str, Mapping[str, Any]],
        initial_scores: Mapping[str, Mapping[str, Any]],
        final_scores: Mapping[str, Mapping[str, Any]],
        repair_rounds: int,
    ) -> dict[str, Any]:
        language_by_case = {
            str(row["case_id"]): str(row["source_language"])
            for form in forms.values()
            for row in form["cases"]
        }

        def project(
            scores: Mapping[str, Mapping[str, Any]], language: str, primary: bool
        ) -> dict[str, Any]:
            by_form = {
                form_id: self._language_score_from_details(
                    protocol=protocol,
                    details=[
                        row
                        for row in scores[form_id]["details"]
                        if language_by_case.get(str(row.get("case_id")))
                        == language
                    ],
                    primary=primary,
                )
                for form_id in ("A", "B")
            }
            return {
                "score": round(
                    statistics.fmean(row["score"] for row in by_form.values()),
                    2,
                ),
                "forms": by_form,
                "case_count": sum(row["case_count"] for row in by_form.values()),
                "critical_count": sum(
                    row["critical_count"] for row in by_form.values()
                ),
                "major_count": sum(row["major_count"] for row in by_form.values()),
            }

        initial_en = project(initial_scores, "en", True)
        final_en = project(final_scores, "en", True)
        final_zh = project(final_scores, "zh", False)
        final_ja = project(final_scores, "ja", False)
        primary_case_ids = sorted(
            case_id
            for case_id, language in language_by_case.items()
            if language == "en"
        )
        quality = (
            "PASS"
            if final_en["critical_count"] == 0
            and final_en["score"] >= PASSING_SCORE
            else "FAIL"
        )
        return {
            "score_override": final_en["score"],
            "first_pass_score_override": initial_en["score"],
            "quality_verdict_override": quality,
            "primary_case_ids": primary_case_ids,
            "primary_language_track": "en",
            "language_tracks": {
                "en": {
                    "role": "PRODUCTION_PRIMARY",
                    "first_pass": initial_en,
                    "post_repair": final_en,
                    "affects_primary_score": True,
                    "affects_model_fail": True,
                },
                "zh": {
                    "role": "DIAGNOSTIC_ONLY",
                    "post_repair": final_zh,
                    "affects_primary_score": False,
                    "affects_model_fail": False,
                },
                "ja": {
                    "role": "DIAGNOSTIC_ONLY",
                    "post_repair": final_ja,
                    "affects_primary_score": False,
                    "affects_model_fail": False,
                },
            },
            "cross_language_aggregation_forbidden": True,
            "repair_burden_affects_capability_score": False,
            "repair_burden_reported_separately": True,
            "repair_rounds_used": repair_rounds,
        }

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
            reason="Quality_LANGUAGE_BANK_REFERENCE_REGRESSION_READY",
            base_model_calls=6 if plan["profile_kind"] == "LOCAL" else 4,
            sample_case_count_per_form=24,
            sample_pack_count_per_form=4,
            sample_rotation_slots=self.SAMPLE_ROTATION_SLOTS,
            sample_language_counts_per_form={"en": 18, "zh": 3, "ja": 3},
            primary_language_track="en",
            diagnostic_language_tracks=["zh", "ja"],
            cross_language_aggregation_forbidden=True,
            repair_burden_affects_capability_score=False,
            second_round_penalty_semantics=(
                "REPORTED_SEPARATELY_NOT_DEDUCTED_FROM_CAPABILITY_SCORE"
            ),
        )
        plan["plan_sha256"] = _sha256(plan)
        return plan
