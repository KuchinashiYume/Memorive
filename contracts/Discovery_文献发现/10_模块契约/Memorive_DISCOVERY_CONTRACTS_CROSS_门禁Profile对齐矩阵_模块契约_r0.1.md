# Discovery gate profile 对齐矩阵

- profile: `Discovery_EXTERNAL_SOURCE_GATED_ACTIVATION_V1`
- central_status: `REGISTERED` in gate r2.4
- Initialization A1 canary: `NOT_APPLICABLE`

| stage | network | external calls | formal writes | authorization |
|---|---|---:|---:|---|
| `H0` | `disabled` | `0` | `0` | `T_START` |
| `CONSTRUCTION_A0` | `disabled` | `0` | `0` | `T_START` |
| `CONSTRUCTION_A1_CANARY` | `task-specific` | `task-specific-ceiling` | `0` | `SEPARATE_PER_CALL` |
| `HUMAN_GATE_A` | `disabled` | `0` | `0` | `CURRENT_FREEZE_HASH_ONLY` |
| `SHADOW_B` | `disabled-default` | `0` | `0` | `GATE_A_CURRENT_FREEZE` |
| `HUMAN_GATE_B` | `disabled` | `0` | `0` | `TASK_ACCEPTANCE` |
| `FORMALIZATION` | `git-only-when-authorized` | `0` | `accepted-public-safe-exact-set` | `SECTION_6_7_OR_SEPARATE` |
| `CONTROLLED_ACTIVATION` | `separately-frozen` | `separately-frozen` | `separately-frozen` | `ITEMIZED_SEPARATE` |

Initialization 只获 H0 与 A0；Gate A/B、Shadow B、正式化和 activation 均未运行或未授权。
