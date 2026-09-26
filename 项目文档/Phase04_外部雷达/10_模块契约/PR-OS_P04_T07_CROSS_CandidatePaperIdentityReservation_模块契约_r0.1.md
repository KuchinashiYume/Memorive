# PR-OS P04/T07 Candidate Paper Identity Reservation 模块契约 r0.1

## 1. 状态与授权

- `run_id`: `P04_T07_CROSS_20260811_165122_candidate001`
- 权威来源：用户批准的 readiness decision candidate001，完整 SHA-256 为 `3CDA04DF39E15BD462BD921C06A79F05FAD74E6B105F316EF9D00BF4F624CE68`。
- 当前状态：`FORMAL_HISTORY` / `SUPERSEDED_BY_R0.2`。
- Human Gate B 保留 r0.1 为历史基线；当前正式 revision 为 r0.2，且未执行任何生产 registry 写入。
- 本契约只关闭当前 run 的 `CONTRACT-P04-02`；不改动 Card Schema 或 M01 生产代码。

## 2. 双身份轴

- `candidate_id` 是永久 P04 candidate identity。
- `paper_id` 是通过 M01 `make_paper_id(surname, year, explicit_suffix)` 生成的合法 Card identity。
- `RESERVED_CANDIDATE` 是独立的 paper identity status，绝不得作为字面 `paper_id`。
- Card 中 `paper_id` 必须合法且等于 `source_anchor.paper_id`；`review_status=pending`，`completion_status=pending_review`。

## 3. 预留回执与碰撞

- 预留回执必须绑定：`candidate_id`、`work_cluster_id`、`manifestation_id`、`paper_id`、`surname`、`year`、`explicit_suffix`、`first_approval_receipt_hash`、`rights_receipt_hash`、`candidate_registry_before_hash`、`candidate_registry_after_hash`。
- 碰撞时 fail closed；禁止自动生成后缀。
- 需要后缀时，只能由人明确选择 `a` 或 `b` 并取得 registry 确认。
- candidate registry 与正式 M01 registry 分离；T07 不修改正式 M01 registry。
- 完全相同绑定的 replay 幂等；同一 `paper_id` 如指向不同 candidate 或文件夹，必须阻断。

## 4. 促进边界

- T08 必须重新验证，并在正式 registry 中原子占用同一 `paper_id`。
- T07 只允许薄适配层；不修改 Card Schema 或 M01 生产实现。
- T07 所有 candidate bundle 均为 `promotion_status=NOT_ELIGIBLE`，任何 promotion intent 或 writer 请求均必须拒绝。
