"""Invocation-local sampling views over the existing Quality/Execution executors.

No replacement provider transport, scorer, Gold, repair prompt or reply cap.
FULL preserves the predecessor inputs. Reduced tiers retain every family that
the predecessor scorer requires; actual coverage is always part of the plan.
"""
from __future__ import annotations

from copy import copy, deepcopy
import math
import inspect
from typing import Any, Mapping

from .contracts import canonical_sha256
from .exam_tiers import REVISION, ROLE_BY_NODE, TIERS, nested_selection, tier_manifest, validate_tier


def _hash(value, field):
    value.pop(field, None)
    value[field] = canonical_sha256({k: v for k, v in value.items() if not k.startswith("_")})
    return value


def _coverage(role, tier, full, selected, binding=None, required=(), unit="CASE"):
    return tier_manifest(role=role, tier=tier, full_ids=full, selected_ids=selected,
                         required_ids=required, source_binding=binding, unit=unit,
                         floor_reason=("EXISTING_SCORER_FAMILY_OR_ATOMIC_CORPUS_MINIMUM"
                                       if len(selected) > math.ceil(len(full) * TIERS[tier][0]) else None))


def _card_inputs(base, cls, tier, **kwargs):
    data = base._exam_inputs(**kwargs)
    if tier == "FULL":
        return data
    full = [r["case_id"] for form in data["forms"].values() for r in form["cases"]]
    required = []
    for fid in ("A", "B"):
        form, gold = data["forms"][fid], data["golds"][fid]
        metadata = {r["case_id"]: {k: r[k] for k in ("family", "difficulty")} for r in gold["cases"]}
        chosen = []
        for language in ("en", "zh", "ja"):
            rows = [{**r, **metadata[r["case_id"]]} for r in form["cases"] if r["source_language"] == language]
            controls = []
            if language == "en":
                for index, family in enumerate(sorted({r["family"] for r in rows})):
                    members = [r for r in rows if r["family"] == family]
                    desired = ("hard", "medium", "easy")[index % 3]
                    members.sort(key=lambda r: (r["difficulty"] != desired, r["case_id"]))
                    controls.append(members[0]["case_id"])
                if not any(r["difficulty"] == "hard" and r["case_id"] in controls for r in rows):
                    controls.append(next(r["case_id"] for r in rows if r["difficulty"] == "hard"))
            else:
                controls.append(rows[0]["case_id"])
            required.extend(controls)
            chosen.extend(nested_selection(rows, tier=tier, id_key="case_id", required_ids=controls,
                                            strata=("family", "difficulty")))
        ids = {r["case_id"] for r in chosen}
        chunks = {r["case_id"]: r["source_unit"]["chunk_id"] for r in form["cases"]}
        gold_packs = {r["pack_id"]: r for r in gold["packs"]}
        pairs = [base._prune_pack(pack=p, gold_pack=gold_packs[p["pack_id"]], selected_case_ids=ids,
                                 case_chunk_by_id=chunks) for p in form["packs"] if set(p["case_ids"]) & ids]
        form["cases"] = [r for r in form["cases"] if r["case_id"] in ids]
        form["packs"] = [p[0] for p in pairs]
        gold["cases"] = [r for r in gold["cases"] if r["case_id"] in ids]
        gold["packs"] = [p[1] for p in pairs]
    selected = [r["case_id"] for f in data["forms"].values() for r in f["cases"]]
    metadata = data["sample_metadata"]
    metadata["exam_tier"] = _coverage("CARD_DISTILLER", tier, full, selected,
                                       metadata["manifest_sha256"], required)
    metadata["selected_case_count_per_form"] = len(data["forms"]["A"]["cases"])
    metadata["selected_pack_count_per_form"] = len(data["forms"]["A"]["packs"])
    metadata["language_counts_per_form"] = {lang: sum(r["source_language"] == lang for r in data["forms"]["A"]["cases"])
                                            for lang in ("en", "zh", "ja")}
    metadata["selection"] = {fid: {"selected_pack_ids": [r["pack_id"] for r in data["forms"][fid]["packs"]],
                                  "selected_case_ids": [r["case_id"] for r in data["forms"][fid]["cases"]]}
                              for fid in ("A", "B")}
    _hash(metadata, "manifest_sha256")
    return data


def _reranker_manifest(self, base, tier, slot):
    value = base._sample_manifest(self, slot)
    if tier == "FULL":
        return value
    full = [r["item_id"] for rows in value["_selected"].values() for r in rows]
    required = []
    for fid in ("A", "B"):
        rows = value["_selected"][fid]
        controls = []
        for family in self.FAMILIES:
            controls.append(next(r["item_id"] for r in rows if r["family_id"] == family and r["language_mode"] == "en_en"))
        for lang in ("zh_zh", "ja_ja"):
            controls.append(next(r["item_id"] for r in rows if r["language_mode"] == lang))
        required.extend(controls)
        selected = nested_selection(rows, tier=tier, id_key="item_id", required_ids=controls,
                                    strata=("language_mode", "family_id"))
        value["_selected"][fid] = selected
        value["main_items_by_form"][fid] = [r["item_id"] for r in selected]
        value["english_primary_items_by_form"][fid] = [r["item_id"] for r in selected if r["language_mode"] == "en_en"]
    selected_ids = [r["item_id"] for rows in value["_selected"].values() for r in rows]
    value["exam_tier"] = _coverage("RERANKER_TEXT", tier, full, selected_ids, required=required)
    self._tier_coverage = value["exam_tier"]
    self.MAIN_CALLS = value["main_call_count"] = len(selected_ids)
    self.MODEL_CALLS = self.MAIN_CALLS + self.REPEAT_CALLS
    value["language_mode_counts"] = {lang: sum(r["language_mode"] == lang for rows in value["_selected"].values() for r in rows)
                                       for lang in ("en_en", "zh_zh", "ja_ja")}
    return _hash(value, "sample_manifest_sha256")


def _embedding_assets(self, base, tier):
    if getattr(self, "_tier_assets", None) is not None:
        return self._tier_assets
    original = base._load_assets(self)
    if tier == "FULL":
        return original
    # Do not deepcopy imported scorer modules. They are read-only reused assets.
    data = dict(original)
    data["production"] = deepcopy(original["production"])
    data["adversarial_forms"] = deepcopy(original["adversarial_forms"])
    corpora = data["production"]["corpora"]
    full = ["PROD:" + r["corpus_id"] for r in corpora]
    required = [next(r["corpus_id"] for r in corpora if r["scope_kind"] == scope)
                for scope in ("PAPER_SCOPED", "CROSS_PAPER")]
    selected = nested_selection(corpora, tier=tier, id_key="corpus_id", required_ids=required,
                                strata=("scope_kind",))
    corpus_ids = {r["corpus_id"] for r in selected}
    data["production"]["corpora"] = selected
    data["production"]["tasks"] = [r for r in data["production"]["tasks"] if r["corpus_id"] in corpus_ids]
    task_ids = {r["task_id"] for r in data["production"]["tasks"]}
    data["gold"] = [r for r in original["gold"] if r["task_id"] in task_ids]
    data["corpus_by_id"] = {r["corpus_id"]: r for r in selected}
    data["tasks_by_corpus"] = {k: v for k, v in original["tasks_by_corpus"].items() if k in corpus_ids}
    chosen = ["PROD:" + r["corpus_id"] for r in selected]
    for fid in ("A", "B"):
        form = data["adversarial_forms"][fid]
        full.extend(f"ADV:{fid}:" + r["corpus_id"] for r in form["corpora"])
        form["corpora"] = nested_selection(form["corpora"], tier=tier, id_key="corpus_id")
        keep = {r["corpus_id"] for r in form["corpora"]}
        form["tasks"] = [r for r in form["tasks"] if r["corpus_id"] in keep]
        ids = {r["task_id"] for r in form["tasks"]}
        form["gold"] = [r for r in form["gold"] if r["task_id"] in ids]
        chosen.extend(f"ADV:{fid}:" + r["corpus_id"] for r in form["corpora"])
    self.PRODUCTION_INPUT_COUNT = sum(len(r["documents"]) for r in selected) + len(task_ids)
    self.ADVERSARIAL_INPUT_COUNT = sum(sum(len(r["documents"]) for r in f["corpora"]) + len(f["tasks"])
                                       for f in data["adversarial_forms"].values())
    self.REPEATABILITY_INPUT_COUNT = 4 * len(selected)
    self._tier_coverage = _coverage("EMBEDDING_TEXT", tier, full, chosen,
                                   original["reference_pack_sha256"], ["PROD:" + x for x in required], "INTACT_CORPUS")
    self._tier_assets = data
    return data


def _analysis_reviewer_sample(self, base, tier, context):
    from .analysis_reviewer_exam import _project_output_schema
    sample = base._load_sample(self, context)
    if tier == "FULL":
        return sample
    full, selected_ids, controls = [], [], []
    source_hash = sample["manifest"]["manifest_sha256"]
    for fid in ("A", "B"):
        form, gold = sample["forms"][fid], sample["golds"][fid]
        full.extend(r["case_id"] for r in form["cases"])
        required = [next(r["case_id"] for r in gold["cases"] if r["expected_issue_family"] == "none"),
                    next(r["case_id"] for r in gold["cases"] if r.get("critical") is True)]
        controls.extend(required)
        metadata = [{"case_id":r["case_id"], "family":r["expected_issue_family"], "difficulty":r["difficulty"]}
                    for r in gold["cases"]]
        ids = {r["case_id"] for r in nested_selection(metadata, tier=tier, id_key="case_id",
                                                     required_ids=required, strata=("difficulty", "family"))}
        form["cases"] = [r for r in form["cases"] if r["case_id"] in ids]
        gold["cases"] = [r for r in gold["cases"] if r["case_id"] in ids]
        ordered = [r["case_id"] for r in form["cases"]]
        selected_ids.extend(ordered)
        sample["schemas"][fid] = _project_output_schema(sample["schemas"][fid], ordered)
        sample["manifest"]["bindings"][fid] = [r for r in sample["manifest"]["bindings"][fid] if r["case_id"] in ids]
    coverage = _coverage("ANALYSIS_REVIEWER", tier, full, selected_ids, source_hash, controls)
    sample["manifest"].update(selected_case_count=len(selected_ids), exam_tier=coverage,
                               selected_case_ids_sha256=canonical_sha256(selected_ids))
    _hash(sample["manifest"], "manifest_sha256")
    self._tier_coverage = coverage
    return sample


def tier_executor(executor: Any, context: Mapping[str, Any]) -> Any:
    """Return a new invocation-local view; never mutate a shared executor."""
    if "workflow_exam_tier" not in context:
        return executor
    tier = validate_tier(context["workflow_exam_tier"])
    base = type(executor)
    attrs: dict[str, Any] = {}
    name = base.__name__
    if name == "QualityNewCardDistillerLanguageBankExamExecutor":
        attrs["_exam_inputs"] = classmethod(lambda cls, **kw: _card_inputs(base, cls, tier, **kw))
    elif name == "QualityNewRerankerTextLanguageBankExamExecutor":
        attrs["_sample_manifest"] = lambda self, slot: _reranker_manifest(self, base, tier, slot)
    elif name == "QualityNewEmbeddingExamExecutor":
        attrs["_load_assets"] = lambda self: _embedding_assets(self, base, tier)
        attrs["_physical_call_count"] = lambda self, batch: sum(math.ceil(n / batch) for n in (
            self.PRODUCTION_INPUT_COUNT, self.ADVERSARIAL_INPUT_COUNT, self.REPEATABILITY_INPUT_COUNT))
    elif name == "QualityAnalysisReviewerExamSuccessorExecutor":
        attrs["_load_sample"] = lambda self, ctx: _analysis_reviewer_sample(self, base, tier, ctx)
    elif hasattr(base, "_exam_inputs"):
        class_bound = isinstance(inspect.getattr_static(base, "_exam_inputs"), classmethod)
        def capture_inputs(self, *args, **kwargs):
            data = base._exam_inputs(*args, **kwargs) if class_bound else base._exam_inputs(self, *args, **kwargs)
            self._tier_inputs = data
            return data
        attrs["_exam_inputs"] = capture_inputs

    def plan(self, node, target, accepted_context):
        result = base.plan(self, node, target, accepted_context)
        if result.get("status") != "READY":
            return result
        role = ROLE_BY_NODE.get(node.get("node_id"))
        if role is None:
            return result
        coverage = getattr(self, "_tier_coverage", None)
        if name == "QualityNewCardDistillerLanguageBankExamExecutor":
            pack = self._quality()["reference"].load_card_distiller_reference_exam_pack(self._reference_pack_root)
            data = self._exam_inputs(pack=pack, context=accepted_context)
            full_ids = [r["case_id"] for f in data["forms"].values() for r in f["cases"]]
            coverage = data["sample_metadata"].get("exam_tier") or _coverage(role, tier, full_ids, full_ids)
            result.update(sample_case_count_per_form=len(data["forms"]["A"]["cases"]),
                          sample_pack_count_per_form=len(data["forms"]["A"]["packs"]),
                          sample_language_counts_per_form=data["sample_metadata"]["language_counts_per_form"])
        if coverage is None:
            # Reviewers and Analysis already use one complete safety-family block
            # per form; smaller blocks cannot use their accepted scorer. OCR's
            # balanced minimum is similarly atomic. Do not secretly drop a family.
            data = getattr(self, "_tier_inputs", None)
            if name == "QualityAnalysisReviewerExamSuccessorExecutor":
                data = self._load_sample(accepted_context)
            if data and "forms" in data:
                ids = [r["case_id"] for f in data["forms"].values() for r in f["cases"]]
            elif name == "QualityOcrPageExamExecutor":
                sample, _ = self._sample_manifest()
                ids = [r["case_id"] for r in sample["main_cases"]]
            elif name == "QualityNewRerankerTextLanguageBankExamExecutor":
                sample = self._sample_manifest(int(result["sample_slot"]))
                ids = [r["item_id"] for rows in sample["_selected"].values() for r in rows]
            elif name == "QualityNewEmbeddingExamExecutor":
                data = self._load_assets()
                ids = ["PROD:" + r["corpus_id"] for r in data["production"]["corpora"]] + [
                    f"ADV:{fid}:" + r["corpus_id"] for fid, f in data["adversarial_forms"].items() for r in f["corpora"]]
            else:
                # An unknown predecessor must not pretend to implement tiers.
                return dict(status="NOT_AVAILABLE", reason="EXAM_TIER_EXECUTOR_UNSUPPORTED", mode="NONE")
            coverage = _coverage(role, tier, ids, ids, result.get("sample_manifest_sha256") or result.get("reference_pack_sha256"), ids,
                                 "INTACT_CORPUS" if name == "QualityNewEmbeddingExamExecutor" else "CASE")
        coverage = deepcopy(coverage)
        coverage["execution_binding"] = {k: result.get(k) for k in
            ("reference_pack_sha256", "scoring_protocol_sha256", "sample_manifest_sha256", "executor_ref")}
        if role in {"OCR_PAGE", "EMBEDDING_TEXT", "RERANKER_TEXT"}:
            coverage["repair_applicability"] = "NO_SEMANTIC_ANSWER_REPAIR_IN_EXISTING_CATEGORY"
            coverage["semantic_repair_round_limit"] = 0
        else:
            coverage["repair_applicability"] = "TWO_DIRECTED_SEMANTIC_REPAIRS"
            coverage["semantic_repair_round_limit"] = 2
        _hash(coverage, "manifest_sha256")
        result["exam_tier"] = coverage
        if tier != "FULL":
            comparison = {"predecessor_cohort":result.get("comparison_cohort_id"),
                          "tier_manifest_sha256":coverage["manifest_sha256"], "revision":REVISION}
            result["comparison_cohort_id"] = role + ":TIER:" + canonical_sha256(comparison)
            result["comparison_binding_sha256"] = canonical_sha256(comparison)
            result["horizontal_comparison_eligible"] = False
        result["authorization_required"] = True
        result["repair_policy_by_tier"] = "IDENTICAL_TWO_ROUNDS_EXISTING_CATEGORY_COEFFICIENTS"
        result["cost_fraction_guaranteed"] = False
        if name == "QualityAnalysisReviewerExamSuccessorExecutor":
            result["sample_case_count"] = coverage["selected_count"]
        if name == "QualityNewRerankerTextLanguageBankExamExecutor" and tier != "FULL":
            result["main_model_calls"] = self.MAIN_CALLS
            result["maximum_model_calls"] = self.MODEL_CALLS + int(result.get("transport_retry_budget") or 0)
            result["english_primary_main_call_count"] = self.MAIN_CALLS - 4
        self._tier_coverage = coverage
        return _hash(result, "plan_sha256")

    attrs["plan"] = plan
    view = copy(executor)
    view.__class__ = type(f"{name}CoverageView", (base,), attrs)
    # Partial observations must never complete a predecessor/full cohort cache.
    if tier != "FULL" and hasattr(view, "_scratch_root"):
        view._scratch_root = view._scratch_root / "exam_tiers" / tier.lower()
    return view


def project_tier_result(result: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(result)
    # Some reused scorers omit the executor identity from their return projection.
    # Carry the already-bound plan identity; never replace a conflicting identity.
    if not value.get("executor_ref") and isinstance(plan.get("executor_ref"), str):
        value["executor_ref"] = plan["executor_ref"]
    coverage = plan.get("exam_tier")
    if not isinstance(coverage, Mapping):
        return value
    value["exam_tier"] = deepcopy(dict(coverage))
    value["formal_qualification_eligible"] = False
    value["tier_result_is_provisional"] = coverage["tier"] != "FULL"
    if value["tier_result_is_provisional"]:
        value["model_fail_established"] = False
        value["stable_score_cache_eligible"] = False
        value["horizontal_comparison_eligible"] = False
        value["operational_eligibility_verdict"] = "NOT_ASSESSED"
    # Preserve the category's existing raw-capability and repair-burden axes.
    # A repair-adjusted display is additive; historical score is not overwritten.
    penalty = value.get("cumulative_category_repair_penalty")
    if isinstance(value.get("cumulative_category_repair_penalty_by_form"), Mapping):
        # Analysis Reviewer already deducts each form's own nonlinear burden.
        value["repair_adjusted_exam_score"] = value.get("score_exact", value.get("score"))
    if isinstance(penalty, Mapping) and isinstance(penalty.get("cumulative_penalty"), (int, float)):
        amount = float(penalty["cumulative_penalty"])
        value["repair_adjusted_exam_score"] = (
            round(max(0, float(value["score"]) - amount), 6)
            if (value.get("repair_penalty_applied_to_role_ability") is False
                or value.get("repair_burden_affects_capability_score") is False)
            else value.get("score")
        ) if isinstance(value.get("score"), (int, float)) else None
    return value
