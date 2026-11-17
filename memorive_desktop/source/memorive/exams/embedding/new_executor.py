"""QualityNewEmbeddingExamExecutor: plans, runs and scores the current embedding exam.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.util
import math
from pathlib import Path
import re
import statistics
from time import monotonic
from typing import (
    Any,
    Callable,
    Mapping,
    Sequence,
)
import uuid
from memorive.exams.outcomes import classify_execution_outcome
from memorive.exams.retrieval_calibration import (
    EMBEDDING_ROLE_ADEQUACY_POLICY,
    assess_embedding_role_adequacy,
)
from memorive.exams.common import (
    AUTHORIZATION_SCHEMA,
    EXECUTOR_REF,
    PLAN_SCHEMA,
    RESULT_SCHEMA,
    _compact,
    _file_sha256,
    _load_json,
    _not_available,
    _not_run,
    _sha256,
    _write_json_create_only,
)
from memorive.exams.embedding.executor import QualityEmbeddingExamExecutor


@dataclass
class _EmbeddingExamRun:
    """One run of the embedding exam (QualityNewEmbeddingExamExecutor.execute).

    The plan, the run folder and the call counters are set when the run starts; _embedding_exam_rows
    builds the production, adversarial and repeat rows from the exam assets; _run_embedding_lanes embeds
    each lane; _score_embedding_exam scores the rankings, writes the evidence and builds the result. A
    failure is reported from the plan and counters as they are."""

    # The run as planned.
    target: Mapping[str, Any]
    embedding_call: Callable[..., Mapping[str, Any]]
    plan: Mapping[str, Any]
    requested_model: str | None
    started: float
    run_id: str
    run_root: Path
    state: dict[str, Any]
    # The exam assets and the rows built from them.
    assets: Any = None
    production_tasks: Any = None
    production_rows: list[dict[str, str]] = field(default_factory=list)
    adversarial_rows: list[dict[str, str]] = field(default_factory=list)
    adversarial_tasks_by_form: dict[str, dict[str, list[Mapping[str, Any]]]] = field(default_factory=dict)
    repeat_rows: list[dict[str, str]] = field(default_factory=list)
    # Counters and the vectors of each lane.
    call_index: int = 0
    dimension: int | None = None
    observed_returned_model: str | None = None
    production_vectors: dict[str, list[float]] = field(default_factory=dict)
    adversarial_vectors: dict[str, list[float]] = field(default_factory=dict)
    repeat_vectors: dict[str, list[float]] = field(default_factory=dict)


class QualityNewEmbeddingExamExecutor(QualityEmbeddingExamExecutor):
    """Run the production-shaped successor and a bounded legacy diagnostic.

    The production track models the Core retrieval pool and uses Recall@15.
    A small, immutable sample of the original Quality short-CJK pack remains a
    separate adversarial diagnostic.  The two scores are deliberately never
    combined.  A successor overlay assesses first-stage role adequacy while
    formal qualification still requires a live production-shaped run.
    """

    EXECUTOR_REF = "Quality_NEW_Quality_EMBEDDING_SUCCESSOR_EXECUTOR_V2"
    MODE = "LIVE_Quality_NEW_EMBEDDING_PRODUCTION_AND_ADVERSARIAL_REFERENCE_REGRESSION"
    SAMPLE_REVISION = "EMBEDDING_PRODUCTION_R0_2_PLUS_FROZEN_V1_SAMPLE_V1"
    SCORING_PROJECTION_REVISION = (
        "Quality_NEW_EMBEDDING_PRODUCTION_RECALL_AT_15_ROLE_ADEQUACY_V2"
    )
    BATCH_EQUIVALENCE_REVISION = (
        "Quality_NEW_EMBEDDING_PROVIDER_LIMIT_ALIGNED_BATCH_V1"
    )
    REPEATABILITY_SELECTION_REVISION = (
        "TWO_DOCUMENTS_AND_TWO_QUERIES_PER_PRODUCTION_CORPUS_V1"
    )
    PRODUCTION_INPUT_COUNT = 392
    ADVERSARIAL_INPUT_COUNT = 294
    REPEATABILITY_INPUT_COUNT = 16
    BGE_BATCH_SIZE = 256
    QWEN_BATCH_SIZE = 32
    API_BUDGET_CAP_CNY = 0.40
    SUPPORTED_API_MODELS = {
        "BAAI/bge-m3",
        "Pro/BAAI/bge-m3",
        "Qwen/Qwen3-VL-Embedding-8B",
    }

    def __init__(
        self,
        *,
        scratch_root: Path,
        quality_package: str,
        reference_pack_root: Path,
    ) -> None:
        super().__init__(
            scratch_root=scratch_root,
            quality_package=quality_package,
            reference_pack_root=reference_pack_root,
        )
        self._legacy_reference_pack_root = (
            self._reference_pack_root.parent.parent
            / "v1"
            / "reference_pack"
        ).resolve()
        self._successor_assets: dict[str, Any] | None = None
        self._successor_scorer: Any | None = None

    @staticmethod
    def _profile_kind(target: Mapping[str, Any]) -> str | None:
        if target.get("kind") == "API":
            if (
                "siliconflow" in _compact(target.get("provider"))
                and target.get("model_name")
                in QualityNewEmbeddingExamExecutor.SUPPORTED_API_MODELS
                and target.get("connection_status") == "AVAILABLE"
                and isinstance(target.get("config_id"), str)
                and bool(target.get("config_id"))
            ):
                return "API"
            return None
        if target.get("kind") == "LOCAL":
            if (
                target.get("endpoint_kind") == "ollama"
                and target.get("capability") == "EMBEDDING"
                and target.get("execution_eligible") is True
                and target.get("exact_identity_available") is True
                and target.get("connection_status") == "AVAILABLE"
                and isinstance(target.get("model_name"), str)
                and bool(target.get("model_name"))
                and isinstance(target.get("model_digest"), str)
                and bool(re.fullmatch(r"[A-F0-9]{64}", target["model_digest"]))
                and isinstance(target.get("config_id"), str)
                and bool(target.get("config_id"))
            ):
                return "LOCAL"
        return None

    @staticmethod
    def _batch_size(target: Mapping[str, Any]) -> int:
        # The reused Ollama transport sends one source text per physical request
        # (and keeps its existing no-truncation recovery). Plan and claims must
        # match that boundary; an API-sized batch falsely failed the receipt.
        if target.get("kind") == "LOCAL":
            return 1
        model_name = str(target.get("model_name") or "")
        return (
            QualityNewEmbeddingExamExecutor.QWEN_BATCH_SIZE
            if "qwen" in model_name.casefold()
            else QualityNewEmbeddingExamExecutor.BGE_BATCH_SIZE
        )

    @staticmethod
    def _expected_dimension(target: Mapping[str, Any]) -> int | None:
        if target.get("kind") != "API":
            return None
        if target.get("model_name") == "Qwen/Qwen3-VL-Embedding-8B":
            return 4096
        return 1024

    @staticmethod
    def _physical_call_count(batch_size: int) -> int:
        return sum(
            math.ceil(count / batch_size)
            for count in (
                QualityNewEmbeddingExamExecutor.PRODUCTION_INPUT_COUNT,
                QualityNewEmbeddingExamExecutor.ADVERSARIAL_INPUT_COUNT,
                QualityNewEmbeddingExamExecutor.REPEATABILITY_INPUT_COUNT,
            )
        )

    def _load_successor_scorer(self) -> Any:
        if self._successor_scorer is not None:
            return self._successor_scorer
        scorer_path = (
            self._reference_pack_root
            / "scoring"
            / "embedding_successor_scorer.py"
        )
        module_name = (
            "_memorive_embedding_successor_"
            + hashlib.sha256(str(scorer_path).encode("utf-8")).hexdigest()
        )
        spec = importlib.util.spec_from_file_location(module_name, scorer_path)
        if spec is None or spec.loader is None:
            raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_SUCCESSOR_SCORER_INVALID")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not callable(getattr(module, "score_track", None)):
            raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_SUCCESSOR_SCORER_INVALID")
        self._successor_scorer = module
        return module

    def _verify_successor_checksums(self) -> dict[str, Any]:
        manifest_path = self._reference_pack_root / "manifest.reference.json"
        sums_path = self._reference_pack_root / "SHA256SUMS.txt"
        manifest = _load_json(manifest_path)
        if (
            manifest.get("schema_version")
            != "EmbeddingSuccessorReferenceManifest-v1"
            or manifest.get("status")
            != "FROZEN_EXECUTABLE_REFERENCE_PACK_CANDIDATE"
            or manifest.get("pack_revision")
            != "embedding_text_production_adversarial_successor_r0.2"
            or manifest.get("content_class") != "SYNTHETIC_REDISTRIBUTABLE"
            or manifest.get("gold_runtime_local_only") is not True
            or manifest.get("gold_sent_to_provider") is not False
            or manifest.get("real_pdf_or_chunk_text_included") is not False
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_SUCCESSOR_MANIFEST_INVALID")
        self_hash = manifest.get("manifest_content_sha256")
        payload = deepcopy(manifest)
        payload.pop("manifest_content_sha256", None)
        if self_hash != _sha256(payload):
            raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_SUCCESSOR_MANIFEST_HASH_INVALID")

        checksums: dict[str, str] = {}
        for raw_line in sums_path.read_text(encoding="ascii").splitlines():
            parts = raw_line.split("  ", 1)
            if (
                len(parts) != 2
                or not re.fullmatch(r"[A-F0-9]{64}", parts[0])
                or not parts[1]
                or "\\" in parts[1]
                or parts[1].startswith("/")
                or ".." in parts[1].split("/")
                or parts[1] in checksums
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_SUCCESSOR_SUMS_INVALID")
            checksums[parts[1]] = parts[0]
        expected_paths = {
            "manifest.reference.json",
            *(
                str(value)
                for value in (manifest.get("files") or {}).keys()
            ),
        }
        if set(checksums) != expected_paths:
            raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_SUCCESSOR_SUMS_EXACT_SET_INVALID")
        for relative, expected_hash in checksums.items():
            path = self._reference_pack_root.joinpath(*relative.split("/"))
            if not path.is_file() or _file_sha256(path) != expected_hash:
                raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_SUCCESSOR_FILE_HASH_INVALID")
        manifest_files = manifest.get("files")
        if not isinstance(manifest_files, Mapping) or any(
            checksums.get(str(relative)) != expected_hash
            for relative, expected_hash in manifest_files.items()
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_SUCCESSOR_MANIFEST_FILES_INVALID")
        return manifest

    def _load_assets(self) -> dict[str, Any]:
        if self._successor_assets is not None:
            return self._successor_assets
        manifest = self._verify_successor_checksums()
        production = _load_json(
            self._reference_pack_root / "public" / "production_corpora.json"
        )
        gold_document = _load_json(
            self._reference_pack_root / "gold" / "production_gold.json"
        )
        sample = _load_json(
            self._reference_pack_root / "sampling" / "adversarial_sample.json"
        )
        protocol = _load_json(
            self._reference_pack_root / "scoring" / "scoring_protocol.json"
        )
        revision = manifest["pack_revision"]
        corpora = production.get("corpora")
        tasks = production.get("tasks")
        gold_rows = gold_document.get("gold")
        if (
            production.get("schema_version") != "EmbeddingProductionTrackForms-v1"
            or production.get("pack_revision") != revision
            or production.get("provider_received_gold") is not False
            or gold_document.get("schema_version") != "EmbeddingProductionTrackGold-v1"
            or gold_document.get("pack_revision") != revision
            or gold_document.get("provider_received_gold") is not False
            or protocol.get("schema_version") != "EmbeddingSuccessorScoringProtocol-v1"
            or protocol.get("single_aggregate_may_establish_fail") is not False
            or protocol.get("qualification_threshold_defined") is not False
            or not isinstance(corpora, list)
            or not isinstance(tasks, list)
            or not isinstance(gold_rows, list)
            or len(corpora) != 4
            or len(tasks) != 24
            or len(gold_rows) != 24
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_SUCCESSOR_ASSETS_INVALID")

        corpus_by_id: dict[str, Mapping[str, Any]] = {}
        document_ids: set[str] = set()
        document_lengths: list[int] = []
        for corpus, expected_count in zip(corpora, (80, 80, 80, 128)):
            corpus_id = corpus.get("corpus_id") if isinstance(corpus, Mapping) else None
            documents = corpus.get("documents") if isinstance(corpus, Mapping) else None
            if (
                not isinstance(corpus_id, str)
                or not corpus_id
                or corpus_id in corpus_by_id
                or not isinstance(documents, list)
                or len(documents) != expected_count
                or corpus.get("scope_kind") not in {"PAPER_SCOPED", "CROSS_PAPER"}
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_SUCCESSOR_CORPUS_INVALID")
            corpus_by_id[corpus_id] = corpus
            for document in documents:
                unit_id = document.get("unit_id") if isinstance(document, Mapping) else None
                text = document.get("text") if isinstance(document, Mapping) else None
                if (
                    not isinstance(unit_id, str)
                    or not unit_id
                    or unit_id in document_ids
                    or not isinstance(text, str)
                    or not 180 <= len(text) <= 810
                    or len(text.encode("utf-8")) > 65536
                ):
                    raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_SUCCESSOR_DOCUMENT_INVALID")
                document_ids.add(unit_id)
                document_lengths.append(len(text))
        task_by_id: dict[str, Mapping[str, Any]] = {}
        tasks_by_corpus: dict[str, list[Mapping[str, Any]]] = {
            corpus_id: [] for corpus_id in corpus_by_id
        }
        cjk_query_count = 0
        query_lengths: list[int] = []
        for task in tasks:
            task_id = task.get("task_id") if isinstance(task, Mapping) else None
            corpus_id = task.get("corpus_id") if isinstance(task, Mapping) else None
            query = task.get("query") if isinstance(task, Mapping) else None
            if (
                not isinstance(task_id, str)
                or not task_id
                or task_id in task_by_id
                or corpus_id not in corpus_by_id
                or not isinstance(query, str)
                or not 150 <= len(query) <= 225
                or len(query.encode("utf-8")) > 65536
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_SUCCESSOR_TASK_INVALID")
            task_by_id[task_id] = task
            tasks_by_corpus[str(corpus_id)].append(task)
            query_lengths.append(len(query))
            if re.search(r"[\u3400-\u9fff]", query):
                cjk_query_count += 1
        if (
            set(task_by_id) != {
                str(row.get("task_id"))
                for row in gold_rows
                if isinstance(row, Mapping)
            }
            or any(len(rows) != 6 for rows in tasks_by_corpus.values())
            or cjk_query_count != 4
            or len(document_ids) != 368
            or manifest.get("production_corpus_count") != 4
            or manifest.get("production_task_count") != 24
            or manifest.get("production_document_count") != 368
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_SUCCESSOR_TOPOLOGY_INVALID")
        for gold in gold_rows:
            task_id = str(gold["task_id"])
            corpus_id = str(gold.get("corpus_id"))
            if corpus_id != task_by_id[task_id].get("corpus_id"):
                raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_SUCCESSOR_GOLD_SCOPE_INVALID")
            allowed_ids = {
                str(row["unit_id"])
                for row in corpus_by_id[corpus_id]["documents"]
            }
            relevant = set(gold.get("relevant_ids") or [])
            critical = set(gold.get("critical_ids") or [])
            forbidden = set(gold.get("forbidden_ids") or [])
            facets = [set(row) for row in (gold.get("facet_groups") or [])]
            if (
                not relevant
                or not critical
                or not facets
                or not relevant <= allowed_ids
                or not critical <= relevant
                or not forbidden <= allowed_ids
                or relevant & forbidden
                or any(not facet or not facet <= relevant for facet in facets)
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_SUCCESSOR_GOLD_INVALID")

        exam = self._quality()["exam"]
        legacy_sha256 = _file_sha256(
            self._legacy_reference_pack_root / "SHA256SUMS.txt"
        )
        legacy_scorer = exam.load_bundled_scorer(
            self._legacy_reference_pack_root
        )
        diagnosis = exam.inspect_reference_pack(self._legacy_reference_pack_root)
        replay = legacy_scorer.verify_reference_pack(
            self._legacy_reference_pack_root
        )
        if (
            diagnosis.get("status") != "PASS"
            or replay.get("verification_result") != "PASS"
            or sample.get("schema_version")
            != "EmbeddingAdversarialSampleManifest-v1"
            or sample.get("source_pack_revision")
            != "embedding_text_reference_exam_r0.5"
            or sample.get("source_pack_sha256") != legacy_sha256
            or manifest.get("adversarial_source_pack_sha256") != legacy_sha256
            or sample.get("corpus_count") != 6
            or sample.get("task_count") != 42
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_LEGACY_SAMPLE_INVALID")

        adversarial_forms: dict[str, Any] = {}
        indices_by_form = sample.get("corpus_indices_by_form")
        if not isinstance(indices_by_form, Mapping):
            raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_LEGACY_SAMPLE_INVALID")
        for form_id in ("A", "B"):
            indices = indices_by_form.get(form_id)
            if (
                not isinstance(indices, list)
                or len(indices) != 3
                or any(not isinstance(index, int) or isinstance(index, bool) for index in indices)
                or len(set(indices)) != len(indices)
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_LEGACY_SAMPLE_INVALID")
            form_tasks = _load_json(
                self._legacy_reference_pack_root / "forms" / f"form_{form_id}.json"
            )["tasks"]
            form_corpora = _load_json(
                self._legacy_reference_pack_root
                / "corpora"
                / f"form_{form_id}_corpora.json"
            )["corpora"]
            form_gold = _load_json(
                self._legacy_reference_pack_root
                / "gold"
                / f"form_{form_id}_gold.json"
            )["gold"]
            selected_corpora = [form_corpora[index - 1] for index in indices]
            selected_ids = {str(row["corpus_id"]) for row in selected_corpora}
            selected_tasks = [
                row for row in form_tasks if str(row["corpus_id"]) in selected_ids
            ]
            selected_task_ids = {str(row["task_id"]) for row in selected_tasks}
            selected_gold = [
                row for row in form_gold if str(row["task_id"]) in selected_task_ids
            ]
            if (
                len(selected_tasks) != 21
                or len(selected_gold) != 21
                or any(len(row["documents"]) != 42 for row in selected_corpora)
            ):
                raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_LEGACY_SAMPLE_INVALID")
            adversarial_forms[form_id] = {
                "corpora": selected_corpora,
                "tasks": selected_tasks,
                "gold": selected_gold,
            }

        self._load_successor_scorer()
        self._successor_assets = {
            "manifest": manifest,
            "production": production,
            "gold": gold_rows,
            "corpus_by_id": corpus_by_id,
            "tasks_by_corpus": tasks_by_corpus,
            "protocol": protocol,
            "reference_pack_sha256": _file_sha256(
                self._reference_pack_root / "SHA256SUMS.txt"
            ),
            "legacy_reference_pack_sha256": legacy_sha256,
            "legacy_scorer": legacy_scorer,
            "adversarial_forms": adversarial_forms,
            "workload_shape": {
                "document_mean_chars": round(statistics.fmean(document_lengths), 6),
                "document_median_chars": round(statistics.median(document_lengths), 6),
                "document_max_chars": max(document_lengths),
                "query_mean_chars": round(statistics.fmean(query_lengths), 6),
                "query_median_chars": round(statistics.median(query_lengths), 6),
                "query_max_chars": max(query_lengths),
                "cjk_query_count": cjk_query_count,
            },
        }
        return self._successor_assets

    def plan(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        if node.get("node_id") != "chunk_embedding":
            return _not_available("WORKFLOW_MODEL_EXAM_DYNAMIC_NODE_UNSUPPORTED")
        profile_kind = self._profile_kind(target)
        if profile_kind is None:
            return _not_available("WORKFLOW_MODEL_EXAM_DYNAMIC_PROFILE_UNSUPPORTED")
        try:
            assets = self._load_assets()
        except (ImportError, OSError, ValueError, RuntimeError):
            return _not_available("WORKFLOW_MODEL_EXAM_Quality_ASSETS_INVALID")
        batch_size = self._batch_size(target)
        maximum_model_calls = self._physical_call_count(batch_size)
        manifest = assets["manifest"]
        scoring_protocol_sha256 = _file_sha256(
            self._reference_pack_root / "scoring" / "scoring_protocol.json"
        )
        comparison_basis = {
            "exam_category_id": "Quality_NEW_EMBEDDING_PRODUCTION_RECALL_REFERENCE_REGRESSION",
            "role_id": "EMBEDDING_TEXT",
            "reference_pack_sha256": assets["reference_pack_sha256"],
            "scoring_protocol_sha256": scoring_protocol_sha256,
            "scoring_projection_revision": self.SCORING_PROJECTION_REVISION,
            "sample_revision": self.SAMPLE_REVISION,
            "input_modality": "TEXT_ONLY",
        }
        comparison_cohort_id = "EMBEDDING_TEXT:" + _sha256(comparison_basis)
        adversarial_basis = {
            "exam_category_id": "Quality_EMBEDDING_TEXT_ADVERSARIAL_DIAGNOSTIC_SAMPLE",
            "role_id": "EMBEDDING_TEXT",
            "source_pack_sha256": assets["legacy_reference_pack_sha256"],
            "sample_revision": self.SAMPLE_REVISION,
        }
        adversarial_cohort_id = "EMBEDDING_TEXT_ADVERSARIAL:" + _sha256(
            adversarial_basis
        )
        plan: dict[str, Any] = {
            "schema_version": PLAN_SCHEMA,
            "status": "READY",
            "mode": self.MODE,
            "reason": "Quality_NEW_EMBEDDING_SUCCESSOR_REFERENCE_REGRESSION_READY",
            "node_id": "chunk_embedding",
            "executor_ref": self.EXECUTOR_REF,
            "profile_kind": profile_kind,
            "profile_ref": target["config_id"],
            "model_name": target["model_name"],
            "model_digest": target.get("model_digest"),
            "expected_dimension": self._expected_dimension(target),
            "settings_revision": context.get("settings_revision"),
            "settings_sha256": context.get("settings_sha256"),
            "reference_pack_id": manifest["pack_id"],
            "reference_pack_revision": manifest["pack_revision"],
            "reference_pack_sha256": assets["reference_pack_sha256"],
            "legacy_reference_pack_sha256": assets["legacy_reference_pack_sha256"],
            "scoring_protocol_sha256": scoring_protocol_sha256,
            "scoring_projection_revision": self.SCORING_PROJECTION_REVISION,
            "exam_category_id": comparison_basis["exam_category_id"],
            "role_id": comparison_basis["role_id"],
            "sample_revision": self.SAMPLE_REVISION,
            "comparison_cohort_id": comparison_cohort_id,
            "comparison_binding_sha256": _sha256(
                {**comparison_basis, "comparison_cohort_id": comparison_cohort_id}
            ),
            "adversarial_comparison_cohort_id": adversarial_cohort_id,
            "adversarial_comparison_binding_sha256": _sha256(
                {**adversarial_basis, "comparison_cohort_id": adversarial_cohort_id}
            ),
            "horizontal_comparison_eligible": True,
            "horizontal_comparison_allowed_only_with_same_cohort": True,
            "cross_category_comparison_forbidden": True,
            "global_ranking_forbidden": True,
            "reference_regression_only": True,
            "blind_holdout_eligible": False,
            "qualification_eligible": False,
            "production_primary_score": "PRODUCTION_RECALL_AT_15",
            "adversarial_score_is_independent": True,
            "single_aggregate_may_establish_fail": False,
            "pack_native_quality_threshold_defined": False,
            "quality_threshold_defined": True,
            "role_adequacy_policy": deepcopy(
                EMBEDDING_ROLE_ADEQUACY_POLICY
            ),
            "single_reference_regression_may_establish_model_fail": False,
            "production_input_count": self.PRODUCTION_INPUT_COUNT,
            "adversarial_input_count": self.ADVERSARIAL_INPUT_COUNT,
            "repeatability_input_count": self.REPEATABILITY_INPUT_COUNT,
            "maximum_model_calls": maximum_model_calls,
            "maximum_inputs_per_model_request": batch_size,
            "batch_equivalence_revision": self.BATCH_EQUIVALENCE_REVISION,
            "repeatability_selection_revision": self.REPEATABILITY_SELECTION_REVISION,
            "workload_shape": deepcopy(assets["workload_shape"]),
            "budget_cap_cny": (
                self.API_BUDGET_CAP_CNY if profile_kind == "API" else None
            ),
            "cost_semantics": (
                "MODEL_SPECIFIC_EMBEDDING_INPUT_PRICE_ESTIMATE"
                if profile_kind == "API"
                else "LOCAL_COMPUTE_NO_PROVIDER_API_COST"
            ),
            "authorization_schema_version": AUTHORIZATION_SCHEMA,
            "external_network_calls": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
        }
        plan["plan_sha256"] = _sha256(plan)
        return plan

    @staticmethod
    def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
        if len(left) != len(right) or not left:
            raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_VECTOR_DIMENSION_INVALID")
        dot = math.fsum(float(a) * float(b) for a, b in zip(left, right))
        left_norm = math.sqrt(math.fsum(float(value) ** 2 for value in left))
        right_norm = math.sqrt(math.fsum(float(value) ** 2 for value in right))
        if left_norm <= 0.0 or right_norm <= 0.0:
            raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_ZERO_NORM_VECTOR")
        return dot / (left_norm * right_norm)

    @classmethod
    def _rank_ids(
        cls,
        document_ids: Sequence[str],
        document_vectors: Sequence[Sequence[float]],
        query_vector: Sequence[float],
    ) -> list[str]:
        if len(document_ids) != len(document_vectors):
            raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_RANKING_INPUT_INVALID")
        scored = [
            (cls._cosine(vector, query_vector), index, str(document_id))
            for index, (document_id, vector) in enumerate(
                zip(document_ids, document_vectors)
            )
        ]
        scored.sort(key=lambda row: (-row[0], row[1]))
        return [row[2] for row in scored]

    @staticmethod
    def _state_cost(state: Mapping[str, Any], profile_kind: str) -> float | None:
        if profile_kind == "LOCAL":
            return 0.0
        return (
            round(float(state["estimated_cost_cny"]), 9)
            if state.get("cost_estimate_complete") is True
            else None
        )

    def execute(
        self,
        node: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, Any],
        *,
        authorization: Mapping[str, Any] | None,
        structured_chat: Callable[..., Mapping[str, Any]],
        embedding_call: Callable[..., Mapping[str, Any]],
    ) -> dict[str, Any]:
        del structured_chat
        started = monotonic()
        plan = self.plan(node, target, context)
        requested_model = (
            str(target.get("model_name"))
            if isinstance(target.get("model_name"), str)
            else None
        )
        if plan.get("status") != "READY":
            return _not_run(
                str(plan.get("reason") or "WORKFLOW_MODEL_EXAM_NOT_AVAILABLE"),
                requested_model=requested_model,
            )
        reason = self._authorization_reason(authorization, plan)
        if reason is not None:
            return _not_run(reason, requested_model=requested_model)
        run_id = "workflow-exam-" + uuid.uuid4().hex
        run_root = self.scratch_root / "workflow_exams" / run_id
        run_root.mkdir(parents=True, exist_ok=False)
        _write_json_create_only(run_root / "plan.json", plan)
        _write_json_create_only(
            run_root / "authorization_receipt.json",
            {
                **deepcopy(dict(authorization or {})),
                "run_id": run_id,
                "accepted": True,
                "accepted_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        run = _EmbeddingExamRun(
            target=target, embedding_call=embedding_call, plan=plan, requested_model=requested_model,
            started=started, run_id=run_id, run_root=run_root,
            state={
            "request_attempts": 0,
            "provider_calls": 0,
            "external_model_calls": 0,
            "external_network_calls": 0,
            "external_process_launches": 0,
            "local_metadata_calls": 0,
            "estimated_cost_cny": 0.0,
            "cost_estimate_complete": True,
            "token_usage": {"prompt_tokens": 0, "completion_tokens": 0},
        },
        )
        try:
            self._embedding_exam_rows(run)

            run.call_index = 0
            run.dimension = run.plan["expected_dimension"]
            run.observed_returned_model = None

            self._run_embedding_lanes(run)

            return self._score_embedding_exam(run)
        except Exception as error:
            reason = str(error) or type(error).__name__
            if len(reason) > 200:
                reason = type(error).__name__
            estimated_cost = self._state_cost(run.state, str(run.plan["profile_kind"]))
            failure = {
                "schema_version": "WorkflowModelExamRunFailure-v1",
                "status": "NOT_ASSESSED",
                "execution_outcome": classify_execution_outcome(
                    {"status": "ERROR", "reason": reason}
                ).value,
                "scorability": "NOT_ASSESSED",
                "quality_verdict": "NOT_ASSESSED",
                "reason": reason,
                "run_id": run.run_id,
                "plan_sha256": run.plan["plan_sha256"],
                "request_attempts": run.state["request_attempts"],
                "provider_calls": run.state["provider_calls"],
                "external_model_calls": run.state["external_model_calls"],
                "external_network_calls": run.state["external_network_calls"],
                "external_process_launches": run.state["external_process_launches"],
                "local_metadata_calls": run.state["local_metadata_calls"],
                "token_usage": run.state["token_usage"],
                "estimated_cost_cny": estimated_cost,
                "score": None,
                "provider_received_gold": False,
            }
            evidence = _write_json_create_only(
                run.run_root / "exam_failure.json", failure
            )
            result = _not_run(reason, requested_model=run.requested_model)
            result.update(
                duration_ms=max(0, int((monotonic() - run.started) * 1000)),
                execution_outcome=failure["execution_outcome"],
                scorability="NOT_ASSESSED",
                quality_verdict="NOT_ASSESSED",
                exam_run_id=run.run_id,
                exam_run_root=str(run.run_root.resolve()),
                exam_evidence_sha256=evidence["sha256"],
                cost_semantics=run.plan["cost_semantics"],
                request_attempts=run.state["request_attempts"],
                provider_calls=run.state["provider_calls"],
                external_model_calls=run.state["external_model_calls"],
                external_network_calls=run.state["external_network_calls"],
                external_process_launches=run.state["external_process_launches"],
                local_metadata_calls=run.state["local_metadata_calls"],
                token_usage=run.state["token_usage"],
                estimated_cost_cny=estimated_cost,
                provider_received_gold=False,
            )
            return result

    def _embedding_exam_rows(self, run: _EmbeddingExamRun) -> None:
        """The production, adversarial and repeat rows to embed, built from the exam assets."""
        run.assets = self._load_assets()
        production = run.assets["production"]
        run.production_tasks = production["tasks"]
        tasks_by_corpus = run.assets["tasks_by_corpus"]
        run.production_rows = []
        for corpus in production["corpora"]:
            corpus_id = str(corpus["corpus_id"])
            run.production_rows.extend(
                {
                    "key": f"PROD:D:{row['unit_id']}",
                    "item_id": str(row["unit_id"]),
                    "text": str(row["text"]),
                    "corpus_id": corpus_id,
                }
                for row in corpus["documents"]
            )
            run.production_rows.extend(
                {
                    "key": f"PROD:Q:{row['task_id']}",
                    "item_id": str(row["task_id"]),
                    "text": str(row["query"]),
                    "corpus_id": corpus_id,
                }
                for row in tasks_by_corpus[corpus_id]
            )
        run.adversarial_rows = []
        run.adversarial_tasks_by_form = {}
        for form_id in ("A", "B"):
            form = run.assets["adversarial_forms"][form_id]
            grouped: dict[str, list[Mapping[str, Any]]] = {}
            for task in form["tasks"]:
                grouped.setdefault(str(task["corpus_id"]), []).append(task)
            run.adversarial_tasks_by_form[form_id] = grouped
            for corpus in form["corpora"]:
                corpus_id = str(corpus["corpus_id"])
                run.adversarial_rows.extend(
                    {
                        "key": f"ADV:{form_id}:D:{row['unit_id']}",
                        "item_id": str(row["unit_id"]),
                        "text": str(row["text"]),
                        "corpus_id": corpus_id,
                        "form_id": form_id,
                    }
                    for row in corpus["documents"]
                )
                run.adversarial_rows.extend(
                    {
                        "key": f"ADV:{form_id}:Q:{row['task_id']}",
                        "item_id": str(row["task_id"]),
                        "text": str(row["query"]),
                        "corpus_id": corpus_id,
                        "form_id": form_id,
                    }
                    for row in grouped[corpus_id]
                )
        run.repeat_rows = []
        for corpus in production["corpora"]:
            corpus_id = str(corpus["corpus_id"])
            for row in corpus["documents"][:2]:
                source_key = f"PROD:D:{row['unit_id']}"
                run.repeat_rows.append(
                    {
                        "key": f"REPEAT:{source_key}",
                        "source_key": source_key,
                        "item_id": str(row["unit_id"]),
                        "text": str(row["text"]),
                        "corpus_id": corpus_id,
                    }
                )
            for row in tasks_by_corpus[corpus_id][:2]:
                source_key = f"PROD:Q:{row['task_id']}"
                run.repeat_rows.append(
                    {
                        "key": f"REPEAT:{source_key}",
                        "source_key": source_key,
                        "item_id": str(row["task_id"]),
                        "text": str(row["query"]),
                        "corpus_id": corpus_id,
                    }
                )
        if (
            len(run.production_rows) != self.PRODUCTION_INPUT_COUNT
            or len(run.adversarial_rows) != self.ADVERSARIAL_INPUT_COUNT
            or len(run.repeat_rows) != self.REPEATABILITY_INPUT_COUNT
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_INPUT_TOPOLOGY_INVALID")

    def _run_embedding_lanes(self, run: _EmbeddingExamRun) -> None:
        """Embed the production, adversarial and repeat lanes with the model under test."""
        def execute_lane(
            lane_id: str,
            rows: Sequence[Mapping[str, str]],
        ) -> dict[str, list[float]]:
            collected: dict[str, list[float]] = {}
            batch_size = int(run.plan["maximum_inputs_per_model_request"])
            for start_index in range(0, len(rows), batch_size):
                batch_rows = rows[start_index : start_index + batch_size]
                inputs = [str(row["text"]) for row in batch_rows]
                input_ids = [str(row["key"]) for row in batch_rows]
                run.call_index += 1
                behavior_sha256 = _sha256(
                    {
                        "model": run.requested_model,
                        "inputs": inputs,
                        "purpose": "workflow_model_exam",
                    }
                )
                claim = {
                    "schema_version": "WorkflowEmbeddingPreSendClaim-v2",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "call_id": f"call-{run.call_index:03d}",
                    "call_kind": "EMBEDDING_SUCCESSOR_BATCH",
                    "lane_id": lane_id,
                    "profile_kind": run.plan["profile_kind"],
                    "profile_ref": run.plan["profile_ref"],
                    "purpose": "workflow_model_exam",
                    "route": (
                        "EXISTING_SETTINGS_API_TRANSPORT"
                        if run.plan["profile_kind"] == "API"
                        else "OLLAMA_LOOPBACK"
                    ),
                    "requested_model": run.requested_model,
                    "model_digest": run.target.get("model_digest"),
                    "input_count": len(inputs),
                    "input_id_exact_set_sha256": _sha256(input_ids),
                    "input_exact_set_sha256": _sha256(inputs),
                    "behavior_sha256": behavior_sha256,
                    "plan_sha256": run.plan["plan_sha256"],
                    "provider_received_gold": False,
                }
                claim_receipt = _write_json_create_only(
                    run.run_root / "claims" / f"call_{run.call_index:03d}.json",
                    claim,
                )
                result = dict(
                    run.embedding_call(
                        profile_kind=str(run.target["kind"]),
                        service=run.target,
                        model=run.target,
                        inputs=inputs,
                        purpose="workflow_model_exam",
                        timeout_seconds=300,
                    )
                )
                for field in (
                    "provider_calls",
                    "external_model_calls",
                    "external_network_calls",
                    "external_process_launches",
                    "local_metadata_calls",
                ):
                    run.state[field] += int(result.get(field) or 0)
                run.state["request_attempts"] += int(
                    result.get("external_model_calls") or 0
                )
                receipt = result.get("execution_receipt")
                if isinstance(receipt, Mapping):
                    estimate = receipt.get("estimated_cost_cny")
                    if isinstance(estimate, (int, float)) and not isinstance(estimate, bool):
                        run.state["estimated_cost_cny"] += float(estimate)
                    elif run.plan["profile_kind"] == "API":
                        run.state["cost_estimate_complete"] = False
                    usage = receipt.get("token_usage")
                    if isinstance(usage, Mapping):
                        for field in ("prompt_tokens", "completion_tokens"):
                            value = usage.get(field)
                            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                                run.state["token_usage"][field] = run.state["token_usage"][field] + value if run.state["token_usage"][field] is not None else None
                if result.get("status") != "PASS":
                    raise ValueError(str(result.get("reason") or "EMBEDDING_CALL_FAILED"))
                if int(result.get("external_model_calls") or 0) != 1:
                    raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_PHYSICAL_CALL_BINDING_INVALID")
                returned_model = result.get("returned_model")
                if returned_model != run.requested_model:
                    raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_RETURNED_MODEL_MISMATCH")
                if run.observed_returned_model is None:
                    run.observed_returned_model = str(returned_model)
                elif run.observed_returned_model != returned_model:
                    raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_RETURNED_MODEL_DRIFT")
                if (
                    not isinstance(receipt, Mapping)
                    or receipt.get("behavior_sha256") != behavior_sha256
                ):
                    raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_BEHAVIOR_RECEIPT_INVALID")
                vectors = result.get("vectors")
                if not isinstance(vectors, list):
                    raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_VECTORS_MISSING")
                observed_dimension = run.assets["legacy_scorer"].validate_vectors(
                    vectors,
                    len(inputs),
                    expected_dim=run.dimension,
                )
                if run.dimension is None:
                    run.dimension = observed_dimension
                elif run.dimension != observed_dimension:
                    raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_DIMENSION_DRIFT")
                for input_id, vector in zip(input_ids, vectors):
                    if input_id in collected:
                        raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_INPUT_ID_DUPLICATE")
                    collected[input_id] = [float(value) for value in vector]
                _write_json_create_only(
                    run.run_root / "receipts" / f"call_{run.call_index:03d}.json",
                    {
                        "schema_version": "WorkflowEmbeddingBatchReceipt-v3",
                        "call_id": claim["call_id"],
                        "lane_id": lane_id,
                        "input_count": len(inputs),
                        "input_id_exact_set_sha256": claim["input_id_exact_set_sha256"],
                        "response_vector_sha256": _sha256(vectors),
                        "dimension": run.dimension,
                        "returned_model": returned_model,
                        "physical_model_calls": 1,
                        "pre_send_claim_sha256": claim_receipt["sha256"],
                        "provider_received_gold": False,
                        "execution_receipt": deepcopy(dict(receipt)),
                    },
                )
            return collected

        run.production_vectors = execute_lane("PRODUCTION_PRIMARY", run.production_rows)
        run.adversarial_vectors = execute_lane("ADVERSARIAL_DIAGNOSTIC", run.adversarial_rows)
        run.repeat_vectors = execute_lane("REPEATABILITY", run.repeat_rows)
        if (
            run.call_index != int(run.plan["maximum_model_calls"])
            or run.state["request_attempts"] != int(run.plan["maximum_model_calls"])
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_EMBEDDING_CALL_COUNT_MISMATCH")

    def _score_embedding_exam(self, run: _EmbeddingExamRun) -> Any:
        """Score the rankings, repeatability and role adequacy, write the evidence and the public result,
        and return the result."""
        production_rankings: dict[str, list[str]] = {}
        for task in run.production_tasks:
            corpus = run.assets["corpus_by_id"][str(task["corpus_id"])]
            document_ids = [str(row["unit_id"]) for row in corpus["documents"]]
            document_vectors = [
                run.production_vectors[f"PROD:D:{unit_id}"]
                for unit_id in document_ids
            ]
            production_rankings[str(task["task_id"])] = self._rank_ids(
                document_ids,
                document_vectors,
                run.production_vectors[f"PROD:Q:{task['task_id']}"],
            )
        production_score = self._load_successor_scorer().score_track(
            run.assets["gold"], production_rankings
        )

        adversarial_scores: dict[str, dict[str, Any]] = {}
        adversarial_rankings: dict[str, list[str]] = {}
        for form_id in ("A", "B"):
            form = run.assets["adversarial_forms"][form_id]
            grouped = run.adversarial_tasks_by_form[form_id]
            form_rankings: dict[str, list[str]] = {}
            for corpus in form["corpora"]:
                corpus_id = str(corpus["corpus_id"])
                document_ids = [str(row["unit_id"]) for row in corpus["documents"]]
                document_vectors = [
                    run.adversarial_vectors[f"ADV:{form_id}:D:{unit_id}"]
                    for unit_id in document_ids
                ]
                for task in grouped[corpus_id]:
                    ranked = self._rank_ids(
                        document_ids,
                        document_vectors,
                        run.adversarial_vectors[
                            f"ADV:{form_id}:Q:{task['task_id']}"
                        ],
                    )
                    form_rankings[str(task["task_id"])] = ranked
                    adversarial_rankings[
                        f"{form_id}:{task['task_id']}"
                    ] = ranked
            adversarial_scores[form_id] = run.assets["legacy_scorer"].score_form(
                form["gold"], form_rankings
            )
        adversarial_mean = statistics.fmean(
            float(adversarial_scores[form_id]["score"])
            for form_id in ("A", "B")
        )

        similarities = [
            self._cosine(
                run.production_vectors[str(row["source_key"])],
                run.repeat_vectors[str(row["key"])],
            )
            for row in run.repeat_rows
        ]
        repeatability = {
            "schema_version": "EmbeddingRepeatabilityScore-v1",
            "selection_revision": self.REPEATABILITY_SELECTION_REVISION,
            "sample_count": len(similarities),
            "mean_vector_cosine": round(statistics.fmean(similarities), 12),
            "minimum_vector_cosine": round(min(similarities), 12),
            "below_0_999999_count": sum(value < 0.999999 for value in similarities),
            "qualification_threshold_defined": False,
        }
        role_adequacy = assess_embedding_role_adequacy(
            production_score,
            repeatability,
        )
        cap = run.plan.get("budget_cap_cny")
        estimated_cost = self._state_cost(run.state, str(run.plan["profile_kind"]))
        if cap is not None and (
            estimated_cost is None or estimated_cost > float(cap)
        ):
            raise ValueError("WORKFLOW_MODEL_EXAM_COST_CAP_NOT_VERIFIABLE_OR_EXCEEDED")

        _write_json_create_only(
            run.run_root / "rankings" / "production_rankings.json",
            {
                "schema_version": "EmbeddingProductionRankings-v1",
                "rankings": production_rankings,
                "provider_received_gold": False,
            },
        )
        _write_json_create_only(
            run.run_root / "rankings" / "adversarial_rankings.json",
            {
                "schema_version": "EmbeddingAdversarialRankings-v1",
                "rankings": adversarial_rankings,
                "provider_received_gold": False,
            },
        )
        _write_json_create_only(
            run.run_root / "scores" / "production_score.local.json",
            production_score,
        )
        _write_json_create_only(
            run.run_root / "scores" / "adversarial_score.local.json",
            adversarial_scores,
        )
        _write_json_create_only(
            run.run_root / "scores" / "repeatability.json",
            repeatability,
        )

        production_score_exact = float(
            production_score["production_recall_score"]
        )
        embedding_diagnostics = {
            "schema_version": "QualityNewEmbeddingLiveDiagnostic-v2",
            "score_views": {
                "production_primary": {
                    "kind": "PRODUCTION_RECALL_AT_15",
                    "score_exact": production_score_exact,
                    "critical_evidence_recall_at_15": production_score[
                        "critical_evidence_recall_at_15"
                    ],
                    "facet_coverage_at_15": production_score[
                        "facet_coverage_at_15"
                    ],
                    "source_scope_integrity_at_15": production_score[
                        "source_scope_integrity_at_15"
                    ],
                    "clean_control_recall_at_10": production_score[
                        "clean_control_recall_at_10"
                    ],
                    "qualification_eligible": False,
                    "quality_verdict": role_adequacy["quality_verdict"],
                },
                "adversarial_diagnostic": {
                    "kind": "LEGACY_NONLINEAR_DEDUCTION_SAMPLE",
                    "score_exact": round(adversarial_mean, 6),
                    "form_scores": {
                        form_id: adversarial_scores[form_id]["score"]
                        for form_id in ("A", "B")
                    },
                    "independent_from_production_primary": True,
                    "qualification_eligible": False,
                },
                "repeatability": repeatability,
            },
            "workload_shape": deepcopy(run.assets["workload_shape"]),
            "selection_contract": {
                "single_aggregate_is_sufficient_for_selection": False,
                "production_and_adversarial_scores_must_not_be_combined": True,
                "repeatability_and_operational_profile_required": True,
                "meaningful_pairwise_separation_must_be_demonstrated": True,
                "pack_native_quality_threshold_defined": False,
                "quality_threshold_defined": True,
                "role_adequacy_policy_revision": (
                    EMBEDDING_ROLE_ADEQUACY_POLICY["revision"]
                ),
                "formal_qualification_eligible": False,
                "model_failure_established": False,
            },
            "role_adequacy_assessment": role_adequacy,
            "execution_provenance": {
                "physical_model_call_target": run.plan["maximum_model_calls"],
                "maximum_inputs_per_model_request": run.plan[
                    "maximum_inputs_per_model_request"
                ],
                "production_input_count": self.PRODUCTION_INPUT_COUNT,
                "adversarial_input_count": self.ADVERSARIAL_INPUT_COUNT,
                "repeatability_input_count": self.REPEATABILITY_INPUT_COUNT,
                "historical_quality_call_count_relabelled": False,
            },
            "cross_category_comparison_forbidden": True,
            "global_ranking_forbidden": True,
        }
        embedding_diagnostics["diagnostic_sha256"] = _sha256(
            embedding_diagnostics
        )
        public_result = {
            "schema_version": "WorkflowModelExamRunResult-v1",
            "status": "PASS",
            "execution_outcome": "COMPLETED",
            "scorability": "SCOREABLE",
            "raw_quality_candidate_verdict": role_adequacy[
                "quality_verdict"
            ],
            "reason": "Quality_NEW_EMBEDDING_SUCCESSOR_TECHNICALLY_COMPLETED",
            "run_id": run.run_id,
            "plan_sha256": run.plan["plan_sha256"],
            "requested_model": run.requested_model,
            "returned_model": run.observed_returned_model,
            "score": int(round(production_score_exact)),
            "score_exact": round(production_score_exact, 6),
            "score_semantics": (
                "FIRST_STAGE_HIGH_RECALL_AT_15_ROLE_ADEQUACY"
            ),
            "production_recall_score_exact": round(production_score_exact, 6),
            "adversarial_diagnostic_score_exact": round(adversarial_mean, 6),
            "quality_verdict": role_adequacy["quality_verdict"],
            "acceptance_verdict": "NOT_ASSESSED",
            "quality_threshold_defined": True,
            "score_is_descriptive_within_category": False,
            "role_adequacy_assessment": role_adequacy,
            "model_fail_verdict": "NOT_ESTABLISHED",
            "model_fail_established": False,
            "exam_category_id": run.plan["exam_category_id"],
            "role_id": run.plan["role_id"],
            "sample_revision": run.plan["sample_revision"],
            "comparison_cohort_id": run.plan["comparison_cohort_id"],
            "comparison_binding_sha256": run.plan["comparison_binding_sha256"],
            "adversarial_comparison_cohort_id": run.plan[
                "adversarial_comparison_cohort_id"
            ],
            "horizontal_comparison_eligible": True,
            "horizontal_comparison_allowed_only_with_same_cohort": True,
            "cross_category_comparison_forbidden": True,
            "global_ranking_forbidden": True,
            "failed_exam_item_count": int(production_score["critical_miss_count"]),
            "critical_miss_count": int(production_score["critical_miss_count"]),
            "failed_exam_item_count_semantics": (
                "PRODUCTION_DIAGNOSTIC_CRITICAL_MISS_NOT_ADMISSION_FAIL"
            ),
            "dimension": run.dimension,
            "request_attempts": run.state["request_attempts"],
            "provider_calls": run.state["provider_calls"],
            "external_model_calls": run.state["external_model_calls"],
            "external_network_calls": run.state["external_network_calls"],
            "external_process_launches": run.state["external_process_launches"],
            "local_metadata_calls": run.state["local_metadata_calls"],
            "token_usage": run.state["token_usage"],
            "estimated_cost_cny": estimated_cost,
            "actual_cost": None,
            "cost_semantics": run.plan["cost_semantics"],
            "reference_regression_only": True,
            "blind_holdout_eligible": False,
            "qualification_eligible": False,
            "provider_received_answer_key": False,
            "provider_received_gold": False,
            "embedding_diagnostics": embedding_diagnostics,
        }
        evidence = _write_json_create_only(
            run.run_root / "exam_result.json", public_result
        )
        return {
            **public_result,
            "schema_version": RESULT_SCHEMA,
            "kind": "WORKFLOW_NODE",
            "duration_ms": max(0, int((monotonic() - run.started) * 1000)),
            "exam_mode": run.plan["mode"],
            "exam_run_id": run.run_id,
            "exam_run_root": str(run.run_root.resolve()),
            "exam_evidence_sha256": evidence["sha256"],
        }
