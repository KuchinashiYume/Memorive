---
document_id: Memorive-RECOMMENDATION-HISTORY-RESEARCH_RECOMMENDATIONS-EXPOSURE-FEEDBACK-WEAK-SIGNAL-CONTRACT
phase_owner: Discovery
task_owner: Verification
module_owner: RESEARCH_RECOMMENDATIONS
document_type: module-contract
authority: formal-record
lifecycle: accepted-production-disabled
revision: r0.1
source_run_id: RECOMMENDATION_HISTORY_RESEARCH_RECOMMENDATIONS_20260811_132238_candidate003
supersedes_run_id: RECOMMENDATION_HISTORY_RESEARCH_RECOMMENDATIONS_20260811_131159_candidate002
accepted_candidate: candidate003
human_gate_b: ACCEPTED
gate_profile: Discovery_EXTERNAL_SOURCE_GATED_ACTIVATION_V1
gate_rule_revision: r2.5
operation_manual_revision: r0.1
research_interaction_mode: NOT_CONFIGURED
production_activation: false
formalization_status: ACCEPTED_PRODUCTION_DISABLED
created_date: 2026-08-11
---

# RECOMMENDATION-HISTORY/RESEARCH_RECOMMENDATIONS · 反馈、曝光账本与弱信号模块契约 r0.1

## 1. 身份与边界

本模块契约由 run `RECOMMENDATION_HISTORY_RESEARCH_RECOMMENDATIONS_20260811_132238_candidate003` 的 candidate003 受控升格而来。其唯一一次纯离线 `SHADOWB_run_001` 已 28/28 PASS，并经用户 Human Gate B 接受。source 保持为 `9d55b17da8d8d27f67965391ddc14eacd0e27618` / tree `094896bddfbfba42d19aae9f6219826284725921`；authority inventory 为 43 项、aggregate SHA-256 `BCD33C57F03C22855F4BAD7B7FE96AACB0EDCA84DB1C6CB9219281BA5F1C0219`。

本任务已经在中央治理 successor r2.6/r1.3/r1.6/r1.8 出现前启动。依启动 manifest 冻结条款，本 run 继续使用 r2.5/r1.2/r1.5/r1.7；并发 main 不导入本候选 source、判据或授权。

已接受的正式范围只包括公开安全的离线合同、Schema、实现、合成 fixture 及其不可变证据链。真实 Research/Profile/Policy 读写、外部来源/network/model/API、A1、formal library、ARTIFACT_REGISTRY、controlled activation、部署、生产指针和 cleanup 仍未获授权。

## 2. 上游只读绑定

| 事实 | 精确绑定 | Verification 使用方式 |
|---|---|---|
| Slate 方向 | `RecommendationSlateDetail.selected[*].primary_direction_id` | 直接作为 `direction_id`；不猜配、不重算 |
| 方向 revision | 同一 `ResearchContextDetail.directions[*].revision` | 与 Slate `context_hash` 同源绑定 |
| work 身份 | `selected[*].work_cluster_id` | 完全相同 ID；标题/文本/向量不得代替 |
| channel/rank/caps | Intake accepted Slate | 只读；Verification 不改变资格、配额、caps 或 no-backfill |
| 版本/访问/关系 | ServiceContracts/Configuration 正式事实 | 只消费 authority evidence ref；不重算上游事实 |
| Research 交互 | 正式跨阶段合同缺失 | `NOT_CONFIGURED`；真实 adapter read 与真实 digest/boost 固定为 0 |

Intake Slate/context 必须通过 seal、schema version、`context_hash`、selected 唯一性和方向 revision 校验。任何缺失、重复或 hash 漂移都 fail closed。

## 3. 合同对象

| 对象 | 身份/用途 | 不变量 |
|---|---|---|
| `FeedbackTaxonomy` | 8 类明确反馈及唯一路由 | no-click/timeout/no-decision 不产生负反馈；`field_id` 不替代方向 |
| `ExposureLedger` | append-only 曝光与控制事件流 | 唯一 writer；同 event 幂等；碰撞失败；canonical replay 字节一致 |
| `ScopedSuppressionRecord` | `work_cluster_id + direction_id + decision_revision` | 仅 `NOT_FOR_THIS_DIRECTION` 创建；保存 `direction_revision` |
| `ResurfacePolicy` | 冷却与允许/禁止 trigger | 必须携带 authority evidence 和旧决定披露；历史不覆盖 |
| `ResearchInteractionEvidence` | Discovery 消费侧最小中性投影 | 只含 hash/中性字段；当前仅 synthetic guard |
| `BehavioralInterestDigest` | 去重、折扣、衰减、饱和后的方向摘要 | 无全局 scalar；真实读数/真实 boost 为 0 |
| `ExposurePolicyChangeProposal` | 长期证据达到阈值后的惰性提案 | `PENDING_HUMAN_CONFIRMATION`、`applied=false`、Profile/Policy writes=0 |

所有正式对象使用 UTF-8、键序 canonical JSON：`sort_keys=true`、分隔符 `(',', ':')`、`allow_nan=false`；`content_hash=SHA256(canonical JSON without content_hash)`。

## 4. FeedbackTaxonomy 与路由

| reason | 路由 | 作用域/确认 |
|---|---|---|
| `NOT_FOR_THIS_DIRECTION` | `SCOPED_DIRECTION_SUPPRESSION` | exact work × direction × decision revision |
| `GLOBALLY_USELESS` | `GLOBAL_EXCLUSION` | 二次显式确认前只返回 `GLOBAL_CONFIRMATION_REQUIRED` |
| `ALREADY_READ` / `IN_LIBRARY` | `GLOBAL_DEDUPE` | 作品级去重，不惩罚方向 |
| `SNOOZE` | `TIME_BOUND_SNOOZE` | `cooldown_until` 前暂停；到期后恢复评估 |
| `NO_LEGAL_FULLTEXT` | `ACCESS_CHANGE_WAIT` | 仅合法访问状态发生正式变化后重新浮现 |
| `CARD_ERROR` | `CARD_REPAIR` | Card 修复，不产生兴趣负信号 |
| `MANIFESTATION_NOT_USEFUL` | `MANIFESTATION_SCOPE_SUPPRESSION` | 仅当前 manifestation；重大正式版本可重评 |
| `MANUAL_RESTORE` | append-only control event | 撤销当前 scoped 影响，不删除旧事件 |

`NO_CLICK`、`TIMEOUT`、`NO_DECISION` 返回无事件。所有路由的 `negative_interest_signal=false`。

## 5. ExposureLedger

### 5.1 曝光事实

每条 `EXPOSURE_RECORDED` 必须绑定 `exposure_id/event_id/slate_id/slate_revision_or_hash/position/candidate_id/work_cluster_id/channel/direction_id/direction_revision/shown_at/source_contract_hash/evidence_refs`。`exposure_id` 和基础曝光事实写入后不可修改。

### 5.2 追加控制

`DECISION_RECORDED`、`DECISION_REVOKED`、`FEEDBACK_RESET` 和 `MANUAL_RESTORE` 都是唯一 `event_id` 的追加事件。相同 `event_id` + 相同 canonical bytes 只计作 idempotent replay；相同 ID + 不同 bytes 为 `EVENT_ID_COLLISION`。同一 exposure 的 decision revision 不得冲突。

Current projection 由事件流按 `event_time + exposure_id + event_type priority + revision + event_id` 重建。revoke/reset/restore 只撤销当前影响；`history_event_count` 不减少。

## 6. 精确方向抑制

1. 自动方向抑制只来自显式 `NOT_FOR_THIS_DIRECTION`。
2. exact same `work_cluster_id + direction_id + direction_revision` 返回 `SUPPRESSED_EXACT_WORK_DIRECTION`。
3. 相同 work、不同 direction 不继承抑制；冻结跨方向 cooldown 到期后返回 `OTHER_DIRECTION_NOT_SUPPRESSED`，并披露旧决定。
4. direction split、merge、major revision 或 revision mismatch 返回 `PENDING_DIRECTION_REMAP`；不静默继承。
5. 文本、embedding、近邻分类、标题或 Research `field_id` 的传播固定为 0。
6. 已确认 global、dedupe、snooze、access wait、Card repair 和 manifestation scope 各走独立路由，不冒充方向负信号。

## 7. ResurfacePolicy

允许 trigger 仅为：`FORMAL_MAJOR_VERSION`、`LEGAL_ACCESS_AVAILABLE`、`CORRECTION_OR_RETRACTION`、`IMPORTANT_CITATION_CHANGE`、`NEW_STABLE_DIRECTION`、`BRIDGE_ROLE_CHANGE`、`MANUAL_RESTORE`。每次均需非空 authority evidence ref、旧决定披露和新理由。

`TITLE_DRIFT`、`ABSTRACT_FORMAT_DRIFT`、`SOURCE_MIRROR_CHANGE`、`MINOR_METADATA_DRIFT` 永不触发。重大版本必须证明 `major_change=true` 且 manifestation 改变；合法全文必须证明 access 从不可用态进入 `RIGHTS_VERIFIED|MATERIALIZED`；人工恢复必须引用追加 restore event。

除 correction/retraction 和 manual restore 外，冻结 cooldown 到期前仍阻止重新浮现。

## 8. Research 模式与弱行为参数

当前 `Research_INTERACTION_MODE=NOT_CONFIGURED`。`REAL_ADAPTER` 输入必须以 `Research_REAL_ADAPTER_NOT_CONFIGURED` 阻断；合成 guard 可验证未来消费边界，但 `real_adapter_read_count=0`、`real_digest_or_boost_count=0`。

最小输入字段是 `query_hash/event_time/session_hash/field_dimension/cited_work_id/completion_state/origin/optional exposure_id`。原始 query、用户身份、private payload、完整会话、全文和 secret 字段 fail closed。`field_dimension` 不能直接映射 `direction_id`；测试映射必须是独立的 `EXACT_SYNTHETIC` binding。

冻结参数：

- 去重：同 `direction_id + session_hash` 只保留权重最高的一项；重复/改写不重复放大。
- origin 权重：`USER_INITIATED=1.0`、`Discovery_EXPOSURE_FOLLOWUP=0.25`、`SYSTEM_CLARIFICATION=0`、`RETRY_OR_REPHRASE=0`；Discovery follow-up 必须有 exposure ref。
- 衰减：age `0—30/31—90/91—180/>180` 天分别为 `1/0.5/0.25/0`。
- 每方向上限：`1.0`；不形成全局 scalar score。
- proposal 阈值：至少 3 个独立 user-initiated session 且至少 2 个 active date。
- tie-break：仅同 channel 且 `pre_tie_rank_key` canonical bytes 完全相同的对象；epsilon=`0`。只替换最终 canonical identity tie-break，channel positions、候选 exact-set、eligibility、caps、no-backfill 和 Profile/Policy 不变。

## 9. 用户控制与惰性提案

view/revoke/reset/restore 均由 append-only ledger 派生。达到阈值时只产生 `ExposurePolicyChangeProposal`：target revision 为 `UNASSIGNED_UNTIL_HUMAN_CONFIRMATION`，状态为 `PENDING_HUMAN_CONFIRMATION`，`applied=false`。Verification 不提供 apply 路径。

Profile、Policy、DirectionRegistry 和 Intake Slate 的 before/after canonical hash 必须一致；任何变化为 `LONG_TERM_STATE_MUTATION_BREACH`。

## 10. 运行与错误语义

所有时间均来自 frozen fixture；模块不读取 wall clock。实现不得 import 或调用 socket/HTTP/SDK；显式 business I/O guard 对 network、external source、model/API、真实 Research、Profile/Policy、ARTIFACT_REGISTRY、formal library 和 production write 全部 fail closed。

常见硬失败：`SLATE_HASH_DRIFT`、`SLATE_CONTEXT_BINDING_MISMATCH`、`EVENT_ID_COLLISION`、`EXPOSURE_LEDGER_APPEND_ONLY_BREACH`、`PENDING_DIRECTION_REMAP`、`Research_REAL_ADAPTER_NOT_CONFIGURED`、`FORBIDDEN_PRIVATE_OR_RAW_FIELD`。失败 A0 原件不覆盖；修改任一候选/Schema/fixture/expected/runner/参数后建立新独立 A0 run。

## 11. 验收轴与未授权项

Construction A0 历史始终保持 `acceptance_verdict=NOT_ASSESSED`；A0 PASS 本身不构成 Gate A、Shadow B 或 Human Gate B 裁决。

candidate003 冻结清单文件 SHA-256 为 `CB81B18D802354A2386E1D58475FE5FF95F184F474BD0830D95548F47045DA38`，payload aggregate 为 `E7848A473BAC4B1CA97CF6038AA5331A2EE8FD9E46E303FA95923CC937FD88B9`；单次 Shadow B 权限已按 `1/1/0` 消费，不得重跑、覆盖或复用。

用户已在 Human Gate B 接受当前候选，RECOMMENDATION-HISTORY 终态为 `completed/PASS/PASS`。该状态只覆盖验收时展示并接受的 public-safe exact-set 及正式化必要状态盖章；Discovery 阶段仍为 `NOT_ASSESSED`。

`CONTRACT-Discovery-03` 仍为 open，Research 交互模式保持 `NOT_CONFIGURED`。当前正式基线保持 `production_activation=false`；真实 Research/Profile/Policy、真实来源、ARTIFACT_REGISTRY、Card/Analysis、Retrieval、部署、生产指针与 cleanup 均未自动获权。
