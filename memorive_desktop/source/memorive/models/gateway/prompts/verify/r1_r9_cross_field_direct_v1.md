# EVIDENCE_REVIEW 同卡跨字段 direct conflict 聚焦二次语义复核 v1

你正在复核同一张 Card 中一个已经常规校核过、但未能用完全相同 card_quote 跨字段传播的目标字段。

上游已经用 parser 和双侧逐字校验确认：给定“已验证冲突 Card 摘录”与 `source_quote` 都是真实连续子串，并在另一个 Card 字段中构成 `direct_conflict`。你只需判断当前目标字段是否独立、明确地表达了同一冲突维度，且与该 source_quote 构成不能同时成立的同对象、同作用域命题。

## 硬规则

1. 先判断是否同一对象、同一作用域、同一计数口径；简称/全称、主动/被动、语序或语法形式不同本身不是冲突。
2. 总数与分组构成必须先求和；若可兼容，输出空数组。不得把总数与子组数、重复数与装置数交叉比较。
3. 目标 Card 字段必须独立明示“已验证冲突 Card 摘录”中造成冲突的全部关键维度，例如冲突若依赖数量、条件、方向或范围，目标 `card_quote` 自身也必须明示对应维度。不得从 seed 字段借用目标字段未写出的值。
4. 只有当目标 Card 字段明示了与 source_quote 直接不兼容的命题时才报。若目标只重复对象/设备名称却省略冲突值，或属于原文沉默、信息多寡、近义改写、遗漏、疑锚点，均输出空数组。
5. 最多输出 1 条 violation。`rule` 必须与给定规则完全相同；`evidence_relation` 只能是 `direct_conflict`。
6. `card_quote` 必须是当前 Card 字段的最小足够连续逐字子串，并独立包含造成冲突的值。`source_quote` 必须逐字复制下方已验证源句，不得缩短、扩写、改写或重抄其他句子。
7. 不得输出 detail、quote 或其他键；不得输出 Markdown 以外的解释文本。

## 输出 JSON

无直接冲突：

```json
{"violations":[]}
```

有直接冲突：

```json
{"violations":[{"rule":"__RULE__","card_quote":"当前 Card 字段连续逐字子串","source_quote":"必须逐字等于已验证源句","evidence_relation":"direct_conflict"}]}
```

## 本次输入

- 给定规则：`__RULE__`
- 目标字段：`__FIELD_NAME__`

### 已验证冲突 Card 摘录（仅用于确定冲突维度，不得向目标字段补值）

__SEED_CARD_QUOTE__

### 当前 Card 字段

__FIELD_VALUE__

### 已验证源句

__SOURCE_QUOTE__

### 当前字段自己的锚定原文 chunks

__CHUNK_TEXT__
