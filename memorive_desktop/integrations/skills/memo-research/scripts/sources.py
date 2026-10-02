"""Small, local-only, source-bound preparation and citation helper."""
import argparse
import contextlib
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

VERSION = "0.1.1"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_hash(path):
    return digest(Path(path).read_bytes())


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def emit(value):
    print(json.dumps(value, ensure_ascii=False))


def bound_file(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Artifact path escapes source bundle")
    return path


def verify(manifest, current=None):
    path = Path(manifest).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != "memo-source/1":
        raise ValueError("Unsupported source manifest")
    root = path.parent
    for key in ("original", "raw"):
        record = data[key]
        actual = file_hash(bound_file(root, record["path"]))
        if actual != record["sha256"]:
            raise ValueError(f"Integrity mismatch: {key}")
    if data["original"]["sha256"] != data["source_sha256"]:
        raise ValueError("Source identity mismatch")
    if current is not None and file_hash(current) != data["source_sha256"]:
        raise ValueError("Current source differs from preserved original")
    return data


def prepare(path, output):
    path = Path(path).resolve(strict=True)
    suffix = path.suffix.lower()
    if suffix not in (".pdf", ".md", ".txt"):
        raise ValueError("Supported inputs: PDF, UTF-8 MD/TXT")
    original = path.read_bytes()
    source_hash = digest(original)
    converter = {"helper": VERSION, "helper_sha256": file_hash(__file__), "format": suffix}
    if suffix == ".pdf":
        converter.update({"engine": "pymupdf4llm", "version": importlib.metadata.version("pymupdf4llm"),
                          "pymupdf": importlib.metadata.version("pymupdf"),
                          "layout": importlib.metadata.version("pymupdf_layout"),
                          "settings": {"page_chunks": True, "use_ocr": False, "write_images": False}})
    else:
        converter.update({"engine": "utf8-text", "version": "1", "settings": {"newlines": "LF"}})
    key = digest(json.dumps(converter, sort_keys=True).encode())
    root = Path(output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    bundle = root / (source_hash[:24] + "-" + key[:16])
    if bundle.exists():
        data = verify(bundle / "source.json", path)
        if data["converter"] != converter:
            raise ValueError("Conversion identity collision")
        return {"input": str(path), "status": "cached", "manifest": str(bundle / "source.json"),
                "source_id": data["source_id"], "warnings": data["warnings"]}
    # Extract from a preserved snapshot, not a file that might change mid-conversion.
    # Inherit the output directory ACL, including the requesting user on Windows.
    # tempfile.mkdtemp uses mode 0700 and would restrict bundles to the sandbox account.
    temporary = root / (".prepare-" + uuid.uuid4().hex)
    temporary.mkdir(mode=0o777)
    try:
        original_path = temporary / ("original" + suffix)
        original_path.write_bytes(original)
        if suffix == ".pdf":
            with contextlib.redirect_stdout(sys.stderr):
                import pymupdf4llm
                chunks = pymupdf4llm.to_markdown(str(original_path), page_chunks=True,
                                                use_ocr=False, write_images=False)
            texts = [c["text"] for c in chunks]
            if not texts:
                raise ValueError("No PDF pages extracted")
        else:
            texts = [original.decode("utf-8-sig")]
        warnings = []
        lines = [f"<!-- source_sha256: {source_hash} -->", ""]
        pages = []
        for idx, text in enumerate(texts, 1):
            page = idx if suffix == ".pdf" else None
            lines.extend([f"## {'PDF page ' + str(idx) if page else 'Source text'}", ""])
            start = len(lines) + 1
            page_lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
            if not page_lines:
                page_lines = [""]
            lines.extend(page_lines)
            pages.append({"page": page, "line_start": start, "line_end": len(lines)})
            lines.append("")
            if len("".join(text.split())) < 40:
                warnings.append(f"sparse_text:{'page_' + str(idx) if page else 'text'}")
        if suffix == ".pdf":
            warnings.append("OCR_NOT_RUN; extraction does not prove table/equation/reading-order fidelity")
        raw = "\n".join(lines) + "\n"
        (temporary / "raw.md").write_text(raw, encoding="utf-8", newline="\n")
        data = {"schema": "memo-source/1", "source_id": "S-" + source_hash[:12],
                "source_sha256": source_hash, "input_path": str(path),
                "original": {"path": original_path.name, "sha256": source_hash},
                "raw": {"path": "raw.md", "sha256": file_hash(temporary / "raw.md")},
                "converter": converter, "pages": pages, "warnings": warnings,
                "origin": "local_preserved_bytes", "memo_evidence_id": None}
        write_json(temporary / "source.json", data)
        # Same-volume rename publishes only a completed bundle; never replaces an existing one.
        temporary.rename(bundle)
        return {"input": str(path), "status": "prepared", "manifest": str(bundle / "source.json"),
                "source_id": data["source_id"], "warnings": warnings}
    finally:
        # Only this function's verified temporary directory is eligible for cleanup.
        if temporary.exists() and temporary.parent == root and temporary.name.startswith(".prepare-"):
            shutil.rmtree(temporary)


def evidence(manifest, page=None, span=None, quote=None):
    data = verify(manifest)
    pages = [p for p in data["pages"] if p["page"] == page]
    if len(pages) != 1:
        raise ValueError("Choose an existing physical PDF --page; omit --page for text inputs")
    bounds = pages[0]
    start, end = bounds["line_start"], bounds["line_end"]
    if span:
        start, end = map(int, span.split(":"))
    if not (bounds["line_start"] <= start <= end <= bounds["line_end"]):
        raise ValueError("Line range is outside the selected source page/chunk")
    raw_path = bound_file(Path(manifest).resolve().parent, data["raw"]["path"])
    lines = raw_path.read_text(encoding="utf-8").splitlines()
    excerpt = "\n".join(lines[start - 1:end])
    if quote is not None and (not quote.strip() or " ".join(quote.split()) not in " ".join(excerpt.split())):
        raise ValueError("Quote not found in selected span (whitespace normalization only)")
    return {"source_id": data["source_id"], "source_sha256": data["source_sha256"],
            "page": page, "line_start": start, "line_end": end,
            "text": excerpt, "integrity_verified": True, "quote_verified": quote is not None,
            "semantic_support": "NOT_ASSESSED"}


def render(manifest, page, out):
    data = verify(manifest)
    if not any(p["page"] == page for p in data["pages"]) or page is None:
        raise ValueError("Rendering requires an existing PDF page")
    import pymupdf
    original = bound_file(Path(manifest).resolve().parent, data["original"]["path"])
    output = Path(out).resolve()
    if output.exists():
        raise ValueError("Render output already exists; choose a new filename")
    if output.suffix.lower() != ".png":
        raise ValueError("Render output must be PNG")
    output.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(original) as doc:
        doc[page - 1].get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5)).save(output)
    return {"output": str(output), "page": page, "source_sha256": data["source_sha256"]}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--out", required=True)
    p.add_argument("inputs", nargs="+")
    p = sub.add_parser("verify")
    p.add_argument("manifest")
    p.add_argument("--current")
    p = sub.add_parser("evidence")
    p.add_argument("manifest")
    p.add_argument("--page", type=int)
    p.add_argument("--lines")
    p.add_argument("--quote")
    p = sub.add_parser("render")
    p.add_argument("manifest")
    p.add_argument("--page", required=True, type=int)
    p.add_argument("--out", required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        results = []
        for path in args.inputs:
            try:
                results.append(prepare(path, args.out))
            except Exception as exc:
                results.append({"input": path, "status": "failed", "error": str(exc)})
        emit({"results": results})
        return 2 if any(r["status"] == "failed" for r in results) else 0
    try:
        if args.command == "verify":
            data = verify(args.manifest, args.current)
            emit({"source_id": data["source_id"], "integrity_verified": True,
                  "current_source_checked": args.current is not None})
        elif args.command == "evidence":
            emit(evidence(args.manifest, args.page, args.lines, args.quote))
        else:
            emit(render(args.manifest, args.page, args.out))
        return 0
    except Exception as exc:
        emit({"status": "failed", "error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

