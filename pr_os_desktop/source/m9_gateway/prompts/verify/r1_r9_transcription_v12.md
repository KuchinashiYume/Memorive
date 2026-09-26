# M6 搬运类校核 · v12 关系精度增量

> 本文件只追加在完整、无矛盾的 v10 基线后。它不改变四字段 JSON、
> 逐字证据或 fail-closed，只限制 omission 的适用字段并统一计数口径。

## explicit_omission 仅用于结构化 key_data

- 只有当 `__FIELD_NAME__` **严格等于** `key_data` 时，才允许输出 `explicit_omission`；此时仍须满足：source_quote 明确包含 Card 同一条结构化记录遗漏的关键条件、对象、作用域或统计限定。
- 对 research_object、method、boundary_conditions、key_results、author_conclusion 等叙述字段，**禁止输出 `explicit_omission`**。Card 命题在给定 chunks 中没有支持且原文没有相反命题时，只能用 R8 `missing_from_given_chunks`；原文明示相反命题时才用 `direct_conflict`。
- 同一科学命题的同义改写、主动/被动转换、全称/简称、列表压缩或语序变化都不是遗漏；先判支持，再判违规。

## 计数冲突必须同对象、同口径

- 输出计数冲突前，必须先确认 Card 数字与 source 数字统计的是**同一对象、同一层级、同一口径**。总数只能与总数比较，单组数量只能与同一单组数量比较。
- 当一侧给总数、另一侧给分组构成时，必须先做确定性的分组求和或展开。例如 `two controls` 加 `treatment digesters in triplicate` 等于五台；这与 `five reactors` 相互支持，不得报冲突。
- 只有同口径计数明确不兼容时才是 `direct_conflict`，例如同一批 0.5 L 连续搅拌反应器一处明确写 five、另一处明确写 eight。
- 不得把总数与其中一个子组、实验重复数与装置总数、阶段数与样本数交叉比较。
