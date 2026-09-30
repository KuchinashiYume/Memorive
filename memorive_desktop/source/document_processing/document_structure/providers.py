"""Native PDF coordinates and externally supplied Marker 2 chunk candidates."""
from __future__ import annotations

from importlib.metadata import version
from pathlib import Path
import re

from .contract import (ENVELOPE, MAX_BLOCKS, MAX_HTML_CHARS, MAX_PAGES, bbox,
                       canonical, digest, fail, hash_value, polygon, read_json, rectangle)
from .html_data import plain_text, tables


def _pdf(path: Path):
    import pymupdf

    if path.stat().st_size > 512 * 1024 * 1024:
        fail("STRUCTURE_INPUT_TOO_LARGE", "PDF exceeds the E10-A 512 MiB input limit")
    document = pymupdf.open(path)
    if not document.is_pdf or document.needs_pass:
        document.close()
        fail("STRUCTURE_PDF_UNAVAILABLE", "Structure requires an unencrypted PDF")
    if not 1 <= len(document) <= MAX_PAGES:
        document.close()
        fail("STRUCTURE_PAGES_INVALID", "PDF page count is outside 1..2000")
    return document


def native_pdf(source: Path) -> dict:
    blocks, pages, empty = [], [], []
    with _pdf(source) as document:
        for index, page in enumerate(document):
            # get_text coordinates are unrotated. page.rect is rotated.
            bounds = bbox(list(page.rect * page.derotation_matrix))
            pages.append({"page_index": index, "physical_page": index + 1,
                          "bbox": bounds, "rotation": page.rotation,
                          "coordinate_system": "pymupdf_unrotated_page_points"})
            count = 0
            for item in page.get_text("blocks", sort=True):
                if item[6] != 0 or not item[4].strip():
                    continue
                box = bbox(list(item[:4]), enclosing=bounds)
                blocks.append({"upstream_id": f"/page/{index}/NativeText/{item[5]}",
                               "page_index": index, "block_type": "Text",
                               "bbox": box, "polygon": rectangle(box),
                               "text": item[4], "html": None, "tables": [],
                               "section_hierarchy": {}, "image_count": 0})
                count += 1
            if not count:
                empty.append(index + 1)
    if len(blocks) > MAX_BLOCKS:
        fail("STRUCTURE_BLOCKS_INVALID", "Too many native blocks")
    return {"engine": "pymupdf_native_structure", "engine_version": version("PyMuPDF"),
            "config": {"sort": True, "ocr": False, "role": "auxiliary_source_index"},
            "provenance": "LOCAL_NATIVE_EXTRACTION",
            "pages": pages, "blocks": blocks,
            "capabilities": {"source_coordinates": True, "structured_tables": False,
                             "ocr": False, "image_derivatives": False},
            "warnings": ["NATIVE_TEXT_IS_AUXILIARY_RAWMD_REMAINS_AUTHORITATIVE",
                         "TABLE_AND_FORMULA_STRUCTURE_NOT_DETECTED", "OCR_NOT_RUN"],
            "empty_native_text_pages": empty}


def marker_chunks(source: Path, source_sha: str, candidate: Path, *, allow_page_fallback: bool = False) -> tuple[dict, bytes]:
    if type(allow_page_fallback) is not bool:
        fail("STRUCTURE_CONFIG_UNSUPPORTED", "Page fallback must be explicit boolean")
    envelope, raw = read_json(candidate)
    if envelope.get("schema_version") != ENVELOPE:
        fail("STRUCTURE_ENVELOPE_REQUIRED", "Use a source-hash-bound Marker chunks envelope")
    if hash_value(envelope.get("source_sha256")) != source_sha:
        fail("STRUCTURE_SOURCE_MISMATCH", "Marker candidate belongs to a different source")
    if envelope.get("engine") != "marker" or envelope.get("engine_version") != "2.0.0":
        fail("STRUCTURE_ENGINE_UNSUPPORTED", "This importer accepts Marker 2.0.0 only")
    config = envelope.get("config")
    if (not isinstance(config, dict) or set(config) != {"mode", "disable_ocr", "use_llm"}
            or config.get("mode") not in {"fast", "balanced"}
            or type(config.get("disable_ocr")) is not bool or config.get("use_llm") is not False):
        fail("STRUCTURE_CONFIG_UNSUPPORTED", "Declare mode, disable_ocr and use_llm=false")
    chunks = envelope.get("chunks")
    if not isinstance(chunks, dict):
        fail("STRUCTURE_CHUNKS_INVALID", "Envelope has no chunks object")
    with _pdf(source) as document:
        page_count = len(document)
    info = chunks.get("page_info")
    if not isinstance(info, dict) or set(info) != {str(i) for i in range(page_count)}:
        fail("STRUCTURE_PAGE_MAPPING_INVALID", "Chunks must cover every physical PDF page")
    pages = []
    for i in range(page_count):
        entry = info[str(i)]
        if not isinstance(entry, dict):
            fail("STRUCTURE_PAGE_MAPPING_INVALID", "Invalid page_info entry")
        box = bbox(entry.get("bbox"))
        if box[0] >= box[2] or box[1] >= box[3]:
            fail("STRUCTURE_PAGE_MAPPING_INVALID", "Page has no area")
        polygon(entry.get("polygon"), box)
        pages.append({"page_index": i, "physical_page": i + 1, "bbox": box,
                      "polygon": entry["polygon"], "rotation": None,
                      "coordinate_system": "marker_page_coordinates_not_calibrated_to_pdf_points"})
    incoming = chunks.get("blocks")
    if not isinstance(incoming, list) or len(incoming) > MAX_BLOCKS:
        fail("STRUCTURE_BLOCKS_INVALID", "Invalid Marker blocks list")
    ids, blocks, page_counters = set(), [], {}
    audit = envelope.get("recognition_audit")
    recovery_ids = set()
    if audit is not None:
        if not isinstance(audit, dict) or audit.get("version") != "MemoriveTextRecovery-v1" or not isinstance(audit.get("attempts"), list) or len(audit["attempts"]) > MAX_BLOCKS:
            fail("STRUCTURE_RECOVERY_AUDIT_INVALID", "Invalid transcription recovery audit")
        for attempt in audit["attempts"]:
            if not isinstance(attempt, dict) or not isinstance(attempt.get("upstream_id"), str):
                fail("STRUCTURE_RECOVERY_AUDIT_INVALID", "Invalid recovery block identity")
            recovery_ids.add(attempt["upstream_id"])
    for item in incoming:
        if not isinstance(item, dict):
            fail("STRUCTURE_BLOCKS_INVALID", "Marker block must be an object")
        upstream_page = item.get("page")
        upstream = item.get("id")
        match = re.fullmatch(r"/page/(\d{1,9})/[^/]{1,80}/\d{1,12}", upstream) if isinstance(upstream, str) else None
        # Marker 2.0 ChunkRenderer uses the last component of the Page block
        # ID as `page`. PageGroup's mutable block counter can occupy that slot.
        # The leaf ID and page_info keys carry the physical page index.
        if (type(upstream_page) is not int or not 0 <= upstream_page < 2**31
                or not match or not 0 <= int(match[1]) < page_count):
            fail("STRUCTURE_PAGE_MAPPING_INVALID", "Invalid Marker physical page identity")
        page = int(match[1])
        if page in page_counters and page_counters[page] != upstream_page:
            fail("STRUCTURE_PAGE_MAPPING_INVALID", "Marker blocks disagree on their page object")
        page_counters[page] = upstream_page
        if upstream in ids:
            fail("STRUCTURE_DUPLICATE_BLOCK", "Duplicate upstream block ID")
        ids.add(upstream)
        kind, html = item.get("block_type"), item.get("html")
        if not isinstance(kind, str) or not kind or len(kind) > 80:
            fail("STRUCTURE_BLOCKS_INVALID", "Invalid block type")
        if not isinstance(html, str) or len(html) > MAX_HTML_CHARS:
            fail("STRUCTURE_HTML_INVALID", "Block HTML is missing or exceeds 2 Mi characters")
        box = bbox(item.get("bbox"))
        shape = polygon(item.get("polygon"), box)
        a,b,c,d = pages[page]["bbox"]
        outside = box[0] < a-.01 or box[1] < b-.01 or box[2] > c+.01 or box[3] > d+.01
        coordinate = {}
        if outside:
            if not allow_page_fallback:
                fail("STRUCTURE_BBOX_OUTSIDE_PAGE", "Block lies outside its declared page")
            coordinate = {"coordinate_status":"PAGE_ONLY_OUTSIDE_PAGE", "upstream_bbox":box, "upstream_polygon":shape}
            box = list(pages[page]["bbox"])
            shape = [[box[0],box[1]],[box[2],box[1]],[box[2],box[3]],[box[0],box[3]]]
        if upstream.split("/")[-2] != kind:
            fail("STRUCTURE_BLOCKS_INVALID", "Block type disagrees with upstream identity")
        hierarchy = item.get("section_hierarchy")
        if hierarchy is None:
            hierarchy = {}
        if (not isinstance(hierarchy, dict) or len(hierarchy) > 32
                or any(not isinstance(k, str) or not k.isdecimal() or len(k) > 3
                       or not isinstance(v, str) or len(v) > 512 for k, v in hierarchy.items())):
            fail("STRUCTURE_HIERARCHY_INVALID", "Invalid section hierarchy")
        images = item.get("images")
        if images is None:
            images = {}
        if not isinstance(images, dict) or len(images) > 10000:
            fail("STRUCTURE_IMAGES_INVALID", "Invalid images object")
        text = plain_text(html)
        textual = kind in {"Text", "TextInlineMath", "SectionHeader", "ListItem", "Caption", "Footnote", "Equation", "Table", "Code", "Reference"}
        blocks.append({**coordinate, "upstream_id": upstream, "page_index": page, "block_type": kind,
                       "upstream_page_value": upstream_page,
                       "bbox": box, "polygon": shape, "html": html, "text": text,
                       "transcription_status": "NO_TEXT_RETURNED" if textual and not text else "RECOVERY_REVIEW_REQUIRED" if upstream in recovery_ids else "TEXT_PRESENT_NOT_VERIFIED" if text else "NON_TEXT_OR_SUPPRESSED",
                       "tables": tables(html), "section_hierarchy": hierarchy,
                       "image_count": len(images)})
    result = {"engine": "marker", "engine_version": "2.0.0", "config": config,
              "provenance": "EXTERNAL_CANDIDATE_DECLARATION_NOT_EXECUTION_PROOF",
              "pages": pages, "blocks": blocks,
              "capabilities": {"source_coordinates": True, "structured_tables": True,
                               "ocr": "DECLARED_NOT_VERIFIED", "image_derivatives": False},
              "warnings": ["EXTERNAL_MARKER_EXECUTION_NOT_VERIFIED", "HTML_IS_INERT_EVIDENCE_ONLY",
                           "IMAGE_DATA_RETAINED_ONLY_IN_ORIGINAL_CANDIDATE",
                           "MARKER_COORDINATES_REQUIRE_CALIBRATION_FOR_PDF_OVERLAY",
                            "MARKER_PAGE_FIELD_IS_NOT_USED_AS_PHYSICAL_PAGE_INDEX"],
              "empty_native_text_pages": None,
              "pages_without_structure_blocks": sorted(set(range(1, page_count + 1))
                  - {block["page_index"] + 1 for block in blocks}),
              "upstream_payload_sha256": digest(canonical(chunks))}
    if any(b.get("coordinate_status") for b in blocks):
        result["warnings"].append("OUTSIDE_PAGE_COORDINATES_RETAINED_AS_DATA_OVERLAY_DISABLED")
    if config["disable_ocr"]:
        result["warnings"].append("OCR_DECLARED_DISABLED")
    missing = [b["upstream_id"] for b in blocks if b["transcription_status"] == "NO_TEXT_RETURNED"]
    if missing:
        result["warnings"].append("TEXT_BLOCK_TRANSCRIPTION_MISSING_CHECK_ORIGINAL")
        result["untranscribed_block_ids"] = missing
    if recovery_ids:
        if not recovery_ids <= ids:
            fail("STRUCTURE_RECOVERY_AUDIT_INVALID", "Recovery refers to an absent block")
        result["warnings"].append("TEXT_RECOVERY_IS_UNVERIFIED_CHECK_ORIGINAL")
        result["recognition_audit"] = audit
    return result, raw
