"""Formal M14 embedding_text reference-exam integration.

The formal run is intentionally bound to one repository-resident reference
pack.  Gold is loaded only by the local scorer.  Provider requests contain
only the frozen synthetic document/query strings and the requested model ID.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import ModuleType
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = (
    Path(__file__).resolve().parent
    / "assets"
    / "embedding_text_exam"
    / "v1"
)
CANONICAL_REFERENCE_PACK = ASSET_ROOT / "reference_pack"
SELECTION_SCALE_PATH = ASSET_ROOT / "selection" / "selection_scale_r0_3.json"
EXPECTED_PACK_REVISION = "embedding_text_reference_exam_r0.5"
EXPECTED_MODEL = "Pro/BAAI/bge-m3"
EXPECTED_DIMENSION = 1024
BASE_REQUIRED_MEMBERS = {
    "README.md",
    "manifest.reference.json",
    "SHA256SUMS.txt",
    "corpora/form_A_corpora.json",
    "corpora/form_B_corpora.json",
    "forms/form_A.json",
    "forms/form_B.json",
    "gold/form_A_gold.json",
    "gold/form_B_gold.json",
    "profiles/reference_profiles.json",
    "scoring/embedding_exam_tool.py",
    "scoring/scoring_protocol.json",
}


class EmbeddingTextExamError(RuntimeError):
    """Fail-closed formal embedding exam error."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
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
        return rows, ["SHA256SUMS.txt"]
    for line_number, line in enumerate(
        checksum_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        parts = line.split("  ", 1)
        if len(parts) != 2 or len(parts[0]) != 64:
            errors.append(f"SHA256SUMS.txt:line_{line_number}")
            continue
        digest, relative = parts
        normalized = Path(relative).as_posix()
        if normalized in rows:
            errors.append(f"SHA256SUMS.txt:duplicate:{normalized}")
            continue
        rows[normalized] = digest.lower()
    return rows, errors


def inspect_reference_pack(
    pack_root: Path = CANONICAL_REFERENCE_PACK,
) -> dict[str, Any]:
    """Return an exact missing/extra/hash diagnosis without provider calls."""

    root = pack_root.resolve()
    missing: list[str] = []
    extra: list[str] = []
    hash_mismatches: list[str] = []
    malformed_checksums: list[str] = []
    symlinks: list[str] = []
    policy_errors: list[str] = []

    if not root.is_dir():
        missing.extend(sorted(BASE_REQUIRED_MEMBERS))
        return {
            "status": "FAIL",
            "pack_root": str(root),
            "missing_files": missing,
            "extra_files": extra,
            "hash_mismatches": hash_mismatches,
            "malformed_checksums": malformed_checksums,
            "symlinks": symlinks,
            "policy_errors": ["REFERENCE_PACK_ROOT_MISSING"],
        }

    for path in root.rglob("*"):
        if path.is_symlink():
            symlinks.append(path.relative_to(root).as_posix())

    checksums, malformed_checksums = _checksum_rows(root / "SHA256SUMS.txt")
    declared = set(checksums) | {"SHA256SUMS.txt"}
    actual = _relative_files(root)
    missing.extend(sorted(BASE_REQUIRED_MEMBERS - actual))
    missing.extend(sorted(declared - actual - set(missing)))
    extra.extend(sorted(actual - declared))

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
            classification = manifest.get("classification", {})
            if classification.get("reference_regression_only") is not True:
                policy_errors.append("REFERENCE_REGRESSION_ONLY_REQUIRED")
            if classification.get("blind_holdout_eligible") is not False:
                policy_errors.append("BLIND_HOLDOUT_MUST_BE_FALSE")
            if classification.get("qualification_eligible") is not False:
                policy_errors.append("QUALIFICATION_MUST_BE_FALSE")
            if classification.get("gold_provider_visibility") is not False:
                policy_errors.append("GOLD_PROVIDER_VISIBILITY_MUST_BE_FALSE")
            generator = manifest.get("generator", {})
            scorer_path = root / "scoring" / "embedding_exam_tool.py"
            if (
                scorer_path.is_file()
                and _sha256_file(scorer_path) != generator.get("sha256")
            ):
                policy_errors.append("BUNDLED_SCORER_HASH_MISMATCH")
            for source in manifest.get("source_freeze", []):
                source_path = str(source.get("path", ""))
                if "PR-OS-沙盒" in source_path:
                    policy_errors.append("SANDBOX_SOURCE_POINTER_FORBIDDEN")
    else:
        policy_errors.append("REFERENCE_MANIFEST_MISSING")

    for path in actual:
        if "PR-OS-沙盒" in path:
            policy_errors.append("SANDBOX_MEMBER_PATH_FORBIDDEN")

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
        "declared_member_count": len(declared),
        "missing_files": sorted(set(missing)),
        "extra_files": sorted(set(extra)),
        "hash_mismatches": sorted(set(hash_mismatches)),
        "malformed_checksums": sorted(set(malformed_checksums)),
        "symlinks": sorted(set(symlinks)),
        "policy_errors": sorted(set(policy_errors)),
    }


def _load_bundled_scorer(
    pack_root: Path = CANONICAL_REFERENCE_PACK,
) -> ModuleType:
    scorer_path = pack_root.resolve() / "scoring" / "embedding_exam_tool.py"
    if not scorer_path.is_file():
        raise EmbeddingTextExamError(
            "FORMAL_REFERENCE_PACK_MISSING:scoring/embedding_exam_tool.py"
        )
    module_name = (
        "_m14_embedding_text_scorer_"
        + _sha256_file(scorer_path)[:16]
    )
    module = ModuleType(module_name)
    module.__file__ = str(scorer_path)
    module.__package__ = ""
    code = compile(
        scorer_path.read_bytes(),
        str(scorer_path),
        "exec",
    )
    exec(code, module.__dict__)
    return module


def load_selection_scale() -> dict[str, Any]:
    if not SELECTION_SCALE_PATH.is_file():
        raise EmbeddingTextExamError(
            "FORMAL_SELECTION_SCALE_MISSING:"
            + str(SELECTION_SCALE_PATH.relative_to(REPO_ROOT))
        )
    protocol = _load_json(SELECTION_SCALE_PATH)
    if protocol.get("protocol_revision") != "embedding_text_selection_scale_r0.3":
        raise EmbeddingTextExamError("FORMAL_SELECTION_SCALE_REVISION_MISMATCH")
    anchors = protocol.get("anchors", [])
    if len(anchors) < 2:
        raise EmbeddingTextExamError("FORMAL_SELECTION_SCALE_ANCHORS_INVALID")
    xs = [float(row["raw"]) for row in anchors]
    ys = [float(row["calibrated"]) for row in anchors]
    if not all(left < right for left, right in zip(xs, xs[1:])):
        raise EmbeddingTextExamError("FORMAL_SELECTION_SCALE_RAW_NOT_MONOTONIC")
    if not all(left < right for left, right in zip(ys, ys[1:])):
        raise EmbeddingTextExamError(
            "FORMAL_SELECTION_SCALE_CALIBRATED_NOT_MONOTONIC"
        )
    if xs[0] != 0.0 or ys[0] != 0.0 or xs[-1] != 100.0 or ys[-1] != 100.0:
        raise EmbeddingTextExamError("FORMAL_SELECTION_SCALE_ENDPOINT_INVALID")
    return protocol


def calibrate_selection_score(raw_score: float) -> float:
    protocol = load_selection_scale()
    anchors = [
        (float(row["raw"]), float(row["calibrated"]))
        for row in protocol["anchors"]
    ]
    raw = float(raw_score)
    if raw <= anchors[0][0]:
        return anchors[0][1]
    if raw >= anchors[-1][0]:
        return anchors[-1][1]
    for (x0, y0), (x1, y1) in zip(anchors, anchors[1:]):
        if x0 <= raw <= x1:
            ratio = (raw - x0) / (x1 - x0)
            return y0 + ratio * (y1 - y0)
    raise EmbeddingTextExamError("FORMAL_SELECTION_SCALE_INTERVAL_MISSING")


def verify_canonical_reference_pack() -> dict[str, Any]:
    diagnosis = inspect_reference_pack(CANONICAL_REFERENCE_PACK)
    if diagnosis["status"] != "PASS":
        raise EmbeddingTextExamError(
            "FORMAL_REFERENCE_PACK_INVALID:"
            + json.dumps(diagnosis, ensure_ascii=False, sort_keys=True)
        )
    scorer = _load_bundled_scorer(CANONICAL_REFERENCE_PACK)
    replay = scorer.verify_reference_pack(CANONICAL_REFERENCE_PACK)
    manifest = _load_json(CANONICAL_REFERENCE_PACK / "manifest.reference.json")
    scale = load_selection_scale()
    return {
        "verification_result": "PASS",
        "diagnosis": diagnosis,
        "replay": replay,
        "pack_revision": manifest["pack_revision"],
        "manifest_sha256": _sha256_file(
            CANONICAL_REFERENCE_PACK / "manifest.reference.json"
        ),
        "checksums_sha256": _sha256_file(
            CANONICAL_REFERENCE_PACK / "SHA256SUMS.txt"
        ),
        "scorer_sha256": _sha256_file(
            CANONICAL_REFERENCE_PACK
            / "scoring"
            / "embedding_exam_tool.py"
        ),
        "selection_scale_revision": scale["protocol_revision"],
        "selection_scale_sha256": _sha256_file(SELECTION_SCALE_PATH),
        "provider_api_calls": 0,
    }


def formal_pro_plan() -> dict[str, Any]:
    verification = verify_canonical_reference_pack()
    scorer = _load_bundled_scorer(CANONICAL_REFERENCE_PACK)
    profile = scorer.PROFILES["pro"]
    if profile["model"] != EXPECTED_MODEL:
        raise EmbeddingTextExamError("FORMAL_PRO_PROFILE_MODEL_MISMATCH")
    budget = scorer.estimate_run_budget(
        CANONICAL_REFERENCE_PACK,
        ["A", "B"],
        None,
        2,
        profile["price_cny_per_million_tokens"],
        2,
    )
    return {
        "plan_status": "READY",
        "profile_key": "pro",
        "model": profile["model"],
        "provider": profile["provider"],
        "forms": ["A", "B"],
        "expected_dimension": EXPECTED_DIMENSION,
        "budget": budget,
        "canonical_reference_pack": str(CANONICAL_REFERENCE_PACK.resolve()),
        "reference_pack_manifest_sha256": verification["manifest_sha256"],
        "reference_pack_scorer_sha256": verification["scorer_sha256"],
        "selection_scale_sha256": verification["selection_scale_sha256"],
        "sandbox_input_allowed": False,
        "provider_api_calls": 0,
    }


def _write_formal_checksums(output_dir: Path) -> str:
    rows = []
    for path in sorted(output_dir.rglob("*")):
        if (
            path.is_file()
            and path.name != "FORMAL_SHA256SUMS.txt"
            and "chroma" not in path.parts
        ):
            rows.append(
                f"{_sha256_file(path)}  "
                f"{path.relative_to(output_dir).as_posix()}"
            )
    target = output_dir / "FORMAL_SHA256SUMS.txt"
    target.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return _sha256_file(target)


def run_formal_pro_exam(
    output_dir: Path,
    *,
    max_cost_cny: float = 0.40,
    timeout_seconds: float = 120.0,
) -> dict[str, Any]:
    """Run the canonical Pro exam; no arbitrary pack path is accepted."""

    if Path.cwd().resolve() != REPO_ROOT.resolve():
        raise EmbeddingTextExamError(
            "FORMAL_RUN_CWD_MISMATCH:"
            f"expected={REPO_ROOT.resolve()}:observed={Path.cwd().resolve()}"
        )
    output = output_dir.resolve()
    if output.exists():
        raise EmbeddingTextExamError(
            f"FORMAL_RUN_OUTPUT_ALREADY_EXISTS:{output}"
        )
    verification = verify_canonical_reference_pack()
    plan = formal_pro_plan()
    if plan["budget"]["worst_case_cost_cny"] > max_cost_cny:
        raise EmbeddingTextExamError(
            "FORMAL_RUN_COST_CAP_EXCEEDED:"
            f"{plan['budget']['worst_case_cost_cny']}>{max_cost_cny}"
        )
    scorer = _load_bundled_scorer(CANONICAL_REFERENCE_PACK)
    result = scorer.run_profile(
        CANONICAL_REFERENCE_PACK,
        output,
        "pro",
        ["A", "B"],
        None,
        2,
        timeout_seconds,
        2,
        max_cost_cny,
        True,
    )
    technical = result["technical"]
    if technical.get("dimension") != EXPECTED_DIMENSION:
        raise EmbeddingTextExamError(
            "FORMAL_RUN_DIMENSION_MISMATCH:"
            f"{technical.get('dimension')}!={EXPECTED_DIMENSION}"
        )
    if technical.get("requested_model") != EXPECTED_MODEL:
        raise EmbeddingTextExamError("FORMAL_RUN_MODEL_IDENTITY_MISMATCH")
    raw_scores = result["scores"]
    form_a_raw = float(raw_scores["forms"]["A"]["score"])
    form_b_raw = float(raw_scores["forms"]["B"]["score"])
    form_a = calibrate_selection_score(form_a_raw)
    form_b = calibrate_selection_score(form_b_raw)
    selection = {
        "schema_version": "m14-embedding-text-selection-scorecard-v1",
        "status": "POST_HOC_USER_DIRECTED_CALIBRATION",
        "profile_key": "pro",
        "model": EXPECTED_MODEL,
        "pack_revision": EXPECTED_PACK_REVISION,
        "raw_deduction_score": {
            "form_A": form_a_raw,
            "form_B": form_b_raw,
            "mean": float(raw_scores["cross_form"]["mean_score"]),
        },
        "calibrated_selection_score": {
            "form_A": round(form_a, 6),
            "form_B": round(form_b, 6),
            "mean": round((form_a + form_b) / 2.0, 6),
            "minimum_form": round(min(form_a, form_b), 6),
            "form_gap": round(abs(form_a - form_b), 6),
        },
        "critical_misses": int(
            raw_scores["cross_form"]["critical_miss_count"]
        ),
        "admission_verdict": "NOT_ASSESSED",
        "qualification_eligible": False,
        "warning": (
            "The calibrated selection scale was frozen after the earlier raw "
            "campaign; it is not a pre-registered admission threshold."
        ),
    }
    _write_json(output / "selection_scorecard.json", selection)
    member_paths = sorted(
        str(path.resolve())
        for path in CANONICAL_REFERENCE_PACK.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )
    if any("PR-OS-沙盒" in path for path in member_paths):
        raise EmbeddingTextExamError("FORMAL_RUN_SANDBOX_INPUT_DETECTED")
    provenance = {
        "schema_version": "m14-embedding-text-formal-input-provenance-v1",
        "cwd": str(Path.cwd().resolve()),
        "repo_root": str(REPO_ROOT.resolve()),
        "canonical_reference_pack": str(
            CANONICAL_REFERENCE_PACK.resolve()
        ),
        "canonical_member_paths": member_paths,
        "selection_scale_path": str(SELECTION_SCALE_PATH.resolve()),
        "reference_pack_manifest_sha256": verification["manifest_sha256"],
        "reference_pack_checksums_sha256": verification["checksums_sha256"],
        "reference_pack_scorer_sha256": verification["scorer_sha256"],
        "selection_scale_sha256": verification["selection_scale_sha256"],
        "sandbox_input_paths": [],
        "sandbox_input_used": False,
        "gold_sent_to_provider": False,
        "provider_request_allowlist": [
            "model",
            "input",
            "encoding_format",
        ],
    }
    _write_json(output / "formal_input_provenance.json", provenance)
    formal_sums_sha256 = _write_formal_checksums(output)
    return {
        "run_status": "completed",
        "verification_result": "PASS",
        "acceptance_verdict": "NOT_ASSESSED",
        "output_dir": str(output),
        "model": EXPECTED_MODEL,
        "raw_mean": selection["raw_deduction_score"]["mean"],
        "calibrated_mean": selection["calibrated_selection_score"]["mean"],
        "critical_misses": selection["critical_misses"],
        "technical": technical,
        "formal_sha256sums_sha256": formal_sums_sha256,
        "sandbox_input_used": False,
    }
