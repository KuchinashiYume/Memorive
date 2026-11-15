"""Read-only utility CLI for Installation qualification artifacts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from model_gateway.local_reranker.doctor import reranker_doctor

from .candidate_trace import explain_result


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="retrieval_calibration")
    commands = parser.add_subparsers(dest="command", required=True)
    compare = commands.add_parser("compare-profiles")
    compare.add_argument("--qualification", type=Path, required=True)
    compare.add_argument("--query-id", required=True)
    explain = commands.add_parser("explain-result")
    explain.add_argument("--qualification", type=Path, required=True)
    explain.add_argument("--query-id", required=True)
    explain.add_argument("--candidate-id", required=True)
    doctor = commands.add_parser("reranker-doctor")
    doctor.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "reranker-doctor":
        output = reranker_doctor(_read(args.manifest))
    else:
        result = _read(args.qualification)
        if args.command == "compare-profiles":
            output = result["profile_details"][args.query_id]
        else:
            traces = [
                trace
                for trace in result["candidate_traces"]
                if trace.get("query_id") == args.query_id
            ]
            output = explain_result(traces, args.candidate_id)
    print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
