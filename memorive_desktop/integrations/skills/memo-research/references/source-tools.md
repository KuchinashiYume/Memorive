# Source preparation and checking

Resolve script paths relative to this Skill, and choose the host's available Python 3.10+. MD/TXT preparation uses the standard library. PDF conversion and rendering require the optional dependencies in `scripts/requirements-pdf.txt`. Use an existing suitable interpreter first; if missing, create a task-local venv and install that file. Do not install into an unrelated project or silently modify a shared interpreter.

```text
python <skill>/scripts/sources.py prepare --out <task>/sources <paper.pdf> <other.md>
python <skill>/scripts/sources.py verify <bundle>/source.json
python <skill>/scripts/sources.py evidence <bundle>/source.json --page 3 --lines 110:119
python <skill>/scripts/sources.py evidence <bundle>/source.json --page 3 --lines 110:119 --quote "short verbatim substring"
python <skill>/scripts/sources.py render <bundle>/source.json --page 3 --out <task>/page-3.png
```

`prepare` prints one JSON object with results for every input; exit 2 means some inputs failed, while successful bundles remain usable. PDF conversion uses PyMuPDF4LLM with original physical PDF pages (1-based). MD/TXT is one text chunk, not PDF page 1; omit `--page`. RawMD line numbers are 1-based global line numbers in the generated file. Read `source.json.pages` for page-to-line boundaries before choosing a span. Line wrapping and conversion can differ from print layout. `evidence` confirms artifact integrity, optional current source integrity, range and quote membership; it does not judge whether a quote supports a claim.

`verify --current <input>` additionally compares a current input to the preserved original. Missing external originals do not invalidate an intact archived bundle, but the helper cannot infer whether a newer document exists. `prepare` refuses corrupted cached bundles; choose a new output folder or investigate instead of overwriting evidence.

The `prepare` cache is local to its `--out` directory. For a follow-up with previous artifacts, first locate `source.json` within the supplied previous-artifact directory, then run `verify <existing-manifest> --current <current-input>`. Check `converter.helper`, `converter.helper_sha256`, package versions and settings against the current script/environment; `verify` alone checks bytes, not converter compatibility. Reuse that manifest/RawMD via a relative or absolute path from the new answer. Do not create a second conversion just to populate a new answer directory. Report missing inputs once and continue with readable inputs; a confirmed nonexistent path needs no repeated conversion attempt.

The helper records sparse extracted pages and declares that OCR was not run. A non-sparse extraction still needs targeted original-page inspection for important tables, equations and ambiguous reading order. Converted Markdown is not a visual replica. Use `render` only for the pages needed; inspect the resulting PNG through the host image tool. Rendering does not authorize an unscoped OCR or model upload.

For saved analyses, use a minimal `citations.json`:

```json
{
  "question": "the question answered",
  "coverage": [{"input": "paper.pdf", "status": "read", "gaps": []}],
  "claims": [{
    "id": "C1",
    "text": "a consequential source claim or bounded cross-source comparison",
    "kind": "source_statement",
    "supports": [{
      "manifest": "sources/<bundle>/source.json",
      "source_sha256": "full SHA-256 from source.json",
      "page": 3,
      "line_start": 110,
      "line_end": 119,
      "quote": "optional short exact excerpt",
      "checked": true
    }]
  }]
}
```

Use `kind: inference` for a bounded synthesis and cite its premises; keep general background out of the source-evidence list unless externally verified. Use `page: null` for MD/TXT. Paths are relative to the analysis directory, or absolute when needed. `checked: true` means the cited location was actually checked; it does not grant scientific validity. Keep public prose concise rather than showing this JSON to the user by default.
