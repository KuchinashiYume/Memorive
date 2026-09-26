# Run / Observation / Candidate / Slate / Event 共同合同

- schema_revision: `p04-t01-common-contract-r0.1-candidate001`
- 所有 fixture 都是 public-safe synthetic；T01 不创建真实 run、来源观察、候选或 Slate。
- envelope 固定 object_id/revision/hash/producer/consumer/single writer/parents/supersedes/immutable fields。
- `AI_HINT` 只是 discovery_origin，不是书目身份、全文权利、人工决定、Card 或晋升状态。
- parent 断裂、重复 ID/revision、hash mismatch、未知字段或 enum 均拒绝。
