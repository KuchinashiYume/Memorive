# P04/T08 原子晋升与补偿合同（candidate001）

- 适用范围：仅 `P04_T08_CROSS_20260811_195107_candidate001` 的公开安全、纯合成、纯离线沙盒镜像。
- 单写者：`CardPromotionIntent` 仅由 `P04_T08.PROMOTION_COORDINATOR` 形成；`PromotionReceipt` 仅由 `P04_T08.ATOMIC_PROMOTION_WRITER` 形成。
- 批准绑定：第二次批准必须精确绑定 `candidate_id + card_revision + candidate_bundle_hash + target_namespace + promotion_id + paper_id + target_prestate_hash`，且未过期、未撤销、decision 为 `APPROVE`。
- 写前门：bundle、成员 hash、identity、rights/source、Card v6、completion/omissions、anchors、Analysis 禁令、identifier、paper_id、target path 和 target prestate 全部通过后才允许形成 intent。
- 事务状态：`PRECHECKED -> PREPARED -> COMMITTING -> COMMITTED`；失败终态仅为 `ROLLED_BACK` 或 `COMPENSATION_REQUIRED`。
- WAL：每次状态变化追加一个不可覆盖的 JSONL journal entry；不得截断 M8/M13 append-only 历史。
- 锁：promotion_id 与 paper_id 双锁；未知活动锁或未完成 journal 一律 fail closed，不猜删 stale lock。
- 幂等：同 promotion_id、同 intent 且已提交时逐字节返回原 receipt，新增正式文件/M8/M13/index/receipt 写入均为 0；同 ID 异 intent 冲突；同 bundle 异 ID 禁止重复晋升；失败 ID 不自动重试。
- 提交顺序：正式文件镜像、M8 镜像事件、M13 v2 镜像注册、formal index 镜像、后验核验、COMMITTED journal/ledger，最后才写 `PromotionReceipt`。
- 可见性：`PromotionReceipt` 本身是最后可见性 marker；reader 同时校验 receipt hash、COMMITTED journal、M8 active、M13 locator/lineage、index 和目标 bytes，任一不符均不可见。
- 补偿：receipt 之前失败时，已发布文件移入 transaction quarantine；M8/M13 已追加历史只追加补偿事件；index 删除或 tombstone；补偿失败进入 `COMPENSATION_REQUIRED`、保留双锁与 journal、禁止自动重试且 reader visibility=0。
- 红线：生产 `G:\PR-OS`、`G:\PR-OS-运维`、`G:\PR-OS-ops` 写入为 0；网络、模型、OCR、Embedding、credential 读取为 0；Analysis payload 为 0；A/B 不消费真实候选或真实批准。
