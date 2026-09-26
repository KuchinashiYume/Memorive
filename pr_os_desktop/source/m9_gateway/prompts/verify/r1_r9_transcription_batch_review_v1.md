# M6 搬运类校核 · 整批最终复查 v1 · M6_BATCH_FINAL_REVIEW

这是同一批字段的第二次、也是最后一次模型复查。你必须重新扫描全部 subjects 和 sources；
首审结果只是待复核记录，不是必须保留的答案。删除首审误报、补上首审漏报，并返回复查后的
完整最终集合，不能只返回相对首审的增量。

__POLICY_TEXT__

## 整批输入

__BATCH_INPUT_JSON__

## 首审结果

__INITIAL_RESULT_JSON__

## 最终复查要求

1. 必须逐一复查全部 location，尤其复核数值、计数、否定、对象、口径、锚点范围及跨字段一致性。
2. 对每个 subject 必须按 `card_items` **逐个 item_index** 重新审核；每项再枚举其中的数值、计数、**具名方法、工具、软件或统计程序**、样本组、处理条件和结论限定，逐项确认它们是否由该 subject 自己的 anchors 支持。不得因项目位于长列表末尾而跳过。
3. 首审中的每条 violation 都要重新确认双侧逐字证据与作用域；不成立就从最终集合删除。
4. 首审未报告不等于无问题；重新扫描后可补充遗漏。
5. `checked_locations` 必须恰好覆盖输入全部 location，无重复、无额外 location。
6. `checked_items` 必须为每个 location 返回按原顺序排列的全部 item_index；少一个、重复或乱序都不合格。
7. `item_reviews` 必须为每个 item 恰好返回一条 `supported` 或 `violation`。只有该 item 的所有原子主张均由自己的 anchors 支持时才是 supported；任一原子主张冲突、关键遗漏或缺锚即为 violation。
8. 每条 violation 必须同时绑定 `location` 和 `item_index`；item_reviews 标 violation 当且仅当 violations 中至少有一条绑定同一 item。
9. 最终 `violations` 必须是全量最终结论。
10. 只输出 JSON，不输出 markdown、解释或额外文字。

## 最终输出契约

顶层必须且只能有 `checked_locations`、`checked_items`、`item_reviews`、`violations`。每条 violation 必须且只能有
`location`、`item_index`、`rule`、`card_quote`、`source_quote`、`evidence_relation`；不得输出 detail。

无违规示例：

{"checked_locations":["by_field.method"],"checked_items":[{"location":"by_field.method","item_indexes":[0,1]}],"item_reviews":[{"location":"by_field.method","item_index":0,"status":"supported"},{"location":"by_field.method","item_index":1,"status":"supported"}],"violations":[]}
