# M6 搬运类校核 · v11 omission 方向与计数冲突增量

> 本文件只追加在完整、无矛盾的 v10 基线后。它不改变三种 relation、
> 四字段 JSON、逐字证据或 fail-closed，只澄清 omission 的方向并强化计数冲突扫描。

## explicit_omission 的单向必要条件

- `source_quote 必须包含 Card 缺失的科学内容`：关键步骤、条件、作用域、统计限定或对象必须在 source 中明确出现，而在 card_quote 对应命题中确实缺失。
- 若 **Card 反而多出** source_quote 没有的单位、对象、步骤或结论，不得使用 `explicit_omission`。给定 chunks 没有支持时用 R8 `missing_from_given_chunks`；只有原文明示不同值/单位/对象时才用 `direct_conflict`。
- source 仅多出 `resulting in`、冠词、连接词、句法成分或其他不改变科学命题的文字，不是关键遗漏。

## 每字段逐项扫描计数与数值

- 输出前必须对 Card 中的实验数量、样本数、反应器数、组数、阶段数及其他计数**逐项扫描计数**，再与给定原文的同一对象逐项对照；数字与英文数词都属于数值。
- Card 与原文在**同一容量或其他唯一上下文**下明确给出不同计数，即使一边用全称、另一边用缩写或同一装置的简称，只要上下文足以确定是同一对象，也属于 `direct_conflict`。
- 简称/全称不同本身不构成冲突；必须同时有同一对象上下文和明确不同的计数、数值、方向或肯否。
