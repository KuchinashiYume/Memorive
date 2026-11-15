# Memorive CANDIDATE-REVIEW CandidateCardSpace 模块契约 r0.1

## 1. 状态与授权

- `run_id`: `CANDIDATE_REVIEW_CROSS_20260811_165122_candidate001`
- 权威来源：用户批准的 readiness decision candidate001，完整 SHA-256 为 `3CDA04DF39E15BD462BD921C06A79F05FAD74E6B105F316EF9D00BF4F624CE68`。
- 当前状态：`FORMAL_RECORD` / `ACCEPTED_PRODUCTION_DISABLED`。
- Human Gate B 已接受当前 public-safe 离线基线；本状态不创建、不读写且不启用生产 CandidateCardSpace。
- 本契约只关闭当前 run 的 `DATA-Discovery-01`；不创建、不读写、不启用生产 CandidateCardSpace。

## 2. 唯一写者与未来 locator

- 逻辑所有者和唯一写者：`CANDIDATE_REVIEW.CANDIDATE_CARD_BUNDLE_WRITER`。
- 未来生产文件根 locator：`G:\Memorive-运维\候选卡片空间`。
- 未来生产索引根 locator：`G:\Memorive-ops\vector-store\discovery-candidate-card-space-v1`。
- 未来 namespace：`memorive_discovery_candidate_cards_v1`。
- 环境变量名：`MEMORIVE_Discovery_CANDIDATE_ROOT`、`MEMORIVE_Discovery_CANDIDATE_INDEX_ROOT`、`MEMORIVE_Discovery_CANDIDATE_COLLECTION`。
- 创建状态：`LOCATOR_APPROVED_NOT_CREATED`；创建需要独立 controlled activation 授权。
- runtime 内容永久禁止进入 Git/GitHub。

## 3. 访问、布局与隔离

- 读者默认拒绝；仅 Retrieval writer 与未来精确 Analysis promotion writer 可按独立契约访问。
- 文件布局：
  - `source/<candidate_id>/<source_revision>/`
  - `document_processing/<candidate_id>/<materialization_revision>/`
  - `cards/<candidate_id>/<card_revision>/`
  - `receipts/<candidate_id>/`
- 沙盒 shadow mirror 可另有本地确定性 `index/`，仅用于 A/B 验证。
- 本空间与 `memorive_sandbox` 及 `memorive_main` 完全独立；Retrieval A/B 只使用 run-local 确定性 JSON 索引。
- Retrieval A/B 对上述生产 locator 的读取和写入计数必须均为 `0`。

## 4. 保留与处置

- 保留策略：`RETAIN_UNTIL_EXPLICIT_DISPOSITION`；禁止按日期自动删除。
- rejected、snoozed、failed 和 sealed 历史均保留。
- quarantine 或最终删除必须使用唯一 `batch_id` 取得独立授权。
- promotion 后不自动删除；manifest 和 receipts 继续保留，payload 处置需另行授权。

## 5. 本 run 的强制边界

- 仅处理 Memorive 自著、public-safe 合成文本。
- 禁止真实全文、网络、外部来源、模型/API、OCR、Embedding、RESEARCH_ANALYSIS Analysis、KNOWLEDGE_ADMISSION active、ARTIFACT_REGISTRY 和 Analysis promotion。
- 候选 Card 必须保持 `review_status=pending`、`completion_status=pending_review`、`promotion_status=NOT_ELIGIBLE`。
- `REVIEW_READY` 只是确定性机械就绪，不表示语义验证、接纳或促进。
- sealed bundle 不得覆盖；repair 必须创建新 revision 并保留旧 hash。
