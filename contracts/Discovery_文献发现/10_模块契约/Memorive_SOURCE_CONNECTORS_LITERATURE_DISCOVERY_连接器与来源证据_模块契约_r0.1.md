---
document_id: Memorive-SOURCE-CONNECTORS-LITERATURE_DISCOVERY-CONNECTOR-AND-SOURCE-EVIDENCE-CONTRACT
phase_owner: Discovery
applies_to: SOURCE-CONNECTORS
task_owner: DataContracts
module_owner: LITERATURE_DISCOVERY
document_type: module-contract
authority: normative
lifecycle: active
revision: r0.1
created_date: 2026-08-10
updated_date: 2026-08-10
approval_status: accepted
approved_at: 2026-08-10 20:00 +09:00
approval_basis_sha256: 95C231F17329A5C2CDE75EA624852BEB371D9D446DC98B1AAFFB863918AD8B43
accepted_candidate: SOURCE_CONNECTORS_LITERATURE_DISCOVERY_candidate002_freeze001
freeze_manifest_sha256: BF7657589AF2D8B88FEBA3CB1159C3BEEB2401DF7939695A38EA3193B2850855
canonical_path: G:\Memorive\contracts\Discovery_文献发现\10_模块契约\Memorive_SOURCE_CONNECTORS_LITERATURE_DISCOVERY_连接器与来源证据_模块契约_r0.1.md
---

# SOURCE-CONNECTORS/LITERATURE_DISCOVERY · 连接器与来源证据模块契约

## 1. authority 与职责

LITERATURE_DISCOVERY 是 `Connector`、`SourceObservation`、`ExternalSourceRequestReceipt` 和 `SourceSnapshot` 的 DataContracts 单写者。Initialization 仍是共同 envelope、状态轴和错误语义的上位 authority；ServiceContracts 消费 observation 并负责跨来源 WorkCluster/Manifestation 解析，DataContracts 不静默合并冲突记录。

MVP connector 只有 `ARXIV` 与 `CROSSREF`。A0 的“ready”仅表示合成 fixture parser/replay 可用，不表示真实 endpoint、条款、速率、返回字段或生产网络可用。OpenAlex、PMC OA、bioRxiv/medRxiv、Unpaywall、Web of Science 和 Elsevier 均为 `NOT_CONFIGURED`。

## 2. Connector protocol

```text
search(discovery_query, cursor, run_context, source_snapshot) -> SourceObservationPage
resolve(identifier_or_bibliographic_hint, run_context, source_snapshot) -> ResolutionResult
get_open_locations(work_identity, run_context, source_snapshot) -> OpenLocationResult
healthcheck(run_context) -> ConnectorHealthReceipt
```

四个方法必须显式接收冻结 snapshot；A0 不存在自动 transport。任意 cursor 必须由 adapter 自己验证，未知或被篡改的 cursor 返回 `INVALID_CURSOR`。`healthcheck` 在 A0 只检查 capability/fixtures/schema，不探测 DNS、socket 或 provider。

## 3. SourceObservation

`SourceObservation` 必须：

- 使用稳定 `object_id/revision/content_hash`；producer/single_writer 都是 `SOURCE-CONNECTORS/LITERATURE_DISCOVERY`；
- 绑定 run、connector、snapshot、原始/规范化 record hash；
- 只记录来源实际给出的标识符，不补造 DOI/arXiv ID/PMID；
- `access_status=METADATA_ONLY`，所有 location claim 的 `materialized=false`；
- `decision_status=PENDING`、`candidate_card_status=NOT_REQUESTED`、`promotion_status=NOT_ELIGIBLE`；
- 来源冲突时 `identity_status=CONFLICT`，保留双份记录并交 ServiceContracts，不在 DataContracts 合并；
- canonical hash 排除自身 `content_hash` 字段，其他字段按 UTF-8、sorted keys、compact separators 计算 SHA-256。

## 4. SourceSnapshot

Snapshot 绑定 connector revision、endpoint family、source schema revision、raw fixture bytes hash、normalized payload hash、bytes、受控 locator 和固定采集时间。A0 只能是 `SYNTHETIC_PUBLIC_SAFE_A0`、`SYNTHETIC_SELF_OWNED`、`network_used=false`、`materialized_fulltext=false`、`request_id=null`。

Snapshot hash mismatch 必须在解析或 replay 前阻断，不能靠重解析新 bytes 覆盖旧 snapshot。

## 5. ExternalSourceRequestReceipt

非模型 request receipt 与现行模型专用逐调用 Schema 的关系是“候选、尚未中央批准”，不是替换或兼容已成立。模型字段固定为：

```json
{
  "requested_model": null,
  "returned_model": null,
  "field_applicability": "NOT_APPLICABLE_NON_MODEL_API",
  "reason_code": "SCHOLARLY_METADATA_API_IS_NOT_A_MODEL_PROVIDER"
}
```

不得填 `arxiv`、`crossref`、`none` 或其他字符串来冒充模型。当前 A0 的 blocked receipt 记录 preflight 事实，但不冒充一次物理 request。`SENT` 只有在中央 successor/裁决批准非模型适用性、当前官方条款核对、exact request 授权、query/cursor、route/region/egress、请求/重试上限和 receipt writer 全部冻结后才有 Schema 路径；当前 gate 不提供 transport，始终阻断。

## 6. pagination/cache/backoff

- arXiv A0 使用 opaque offset cursor，Crossref A0 使用 opaque source cursor；adapter 对 cursor 与 connector/query/page 绑定进行校验。
- cache key 至少包含 connector id/revision、query hash、cursor hash 和 snapshot hash；cache hit 不改变 canonical page bytes。
- backoff 为 deterministic capped exponential；若有合法 `Retry-After` 则取不低于其值且不超过 cap。A0 只计算 schedule，不 sleep。
- 429/5xx 只验证分类与 schedule；不会制造或发送请求。

## 7. partial failure 与冲突

一个 MVP source 成功、另一个失败时 overall coverage=`PARTIAL`，失败 source、error code、retryability 和 impact 必须保留。两个 source 都失败时为 `FAILED`。不得把成功 source 的结果写成“全源成功”。同一 DOI/arXiv ID 的关键书目字段冲突必须保留两条 observation、标 `SOURCE_IDENTIFIER_CONFLICT`，禁止 DataContracts 直接择一或覆盖。

## 8. A0/A1/Shadow 边界

- A0：合成 fixtures、零网络、零 provider、零模型、零成本；candidate002 run_005 已 `completed/PASS/NOT_ASSESSED`，43/43 PASS。
- A1：`NOT_AUTHORIZED_NOT_RUN`；需要独立逐次授权和所有 blocker 关闭。
- Shadow B：Human Gate A 已绑定精确 freeze SHA-256，run_002 已唯一一次离线执行并 15/15 PASS；单次授权已消费，不得复用或重跑。
- Construction/Shadow 均不得写正式文献库、CandidateCardSpace、DOCUMENT_PROCESSING、RUNTIME_LOG production、ARTIFACT_REGISTRY、Card/Analysis、index/route/profile/scheduler/pointer 或 Git。
- 用户 Human Gate B 已使 DataContracts 成为 `completed/PASS/PASS` 并授权 public-safe 正式化；这不授权 A1、ServiceContracts、部署、生产激活、ARTIFACT_REGISTRY、指针或清理。

## 9. 兼容与 successor

新增 provider 能力、字段语义、发送条件或枚举收窄必须升 revision。reader 可忽略 provider-specific `extensions` 只在未来 Schema 明确允许后使用；当前所有 Schema `additionalProperties=false`，未知字段 fail closed。生产 transport、真实 healthcheck、RUNTIME_LOG writer、credential/route integration 和 current-term policy 都是后续授权范围，不得从本 r0.1 推导。
