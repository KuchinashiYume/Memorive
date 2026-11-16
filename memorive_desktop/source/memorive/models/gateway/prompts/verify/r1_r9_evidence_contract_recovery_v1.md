# EVIDENCE_REVIEW 双侧证据 JSON 契约恢复 v1

<!-- EVIDENCE_REVIEW_EVIDENCE_CONTRACT_RECOVERY -->

上一次 `__RECOVERY_LABEL__` 响应未通过机器证据契约，不能进入信用档。请严格按下方原任务重新判断一次，并只返回合法 JSON。

## 恢复硬规则

1. 语义任务、候选范围和证据标准完全以“原任务”为准，不得放宽，不得使用外部知识。
2. 不得机械给上次响应补键。先独立比较上次候选的 `card_quote` 与 `source_quote`：判断它们是否描述同一对象、同一作用域和同一口径，以及两者能否同时为真。缺少结构键本身不是丢弃逐字候选或默认空数组的理由。
3. 若候选双侧命题在同对象同口径下不能同时为真，必须保留原响应中的逐字 `rule`、`card_quote`、`source_quote`，并输出完整 `direct_conflict` 契约；只有语义可兼容、quote 不满足原任务逐字边界、只是遗漏/信息多寡/一般歧义时才返回 `{"violations":[]}`。
4. 每条 violation 必须恰好包含四个键：`rule`、`card_quote`、`source_quote`、`evidence_relation`，不得多也不得少。
5. `evidence_relation` 只能是字符串 `direct_conflict`。两侧 quote 必须遵守原任务的逐字、同字段和同锚点要求，不得改写、补字、换字或跨字段复制。
6. 不得输出 Markdown 围栏、解释、detail、location、field、quote 或其他文本，只输出一个 JSON 对象。

合法形式仅为：

{"violations":[]}

或：

{"violations":[{"rule":"原任务允许的 R1-R9 规则","card_quote":"Card 连续逐字子串","source_quote":"锚定原文连续逐字子串","evidence_relation":"direct_conflict"}]}

## 原任务

__ORIGINAL_TASK__

## 上一次不合格响应（仅供识别错误，不得照抄结论）

__INVALID_RESPONSE__
