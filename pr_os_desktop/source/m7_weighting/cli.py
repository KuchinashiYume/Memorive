"""Explicit-path CLI for the isolated T03 manifest index.

The CLI accepts no environment-derived or default path.  Every invocation
must provide ``--input``, ``--output``, ``--root`` and ``--cutoff``.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path, PurePath
from typing import Any, Mapping, Sequence

from .canonical import canonical_json_bytes
from .candidate_index import build_candidate_index
from .contracts import require_utc_timestamp
from .errors import ContractViolation, FieldVectorError
from .locator import (
    PathBoundaryError,
    ensure_owned_path,
    load_json_file,
    validate_locator,
    validate_run_root,
)
from .migration import FAIL_POINTS, MigrationFailure, migrate_candidate


class CLIContractError(ContractViolation):
    """The explicit CLI request does not match its frozen operation schema."""


_REQUEST_KEYS = {
    "build": {
        "operation",
        "records",
        "profile",
        "index_identity",
        "candidate_relative_path",
        "source_snapshot",
        "active_index_baseline",
    },
    "migrate": {
        "operation",
        "records",
        "profile",
        "index_identity",
        "candidate_relative_path",
        "source_snapshot",
        "active_index_baseline",
        "previous_locator",
        "fail_at",
    },
    "validate": {"operation", "locator"},
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m m7_weighting",
        description="P03/T03 non-production canonical manifest index",
        allow_abbrev=False,
    )
    parser.add_argument("--input", required=True, help="explicit in-run request JSON")
    parser.add_argument("--output", required=True, help="explicit in-run receipt JSON")
    parser.add_argument("--root", required=True, help="explicit governed T03 run root")
    parser.add_argument("--cutoff", required=True, help="explicit UTC build cutoff")
    return parser


def _exact_request(request: Mapping[str, Any]) -> str:
    operation = request.get("operation")
    if operation not in _REQUEST_KEYS:
        raise CLIContractError("request.operation must be build, migrate, or validate")
    expected = _REQUEST_KEYS[str(operation)]
    actual = set(request)
    if actual != expected:
        raise CLIContractError(
            "request keys do not match the operation exact set",
            context={
                "operation": operation,
                "missing": sorted(expected - actual),
                "unexpected": sorted(actual - expected),
            },
        )
    return str(operation)


def _candidate_path(relative_value: Any, run_root: Path) -> Path:
    if not isinstance(relative_value, str) or not relative_value:
        raise CLIContractError("candidate_relative_path must be non-empty")
    relative = PurePath(relative_value.replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
        raise PathBoundaryError("candidate_relative_path must not be absolute or traverse")
    if len(relative.parts) < 2 or relative.parts[0] != "10_candidate":
        raise PathBoundaryError("candidate must be below the run-owned 10_candidate root")
    if relative.parts[1] == "m7_weighting":
        raise PathBoundaryError("candidate output may not overlap the source package")
    destination = run_root.joinpath(*relative.parts)
    destination = ensure_owned_path(destination, run_root, must_exist=False)
    parent = ensure_owned_path(destination.parent, run_root, must_exist=True, allow_root=True)
    if not parent.is_dir():
        raise PathBoundaryError("candidate output parent must already exist")
    return destination


def _write_receipt(path: Path, receipt: Mapping[str, Any]) -> None:
    data = canonical_json_bytes(dict(receipt)) + b"\n"
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _require_records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise CLIContractError("request.records must be an array")
    result: list[dict[str, Any]] = []
    for ordinal, record in enumerate(value):
        if not isinstance(record, dict):
            raise CLIContractError(f"request.records[{ordinal}] must be an object")
        result.append(record)
    return result


def _require_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CLIContractError(f"{field} must be an object")
    return value


def _assert_cutoff(expected: str, observed: Any, field: str) -> None:
    if observed != expected:
        raise CLIContractError(
            f"{field} differs from explicit --cutoff",
            context={"expected": expected, "observed": observed},
        )


def execute_request(
    request: Mapping[str, Any],
    *,
    run_root: Path,
    cutoff: str,
) -> dict[str, Any]:
    """Execute a validated ordinary-dict request inside one owned run root."""

    operation = _exact_request(request)
    if operation == "validate":
        locator = _require_mapping(request["locator"], "request.locator")
        _assert_cutoff(cutoff, locator.get("build_cutoff_at"), "locator.build_cutoff_at")
        checked = validate_locator(locator, run_root)
        return {
            "receipt_schema_version": "1.0",
            "operation": "validate",
            "lifecycle_status": "completed",
            "verification_result": "PASS",
            "acceptance_verdict": "NOT_ASSESSED",
            "locator": checked,
            "production_eligible": False,
            "activation": False,
        }

    records = _require_records(request["records"])
    profile = _require_mapping(request["profile"], "request.profile")
    identity = _require_mapping(request["index_identity"], "request.index_identity")
    source_snapshot = _require_mapping(request["source_snapshot"], "request.source_snapshot")
    active_index_baseline = _require_mapping(
        request["active_index_baseline"], "request.active_index_baseline"
    )
    _assert_cutoff(cutoff, identity.get("build_cutoff_at"), "index_identity.build_cutoff_at")
    destination = _candidate_path(request["candidate_relative_path"], run_root)

    if operation == "build":
        locator = build_candidate_index(
            records,
            profile,
            identity,
            destination,
            owned_root=run_root,
            source_snapshot=source_snapshot,
            active_index_baseline=active_index_baseline,
        )
        return {
            "receipt_schema_version": "1.0",
            "operation": "build",
            "lifecycle_status": "completed",
            "verification_result": "PASS",
            "acceptance_verdict": "NOT_ASSESSED",
            "locator": locator,
            "production_eligible": False,
            "activation": False,
        }

    previous = request["previous_locator"]
    if previous is not None and not isinstance(previous, dict):
        raise CLIContractError("previous_locator must be null or an object")
    fail_at = request["fail_at"]
    if fail_at is not None and fail_at not in FAIL_POINTS:
        raise CLIContractError("fail_at is not a frozen injection point")
    receipt = migrate_candidate(
        records,
        profile,
        identity,
        destination,
        owned_root=run_root,
        source_snapshot=source_snapshot,
        active_index_baseline=active_index_baseline,
        previous_locator=previous,
        fail_at=fail_at,
    )
    receipt["acceptance_verdict"] = "NOT_ASSESSED"
    return receipt


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    run_root = validate_run_root(args.root)
    cutoff = require_utc_timestamp(args.cutoff, "--cutoff")
    input_path = ensure_owned_path(args.input, run_root, must_exist=True)
    output_path = ensure_owned_path(args.output, run_root, must_exist=False)
    if input_path == output_path:
        raise PathBoundaryError("--input and --output must be different paths")
    output_parent = ensure_owned_path(output_path.parent, run_root, must_exist=True, allow_root=True)
    if not output_parent.is_dir():
        raise PathBoundaryError("--output parent must already exist")

    request = load_json_file(input_path)
    try:
        receipt = execute_request(request, run_root=run_root, cutoff=cutoff)
        _write_receipt(output_path, receipt)
        return 0
    except MigrationFailure as exc:
        receipt = {
            **exc.receipt,
            "acceptance_verdict": "NOT_ASSESSED",
            "production_eligible": False,
            "activation": False,
        }
        _write_receipt(output_path, receipt)
        return 2
    except FieldVectorError as exc:
        receipt = {
            "receipt_schema_version": "1.0",
            "operation": request.get("operation"),
            "lifecycle_status": "completed",
            "verification_result": "ERROR",
            "acceptance_verdict": "NOT_ASSESSED",
            "issue": exc.to_issue(),
            "production_eligible": False,
            "activation": False,
        }
        _write_receipt(output_path, receipt)
        return 2


__all__ = ["CLIContractError", "execute_request", "main"]
