# EVIDENCE_REVIEW 无 seed 卡级全字段扩展 v1

<!-- DIRECT_SEED_CARD_EXPANSION -->

上游已经从同一张 Card 中发现并逐字验证了一组 `direct_conflict` seed。你现在只做一次全字段扩展：逐个检查下列所有 TARGET SUBJECT，找出哪些目标字段自身也独立、明确地表达了与同一 `source_quote` 不能同时成立的同对象、同作用域命题。

## 硬规则

1. 必须逐个独立检查全部 TARGET SUBJECT。每个目标最多输出 1 条；没有符合项就输出空数组。
2. “已验证冲突 Card 摘录”只用于确定冲突维度，不得向目标字段补值。目标字段必须自身明示造成冲突的全部关键维度；若冲突依赖数量、条件、方向或范围，目标 `card_quote` 自身也必须明示对应维度。
3. 目标若只重复对象、设备、材料或过程名称，却省略冲突值，必须不报。总数与分组构成先求和；可兼容、信息多寡、遗漏、疑锚点、近义措辞或一般歧义都不报。
4. 每条 `card_quote` 必须逐字取自对应 TARGET SUBJECT 的当前 Card 字段；`source_quote` 必须逐字复制下方已验证源句。不得跨 TARGET 拼接，不得复制 seed 字段原句冒充目标引语。
5. 每条 `rule` 必须等于给定规则；`evidence_relation` 只能是 `direct_conflict`。不得缩短、扩写、改写、补字或换字。
6. 人名、文内引用、变量、希腊字母、带变音符号字母及其他非英文字母都可能合法出现；这些字符本身不是冲突证据。不得建立字符白名单或按字符形态判错。
7. 不得输出 location、field、detail、quote 或其他键；不得输出 JSON 以外的解释文本。

## 输出 JSON

```json
{"violations":[{"rule":"__RULE__","card_quote":"某个目标字段自身的连续逐字子串","source_quote":"必须逐字等于已验证源句","evidence_relation":"direct_conflict"}]}
```

没有符合项时：

```json
{"violations":[]}
```

## 已验证 seed

- 给定规则：`__RULE__`

### 已验证冲突 Card 摘录

__SEED_CARD_QUOTE__

### 已验证源句

__SOURCE_QUOTE__

## 待检查 TARGET SUBJECTS

__TARGET_SUBJECT_BLOCKS__
