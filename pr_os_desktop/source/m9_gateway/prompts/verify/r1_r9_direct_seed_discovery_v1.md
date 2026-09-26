# M6 无 seed 卡级 direct conflict 聚焦发现 v1

<!-- DIRECT_SEED_DISCOVERY -->

你正在对同一张 Card 做一次卡级补漏。上游已经分别校核了下列 Card 字段，但没有得到任何通过双侧逐字校验的 `direct_conflict` 种子。你只需在这些已校核字段及各字段自己的锚定原文中，找出最多一组最明确、最高确信、直接且不能同时成立的冲突证据。

## 硬规则

1. 最多输出 1 条 violation；没有明确直接冲突就输出空数组。不得报告遗漏、疑锚点、信息多寡、措辞差异或一般歧义。
2. 先判断是否同一对象、同一作用域、同一计数口径。总数与分组构成必须先求和；若可兼容，输出空数组。不得把总数与子组数、重复数与装置数交叉比较。
3. `card_quote` 必须逐字取自某一个 SUBJECT 的“当前 Card 字段”；`source_quote` 必须逐字取自同一个 SUBJECT 的“该字段自己的锚定原文 chunks”。不得跨 SUBJECT 拼接证据，不得缩短后改写，不得补字或换字。
4. 人名、文内引用、变量、希腊字母、带变音符号字母及其他非英文字母都可能合法出现；这些字符本身不是冲突证据。不得建立字符白名单或按字符形态判错。
5. 不得使用外部知识、常识补全或其他字段未锚定的原文。简称/全称、主动/被动、语序、大小写和语法形式不同本身不是冲突。
6. `rule` 只能是 R1–R9 中与该直接冲突最贴近的一项；`evidence_relation` 只能是 `direct_conflict`。
7. 不得输出 location、field、detail、quote 或其他键；不得输出 Markdown 以外的解释文本。

## 输出 JSON

无直接冲突：

```json
{"violations":[]}
```

有直接冲突：

```json
{"violations":[{"rule":"R1-R9 之一","card_quote":"某一 Card 字段的连续逐字子串","source_quote":"同一 SUBJECT 锚定原文的连续逐字子串","evidence_relation":"direct_conflict"}]}
```

## 已校核 SUBJECTS

__SUBJECT_BLOCKS__
