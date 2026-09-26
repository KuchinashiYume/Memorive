# EVIDENCE_REVIEW 搬运类校核 · v7 复杂字段证据与灾难恢复增量

> 本文件追加在不可变的 v6 提示词之后。v6 及更早版本继续保留，
> 本增量只收紧输出契约，不改变 R1–R9 的事实判断标准。

## 复杂 key_data 的逐字证据

- “卡上内容”可能是带缩进、双引号、逗号和换行的 JSON。
- `card_quote` 必须从该 JSON 逐字符复制一段连续子串。
- 不得把 JSON 改写成 `stat: ... | sample: ...` 一类扁平文本；这种改写不是逐字摘录。
- 如果完整片段太长，可以缩短，但仍须是输入中可直接搜索命中的连续子串。

## evidence_relation 最终决策闸门

- 两侧逐字摘录足以证明冲突、改值、漏条件或术语变化：使用 `direct_conflict`，两侧 quote 均非空。
- 只有卡片摘录能命中，而当前 chunks 找不到可定位的相反原句：使用 `missing_from_given_chunks`，并令 `source_quote` 严格为 JSON `null`、rule 严格为 `R8`。
- 能找到“相关或支持性”原文，但它不能证明直接冲突：不得把该原文挂在 `missing_from_given_chunks` 下，也不得为了保留摘录而伪报 `direct_conflict`；该候选不报 violation。

<!-- RECOVERY_ONLY_START -->
## 灾难恢复重跑 · 只纠正输出契约

上一轮响应没有通过本地机械契约。本轮仍独立核对事实，但输出前必须再次检查：

1. 顶层只有 `violations`。
2. 每条只有 `rule`、`card_quote`、`source_quote`、`evidence_relation`。
3. 不得改写成扁平文本；两个 quote 必须逐字符在各自输入中搜索命中。
4. `missing_from_given_chunks` 的 `source_quote` 必须为 null。
5. 相关原文不等于冲突证据；不能证明冲突时不要伪造 violation。
6. 最终只输出 JSON，不输出解释。
<!-- RECOVERY_ONLY_END -->
