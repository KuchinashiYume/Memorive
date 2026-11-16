---
name: memo-feedback
description: Turn a research result the user wants to keep into a Memorive knowledge draft, with the cited evidence, its scope and its limitations. The draft waits for the user's review in Memorive. Use when the user asks to save a finding as reusable knowledge; not for approving, changing or retracting knowledge.
---

# Memo feedback

Prepare one knowledge draft from a result the user asked to keep. It stays pending until the user reviews it in Memorive.

1. Read every passage you will cite with `memo_read_evidence`: `project`, `evidence_id` and the `expected_hash` that came with it. If the source has changed since, the read fails (`EVIDENCE_VERSION_CONFLICT` or `EVIDENCE_STALE`); read the current evidence again instead of citing the old text.
2. Write the draft in three parts: the reusable claim, the scope in which it holds, and its limitations. Leave out anything the passages do not support.
3. Submit it with `memo_submit_draft`: `project`, `title`, `claim`, `scope`, `limitations`, and the `evidence_ids` you read. When the work answers a Memo handoff, also pass that handoff's `handoff_id` and `handoff_hash` and a stable `request_id`: all three or none.
4. Tell the user the draft ID that comes back and that it is pending review. Only the user reviews and confirms it, in Memorive.

Do not update citations silently, fill in missing lineage, or call a claim active. To change or retract knowledge that is already active, point the user to that item under Research Chat > Knowledge in Memorive; these tools have no method that approves or retracts.
