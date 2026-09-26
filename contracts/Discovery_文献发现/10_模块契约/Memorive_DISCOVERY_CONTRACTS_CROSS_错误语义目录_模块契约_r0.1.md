# 错误语义与 fail-closed 目录

| code | trigger | run 三轴 | retry / new run | side effects |
|---|---|---|---|---:|
| `PRECONDITION_BLOCKED` | required governance/upstream/authorization input missing | `aborted/NOT_RUN/NOT_ASSESSED` | after blocker resolution / `True` | 0 |
| `SCHEMA_REJECTED` | unknown field/enum/schema revision or malformed required field | `completed/FAIL/NOT_ASSESSED` | after directed fix / `True` | 0 |
| `IDENTITY_CONFLICT` | multiple incompatible identity assertions | `completed/FAIL/NOT_ASSESSED` | after new evidence / `True` | 0 |
| `RECEIPT_MISSING` | required external-source receipt absent | `aborted/NOT_RUN/NOT_ASSESSED` | only after new authorization and receipt readiness / `True` | 0 |
| `SOURCE_UNAVAILABLE` | source observation cannot be obtained | `completed/FAIL/NOT_ASSESSED` | task-specific / `True` | 0 |
| `RIGHTS_UNKNOWN` | materialization rights not verified | `completed/FAIL/NOT_ASSESSED` | after rights evidence / `True` | 0 |
| `ILLEGAL_TRANSITION` | state transition or cross-axis precondition invalid | `completed/FAIL/NOT_ASSESSED` | after directed fix / `True` | 0 |
| `VALIDATOR_ERROR` | validator cannot evaluate frozen input | `completed/ERROR/NOT_ASSESSED` | after validator successor / `True` | 0 |
| `VERIFICATION_FAILED` | frozen assertion fails | `completed/FAIL/NOT_ASSESSED` | after directed fix / `True` | 0 |
| `GOVERNANCE_NOT_ASSESSED` | method or reviewer capability insufficient | `completed/PASS/NOT_ASSESSED` | not a retry by itself / `False` | 0 |
| `HASH_MISMATCH` | recorded content hash differs from canonical bytes | `completed/FAIL/NOT_ASSESSED` | after new immutable object revision / `True` | 0 |
| `PARENT_NOT_FOUND` | parent or supersedes reference cannot be resolved | `completed/FAIL/NOT_ASSESSED` | after reference repair / `True` | 0 |
| `DUPLICATE_IDENTITY` | object_id/revision or run_id reused | `completed/FAIL/NOT_ASSESSED` | after unique successor identity / `True` | 0 |
| `FORBIDDEN_SIDE_EFFECT` | network/formal root/registry/pointer mutation attempted | `aborted/NOT_RUN/NOT_ASSESSED` | requires separate authorization, never in same run / `True` | 0 |
