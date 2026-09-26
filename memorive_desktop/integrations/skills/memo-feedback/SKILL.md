---
name: memo-feedback
description: Turn an authorized research result into a Memorive knowledge-feedback draft with verifiable evidence and explicit scope.
---

Read the source evidence with expected_hash checks. Separate the reusable claim, its applicable scope, and unresolved limitations. Use exact returned evidence IDs with memo_submit_draft.

Report the returned draft ID and its review_pending state. Only the user reviews and confirms within Memorive. A changed source invalidates the draft until the evidence is refreshed; do not silently update citations, fill missing lineage, or mark a claim active.

If the user asks to change or retract active knowledge, direct them to the matching item in Research Chat > Knowledge in Memorive. These agent tools deliberately have no approval or retraction method.
