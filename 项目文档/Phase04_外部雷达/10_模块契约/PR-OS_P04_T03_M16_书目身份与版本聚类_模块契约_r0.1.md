# P04/T03/M16 书目身份与版本聚类模块契约

## 1. 权威边界

- T03 只消费 T01 共同 envelope/五状态轴和 T02 冻结 `SourceObservation` / `SourceSnapshot`；不修改任何上游字节。
- 唯一 writer：`M16.IDENTITY_RESOLVER`；`CandidateRecord` 投影 writer 仍为 T01 已登记的 `M16.CANDIDATE_REGISTRY`。
- 消费者仅为 `M16.CANDIDATE_REGISTRY` 与 `P04/T04/CROSS`。T03 不生成 Candidate Card、Card、Analysis，不写 M13、正式库、索引、指针或生产状态。
- 所有稳定 ID 与 `content_hash` 使用 T02 `canonical_json` / SHA-256 语义；对象 `content_hash` 以大写十六进制表达以兼容 T01 envelope。

## 2. 精确标识符规范化

| kind | 合法输入 | canonical | 非法处理 |
|---|---|---|---|
| DOI | T02 `normalize_doi` 接受的 DOI、`doi:` 或 `http(s)://doi.org/` 写法 | 小写 DOI，不含 prefix | `INVALID_DOI`，不得补造 |
| arXiv | `YYMM.NNNN[vN]` / `YYMM.NNNNN[vN]` 及 arXiv URL 尾段 | base id + 显式整数 version；无 version 时为 `null` | `INVALID_ARXIV_ID` |
| PMID | ASCII 数字、`PMID:` 或精确 PubMed URL；原始数字最多 12 位、去前导零后 1—9 位且不为 0 | 无前导零 ASCII 数字 | `INVALID_PMID` |

规范化成功只证明字符串可规范化，不证明文献存在。只有内容 hash、snapshot hash、normalized-record hash 均与冻结 evidence registry 一致的可信 `SourceObservation` 才提供 `TRUSTED_FROZEN_SOURCE_OBSERVATION` 存在性证据。

## 3. 自动合并与版本关系

1. 无冲突时，相同 canonical DOI、PMID 或相同 arXiv base+version 可以合并为一个 `Manifestation`，只聚合 observation refs，不改变 observation。
2. 同一 arXiv base 的不同显式版本属于同一 `WorkCluster` 的不同 `Manifestation`，建立有向 `ARXIV_VERSION_SUCCESSOR` 边。
3. 预印本、会议版、AAM、VOR 只有在输入含冻结的显式 version evidence、relation id 与稳定 work anchor 时才连接；各 manifestation 永不覆盖。
4. 标题、作者、年份或摘要相同/相似只可产生 `POSSIBLE_DUPLICATE_TITLE_ONLY` review item，绝不触发 union。
5. 冲突 observation 不自动合并；每个 claim 单独保存并标为 `CONFLICT`。

## 4. 冲突与复核原因码

- `EXACT_IDENTIFIER_METADATA_CONFLICT`：相同精确 identifier 的题名、作者集合或发表年份互斥。
- `CROSS_IDENTIFIER_CONFLICT`：相同 arXiv manifestation/PMID 指向多个 DOI，或同 DOI 指向多个 PMID。
- `UPSTREAM_IDENTITY_CONFLICT`：T02 已把 observation 标为 `CONFLICT`。
- `INVALID_IDENTIFIER`：输入 identifier 非法且不能建立已核验 identity。
- `VERSION_EVIDENCE_CONFLICT`：显式版本边端点、anchor 或方向不闭合。
- `POSSIBLE_DUPLICATE_TITLE_ONLY`：只有书目文本证据的近似/碰撞；保持分离。

所有冲突和 possible duplicate 均生成稳定、`OPEN`、无外部副作用的 review item；禁止动作固定含 `AUTO_MERGE`、`AUTO_SELECT`、`CANDIDATE_CARD_ELIGIBILITY`。

## 5. CandidateRecord 身份投影

- T03 projection 复用 T01 envelope、五状态轴、producer/single writer 和 canonical hash；扩展字段只保存 identity/cluster/manifestation/evidence/review refs。
- T02 小写 `source_observation_<hex>` 通过一一映射的 T01 alias `SOURCE_OBSERVATION:<HEX>` 进入基础 `source_observation_refs`；`identity_evidence_refs` 同时保留原始精确 T02 object id，不改变上游身份。
- 只有无冲突、具可信精确 evidence 的 `VERIFIED` cluster 才可令 `t04_input_eligible=true`。
- T03 对所有 projection 固定 `candidate_card_eligible=false`、`candidate_card_status=NOT_REQUESTED`、`promotion_status=NOT_ELIGIBLE`。
- 无可信 `SourceObservation` 的 title/author/year 或 `AI_HINT` 不生成 CandidateRecord；只输出 `HINT_UNVERIFIED` decision，避免伪造 parent/source refs。

## 6. A0 硬判据

- 单一 run 的 mandatory checks 全部通过，failed/errors/skipped=`0/0/0`。
- 固定正向组 false split=`0`；固定负向对自动 false merge=`0`。
- 输入顺序与重复 replay 的 canonical fingerprint 相同。
- observation 数量守恒；版本连接不覆盖 manifestation；冲突与 queue 数量可回指。
- network/API/model/fulltext/M13/formal roots/Git/production writes=`0`。
