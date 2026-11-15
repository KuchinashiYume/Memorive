"""Read-only source / write-only exact run-context command-line entry point."""

from __future__ import annotations

import argparse
import json
import re
import stat
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from .adapters import OFFICIAL_VALIDATOR_RECEIPTS
from .authority import (
    _json_value,
    load_actor_authority,
    load_common_reference_authority,
    load_common_schema,
    load_historical_authority,
    load_evidence_review_authority,
    load_prior_digest_authority,
)
from .canonical import canonical_json_bytes, pretty_json_bytes, sha256_bytes, write_new_bytes, write_new_json
from .digest import GENERATOR_VERSION, build_digest
from .errors import ResearchReportsError, ManifestIntegrityError, OutputBoundaryError
from .issues import to_runtime_log_error_candidate
from .renderer import render_markdown
from .source import load_source_snapshot
from .initialization_pack import validate_data_contracts_domain_pack

CONTEXT_FIELDS = {
    "schema_version",
    "candidate_id",
    "stage",
    "successor_root",
    "run_root",
    "source_manifest_sha256",
    "candidate_generator_version",
    "exact_output_dir",
    "output_must_be_absent",
    "input_anchors",
    "cli_identity",
}
INPUT_ANCHOR_PATHS = {
    "source_manifest_schema": "validator/source_snapshot_manifest_candidate002.schema.json",
    "common_object_schema": "repo/contracts/Research_研究运营/10_模块契约/Memorive_RESEARCH_CONTRACTS_CROSS_common_object_contract_v2.schema.json",
    "evidence_review_authority": "00_manifest/knowledge_admission_evidence_review_evidence_authority_candidate002.json",
    "actor_authority": "00_manifest/initialization_actor_reference_authority_candidate002.json",
    "common_reference_authority": "00_manifest/common_object_reference_authority_candidate002.json",
    "prior_digest_authority": "00_manifest/prior_digest_authority_candidate002.json",
    "historical_cutoff_authority": "00_manifest/historical_cutoff_authority_candidate002.json",
}
INPUT_ANCHOR_ROLES = set(INPUT_ANCHOR_PATHS) | {"source_manifest"}
CLI_IDENTITY_FIELDS = {
    "digest_kind", "window_start", "window_end", "cutoff_at", "revision", "supersedes_digest_id"
}
RUN_ATTEMPT_RE = re.compile(r"A_ATTEMPT00[4-8]")
EXACT_SUCCESSOR_ROOT_NAME = (
    "research_reports_research_reports_v1_successor002_20260802_1620"
)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _is_reparse(path: Path) -> bool:
    info = path.lstat()
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse_flag)


def _assert_existing_chain_plain(path: Path, root: Path) -> None:
    current = path
    while True:
        if not current.exists() or _is_reparse(current):
            raise OutputBoundaryError(f"existing output ancestor is missing or reparse: {current}")
        if current == root:
            return
        try:
            current.relative_to(root)
        except ValueError as exc:
            raise OutputBoundaryError("output ancestor escapes successor root") from exc
        if current.parent == current:
            raise OutputBoundaryError("output ancestor never reached successor root")
        current = current.parent


def _canonical_absolute(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise OutputBoundaryError(f"{label} must be absolute canonical text")
    path = Path(value)
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise OutputBoundaryError(f"{label} must be absolute canonical text")
    if value != str(path.resolve()):
        raise OutputBoundaryError(f"{label} must equal its canonical absolute path")
    return path


def _anchor_path(successor_root: Path, entry: object, role: str) -> Path:
    if not isinstance(entry, dict) or set(entry) != {"path", "bytes", "sha256"}:
        raise ManifestIntegrityError(f"input anchor {role} fields mismatch")
    relative = entry["path"]
    if (
        not isinstance(relative, str)
        or not relative
        or "\\" in relative
        or any(char in relative for char in (":", "#", "?"))
        or any(part in {"", ".", ".."} for part in relative.split("/"))
        or relative.startswith("/")
    ):
        raise ManifestIntegrityError(f"input anchor {role} path is not canonical relative POSIX")
    if role in INPUT_ANCHOR_PATHS and relative != INPUT_ANCHOR_PATHS[role]:
        raise ManifestIntegrityError(f"input anchor {role} path mismatch")
    if role == "source_manifest" and not re.fullmatch(r"00_manifest/source_snapshot(?:_[a-z0-9_]+)?_manifest\.json|00_manifest/source_snapshot_manifest\.json", relative):
        raise ManifestIntegrityError("source manifest anchor path is outside the frozen manifest family")
    target = successor_root / relative
    if not target.is_file() or _is_reparse(target):
        raise ManifestIntegrityError(f"input anchor {role} target missing/reparse")
    _assert_existing_chain_plain(target.parent, successor_root)
    size = entry["bytes"]
    digest = entry["sha256"]
    if (
        isinstance(size, bool)
        or not isinstance(size, int)
        or size < 1
        or not isinstance(digest, str)
        or not re.fullmatch(r"[A-F0-9]{64}", digest)
    ):
        raise ManifestIntegrityError(f"input anchor {role} metadata type mismatch")
    raw = target.read_bytes()
    if len(raw) != size or sha256_bytes(raw) != digest:
        raise ManifestIntegrityError(f"input anchor {role} file binding mismatch")
    return target


def _candidate_successor_root(value: object) -> Path:
    root = _canonical_absolute(value, "successor_root")
    if root.name != EXACT_SUCCESSOR_ROOT_NAME:
        raise OutputBoundaryError("candidate package is outside the exact successor002 root")
    if not root.is_dir() or _is_reparse(root):
        raise OutputBoundaryError("successor_root must be an existing plain directory")
    _assert_existing_chain_plain(root, root)
    return root


def _load_run_context(
    context_path: str | Path,
    *,
    output_dir: str | Path,
    source_manifest: str | Path,
) -> tuple[dict, Path, Path]:
    path = _canonical_absolute(context_path, "run context")
    if not path.is_file() or _is_reparse(path):
        raise OutputBoundaryError("run context must be an existing absolute regular file")
    value = _json_value(path.read_bytes(), "run context")
    if not isinstance(value, dict) or set(value) != CONTEXT_FIELDS:
        raise OutputBoundaryError("run context fields mismatch")
    if (
        value["schema_version"] != "1.0"
        or value["candidate_id"] != "candidate002"
        or value["stage"] not in {"A", "B"}
        or value["candidate_generator_version"] != GENERATOR_VERSION
        or value["output_must_be_absent"] is not True
    ):
        raise OutputBoundaryError("run context identity/policy mismatch")
    successor_root = _candidate_successor_root(value["successor_root"])
    run_root = _canonical_absolute(value["run_root"], "run_root")
    expected_parent = successor_root / ("20_A" if value["stage"] == "A" else "30_B")
    if run_root.parent != expected_parent.resolve():
        raise OutputBoundaryError("run_root is outside the stage exact child boundary")
    if value["stage"] == "A" and not RUN_ATTEMPT_RE.fullmatch(run_root.name):
        raise OutputBoundaryError("A run_root must be A_ATTEMPT004 through A_ATTEMPT008")
    if not run_root.is_dir():
        raise OutputBoundaryError("run_root must already exist")
    _assert_existing_chain_plain(run_root, successor_root)
    if path.resolve().parent != (run_root / "run_contexts").resolve():
        raise OutputBoundaryError("run context must be a direct member of run_root/run_contexts")

    if not isinstance(output_dir, (str, Path)) or str(output_dir) != value["exact_output_dir"]:
        raise OutputBoundaryError("output differs from run context exact_output_dir")
    target = _canonical_absolute(value["exact_output_dir"], "exact_output_dir")
    if target.exists():
        raise OutputBoundaryError("exact output directory must be absent")
    if not _is_within(target, run_root) or target == run_root:
        raise OutputBoundaryError("exact output directory must be below run_root")
    if not target.parent.is_dir():
        raise OutputBoundaryError("CLI must not create the output parent")
    _assert_existing_chain_plain(target.parent, successor_root)

    if not isinstance(source_manifest, (str, Path)):
        raise ManifestIntegrityError("source manifest path type mismatch")
    manifest_path = _canonical_absolute(str(source_manifest), "source manifest")
    anchors = value["input_anchors"]
    if not isinstance(anchors, dict) or set(anchors) != INPUT_ANCHOR_ROLES:
        raise ManifestIntegrityError("input_anchors role set mismatch")
    resolved_anchors = {
        role: _anchor_path(successor_root, anchors[role], role)
        for role in sorted(INPUT_ANCHOR_ROLES)
    }
    if manifest_path != resolved_anchors["source_manifest"]:
        raise ManifestIntegrityError("source manifest argument differs from frozen anchor")
    if not manifest_path.is_file() or _is_reparse(manifest_path):
        raise ManifestIntegrityError("source manifest must be an existing absolute regular file")
    if manifest_path.resolve().parent != (successor_root / "00_manifest").resolve():
        raise ManifestIntegrityError("source manifest must be a frozen successor manifest")
    observed_manifest_hash = anchors["source_manifest"]["sha256"]
    if value["source_manifest_sha256"] != observed_manifest_hash:
        raise ManifestIntegrityError("run context source manifest hash mismatch")
    identity = value["cli_identity"]
    if not isinstance(identity, dict) or set(identity) != CLI_IDENTITY_FIELDS:
        raise OutputBoundaryError("cli_identity fields mismatch")
    revision = identity["revision"]
    if (
        identity["digest_kind"] not in {"daily", "weekly", "monthly"}
        or isinstance(revision, bool)
        or revision not in {1, 2}
        or (revision == 1 and identity["supersedes_digest_id"] is not None)
        or (revision == 2 and not isinstance(identity["supersedes_digest_id"], str))
    ):
        raise OutputBoundaryError("cli_identity revision/supersedes policy mismatch")
    value["_resolved_input_anchors"] = {key: str(path) for key, path in resolved_anchors.items()}
    return value, successor_root, target


def validate_output_dir(
    output_dir: str | Path,
    *,
    run_context: str | Path,
    source_manifest: str | Path,
) -> Path:
    return _load_run_context(
        run_context, output_dir=output_dir, source_manifest=source_manifest
    )[2]


def _jsonl_bytes(values: list[dict]) -> bytes:
    return b"".join(canonical_json_bytes(value) + b"\n" for value in values)


def _validate_domain_schemas(successor_root: Path, events: list[dict], digest: dict) -> None:
    schema_root = Path(__file__).resolve().parent / "schemas"
    cases = {
        "research_event_adapter.schema.json": events,
        "change_digest.schema.json": [digest],
        "change_digest_issue.schema.json": digest["unparseable_events"],
    }
    for name, values in cases.items():
        schema = _json_value((schema_root / name).read_bytes(), name)
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        for value in values:
            validator.validate(value)


def run(args: argparse.Namespace) -> dict:
    context, successor_root, target = _load_run_context(
        args.run_context,
        output_dir=args.output_dir,
        source_manifest=args.source_manifest,
    )
    identity = context["cli_identity"]
    observed_identity = {
        "digest_kind": args.digest_kind,
        "window_start": args.window_start,
        "window_end": args.window_end,
        "cutoff_at": args.cutoff_at,
        "revision": args.revision,
        "supersedes_digest_id": args.supersedes_digest_id,
    }
    if observed_identity != identity:
        raise OutputBoundaryError("CLI arguments differ from frozen cli_identity")
    anchors = {key: Path(value) for key, value in context["_resolved_input_anchors"].items()}
    manifest_dir = successor_root / "00_manifest"
    common_schema, common_schema_hash = load_common_schema(
        anchors["common_object_schema"]
    )
    evidence_review_authority, evidence_review_hash = load_evidence_review_authority(
        anchors["evidence_review_authority"],
        successor_root=successor_root,
    )
    snapshot = load_source_snapshot(
        anchors["source_manifest"],
        manifest_schema_path=anchors["source_manifest_schema"],
        evidence_review_authority=evidence_review_authority,
    )
    actor_locators, knowledge_admission_refs, actor_hash = load_actor_authority(
        anchors["actor_authority"],
        successor_root=successor_root,
        snapshot=snapshot,
        evidence_review_authority=evidence_review_authority,
    )
    authority_refs, common_ref_hash = load_common_reference_authority(
        anchors["common_reference_authority"],
        successor_root=successor_root,
        actor_locators=actor_locators,
        frozen_knowledge_admission_refs=knowledge_admission_refs,
    )
    prior_digest, prior_common, prior_hash = load_prior_digest_authority(
        anchors["prior_digest_authority"],
        successor_root=successor_root,
        common_schema=common_schema,
        actor_locators=actor_locators,
        authority_refs=authority_refs,
    )
    historical, _, historical_hash = load_historical_authority(
        anchors["historical_cutoff_authority"],
        successor_root=successor_root,
        snapshot=snapshot,
        prior_digest=prior_digest,
    )
    correction = args.revision == 2
    if correction and args.supersedes_digest_id != prior_digest["digest_id"]:
        raise ManifestIntegrityError("CLI supersedes ID differs from frozen prior authority")
    digest = build_digest(
        snapshot,
        digest_kind=args.digest_kind,
        window_start=args.window_start,
        window_end=args.window_end,
        cutoff_at=args.cutoff_at,
        revision=args.revision,
        supersedes_digest_id=args.supersedes_digest_id,
        prior_digest=prior_digest if correction else None,
        historical_authority=historical,
    )
    validate_data_contracts_domain_pack(
        snapshot["events"],
        digest,
        common_schema=common_schema,
        actor_locators=actor_locators,
        authority_refs=authority_refs,
        prior_common_object=prior_common if correction else None,
        historical_authority=historical,
    )
    _validate_domain_schemas(successor_root, snapshot["events"], digest)

    markdown = (render_markdown(digest) + "\n").encode("utf-8")
    normalized = list(snapshot["events"])
    issues = digest["unparseable_events"]
    runtime_log_candidates = [to_runtime_log_error_candidate(issue) for issue in issues]
    payloads = {
        "change_digest.json": pretty_json_bytes(digest),
        "research_change_log.md": markdown,
        "normalized_research_events.jsonl": _jsonl_bytes(normalized),
        "issue_index.jsonl": _jsonl_bytes(issues),
        "runtime_log_error_candidates.jsonl": _jsonl_bytes(runtime_log_candidates),
    }
    result = {
        "schema_version": "1.0",
        "candidate_id": "candidate002",
        "stage": context["stage"],
        "lifecycle_status": "completed",
        "verification_result": "PASS" if digest["report_status"] == "complete" else "FAIL",
        "acceptance_verdict": "NOT_ASSESSED",
        "digest_id": digest["digest_id"],
        "digest_kind": digest["digest_kind"],
        "report_status": digest["report_status"],
        "source_manifest_sha256": snapshot["manifest_sha256"],
        "generator_version": GENERATOR_VERSION,
        "authority_hashes": {
            "common_schema": common_schema_hash,
            "evidence_review": evidence_review_hash,
            "actors": actor_hash,
            "common_refs": common_ref_hash,
            "prior_digest": prior_hash,
            "historical_cutoff": historical_hash,
        },
        "official_validator_receipts": OFFICIAL_VALIDATOR_RECEIPTS,
        "output_hashes": {name: sha256_bytes(data) for name, data in payloads.items()},
        "source_event_count": len(normalized),
        "issue_count": len(issues),
        "external_calls": 0,
        "external_tokens": 0,
        "cost_cny": 0,
        "production_mutations": 0,
        "git_mutations": 0,
    }
    target.mkdir()
    for name, data in payloads.items():
        write_new_bytes(target / name, data)
    write_new_json(target / "run_result.json", result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research_reports-research-changelog")
    parser.add_argument("--run-context", required=True)
    parser.add_argument("--source-manifest", required=True)
    parser.add_argument("--digest-kind", choices=("daily", "weekly", "monthly"), required=True)
    parser.add_argument("--window-start", required=True)
    parser.add_argument("--window-end", required=True)
    parser.add_argument("--cutoff-at", required=True)
    parser.add_argument("--revision", required=True, type=int)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--supersedes-digest-id")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = run(args)
    except (ResearchReportsError, OSError, ValueError, json.JSONDecodeError) as exc:
        parser.exit(2, f"RESEARCH_REPORTS_FAIL_CLOSED: {type(exc).__name__}: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["verification_result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
