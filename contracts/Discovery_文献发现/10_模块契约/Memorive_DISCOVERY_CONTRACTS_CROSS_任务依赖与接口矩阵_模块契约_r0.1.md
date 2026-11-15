# Discovery Initialization—Integration 依赖与接口矩阵

| T | depends_on | consumes | produces |
|---|---|---|---|
| `Initialization` | `NONE` | Discovery governance; Research handoff | common envelopes; state/error/profile contracts |
| `DataContracts` | `Initialization` | DiscoveryPlan; DiscoveryRun | SourceObservation |
| `ServiceContracts` | `Initialization,DataContracts` | SourceObservation | CandidateRecord identity projection |
| `Configuration` | `ServiceContracts` | CandidateRecord identity; local read-only facts | CandidateRecord relation/novelty projection |
| `Intake` | `DataContracts,ServiceContracts,Configuration` | DirectionDefinition; ResearchInterestProfile; ResearchExposurePolicy; CandidateRecord | DiscoveryPlan; RecommendationSlate |
| `Verification` | `Intake` | RecommendationSlate; ResearchInteractionEvidence | ExposureLedger; ScopedSuppressionRecord; BehavioralInterestDigest; ExposurePolicyChangeProposal |
| `Retrieval` | `DataContracts,ServiceContracts,Configuration,Intake,Verification` | CandidateRecord; first human approval | CandidateCardBundle |
| `Analysis` | `Retrieval` | CandidateCardBundle; second human approval | CardPromotionIntent; PromotionReceipt |
| `Integration` | `Initialization,DataContracts,ServiceContracts,Configuration,Intake,Verification,Retrieval,Analysis` | Initialization-Analysis completion archives | end-to-end Shadow and controlled activation handoff |

- DAG cycle 必须为 0。
- 下游 T 仍需各自手册、准备审计与启动授权。
