"""Formal M14 ``reranker_text`` reference-exam integration.

The formal entrypoint is bound to one repository-resident reference pack and
one frozen low-cost rehearsal receipt.  Provider requests contain only the
model ID, query, candidate documents, and frozen rerank parameters.  Gold is
loaded only after provider output has been persisted by the local scorer.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import ModuleType
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = (
    Path(__file__).resolve().parent
    / "assets"
    / "reranker_text_exam"
    / "v1"
)
CANONICAL_REFERENCE_PACK = ASSET_ROOT / "reference_pack"
CANONICAL_LOW_COST_REHEARSAL = ASSET_ROOT / "low_cost_rehearsal"
OPS_TEST_ROOT = Path(r"G:\PR-OS-运维\测试")

EXPECTED_PACK_REVISION = "reranker_text_reference_pack_r0.1.1"
EXPECTED_MODEL = "Pro/BAAI/bge-reranker-v2-m3"
EXPECTED_LOW_COST_MODEL = "BAAI/bge-reranker-v2-m3"
EXPECTED_ENDPOINT = "https://api.siliconflow.cn/v1/rerank"
EXPECTED_PACK_MANIFEST_SHA256 = (
    "220610F318DDC92DD0FE1C535CFDB984771896E8898A35FC6973CEECF749F4D0"
)
EXPECTED_RUNNER_SHA256 = (
    "077ABE7C6F98AF50942E6728C38E11A4DC357C156BBF1F6E9BDC8988E5FC9AF3"
)
EXPECTED_REHEARSAL_MANIFEST_SHA256 = (
    "A18854DE472E6FA642F389DFFD267C83F777DEB410CA840C32C23DD666FFD9DE"
)
EXPECTED_REHEARSAL_SCORES_SHA256 = (
    "F90A4AC7089C286119965205973202CA9CCC61413673B017BB61A6AFF18A23E4"
)
EXPECTED_API_PARAMETERS = {
    "return_documents": False,
    "top_n": 16,
    "max_chunks_per_doc": 1,
    "overlap_tokens": 0,
}
PACK_REQUIRED_MEMBERS = {
    "README.md",
    "manifest.reference.json",
    "SHA256SUMS.txt",
    "forms/form_A.json",
    "forms/form_B.json",
    "gold/form_A_gold.json",
    "gold/form_B_gold.json",
    "profiles/reference_profiles.json",
    "scoring/reranker_exam_tool.py",
    "scoring/scoring_protocol.json",
}
PUBLIC_REQUIRED_MEMBERS = {
    "README.md",
    "manifest.public.json",
    "reranker_text_exam_protocol.public.json",
    "SHA256SUMS.txt",
    "low_cost_rehearsal/run_manifest.json",
    "low_cost_rehearsal/scores.json",
    "low_cost_rehearsal/SHA256SUMS.txt",
}

EXAM_LOGICAL_CALLS = 188
MAX_ATTEMPTS_PER_EXAM_CALL = 2
CAPABILITY_PREFLIGHT_CALLS = 1
MAX_PHYSICAL_ATTEMPTS = (
    EXAM_LOGICAL_CALLS * MAX_ATTEMPTS_PER_EXAM_CALL
    + CAPABILITY_PREFLIGHT_CALLS
)
OBSERVED_REHEARSAL_INPUT_TOKENS = 251_268
MAX_BILLED_INPUT_TOKENS = 520_000
PRO_PRICE_CNY_PER_MILLION_INPUT_TOKENS = 0.07
DEFAULT_MAX_COST_CNY = 0.05
LANGUAGE_SUCCESSOR_PACK_REVISION = "reranker_text_language_bank_r2.0"
LANGUAGE_SUCCESSOR_REQUIRED_MEMBERS = PACK_REQUIRED_MEMBERS
LANGUAGE_SUCCESSOR_COUNTS_PER_FORM = {
    "en_en": 50,
    "zh_zh": 18,
    "ja_ja": 18,
}


class RerankerTextExamError(RuntimeError):
    """Fail-closed formal reranker exam error."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _relative_files(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }


def _checksum_rows(checksum_path: Path) -> tuple[dict[str, str], list[str]]:
    rows: dict[str, str] = {}
    errors: list[str] = []
    if not checksum_path.is_file():
        return rows, [checksum_path.name]
    for line_number, line in enumerate(
        checksum_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        parts = line.split("  ", 1)
        if len(parts) != 2 or len(parts[0]) != 64:
            errors.append(f"{checksum_path.name}:line_{line_number}")
            continue
        digest, relative = parts
        normalized = Path(relative).as_posix()
        if normalized in rows:
            errors.append(f"{checksum_path.name}:duplicate:{normalized}")
            continue
        rows[normalized] = digest.upper()
    return rows, errors


def _is_within(path: Path, parent: Path) -> bool:
    resolved = path.resolve()
    root = parent.resolve()
    return resolved == root or root in resolved.parents


def inspect_language_successor_reference_pack(
    pack_root: Path,
) -> dict[str, Any]:
    """Inspect the 172-item production-language successor exact set."""

    root = Path(pack_root).resolve()
    missing: list[str] = []
    extra: list[str] = []
    hash_mismatches: list[str] = []
    malformed_checksums: list[str] = []
    symlinks: list[str] = []
    policy_errors: list[str] = []
    if not root.is_dir():
        return {
            "status": "FAIL",
            "pack_root": str(root),
            "member_count": 0,
            "missing_files": sorted(LANGUAGE_SUCCESSOR_REQUIRED_MEMBERS),
            "extra_files": [],
            "hash_mismatches": [],
            "malformed_checksums": [],
            "symlinks": [],
            "policy_errors": ["REFERENCE_PACK_ROOT_MISSING"],
            "historical_source_pointer_present": False,
            "historical_source_pointer_runtime_dereferenced": False,
        }
    for path in root.rglob("*"):
        if path.is_symlink():
            symlinks.append(path.relative_to(root).as_posix())
    actual = _relative_files(root)
    missing.extend(sorted(LANGUAGE_SUCCESSOR_REQUIRED_MEMBERS - actual))
    extra.extend(sorted(actual - LANGUAGE_SUCCESSOR_REQUIRED_MEMBERS))
    checksums, malformed_checksums = _checksum_rows(root / "SHA256SUMS.txt")
    expected_checksums = LANGUAGE_SUCCESSOR_REQUIRED_MEMBERS - {"SHA256SUMS.txt"}
    if set(checksums) != expected_checksums:
        for relative in sorted(expected_checksums - set(checksums)):
            policy_errors.append(f"CHECKSUM_DECLARATION_MISSING:{relative}")
        for relative in sorted(set(checksums) - expected_checksums):
            policy_errors.append(f"CHECKSUM_DECLARATION_EXTRA:{relative}")
    for relative, expected in checksums.items():
        path = root / relative
        if path.is_file() and _sha256_file(path) != expected:
            hash_mismatches.append(relative)

    manifest_path = root / "manifest.reference.json"
    manifest: dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            value = _load_json(manifest_path)
            manifest = value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            policy_errors.append("REFERENCE_MANIFEST_INVALID_JSON")
        if manifest.get("pack_revision") != LANGUAGE_SUCCESSOR_PACK_REVISION:
            policy_errors.append("REFERENCE_PACK_REVISION_MISMATCH")
        if manifest.get("category_id") != "reranker_text":
            policy_errors.append("REFERENCE_CATEGORY_MISMATCH")
        if manifest.get("status") != "FROZEN_EXECUTABLE_REFERENCE_PACK":
            policy_errors.append("REFERENCE_PACK_NOT_FROZEN")
        if manifest.get("candidate_pool_size") != 15:
            policy_errors.append("REFERENCE_CANDIDATE_POOL_MISMATCH")
        if manifest.get("full_bank_language_distribution") != {
            "en_en": 100,
            "zh_zh": 36,
            "ja_ja": 36,
        }:
            policy_errors.append("REFERENCE_LANGUAGE_BANK_DISTRIBUTION_MISMATCH")
        if manifest.get("gold_isolation", {}).get("gold_sent_to_provider") is not False:
            policy_errors.append("GOLD_PROVIDER_VISIBILITY_MUST_BE_FALSE")
        workload = manifest.get("workload_binding", {})
        if (
            workload.get("question_chars_200_percent_ceiling") != 365
            or workload.get("chunk_chars_200_percent_ceiling") != 810
            or workload.get("production_final_candidate_target") != 15
        ):
            policy_errors.append("PRODUCTION_WORKLOAD_BINDING_MISMATCH")
    else:
        policy_errors.append("REFERENCE_MANIFEST_MISSING")

    topology: dict[str, Any] = {}
    form_item_sets: list[set[str]] = []
    for form_id in ("A", "B"):
        form_path = root / "forms" / f"form_{form_id}.json"
        gold_path = root / "gold" / f"form_{form_id}_gold.json"
        if not form_path.is_file() or not gold_path.is_file():
            continue
        try:
            form = _load_json(form_path)
            gold = _load_json(gold_path)
        except (OSError, json.JSONDecodeError):
            policy_errors.append(f"FORM_OR_GOLD_INVALID_JSON:{form_id}")
            continue
        items = form.get("items") if isinstance(form, dict) else None
        gold_items = gold.get("items") if isinstance(gold, dict) else None
        if not isinstance(items, list) or not isinstance(gold_items, list):
            policy_errors.append(f"FORM_OR_GOLD_ITEMS_INVALID:{form_id}")
            continue
        if len(items) != 86 or len(gold_items) != 86:
            policy_errors.append(f"FORM_ITEM_COUNT_MISMATCH:{form_id}")
        if form.get("candidate_pool_size") != 15:
            policy_errors.append(f"FORM_POOL_SIZE_MISMATCH:{form_id}")
        if form.get("provider_blind") is not True or form.get("gold_in_subject_request") is not False:
            policy_errors.append(f"FORM_VISIBILITY_INVALID:{form_id}")
        if gold.get("subject_visible") is not False:
            policy_errors.append(f"GOLD_SUBJECT_VISIBILITY_INVALID:{form_id}")
        if gold.get("bound_form_sha256") != _sha256_file(form_path):
            policy_errors.append(f"GOLD_FORM_BINDING_MISMATCH:{form_id}")
        ids = [str(row.get("item_id")) for row in items if isinstance(row, dict)]
        gold_ids = [str(row.get("item_id")) for row in gold_items if isinstance(row, dict)]
        if ids != gold_ids or len(set(ids)) != len(ids):
            policy_errors.append(f"FORM_GOLD_ITEM_EXACT_SET_MISMATCH:{form_id}")
        form_item_sets.append(set(ids))
        language_counts = {
            language_mode: sum(
                isinstance(row, dict) and row.get("language_mode") == language_mode
                for row in items
            )
            for language_mode in LANGUAGE_SUCCESSOR_COUNTS_PER_FORM
        }
        if language_counts != LANGUAGE_SUCCESSOR_COUNTS_PER_FORM:
            policy_errors.append(f"FORM_LANGUAGE_DISTRIBUTION_MISMATCH:{form_id}")
        difficulty_counts: dict[str, dict[str, int]] = {}
        for language_mode in LANGUAGE_SUCCESSOR_COUNTS_PER_FORM:
            difficulty_counts[language_mode] = {
                difficulty: sum(
                    isinstance(row, dict)
                    and row.get("language_mode") == language_mode
                    and row.get("difficulty") == difficulty
                    for row in items
                )
                for difficulty in ("easy", "medium", "hard")
            }
            if language_mode != "en_en" and difficulty_counts[language_mode] != {
                "easy": 6,
                "medium": 6,
                "hard": 6,
            }:
                policy_errors.append(
                    f"FORM_DIFFICULTY_DISTRIBUTION_MISMATCH:{form_id}:{language_mode}"
                )
            families = {
                str(row.get("family_id"))
                for row in items
                if isinstance(row, dict) and row.get("language_mode") == language_mode
            }
            if families != {f"RR{index}" for index in range(1, 8)}:
                policy_errors.append(
                    f"FORM_LANGUAGE_FAMILY_COVERAGE_MISMATCH:{form_id}:{language_mode}"
                )
        if sum(difficulty_counts["en_en"].values()) != 50 or any(
            value < 16 for value in difficulty_counts["en_en"].values()
        ):
            policy_errors.append(f"FORM_EN_DIFFICULTY_DISTRIBUTION_MISMATCH:{form_id}")
        gold_by_id = {
            str(row.get("item_id")): row for row in gold_items if isinstance(row, dict)
        }
        query_lengths: list[int] = []
        candidate_lengths: list[int] = []
        for item in items:
            if not isinstance(item, dict):
                policy_errors.append(f"FORM_ITEM_INVALID:{form_id}")
                continue
            item_id = str(item.get("item_id"))
            query = item.get("query")
            candidates = item.get("candidates")
            gold_item = gold_by_id.get(item_id)
            if not isinstance(query, str) or not isinstance(candidates, list) or not isinstance(gold_item, dict):
                policy_errors.append(f"FORM_ITEM_SHAPE_INVALID:{form_id}:{item_id}")
                continue
            query_lengths.append(len(query))
            if not 1 <= len(query) <= 365:
                policy_errors.append(f"QUERY_LENGTH_LIMIT:{form_id}:{item_id}")
            if len(candidates) != 15 or item.get("candidate_pool_size") != 15:
                policy_errors.append(f"CANDIDATE_POOL_SIZE:{form_id}:{item_id}")
            candidate_ids = [str(row.get("candidate_id")) for row in candidates if isinstance(row, dict)]
            gold_candidate_ids = [
                str(row.get("candidate_id"))
                for row in gold_item.get("candidates", [])
                if isinstance(row, dict)
            ]
            if set(candidate_ids) != set(gold_candidate_ids) or len(set(candidate_ids)) != 15:
                policy_errors.append(f"CANDIDATE_EXACT_SET:{form_id}:{item_id}")
            for candidate in candidates:
                text = candidate.get("text") if isinstance(candidate, dict) else None
                if not isinstance(text, str):
                    policy_errors.append(f"CANDIDATE_TEXT_INVALID:{form_id}:{item_id}")
                    continue
                candidate_lengths.append(len(text))
                if not 1 <= len(text) <= 810:
                    policy_errors.append(f"CANDIDATE_LENGTH_LIMIT:{form_id}:{item_id}")
        if query_lengths and not 150 <= sum(query_lengths) / len(query_lengths) <= 250:
            policy_errors.append(f"QUERY_MEAN_NOT_PRODUCTION_SHAPED:{form_id}")
        if candidate_lengths and not 350 <= sum(candidate_lengths) / len(candidate_lengths) <= 550:
            policy_errors.append(f"CANDIDATE_MEAN_NOT_PRODUCTION_SHAPED:{form_id}")
        topology[form_id] = {
            "items": len(items),
            "language_counts": language_counts,
            "difficulty_counts": difficulty_counts,
            "query_chars_mean": (
                round(sum(query_lengths) / len(query_lengths), 6)
                if query_lengths
                else None
            ),
            "query_chars_max": max(query_lengths) if query_lengths else None,
            "candidate_chars_mean": (
                round(sum(candidate_lengths) / len(candidate_lengths), 6)
                if candidate_lengths
                else None
            ),
            "candidate_chars_max": max(candidate_lengths) if candidate_lengths else None,
        }
    if len(form_item_sets) == 2 and form_item_sets[0] & form_item_sets[1]:
        policy_errors.append("CROSS_FORM_ITEM_ID_COLLISION")
    for relative in actual:
        path = root / relative
        if not _is_within(path, root):
            policy_errors.append(f"MEMBER_ESCAPES_PACK_ROOT:{relative}")
    status = "PASS" if not (
        missing
        or extra
        or hash_mismatches
        or malformed_checksums
        or symlinks
        or policy_errors
    ) else "FAIL"
    return {
        "status": status,
        "pack_root": str(root),
        "pack_id": manifest.get("pack_id"),
        "pack_revision": manifest.get("pack_revision"),
        "member_count": len(actual),
        "missing_files": sorted(set(missing)),
        "extra_files": sorted(set(extra)),
        "hash_mismatches": sorted(set(hash_mismatches)),
        "malformed_checksums": sorted(set(malformed_checksums)),
        "symlinks": sorted(set(symlinks)),
        "policy_errors": sorted(set(policy_errors)),
        "topology": topology,
        "gold_sent_to_provider": False,
        "historical_source_pointer_present": False,
        "historical_source_pointer_runtime_dereferenced": False,
    }


def inspect_reference_pack(
    pack_root: Path = CANONICAL_REFERENCE_PACK,
) -> dict[str, Any]:
    """Return an exact missing/extra/hash/policy diagnosis without API calls."""

    candidate_manifest_path = Path(pack_root).resolve() / "manifest.reference.json"
    if candidate_manifest_path.is_file():
        try:
            candidate_manifest = _load_json(candidate_manifest_path)
        except (OSError, json.JSONDecodeError):
            candidate_manifest = {}
        if (
            isinstance(candidate_manifest, dict)
            and candidate_manifest.get("pack_revision")
            == LANGUAGE_SUCCESSOR_PACK_REVISION
        ):
            return inspect_language_successor_reference_pack(pack_root)

    root = pack_root.resolve()
    missing: list[str] = []
    extra: list[str] = []
    hash_mismatches: list[str] = []
    malformed_checksums: list[str] = []
    symlinks: list[str] = []
    policy_errors: list[str] = []
    historical_source_pointer_present = False

    if not root.is_dir():
        return {
            "status": "FAIL",
            "pack_root": str(root),
            "member_count": 0,
            "missing_files": sorted(PACK_REQUIRED_MEMBERS),
            "extra_files": [],
            "hash_mismatches": [],
            "malformed_checksums": [],
            "symlinks": [],
            "policy_errors": ["REFERENCE_PACK_ROOT_MISSING"],
            "historical_source_pointer_present": False,
        }

    for path in root.rglob("*"):
        if path.is_symlink():
            symlinks.append(path.relative_to(root).as_posix())

    actual = _relative_files(root)
    missing.extend(sorted(PACK_REQUIRED_MEMBERS - actual))
    extra.extend(sorted(actual - PACK_REQUIRED_MEMBERS))

    checksums, malformed_checksums = _checksum_rows(
        root / "SHA256SUMS.txt"
    )
    expected_checksum_members = PACK_REQUIRED_MEMBERS - {"SHA256SUMS.txt"}
    if set(checksums) != expected_checksum_members:
        for relative in sorted(expected_checksum_members - set(checksums)):
            policy_errors.append(f"CHECKSUM_DECLARATION_MISSING:{relative}")
        for relative in sorted(set(checksums) - expected_checksum_members):
            policy_errors.append(f"CHECKSUM_DECLARATION_EXTRA:{relative}")
    for relative, expected in checksums.items():
        path = root / Path(relative)
        if path.is_file() and _sha256_file(path) != expected:
            hash_mismatches.append(relative)

    manifest_path = root / "manifest.reference.json"
    if manifest_path.is_file():
        try:
            manifest = _load_json(manifest_path)
        except (OSError, json.JSONDecodeError):
            policy_errors.append("REFERENCE_MANIFEST_INVALID_JSON")
        else:
            if manifest.get("pack_revision") != EXPECTED_PACK_REVISION:
                policy_errors.append("REFERENCE_PACK_REVISION_MISMATCH")
            if manifest.get("category_id") != "reranker_text":
                policy_errors.append("REFERENCE_CATEGORY_MISMATCH")
            if manifest.get("status") != "FROZEN_EXECUTABLE_REFERENCE_PACK":
                policy_errors.append("REFERENCE_PACK_NOT_FROZEN")
            if manifest.get("candidate_pool_size") != 16:
                policy_errors.append("REFERENCE_CANDIDATE_POOL_MISMATCH")
            if manifest.get("api_parameters") != EXPECTED_API_PARAMETERS:
                policy_errors.append("REFERENCE_API_PARAMETERS_MISMATCH")
            if (
                manifest.get("gold_isolation", {}).get(
                    "gold_sent_to_provider"
                )
                is not False
            ):
                policy_errors.append("GOLD_PROVIDER_VISIBILITY_MUST_BE_FALSE")
            source_path = str(
                manifest.get("source_blueprint", {}).get("path", "")
            )
            historical_source_pointer_present = "PR-OS-沙盒" in source_path
    else:
        policy_errors.append("REFERENCE_MANIFEST_MISSING")

    if (
        manifest_path.is_file()
        and _sha256_file(manifest_path) != EXPECTED_PACK_MANIFEST_SHA256
    ):
        policy_errors.append("REFERENCE_MANIFEST_FROZEN_HASH_MISMATCH")
    runner_path = root / "scoring" / "reranker_exam_tool.py"
    if (
        runner_path.is_file()
        and _sha256_file(runner_path) != EXPECTED_RUNNER_SHA256
    ):
        policy_errors.append("BUNDLED_RUNNER_FROZEN_HASH_MISMATCH")

    for form_id in ("A", "B"):
        form_path = root / "forms" / f"form_{form_id}.json"
        gold_path = root / "gold" / f"form_{form_id}_gold.json"
        if form_path.is_file():
            try:
                form = _load_json(form_path)
            except (OSError, json.JSONDecodeError):
                policy_errors.append(f"FORM_INVALID_JSON:{form_id}")
            else:
                if form.get("provider_blind") is not True:
                    policy_errors.append(f"FORM_NOT_PROVIDER_BLIND:{form_id}")
                if form.get("gold_in_subject_request") is not False:
                    policy_errors.append(
                        f"FORM_GOLD_SUBJECT_VISIBILITY_INVALID:{form_id}"
                    )
        if gold_path.is_file():
            try:
                gold = _load_json(gold_path)
            except (OSError, json.JSONDecodeError):
                policy_errors.append(f"GOLD_INVALID_JSON:{form_id}")
            else:
                if gold.get("subject_visible") is not False:
                    policy_errors.append(
                        f"GOLD_SUBJECT_VISIBILITY_INVALID:{form_id}"
                    )

    for relative in actual:
        path = root / Path(relative)
        if not _is_within(path, root):
            policy_errors.append(f"MEMBER_ESCAPES_PACK_ROOT:{relative}")

    status = (
        "PASS"
        if not (
            missing
            or extra
            or hash_mismatches
            or malformed_checksums
            or symlinks
            or policy_errors
        )
        else "FAIL"
    )
    return {
        "status": status,
        "pack_root": str(root),
        "member_count": len(actual),
        "missing_files": sorted(set(missing)),
        "extra_files": sorted(set(extra)),
        "hash_mismatches": sorted(set(hash_mismatches)),
        "malformed_checksums": sorted(set(malformed_checksums)),
        "symlinks": sorted(set(symlinks)),
        "policy_errors": sorted(set(policy_errors)),
        "historical_source_pointer_present": (
            historical_source_pointer_present
        ),
        "historical_source_pointer_runtime_dereferenced": False,
    }


def inspect_public_assets() -> dict[str, Any]:
    """Verify the declassification envelope and frozen rehearsal receipt."""

    root = ASSET_ROOT.resolve()
    missing: list[str] = []
    extra: list[str] = []
    hash_mismatches: list[str] = []
    policy_errors: list[str] = []
    malformed_checksums: list[str] = []

    if not root.is_dir():
        return {
            "status": "FAIL",
            "missing_files": sorted(PUBLIC_REQUIRED_MEMBERS),
            "extra_files": [],
            "hash_mismatches": [],
            "malformed_checksums": [],
            "policy_errors": ["PUBLIC_ASSET_ROOT_MISSING"],
        }

    actual = {
        relative
        for relative in _relative_files(root)
        if not relative.startswith("reference_pack/")
    }
    missing.extend(sorted(PUBLIC_REQUIRED_MEMBERS - actual))
    extra.extend(sorted(actual - PUBLIC_REQUIRED_MEMBERS))

    checksums, malformed_checksums = _checksum_rows(root / "SHA256SUMS.txt")
    expected_checksum_members = PUBLIC_REQUIRED_MEMBERS - {"SHA256SUMS.txt"}
    if set(checksums) != expected_checksum_members:
        for relative in sorted(expected_checksum_members - set(checksums)):
            policy_errors.append(f"PUBLIC_CHECKSUM_MISSING:{relative}")
        for relative in sorted(set(checksums) - expected_checksum_members):
            policy_errors.append(f"PUBLIC_CHECKSUM_EXTRA:{relative}")
    for relative, expected in checksums.items():
        path = root / Path(relative)
        if path.is_file() and _sha256_file(path) != expected:
            hash_mismatches.append(relative)

    rehearsal_sums, rehearsal_errors = _checksum_rows(
        CANONICAL_LOW_COST_REHEARSAL / "SHA256SUMS.txt"
    )
    malformed_checksums.extend(rehearsal_errors)
    expected_rehearsal = {"run_manifest.json", "scores.json"}
    if set(rehearsal_sums) != expected_rehearsal:
        policy_errors.append("LOW_COST_REHEARSAL_EXACT_SET_MISMATCH")
    for relative, expected in rehearsal_sums.items():
        path = CANONICAL_LOW_COST_REHEARSAL / relative
        if path.is_file() and _sha256_file(path) != expected:
            hash_mismatches.append(f"low_cost_rehearsal/{relative}")

    manifest_path = root / "manifest.public.json"
    if manifest_path.is_file():
        try:
            manifest = _load_json(manifest_path)
        except (OSError, json.JSONDecodeError):
            policy_errors.append("PUBLIC_MANIFEST_INVALID_JSON")
        else:
            reference = manifest.get("reference_pack", {})
            if reference.get("classification") != "REFERENCE_EXAM_PACK":
                policy_errors.append("REFERENCE_CLASSIFICATION_MISSING")
            if reference.get("reference_regression_only") is not True:
                policy_errors.append("REFERENCE_REGRESSION_ONLY_REQUIRED")
            if reference.get("blind_holdout_eligible") is not False:
                policy_errors.append("BLIND_HOLDOUT_MUST_BE_FALSE")
            if reference.get("qualification_eligible") is not False:
                policy_errors.append("QUALIFICATION_MUST_BE_FALSE")
            if reference.get("gold_subject_visible") is not False:
                policy_errors.append("GOLD_SUBJECT_VISIBILITY_MUST_BE_FALSE")
            if reference.get("gold_provider_visible") is not False:
                policy_errors.append("GOLD_PROVIDER_VISIBILITY_MUST_BE_FALSE")
            provenance = manifest.get("source_provenance", {})
            if provenance.get("runtime_dereference_allowed") is not False:
                policy_errors.append(
                    "HISTORICAL_SOURCE_RUNTIME_DEREFERENCE_FORBIDDEN"
                )
    else:
        policy_errors.append("PUBLIC_MANIFEST_MISSING")

    rehearsal_manifest_path = (
        CANONICAL_LOW_COST_REHEARSAL / "run_manifest.json"
    )
    rehearsal_scores_path = CANONICAL_LOW_COST_REHEARSAL / "scores.json"
    if rehearsal_manifest_path.is_file() and rehearsal_scores_path.is_file():
        rehearsal_manifest = _load_json(rehearsal_manifest_path)
        rehearsal_scores = _load_json(rehearsal_scores_path)
        if (
            _sha256_file(rehearsal_manifest_path)
            != EXPECTED_REHEARSAL_MANIFEST_SHA256
        ):
            policy_errors.append("LOW_COST_MANIFEST_FROZEN_HASH_MISMATCH")
        if (
            _sha256_file(rehearsal_scores_path)
            != EXPECTED_REHEARSAL_SCORES_SHA256
        ):
            policy_errors.append("LOW_COST_SCORES_FROZEN_HASH_MISMATCH")
        if rehearsal_manifest.get("model") != EXPECTED_LOW_COST_MODEL:
            policy_errors.append("LOW_COST_MODEL_MISMATCH")
        if (
            rehearsal_manifest.get("cost_gate_status")
            != "LOW_COST_REHEARSAL_PASS"
        ):
            policy_errors.append("LOW_COST_REHEARSAL_PASS_MISSING")
        if (
            rehearsal_manifest.get("pack_manifest_sha256")
            != EXPECTED_PACK_MANIFEST_SHA256
        ):
            policy_errors.append("LOW_COST_PACK_BINDING_MISMATCH")
        if rehearsal_manifest.get("runner_sha256") != EXPECTED_RUNNER_SHA256:
            policy_errors.append("LOW_COST_RUNNER_BINDING_MISMATCH")
        if (
            rehearsal_manifest.get("api_parameters")
            != EXPECTED_API_PARAMETERS
        ):
            policy_errors.append("LOW_COST_API_PARAMETERS_MISMATCH")
        if rehearsal_manifest.get("gold_sent_to_provider") is not False:
            policy_errors.append("LOW_COST_GOLD_PROVIDER_ISOLATION_FAILED")
        if rehearsal_scores.get("verification_result") != "PASS":
            policy_errors.append("LOW_COST_SCORE_VERIFICATION_FAILED")

    status = (
        "PASS"
        if not (
            missing
            or extra
            or hash_mismatches
            or malformed_checksums
            or policy_errors
        )
        else "FAIL"
    )
    return {
        "status": status,
        "asset_root": str(root),
        "member_count": len(actual),
        "missing_files": sorted(set(missing)),
        "extra_files": sorted(set(extra)),
        "hash_mismatches": sorted(set(hash_mismatches)),
        "malformed_checksums": sorted(set(malformed_checksums)),
        "policy_errors": sorted(set(policy_errors)),
    }


def _load_bundled_tool() -> ModuleType:
    runner_path = (
        CANONICAL_REFERENCE_PACK / "scoring" / "reranker_exam_tool.py"
    )
    if not runner_path.is_file():
        raise RerankerTextExamError(
            "FORMAL_REFERENCE_PACK_MISSING:"
            "scoring/reranker_exam_tool.py"
        )
    module_name = "_m14_reranker_text_tool_" + _sha256_file(runner_path)[:16]
    module = ModuleType(module_name)
    module.__file__ = str(runner_path)
    module.__package__ = ""
    code = compile(runner_path.read_bytes(), str(runner_path), "exec")
    exec(code, module.__dict__)
    return module


def verify_canonical_reference_pack() -> dict[str, Any]:
    diagnosis = inspect_reference_pack()
    public_diagnosis = inspect_public_assets()
    if diagnosis["status"] != "PASS":
        raise RerankerTextExamError(
            "FORMAL_REFERENCE_PACK_INVALID:"
            + json.dumps(diagnosis, ensure_ascii=False, sort_keys=True)
        )
    if public_diagnosis["status"] != "PASS":
        raise RerankerTextExamError(
            "FORMAL_PUBLIC_ASSETS_INVALID:"
            + json.dumps(
                public_diagnosis,
                ensure_ascii=False,
                sort_keys=True,
            )
        )

    tool = _load_bundled_tool()
    replay = tool.verify_pack(CANONICAL_REFERENCE_PACK)
    rehearsal = tool.verify_rehearsal(
        CANONICAL_LOW_COST_REHEARSAL,
        EXPECTED_PACK_MANIFEST_SHA256,
    )
    member_paths = [
        path.resolve()
        for path in CANONICAL_REFERENCE_PACK.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    ]
    member_paths.extend(
        path.resolve()
        for path in CANONICAL_LOW_COST_REHEARSAL.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )
    outside_repo = [
        str(path) for path in member_paths if not _is_within(path, REPO_ROOT)
    ]
    if outside_repo:
        raise RerankerTextExamError(
            "FORMAL_RUNTIME_INPUT_OUTSIDE_REPOSITORY:"
            + json.dumps(outside_repo, ensure_ascii=False)
        )
    return {
        "verification_result": "PASS",
        "diagnosis": diagnosis,
        "public_diagnosis": public_diagnosis,
        "replay": replay,
        "low_cost_rehearsal_binding": rehearsal,
        "pack_revision": EXPECTED_PACK_REVISION,
        "manifest_sha256": EXPECTED_PACK_MANIFEST_SHA256,
        "runner_sha256": EXPECTED_RUNNER_SHA256,
        "runtime_input_member_count": len(member_paths),
        "runtime_input_outside_repo_count": 0,
        "sandbox_runtime_input_count": 0,
        "provider_api_calls": 0,
    }


def formal_pro_plan() -> dict[str, Any]:
    verification = verify_canonical_reference_pack()
    worst_case_cost = round(
        MAX_BILLED_INPUT_TOKENS
        * PRO_PRICE_CNY_PER_MILLION_INPUT_TOKENS
        / 1_000_000,
        8,
    )
    return {
        "plan_status": "READY",
        "profile_id": "siliconflow_pro_subject",
        "provider": "siliconflow",
        "model": EXPECTED_MODEL,
        "endpoint": EXPECTED_ENDPOINT,
        "forms": ["A", "B"],
        "items_per_form": 80,
        "candidate_pool_size": 16,
        "exam_logical_calls": EXAM_LOGICAL_CALLS,
        "capability_preflight_calls": CAPABILITY_PREFLIGHT_CALLS,
        "maximum_physical_attempts": MAX_PHYSICAL_ATTEMPTS,
        "max_attempts_per_exam_call": MAX_ATTEMPTS_PER_EXAM_CALL,
        "observed_rehearsal_input_tokens": (
            OBSERVED_REHEARSAL_INPUT_TOKENS
        ),
        "maximum_billed_input_tokens": MAX_BILLED_INPUT_TOKENS,
        "price_cny_per_million_input_tokens": (
            PRO_PRICE_CNY_PER_MILLION_INPUT_TOKENS
        ),
        "worst_case_cost_cny": worst_case_cost,
        "default_hard_cap_cny": DEFAULT_MAX_COST_CNY,
        "balance_gate": (
            "USER_AUTHORIZED_SINGLE_RUN_WITH_FIXED_HARD_CAP;"
            "NO_INDEPENDENT_BALANCE_ENDPOINT"
        ),
        "api_parameters": EXPECTED_API_PARAMETERS,
        "canonical_reference_pack": str(
            CANONICAL_REFERENCE_PACK.resolve()
        ),
        "canonical_low_cost_rehearsal": str(
            CANONICAL_LOW_COST_REHEARSAL.resolve()
        ),
        "reference_pack_manifest_sha256": (
            verification["manifest_sha256"]
        ),
        "reference_pack_runner_sha256": verification["runner_sha256"],
        "low_cost_rehearsal_binding": (
            verification["low_cost_rehearsal_binding"]
        ),
        "sandbox_input_allowed": False,
        "provider_api_calls": 0,
    }


def _write_formal_checksums(output_dir: Path) -> str:
    rows: list[str] = []
    for path in sorted(output_dir.rglob("*")):
        if path.is_file() and path.name != "FORMAL_SHA256SUMS.txt":
            rows.append(
                f"{_sha256_file(path)}  "
                f"{path.relative_to(output_dir).as_posix()}"
            )
    target = output_dir / "FORMAL_SHA256SUMS.txt"
    target.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return _sha256_file(target)


def _formal_runtime_provenance(
    verification: dict[str, Any],
) -> dict[str, Any]:
    paths = sorted(
        str(path.resolve())
        for root in (
            CANONICAL_REFERENCE_PACK,
            CANONICAL_LOW_COST_REHEARSAL,
        )
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )
    return {
        "schema_version": "m14-reranker-text-formal-input-provenance-v1",
        "cwd": str(Path.cwd().resolve()),
        "repo_root": str(REPO_ROOT.resolve()),
        "canonical_reference_pack": str(
            CANONICAL_REFERENCE_PACK.resolve()
        ),
        "canonical_low_cost_rehearsal": str(
            CANONICAL_LOW_COST_REHEARSAL.resolve()
        ),
        "runtime_input_member_paths": paths,
        "reference_pack_manifest_sha256": (
            verification["manifest_sha256"]
        ),
        "reference_pack_runner_sha256": verification["runner_sha256"],
        "sandbox_input_paths": [],
        "sandbox_input_used": False,
        "historical_source_pointer_runtime_dereferenced": False,
        "gold_sent_to_subject": False,
        "gold_sent_to_provider": False,
        "provider_request_allowlist": [
            "model",
            "query",
            "documents",
            "return_documents",
            "top_n",
            "max_chunks_per_doc",
            "overlap_tokens",
        ],
    }


def _capability_preflight(
    tool: ModuleType,
    api_key: str,
) -> dict[str, Any]:
    documents = [
        f"Capability probe document {index:02d}: "
        + (
            "the exact capability marker is present"
            if index == 0
            else "this is an unrelated generic statement"
        )
        for index in range(16)
    ]
    rankings, receipt = tool.api_call(
        endpoint=EXPECTED_ENDPOINT,
        api_key=api_key,
        model=EXPECTED_MODEL,
        query="Rank the exact capability marker ahead of unrelated text.",
        documents=documents,
        max_attempts=1,
    )
    return {
        "schema_version": "m14-reranker-text-capability-preflight-v1",
        "status": "PASS",
        "model": EXPECTED_MODEL,
        "endpoint": EXPECTED_ENDPOINT,
        "evaluation_content_included": False,
        "gold_included": False,
        "counts_as_complete_exam": False,
        "document_count": len(documents),
        "result_count": len(rankings),
        "top_result_index": rankings[0]["index"],
        "receipt": receipt,
    }


def run_formal_pro_exam(
    output_dir: Path,
    *,
    max_cost_cny: float = DEFAULT_MAX_COST_CNY,
) -> dict[str, Any]:
    """Run one canonical Pro exam without accepting arbitrary input paths."""

    if Path.cwd().resolve() != REPO_ROOT.resolve():
        raise RerankerTextExamError(
            "FORMAL_RUN_CWD_MISMATCH:"
            f"expected={REPO_ROOT.resolve()}:observed={Path.cwd().resolve()}"
        )
    output = output_dir.resolve()
    if not _is_within(output, OPS_TEST_ROOT):
        raise RerankerTextExamError(
            "FORMAL_RUN_OUTPUT_ROOT_MISMATCH:"
            f"expected_under={OPS_TEST_ROOT.resolve()}:observed={output}"
        )
    if output.exists():
        raise RerankerTextExamError(
            f"FORMAL_RUN_OUTPUT_ALREADY_EXISTS:{output}"
        )

    verification = verify_canonical_reference_pack()
    plan = formal_pro_plan()
    if plan["worst_case_cost_cny"] > max_cost_cny:
        raise RerankerTextExamError(
            "FORMAL_RUN_COST_CAP_EXCEEDED:"
            f"{plan['worst_case_cost_cny']}>{max_cost_cny}"
        )
    key = os.environ.get("SILICONFLOW_API_KEY")
    if not key:
        raise RerankerTextExamError(
            "REQUIRED_API_KEY_ENV_ABSENT:SILICONFLOW_API_KEY"
        )

    output.mkdir(parents=True, exist_ok=False)
    preflight_root = output / "00_preflight"
    preflight_root.mkdir()
    contract = {
        "schema_version": "m14-reranker-text-formal-run-contract-v1",
        "authorization": (
            "USER_APPROVED_FORMAL_WRITE_AND_ONE_COMPLETE_TEST_2026-07-31"
        ),
        "run_count_authorized": 1,
        "model": EXPECTED_MODEL,
        "pack_revision": EXPECTED_PACK_REVISION,
        "pack_manifest_sha256": EXPECTED_PACK_MANIFEST_SHA256,
        "runner_sha256": EXPECTED_RUNNER_SHA256,
        "low_cost_rehearsal_manifest_sha256": (
            EXPECTED_REHEARSAL_MANIFEST_SHA256
        ),
        "low_cost_rehearsal_scores_sha256": (
            EXPECTED_REHEARSAL_SCORES_SHA256
        ),
        "api_parameters": EXPECTED_API_PARAMETERS,
        "plan": plan,
        "hard_cost_cap_cny": max_cost_cny,
        "formal_commit_authorized": False,
        "formal_push_authorized": False,
    }
    _write_json(preflight_root / "formal_run_contract.json", contract)
    _write_json(
        output / "formal_input_provenance.json",
        _formal_runtime_provenance(verification),
    )

    tool = _load_bundled_tool()
    try:
        capability = _capability_preflight(tool, key)
        _write_json(
            preflight_root / "capability_preflight.json",
            capability,
        )
        run_result = tool.run_api(
            pack_dir=CANONICAL_REFERENCE_PACK,
            output=output / "10_exam",
            profile_id="siliconflow_pro_subject",
            model=EXPECTED_MODEL,
            endpoint=EXPECTED_ENDPOINT,
            api_key_env="SILICONFLOW_API_KEY",
            workers=6,
            max_attempts=MAX_ATTEMPTS_PER_EXAM_CALL,
            repeat_per_family=2,
            rehearsal_run=CANONICAL_LOW_COST_REHEARSAL,
        )
    except Exception as exc:
        _write_json(
            output / "formal_failure_receipt.json",
            {
                "schema_version": (
                    "m14-reranker-text-formal-failure-receipt-v1"
                ),
                "lifecycle_status": "completed_unscored",
                "verification_result": "ERROR",
                "acceptance_verdict": "NOT_ASSESSED",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "canonical_missing_file_diagnosis": (
                    inspect_reference_pack()
                ),
                "public_asset_diagnosis": inspect_public_assets(),
                "provider_retry_authorized": False,
                "sandbox_input_used": False,
            },
        )
        _write_formal_checksums(output)
        raise

    scores = _load_json(output / "10_exam" / "scores.json")
    run_manifest = _load_json(output / "10_exam" / "run_manifest.json")
    call_ledger = _load_json(output / "10_exam" / "call_ledger.json")
    calls = call_ledger.get("calls", [])
    if scores.get("verification_result") != "PASS":
        raise RerankerTextExamError("FORMAL_RUN_SCORE_VERIFICATION_FAILED")
    if run_manifest.get("model") != EXPECTED_MODEL:
        raise RerankerTextExamError("FORMAL_RUN_MODEL_IDENTITY_MISMATCH")
    if run_manifest.get("main_call_count") != 160:
        raise RerankerTextExamError("FORMAL_RUN_MAIN_CALL_COUNT_MISMATCH")
    if run_manifest.get("repeat_call_count") != 28:
        raise RerankerTextExamError("FORMAL_RUN_REPEAT_CALL_COUNT_MISMATCH")
    if run_manifest.get("gold_sent_to_provider") is not False:
        raise RerankerTextExamError("FORMAL_RUN_GOLD_ISOLATION_FAILED")
    if len(calls) != EXAM_LOGICAL_CALLS:
        raise RerankerTextExamError("FORMAL_RUN_LEDGER_COUNT_MISMATCH")

    input_tokens = sum(
        int(
            (row.get("meta") or {})
            .get("tokens", {})
            .get("input_tokens", 0)
        )
        for row in calls
    )
    preflight_tokens = int(
        capability.get("receipt", {})
        .get("meta", {})
        .get("tokens", {})
        .get("input_tokens", 0)
    )
    total_input_tokens = input_tokens + preflight_tokens
    estimated_cost = round(
        total_input_tokens
        * PRO_PRICE_CNY_PER_MILLION_INPUT_TOKENS
        / 1_000_000,
        8,
    )
    retry_count = sum(
        max(0, int(row.get("attempt_count", 1)) - 1) for row in calls
    )
    trace_count = sum(bool(row.get("trace_id")) for row in calls)
    formal_receipt = {
        "schema_version": "m14-reranker-text-formal-run-receipt-v1",
        "run_id": output.name,
        "lifecycle_status": "completed",
        "verification_result": "PASS",
        "acceptance_verdict": "NOT_ASSESSED",
        "reference_regression_only": True,
        "blind_holdout_eligible": False,
        "qualification_eligible": False,
        "provider": "siliconflow",
        "model": EXPECTED_MODEL,
        "pack_revision": EXPECTED_PACK_REVISION,
        "pack_manifest_sha256": EXPECTED_PACK_MANIFEST_SHA256,
        "runner_sha256": EXPECTED_RUNNER_SHA256,
        "cost_gate_status": "SUBJECT_RUN_BOUND_TO_REHEARSAL_PASS",
        "capability_preflight_status": capability["status"],
        "main_calls": run_manifest["main_call_count"],
        "repeat_calls": run_manifest["repeat_call_count"],
        "exam_logical_calls": len(calls),
        "retry_count": retry_count,
        "trace_id_count": trace_count,
        "input_tokens_exam": input_tokens,
        "input_tokens_capability_preflight": preflight_tokens,
        "input_tokens_total": total_input_tokens,
        "estimated_cost_cny": estimated_cost,
        "hard_cost_cap_cny": max_cost_cny,
        "raw_score": scores["raw_score"],
        "selection_score": scores["selection_score"],
        "form_scores": {
            form_id: {
                "raw_score": scores["forms"][form_id]["raw_score"],
                "selection_score": (
                    scores["forms"][form_id]["selection_score"]
                ),
            }
            for form_id in ("A", "B")
        },
        "aggregate_metrics": scores["aggregate_metrics"],
        "scorer_internal_acceptance_verdict": (
            scores.get("acceptance_verdict")
        ),
        "scorer_internal_verdict_interpretation": (
            "LOCAL_SCORE_INTEGRITY_ONLY_NOT_FORMAL_QUALIFICATION"
        ),
        "latency_ms": run_result["latency_ms"],
        "gold_sent_to_provider": False,
        "sandbox_input_used": False,
        "historical_source_pointer_runtime_dereferenced": False,
        "git_commit_performed": False,
        "git_push_performed": False,
    }
    _write_json(output / "formal_run_receipt.json", formal_receipt)
    formal_sums_sha256 = _write_formal_checksums(output)
    return {
        **formal_receipt,
        "output_dir": str(output),
        "formal_sha256sums_sha256": formal_sums_sha256,
    }
