# E10-A structure protocol

This package writes auxiliary candidates. It does not launch Marker, execute HTML,
decode image data, replace RawMD or admit table cells as research facts.

The `document_conversion` facade and CLI accept `structure_mode=none` (default),
`native_pdf`, or `marker_chunks`. Only PDF is supported in this batch. API results
add optional `structure_path` and `structure_receipt_path`. Existing conversion
receipt v1 fields remain intact; its evidence_refs can bind the structure hashes.

## Marker import envelope

```json
{
  "schema_version": "MemoriveMarkerChunksEnvelope-v1",
  "source_sha256": "<64 hex characters of the original PDF>",
  "engine": "marker",
  "engine_version": "2.0.0",
  "config": {"mode": "fast", "disable_ocr": true, "use_llm": false},
  "chunks": {
    "blocks": [],
    "page_info": {
      "0": {"bbox": [0, 0, 400, 600], "polygon": [[0, 0], [400, 0], [400, 600], [0, 600]]}
    },
    "metadata": {}
  }
}
```

`chunks` is the JSON serialization of Marker 2.0.0 ChunkOutput. The example is an
empty one-page protocol illustration, not Marker execution evidence. Every physical
PDF page, including blank pages, must have page_info. Page-range candidates are
not accepted by this first protocol. Block IDs must match `/page/N/TYPE/ID` and
the declared page and block_type. The top-level config has exactly the three shown
keys; `balanced` and `disable_ocr=false` declarations are accepted as unverified
external provenance. `use_llm=true` is outside this batch's import profile.

DocumentStructure-v1 uses zero-based `page_index`, one-based `physical_page`,
zero-based `reading_order`, and zero-based table row/column positions. `rowspan`
and `colspan` retain the original table geometry. The original HTML remains
byte-for-byte as a JSON string; text is a convenience projection and may omit
typographic distinctions such as superscript styling. It must not replace HTML
or the original source for numeric verification.

Native coordinates are PyMuPDF unrotated page points, accompanied by rotation.
Marker coordinates retain its declared page frame; PDF overlays need separate
calibration. A source-bound ID is stable only for identical provider output and
configuration. It is not a cross-version entity ID.

## Integrity and limits

The facade writes all files under a temporary directory and then publishes the
whole directory. On failure it preserves a `.failed-*` candidate and returns an
error. No existing destination is overwritten. Source and RawMD are bound to
the exact retained bytes. A sibling StructureReceipt lists their names and hashes,
the structure hash, and the imported envelope hash, when present.

`verify_bundle(receipt_path)` verifies these bytes and structure identity. A PASS
establishes consistency only; it is not a signature, an extraction accuracy grade,
proof of model execution, or protection against someone rewriting all receipts.

Limits: 32 MiB JSON per artifact; 512 MiB PDF; 2,000 pages; 100,000 blocks;
2 Mi characters HTML per block; 4,096 table rows, 512 columns and 200,000 expanded
grid positions per table. Nested tables and malformed/overlapping spans need
separate review. Invalid JSON, nonfinite/oversized coordinates, duplicate keys,
duplicate block IDs and missing pages fail with DocumentConversionError codes.

This is a callable library/CLI implementation. Desktop pipeline wiring, optional
Marker worker, model installation, quality qualification and native packaging
remain E10-B/C/D work.
