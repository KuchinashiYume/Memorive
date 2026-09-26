---
document_id: Memorive-DISCOVERY-CHANNELS-CROSS-MULTICHANNEL-SLATE-CONTRACT
revision: r0.1
status: accepted-production-disabled
phase: Discovery
task: Intake
module_owner: CROSS
affected_modules: [LITERATURE_DISCOVERY, RESEARCH_RECOMMENDATIONS]
gate_profile: Discovery_EXTERNAL_SOURCE_GATED_ACTIVATION_V1
source_commit: 3a13954e574b7501368e3fb4125be06952a9554b
source_run_id: DISCOVERY_CHANNELS_CROSS_20260811_102625_candidate001
accepted_candidate: candidate003
human_gate_b: ACCEPTED
production_activation: false
---

# DISCOVERY-CHANNELS 多通道发现、有限 Slate 与方向极化防护模块契约

> 本文件由 Human Gate B 已接受的 candidate003 受控升格而来，是 DISCOVERY-CHANNELS 的正式公开安全离线基线；它不是部署授权、真实来源授权、生产配置或 controlled activation 授权。

## 1. 权威承接与 writer 边界

- Initialization 的 `DirectionDefinition / ResearchInterestProfile / ResearchExposurePolicy / HorizonAnchor / SessionResearchContext` 以及 `DiscoveryPlan / DiscoveryRun / RecommendationSlate` object identity 保持不变；Intake 只增加 task-level detail。
- ServiceContracts 是 `WorkCluster / Manifestation / CandidateRecordIdentityProjection` 的唯一身份事实 writer；Intake 不合并、拆分或改写 work identity。
- Configuration 是 `LibraryRelation / NoveltyEvidence / CandidateComparison` 的唯一关系与新颖性事实 writer；Intake 不重算这些事实。
- LITERATURE_DISCOVERY 只写 `DiscoveryCandidateFact` 投影；RESEARCH_RECOMMENDATIONS 只写通道候选、批次 `CandidateCluster`、`RecommendationSlateDetail` 与覆盖/极化报告。
- Profile、Policy、Direction、正式库、ARTIFACT_REGISTRY、Card、Analysis、promotion、production state 的写入计数固定为 0。

## 2. 冻结研究上下文

- `ResearchInterestProfile` 只记录用户显式长期兴趣；禁止行为直接写入。
- `ResearchExposurePolicy` 单独记录批次、通道目标、留白、集中度上限与 no-backfill；其字段不得进入 Profile。
- `HorizonAnchor` 必须 `independent_of_local_similarity=true`，且不得来自 click/dwell/question 等行为。
- `SessionResearchContext` 只服务当前计划，`long_term_profile_mutation=false`、`long_term_policy_mutation=false`。
- 已接受基线只使用 public-safe synthetic/test-local 快照，不读取真实 Registry。

## 3. 五通道资格

| 通道 | 最小资格 | 禁止替代 |
|---|---|---|
| `CORE` | Profile 中冻结方向的 `EXACT` 匹配 | 行为热度 |
| `ADJACENT` | 明确 `RELATED` 方向证据或 Configuration 相邻关系证据 | CORE 剩余项 |
| `BRIDGE` | 至少两个明确方向引用，且有 bridge evidence | 单方向相似 |
| `HORIZON` | 冻结 HorizonAnchor 引用 | 本地近邻、点击或当前会话偏好 |
| `COVERAGE_REPAIR` | 冻结 coverage gap 引用 | CORE 别名 |

primary-channel 优先级固定为：`HORIZON > COVERAGE_REPAIR > BRIDGE > CORE > ADJACENT`。这只解决多通道命中归属，不构成跨通道总分。

## 4. 通道内排序

通道内比较采用字典序分量，不计算或输出全局 scalar score：

1. direction/channel evidence strength；
2. evidence completeness；
3. Configuration `library_new` 状态；
4. timeliness；
5. access posture；
6. canonical identity 升序作为最终 tie-break。

输入枚举顺序、wall clock、随机数、行为事件不得进入比较键。

## 5. CandidateCluster 与代表项

- 聚类键只能是 ServiceContracts `work_cluster_id`；`POSSIBLE_DUPLICATE/UNRESOLVED` 不自动并入其他 work。
- 代表项比较顺序固定为 identity verified、evidence completeness、manifestation fitness、access posture、canonical identity。
- 同一 WorkCluster 在一个 Slate 中最多一项；被抑制 manifestation 必须记录 `NON_REPRESENTATIVE_MANIFESTATION`。
- CandidateCluster 不产生新的身份事实。

## 6. 有限 Slate

test-local baseline：`batch_size=10`，目标为 `CORE=3 / ADJACENT=2 / BRIDGE=1 / HORIZON=1 / COVERAGE_REPAIR=1`，保留 `2` 个 whitespace slots。

集中度上限固定为：author=`2`、institution=`2`、journal=`2`、source=`2`、primary direction=`4`。检查顺序固定为 author、institution、journal、source、direction；之后才判断通道目标是否已满。

- 不允许跨通道借位或 CORE 静默回填。
- 具备资格的 HORIZON/COVERAGE_REPAIR 保留位不得被行为或 CORE 侵占。
- 每个选中和未选中候选必须有确定性 reason codes 与 evidence refs。
- 未使用目标位输出 `CHANNEL_UNDERFILLED`；批次剩余留白输出 `POLICY_WHITESPACE_RESERVED`。

## 7. 30/90 日覆盖与极化

- cutoff 从冻结 `as_of` 和窗口天数计算，不读 wall clock。
- denominator 是窗口内可验证 exposure 事件数；history coverage 不完整时整个窗口为 `UNKNOWN`，count/share 不得伪装为 0。
- 报告记录 planned current-slate count、actual exposure count/share、channel count、gap 和集中度警示。
- 报告只输出 warning；proposal boundary 固定为 `Verification_ONLY_NO_MUTATION`，不修改 Profile/Policy。

## 8. 行为与生产越权防护

- 非空 behavior events 输入必须 fail closed：`BEHAVIOR_INPUT_NOT_ACCEPTED_IN_Intake`。
- production write target 必须 fail closed：`PRODUCTION_WRITE_FORBIDDEN_Intake`。
- 拒绝前后 Profile、Policy、DirectionRegistry、Plan 与已冻结 Slate hash 必须相同。
- Intake 不实现 ExposureLedger、suppression、cooldown、resurfacing、behavior proposal writer 或 controlled activation。

## 9. 硬失败

以下任一项使离线验证失败：全局单分、CORE 回填、重复 WorkCluster、cap 绕过、理由缺失、HORIZON/coverage 来源不独立、UNKNOWN 被写成 0、输入乱序改变 bytes、行为改变长期状态、任何网络/模型/API/正式库/ARTIFACT_REGISTRY/生产写入。

## 10. 生命周期边界

- candidate003 的唯一一次纯离线 `SHADOWB_run_001` 已完成并通过；冻结清单文件 SHA-256 为 `53D7F06B12026D61E94B94F30AB36AA6AE398D01E28B7D6BCED5F4693349BB2B`，payload aggregate 为 `5C65FE86E612D20EBE2C3EC2860F9937D993C603D5CA779841434162207F8B33`。
- Human Gate B 已接受当前候选，Intake 终态为 `completed/PASS/PASS`；该状态只覆盖验收时展示并接受的公开安全 exact-set 及正式化所需的状态盖章。
- 正式化不重跑、覆盖或复用 Shadow B；既有 `ERROR`、`FAIL`、successor、freeze 和消费证据保持不可变可追溯。
- 当前正式基线保持 `production_activation=false`；真实 Profile/Policy、真实来源、ARTIFACT_REGISTRY、Card/Analysis、Verification、部署和任何生产指针均未获授权。
