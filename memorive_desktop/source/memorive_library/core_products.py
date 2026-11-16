"""Document processing results as library rows: the files each finished document job delivered.

ProductApi builds one CoreProducts at startup. LibraryProductController lists rows() among its
entries (core_artifact_provider); research's periodic reports read job_rows() (ResearchHost).
It asks the settings service for the jobs and their bindings and checks each delivered file in the
job folders (Core_JOBS); a job whose files cannot be shown gets one row saying so, written to
<state folder>/library/core-unavailable.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
import re
import stat
from typing import Any, Callable, Mapping


class CoreProducts:
    """The library rows of document jobs. service is the settings service; profile_path resolves a
    profile folder; state_dir is the app's state folder; write_json_file and is_reparse_point are
    ProductApi's file helpers."""

    def __init__(self, *, service: Any, profile_path: Callable[[str], Path], state_dir: Path,
                 write_json_file: Callable[[Path, dict], None], is_reparse_point: Callable[[Path], bool]):
        self.service = service
        self.profile_path = profile_path
        self.state_dir = state_dir
        self.write_json_file = write_json_file
        self.is_reparse_point = is_reparse_point

    def rows(self) -> list[dict[str, Any]]:
        """Project every job independently so one legacy defect cannot fail the page."""

        rows: list[dict[str, Any]] = []
        for core_job in self.job_rows():
            try:
                rows.extend(self.rows_for_jobs([core_job]))
            except (OSError, RuntimeError, ValueError) as error:
                rows.append(self.unavailable_row(core_job, error))
        return rows

    def job_rows(self) -> list[dict[str, Any]]:
        """Return durable, successfully delivered Core products.

        Current Task intentionally retains completed cards for only a short UI
        window. Library is a durable product index and must therefore enumerate
        Application Service jobs directly instead of inheriting that transient
        retention policy.
        """

        projection = self.service.call("list_jobs", {})
        source = projection.get("jobs", []) if isinstance(projection, Mapping) else []
        if not isinstance(source, list):
            raise RuntimeError("Core_LIBRARY_JOB_LIST_INVALID")
        rows: list[dict[str, Any]] = []
        for status in source:
            if not isinstance(status, Mapping) or status.get("job_type") != "memorive-core":
                continue
            job_id = str(status.get("job_id") or "")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}", job_id):
                raise RuntimeError("Core_JOB_ID_INVALID")
            job = self.service.call("get_job", {"job_id": job_id})
            bindings = job.get("artifact_bindings") or []
            if not isinstance(bindings, list) or not bindings:
                continue
            completed = str(job.get("control_state") or "").upper() == "SUCCEEDED"
            has_review = any(isinstance(binding, Mapping) and
                str(binding.get('artifact_id') or '').startswith(('core-data-review-','core-logic-review-'))
                for binding in bindings)
            if not completed and not has_review:
                continue
            # An E2 report is independently published. Incomplete jobs expose
            # only those immutable reports, never legacy partial folder output.
            resolved = (
                job.get("snapshots", {})
                .get("resolved_task_input", {})
                .get("content", {})
            )
            if not isinstance(resolved, Mapping):
                resolved = {}
            rows.append(
                {
                    "job_id": job_id,
                    "attempt_id": str(job.get("attempt_id") or status.get("attempt_id") or ""),
                    "control_state": str(
                        job.get("control_state")
                        or status.get("job_control_state")
                        or "UNKNOWN"
                    ).upper(),
                    "display_name": str(
                        resolved.get("display_name")
                        or status.get("display_name")
                        or job_id
                    )[:180],
                    "source_sha256": str(
                        resolved.get("content_sha256")
                        or job.get("source_sha256")
                        or ""
                    ).upper(),
                    "created_at": job.get("created_at") or status.get("created_at"),
                    "updated_at": job.get("updated_at") or status.get("updated_at"),
                    "artifact_count": len(bindings),
                    "context_pack": None,
                    "review_only": not completed,
                    "raw_private_content_included": False,
                }
            )
        rows.sort(
            key=lambda row: (
                str(row.get("updated_at") or ""),
                str(row.get("job_id") or ""),
            ),
            reverse=True,
        )
        return rows

    def rows_for_jobs(
        self, core_jobs: list[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        """Resolve selected Core bindings to verified controlled files."""
        from memorive_inbox.contracts import IMPORTABLE_EXTENSIONS

        kind_names = {
            "CORE_DATA_REVIEW":"数据分析摘要", "CORE_LOGIC_REVIEW":"逻辑分析报告",
            "CORE_DATA_REVIEW_RECORD":"数据核查记录", "CORE_LOGIC_REVIEW_RECORD":"逻辑核查记录",
            "CORE_SOURCE_DOCUMENT": "原始文献",
            "CORE_RAW_DOCUMENT": "原始转换文档",
            "CORE_CLEAN_DOCUMENT": "清洗文档",
            "CORE_CHUNKS": "Chunks",
            "CORE_CARD": "Card",
            "CORE_CONTEXT_PACK": "Context Pack",
            "CORE_ANALYSIS": "Analysis",
            "CORE_JUDGMENT_REVIEW": "判断类异源校对",
            "CORE_HUMAN_REVIEW": "人工最终复核包",
            "CORE_FOLDER_MANIFEST": "文件夹清单",
        }
        rows: list[dict[str, Any]] = []
        for core_job in core_jobs:
            job_id = core_job["job_id"]
            review_only = core_job.get('review_only') is True
            review_prefixes = ('core-data-review-', 'core-logic-review-')
            job = self.service.call("get_job", {"job_id": job_id})
            artifact_root = (
                self.profile_path("Core_JOBS")
                / job_id
                / "artifacts"
            ).resolve(strict=False)
            root = artifact_root.resolve(strict=True)
            resolved_input = (
                job.get("snapshots", {})
                .get("resolved_task_input", {})
                .get("content", {})
            )
            source_document: dict[str, Any] | None = None
            legacy_source_error_code: str | None = None
            legacy_source_candidate: Path | None = None
            legacy_source_sha256 = ""
            if isinstance(resolved_input, Mapping):
                source_locator = str(resolved_input.get("bound_locator") or "").replace(
                    "\\", "/"
                )
                source_sha256 = str(
                    resolved_input.get("content_sha256")
                    or core_job.get("source_sha256")
                    or job.get("source_sha256")
                    or ""
                ).upper()
                legacy_source_sha256 = source_sha256
                if source_locator or source_sha256:
                    source_parts = source_locator.split("/")
                    if (
                        not source_locator
                        or source_locator.startswith("/")
                        or any(
                            part in {"", ".", ".."} or ":" in part
                            for part in source_parts
                        )
                        or re.fullmatch(r"[A-F0-9]{64}", source_sha256) is None
                    ):
                        legacy_source_error_code = "Core_LIBRARY_SOURCE_BINDING_INVALID"
                    else:
                        inbox_root = self.profile_path("INBOX").resolve(strict=True)
                        source_candidate = inbox_root.joinpath(*source_parts)
                        legacy_source_candidate = source_candidate
                        try:
                            source_attributes = source_candidate.lstat()
                            source_target = source_candidate.resolve(strict=True)
                        except OSError as error:
                            legacy_source_error_code = (
                                "Core_LIBRARY_LEGACY_PATH_UNAVAILABLE"
                                if getattr(error, "winerror", None) in {206, 3}
                                else "Core_LIBRARY_SOURCE_UNAVAILABLE"
                            )
                        else:
                            reparse_mask = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                            if (
                                source_candidate.is_symlink()
                                or bool(
                                    getattr(source_attributes, "st_file_attributes", 0)
                                    & reparse_mask
                                )
                                or not source_target.is_relative_to(inbox_root)
                                or not source_target.is_file()
                                or source_target.suffix.casefold() not in (IMPORTABLE_EXTENSIONS if review_only else {'.pdf'})
                            ):
                                legacy_source_error_code = "Core_LIBRARY_SOURCE_PATH_INVALID"
                            else:
                                observed_source_sha256 = hashlib.sha256(
                                    source_target.read_bytes()
                                ).hexdigest().upper()
                                if observed_source_sha256 != source_sha256:
                                    legacy_source_error_code = "Core_LIBRARY_SOURCE_HASH_MISMATCH"
                                else:
                                    source_document = {
                                        "source_root": str(inbox_root),
                                        "private_path": str(source_target),
                                        "sha256": source_sha256,
                                    }
            verified_bindings: list[dict[str, Any]] = []
            for binding in job.get("artifact_bindings") or []:
                if not isinstance(binding, Mapping):
                    continue
                source_artifact_id = str(binding.get("artifact_id") or "")
                if review_only and not source_artifact_id.startswith(review_prefixes) and source_artifact_id != 'core-source-document':
                    continue
                locator = str(binding.get("locator") or "")
                prefix = f"core-artifact:{job_id}:"
                if not locator.startswith(prefix):
                    raise RuntimeError("Core_LIBRARY_ARTIFACT_LOCATOR_INVALID")
                relative = locator[len(prefix) :].replace("\\", "/")
                parts = relative.split("/")
                if (
                    not relative
                    or relative.startswith("/")
                    or any(part in {"", ".", ".."} for part in parts)
                    or any(":" in part for part in parts)
                ):
                    raise RuntimeError("Core_LIBRARY_ARTIFACT_RELATIVE_PATH_INVALID")
                target = artifact_root.joinpath(*parts).resolve(strict=True)
                if (
                    not target.is_relative_to(root)
                    or not target.is_file()
                    or target.is_symlink()
                    or self.is_reparse_point(target)
                ):
                    raise RuntimeError("Core_LIBRARY_ARTIFACT_PATH_INVALID")
                observed = hashlib.sha256(target.read_bytes()).hexdigest().upper()
                expected = str(binding.get("sha256") or "").upper()
                if not re.fullmatch(r"[A-F0-9]{64}", expected) or observed != expected:
                    raise RuntimeError("Core_LIBRARY_ARTIFACT_HASH_MISMATCH")
                verified_bindings.append(
                    {
                        "binding": binding,
                        "artifact_id": source_artifact_id,
                        "relative_path": relative,
                        "target": target,
                        "sha256": observed,
                    }
                )

            legacy_stage_directories = {
                "analysis", "card", "context", "document", "review", "verification"
            }
            projection: dict[str, Any] | None = None
            if not review_only and verified_bindings and (
                source_document is not None
                or any(
                str(row["relative_path"]).split("/", 1)[0] in legacy_stage_directories
                for row in verified_bindings
                )
            ):
                summary: dict[str, Any] = {}
                summary_path = artifact_root.parent / "core_execution_summary.json"
                if summary_path.is_file() and not summary_path.is_symlink():
                    try:
                        parsed = json.loads(summary_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError) as error:
                        raise RuntimeError("Core_FOLDER_PROJECTION_SUMMARY_INVALID") from error
                    if isinstance(parsed, dict):
                        summary = parsed
                folder_module = importlib.import_module("memorive.platform.folders")
                projection = folder_module.ensure_legacy_core_projection(
                    artifact_root=artifact_root,
                    job_id=job_id,
                    paper_id=str(
                        summary.get("paper_id")
                        or core_job.get("paper_id")
                        or ""
                    ),
                    title=str(core_job["display_name"]),
                    source_sha256=(
                        str(core_job.get("source_sha256") or job.get("source_sha256") or "")
                        or None
                    ),
                    source_document=source_document,
                    bindings=[
                        {
                            "artifact_id": row["artifact_id"],
                            "relative_path": row["relative_path"],
                            "sha256": row["sha256"],
                        }
                        for row in verified_bindings
                        # E2 supplements have their own immutable registry. They
                        # do not rewrite the completed mainline folder manifest.
                        if not str(row['artifact_id']).startswith(('core-data-review-','core-logic-review-','core-structure-'))
                    ],
                )

            source_target: Path | None = None
            source_content_sha256 = ""
            if projection is not None and projection.get("source_document") is not None:
                source_value = projection["canonical_path_by_artifact_id"].get(
                    "core-source-document"
                )
                source_content_sha256 = str(
                    projection["source_document"].get("sha256") or ""
                ).upper()
                if source_value:
                    source_target = Path(source_value).resolve(strict=True)
                    if (
                        not source_target.is_relative_to(root)
                        or not source_target.is_file()
                        or source_target.is_symlink()
                        or hashlib.sha256(source_target.read_bytes()).hexdigest().upper()
                        != source_content_sha256
                    ):
                        raise RuntimeError("Core_FOLDER_PROJECTION_SOURCE_INVALID")

            # Canonical jobs already carry the original PDF as a hash-verified
            # artifact. Their input snapshot need not retain an inbox locator.
            # Bind every product to that PDF, never to a display title or to its
            # own (different) artifact hash.
            if source_target is None:
                originals = [row for row in verified_bindings
                             if row["artifact_id"] == "core-source-document"]
                if len(originals) > 1:
                    raise RuntimeError("Core_LIBRARY_SOURCE_CARDINALITY_INVALID")
                if originals:
                    source_target = originals[0]["target"]
                    source_content_sha256 = originals[0]["sha256"]
                    expected_source = str(core_job.get("source_sha256") or job.get("source_sha256") or "").upper()
                    if expected_source and expected_source != source_content_sha256:
                        raise RuntimeError("Core_LIBRARY_SOURCE_HASH_MISMATCH")

            source_root = root
            if review_only and source_target is None and source_document is not None:
                source_target = Path(source_document['private_path'])
                source_content_sha256 = source_document['sha256']
                source_root = Path(source_document['source_root'])
            job_rows: list[dict[str, Any]] = []
            for verified in verified_bindings:
                binding = verified["binding"]
                source_artifact_id = str(verified["artifact_id"])
                if review_only and not source_artifact_id.startswith(review_prefixes):
                    continue
                relative = str(verified["relative_path"])
                target = Path(verified["target"])
                observed = str(verified["sha256"])
                canonical_target = target
                if projection is not None:
                    canonical_value = projection["canonical_path_by_artifact_id"].get(
                        source_artifact_id
                    )
                    if canonical_value:
                        canonical_target = Path(canonical_value).resolve(strict=True)
                        if (
                            not canonical_target.is_relative_to(artifact_root.resolve(strict=True))
                            or not canonical_target.is_file()
                            or canonical_target.is_symlink()
                            or hashlib.sha256(canonical_target.read_bytes()).hexdigest().upper()
                            != observed
                        ):
                            raise RuntimeError("Core_FOLDER_PROJECTION_TARGET_INVALID")
                canonical_relative = canonical_target.relative_to(artifact_root).as_posix()
                artifact_id = "core-product-" + hashlib.sha256(
                    f"{job_id}\0{source_artifact_id}".encode("utf-8")
                ).hexdigest()[:24]
                # E10 sidecars are reviewed through the source-bound structure dialog.
                if source_artifact_id.startswith('core-structure-'):
                    continue
                artifact_kind = source_artifact_id.replace("core-", "Core_").replace("-", "_").upper()
                review_meta=None
                review_unpublished=False
                for prefix,kind in [('core-data-review-record-','CORE_DATA_REVIEW_RECORD'),('core-logic-review-record-','CORE_LOGIC_REVIEW_RECORD'),
                                    ('core-data-review-','CORE_DATA_REVIEW'),('core-logic-review-','CORE_LOGIC_REVIEW')]:
                    if source_artifact_id.startswith(prefix):
                        artifact_kind=kind
                        from memorive_review.store import ReviewStore
                        reviews=ReviewStore(self.profile_path('Core_JOBS'))
                        report_id=source_artifact_id[len(prefix):]
                        try:
                            report=reviews.report(report_id)
                        except ValueError as error:
                            if str(error)=="REVIEW_REPORT_NOT_PUBLISHED":
                                review_unpublished=True
                                break
                            raise
                        review_meta={k:report.get(k) for k in ('report_id','kind','status','color','scope','report_sha256')}
                        state=reviews.get(job_id) or {}
                        review_meta.update(reviews.source_status(report),derived_opinion=True,raw_evidence_eligible=False)
                        break
                if review_unpublished:
                    continue
                display_name = kind_names.get(artifact_kind, source_artifact_id or target.stem)
                job_rows.append(
                    {
                        "artifact_id": artifact_id,
                        "source_artifact_id": source_artifact_id,
                        "literature_review":review_meta,
                        "partial_result":review_only,
                        "source_task_state":str(job.get('control_state') or 'UNKNOWN'),
                        "artifact_kind": artifact_kind,
                        "job_id": job_id,
                        "display_name": f"{core_job['display_name']} · {display_name}",
                        "kind": display_name,
                        "content_sha256": observed,
                        "relative_path": f"core/{job_id}/{canonical_relative}",
                        "private_path": str(canonical_target),
                        "artifact_root": str(root),
                        "updated_at": core_job.get("updated_at") or core_job.get("created_at"),
                        "size_bytes": canonical_target.stat().st_size,
                        "context_pack": (
                            deepcopy(core_job.get("context_pack"))
                            if source_artifact_id == "core-context-pack"
                            and isinstance(core_job.get("context_pack"), Mapping)
                            else None
                        ),
                        "raw_private_content_included": False,
                    }
                )
            product_files = [
                {
                    "artifact_id": row["artifact_id"],
                    "kind": row["kind"],
                    "file_name": Path(row["private_path"]).name,
                }
                for row in job_rows
            ]
            if source_target is not None:
                for row in job_rows:
                    row.update(
                        {
                            "source_file_name": source_target.name,
                            "source_private_path": str(source_target),
                            "source_root": str(source_root),
                            "source_content_sha256": source_content_sha256,
                            "source_file_exists": True,
                        }
                    )
            elif legacy_source_error_code is not None:
                unavailable_root = self.profile_path("INBOX").resolve(strict=False)
                unavailable_source = legacy_source_candidate or (unavailable_root / "bound" / "source-unavailable.pdf")
                unavailable_sha256 = legacy_source_sha256
                if re.fullmatch(r"[A-F0-9]{64}", unavailable_sha256) is None:
                    unavailable_sha256 = hashlib.sha256(
                        f"unavailable\0{job_id}".encode("utf-8")
                    ).hexdigest().upper()
                for row in job_rows:
                    row.update(
                        {
                            "source_file_name": unavailable_source.name,
                            "source_private_path": str(unavailable_source),
                            "source_root": str(unavailable_root),
                            "source_content_sha256": unavailable_sha256,
                            "source_file_exists": False,
                            "source_error_code": legacy_source_error_code,
                            "projection_status": "SOURCE_UNAVAILABLE",
                        }
                    )
            for row in job_rows:
                row["product_files"] = deepcopy(product_files)
            rows.extend(job_rows)
        return rows

    def unavailable_row(
        self, core_job: Mapping[str, Any], error: BaseException
    ) -> dict[str, Any]:
        """Persist one public-safe degraded row without hiding the failed job."""

        job_id = str(core_job.get("job_id") or "")
        error_code = self.projection_error_code(error)
        root = (self.state_dir / "library" / "core-unavailable").resolve(strict=False)
        root.mkdir(parents=True, exist_ok=True)
        identity = hashlib.sha256(f"{job_id}\0{error_code}".encode("utf-8")).hexdigest()[:24]
        artifact_id = "core-product-" + identity
        target = root / f"{identity}.json"
        payload = {
            "schema_version": "MemoriveCoreUnavailableProjection-v1",
            "status": "SOURCE_UNAVAILABLE",
            "error_code": error_code,
            "job_id": job_id,
            "provider": "core_artifact_provider",
            "skipped_record_and_continued": True,
            "raw_private_content_included": False,
        }
        self.write_json_file(target, payload)
        content_sha256 = hashlib.sha256(target.read_bytes()).hexdigest().upper()
        source_sha256 = str(core_job.get("source_sha256") or "").upper()
        if re.fullmatch(r"[A-F0-9]{64}", source_sha256) is None:
            source_sha256 = hashlib.sha256(f"unavailable\0{job_id}".encode("utf-8")).hexdigest().upper()
        return {
            "artifact_id": artifact_id,
            "source_artifact_id": "core-unavailable",
            "artifact_kind": "Core_UNAVAILABLE",
            "job_id": job_id,
            "display_name": f"{str(core_job.get('display_name') or job_id)} · 源文件不可用",
            "kind": "源文件不可用",
            "content_sha256": content_sha256,
            "relative_path": f"core-unavailable/{target.name}",
            "private_path": str(target),
            "artifact_root": str(root),
            "updated_at": core_job.get("updated_at") or core_job.get("created_at"),
            "size_bytes": target.stat().st_size,
            "source_file_name": "source-unavailable.pdf",
            "source_private_path": str(root / "source-unavailable.pdf"),
            "source_root": str(root),
            "source_content_sha256": source_sha256,
            "source_file_exists": False,
            "source_error_code": error_code,
            "projection_status": "SOURCE_UNAVAILABLE",
            "product_files": [
                {"artifact_id": artifact_id, "kind": "源文件不可用", "file_name": target.name}
            ],
            "raw_private_content_included": False,
        }

    @staticmethod
    def projection_error_code(error: BaseException) -> str:
        if isinstance(error, OSError) and getattr(error, "winerror", None) in {206, 3}:
            return "Core_LIBRARY_LEGACY_PATH_UNAVAILABLE"
        value = str(error).strip()
        if re.fullmatch(r"[A-Z][A-Z0-9_]{2,119}", value):
            return value
        return "Core_LIBRARY_" + type(error).__name__.upper()
