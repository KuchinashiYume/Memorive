"""Small, local-only, source-bound preparation and citation helper.

    prepare --out SOURCES INPUT...      bundles (original, raw.md, source.json) named S1, S2 ... in SOURCES/index.json
    verify MANIFEST [--current INPUT]   bundle integrity, optionally against the current input
    evidence MANIFEST [--page N] [--lines A:B] [--quote TEXT]
    render MANIFEST --page N --out PNG
    cite --sources SOURCES --draft DRAFT.md --question TEXT [--out DIR]
                                        a draft with [S1 p.3 L120-124: "quote"] marks -> analysis.md + citations.json
"""
import argparse
import contextlib
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import sys
import uuid

import drafts

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
                "source_id": data["source_id"], "alias": name(root, bundle, path), "warnings": data["warnings"]}
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
                "source_id": data["source_id"], "alias": name(root, bundle, path), "warnings": warnings}
    finally:
        # Only this function's verified temporary directory is eligible for cleanup.
        if temporary.exists() and temporary.parent == root and temporary.name.startswith(".prepare-"):
            shutil.rmtree(temporary)


def name(root, bundle, path):
    """The short name of a bundle (S1, S2 ...) in root/index.json; a bundle keeps its name."""
    index_path = root / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.is_file() else {}
    manifest = (bundle / "source.json").relative_to(root).as_posix()
    alias = next((a for a, row in index.items() if row["manifest"] == manifest), None)
    if alias is None:
        alias = "S" + str(len(index) + 1)
        index[alias] = {"manifest": manifest, "input": str(path)}
        write_json(index_path, index)
    return alias


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


COVERAGE = re.compile(r"<!--\s*coverage\s*\n(.*?)-->", re.S)
COVERAGE_LINE = re.compile(r"^\s*(S\d+)\s+(read|partial|unread)\s*(?::\s*(.*))?$")
WHERE = re.compile(r"^(?:p\.?\s*(\d+)\s+)?L(\d+)(?:\s*[-:]\s*(\d+))?$")


def cite(sources, draft, question, out):
    """analysis.md and citations.json from a draft with [S1 p.3 L120-124: "quote"] marks (drafts.py).
    Each span is checked with `evidence` (bundle integrity, page, lines, quote), and the numbers of a
    source statement must be in its spans. The draft ends with a coverage block naming every source:
        <!-- coverage
        S1 read
        S2 partial: the methods section was not read
        -->"""
    sources, out = Path(sources).resolve(), Path(out).resolve()
    index = json.loads((sources / "index.json").read_text(encoding="utf-8"))
    problems, coverage = [], {}
    block = COVERAGE.search(draft)
    for line in (block.group(1).splitlines() if block else []):
        if line.strip():
            match = COVERAGE_LINE.match(line)
            if not match or match.group(1) not in index:
                problems.append(f"coverage line not understood: {line.strip()}")
                continue
            alias, status, gaps = match.groups()
            if status != "read" and not (gaps or "").strip():
                problems.append(f"coverage of {alias}: say what was not read and why it matters")
            coverage[alias] = {"input": Path(index[alias]["input"]).name, "status": status,
                               "gaps": [gaps.strip()] if gaps else []}
    problems += [f"coverage: say whether {alias} was read, partially read or not read"
                 for alias in index if alias not in coverage]
    body = COVERAGE.sub("", draft)

    def resolve(alias, where, quote):
        if alias not in index:
            raise ValueError(f"{alias} is not a prepared source (see {sources / 'index.json'})")
        found = WHERE.match(where)
        if not found:
            raise ValueError(f'{alias}: give the place as "p.3 L120-124" (PDF) or "L8-10" (MD/TXT), not "{where}"')
        page, first, last = found.group(1), int(found.group(2)), int(found.group(3) or found.group(2))
        manifest = sources / index[alias]["manifest"]
        try:
            span = evidence(manifest, int(page) if page else None, f"{first}:{last}", quote)
        except (ValueError, OSError) as error:
            raise ValueError(f'{alias} {where}: {error}: "{quote[:120]}"') from None
        record = {"manifest": os.path.relpath(manifest, out).replace(os.sep, "/"), "source_sha256": span["source_sha256"],
                  "page": span["page"], "line_start": first, "line_end": last, "quote": quote, "checked": True}
        return dict(record, alias=alias), span["text"]

    def render(supports):
        one = len(index) == 1
        places = [("" if one else s["alias"] + ", ") + (f"p.{s['page']} " if s["page"] else "")
                  + (f"L{s['line_start']}" + (f"-{s['line_end']}" if s["line_end"] != s["line_start"] else ""))
                  for s in supports]
        return "[" + "; ".join(places) + "]" if places else ""

    text, claims, more = drafts.apply(body, resolve, render)
    problems += more
    if problems:
        raise ValueError("The draft needs changes:\n- " + "\n- ".join(problems))
    for claim in claims:
        for support in claim["supports"]:
            support.pop("alias")
    claims = [dict(claim, id=f"C{n}") for n, claim in enumerate(claims, 1)]
    out.mkdir(parents=True, exist_ok=True)
    (out / "analysis.md").write_text(text + "\n", encoding="utf-8")
    write_json(out / "citations.json", {"question": question, "coverage": list(coverage.values()), "claims": claims})
    return {"analysis": str(out / "analysis.md"), "citations": str(out / "citations.json"), "claims": len(claims),
            "coverage": {alias: row["status"] for alias, row in coverage.items()}}


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
    p = sub.add_parser("cite")
    p.add_argument("--sources", required=True)
    p.add_argument("--draft", required=True)
    p.add_argument("--question", required=True)
    p.add_argument("--out")
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
        elif args.command == "cite":
            draft = Path(args.draft).read_text(encoding="utf-8-sig")
            # By default the analysis goes to the task folder: the folder that holds the sources.
            emit(cite(args.sources, draft, args.question, args.out or Path(args.sources).resolve().parent))
        else:
            emit(render(args.manifest, args.page, args.out))
        return 0
    except Exception as exc:
        emit({"status": "failed", "error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

