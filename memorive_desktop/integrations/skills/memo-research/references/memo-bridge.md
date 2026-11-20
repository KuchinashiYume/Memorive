# Return research to the current Memo question

Use the supplied `handoff.json`: current question, selected sources and eligible history. Earlier judgments are claims to check, not conclusions new material must support. Desktop handoffs contain `transport.json`, the configured local command without credentials. Do not invent a workspace or connect to another project. Older Memo releases may offer only search/read/draft; the helper checks capabilities and says so, and then you keep local results instead of claiming a return.

The helper `scripts/memo_bridge.py` uses that public transport, never the database. Its work files go to `work/` beside `handoff.json`; the hand-off folder holds only the pack, the answer and `work/`.

## 1. Read the sources

```text
python <skill>/scripts/memo_bridge.py find PATH/handoff.json "term" "another term" "12.5"
python <skill>/scripts/memo_bridge.py read PATH/handoff.json
python <skill>/scripts/memo_bridge.py read PATH/handoff.json --artifact art_REAL_ID --pages 3-6
```

`find` prints the passages that contain any of the terms (most terms first), numbered E1, E2 …, with paper, page and the matching lines plus table and figure titles, and keeps them. `read` keeps every passage of the hand-off (or of one source and page range) and writes them all to `work/evidence.md` to read with your file tool. Both write `work/evidence.json` and `work/evidence.md`, and later calls keep the numbers. The answer's coverage follows what was kept: a source counts as read when all its passages were kept, partial otherwise. For a broad question keep reading until the coverage you need is actually reached.

Use Memo's text directly; do not reconvert a PDF because its converter differs from the standalone helper. `line_basis=rawmd_global` means global RawMD lines; regular attachments use page-local lines. Physical PDF pages are independent. Inspect a supplied original for important diagrams or extraction ambiguities. Source warnings are processing gaps, not limitations of the research.

## 2. Draft with support marks

Write the answer as Markdown (for example `work/draft.md`). Organize a provisional answer, check important claims once, and correct unsupported facts. Explain what new material changes for this question. Distinguish source statements, inference, background and user hypotheses. Do not automatically recommend similar papers.

Put the support right after each consequential claim:

- `[E3: "short exact quote"]` — a source statement; several passages: `[E3: "…"; E7: "…"]`
- `[inference E3: "…"; E5: "…"]` — an inference, citing its premises; computed values (differences, ratios, conversions) are inferences
- background and the user's hypotheses carry no mark

The claim is the text from the previous mark, sentence end or line start up to the mark, so keep one claim per sentence or clause. Copy quotes exactly from the passage; keep them short, and for a number quote the words that report it. Give readable titles and pages in the prose; never internal IDs.

## 3. Build, check and return

```text
python <skill>/scripts/memo_bridge.py answer PATH/handoff.json --draft work/draft.md --gap "what was not read and why it matters" --return
```

`answer` replaces the marks with [1], [2] … in first-use order and saves `memo-answer.json`. It refuses the draft, listing every problem at once, when a quote is not in its passage (it shows the closest text of that passage), a number of a source statement is not in the passages it cites (it names the kept passages that report it), a value outside the claims is reported by no kept passage, or a passage was not read. Quotes may differ from the extracted text in ligatures, curly quotes, dashes, line breaks and words split at a line end; the answer stores the passage's own text. Coverage comes from what `read` fetched: `read`, `partial` or `unread` per source; `--gap` adds your concrete account of the unread parts and their impact (put material gaps in the answer too). The request ID is derived from the answer, so an exact retry is the same request and a changed answer is a new one.

`--return` sends it with `memo.skill_answer`. The printed `status` says what happened and `next` what to tell the user: `RETURNED` means the answer is in the same conversation with its citations and was not admitted as knowledge; `NOT_RETURNED` means the answer is complete and saved but Memorive's local command could not run (an agent sandbox blocks it, for instance): the user can approve the command, or add `memo-answer.json` through that conversation's attachment picker. Without `--return`, return later with `python <skill>/scripts/memo_bridge.py return PATH/handoff.json --result memo-answer.json`. Stale question, source or conversation bindings are rejected with their code and what it means: keep the local answer and obtain a fresh handoff; never bypass checks. It becomes an answer with citations, not a new independent source or admitted knowledge. Hash and quote checks establish traceability, not semantic entailment.

Only when the user requests knowledge preservation, use the returned answer's existing Memo knowledge-save action, or `memo_submit_draft` with real evidence IDs and scope/limitations under a fresh handoff. It creates pending review. Returning an answer advances the conversation, invalidating the old handoff for further operations and review of any draft bound to it. Do not submit a bound knowledge draft and then invalidate it by returning an answer under the same handoff.

## Hosts with MCP tools

Use `memo_capabilities`, `memo_skill_context`, `memo_read_evidence`, `memo_skill_answer` when configured, with their runtime schemas (`methods[method].required` and `properties`). MCP names use underscores; CLI methods use dots. An exact evidence read is `memo.read_evidence` with `{"project", "evidence_id", "expected_hash"}` from the current handoff/context; returned evidence names the hash `content_hash`. `memo_skill_answer` takes the fields of `memo-answer.json` without `schema`:

```json
{
  "schema": "memo-research-answer/1",
  "project": "FROM_HANDOFF", "handoff_id": "FROM_HANDOFF", "handoff_hash": "FROM_HANDOFF",
  "request_id": "stable-unique-request-id",
  "answer": "Direct answer and explanation. Claim wording [1]. Limits and material gaps.",
  "claims": [{"text": "Claim wording", "kind": "source_statement",
              "supports": [{"evidence_id": "REAL_MEMO_ID", "content_hash": "RETURNED_CONTENT_HASH", "quote": "Short exact source text"}]}],
  "coverage": [{"artifact_id": "REAL_MEMO_ARTIFACT_ID", "status": "partial", "gaps": "Specific unread part and its impact."}]
}
```

Claim text occurs verbatim in `answer`; statements and inferences need supports; [n] numbers follow the deduplicated first-use order of supports; coverage names every context source once, and partial/unread sources need concrete gaps. `memo_bridge.py context PATH/handoff.json --cursor N` prints one raw `memo.skill_context` page.

## Reuse independent artifacts

The user can add each standalone `source.json` through Memo's attachment picker. Memo validates/copies Original and RawMD, preserves conversion identity, ranges and warnings, and does not re-extract. Valid sources proceed even if another file fails. Adding the same original later reuses that verified extraction. Do not import Analysis as another original.

After import create a fresh handoff. An existing answer whose question still matches can be mapped:

```text
python <skill>/scripts/memo_bridge.py bind PATH/handoff.json --analysis analysis.md --citations citations.json --request-id stable-request-001 --out memo-answer.json
```

This verifies local bundles and maps source hash + RawMD hash + page/line spans to real Memo IDs. Mismatched conversions and guessed IDs fail. It only saves the local return file. Inspect it, then return as above. A changed question requires fresh reasoning. Mapping conservatively marks coverage partial; refine only to reflect reading actually performed.

Keep saved project weights. Original, RawMD, Card and Analysis from one paper share a lineage, not independent corroboration. Cache reuse is not scientific correctness. Existing full-workflow optional node controls remain in effect.
