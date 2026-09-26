# P04 T01—T09 依赖与接口矩阵

| T | depends_on | consumes | produces |
|---|---|---|---|
| `T01` | `NONE` | P04 governance; P03 handoff | common envelopes; state/error/profile contracts |
| `T02` | `T01` | DiscoveryPlan; DiscoveryRun | SourceObservation |
| `T03` | `T01,T02` | SourceObservation | CandidateRecord identity projection |
| `T04` | `T03` | CandidateRecord identity; local read-only facts | CandidateRecord relation/novelty projection |
| `T05` | `T02,T03,T04` | DirectionDefinition; ResearchInterestProfile; ResearchExposurePolicy; CandidateRecord | DiscoveryPlan; RecommendationSlate |
| `T06` | `T05` | RecommendationSlate; P03InteractionEvidence | ExposureLedger; ScopedSuppressionRecord; BehavioralInterestDigest; ExposurePolicyChangeProposal |
| `T07` | `T02,T03,T04,T05,T06` | CandidateRecord; first human approval | CandidateCardBundle |
| `T08` | `T07` | CandidateCardBundle; second human approval | CardPromotionIntent; PromotionReceipt |
| `T09` | `T01,T02,T03,T04,T05,T06,T07,T08` | T01-T08 completion archives | end-to-end Shadow and controlled activation handoff |

- DAG cycle 必须为 0。
- 下游 T 仍需各自手册、准备审计与启动授权。
