# Source preparation and checking

Resolve script paths relative to this Skill, and choose the host's available Python 3.10+. MD/TXT preparation uses the standard library. PDF conversion and rendering require the optional dependencies in `scripts/requirements-pdf.txt`. Use an existing suitable interpreter first; if missing, create a task-local venv and install that file. Do not install into an unrelated project or silently modify a shared interpreter.

```text
python <skill>/scripts/sources.py prepare --out <task>/sources <paper.pdf> <other.md>
python <skill>/scripts/sources.py verify <bundle>/source.json
python <skill>/scripts/sources.py evidence <bundle>/source.json --page 3 --lines 110:119
python <skill>/scripts/sources.py evidence <bundle>/source.json --page 3 --lines 110:119 --quote "short verbatim substring"
python <skill>/scripts/sources.py render <bundle>/source.json --page 3 --out <task>/work/page-3.png
python <skill>/scripts/sources.py cite --sources <task>/sources --draft <task>/work/draft.md --question "the question"
```

`prepare` prints one JSON object with results for every input; exit 2 means some inputs failed, while successful bundles remain usable. Each bundle gets a short name, S1, S2 …, kept in `<task>/sources/index.json`. PDF conversion uses PyMuPDF4LLM with original physical PDF pages (1-based). MD/TXT is one text chunk, not PDF page 1; omit `--page`. RawMD line numbers are 1-based global line numbers in the generated `raw.md`; read it with your file tool. Read `source.json.pages` for page-to-line boundaries before choosing a span. Line wrapping and conversion can differ from print layout. `evidence` confirms artifact integrity, optional current source integrity, range and quote membership; it does not judge whether a quote supports a claim.

`verify --current <input>` additionally compares a current input to the preserved original. Missing external originals do not invalidate an intact archived bundle, but the helper cannot infer whether a newer document exists. `prepare` refuses corrupted cached bundles; choose a new output folder or investigate instead of overwriting evidence.

The `prepare` cache is local to its `--out` directory. For a follow-up with previous artifacts, first locate `source.json` within the supplied previous-artifact directory, then run `verify <existing-manifest> --current <current-input>`. Check `converter.helper`, `converter.helper_sha256`, package versions and settings against the current script/environment; `verify` alone checks bytes, not converter compatibility. Reuse that manifest/RawMD via a relative or absolute path from the new answer. Do not create a second conversion just to populate a new answer directory. Report missing inputs once and continue with readable inputs; a confirmed nonexistent path needs no repeated conversion attempt.

The helper records sparse extracted pages and declares that OCR was not run. A non-sparse extraction still needs targeted original-page inspection for important tables, equations and ambiguous reading order. Converted Markdown is not a visual replica. Use `render` only for the pages needed; inspect the resulting PNG through the host image tool. Rendering does not authorize an unscoped OCR or model upload.

## Saved analyses: draft with marks, then `cite`

Write the analysis as Markdown in `<task>/work/draft.md`, in the user's language. Put the support right after each consequential claim:

- `[S1 p.3 L120-124: "short exact quote"]` — a source statement; MD/TXT spans omit the page (`[S2 L8-10: "…"]`); several spans: `[S1 p.3 L120: "…"; S2 L8: "…"]`
- `[inference S1 p.3 L120: "…"; S1 p.5 L40: "…"]` — an inference, citing its premises; computed values are inferences
- background and the user's hypotheses carry no mark

The claim is the text from the previous mark, sentence end or line start up to the mark. End the draft with one coverage line per prepared source:

```text
<!-- coverage
S1 read
S2 partial: the methods section and the supplementary tables were not read
-->
```

For a reusable analysis use one `analysis.md`: the direct answer and the scope of papers and question; what each source contributes (a comparison table only when it helps); the reasoning with locators, counterevidence and limits; useful background, labelled as such; open questions and processing gaps. Write per-paper analyses or cards only when asked. Save into a new task folder; never overwrite an earlier answer without being asked.

`cite` checks every span with `evidence`, refuses a source statement whose numbers are not in its spans, and lists every problem at once. It then writes `<task>/analysis.md` (marks become readable locators such as `[p.3 L120-124]`) and `<task>/citations.json`: the question, the coverage, and each claim with its kind, verbatim text and supports (manifest path relative to the analysis, source hash, page, lines, quote, `checked: true`). Claim texts stay verbatim in the analysis, so `memo_bridge.py bind` can later map the answer to Memo evidence. `checked: true` means the cited location was actually checked; it does not grant scientific validity. Keep public prose concise rather than showing this JSON to the user by default.
