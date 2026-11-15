---
document_id: Memorive-NOVELTY-ASSESSMENT-LITERATURE_DISCOVERY-LOCAL-RELATION-NOVELTY-CONTRACT
phase_owner: Discovery
task_owner: Configuration
module_owner: LITERATURE_DISCOVERY
document_type: module-contract
authority: formal-record
lifecycle: active-contract
revision: r0.1
schema_revision: discovery.configuration.0.1
formalization_status: FORMALIZED_AFTER_HUMAN_GATE_B
accepted_at: 2026-08-11T09:35:28.8229491+09:00
user_acceptance_sha256: 6BC7D0CAA66661449F534086AFC68D5E43A54369B422DE271C414E66D092AE4F
---

# NOVELTY-ASSESSMENT/LITERATURE_DISCOVERY 本地关系与多维新颖性合同

## 1. 边界

本合同只定义公开安全合成快照上的纯离线、query-only 比较。它不读取或修改正式文献库、Chroma、ARTIFACT_REGISTRY、Card/Analysis、route、profile、scheduler、pointer 或知识状态；不调用网络、Embedding、reranker、LLM 或其他 API。用户已在 Human Gate B 接受 candidate001，本合同据此成为正式仓库 authority，但不扩张任何生产或真实数据授权。

`LocalLibraryReadSnapshot` 是 Configuration 的正式合同类型；本 run 中的验证实例仍只由冻结 fixture seed 确定性物化并以 `content_hash` 绑定，不表示真实库快照已获授权，也不要求在 H0 与 A0 之间新增人工门。

## 2. LocalLibraryReadSnapshot

快照必须同时冻结：

- 唯一 `snapshot_id`、cutoff 与显式 `allowed_paper_ids`；
- identity、first-seen、citation、direction 和各向量维度的 coverage；
- active-only 本地 `paper_id`、work/manifestation/identifier、题录与字段；
- 预计算向量、profile、metric、维度、阈值和排序方向；
- 显式 citation edge 与 provenance；
- direction snapshot；
- 每个成员及整体 `content_hash`。

只接受非空、无重复、完全落在快照内的 scope。`COSINE_SIMILARITY` 只能使用 `GTE`，`EUCLIDEAN_DISTANCE` 只能使用 `LTE`；禁止 `authority_weight`、`final_ranking_score`、`add`、`upsert`、`update`、`delete` 或 `register`。

## 3. LibraryRelation

身份轴每个 candidate/local pair 最多一个结果，优先级为：

1. `EXACT_SAME_WORK`：ServiceContracts VERIFIED work/manifestation 或精确 identifier 证据；
2. `ALTERNATE_VERSION`：同一 VERIFIED WorkCluster、不同 Manifestation 且有显式 version evidence；
3. `POSSIBLE_DUPLICATE`：题名/作者/年份或相似证据仅构成待审提示，不成为身份事实。

以下关系与身份轴正交，可并存：`DIRECT_CITATION_RELATION`、`TOPIC_SIMILAR`、`METHOD_SIMILAR`、`OBJECT_SIMILAR`、`BRIDGE_BETWEEN_DIRECTIONS`、`NEW_TO_LIBRARY`。

每条关系必须带 candidate、具体本地 `paper_id` 与 title、证据方法/refs、相似维度、差异维度、evidence level、scope 和算法 revision。`NEW_TO_LIBRARY` 由完整 identity coverage 上的无 exact/version/possible 结论产生，并锚定 scope 中排序最前的本地比较对象；该锚点只是 coverage 证据，不表示两者相似。

显式 citation edge 是引用关系的唯一来源。自然语言、题名、摘要和向量不得生成引用关系。

## 4. NoveltyEvidence

四轴独立输出 `TRUE | FALSE | UNKNOWN`：

- `publication_new`：只比较冻结 publication date 与 cutoff；
- `first_seen_new`：只消费 coverage 完整的 seen-candidate snapshot；
- `library_new`：exact/version 为 `FALSE`；possible/conflict/coverage 不完整为 `UNKNOWN`；完整覆盖且无相关身份项为 `TRUE`；
- `direction_new`：两个以上已有方向的合格分维度证据形成 bridge 时为 `TRUE`；单一已有方向匹配为 `FALSE`；完整方向覆盖下无已有方向匹配可形成 `TRUE`。题录/摘要范围一律标记 `ABSTRACT_LEVEL_ESTIMATE`。

`UNKNOWN` 不得转换为 TRUE，也不得生成 `NEW_TO_LIBRARY` 或 Candidate Card 资格。

## 5. CandidateComparison

`CandidateComparison` 是独立 projection，原样保留 Initialization/ServiceContracts 的 `identity_status`、`access_status`、`decision_status`、`candidate_card_status` 和 `promotion_status`。它只附加 relation、novelty、unknown/conflict queue、scope receipt 和零副作用计数；single writer 仍为 `LITERATURE_DISCOVERY.CANDIDATE_REGISTRY`。

## 6. 确定性与失败语义

- 输入、成员和 scope 顺序不得改变语义结果或 replay fingerprint。
- 任一 hash、schema、profile、dimension、metric、scope 或 coverage 失配必须 fail closed。
- 输入不被就地修改；实现不读取 wall clock、随机数、环境变量、凭据或网络。
- runtime/结果中 external source/API/model/tokens/cost、production library、Chroma、ARTIFACT_REGISTRY、Card/Analysis 和 formal write 均为 0。
- A0 作者侧回归只能登记 `completed/PASS/NOT_ASSESSED`；不声称 independent、blind 或 double-blind。
