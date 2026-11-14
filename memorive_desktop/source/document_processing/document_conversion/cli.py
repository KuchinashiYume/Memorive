"""Thin local CLI for the Intake facade."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .facade import DocumentConversionFacade
from .registry import probe_document


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memorive-document-conversion")
    sub = parser.add_subparsers(dest="command", required=True)
    probe = sub.add_parser("probe")
    probe.add_argument("source")
    verify = sub.add_parser("verify-structure")
    verify.add_argument("receipt")
    convert = sub.add_parser("convert")
    convert.add_argument("source")
    convert.add_argument("--paper-id", required=True)
    convert.add_argument("--title", required=True)
    convert.add_argument("--allowed-root", required=True)
    convert.add_argument("--target-root", required=True)
    convert.add_argument("--data-ownership", choices=("self", "entrusted"), required=True)
    convert.add_argument("--structure-mode", choices=("none", "native_pdf", "marker_chunks"), default="none")
    convert.add_argument("--marker-chunks", help="Source-hash-bound Marker 2.0.0 candidate envelope")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "verify-structure":
        from ..document_structure import verify_bundle
        print(json.dumps(verify_bundle(args.receipt), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "probe":
        print(json.dumps(probe_document(args.source).to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    result = DocumentConversionFacade(allowed_root=Path(args.allowed_root)).convert_document(
        args.source,
        paper_id=args.paper_id,
        title=args.title,
        target_root=Path(args.target_root),
        data_ownership=args.data_ownership,
        structure_mode=args.structure_mode,
        marker_chunks_path=args.marker_chunks,
    )
    print(json.dumps(result.receipt.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    from .models import DocumentConversionError
    try:
        raise SystemExit(main())
    except DocumentConversionError as exc:
        print(json.dumps({"status": "FAILED", "error_code": exc.code, "message": str(exc),
                          "details": exc.details}, ensure_ascii=False))
        raise SystemExit(2)
