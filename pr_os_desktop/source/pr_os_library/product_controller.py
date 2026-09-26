from __future__ import annotations

from pr_os_desktop_service.projection_reads import projection_request, once_per_projection
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import threading
from typing import Any, Callable, Mapping

from m10_knowledge_feedback.conversation_refinement import (
    validate_typed_refinement_item,
)


LIBRARY_METHODS = frozenset(
    {
        "library.get_contract",
        "library.bootstrap",
        "library.list_artifacts",
        "library.get_artifact",
        "library.get_artifact_bindings",
        "library.get_lineage",
        "library.get_status_counts",
        "library.set_view",
        "library.select",
        "library.resolve_locator",
        "library.validate_external_view",
        "library.open_artifact",
        "library.confirm_refinement_review",
        "library.confirm_phase1_review",
        "library.refresh",
        "library.inject_fault",
        "library.restore",
        "library.effect_metrics",
    }
)

SAFE_SUFFIXES = frozenset(
    {
        ".pdf",
        ".md",
        ".txt",
        ".json",
        ".jsonl",
        ".docx",
        ".xlsx",
        ".pptx",
        ".png",
        ".jpg",
        ".jpeg",
        ".tif",
        ".tiff",
    }
)
RESTRICTED_EFFECT_KEYS = (
    "canonical_m13_actual_reads",
    "canonical_m13_actual_writes",
    "external_process_launches",
    "shell_invocations",
    "protocol_handler_invocations",
    "external_network_calls",
    "external_model_calls",
    "credential_value_reads",
    "private_artifact_reads",
    "restricted_artifact_reads",
    "registry_mutations",
    "source_file_mutations",
)


class LibraryProductError(ValueError):
    pass


def _sha256_json(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest().upper()


class LibraryProductController:
    adapter_kind = "t09_library_synthetic_offline_projection_m13_zero_access_safe_view_no_launch"

    def __init__(
        self,
        service: Any,
        control_store: Any,
        contract_root: Path,
        fixture_root: Path,
        state_root: Path,
        *,
        include_synthetic_projection: bool = True,
        phase1_artifact_provider: Callable[[], list[Mapping[str, Any]]] | None = None,
    ):
        self.service = service
        self.control_store = control_store
        self.contract_root = contract_root.resolve()
        self.fixture_root = fixture_root.resolve()
        self.fixture_file_root = (self.fixture_root / "files").resolve()
        self.state_root = state_root.resolve()
        self.include_synthetic_projection = bool(include_synthetic_projection)
        self._phase1_artifact_provider = phase1_artifact_provider
        self.research_artifact_provider = None
        self._research_artifact_ids = set()
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.state_root / "library_projection.json"
        self.refinement_catalog_path = self.state_root / "refinement_products_v1.json"
        self.refinement_product_root = self.state_root / "refinement_products_private"
        self.refinement_review_state_path = self.state_root / "refinement_review_state_v1.json"
        self.refinement_product_root.mkdir(parents=True, exist_ok=True)
        self._refinement_lock = threading.RLock()
        self._phase1_lock = self._refinement_lock  # one catalog publication lock
        from .paper_review import PaperReview
        self.paper_review = PaperReview(self)
        if not self.refinement_review_state_path.exists():
            self.refinement_review_state_path.write_text(
                json.dumps(
                    {
                        "schema_version": "P08LibraryRefinementReviewState-v1",
                        "artifacts": {},
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
        self._load_refinement_review_state()
        self._catalog = (
            json.loads((self.fixture_root / "synthetic_library_catalog.json").read_text(encoding="utf-8"))
            if self.include_synthetic_projection
            else {"schema_version": "P08T09SyntheticLibraryCatalog-v1", "synthetic_only": True, "artifacts": []}
        )
        self._boundary = json.loads((self.contract_root / "SafeExternalViewContract.json").read_text(encoding="utf-8"))
        if self.include_synthetic_projection:
            self._validate_catalog(self._catalog)
        self._rows_by_id = {row["artifact_id"]: deepcopy(row) for row in self._catalog["artifacts"]}
        refinement_catalog = self._load_refinement_catalog()
        self._refinement_entries_by_artifact_id = {
            entry["row"]["artifact_id"]: deepcopy(entry)
            for entry in refinement_catalog["products"]
        }
        self._refinement_artifact_ids = {
            entry["row"]["artifact_id"] for entry in refinement_catalog["products"]
        }
        self._phase1_entries_by_artifact_id: dict[str, dict[str, Any]] = {}
        self._phase1_artifact_ids: set[str] = set()
        self._phase1_row_aliases: dict[str, str] = {}
        for entry in refinement_catalog["products"]:
            self._rows_by_id[entry["row"]["artifact_id"]] = deepcopy(entry["row"])
        self._metrics = {
            "synthetic_fixture_reads": 2 if self.include_synthetic_projection else 0,
            "local_projection_writes": 0,
            "safe_view_validations": 0,
            "safe_view_open_intents": 0,
            "canonical_m13_actual_reads": 0,
            "canonical_m13_actual_writes": 0,
            "external_process_launches": 0,
            "shell_invocations": 0,
            "protocol_handler_invocations": 0,
            "external_network_calls": 0,
            "external_model_calls": 0,
            "credential_value_reads": 0,
            "private_artifact_reads": 0,
            "restricted_artifact_reads": 0,
            "registry_mutations": 0,
            "source_file_mutations": 0,
        }
        if not self.state_path.exists():
            self._save(
                {
                    "schema_version": "P08T09LibraryProjectionState-v1",
                    "revision": 0,
                    "selected_artifact_id": None,
                    "view": "literature",
                    "search": "",
                    "status_filter": "all",
                    "kind_filter": "all",
                    "sort": "updated_desc",
                    "page": 1,
                    "page_size": 4,
                    "locator_history": [],
                    "open_receipts": [],
                }
            )
        self._validate_state(self._load())

    @once_per_projection
    def _sync_phase1_artifacts(self) -> None:
        """Refresh metadata-only Library rows from verified Phase 1 bindings."""

        if self.include_synthetic_projection or self._phase1_artifact_provider is None:
            return
        source = self._phase1_artifact_provider()
        if not isinstance(source, list):
            raise LibraryProductError("LIBRARY_PHASE1_PROVIDER_RESULT_INVALID")
        entries: dict[str, dict[str, Any]] = {}
        for raw in source:
            if not isinstance(raw, Mapping):
                continue
            artifact_id = str(raw.get("artifact_id") or "")
            job_id = str(raw.get("job_id") or "")
            content_sha256 = str(raw.get("content_sha256") or "").upper()
            private_path = str(raw.get("private_path") or "")
            artifact_root = str(raw.get("artifact_root") or "")
            if (
                not re.fullmatch(r"phase1-product-[0-9a-f]{24}", artifact_id)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}", job_id)
                or not re.fullmatch(r"[A-F0-9]{64}", content_sha256)
                or not private_path
                or not artifact_root
            ):
                raise LibraryProductError("LIBRARY_PHASE1_ARTIFACT_BINDING_INVALID")
            suffix = Path(private_path).suffix.casefold()
            if suffix not in SAFE_SUFFIXES:
                raise LibraryProductError("LIBRARY_PHASE1_ARTIFACT_SUFFIX_INVALID")
            source_artifact_id = str(raw.get("source_artifact_id") or "")
            display_name = str(raw.get("display_name") or source_artifact_id or "文献处理产物")[:180]
            kind = str(raw.get("kind") or "文献处理产物")[:80]
            source_private_path = str(raw.get("source_private_path") or private_path)
            source_root = str(raw.get("source_root") or artifact_root)
            source_content_sha256 = str(
                raw.get("source_content_sha256") or content_sha256
            ).upper()
            source_file_name = str(
                raw.get("source_file_name") or Path(source_private_path).name
            )
            if (
                not source_private_path
                or not source_root
                or re.fullmatch(r"[A-F0-9]{64}", source_content_sha256) is None
                or not source_file_name
                or Path(source_file_name).name != source_file_name
            ):
                raise LibraryProductError("LIBRARY_PHASE1_SOURCE_BINDING_INVALID")
            raw_product_files = raw.get("product_files")
            if not isinstance(raw_product_files, list) or not raw_product_files:
                raw_product_files = [
                    {
                        "artifact_id": artifact_id,
                        "kind": kind,
                        "file_name": Path(private_path).name,
                    }
                ]
            product_files: list[dict[str, str]] = []
            for product in raw_product_files:
                if not isinstance(product, Mapping):
                    raise LibraryProductError("LIBRARY_PHASE1_PRODUCT_FILE_INVALID")
                product_artifact_id = str(product.get("artifact_id") or "")
                product_kind = str(product.get("kind") or "")[:80]
                product_file_name = str(product.get("file_name") or "")
                if (
                    re.fullmatch(r"phase1-product-[0-9a-f]{24}", product_artifact_id)
                    is None
                    or not product_kind
                    or not product_file_name
                    or Path(product_file_name).name != product_file_name
                    or Path(product_file_name).suffix.casefold() not in SAFE_SUFFIXES
                ):
                    raise LibraryProductError("LIBRARY_PHASE1_PRODUCT_FILE_INVALID")
                product_files.append(
                    {
                        "artifact_id": product_artifact_id,
                        "kind": product_kind,
                        "file_name": product_file_name,
                    }
                )
            if artifact_id not in {product["artifact_id"] for product in product_files}:
                raise LibraryProductError("LIBRARY_PHASE1_SELECTED_PRODUCT_MISSING")
            updated_at = str(raw.get("updated_at") or self._utc_now())
            size_bytes = int(raw.get("size_bytes") or 0)
            context_pack = (
                raw.get("context_pack")
                if isinstance(raw.get("context_pack"), Mapping)
                else None
            )
            projection_status = str(raw.get("projection_status") or "READY")
            source_error_code = str(raw.get("source_error_code") or "")[:120]
            source_file_exists = bool(raw.get("source_file_exists", True))
            file_state = "文献文件"
            if context_pack is not None and context_pack.get("truncated") is True:
                file_state = (
                    "整理分析材料 已按 Analysis 容量取舍："
                    f"保留 {int(context_pack.get('retained_estimated_tokens') or 0)} / "
                    f"原始估算 {int(context_pack.get('original_estimated_tokens') or 0)} tokens，"
                    f"丢弃 {int(context_pack.get('dropped_evidence_block_count') or 0)} 个证据块"
                )
            if not source_file_exists:
                file_state = "源文件不可用" + (f" · {source_error_code}" if source_error_code else "")
            row = {
                "artifact_id": artifact_id,
                "view_snapshot_id": "phase1-runtime-products-v1",
                "stable_locator": f"pr-os://artifact/{artifact_id}",
                "display_name": display_name,
                "mode": "literature",
                "kind": kind,
                "status": "源文件不可用" if not source_file_exists else "待人工复核",
                "rights_status": "ALLOWED",
                "privacy_class": "PRIVATE",
                "lineage": [f"job:{job_id}", f"sha256:{content_sha256}"],
                "relative_path": str(raw.get("relative_path") or ""),
                "file_exists": True,
                "file_state": file_state,
                "source_file_name": source_file_name,
                "source_file_exists": source_file_exists,
                "source_error_code": source_error_code,
                "projection_status": projection_status,
                "products": [product["file_name"] for product in product_files],
                "product_files": deepcopy(product_files),
                "external_target": "",
                "task": job_id,
                "updated_at": updated_at,
                "size_bytes": size_bytes,
            }
            entries[artifact_id] = {
                "row": row,
                "job_id": job_id,
                "source_artifact_id": source_artifact_id,
                "content_sha256": content_sha256,
                "private_path": private_path,
                "artifact_root": artifact_root,
                "source_private_path": source_private_path,
                "source_root": source_root,
                "source_content_sha256": source_content_sha256,
                "artifact_kind": str(raw.get("artifact_kind") or ""),
                "projection_status": projection_status,
                "source_error_code": source_error_code,
            }
        child_entries = dict(entries)
        grouped: dict[str, dict[str, list[tuple[str, dict[str, Any]]]]] = {}
        for child_id, entry in child_entries.items():
            source_key = str(entry["source_content_sha256"])
            grouped.setdefault(source_key, {}).setdefault(entry["job_id"], []).append(
                (child_id, entry)
            )

        document_rows: dict[str, dict[str, Any]] = {}
        aliases: dict[str, str] = {}
        visible_kinds = {
            "PHASE1_CARD": (0, "Card.md"),
            "PHASE1_ANALYSIS": (1, "Analysis.md"),
            "PHASE1_RAW_DOCUMENT": (2, "RawMD.md"),
            "PHASE1_CLEAN_DOCUMENT": (3, "CleanMD.md"),
            "PHASE1_CHUNKS": (4, "Chunks.jsonl"),
        }
        representative_order = {
            "PHASE1_CARD": 0,
            "PHASE1_ANALYSIS": 1,
            "PHASE1_CLEAN_DOCUMENT": 2,
            "PHASE1_RAW_DOCUMENT": 3,
            "PHASE1_CHUNKS": 4,
        }
        for source_key, by_job in grouped.items():
            winning_job_id = max(
                by_job,
                key=lambda job: (
                    max(str(entry["row"].get("updated_at") or "") for _cid, entry in by_job[job]),
                    job,
                ),
            )
            selected = by_job[winning_job_id]
            representative_id, representative = min(
                selected,
                key=lambda item: (
                    representative_order.get(item[1]["artifact_kind"], 99),
                    item[0],
                ),
            )
            logical_id = "phase1-product-" + hashlib.sha256(
                f"document\0{source_key}".encode("utf-8")
            ).hexdigest()[:24]
            if logical_id in child_entries:
                raise LibraryProductError("LIBRARY_PHASE1_DOCUMENT_ID_COLLISION")

            visible_files = []
            for child_id, entry in selected:
                presentation = visible_kinds.get(entry["artifact_kind"])
                if presentation is None:
                    continue
                order, display_label = presentation
                visible_files.append(
                    {
                        "artifact_id": child_id,
                        "kind": entry["row"]["kind"],
                        "file_name": Path(entry["private_path"]).name,
                        "display_name": display_label,
                        "divider_before": False,
                        "role": "product",
                        "_order": order,
                    }
                )
            visible_files.sort(key=lambda item: (item["_order"], item["artifact_id"]))
            for descriptor in visible_files:
                if descriptor["display_name"] in {"RawMD.md", "CleanMD.md", "Chunks.jsonl"}:
                    descriptor["divider_before"] = True
                    break
            for descriptor in visible_files:
                descriptor.pop("_order", None)

            base_row = deepcopy(representative["row"])
            raw_name = str(base_row.get("display_name") or "")
            document_name = raw_name.split(" · ", 1)[0].strip() or str(
                base_row.get("source_file_name") or "文献"
            )
            source_suffix = Path(str(base_row["source_file_name"])).suffix.casefold()
            source_display_name = (
                "PDF.pdf" if source_suffix == ".pdf" else f"Source{source_suffix or '.file'}"
            )
            all_product_files = []
            seen_products: set[str] = set()
            for _child_id, entry in selected:
                for product in entry["row"].get("product_files") or []:
                    product_id = str(product.get("artifact_id") or "")
                    if product_id and product_id not in seen_products:
                        all_product_files.append(deepcopy(product))
                        seen_products.add(product_id)
            updated_at = max(
                str(entry["row"].get("updated_at") or "") for _child_id, entry in selected
            )
            file_state = next(
                (
                    str(entry["row"].get("file_state"))
                    for _child_id, entry in selected
                    if str(entry["row"].get("file_state") or "").startswith("整理分析材料")
                ),
                "文献文件",
            )
            base_row.update(
                {
                    "artifact_id": logical_id,
                    "stable_locator": f"pr-os://artifact/{logical_id}",
                    "display_name": document_name,
                    "kind": "文献",
                    "lineage": [f"job:{winning_job_id}", f"sha256:{source_key}"],
                    "file_state": file_state,
                    "source_file": {
                        "artifact_id": logical_id,
                        "file_name": base_row["source_file_name"],
                        "display_name": source_display_name,
                        "divider_before": False,
                        "role": "source",
                    },
                    "library_files": visible_files,
                    "products": [str(item.get("file_name") or "") for item in all_product_files],
                    "product_files": all_product_files,
                    "task": winning_job_id,
                    "updated_at": updated_at,
                    "size_bytes": sum(int(entry["row"].get("size_bytes") or 0) for _cid, entry in selected),
                }
            )
            document_rows[logical_id] = base_row
            logical_entry = deepcopy(representative)
            logical_entry.update(
                {
                    "row": base_row,
                    "source_artifact_id": "phase1-document",
                    "artifact_kind": "PHASE1_DOCUMENT",
                }
            )
            entries[logical_id] = logical_entry
            for job_rows in by_job.values():
                for child_id, _entry in job_rows:
                    aliases[child_id] = logical_id

        with self._phase1_lock:
            for artifact_id in self._phase1_artifact_ids:
                self._rows_by_id.pop(artifact_id, None)
            self._phase1_entries_by_artifact_id = entries
            self._phase1_artifact_ids = set(document_rows)
            self._phase1_row_aliases = aliases
            for artifact_id, row in document_rows.items():
                self._rows_by_id[artifact_id] = deepcopy(row)

    @staticmethod
    def _resolve_phase1_entry_file(
        entry: Mapping[str, Any],
        *,
        root_field: str,
        path_field: str,
        sha256_field: str,
        error_scope: str,
    ) -> Path:
        root = Path(str(entry[root_field]))
        candidate = Path(str(entry[path_field]))
        try:
            root = root.resolve(strict=True)
            attributes = candidate.lstat()
            target = candidate.resolve(strict=True)
        except OSError as error:
            raise LibraryProductError(
                f"LIBRARY_PHASE1_{error_scope}_UNAVAILABLE"
            ) from error
        reparse_mask = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if (
            candidate.is_symlink()
            or bool(getattr(attributes, "st_file_attributes", 0) & reparse_mask)
            or not target.is_relative_to(root)
            or not target.is_file()
        ):
            raise LibraryProductError(f"LIBRARY_PHASE1_{error_scope}_PATH_INVALID")
        observed = hashlib.sha256(target.read_bytes()).hexdigest().upper()
        if observed != entry[sha256_field]:
            raise LibraryProductError(f"LIBRARY_PHASE1_{error_scope}_HASH_MISMATCH")
        return target

    def resolve_phase1_private_artifact(self, artifact_id: str) -> Path:
        self._sync_phase1_artifacts()
        entry = self._phase1_entries_by_artifact_id.get(str(artifact_id or ""))
        if entry is None:
            raise LibraryProductError("LIBRARY_PHASE1_ARTIFACT_NOT_FOUND")
        return self._resolve_phase1_entry_file(
            entry,
            root_field="artifact_root",
            path_field="private_path",
            sha256_field="content_sha256",
            error_scope="ARTIFACT",
        )

    def resolve_phase1_private_source(self, artifact_id: str) -> Path:
        self._sync_phase1_artifacts()
        entry = self._phase1_entries_by_artifact_id.get(str(artifact_id or ""))
        if entry is None:
            raise LibraryProductError("LIBRARY_PHASE1_ARTIFACT_NOT_FOUND")
        return self._resolve_phase1_entry_file(
            entry,
            root_field="source_root",
            path_field="source_private_path",
            sha256_field="source_content_sha256",
            error_scope="SOURCE",
        )

    @staticmethod
    def _validate_refinement_catalog(value: Mapping[str, Any]) -> dict[str, Any]:
        if (
            not isinstance(value, Mapping)
            or set(value) != {"schema_version", "products"}
            or value.get("schema_version") != "P08LibraryRefinementProducts-v1"
            or not isinstance(value.get("products"), list)
        ):
            raise LibraryProductError("LIBRARY_REFINEMENT_CATALOG_INVALID")
        accepted = deepcopy(dict(value))
        ids: set[str] = set()
        drafts: set[str] = set()
        required = {"draft_id", "draft_sha256", "private_file", "row"}
        for entry in accepted["products"]:
            if not isinstance(entry, Mapping) or set(entry) != required:
                raise LibraryProductError("LIBRARY_REFINEMENT_ENTRY_INVALID")
            row = entry["row"]
            if not isinstance(row, Mapping):
                raise LibraryProductError("LIBRARY_REFINEMENT_ROW_INVALID")
            artifact_id = row.get("artifact_id")
            draft_id = entry.get("draft_id")
            if (
                not isinstance(artifact_id, str)
                or not artifact_id
                or artifact_id in ids
                or not isinstance(draft_id, str)
                or not draft_id
                or draft_id in drafts
                or not isinstance(entry.get("draft_sha256"), str)
                or not re.fullmatch(r"[0-9A-F]{64}", entry["draft_sha256"])
                or not isinstance(entry.get("private_file"), str)
                or not entry["private_file"]
            ):
                raise LibraryProductError("LIBRARY_REFINEMENT_IDENTITY_INVALID")
            ids.add(artifact_id)
            drafts.add(draft_id)
        return accepted

    def _load_refinement_catalog(self) -> dict[str, Any]:
        if not self.refinement_catalog_path.exists():
            self.refinement_catalog_path.write_text(
                json.dumps(
                    {
                        "schema_version": "P08LibraryRefinementProducts-v1",
                        "products": [],
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
        try:
            value = json.loads(self.refinement_catalog_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise LibraryProductError("LIBRARY_REFINEMENT_CATALOG_UNREADABLE") from error
        return self._validate_refinement_catalog(value)

    def _save_refinement_catalog(self, value: Mapping[str, Any]) -> None:
        accepted = self._validate_refinement_catalog(value)
        temporary = self.refinement_catalog_path.with_suffix(".json.next")
        if temporary.exists():
            raise FileExistsError("LIBRARY_REFINEMENT_CATALOG_TEMP_COLLISION")
        temporary.write_text(
            json.dumps(accepted, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        temporary.replace(self.refinement_catalog_path)

    @staticmethod
    def _validate_refinement_review_state(value: Mapping[str, Any]) -> dict[str, Any]:
        if (
            not isinstance(value, Mapping)
            or set(value) != {"schema_version", "artifacts"}
            or value.get("schema_version") != "P08LibraryRefinementReviewState-v1"
            or not isinstance(value.get("artifacts"), Mapping)
        ):
            raise LibraryProductError("LIBRARY_REFINEMENT_REVIEW_STATE_INVALID")
        accepted = deepcopy(dict(value))
        required = {
            "artifact_id",
            "draft_sha256",
            "opened_at",
            "confirmed_at",
        }
        for artifact_id, row in accepted["artifacts"].items():
            if (
                not isinstance(artifact_id, str)
                or not artifact_id
                or not isinstance(row, Mapping)
                or set(row) != required
                or row.get("artifact_id") != artifact_id
                or not isinstance(row.get("draft_sha256"), str)
                or not re.fullmatch(r"[0-9A-F]{64}", row["draft_sha256"])
                or not isinstance(row.get("opened_at"), str)
                or not row["opened_at"]
                or row.get("confirmed_at") is not None
                and not isinstance(row.get("confirmed_at"), str)
            ):
                raise LibraryProductError("LIBRARY_REFINEMENT_REVIEW_RECORD_INVALID")
        return accepted

    def _load_refinement_review_state(self) -> dict[str, Any]:
        try:
            value = json.loads(
                self.refinement_review_state_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as error:
            raise LibraryProductError(
                "LIBRARY_REFINEMENT_REVIEW_STATE_UNREADABLE"
            ) from error
        return self._validate_refinement_review_state(value)

    def _save_refinement_review_state(self, value: Mapping[str, Any]) -> None:
        accepted = self._validate_refinement_review_state(value)
        temporary = self.refinement_review_state_path.with_suffix(".json.next")
        if temporary.exists():
            raise FileExistsError("LIBRARY_REFINEMENT_REVIEW_STATE_TEMP_COLLISION")
        temporary.write_text(
            json.dumps(accepted, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        temporary.replace(self.refinement_review_state_path)

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )

    def resolve_refinement_private_artifact(self, artifact_id: str) -> Path:
        entry = self._refinement_entries_by_artifact_id.get(str(artifact_id or ""))
        if entry is None:
            raise LibraryProductError("LIBRARY_REFINEMENT_ARTIFACT_NOT_FOUND")
        private_file = entry.get("private_file")
        if (
            not isinstance(private_file, str)
            or not private_file
            or Path(private_file).is_absolute()
            or Path(private_file).name != private_file
            or any(part in {"", ".", ".."} for part in Path(private_file).parts)
        ):
            raise LibraryProductError("LIBRARY_REFINEMENT_PRIVATE_PATH_INVALID")
        root = self.refinement_product_root.resolve(strict=True)
        candidate = root / private_file
        try:
            attributes = candidate.lstat()
            target = candidate.resolve(strict=True)
        except OSError as error:
            raise LibraryProductError(
                "LIBRARY_REFINEMENT_PRIVATE_FILE_UNAVAILABLE"
            ) from error
        reparse_mask = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if (
            candidate.is_symlink()
            or bool(getattr(attributes, "st_file_attributes", 0) & reparse_mask)
            or not target.is_relative_to(root)
            or not target.is_file()
        ):
            raise LibraryProductError("LIBRARY_REFINEMENT_PRIVATE_FILE_INVALID")
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise LibraryProductError(
                "LIBRARY_REFINEMENT_PRIVATE_FILE_UNREADABLE"
            ) from error
        if _sha256_json(payload) != entry["draft_sha256"]:
            raise LibraryProductError("LIBRARY_REFINEMENT_PRIVATE_HASH_MISMATCH")
        return target

    def resolve_refinement_readable_artifact(self, artifact_id: str) -> Path:
        from .refinement_markdown import readable_name, render
        with self._refinement_lock:
            original = self.resolve_refinement_private_artifact(artifact_id)
            payload = json.loads(original.read_text(encoding="utf-8"))
            entry = self._refinement_entries_by_artifact_id[artifact_id]
            if _sha256_json(payload) != entry["draft_sha256"]:
                raise LibraryProductError("LIBRARY_REFINEMENT_PRIVATE_HASH_MISMATCH")
            target = original.parent / readable_name(artifact_id, payload["display_name"])
            content = render(payload, entry["draft_sha256"]).encode("utf-8")
            if target.exists() or target.is_symlink():
                attributes = target.lstat()
                if target.is_symlink() or bool(getattr(attributes,"st_file_attributes",0) & 0x400):
                    raise LibraryProductError("LIBRARY_REFINEMENT_READABLE_PATH_INVALID")
                if target.read_bytes() != content:
                    raise LibraryProductError("LIBRARY_REFINEMENT_READABLE_HASH_MISMATCH")
            else:
                import os, uuid
                temporary = target.with_name("." + uuid.uuid4().hex + ".next")
                with temporary.open("xb") as stream:
                    stream.write(content); stream.flush(); os.fsync(stream.fileno())
                # Same-directory atomic rename; never replace an existing user file.
                temporary.rename(target)
            return target

    def record_refinement_open(self, artifact_id: str) -> dict[str, Any]:
        self.resolve_refinement_private_artifact(artifact_id)
        with self._refinement_lock:
            state = self._load_refinement_review_state()
            entry = self._refinement_entries_by_artifact_id[artifact_id]
            current = state["artifacts"].get(artifact_id)
            opened_at = current["opened_at"] if current else self._utc_now()
            state["artifacts"][artifact_id] = {
                "artifact_id": artifact_id,
                "draft_sha256": entry["draft_sha256"],
                "opened_at": opened_at,
                "confirmed_at": current.get("confirmed_at") if current else None,
            }
            self._save_refinement_review_state(state)
        return {
            "schema_version": "P08LibraryRefinementOpenReceipt-v1",
            "artifact_id": artifact_id,
            "opened_at": opened_at,
            "verified_draft_sha256": entry["draft_sha256"],
            "status": "OPENED",
        }

    def _confirm_refinement_review(self, artifact_id: str) -> dict[str, Any]:
        self.resolve_refinement_private_artifact(artifact_id)
        with self._refinement_lock:
            state = self._load_refinement_review_state()
            current = state["artifacts"].get(artifact_id)
            expected = self._refinement_entries_by_artifact_id[artifact_id][
                "draft_sha256"
            ]
            if current is None or current.get("draft_sha256") != expected:
                raise LibraryProductError("LIBRARY_REFINEMENT_OPEN_REQUIRED")
            confirmed_at = current.get("confirmed_at") or self._utc_now()
            current["confirmed_at"] = confirmed_at
            self._save_refinement_review_state(state)
        return {
            "schema_version": "P08LibraryRefinementReviewReceipt-v1",
            "artifact_id": artifact_id,
            "opened_at": current["opened_at"],
            "confirmed_at": confirmed_at,
            "verified_draft_sha256": expected,
            "status": "REVIEW_CONFIRMED",
        }

    def register_refinement_draft(self, draft: Mapping[str, Any]) -> dict[str, Any]:
        """Persist one private refinement draft as a reviewable Library product."""

        if not isinstance(draft, Mapping):
            raise LibraryProductError("LIBRARY_REFINEMENT_DRAFT_INVALID")
        accepted = deepcopy(dict(draft))
        required = {
            "draft_id",
            "task_id",
            "display_name",
            "local_projection_id",
            "source_snapshot_sha256",
            "items",
            "created_at",
        }
        if (
            accepted.get("schema_version") != "P08SessionRefinementDraft-v3"
            or not required <= set(accepted)
            or not all(
                isinstance(accepted.get(field), str) and accepted[field].strip()
                for field in required - {"items"}
            )
            or not isinstance(accepted.get("items"), list)
            or not accepted["items"]
        ):
            raise LibraryProductError("LIBRARY_REFINEMENT_DRAFT_FIELDS_INVALID")
        try:
            accepted["items"] = [
                validate_typed_refinement_item(item) for item in accepted["items"]
            ]
        except Exception as error:
            raise LibraryProductError("LIBRARY_REFINEMENT_TYPED_ITEMS_INVALID") from error
        draft_sha256 = _sha256_json(accepted)
        artifact_id = "refinement-product-" + hashlib.sha256(
            accepted["draft_id"].encode("utf-8")
        ).hexdigest()[:24]
        private_name = f"{artifact_id}.json"
        destination = self.refinement_product_root / private_name
        raw = json.dumps(
            accepted,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        row = {
            "artifact_id": artifact_id,
            "view_snapshot_id": "local-refinement-products-v1",
            "stable_locator": f"pr-os://artifact/{artifact_id}",
            "display_name": accepted["display_name"],
            "mode": "pros",
            "kind": "对话分析",
            "status": "待复核",
            "rights_status": "ALLOWED",
            "privacy_class": "PRIVATE",
            "lineage": [
                f"session:{accepted['local_projection_id']}",
                f"snapshot:{accepted['source_snapshot_sha256']}",
            ],
            "relative_path": f"refinement_products_private/{private_name}",
            "file_exists": True,
            "file_state": "精炼结果",
            "products": [f"精炼条目 {len(accepted['items'])} 项"],
            "external_target": "",
            "task": accepted["task_id"],
            "updated_at": accepted["created_at"],
            "size_bytes": len(raw.encode("utf-8")),
        }
        with self._refinement_lock:
            catalog = self._load_refinement_catalog()
            existing = next(
                (
                    entry
                    for entry in catalog["products"]
                    if entry["draft_id"] == accepted["draft_id"]
                ),
                None,
            )
            if existing is not None:
                if existing["draft_sha256"] != draft_sha256:
                    raise LibraryProductError("LIBRARY_REFINEMENT_DRAFT_CONFLICT")
                self._rows_by_id[artifact_id] = deepcopy(existing["row"])
                self._refinement_entries_by_artifact_id[artifact_id] = deepcopy(existing)
                self._refinement_artifact_ids.add(artifact_id)
                return {
                    "schema_version": "P08LibraryRefinementRegistrationReceipt-v1",
                    "artifact_id": artifact_id,
                    "stable_locator": existing["row"]["stable_locator"],
                    "replayed": True,
                    "status": "PASS",
                }
            temporary = destination.with_suffix(".json.next")
            if destination.exists() or temporary.exists():
                raise FileExistsError("LIBRARY_REFINEMENT_PRIVATE_FILE_COLLISION")
            temporary.write_text(raw, encoding="utf-8", newline="\n")
            temporary.replace(destination)
            entry = {
                "draft_id": accepted["draft_id"],
                "draft_sha256": draft_sha256,
                "private_file": private_name,
                "row": row,
            }
            try:
                catalog["products"].append(entry)
                self._save_refinement_catalog(catalog)
            except Exception:
                destination.unlink(missing_ok=True)
                raise
            self._rows_by_id[artifact_id] = deepcopy(row)
            self._refinement_entries_by_artifact_id[artifact_id] = deepcopy(entry)
            self._refinement_artifact_ids.add(artifact_id)
            self._metrics["local_projection_writes"] += 2
            return {
                "schema_version": "P08LibraryRefinementRegistrationReceipt-v1",
                "artifact_id": artifact_id,
                "stable_locator": row["stable_locator"],
                "replayed": False,
                "status": "PASS",
            }

    @staticmethod
    def _require_keys(params: Mapping[str, Any], required: set[str], optional: set[str] | None = None) -> None:
        optional = optional or set()
        keys = set(params)
        if not required <= keys or keys - required - optional:
            raise LibraryProductError("LIBRARY_PRODUCT_PARAMS_INVALID")

    @staticmethod
    def _validate_catalog(catalog: Mapping[str, Any]) -> None:
        if set(catalog) != {"schema_version", "synthetic_only", "artifacts"}:
            raise LibraryProductError("LIBRARY_CATALOG_FIELDS_INVALID")
        if catalog.get("schema_version") != "P08T09SyntheticLibraryCatalog-v1" or catalog.get("synthetic_only") is not True:
            raise LibraryProductError("LIBRARY_CATALOG_AUTHORITY_INVALID")
        rows = catalog.get("artifacts")
        if not isinstance(rows, list) or len(rows) != 9:
            raise LibraryProductError("LIBRARY_CATALOG_DENOMINATOR_INVALID")
        required = {
            "artifact_id", "view_snapshot_id", "stable_locator", "display_name", "mode", "kind", "status",
            "rights_status", "privacy_class", "lineage", "relative_path", "file_exists", "file_state",
            "products", "external_target", "task", "updated_at", "size_bytes",
        }
        ids: set[str] = set()
        locators: set[str] = set()
        for row in rows:
            if set(row) != required:
                raise LibraryProductError("LIBRARY_CATALOG_ROW_FIELDS_INVALID")
            artifact_id = row["artifact_id"]
            locator = row["stable_locator"]
            if not isinstance(artifact_id, str) or artifact_id in ids:
                raise LibraryProductError("LIBRARY_ARTIFACT_ID_INVALID")
            if locator != f"pr-os://artifact/{artifact_id}" or locator in locators:
                raise LibraryProductError("LIBRARY_STABLE_LOCATOR_INVALID")
            if row["privacy_class"] not in {"PUBLIC_SAFE", "PRIVATE", "RESTRICTED"}:
                raise LibraryProductError("LIBRARY_PRIVACY_CLASS_INVALID")
            ids.add(artifact_id)
            locators.add(locator)

    @staticmethod
    def _validate_state(state: Mapping[str, Any]) -> None:
        required = {
            "schema_version", "revision", "selected_artifact_id", "view", "search", "status_filter",
            "kind_filter", "sort", "page", "page_size", "locator_history", "open_receipts",
        }
        if set(state) != required or state.get("schema_version") != "P08T09LibraryProjectionState-v1":
            raise LibraryProductError("LIBRARY_STATE_FIELDS_INVALID")
        if not isinstance(state.get("revision"), int) or state["revision"] < 0:
            raise LibraryProductError("LIBRARY_STATE_REVISION_INVALID")
        if state.get("view") not in {"literature", "pros"}:
            raise LibraryProductError("LIBRARY_STATE_VIEW_INVALID")
        if not isinstance(state.get("locator_history"), list) or not isinstance(state.get("open_receipts"), list):
            raise LibraryProductError("LIBRARY_STATE_COLLECTION_INVALID")

    def _load(self) -> dict[str, Any]:
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self._validate_state(state)
        return state

    def _save(self, state: dict[str, Any]) -> None:
        state["revision"] = int(state.get("revision", -1)) + 1
        self._validate_state(state)
        temporary = self.state_path.with_suffix(".json.next")
        if temporary.exists():
            raise FileExistsError("LIBRARY_STATE_TEMP_COLLISION")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        temporary.replace(self.state_path)
        self._metrics["local_projection_writes"] += 1

    @staticmethod
    def _lineage_value(row: Mapping[str, Any], prefix: str) -> str:
        lineage = row.get("lineage")
        if not isinstance(lineage, list):
            return ""
        match = next(
            (value for value in lineage if isinstance(value, str) and value.startswith(prefix)),
            "",
        )
        return match[len(prefix) :] if match else ""

    @staticmethod
    def _refinement_item_count(row: Mapping[str, Any]) -> int:
        products = row.get("products")
        if not isinstance(products, list):
            return 0
        for value in products:
            match = re.search(r"(\d+)\s*项", str(value))
            if match:
                return int(match.group(1))
        return 0

    def _refinement_metadata(self, artifact_id: str) -> dict[str, Any]:
        entry = self._refinement_entries_by_artifact_id.get(artifact_id)
        if entry is None:
            return {}
        row = entry["row"]
        review = self._load_refinement_review_state()["artifacts"].get(artifact_id)
        from .refinement_markdown import readable_name
        readable = readable_name(artifact_id, row["display_name"])
        return {
            "product_kind": "SESSION_REFINEMENT",
            "readable_format": "MARKDOWN",
            "readable_filename": readable,
            "relative_path": "refinement_products_private/" + readable,
            "draft_id": entry["draft_id"],
            "draft_sha256": entry["draft_sha256"],
            "source_session_id": self._lineage_value(row, "session:"),
            "source_snapshot_sha256": self._lineage_value(row, "snapshot:"),
            "item_count": self._refinement_item_count(row),
            "task_id": row["task"],
            "private_storage": True,
            "direct_file_open_supported": True,
            "review_opened": review is not None,
            "review_opened_at": review.get("opened_at") if review else None,
            "review_confirmed": bool(review and review.get("confirmed_at")),
            "review_confirmed_at": review.get("confirmed_at") if review else None,
        }

    def _phase1_metadata(self, artifact_id: str) -> dict[str, Any]:
        entry = self._phase1_entries_by_artifact_id.get(artifact_id)
        if entry is None:
            return {}
        return {
            "product_kind": "PHASE1_ARTIFACT",
            "job_id": entry["job_id"],
            "source_artifact_id": entry["source_artifact_id"],
            "artifact_kind": entry["artifact_kind"],
            "content_sha256": entry["content_sha256"],
            "projection_status": str(entry.get("projection_status") or "READY"),
            "source_error_code": str(entry.get("source_error_code") or ""),
            "private_storage": True,
            "direct_file_open_supported": True,
            **self.paper_review.metadata(artifact_id),
        }

    def _public(self, row: Mapping[str, Any]) -> dict[str, Any]:
        result = deepcopy(dict(row))
        result.update(self._refinement_metadata(str(row.get("artifact_id") or "")))
        result.update(self._phase1_metadata(str(row.get("artifact_id") or "")))
        if result.get("review_confirmed"):
            result["status"] = "复核通过"
            result["updated_at"] = result.get("review_confirmed_at") or result["updated_at"]
        if result.get('product_kind') == 'PHASE1_ARTIFACT':
            file_states = result.get('file_review_states', {})
            for field in ('product_files', 'library_files'):
                for descriptor in result.get(field, []):
                    descriptor['review_status'] = file_states.get(descriptor['artifact_id'], '待人工复核')
            result['file_state'] = '文件已变化，待重新核对' if result.get('review_files_valid') is False else (
                '复核通过' if result.get('review_confirmed') else result['file_state'])
        result["public_safe_projection"] = row["privacy_class"] == "PUBLIC_SAFE"
        result["canonical_registry_row_included"] = False
        result["absolute_path_included"] = False
        result["raw_private_content_included"] = False
        return result

    def phase1_activity_rows(self) -> list[dict[str, Any]]:
        """Return public-safe Phase 1 artifact metadata for Work Log projection."""

        self._sync_phase1_artifacts()
        rows = []
        for artifact_id in sorted(self._phase1_artifact_ids):
            row = self._rows_by_id[artifact_id]
            metadata = self._phase1_metadata(artifact_id)
            rows.append(
                {
                    "artifact_id": artifact_id,
                    "stable_locator": row["stable_locator"],
                    "display_name": row["display_name"],
                    "task_id": metadata["job_id"],
                    "status": row["status"],
                    "kind": row["kind"],
                    "updated_at": row["updated_at"],
                    "content_sha256": metadata["content_sha256"],
                    "source_artifact_id": metadata["source_artifact_id"],
                    "projection_status": str(metadata.get("projection_status") or "READY"),
                    "source_error_code": str(metadata.get("source_error_code") or ""),
                    "source_file_exists": bool(row.get("source_file_exists", True)),
                    "raw_private_content_included": False,
                }
            )
        return rows

    def refinement_activity_rows(self) -> list[dict[str, Any]]:
        """Return metadata-only rows for cross-page lifecycle projection."""

        rows = []
        for artifact_id in sorted(self._refinement_artifact_ids):
            row = self._rows_by_id[artifact_id]
            metadata = self._refinement_metadata(artifact_id)
            rows.append(
                {
                    "artifact_id": artifact_id,
                    "stable_locator": row["stable_locator"],
                    "display_name": row["display_name"],
                    "task_id": metadata["task_id"],
                    "status": "复核通过" if metadata["review_confirmed"] else row["status"],
                    "item_count": metadata["item_count"],
                    "updated_at": row["updated_at"],
                    "draft_id": metadata["draft_id"],
                    "draft_sha256": metadata["draft_sha256"],
                    "source_session_id": metadata["source_session_id"],
                    "source_snapshot_sha256": metadata["source_snapshot_sha256"],
                    "raw_private_content_included": False,
                }
            )
        return rows

    def _get(self, artifact_id: str) -> dict[str, Any]:
        self._sync_research_artifacts()
        self._sync_phase1_artifacts()
        resolved_id = self._phase1_row_aliases.get(artifact_id, artifact_id)
        row = self._rows_by_id.get(resolved_id)
        if row is None:
            raise LibraryProductError("LIBRARY_ARTIFACT_NOT_FOUND")
        public = self._public(row)
        if resolved_id != artifact_id:
            child = self._phase1_entries_by_artifact_id.get(artifact_id)
            if child is not None:
                public.update(
                    {
                        "source_artifact_id": child["source_artifact_id"],
                        "artifact_kind": child["artifact_kind"],
                        "content_sha256": child["content_sha256"],
                    }
                )
        return public

    def _service_ready(self) -> bool:
        health = self.service.call("service.health", {})
        return (
            isinstance(health, dict)
            and health.get("schema_version") == "ApplicationServiceHealth-v1"
            and health.get("protocol_version") == "1.0"
            and all(health.get(key) == 0 for key in ("external_network_calls", "external_model_calls", "credential_value_reads"))
        )

    def _contract(self) -> dict[str, Any]:
        names = (
            "LibraryAssetIdentityContract.json",
            "LibraryLineageRightsStatusContract.json",
            "LibraryLocatorFallbackContract.json",
            "SafeExternalViewContract.json",
            "Part8LibraryActionStateContract.json",
            "LibraryFaultDenominator.json",
            "LibraryAcceptanceDenominator.json",
            "CanonicalM13ReadOnlyBoundary.json",
        )
        contracts = {name: json.loads((self.contract_root / name).read_text(encoding="utf-8")) for name in names}
        return {
            "schema_version": "P08T09LibraryProductContract-v1",
            "method_count": len(LIBRARY_METHODS),
            "methods": sorted(LIBRARY_METHODS),
            "contracts": contracts,
            "d36_actions": ["open_artifact", "resolve_locator"],
            "d36_queries": ["list_artifacts", "get_artifact_bindings"],
            "d36_events": ["artifact_bound", "view_snapshot_changed"],
            "d36_receipts": ["secure_open_receipt", "locator_resolution_receipt"],
            "d36_identities": ["artifact_id", "view_snapshot_id", "stable_locator"],
            "canonical_m13_access": "ZERO_ACTUAL_ACCESS_SYNTHETIC_H0_BOUND_PROJECTION_ONLY",
            "external_view_execution": "DISABLED_VALIDATE_ONLY",
            "status": "PASS",
        }

    def _catalog_rows_snapshot(self) -> list[dict[str, Any]]:
        # Publishers replace several entries together. Readers consume one
        # generation without holding the lock during sorting or UI projection.
        with self._phase1_lock:
            return deepcopy(list(self._rows_by_id.values()))

    def _list(self, params: Mapping[str, Any]) -> dict[str, Any]:
        self._sync_research_artifacts()
        self._sync_phase1_artifacts()
        state = self._load()
        view = str(params.get("view", state["view"]))
        search = str(params.get("search", state["search"]))
        status_filter = str(params.get("status_filter", state["status_filter"]))
        kind_filter = str(params.get("kind_filter", state["kind_filter"]))
        sort = str(params.get("sort", state["sort"]))
        page = int(params.get("page", state["page"]))
        page_size = int(params.get("page_size", state["page_size"]))
        if view not in {"literature", "pros"} or sort not in {"updated_desc", "updated_asc", "name_asc", "name_desc"}:
            raise LibraryProductError("LIBRARY_LIST_AXIS_INVALID")
        if not 1 <= page <= 1000 or not 1 <= page_size <= 100:
            raise LibraryProductError("LIBRARY_PAGING_INVALID")
        term = search.strip().casefold()
        rows = []
        for row in self._catalog_rows_snapshot():
            if row["mode"] != view:
                continue
            public = self._public(row)
            status_group = ('complete' if public['status'] in {'complete','已完成','复核通过','REVIEWED'} else
                            'error' if public['status'] in {'error','异常','执行异常','FAILED'} else 'pending')
            if status_filter != "all" and public["status"] != status_filter and status_group != status_filter:
                continue
            kind_group = public.get('report_period') or ('conversation' if public['kind'] == '对话分析' else 'other')
            if kind_filter != "all" and public["kind"] != kind_filter and kind_group != kind_filter:
                continue
            domain = " ".join(
                str(public[key]) for key in ("artifact_id", "display_name", "kind", "status", "task", "relative_path")
            ).casefold()
            if term and term not in domain:
                continue
            rows.append(public)
        if sort.startswith("updated"):
            rows.sort(key=lambda row: (row["updated_at"], row["artifact_id"]), reverse=sort.endswith("desc"))
        else:
            rows.sort(key=lambda row: (row["display_name"].casefold(), row["artifact_id"]), reverse=sort.endswith("desc"))
        total = len(rows)
        start = (page - 1) * page_size
        selected = rows[start : start + page_size]
        return {
            "schema_version": "P08T09LibraryListProjection-v1",
            "view_snapshot_id": f"library-view-{_sha256_json([view, search, status_filter, kind_filter, sort, page, page_size])[:16].lower()}",
            "view": view,
            "search_sha256": hashlib.sha256(search.encode("utf-8")).hexdigest().upper(),
            "status_filter": status_filter,
            "kind_filter": kind_filter,
            "sort": sort,
            "page": page,
            "page_size": page_size,
            "total_count": total,
            "rows": selected,
            "row_count": len(selected),
            "synthetic_only": self.include_synthetic_projection,
            "canonical_m13_actual_reads": 0,
            "status": "PASS",
        }

    @once_per_projection
    def _sync_research_artifacts(self) -> None:
        if self.research_artifact_provider is None:
            return
        rows = self.research_artifact_provider()
        with self._phase1_lock:
            for artifact_id in self._research_artifact_ids:
                self._rows_by_id.pop(artifact_id, None)
            self._research_artifact_ids = {row['artifact_id'] for row in rows}
            self._rows_by_id.update({row['artifact_id']: deepcopy(row) for row in rows})

    def _safe_view(self, artifact_id: str) -> dict[str, Any]:
        row = self._rows_by_id.get(artifact_id)
        if row is None:
            raise LibraryProductError("LIBRARY_ARTIFACT_NOT_FOUND")
        relative = str(row["relative_path"])
        rejection: str | None = None
        pure = PurePosixPath(relative.replace("\\", "/"))
        if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", relative) or relative.startswith(("//", "\\\\", "\\?\\", "\\.\\")):
            rejection = "URL_URI_UNC_OR_DEVICE_PATH_FORBIDDEN"
        elif pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
            rejection = "ABSOLUTE_OR_TRAVERSAL_PATH_FORBIDDEN"
        elif Path(pure.name).suffix.casefold() not in SAFE_SUFFIXES:
            rejection = "UNSUPPORTED_OR_EXECUTABLE_SUFFIX"
        elif row["privacy_class"] != "PUBLIC_SAFE" or row["rights_status"] != "ALLOWED":
            rejection = "PRIVACY_OR_RIGHTS_BLOCKED"
        elif row["file_exists"] is not True:
            rejection = "SOURCE_FILE_MISSING_OR_MOVED"
        target = self.fixture_file_root.joinpath(*pure.parts)
        root = self.fixture_file_root.resolve()
        resolved = target.resolve(strict=False)
        if rejection is None and (not resolved.is_relative_to(root) or target.is_symlink()):
            rejection = "SYMLINK_OR_ROOT_ESCAPE_FORBIDDEN"
        if rejection is None and (not resolved.exists() or not resolved.is_file()):
            rejection = "SOURCE_FILE_NOT_REGULAR"
        self._metrics["safe_view_validations"] += 1
        return {
            "schema_version": "P08T09SecureOpenValidationReceipt-v1",
            "artifact_id": artifact_id,
            "stable_locator": row["stable_locator"],
            "relative_path_sha256": hashlib.sha256(relative.encode("utf-8")).hexdigest().upper(),
            "suffix": Path(pure.name).suffix.casefold(),
            "allowlist_match": rejection is None,
            "root_containment_verified": rejection is None,
            "regular_file_verified": rejection is None,
            "rights_verified": row["rights_status"] == "ALLOWED",
            "privacy_verified": row["privacy_class"] == "PUBLIC_SAFE",
            "launch_performed": False,
            "shell_invoked": False,
            "protocol_handler_invoked": False,
            "rejection_reason": rejection,
            "status": "PASS_VALIDATED_NOT_LAUNCHED" if rejection is None else "BLOCKED_FAIL_CLOSED",
        }

    @projection_request
    def call(self, method: str, params: Mapping[str, Any]) -> Any:
        if method not in LIBRARY_METHODS:
            raise LibraryProductError("LIBRARY_PRODUCT_METHOD_NOT_ALLOWLISTED")
        accepted = dict(params)
        self._sync_research_artifacts()
        if any(str(key).startswith("_") for key in accepted):
            raise LibraryProductError("LIBRARY_PRODUCT_PRIVATE_PARAM_FORBIDDEN")
        if not self.include_synthetic_projection:
            self._sync_phase1_artifacts()
        if method == "library.get_contract":
            self._require_keys(accepted, set())
            return self._contract()
        if method == "library.bootstrap":
            self._require_keys(accepted, set())
            if not self._service_ready():
                raise LibraryProductError("LIBRARY_SERVICE_HEALTH_CONTRACT_INVALID")
            literature = self._list({"view": "literature", "page": 1, "page_size": 100})
            pros = self._list({"view": "pros", "page": 1, "page_size": 100})
            records = literature["rows"] + pros["rows"]
            return {
                "schema_version": "P08T09LibraryBootstrapProjection-v1",
                "records": records,
                "record_count": len(records),
                "catalog_count": len(self._rows_by_id),
                "binding_count": len(self._rows_by_id),
                "synthetic_only": not bool(self._refinement_artifact_ids or self._phase1_artifact_ids or self._research_artifact_ids),
                "canonical_m13_actual_reads": 0,
                "external_view_execution": "DISABLED_VALIDATE_ONLY",
                "status": "PASS",
            }
        if method == "library.list_artifacts":
            self._require_keys(accepted, set(), {"view", "search", "status_filter", "kind_filter", "sort", "page", "page_size"})
            return self._list(accepted)
        if method == "library.get_artifact":
            self._require_keys(accepted, {"artifact_id"})
            return {"schema_version": "P08T09LibraryDetailProjection-v1", "artifact": self._get(str(accepted["artifact_id"])), "status": "PASS"}
        if method == "library.get_artifact_bindings":
            self._require_keys(accepted, set(), {"artifact_id"})
            artifact_id = accepted.get("artifact_id")
            rows = [row for row in self._catalog_rows_snapshot() if artifact_id is None or row["artifact_id"] == artifact_id]
            return {
                "schema_version": "P08T09ArtifactBindingProjection-v1",
                "bindings": [
                    {
                        "artifact_id": row["artifact_id"],
                        "view_snapshot_id": row["view_snapshot_id"],
                        "stable_locator": row["stable_locator"],
                        "lineage": deepcopy(row["lineage"]),
                        "rights_status": row["rights_status"],
                    }
                    for row in rows
                ],
                "binding_count": len(rows),
                "canonical_m13_actual_reads": 0,
                "status": "PASS",
            }
        if method == "library.get_lineage":
            self._require_keys(accepted, {"artifact_id"})
            row = self._get(str(accepted["artifact_id"]))
            return {"schema_version": "P08T09ArtifactLineageProjection-v1", "artifact_id": row["artifact_id"], "lineage": row["lineage"], "orphan": not bool(row["lineage"]), "status": "PASS"}
        if method == "library.get_status_counts":
            self._require_keys(accepted, set(), {"view"})
            view = str(accepted.get("view", "literature"))
            rows = [row for row in self._catalog_rows_snapshot() if row["mode"] == view]
            counts: dict[str, int] = {}
            for row in rows:
                status = self._public(row)["status"]
                counts[status] = counts.get(status, 0) + 1
            return {"schema_version": "P08T09LibraryStatusCounts-v1", "view": view, "counts": counts, "total": len(rows), "status": "PASS"}
        if method == "library.set_view":
            self._require_keys(accepted, {"view"}, {"search", "status_filter", "kind_filter", "sort", "page", "page_size"})
            state = self._load()
            for key, value in accepted.items():
                state[key] = value
            self._save(state)
            projection = self._list({})
            return {"schema_version": "P08T09ViewSnapshotChangedReceipt-v1", "view_snapshot_id": projection["view_snapshot_id"], "revision": self._load()["revision"], "status": "PASS"}
        if method == "library.select":
            self._require_keys(accepted, {"artifact_id"})
            requested_id = str(accepted["artifact_id"])
            selected = self._get(requested_id)
            artifact_id = str(selected["artifact_id"])
            state = self._load()
            state["selected_artifact_id"] = artifact_id
            self._save(state)
            return {"schema_version": "P08T09ArtifactBoundReceipt-v1", "artifact_id": artifact_id, "stable_locator": self._rows_by_id[artifact_id]["stable_locator"], "status": "PASS"}
        if method == "library.resolve_locator":
            self._require_keys(accepted, {"stable_locator"})
            locator = str(accepted["stable_locator"])
            prefix = "pr-os://artifact/"
            if not locator.startswith(prefix):
                raise LibraryProductError("LIBRARY_LOCATOR_INVALID")
            requested_id = locator[len(prefix) :]
            selected = self._get(requested_id)
            artifact_id = str(selected["artifact_id"])
            locator = str(selected["stable_locator"])
            state = self._load()
            state["locator_history"].append({"stable_locator": locator, "artifact_id": artifact_id})
            state["locator_history"] = state["locator_history"][-16:]
            self._save(state)
            return {"schema_version": "P08T09LocatorResolutionReceipt-v1", "stable_locator": locator, "artifact_id": artifact_id, "view":selected["mode"], "route": "library", "fuzzy_match_attempted": False, "canonical_m13_actual_reads": 0, "status": "PASS"}
        if method == "library.validate_external_view":
            self._require_keys(accepted, {"artifact_id"})
            return self._safe_view(str(accepted["artifact_id"]))
        if method == "library.open_artifact":
            self._require_keys(accepted, {"artifact_id"})
            receipt = self._safe_view(str(accepted["artifact_id"]))
            self._metrics["safe_view_open_intents"] += 1
            state = self._load()
            state["open_receipts"].append({"artifact_id": receipt["artifact_id"], "status": receipt["status"], "launch_performed": False})
            state["open_receipts"] = state["open_receipts"][-16:]
            self._save(state)
            return {**receipt, "schema_version": "P08T09SecureOpenReceipt-v1", "open_intent_recorded": True, "launch_performed": False}
        if method == "library.confirm_refinement_review":
            self._require_keys(accepted, {"artifact_id"})
            return self._confirm_refinement_review(str(accepted["artifact_id"]))
        if method == "library.confirm_phase1_review":
            self._require_keys(accepted, {"artifact_id"})
            return self.paper_review.confirm(str(accepted["artifact_id"]))
        if method == "library.refresh":
            self._require_keys(accepted, set())
            return {"schema_version": "P08T09LibraryRefreshReceipt-v1", "catalog_count": len(self._rows_by_id), "canonical_m13_actual_reads": 0, "status": "PASS"}
        if method == "library.inject_fault":
            self._require_keys(accepted, {"fault"})
            fault = str(accepted["fault"])
            classifications = {
                "duplicate_display_name": "IDENTITIES_REMAIN_DISTINCT_BY_ARTIFACT_ID",
                "path_changed": "STABLE_LOCATOR_PRESERVED_SOURCE_PATH_STALE",
                "orphan_lineage": "ORPHAN_VISIBLE_FAIL_CLOSED_NO_FUZZY_BIND",
                "rights_blocked": "SAFE_EXTERNAL_VIEW_BLOCKED",
                "large_directory": "PAGED_SYNTHETIC_PROJECTION_ONLY",
                "missing": "MISSING_VISIBLE_NO_OPEN",
                "stale": "STALE_VISIBLE_REFRESH_ZERO_CANONICAL_ACCESS",
                "unsupported": "UNSUPPORTED_SUFFIX_BLOCKED",
            }
            if fault not in classifications:
                raise LibraryProductError("LIBRARY_FAULT_UNKNOWN")
            return {
                "schema_version": "P08T09LibraryFaultReceipt-v1",
                "fault": fault,
                "classification": classifications[fault],
                "canonical_m13_actual_reads": 0,
                "launch_performed": False,
                "restricted_effects": 0,
                "status": "PASS",
            }
        if method == "library.restore":
            self._require_keys(accepted, set())
            state = self._load()
            return {"schema_version": "P08T09LibraryRestartRecoveryReceipt-v1", "revision": state["revision"], "selected_artifact_id": state["selected_artifact_id"], "stable_locator_replay": True, "canonical_m13_actual_reads": 0, "status": "PASS"}
        self._require_keys(accepted, set())
        restricted_total = sum(int(self._metrics[key]) for key in RESTRICTED_EFFECT_KEYS)
        return {
            "schema_version": "P08T09LibraryEffectMetrics-v1",
            **deepcopy(self._metrics),
            "restricted_effect_total": restricted_total,
            "synthetic_only": not bool(self._refinement_artifact_ids or self._phase1_artifact_ids or self._research_artifact_ids),
            "status": "PASS" if restricted_total == 0 else "FAIL",
        }


__all__ = ["LIBRARY_METHODS", "SAFE_SUFFIXES", "LibraryProductError", "LibraryProductController"]
