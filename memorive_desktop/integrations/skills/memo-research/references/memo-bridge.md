# Return research to the current Memo question

Use supplied `handoff.json`: current question, selected sources and eligible history. Earlier judgments are claims to check, not conclusions new material must support. Check runtime capabilities. Older Memo releases may offer only search/read/draft; keep local results when return is unsupported.

## Reuse existing material

Use configured MCP tools `memo_capabilities`, `memo_skill_context`, `memo_read_evidence`, `memo_skill_answer` when offered, with their runtime schemas. MCP names use underscores; CLI methods use dots. Desktop handoffs contain `transport.json`, the configured local command without credentials. Do not invent a workspace or connect to another project.

Capabilities exposes `methods[method].required` and `properties`; use these rather than guessing arguments or inspecting product code. An exact evidence read uses `memo.read_evidence` with `{"project":"FROM_HANDOFF","evidence_id":"FROM_CONTEXT","expected_hash":"CONTEXT_CONTENT_HASH"}`. All three values come from the current handoff/context. The schema calls the input `expected_hash`, while returned evidence names it `content_hash`.

The helper uses the public transport, never the database:

```text
python scripts/memo_bridge.py context PATH/handoff.json --limit 12
python scripts/memo_bridge.py context PATH/handoff.json --artifact art_REAL_ID --cursor 12
```

`next_cursor` is relative to the same artifact filter. Read relevant pages, adjacent methods/conditions and counterevidence. For broad reading continue until required coverage is actually reached. The response lists all selected sources even when only some excerpts were returned; unread sources remain explicit.

Reuse Memo's text and real `ev_*` IDs directly. Do not reconvert a PDF because its converter differs from the standalone helper. `line_basis=rawmd_global` means global RawMD lines; regular attachments use page-local lines. Physical PDF pages are independent. Inspect a supplied original for important diagrams or extraction ambiguities. Source warnings are processing gaps, not limitations of the research.

## Return one cited answer

Organize a provisional answer, check important claims once, and correct unsupported facts. Explain what new material changes for this question. Distinguish source statements, inference, background and user hypotheses. Do not automatically recommend similar papers.

For connected research save one `memo-answer.json`; analysis.md is optional:

```json
{
  "schema": "memo-research-answer/1",
  "project": "FROM_HANDOFF",
  "handoff_id": "FROM_HANDOFF",
  "handoff_hash": "FROM_HANDOFF",
  "request_id": "stable-unique-request-id",
  "answer": "Direct answer and explanation. Claim wording [1]. Limits and material gaps.",
  "claims": [{
    "text": "Claim wording",
    "kind": "source_statement",
    "supports": [{"evidence_id": "REAL_MEMO_ID", "content_hash": "RETURNED_CONTENT_HASH", "quote": "Short exact source text"}]
  }],
  "coverage": [{"artifact_id": "REAL_MEMO_ARTIFACT_ID", "status": "partial", "gaps": "Specific unread part and its impact."}]
}
```

Claim text must occur verbatim in `answer`. Kinds: `source_statement`, `inference`, `background`, `user_hypothesis`. Statements and inferences need supports; explain inference premises and limits in prose. Copy exact IDs/hashes and short quotes from evidence. Use [1], [2] by deduplicated first-use support order; readable titles and pages help. Do not expose internal IDs in user-facing prose.

Coverage includes every context source exactly once as `read`, `partial` or `unread`. Partial/unread sources need concrete gaps. Put material gaps in the answer too. Hash/quote checks establish traceability, not semantic entailment.

When asked to continue/return this Memo research, returning to the same conversation is in scope:

```text
python scripts/memo_bridge.py return PATH/handoff.json --result memo-answer.json
```

Or call `memo_skill_answer` with the same fields except `schema`. Keep one stable request ID for exact retries; changed payloads need a new ID. Stale question, source or conversation bindings are rejected. Keep the local answer and obtain a fresh handoff; never bypass checks. If disconnected, the user can add `memo-answer.json` through that conversation's existing attachment picker. It becomes an answer with citations, not a new independent source or admitted knowledge.

Only when the user requests knowledge preservation, use the returned answer's existing Memo knowledge-save action, or `memo_submit_draft` with real evidence IDs and scope/limitations under a fresh handoff. It creates pending review. Returning an answer advances the conversation, invalidating the old handoff for further operations and review of any draft bound to it. Do not submit a bound knowledge draft and then invalidate it by returning an answer under the same handoff.

## Reuse independent artifacts

The user can add each standalone `source.json` through Memo's attachment picker. Memo validates/copies Original and RawMD, preserves conversion identity, ranges and warnings, and does not re-extract. Valid sources proceed even if another file fails. Adding the same original later reuses that verified extraction. Do not import Analysis as another original.

After import create a fresh handoff. An existing answer whose question still matches can be mapped:

```text
python scripts/memo_bridge.py bind PATH/handoff.json --analysis analysis.md --citations citations.json --request-id stable-request-001 --out memo-answer.json
```

This verifies local bundles and maps source hash + RawMD hash + page/line spans to real Memo IDs. Mismatched conversions and guessed IDs fail. It only saves the local return file. Inspect it, then return as above. A changed question requires fresh reasoning. Mapping conservatively marks coverage partial; refine only to reflect reading actually performed.

Keep saved project weights. Original, RawMD, Card and Analysis from one paper share a lineage, not independent corroboration. Cache reuse is not scientific correctness. Existing full-workflow optional node controls remain in effect.
