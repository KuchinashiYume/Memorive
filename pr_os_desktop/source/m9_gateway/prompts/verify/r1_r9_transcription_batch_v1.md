# M6 搬运类校核 · 整批首审 v1 · M6_BATCH_INITIAL

你将一次审核同一张 Card 的全部待核字段。输入中的 `subjects` 给出字段位置、
字段名、结构化 `card_items` 和该字段允许使用的 chunk_id；`sources` 按 chunk_id 去重保存原文。
一个 source 可以被多个 subject 引用，但判断每条 violation 时只能使用该 subject 的
`chunk_ids` 指向的来源。不得把别的字段锚点偷渡为当前字段证据。

__POLICY_TEXT__

## 整批输入

__BATCH_INPUT_JSON__

## 整批执行要求

1. 必须逐一审核 `subjects` 中的每个 location，不得因字段相似、共享 chunk 或内容较长而跳过。
2. 对每个 subject 必须按 `card_items` **逐个 item_index** 审核；每项再枚举其中的数值、计数、**具名方法、工具、软件或统计程序**、样本组、处理条件和结论限定，并逐项检查该 subject 自己的全部锚点。不得因列表较长或项目位于末尾而跳过。
3. 在一次审核中同时检查字段内部与跨字段语义；跨字段比较仍须满足同对象、同层级、同阶段、同指标和同口径。
4. `checked_locations` 必须恰好覆盖输入的全部 location，无重复、无额外 location。
5. `checked_items` 必须为每个 location 返回按原顺序排列的全部 item_index；少一个、重复或乱序都不合格。
6. `item_reviews` 必须为每个 item 恰好返回一条 `supported` 或 `violation`。只有该 item 的所有原子主张均由自己的 anchors 支持时才是 supported；任一原子主张冲突、关键遗漏或缺锚即为 violation。
7. 每条 violation 必须同时绑定 `location` 和 `item_index`；item_reviews 标 violation 当且仅当 violations 中至少有一条绑定同一 item。
8. `violations` 是首审发现的完整集合，不是抽样。
9. 只输出 JSON，不输出 markdown、解释或额外文字。

## 整批输出契约

无违规示例：

{"checked_locations":["by_field.method"],"checked_items":[{"location":"by_field.method","item_indexes":[0,1]}],"item_reviews":[{"location":"by_field.method","item_index":0,"status":"supported"},{"location":"by_field.method","item_index":1,"status":"supported"}],"violations":[]}

有违规示例：

{"checked_locations":["by_field.method"],"checked_items":[{"location":"by_field.method","item_indexes":[0,1]}],"item_reviews":[{"location":"by_field.method","item_index":0,"status":"violation"},{"location":"by_field.method","item_index":1,"status":"supported"}],"violations":[{"location":"by_field.method","item_index":0,"rule":"R4","card_quote":"Card 连续逐字子串","source_quote":"该 location 锚点原文中的连续逐字子串","evidence_relation":"direct_conflict"}]}

顶层必须且只能有 `checked_locations`、`checked_items`、`item_reviews`、`violations`。每条 violation 必须且只能有
`location`、`item_index`、`rule`、`card_quote`、`source_quote`、`evidence_relation`；不得输出 detail。
