"""Offline-only fake process fixture for EXECUTION-CORE.

This script performs no discovery, authentication, network, Git, or external
model action.  Each mode is deterministic and uses only its isolated cwd and
explicit arguments/environment.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time


def block_network(*args, **kwargs):
    raise RuntimeError("EXECUTION-CORE fake process network policy is disabled")


socket.socket.connect = block_network
socket.socket.connect_ex = block_network
socket.create_connection = block_network


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="strict")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")


def emit(payload: object) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True)
    parser.add_argument("--value", default="")
    parser.add_argument("--marker")
    args = parser.parse_args()

    if args.mode == "valid":
        emit({"status": "ok", "message": args.value})
        sys.stderr.buffer.write(b"fake diagnostic\n")
        sys.stderr.buffer.flush()
        return 0
    if args.mode == "cwd":
        emit({"status": "ok", "message": str(Path.cwd())})
        return 0
    if args.mode == "env":
        emit(
            {
                "status": "ok",
                "message": os.environ.get("MEMORIVE_FAKE_PROCESS_SENTINEL", ""),
                "environment_names": sorted(os.environ),
            }
        )
        return 0
    if args.mode == "split":
        sys.stdout.write('{"status":')
        sys.stdout.flush()
        sys.stderr.write("part-one|")
        sys.stderr.flush()
        sys.stdout.write('"ok","message":"split"}')
        sys.stdout.flush()
        sys.stderr.write("part-two")
        sys.stderr.flush()
        return 0
    if args.mode == "invalid-json":
        sys.stdout.write("not-json")
        return 0
    if args.mode == "schema-invalid":
        emit({"unexpected": True})
        return 0
    if args.mode == "nonzero-partial":
        emit({"status": "partial", "message": "preserved"})
        sys.stderr.write("fake nonzero failure")
        return 7
    if args.mode == "timeout":
        time.sleep(60)
        return 0
    if args.mode == "stdin-block":
        time.sleep(60)
        return 0
    if args.mode == "raw-cap":
        sys.stdout.buffer.write(b"x" * 262144)
        sys.stdout.buffer.flush()
        time.sleep(60)
        return 0
    if args.mode == "spawn-child":
        if not args.marker:
            return 9
        subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--mode",
                "child-write",
                "--marker",
                args.marker,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
        time.sleep(60)
        return 0
    if args.mode == "child-write":
        if not args.marker:
            return 9
        time.sleep(0.75)
        Path(args.marker).write_text("orphan", encoding="utf-8")
        time.sleep(60)
        return 0
    return 8


if __name__ == "__main__":
    raise SystemExit(main())
