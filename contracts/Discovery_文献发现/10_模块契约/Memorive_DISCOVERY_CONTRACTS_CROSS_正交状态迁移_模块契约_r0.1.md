# 正交状态与迁移合同

- contract_revision: `discovery_contracts-common-contract-r0.1-candidate001`
- 四类成熟状态、run 三轴和候选五轴分别记录，禁止单字段折叠。

## 状态轴

### maturity_axes

- `design_status`: `proposed | approved | superseded`
- `implementation_status`: `not_started | partial | implemented`
- `verification_status`: `untested | tested | qualified`
- `deployment_status`: `disabled | pilot | active`

### run_axes

- `lifecycle_status`: `completed | aborted | invalidated`
- `verification_result`: `PASS | FAIL | ERROR | NOT_RUN`
- `acceptance_verdict`: `PASS | FAIL | NOT_ASSESSED`

### candidate_axes

- `identity_status`: `HINT_UNVERIFIED | RESOLVING | VERIFIED | CONFLICT | UNRESOLVED`
- `access_status`: `METADATA_ONLY | OA_LOCATION_CLAIMED | RIGHTS_VERIFIED | MATERIALIZED | UNAVAILABLE | RESTRICTED`
- `decision_status`: `PENDING | SNOOZED | APPROVED_FOR_CARD_STAGING | REJECTED`
- `candidate_card_status`: `NOT_REQUESTED | BUILDING | REVIEW_READY | NEEDS_REPAIR | FAILED`
- `promotion_status`: `NOT_ELIGIBLE | READY | PROMOTION_APPROVED | PROMOTED | FAILED`

## 禁止推断

- approved design does not imply implemented
- implemented does not imply tested
- tested does not imply accepted or deployed
- identity verified does not imply access rights
- AI_HINT does not imply identity verified
- rejected does not imply globally suppressed
- invalidated cannot hide a completed assertion failure
