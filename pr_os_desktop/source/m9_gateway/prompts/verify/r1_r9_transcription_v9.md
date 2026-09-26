# M6 搬运类校核 · v9 缺失与冲突关系增量

> 本文件追加在不可变 v6、v7、v8 之后。v9 只收紧
> `direct_conflict` 与 `missing_from_given_chunks` 的边界，不放宽逐字证据、
> 同论文锚点、JSON 或 fail-closed 契约。

## direct_conflict 必须有明确相反命题

`direct_conflict` 不仅要求措辞有差异，还要求 `source_quote` **明确给出相反命题**：
同一对象、指标、阶段和条件下，数值不同、方向相反、肯否相反，或对象/单位被原文明示为另一项，
使两边不能同时为真。输出前必须指出原文中的哪一部分是“相反事实”；指出不出来就不得报冲突。

以下情况一律不是 `direct_conflict`：

1. **原文沉默或信息更少**：原文没有提 Card 的某个修饰语、步骤或推论，或者只陈述其中一部分。未提不等于否定；若给定 chunks 无法支持，应使用 `missing_from_given_chunks`。
2. 原文摘录与 Card 的数值、方向、对象和作用域一致，只是原文更长、Card 更压缩，或 Card 把同一段事实合成一句。这样的 `source_quote` 是支持证据，不得作为冲突证据。
3. 原文只支持 **复合 card_quote** 的一部分。先把 `card_quote` 缩到最小的待核事实；若剩余事实没有明确相反原文，只能报 `missing_from_given_chunks`，不得拿支持其他子句的原文伪造“双侧冲突”。
4. 两个命题可以同时为真，即使一个说抗生素、另一个说耐药基因，或一个只说截留机制、另一个说结果；这首先是证据是否覆盖的问题，不是逻辑冲突。只有原文明示 Card 对象/结果为错时才可 `direct_conflict`。

## 输出前关系自检

- `source_quote` 是否明确包含与 Card 相反的数字、方向、否定、对象或条件？否 → 不得 `direct_conflict`。
- 差异是否只是原文沉默、信息更少或复合句部分支持？是 → 无支持时用 `missing_from_given_chunks`。
- `source_quote` 是否实际上重复并支持 Card 的主要事实？是 → 不输出该 violation。
