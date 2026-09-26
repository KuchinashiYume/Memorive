# Direction / Profile / Policy / Session 合同

- schema_revision: `discovery_contracts-common-contract-r0.1-candidate001`
- canonicalization: UTF-8 sorted-key compact JSON；计算对象 hash 时排除 `content_hash`。
- `DirectionDefinition` 的改名、拆分、合并必须新 revision 且人工确认。
- `ResearchInterestProfile` 只接受显式用户来源；行为事实只能形成 proposal。
- `ResearchExposurePolicy` 的 channel budget、集中度上限和留白参数必须版本化并人工确认。
- `HorizonAnchor` 独立于当前本地库相似度。
- `SessionResearchContext` 必须过期，且不得直接改写长期 profile/policy。
- 未知字段、未知 enum、未来 schema revision、hash mismatch 均 fail closed。
