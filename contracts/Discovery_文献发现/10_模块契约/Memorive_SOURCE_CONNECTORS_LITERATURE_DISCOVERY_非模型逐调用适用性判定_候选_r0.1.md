---
document_id: Memorive-SOURCE-CONNECTORS-LITERATURE_DISCOVERY-NON-MODEL-PER-CALL-APPLICABILITY-DECISION-CANDIDATE
phase_owner: Discovery
applies_to: SOURCE-CONNECTORS
task_owner: DataContracts
module_owner: LITERATURE_DISCOVERY
document_type: contract-compatibility-decision-candidate
authority: proposal-only
lifecycle: draft
revision: r0.1
created_date: 2026-08-10
updated_date: 2026-08-10
---

# 非模型学术元数据 API 逐调用适用性判定候选

## 结论

当前 `CONTRACT-Discovery-01` **未关闭**。现行 `Memorive-SHARED-EXTERNAL-MODEL-CALL-EVIDENCE-RECEIPT-SCHEMA@r1.0` 的 `PROSPECTIVE_STRICT` 形状以模型调用为中心；`SENT` 记录要求非空 `provider` 和 `requested_model`。arXiv/Crossref 是非模型学术元数据 API，若填入伪模型字符串会违反 Discovery 详细设计、中央门禁 r2.4 和“不得猜配/伪造身份”的核心不变量。

本候选提出以下最窄语义，但**不宣称已获得中央批准**：

1. 非模型 request attempt 继续服从 §5.8 的稳定 request identity、route/region/egress、行为 hash、时间、状态、延迟、费用、source refs、retry 新身份和发送前 fail-closed。
2. 模型专属字段使用 JSON `null`，并同时记录 `field_applicability=NOT_APPLICABLE_NON_MODEL_API` 与明确 reason code；不使用空字符串、`NONE`、connector 名或 requested=returned 回填。
3. 新建对象名 `ExternalSourceRequestReceipt`，与 `CallEvidenceReceipt` 正交；未来中央 successor 可选择建立通用 `ExternalRequestReceipt` 上位合同或给现行 Schema 增加 discriminated non-model branch。
4. 在中央 successor/明确兼容裁决完成前，`central_5_8_compatibility_status=UNRESOLVED_BLOCKING`，所有物理请求发送前阻断。
5. blocked-before-send receipt 只证明 gate 生效，不计入 external request attempt、来源质量、canary PASS 或资格证据。

## 当前执行效力

- 对 A0：可实现、可 Schema 验证、可做负向测试。
- 对 A1：`NOT_AUTHORIZED_NOT_RUN`；本文件没有授权效力。
- 对 production/RUNTIME_LOG：无迁移、无 writer、无部署效力。
- 唯一允许的发送结果：无。当前候选实现不包含网络 transport。
