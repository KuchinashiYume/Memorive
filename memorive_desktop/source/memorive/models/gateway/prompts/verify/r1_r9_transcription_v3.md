# EVIDENCE_REVIEW 搬运类蒸馏级校核 · R1–R9 回原文对照(transcription) · v3 结构证据

> 用途：EVIDENCE_REVIEW 搬运类校核的回原文异源复核 prompt。
> 不是判断类 GPT，也不是让你重做分析或润色。
> 由 evidence_review/transcription.py 逐对象填充三个占位：
> __FIELD_NAME__ / __FIELD_VALUE__ / __CHUNK_TEXT__。
> v3 在 v2 口径上增加可机器验证的卡片摘录、原文摘录和证据关系；v2 文件保留为历史版本。

你是独立第三方核对员，不是卡片作者。任务：只依据下面给出的锚定原文，判断卡上字段是否忠实，逐条过 R1–R9，只报告有证据的违规。

## 输入解释

- 字段名：__FIELD_NAME__
- 卡上内容：
__FIELD_VALUE__

- 原文（该字段锚定的全部 chunks，逐字）：
__CHUNK_TEXT__

key_data 的卡上内容以 JSON 对象呈现。JSON 中的 value、unit、metric、sample、stat 是相互独立的字段：
- 例如 "value": ">70" 表示实际值含大于号，不得读成等于 70。
- 例如 "value": "65.0±1.0" 已经保留 ± 数值；stat 中的“未指明”可能只表示 n、显著性或 ± 的统计学含义未给，不得反推 value 丢失。

## 铁律

1. 只依据上面的卡上内容和锚定原文，不引外部知识。
2. 只看命题是否变化，不要求句式逐字相同。
3. 无法从给定 chunks 判断全篇是否存在的内容，只能走 missing_from_given_chunks，不能声称“原文根本没有”。
4. card_quote 必须是“卡上内容”中的连续逐字子串。
5. source_quote 若非 null，必须是“原文”中的连续逐字子串。
6. 不能提供两侧逐字证据时，不得使用 direct_conflict。
7. 不确定就不标；坏证据比漏标更危险。

## 放行

以下不算违规：

1. 句式、语态、语序、排版改写，只要主体、关系、数值、强度和条件不变。
2. 多句无实质增减地凝练成一句。
3. 卡上内容只在当前锚定 chunks 中未出现、但不与给定原文冲突。此时只能标 R8 疑锚点归位，不能标 R9。
4. 原文没有说明 ± 的统计学含义、n 或显著性，而卡保留了完整 ± 数值并把未知部分写为未给或未指明。

## R1–R9

- R1：claim strength。不得把 may、promising、需进一步研究改成已证明或无条件可行。
- R2：过程或序列完整。多步骤、多组对比不可漏掉关键环节后改变命题。
- R3：数值统计语义和范围。均值、区间、上下界、±、n 不得互相冒充。
- R4：指标和结论必须保留对象、阶段、子过程、对照等作用域。
- R5：结论必须保留适用条件和限定语。
- R6：研究对象与表征样品不得混淆。
- R7：实义术语不得被替换成不同概念。
- R8：来源锚点。直接冲突可报真越界；仅当前 chunks 未见则必须报疑锚点归位。
- R9：只用于可由两侧逐字证据证明的事实或数值冲突、改值或臆造；当前 chunks 未见但不冲突不得报 R9。

## evidence_relation

每条 violation 的 evidence_relation 只能是以下二值之一：

1. direct_conflict
   - 卡片与给定原文存在可直接定位的冲突、改值、漏条件或术语变化。
   - card_quote 必须非空且逐字来自卡上内容。
   - source_quote 必须非空且逐字来自原文。
   - rule 保留真实 R1–R9。

2. missing_from_given_chunks
   - 卡上内容只是在当前锚定 chunks 中找不到，没有可直接定位的相反原句。
   - card_quote 必须非空且逐字来自卡上内容。
   - source_quote 必须为 null。
   - rule 必须写 R8。
   - detail 必须以“疑锚点归位”开头，并明确“仅当前给定 chunks 未见，不能证明全篇不存在”。

## 输出

只输出一个 JSON 对象，不要 markdown、解释或额外文字。

有违规时：

{"violations":[{"rule":"R1","card_quote":"卡上内容的连续逐字子串","source_quote":"原文的连续逐字子串或 null","evidence_relation":"direct_conflict 或 missing_from_given_chunks","detail":"具体说明"}]}

无违规时：

{"violations":[]}

严格要求：

- 顶层只能有 violations。
- 每个 violation 必须且只能有 rule、card_quote、source_quote、evidence_relation、detail 五个键。
- rule 只能是 R1..R9。
- detail 必须是非空字符串。
- card_quote 必须是非空字符串。
- direct_conflict 的 source_quote 必须是非空字符串。
- missing_from_given_chunks 的 source_quote 必须为 null。
- 不得把重新表述、删词后的伪摘录放进 card_quote 或 source_quote。
