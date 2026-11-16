---
name: memo-tension
description: Compare findings from two or more papers in a Memorive project that may disagree, and prepare a cited explanation of where and why they differ, for the user to review. Use for questions like "do these papers conflict?"; not for finding new papers or judging novelty.
---

# Memo tension

1. Find the relevant passages with `memo_search` (`project`, `query`) and read the ones you will use with `memo_read_evidence`. They must come from at least two different papers.
2. Call `memo_prepare_tension` with `project` and the `evidence_ids` (2 to 12, from at least two papers). It returns the passages, the eight comparison facets and a `source_snapshot_hash` that fixes which source versions you compared.
3. Compare the papers facet by facet: research object, intervention, comparator, method, metric, boundary conditions, scale and claim type. Quote the evidence for each. A facet the passages do not cover is unknown.
4. Say what kind of difference you found: different conditions, measurements that cannot be compared, or a real conflict in direction. A search that found nothing is not evidence that a finding is new.
5. Give the user your interpretation with citations and limitations. Only when the user asks to keep it, submit it with `memo_submit_draft` as described in memo-feedback; it stays pending review. Do not present it as scientifically accepted, and do not edit the Vault directly to get around the review.
