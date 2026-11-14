"""Safe, model-free Runtime diagnostic entry points."""

from __future__ import annotations

import argparse
import json

from .types import LogicalRole


def _status() -> dict[str, object]:
    return {
        "capability": "LOCAL_INFERENCE_LOCAL_CAPABILITY_BROKER",
        "roles": [
            {
                "logical_role": role.value,
                "qualification": "NOT_ASSESSED_UPSTREAM",
                "route_eligibility": 0,
                "activation_status": "DISABLED",
            }
            for role in LogicalRole
        ],
        "live_model_execution_ready": False,
        "model_process_started": False,
        "model_request_count": 0,
        "external_request_count": 0,
        "production_mutations": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m model_gateway.local_capability")
    parser.add_argument("command", choices=("local-status", "local-preflight", "local-diagnose"))
    args = parser.parse_args(argv)
    value = _status()
    value["command"] = args.command
    if args.command != "local-status":
        value["result"] = "BLOCKED_CONFIGURATION_REQUIRED"
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
