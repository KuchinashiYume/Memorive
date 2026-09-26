"""Format-family adapters behind the single T05 facade."""
from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import io
import re
import shutil
import struct
import sys
import zipfile
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

from .diagnostics import analyze_text, make_source_marker
from .models import (
    AdapterPayload,
    DocumentConversionError,
    DocumentProbe,
    FormatVerdict,
    ImageFrameManifest,
    RecognitionStatus,
    SelectionStatus,
    stable_identifier,
)
from .office_legacy_bridge import convert_legacy_to_ooxml
from .registry import probe_document


def _version(distribution: str, default: str = "stdlib") -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return default


def _finalize_text(
    text: str,
    *,
    anchor_kind: str,
    anchor_map: list[dict],
    engine: str,
    engine_version: str,
    warnings: list[str] | None = None,
    degradation: list[str] | None = None,
) -> AdapterPayload:
    diagnostics = analyze_text(text)
    if diagnostics.hard_failures:
        raise DocumentConversionError(
            "TEXT_DIAGNOSTIC_HARD_FAILURE",
            "converted text failed deterministic integrity diagnostics",
            details={
                "hard_failures": diagnostics.hard_failures,
                "replacement_ratio": diagnostics.replacement_ratio,
                "control_ratio": diagnostics.control_ratio,
            },
        )
    signals = [*(degradation or []), *diagnostics.degradation_signals]
    verdict = FormatVerdict.DEGRADED.value if signals else FormatVerdict.SUPPORTED.value
    return AdapterPayload(
        text=text,
        anchor_kind=anchor_kind,
        anchor_map=tuple(anchor_map),
        engine=engine,
        engine_version=engine_version,
        format_verdict=verdict,
        warnings=tuple(warnings or []),
        degradation_signals=tuple(signals),
    )


def _line_adapter(path: Path, *, format_key: str) -> AdapterPayload:
    text = path.read_text(encoding="utf-8-sig")
    output: list[str] = []
    anchors: list[dict] = []
    for ordinal, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            output.append("")
            continue
        output.append(make_source_marker("line", ordinal))
        output.append(line)
        anchors.append({"kind": "line", "ordinal": ordinal})
    return _finalize_text(
        "\n".join(output).rstrip() + "\n",
        anchor_kind="line",
        anchor_map=anchors,
        engine="local_markdown_v1" if format_key == "md" else "local_text_v1",
        engine_version=sys.version.split()[0],
    )


def _csv_adapter(path: Path) -> AdapterPayload:
    rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8-sig"))))
    if not rows or max((len(row) for row in rows), default=0) < 1:
        raise DocumentConversionError("CSV_EMPTY", "CSV contains no rows")
    width = max(len(row) for row in rows)
    output: list[str] = []
    anchors: list[dict] = []
    for ordinal, row in enumerate(rows, start=1):
        values = [value.replace("|", "\\|") for value in [*row, *([""] * (width - len(row)))]]
        output.append(make_source_marker("row", ordinal))
        output.append("| " + " | ".join(values) + " |")
        if ordinal == 1:
            output.append("| " + " | ".join("---" for _ in range(width)) + " |")
        anchors.append({"kind": "row", "ordinal": ordinal})
    return _finalize_text(
        "\n".join(output) + "\n",
        anchor_kind="row",
        anchor_map=anchors,
        engine="stdlib_csv_v1",
        engine_version=sys.version.split()[0],
    )


class _LocalHTMLParser(HTMLParser):
    _BLOCKS = {"p", "div", "section", "article", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr"}
    _IGNORED = {"script", "style", "noscript"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._current: list[str] = []
        self._ignored_depth = 0
        self.remote_refs = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self._IGNORED:
            self._ignored_depth += 1
        for name, value in attrs:
            if name.lower() in {"src", "href"} and value and re.match(r"^(?:https?:)?//", value, re.I):
                self.remote_refs += 1
        if tag in self._BLOCKS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._IGNORED and self._ignored_depth:
            self._ignored_depth -= 1
        if tag in self._BLOCKS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth and data.strip():
            self._current.append(data.strip())

    def _flush(self) -> None:
        value = " ".join(self._current).strip()
        if value:
            self.blocks.append(value)
        self._current = []

    def finish(self) -> None:
        self._flush()


def _html_adapter(path: Path) -> AdapterPayload:
    parser = _LocalHTMLParser()
    parser.feed(path.read_text(encoding="utf-8-sig"))
    parser.finish()
    output: list[str] = []
    anchors: list[dict] = []
    for ordinal, block in enumerate(parser.blocks, start=1):
        output.extend((make_source_marker("block", ordinal), block, ""))
        anchors.append({"kind": "block", "ordinal": ordinal})
    warnings = [f"REMOTE_REFERENCES_SUPPRESSED:{parser.remote_refs}"] if parser.remote_refs else []
    return _finalize_text(
        "\n".join(output).rstrip() + "\n",
        anchor_kind="block",
        anchor_map=anchors,
        engine="local_html_v1",
        engine_version=sys.version.split()[0],
        warnings=warnings,
        degradation=warnings,
    )


def _xml_texts(element: ET.Element, local_name: str) -> list[str]:
    return [node.text or "" for node in element.iter() if node.tag.rsplit("}", 1)[-1] == local_name and (node.text or "").strip()]


def _docx_adapter(path: Path) -> AdapterPayload:
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    blocks: list[str] = []
    for paragraph in root.iter():
        if paragraph.tag.rsplit("}", 1)[-1] != "p":
            continue
        value = "".join(_xml_texts(paragraph, "t")).strip()
        if value:
            blocks.append(value)
    output: list[str] = []
    anchors: list[dict] = []
    for ordinal, block in enumerate(blocks, start=1):
        output.extend((make_source_marker("block", ordinal), block, ""))
        anchors.append({"kind": "block", "ordinal": ordinal})
    return _finalize_text("\n".join(output), anchor_kind="block", anchor_map=anchors, engine="ooxml_docx_v1", engine_version=sys.version.split()[0])


def _slide_number(name: str) -> int:
    match = re.search(r"slide(\d+)\.xml$", name)
    return int(match.group(1)) if match else 0


def _pptx_adapter(path: Path) -> AdapterPayload:
    output: list[str] = []
    anchors: list[dict] = []
    with zipfile.ZipFile(path) as archive:
        slides = sorted((name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)), key=_slide_number)
        for ordinal, name in enumerate(slides, start=1):
            root = ET.fromstring(archive.read(name))
            text = "\n".join(_xml_texts(root, "t")).strip()
            output.extend((make_source_marker("slide", ordinal), text or "[empty slide]", ""))
            anchors.append({"kind": "slide", "ordinal": ordinal})
    return _finalize_text("\n".join(output), anchor_kind="slide", anchor_map=anchors, engine="ooxml_pptx_v1", engine_version=sys.version.split()[0])


def _xlsx_adapter(path: Path) -> AdapterPayload:
    output: list[str] = []
    anchors: list[dict] = []
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(_xml_texts(node, "t")) for node in root if node.tag.rsplit("}", 1)[-1] == "si"]
        sheets = sorted(name for name in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name))
        for ordinal, name in enumerate(sheets, start=1):
            root = ET.fromstring(archive.read(name))
            values: list[str] = []
            for cell in (node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "c"):
                cell_type = cell.attrib.get("t")
                value_nodes = [node for node in cell if node.tag.rsplit("}", 1)[-1] in {"v", "is"}]
                raw = "".join(_xml_texts(value_nodes[0], "t")) if value_nodes and value_nodes[0].tag.rsplit("}", 1)[-1] == "is" else (value_nodes[0].text or "" if value_nodes else "")
                if cell_type == "s" and raw.isdigit() and int(raw) < len(shared):
                    raw = shared[int(raw)]
                if raw:
                    values.append(f"{cell.attrib.get('r', '?')}={raw}")
            output.extend((make_source_marker("sheet", ordinal, label=f"sheet{ordinal}"), " | ".join(values) or "[empty sheet]", ""))
            anchors.append({"kind": "sheet", "ordinal": ordinal, "label": f"sheet{ordinal}"})
    return _finalize_text("\n".join(output), anchor_kind="sheet", anchor_map=anchors, engine="ooxml_xlsx_v1", engine_version=sys.version.split()[0])


def _pdf_adapter(probe: DocumentProbe) -> AdapterPayload:
    from ..engines.pymupdf_engine import PymupdfLightEngine
    from ..types import IngestMeta, RawObject

    raw = RawObject(
        path=probe.path,
        meta=IngestMeta(
            source=str(probe.path),
            imported_at="1970-01-01T00:00:00+00:00",
            data_ownership="self",
            doc_type="literature",
            original_format="pdf",
        ),
    )
    text, report = PymupdfLightEngine().convert(raw)
    diagnostics = analyze_text(text)
    if diagnostics.hard_failures == ("EMPTY_TEXT_OUTPUT",):
        # pymupdf4llm can classify a short single-line synthetic page wholly
        # as header/footer. One frozen, local fallback reads native page text;
        # it retains physical page anchors and never invokes OCR.
        import pymupdf

        parts: list[str] = []
        with pymupdf.open(probe.path) as document:
            for ordinal, page in enumerate(document, start=1):
                parts.extend((
                    f"<!-- pros:page:{ordinal} -->",
                    page.get_text("text", sort=True).rstrip(),
                ))
        fallback_text = "\n\n".join(parts).strip() + "\n"
        anchors = [
            {"kind": "page", "ordinal": int(value)}
            for value in re.findall(r"<!--\s*pros:page:(\d+)\s*-->", fallback_text)
        ]
        payload = _finalize_text(
            fallback_text,
            anchor_kind="page",
            anchor_map=anchors,
            engine="pymupdf_native_text_v1",
            engine_version=_version("PyMuPDF"),
            warnings=[
                *report.warnings,
                "PRIMARY_PYMUPDF4LLM_EMPTY;ONE_LOCAL_NATIVE_TEXT_FALLBACK_SELECTED",
            ],
        )
        return replace(
            payload,
            selection_status=SelectionStatus.FALLBACK_SELECTED.value,
            primary_engine="pymupdf4llm_native_v1",
            fallback_engine="pymupdf_native_text_v1",
            selection_reason="PRIMARY_EMPTY_TEXT_OUTPUT;ONE_QUALIFIED_LOCAL_FALLBACK",
        )
    anchors = [
        {"kind": "page", "ordinal": int(value)}
        for value in re.findall(r"<!--\s*pros:page:(\d+)\s*-->", text)
    ]
    return _finalize_text(
        text,
        anchor_kind="page",
        anchor_map=anchors,
        engine="pymupdf4llm_native_v1",
        engine_version=_version("pymupdf4llm"),
        warnings=list(report.warnings),
    )


def _jpeg_frames(data: bytes) -> list[dict]:
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        index += 2
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if index + 2 > len(data):
            break
        length = int.from_bytes(data[index:index + 2], "big")
        if length < 2 or index + length > len(data):
            break
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF} and length >= 7:
            height = int.from_bytes(data[index + 3:index + 5], "big")
            width = int.from_bytes(data[index + 5:index + 7], "big")
            return [{"ordinal": 1, "width": width, "height": height}]
        index += length
    raise DocumentConversionError("JPEG_DIMENSIONS_MISSING", "JPEG has no supported SOF dimensions")


def _png_frames(data: bytes) -> list[dict]:
    if len(data) < 24 or data[12:16] != b"IHDR":
        raise DocumentConversionError("PNG_IHDR_MISSING", "PNG has no IHDR")
    width, height = struct.unpack(">II", data[16:24])
    if width < 1 or height < 1:
        raise DocumentConversionError("IMAGE_DIMENSIONS_INVALID", "PNG dimensions are invalid")
    return [{"ordinal": 1, "width": width, "height": height}]


def _tiff_frames(data: bytes, *, max_frames: int = 256) -> list[dict]:
    if data[:2] == b"II":
        endian = "<"
    elif data[:2] == b"MM":
        endian = ">"
    else:
        raise DocumentConversionError("TIFF_ENDIAN_INVALID", "TIFF byte order invalid")
    if struct.unpack(endian + "H", data[2:4])[0] != 42:
        raise DocumentConversionError("TIFF_MAGIC_INVALID", "TIFF magic invalid")
    offset = struct.unpack(endian + "I", data[4:8])[0]
    frames: list[dict] = []
    visited: set[int] = set()
    while offset:
        if offset in visited or len(frames) >= max_frames or offset + 2 > len(data):
            raise DocumentConversionError("TIFF_IFD_CHAIN_INVALID", "TIFF IFD chain invalid or over cap")
        visited.add(offset)
        count = struct.unpack(endian + "H", data[offset:offset + 2])[0]
        cursor = offset + 2
        width = height = None
        for _ in range(count):
            if cursor + 12 > len(data):
                raise DocumentConversionError("TIFF_IFD_TRUNCATED", "TIFF IFD entry truncated")
            tag, typ, item_count = struct.unpack(endian + "HHI", data[cursor:cursor + 8])
            raw = data[cursor + 8:cursor + 12]
            value = struct.unpack(endian + ("H" if typ == 3 and item_count == 1 else "I"), raw[:2] if typ == 3 and item_count == 1 else raw)[0]
            if tag == 256:
                width = value
            elif tag == 257:
                height = value
            cursor += 12
        if cursor + 4 > len(data):
            raise DocumentConversionError("TIFF_IFD_TRUNCATED", "TIFF next IFD offset missing")
        offset = struct.unpack(endian + "I", data[cursor:cursor + 4])[0]
        if not width or not height:
            raise DocumentConversionError("TIFF_DIMENSIONS_MISSING", "TIFF frame dimensions missing")
        frames.append({"ordinal": len(frames) + 1, "width": width, "height": height})
    if not frames:
        raise DocumentConversionError("TIFF_FRAME_MISSING", "TIFF contains no frame")
    return frames


def _image_adapter(probe: DocumentProbe, workdir: Path) -> AdapterPayload:
    data = probe.path.read_bytes()
    if probe.detected_format == "jpeg":
        frames = _jpeg_frames(data)
    elif probe.detected_format == "png":
        frames = _png_frames(data)
    else:
        frames = _tiff_frames(data)
    total_pixels = sum(frame["width"] * frame["height"] for frame in frames)
    if any(frame["width"] * frame["height"] > 40_000_000 for frame in frames) or total_pixels * 4 > 256 * 1024 * 1024:
        raise DocumentConversionError("RESOURCE_LIMIT_REVIEW_REQUIRED", "image decode projection exceeds frozen caps")
    derivative = workdir / f"recognition_derivative{probe.path.suffix.lower()}"
    shutil.copy2(probe.path, derivative)
    derivative_hash = hashlib.sha256(derivative.read_bytes()).hexdigest().upper()
    manifest = ImageFrameManifest(
        manifest_id=stable_identifier("T05I-", {"source": probe.source_sha256, "frames": frames}),
        format_key=probe.detected_format or "image",
        frame_count=len(frames),
        frames=tuple({**frame, "anchor": make_source_marker("frame", frame["ordinal"])} for frame in frames),
        source_sha256=probe.source_sha256,
        derivative_sha256=derivative_hash,
        transform="BYTE_IDENTICAL_HANDOFF_NO_ORIENTATION_OR_METADATA_NORMALIZATION",
    )
    output: list[str] = []
    anchors: list[dict] = []
    for frame in frames:
        output.extend((make_source_marker("frame", frame["ordinal"]), f"[image frame {frame['ordinal']}: {frame['width']}x{frame['height']}]", ""))
        anchors.append({"kind": "frame", "ordinal": frame["ordinal"]})
    return AdapterPayload(
        text="\n".join(output),
        anchor_kind="frame",
        anchor_map=tuple(anchors),
        engine="stdlib_image_probe_v1",
        engine_version=sys.version.split()[0],
        format_verdict=FormatVerdict.DEGRADED.value,
        warnings=("NO_OCR_OR_MODEL_CALL", "DERIVATIVE_IS_BYTE_IDENTICAL_HANDOFF"),
        degradation_signals=("ORIENTATION_AND_METADATA_NORMALIZATION_NOT_QUALIFIED",),
        recognition_status=RecognitionStatus.NOT_RUN_REQUIRES_T07.value,
        image_frame_manifest=manifest,
    )


def adapt_document(probe: DocumentProbe, *, workdir: Path) -> AdapterPayload:
    probe.require_match()
    key = probe.detected_format
    if key == "pdf":
        return _pdf_adapter(probe)
    if key in {"md", "txt"}:
        return _line_adapter(probe.path, format_key=key)
    if key == "csv":
        return _csv_adapter(probe.path)
    if key == "html":
        return _html_adapter(probe.path)
    if key == "docx":
        return _docx_adapter(probe.path)
    if key == "pptx":
        return _pptx_adapter(probe.path)
    if key == "xlsx":
        return _xlsx_adapter(probe.path)
    if key in {"doc", "ppt", "xls"}:
        bridged, bridge_receipt = convert_legacy_to_ooxml(probe.path, format_key=key, output_dir=workdir / "legacy_bridge")
        modern_probe = probe_document(bridged).require_match()
        payload = adapt_document(modern_probe, workdir=workdir)
        return AdapterPayload(
            text=payload.text,
            anchor_kind=payload.anchor_kind,
            anchor_map=payload.anchor_map,
            engine=f"office_legacy_local_v1->{payload.engine}",
            engine_version=payload.engine_version,
            format_verdict=payload.format_verdict,
            warnings=(*payload.warnings, "OFFICE_MACROS_AND_LINK_UPDATES_DISABLED"),
            degradation_signals=payload.degradation_signals,
            hard_failures=payload.hard_failures,
            recognition_status=payload.recognition_status,
            legacy_bridge_receipt=bridge_receipt,
            image_frame_manifest=payload.image_frame_manifest,
            nondeterminism_class="OFFICE_CONTAINER_BYTES_NONDETERMINISTIC_SEMANTIC_OUTPUT_STABLE",
        )
    if key in {"jpeg", "png", "tiff"}:
        return _image_adapter(probe, workdir)
    raise DocumentConversionError("ADAPTER_NOT_REGISTERED", f"no adapter for {key}")
