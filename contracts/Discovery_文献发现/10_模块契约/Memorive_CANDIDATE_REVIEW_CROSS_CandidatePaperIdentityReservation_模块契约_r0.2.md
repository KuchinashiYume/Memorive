# Memorive CANDIDATE-REVIEW Candidate Paper Identity Reservation 模块契约 r0.2

## 1. revision 关系、状态与授权

- `document_id`: `Memorive-CANDIDATE-REVIEW-CROSS-CANDIDATE-PAPER-IDENTITY-RESERVATION`
- `revision`: `r0.2`
- `supersedes_for_current_run`: `r0.1`
- 定向修正：r0.1 的人读语义正确，但配套 receipt Schema 误把 `paper_id` 写成带下划线的形态。现行 DOCUMENT_PROCESSING `make_paper_id(surname, year, suffix="")` 实际连接三个部分，例如 `Synthetic2026` 或显式消歧后的 `Synthetic2026a`。
- `run_id`: `CANDIDATE_REVIEW_CROSS_20260811_165122_candidate001`
- 权威来源：用户批准的 readiness decision candidate001，完整 SHA-256 为 `3CDA04DF39E15BD462BD921C06A79F05FAD74E6B105F316EF9D00BF4F624CE68`。
- 当前状态：`FORMAL_RECORD` / `ACCEPTED_PRODUCTION_DISABLED`。
- Human Gate B 已接受当前 public-safe 离线基线；未执行任何 candidate 或正式 DOCUMENT_PROCESSING registry 写入。
- 本契约只关闭当前 run 的 `CONTRACT-Discovery-02`；不改动 Card Schema 或 DOCUMENT_PROCESSING 生产代码。

## 2. 双身份轴

- `candidate_id` 是永久 Discovery candidate identity。
- `paper_id` 是通过现行 DOCUMENT_PROCESSING `make_paper_id(surname, year, explicit_suffix)` 返回的合法 Card identity；当 `explicit_suffix=""` 时无后缀，只有经人明确消歧时才能为 `a` 或 `b`。
- `RESERVED_CANDIDATE` 是独立的 paper identity status，绝不得作为字面 `paper_id`。
- Card 中 `paper_id` 必须等于对同一 `surname/year/explicit_suffix` 调用 `make_paper_id` 的返回值，且等于 `source_anchor.paper_id`。
- Card 必须保持 `review_status=pending`、`completion_status=pending_review`。

## 3. 预留回执与碰撞

- 预留回执必须绑定：`candidate_id`、`work_cluster_id`、`manifestation_id`、`paper_id`、`surname`、`year`、`explicit_suffix`、`first_approval_receipt_hash`、`rights_receipt_hash`、`candidate_registry_before_hash`、`candidate_registry_after_hash`。
- 碰撞时 fail closed；禁止自动生成后缀。
- 需要后缀时，只能由人明确选择 `a` 或 `b` 并取得 registry 确认。
- candidate registry 与正式 DOCUMENT_PROCESSING registry 分离；Retrieval 不修改正式 DOCUMENT_PROCESSING registry。
- 完全相同绑定的 replay 幂等；同一 `paper_id` 如指向不同 candidate 或文件夹，必须阻断。

## 4. 促进边界

- Analysis 必须重新验证，并在正式 registry 中原子占用同一 `paper_id`。
- Retrieval 只允许薄适配层；不修改 Card Schema 或 DOCUMENT_PROCESSING 生产实现。
- Retrieval 所有 candidate bundle 均为 `promotion_status=NOT_ELIGIBLE`，任何 promotion intent 或 writer 请求均必须拒绝。
