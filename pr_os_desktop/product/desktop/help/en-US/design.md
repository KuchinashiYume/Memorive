# Memorive System Design

*Revised v14 concise draft r2 · Sessions, leaderboard and cost design · 24 September 2026*

This document explains Memorive's research-material flow, the responsibilities of modules M1–M17, cross-module rules, desktop interaction, and current qualification boundaries. It is for product readers and design reviewers; the researcher remains responsible for judgment and final writing.

**The selected Memorive v1.01 / build136_r41_path_compatibility remains an unsigned private-test candidate. Its installation lifecycle and path-compatibility checks passed within their defined scope. Full T13 manual conformance, user acceptance, and public-release qualification have not been established.**

## 1. Positioning and Principles

Memorive is an auxiliary system for personal research workflows, supporting material acquisition, evidence organization, retrieval and analysis, research logging, and knowledge reuse. It is not bound to a single discipline. The system provides traceable materials and recommendations; researchers are responsible for judgment, decision-making, and final writing.

Researchers retain final decision authority. AI can understand, organize, analyze, recommend, verify, and remind, but it cannot independently approve import, acceptance, feedback, or publication, nor can it overwrite raw data or replace the researcher in forming final scientific conclusions. The system can execute authorized tasks and rules; automatic execution does not generate new authorization.

Data is independent of tools. Originals, Markdown, Cards, notes, and decision records are retained long-term; models and software can be replaced. Retrieval indexes and interface projections serve these assets and do not become a separate source of truth.

Build by phase. Complete the minimal research loop first, then add quality governance, lifecycle management, and research operations. Subdivide only high-complexity modules; new capabilities must have clear responsibilities, dependencies, and exit conditions. Do not build all features simultaneously just because the product positioning expands.

Judge quality by evidence. Format validity, content credibility, extraction completeness, provenance validity, and publication permission are distinct issues. Prioritize verification for critical values, causal judgments, and content intended for papers. Ordinary, separable gaps can be retained with reduced status; block output only when core evidence is invalid or continued output would be misleading.

Preserve original text and versions. The data backbone retains the source language; translation occurs only at the user-selected display layer and is not written back to the data. Modifications create new versions; old content, review records, and failure evidence are retained.

Distinguish capability from status. Design approval, implementation existence, limited verification, manual acceptance, production eligibility, actual enablement, and official release are recorded separately. Subsequent successes do not overwrite historical failures, and phase completion does not imply all capabilities are enabled.

### Scope of This Document

The main text covers module responsibilities, inputs and outputs, key dependencies, cross-module constraints, failure handling, and phase status. Complete field sets, prompts, thresholds, provider prices, individual test records, and file hashes are maintained in the relevant module contracts and runtime evidence.

## 2. System Architecture

### 2.1 Main Line and Side Paths

Material Processing: User-authorized input → M1 Preprocessing → M2 Card Generation → M6 Verification → M8 Acceptance Execution → Normal Retrieval Entry.

Research Analysis: Question → M3 Retrieval and Context Pack Assembly → M4 Analysis Recommendation Generation → M6 Independent Review triggered per contract → User Judgment.

Knowledge Reuse: Accepted materials → M5 Opportunity Analysis; user-selected reusable results → M10 knowledge-feedback candidate → user confirms destination and acceptance → M8/M13 execution status and version handling.

External Discovery: M16 acquires candidates, M17 organizes recommendations → isolated processing after first manual approval → formal promotion after second manual approval. Candidate exploration and formal ingestion are not the same action.

Originals and preprocessed intermediate artifacts may be saved first, without requiring the Card to be active; materials entering normal analysis must satisfy their acceptance contract. M15 generates research change reports from multi-source events and is not a mandatory step in the main workflow described above.

![Figure 1 · System overview](assets/figure-01.png)

*Figure 1 · System overview*

### 2.2 Module Responsibilities

| Layer | Module | Responsibility |
| --- | --- | --- |
| Processing and Analysis | M1 Preprocessing; M2 Distillation; M3 Retrieval; M4 Analysis | Form citable evidence and analytical recommendations from raw materials |
| Analysis Bypass | M5 Opportunity Analysis | Identify tensions and author-stated gaps under comparable conditions |
| Shared Services | M6 Verification; M7 Weighting; M8 State Machine; M9 Model Gateway | Responsible for content review, ranking, acceptance execution, and model invocation, respectively |
| Logging and Feedback | M10 Feedback Loop; M11 Logging | Form controlled feedback candidates and record operational facts and final states |
| Long-term Governance | M12 Decision Log; M13 Lifecycle; M14 Quality Assurance | Preserve decision rationale, immutable provenance, and continuous quality evidence |
| Research Operations | M15 Change Log; M16 Intelligence Radar; M17 Active Acquisition | Report changes, discover candidates, and organize recommendations |
| Application and Execution | ApplicationFacade, Application Service, Control Store, ExecutionCore, JobRunner | Connect desktop, business modules, persistent runtime state, and execution channels; do not add new business module numbers |

### 2.3 Responsibility Boundaries

M1 handles conversion and OCR quality; M2 is responsible for extraction and local structural validation; M4 validates citation identity and context membership; M6 determines whether evidence supports the content. M8 executes state transitions solely based on requests and grounds, without generating quality judgments.

M9/provider adapter handles technical retries within the call boundary; JobRunner manages execution, timeouts, cancellation, and recovery; M2/M6 manage business-level rework; M11 records events. M12 stores decision rationale; M13 stores versions and lineage; M14 monitors quality and provides disposition recommendations.

M1–M17 constitute the module set for this version. Subsequent extensions require independent approval of the ModuleManifest, dependencies, compatibility, and eligibility, and are disabled by default; the extension protocol itself does not authorize the addition of new modules.

## III. Data and Comparison Conventions

### 3.1 Two-Tier Knowledge Base

The source layer stores originals and their traceable text: PDFs or other raw files, RawMD, CleanMD, and Chunks. Originals serve as the ultimate evidentiary basis; conversion results retain source locations and conversion records.

The card layer stores Cards that faithfully distill individual documents, intended for retrieval, comparison, and review. Personal annotations, cross-document summaries, Analysis, and feedback objects are treated as separate derived artifacts and must not be mixed into the original-faithful fields of a Card.

Vectors, field vectors, query projections, and the desktop library are all access layers. They must point to specific sources and artifact versions, can be rebuilt according to contract, but cannot reverse-substitute for the authority of originals, Cards, or the Registry.

### 3.2 Identity, Provenance, and Language

Literature uses a stable paper_id; persisted artifacts store artifact ID, content hash, schema version, source scope, parent version, run, and execution configuration. Multiple Context Packs, Analysis, and release manifests may have multiple parent artifacts; they cannot rely solely on a single mutable latest path.

High-risk Card information stores both a readable distillation and a source_anchor: {chunk_id, quote}. The quote must be verifiable within the corresponding chunk of the same document; the authenticity of the original quote does not imply that the distillation is semantically valid, and review is still required. Historical missing identity, structure, or lineage is marked as unknown / not_assessed, and must not be guessed or filled in based on file names, timestamps, or similar text.

RawMD, CleanMD, Card, analysis, and opportunity comparisons retain the original language. Interface language does not change data language; displayed translations must allow review of the original text and anchors, and are not written back as new evidence. Mixed-language granularity and output translation remain within the unresolved scope defined in Chapter 9.

### 3.3 General Comparison Context

| Field | Information to Express |
| --- | --- |
| Research Object | Research subject, material, sample, text, or system |
| Intervention / Factor | Factor or intervention under examination |
| Comparator | Control subject or comparison baseline |
| Method | Method, research design, or analytical framework |
| Metric | Evaluation metric and its meaning |
| Boundary Condition | Conditions and boundaries for the applicability of conclusions |
| Scale / Context | Scale, scenario, and application context |
| Claim Type | Conclusion types such as causal, correlational, mechanistic, performance, and method validation |

Disciplines only replace alias mappings; core fields remain unchanged. M2 extracts comparison context, and M5 uses it to determine comparability. Comparison results cite fact_id or traceable Card field paths and versions; this business expression does not replace the Card Schema, audit sidecar, coverage, or provenance contract.

## 4. Module Design

### M1 Preprocessing Pipeline

![Figure 2 · M1 workflow](assets/figure-02.png)

*Figure 2 · M1 workflow*

Input → Output: Raw documents, images, or notes → Original archive, RawMD, CleanMD, Chunks, vector index, and conversion records.

Dependencies and Deliverables: Ingestion workflows and approved external candidate calls invoke M1; M1 uses M9 conversion/OCR/embedding channels, M7 entry signals, M13 version evidence, and M11 logs to deliver text and provenance to M2. M8 is not invoked before a Card is formed, and conversion quality is not delegated to M6 general review.

| Step | Processing Content |
| --- | --- |
| M1a Import | Preserve original, provenance, identity, timestamp, and data disposition rights markers |
| M1b Conversion | Convert PDF/other inputs to RawMD; perform on-demand OCR for anomalous pages and output a conversion report |
| M1c Cleaning | Process format noise such as headers, footers, line breaks, and column runs using local deterministic rules, while preserving language and source markers |
| M1d Chunking | Split content by structure (sections, paragraphs, tables, figure captions, etc.) and generate stable chunk_id values |
| M1e Embedding | Generate vectors according to the approved profile, record the model and timestamp, and pass through existing structure and provenance metadata |

Page-level OCR. For standard digital PDFs, prioritize local parsing and do not default to full-text OCR. Only pages with scans, near-empty text, garbled characters, or critical structural damage enter the rescue workflow: local diagnosis → provenance check → approved OCR role → parallel verification against original parsing → page merging. The original physical page is the final authority; dual-path results do not determine truth via majority voting. If a single page fails, retain other pages and explicitly mark missing pages and incomplete ranges.

Note verification. Notes are paused by default after M1b for user inspection of transcription and formatting, without judging the value of the note content. Skipping verification requires explicit configuration and logging. Resumption uses validated RawMD metadata and does not rely on previous process memory; stop immediately if critical identity information is missing.

Vector consistency and recovery. The same model name does not guarantee the same vector space; reusing an index requires verifying dimensions, normalization, distance metrics, and retrieval regression. Standard execution and recovery entry points do not have the capability to clear old vectors. Explicit re-embedding first confirms that new text artifacts can be generated, then saves an on-disk snapshot of old vectors, cleans and verifies before rebuilding. If aborted, restore old vectors from the snapshot without re-invoking the model to fake old values; if recovery fails, retain the snapshot and prompt for manual handling. Re-embedding the same document must respect single-writer boundaries.

Scope of this version. P06 registers 14 canonical formats: 11 are supported, while JPEG/PNG/TIFF are degraded and used only for frame probing, not representing image OCR. PDFs retain the [PDF] marker, while other originals are preserved as original bytes with the [Original] marker; typed anchors are projected to chunk_schema_version=3. The Office legacy bridge covers only the frozen Office16 scope, prohibiting macros and link updates. See Chapters 8 and 9 for the actual eligibility of OCR, Marker, and local chains.

### M2 Distillation

![Figure 3 · M2 workflow](assets/figure-03.png)

*Figure 3 · M2 workflow*

Input → Output: A single CleanMD document and a frozen Chunks list → Card, auxiliary extraction results, and coverage records.

Dependencies and delivery: Request card_distiller_primary via M9; results first pass local hard gates, then proceed to M6 review and M8 acceptance, with M13/M11 saving versions and logs. Card field embedding is triggered by subsequent approved workflows and is not mixed with M1e source text embedding as a single step.

Content scope. Core fields include the research question, subject, methods, key results, author conclusions, boundary conditions, and source anchors. Auxiliary extraction includes key data, comparative context, author-stated limitations/future work, and optional terminology and citation relationships. Research limitations are separated from the applicability boundaries of conclusions. Personal insights, critiques, hypotheses, cross-paper synthesis, and writing corpora do not enter the fidelity layer.

| Rule | Extraction Requirement |
| --- | --- |
| R1–R2 | Preserve the strength of author claims; fully retain multi-step processes and multiple comparison groups; do not substitute a single item for the whole. |
| R3–R4 | Extract numerical values along with statistical type, stage, sample, and scope; specify the scope, denominator, and control for metrics. |
| R5–R6 | Attach actual applicability conditions to conclusions; distinguish between the research subject and the characterization/test sample. |
| R7–R8 | Define terms according to the author's definitions; source anchors must cover the actual material scope and must not be uniformly abbreviated as 'abstract'. |
| R9 | Extract information already provided in the source; if truly absent, mark it as such without fabrication. When handling obvious typos based on context, retain the original value, the basis, and the revision trace. |

Structure and coverage. The Card Schema uses v6 as the baseline, retaining per-anchor citations, field_credibility, completion_status, and omissions/tombstone. Required slots are marked as present, absent_in_source, not_applicable, extraction_gap, or not_assessed; 'not found' cannot be directly recorded as 'not in source'. Completion of review and absence of registered deletions do not prove that extraction has covered the entire source text.

Generation and Recovery. Before writing a usable Card, validate the schema, types, enums, source IDs, anchors, and deterministic cross-field constraints. For high-risk quotes, perform exact matching against the original text; normalization addresses only typographical noise. Before Full, a dedicated budget allows one core anchor recovery pass: freeze the precise targets and candidate sources, permitting only the replacement or deletion of invalid anchors. If a field loses all valid anchors, it must not be released.

Post-Full repairs follow the M6 contract: a single Card cascade allows at most one business batch rework; do not regenerate the entire Card or perform a second Full. Systemic failures require creating a new Card, run, and manifest, preserving the previous failure. Technical truncation recovery uses a separate bounded budget.

Long-form Content. P06 segmented_distill has established mechanisms for capacity probing, segmentation, segment anchoring, hierarchical merging, capped revision, and atomic visibility of complete files. Complete file visibility and M8 acceptance are distinct; if review is incomplete, the status remains pending. The module contract and sample eligibility do not guarantee desktop integration.

### M3 Retrieval Weighting

![Figure 4 · M3 workflow](assets/figure-04.png)

*Figure 4 · M3 workflow*

Input → Output: Question, authorized sources, and weighting configuration → a traceable, capacity-constrained Context Pack.

Dependencies: M7 provides ranking scores, M9 provides authorized embedding/reranking channels, and M11 records logs. Results are used by M4, M5, and daily retrieval.

Retrieval comprises question understanding, candidate recall, and context packaging. First locate using Card and field signals; drill down to original text if necessary. Card anchors serve as recall clues, while the actual evidence text delivered comes from Chunks. Packaging simultaneously controls token limits, source diversity, question facets, and high-quality counter-examples, without treating a fixed block count as proof of coverage.

logical_span organizes raw members with explicit structural relationships only at runtime, preserving member_chunk_ids. The current scope covers tables and headings/footnotes within the same chapter, as well as strictly adjacent continuous paragraphs. It does not rewrite chunks nor perform arbitrary cross-chapter semantic fusion.

M3 records covered/missing question facets; M4 generates additional analysis metrics such as direct-answer, subquestion coverage, and source-dominance. Do not guess or fill in unknown structures. References are excluded under standard retrieval rules only when both the chapter and entry format are explicit; bibliographic questions may be retained.

The Context Pack aggregates the data_ownership of actually selected members to form an immutable snapshot including effective_data_ownership, mixed status, contributing sources, summaries, and parsing receipts, which is passed along M3→M4→M6. Unknown or conflicting permissions halt external transmission per the permission contract. If reranking is enabled, only authorized modes may be used; if the resulting identity set is invalid, revert to the original distance order. Retrieval sub-dialog refinement for complex questions is not yet integrated.

### M4 Analysis

![Figure 5 · M4 workflow](assets/figure-05.png)

*Figure 5 · M4 workflow*

Input → Output: Question and Context Pack → analytical recommendations with citations, limitations, and locations for re-verification.

Dependencies: M3 provides evidence, M9 invokes analysis_primary, M6 verifies semantic support, and M11 logs traces. Output is bound to specific Card/Pack parent versions, ownership, model, and timestamp.

Analysis strictly distinguishes four parts: literature-supported content with citations, AI-supplemented general knowledge not verified by this library, risks and uncertainties, and locations for re-verifying original text. When coverage is insufficient, suppress AI general knowledge completion, separately explaining the study's inherent limitations, internal source inconsistencies, and retrieval gaps.

M4 performs deterministic source checks only: source_id must exist and belong to the current Pack or its expanded members. Invalid items are removed from the literature support area with the reason recorded; remaining valid content is retained. Blocking occurs if all core support is invalid or if continued output would be misleading. Whether evidence is sufficient, or whether claims are exaggerated or out of scope, is judged by the independent reviewer in M6.

M4 provides opinion candidates, evidence arrangements, and analysis structure; it does not produce final paper conclusions ready for direct submission. Review comments are handled by the user and do not retroactively change the Card's acceptance status.

### M5 Opportunity Analysis

![Figure 6 · M5 workflow](assets/figure-06.png)

*Figure 6 · M5 workflow*

Input → Output: Accepted Cards, comparison context, and author-stated limitations/future work → Tension candidates and sourced research gaps.

Dependencies: M3/M7 identify topics and fields, M9 executes opportunity identification, M6 verifies induction, M8 manages acceptance according to the independent contract of the opportunity object, M11 logs traces, and M14 receives quality signals. Opportunity induction review must be connected via the corresponding object-adapted contract and must not directly apply the Card scope.

Engine A compares objects, conditions, methods, metrics, statistical standards, and time ranges, proposing tensions where context is comparable but conclusions differ. Engine B only induces explicitly stated limitations, gaps, and future work by authors, preserving original sentences and sources; the absence of search results cannot prove that no one has researched a direction. Gaps can be clustered by topic, counted by mention frequency, and annotated with filling evidence provided by later literature.

Output is categorized as A: High-comparability tensions; B: Condition or method differences; C: Expression, metric, or conclusion type differences; D: User-confirmed ignore. When coverage is insufficient, output not_comparable or coverage_limited; do not fabricate A-level tensions. These categories are not active acceptance statuses.

M5 processes incrementally by topic, triggered by schedules, user actions, or affected feedback, rather than performing pairwise comparisons across the entire library. The user determines comparability and research value; if the ignore rate is abnormal, M14 checks condition extraction and false positives.

### M6 Verification

![Figure 7 · M6 workflow](assets/figure-07.png)

*Figure 7 · M6 workflow*

Input → Output: Cards or Analyses that have passed the deterministic pre-gate, corresponding evidence, and review contracts → Review receipts, issues, and disposition records.

Dependencies: M9 binds the reviewer, M11 records, and M14 aggregates quality; acceptance requests are sent to M8. Card and Analysis have separate scopes, using card_reviewer_primary and analysis_reviewer_primary respectively, and must belong to different model_family_id than their respective generators. Different vendors do not automatically imply model family independence.

Card workflow: One atomic Full → closure if no issues; one consolidated Repair for fixable issues → one atomic Delta → cross-field residual scan and final mechanical gate. RepairPatch only modifies authorized paths, verifies old value hashes, and ensures zero non-target changes; Delta only proves modified items and direct dependencies, not a full-text re-pass.

Review expands by atomic claim and conservative single-document fact identity; confirmed occurrences of the same fact are handled together. Only precise, unique, and separable targets can be logically deleted; vague similarities only suggest candidates, with ambiguity escalated to manual review. Retained values require evidence or a downgrade explanation; deleted values enter the tombstone. Complete closure is complete, separable omissions are passed_with_omissions, and unresolved core issues remain failed or pending manual decision.

Analysis workflow: Review is determined by risk, MUST/EXEMPT, and sampling contracts; skipping requires a reason or pending verification item. The reviewer only receives claims and cited evidence for targeted falsification, not the generator's reasoning process. Sent requests with unknown results cannot be faked as successful via restart or blind resending. See Chapter 6 for desktop limited revision and objection deletion rules.

### M7 Weights

![Figure 8 · M7 workflow](assets/figure-08.png)

*Figure 8 · M7 workflow*

Input → Output: Document metadata and current query → Retrieval ranking score.

Dependencies: M9 provides field embedding calls; M11 records logs; M3 consumes results. Derived types originate from M10; lifting derived downweights requires decision rationale and status events.

| Group | Provider | Meaning |
| --- | --- | --- |
| Relevance | SimilarityWeight | Vector similarity to the current query |
| Relevance | SemanticWeight | Field relevance and relative value calculated per query dimension |
| Authority/Personal Value | ManualWeight | Importance of user-provided rationale |
| Authority/Personal Value | RuleWeight | Rule signals such as year, journal, citation count, and type; missing data does not imply low quality |
| Authority/Personal Value | NoteWeight | User notes, annotations, and other research traces |
| Authority/Personal Value | CitationWeight | Frequency and purpose of citing this material in user research |

Composite relationship: FinalScore = weighted sum of relevance group + DerivedPenalty × weighted sum of authority group.

Coefficients are adjustable configurations, not optimal constants for all disciplines. SemanticWeight is calculated at retrieval time based on the current query, using soft assignment to preserve other fields; missing fields do not cause systematic low scores. Card field vectors are generated separately from M1e source text vectors. The standard field index only accepts Cards that have passed M6 convergence and are active in M8; pre-computation must use an isolated index.

DerivedPenalty only reduces the authority score of derived entries; it does not penalize true relevance. Cross-document synthesis carries the heaviest penalty, conceptual relationships are moderate, and original annotations are the lightest. Reversal requires documented rationale and cannot be achieved by increasing ManualWeight to substitute credibility. M14 monitors drift by tracking the frequency with which derived entries displace their original sources in the top-k results; ratios and review dates serve only as auxiliary metrics. Field credibility and coverage status remain independently displayed and are not automatically converted into global weights.

### M8 State Machine

![Figure 9 · M8 workflow](assets/figure-09.png)

*Figure 9 · M8 workflow*

Input → Output: Objects, change requests with explicit scope and rationale → Validated state transitions and events.

The review_status of a Card is pending, active, or quarantined. Objects that have not been accepted are available for review but must not enter normal retrieval, tension analysis, or automatic feedback consumption. Opportunities and knowledge feedback use their respective contracts; the request constructor for Analysis cannot substitute for the missing general production state contract.

M8 is not responsible for quality judgment, technical retries, business rework, or operational recovery. It generates immutable state_transition_event, which M11 appends and persists, and M13 associates with version and lineage. Lifecycle heat, version status, Card completion, and release status are not written into the Card's three states. Validation evidence must reference the M6 receipt and the basis for the current transition; it cannot rely solely on the active label.

### M9 Model Gateway and Execution Channels

![Figure 10 · M9 workflow](assets/figure-10.png)

*Figure 10 · M9 workflow*

Input → Output: Logical roles, task content, and constraints → Model results from authorized channels, or explicit failure/deferred execution results.

Business modules depend only on logical roles. M9 freezes the profile and route snapshot at the start of a run, binding the provider, model family, Prompt/schema, parameters, execution location, ownership, region, quality evidence, budget, and rollback target. Model changes prioritize configuration updates over modifying business responsibilities.

Key roles include embedding_primary, card_distiller_primary, card_reviewer_primary, analysis_primary, analysis_reviewer_primary, opportunity_analysis_primary, change_digest_primary, ocr_page, and reranker. Reviewers for Card and Analysis do not share an ambiguous unified name.

Execution chain: Role request → ExecutionCore compiles work package → Adapter Registry selects channel → Isolated JobRunner → API, controlled CLI, or local adapter → Results and per-attempt evidence.

JobRunner manages processes, deadlines, heartbeats, cancellation, recovery, and work package boundaries. The gateway handles call-level technical recovery; business rework remains the responsibility of M2/M6. Development assistant sessions are isolated from runtime sessions. P05 has incorporated seven types of controlled CLI adapters, but the ability to execute commands only indicates channel existence and does not substitute for per-role quality qualification.

Configuration governance. Runtime availability is UNKNOWN/AVAILABLE/DEGRADED/MISSING, distinct from governance status CANDIDATE/APPROVED/ACTIVE/DEPRECATED/QUARANTINED/RETIRED. M14 provides role evaluation; users or approved rules make decisions; M9 registers execution; M12 stores the rationale. Quality-cost routing proposes candidates only from the qualified scope; it may abstain if no available qualification exists.

Identity and fallback. Each save records the requested and returned provider/model. Formal reviews block execution per contract if identity mismatch or unverifiability is detected. Silent model swapping within a batch is prohibited; fallback is defined per role and preserves review family independence, ownership, and regional restrictions. Actual paths and hashes of CLI components are verified during pre-checks and before startup. Embedding switches default to a new index; reuse is allowed only after compatibility proof is established.

Dependency failures. Transient network/rate-limiting issues are recovered within budget. Credential, quota, service shutdown, or configuration issues pause affected stages, prompting the module, dependency, reason, and handling method, without stalling independent features. Automatic tasks do not imply mandatory API usage, nor do they allow automatic conversion to paid services after timeout.

Regional policy. The design requires each provider to bind a valid supported-region policy with same-route egress evidence; unknown, expired, or mismatched states must not be released to production. P07 has strengthened HTTPS probes, explicit proxy same-routing, and pre-send verification, but country determination remains CN block/non-CN pass; a provider-specific supported-country whitelist remains a gap.

Prompt versions, caching capabilities, tiers, Thinking, and billing rules are managed by controlled configuration. The maximum tier must be explicitly selected; optional tiers do not prove that the corresponding model has passed qualification. Shared constraints on ownership, billing, and caching are detailed in Chapter 5.

### M10 Controlled Knowledge Feedback

![Figure 11 · M10 workflow](assets/figure-11.png)

*Figure 11 · M10 workflow*

Input → Output: User-selected reusable content and rationale → pending candidates with is_derived, source version, and reasoning.

Dependencies: M8 enforces admission for the target scope, M7 applies derived weighting, M13 stores versions and parent chains, M12 associates decision rationale, M9 executes approved curation tasks, M11 records audit trails, and M14 receives health signals.

Objects eligible for knowledge feedback include annotations, judgments, hypotheses, stable conceptual relationships, and cross-document synthesis. User raw records can be automatically saved or automatically curated into candidates; saving does not equal formal feedback. All types require user confirmation before entering active, canonical, or normal knowledge consumption; one-off Q&A and unconfirmed AI drafts cannot enter the pool directly.

The workflow is: user selects object → forms candidate, source, and rationale → checks allowed destinations and precise changes → user accepts/modifies/rejects/defers → necessary review and admission → version storage and receipt. Objects are bound to input artifact IDs/hashes and execution configuration; upstream changes trigger recalculation of compatibility or staleness status.

Knowledge feedback admission and derived down-weighting separately address "whether it can be used" and "what the ranking weight is." A confirmed summary remains a secondary artifact and does not automatically acquire the authority of original literature due to human confirmation.

### M11 Logs and Terminal State Ledger

![Figure 12 · M11 workflow](assets/figure-12.png)

*Figure 12 · M11 workflow*

Input → Output: Events from modules, gateways, and runners → contextualized runtime logs, final state records, and query views.

M11 is a passive recording layer; it does not execute watchdogs, rework, or state judgments. It displays progress and errors to users and preserves module, object, stage, dependency, cause, evidence location, and disposition for troubleshooting. M12/M14/M15 read according to their respective purposes; operational logs are not fed into literature knowledge retrieval.

| Ledger | Stored Content |
| --- | --- |
| Attempt Ledger | Immutable terminal record of each actual attempt, including route, result, and evidence |
| Paper Outcome Ledger | Terminal state and final artifacts for each paper in every run; redoing creates a new run |
| Publication Ledger | Publication requests, selection lists, results, and independent receipts |

Attempt success, paper processing pass, and publication success are tracked separately. Subsequent successes append records without overwriting prior failures; corrections use an Amendment or explicit successor relationship. Run heartbeats enter the event stream or run snapshot and do not write back to sealed attempts.

External calls are logged in phases: before sending, freeze request identity, route/region, behavior hash, permissions, and budget; after sending, record actual return identity, usage, cost basis, latency, and result. Fields for which old data cannot be recovered are marked as unknown; do not guess values or automatically rerun paid requests to supplement evidence. Recovery and concurrent writes are declared according to the ledger contract and actual test scope.

### M12 Design Decision Log

![Figure 13 · M12 workflow](assets/figure-13.png)

*Figure 13 · M12 workflow*

Input → Output: A significant choice and its context → A searchable, citable, and version-controlled DDL.

Record context, primary alternatives, final choice, reasons for rejection, affected modules, basis/feedback, status, and review date. M11 describes what happened; M12 explains why this choice was made. Do not turn every operational log entry into a decision log.

Applicable items include model acceptance, isolation, canary release, rollback, re-embedding, OCR preparation, lineage exceptions, feedback basis, canonical pointer movement, and publication/withdrawal. Actual events are recorded by M11/M13; M12 references events and explains the trade-offs. Decision changes establish a successor, preserving the original decision and its rationale at the time.

### M13 Data Lifecycle

![Figure 14 · M13 workflow](assets/figure-14.png)

*Figure 14 · M13 workflow*

Input → Output: Data objects and authorized changes → New versions, lineage relationships, and lifecycle records, with old evidence preserved.

Dependencies: Producers of each artifact submit versions; M8 owns and enforces acceptance, M11 stores events, and M13 associates immutable artifacts, parent chains, status events, and compatibility relationships. Git and backups serve as the foundation for storage recovery but do not replace lifecycle semantics.

The Artifact Envelope supports at least immutable IDs, content hashes, type/schema, run, route snapshot, source scope, creation time, and multiple parent_artifacts. The Registry manages these identities and relationships; query projections can be reconstructed, but the production Registry/JSONL remains authoritative.

| Lifecycle | Management Implications |
| --- | --- |
| Usage: Active / Cold / Archive | Usage heat and retrieval priority; does not imply deletion |
| Version: Current / Superseded / History | Version supersession and historical preservation |
| derived.version_status | Draft / current / superseded / historical status of derived objects |

These axes do not replace the pending/active/quarantined states in M8. Business revisions do not directly overwrite the original, text, Card, feedback, or decisions; deletion follows the authorized disposal process in Chapter 7.

Compatibility and Publication. Determine compatible/stale status based on exact parent version, schema, and source scope; retain lineage_unknown for missing parent chains and do not guess matches. A new Card does not automatically grant new combination eligibility to an old Analysis. Freeze the manifest before publication, separately checking structural compatibility, coverage, paper outcome, user choices, and cost decisions; execute publication only after confirming the exact object set and obtaining authorization, leaving a trail via an independent receipt and Publication Ledger. Canonical moves, withdrawals, and replacements are recorded as separate events.

The Registry, compatibility engine, Analysis request construction, and publication dry-run for P02 belong to different construction scopes; the dry-run did not execute a formal publication. P06 has established historical migration and combination query capabilities, but has not authorized real historical batch migration, production index switching, or pointer moves.

### M14 Quality Assurance

![Figure 15 · M14 workflow](assets/figure-15.png)

*Figure 15 · M14 workflow*

Input → Output: Active library, audit receipts, run and lineage records → Quality reports, degradation alerts, and disposal recommendations.

Dependencies: M6 performs single-pass content verification, M1 provides the basis for conversion quality, M9 invokes the corresponding roles, M11 supplies runtime facts, and M12 records gold standards and the rationale for configuration changes; receives quality signals from M5/M10, etc.

M14 employs random sampling, risk-based sampling, and sentinel regression. Content with high weight, high citation frequency, intended for papers, or with prior review discrepancies is prioritized. After changing models, Prompts, or related rules, re-verify using the corresponding test packages; full re-review of all content is not required every time.

| Quality Axis | Check Content |
| --- | --- |
| literal_fidelity | Whether text, numbers, units, and citations are faithful to the source |
| evidence_scope_coverage | Whether evidence covers the conditions, scope, and comparison targets of the claims |
| cross_artifact_consistency | Whether Card, Pack, Analysis, review, and published artifacts are consistent |
| scientific_plausibility | Whether statistical criteria, units, samples, timeframes, and orders of magnitude are reasonable |
| lineage_freshness | Whether the parent artifact combination is compatible and not stale |

The five axes record evidence and outcomes separately, preventing a single overall PASS from masking unassessed items. Routine issues receive limitations and review prompts; core unsupported cases, critical provenance failures, or misleading values trigger machine-readable blocks or human recommendations. M14 does not edit originals, does not alter M8 status, and does not establish a second production pipeline.

Numerical values and page roles. Before direct comparison, align metric, unit, conditions, statistical type, time, sample, and comparator; if alignment is impossible, mark as not_directly_comparable. Reasonableness checks provide prompts only and do not automatically correct raw data. OCR distinguishes body text, cover, table of contents, headers/footers, references, and unknown roles, ensuring that numbers from arbitrary pages are not treated as body text facts.

Sentinels and gold standards. Maintain extraction/distillation, reviewer miss and mutation, retrieval/embedding, and OCR tests by role. Each revision freezes the complete Form, Gold, scorer, and manifest, and reuses them across models; Gold is excluded from test model requests. Corrections must preserve original values, sources, dates, and new revisions, with audit trails maintained by M12. Public commitments or locators cannot substitute for a complete exam pack.

Reference packs are tracked by ownership: only precisely declassified REFERENCE_EXAM_PACK may enter Git; PRIVATE_HOLDOUT_PACK does not enter Git; FORMAL_LOCAL_REFERENCE_PACK may be used locally in full but remains isolated as a whole, and blind test eligibility is not claimed based on it.

Knowledge base health metrics retain indicators for unreviewed, failed, expired, broken links, and derivative displacement. The alternative task completion gate for original P02/T07 did not convert original B ERROR or 12 five-axis not_assessed items into quality passes.

### M15 Research Change Log

![Figure 16 · M15 workflow](assets/figure-16.png)

*Figure 16 · M15 workflow*

Input → Output: Research events over a period → daily, weekly, and monthly change reports.

Normalize and deduplicate from sources such as M11/M8/M13 to generate immutable ResearchEvents, then organize via approved methods such as change_digest_primary. Reports preserve event sources and aggregation provenance, separately expressing trial outcomes, paper final states, releases, and configuration changes.

Daily reports focus on new, modified, pending review, and failed items; weekly reports focus on significant progress, literature, incomplete reviews, and potential tensions; monthly reports focus on structural changes and long-term pending items. M15 reports only changes, risks, and pending items, does not form final research conclusions, and does not alter knowledge acceptance status.

### M16 Research Intelligence Radar

![Figure 17 · M16 workflow](assets/figure-17.png)

*Figure 17 · M16 workflow*

Input → Output: Topics, keywords, authors, journals, or citation chains → external candidates with sources and recommendation reasons.

The actual sources in this version are arXiv and Crossref; other sources still require independent development. External connections and receipts are managed by the connection framework; M9 is invoked only when model judgment is required, not treating all bibliographic HTTP requests as model calls.

Identity clustering automatically deduplicates only for conflict-free exact DOI/arXiv ID/PMID; weak evidence such as titles/authors or identifier conflicts enter OPEN for human review. Preprints, conference versions, AAM, VOR, and arXiv versions of the same work are retained as different manifestations.

Novelty is recorded as TRUE/FALSE/UNKNOWN across four axes: publication, first-seen, library, and direction. Abstract-level direction estimates retain their estimate labels; UNKNOWN is not rewritten as TRUE. Source canaries, Shadow, and controlled activation are verified and approved separately.

Two human gates: The first approval only permits the isolated M1→M2→M6 pipeline to generate a pending Candidate Card, without generating an Analysis; the second approval is required to formally promote the exact set. Promotion uses an idempotent, WAL-based, dual-lock, compensation, and receipt-last protocol; failures must not leave a success receipt. Offline synthesis tests do not indicate that a real promotion has occurred.

### M17 Active Knowledge Acquisition

![Figure 18 · M17 workflow](assets/figure-18.png)

*Figure 18 · M17 workflow*

Input → Output: Research directions, finite candidates, and authorized behavioral signals → Evidence-based recommendations and pending policy suggestions for confirmation.

M17 builds on M16, organizing a finite Slate using five channels: CORE, ADJACENT, BRIDGE, HORIZON, and COVERAGE_REPAIR. It preserves channel components, whitespace, and coverage/polarization information, and does not create a unified score that masks differences.

Exposure, suppression, and re-emergence are written to an append-only Exposure Ledger. Suppression is bound to work_cluster_id, direction_id, and direction_revision, and does not propagate to similar text, vector neighbors, or other directions; re-emergence requires new evidence. Weak signals reaching a threshold only generate a policy proposal pending human confirmation and not yet applied.

M17 can discover and recommend, but does not automatically import, assign high weights, change strategies, or release; it does not handle writing, file output, or publication execution. The desktop's recent interest auto-recommendation still has unresolved implementation gaps.

## V. Shared Rules

### 5.1 Status must include object and scope

| Status Object | Status or Evidence | Owner/Interpretation |
| --- | --- | --- |
| Capability Design | proposed / approved / superseded | Design approval status |
| Capability Implementation | not_started / partial / implemented | Whether code or contracts exist |
| Capability Verification | untested / tested / qualified | Valid only within the scope of corresponding evidence |
| Capability Deployment | disabled / pilot / active / retired | Permitted scope of actual usage |
| Task Execution | frozen / running / completed / aborted / invalidated | Runner manages execution lifecycle |
| Task Acceptance | PASS / FAIL / NOT_ASSESSED | Separated from runtime and machine verification_result |
| Card acceptance | pending / active / quarantined | M8 executes based on scope and provenance |
| Card completeness | pending_review / complete / passed_with_omissions | Review disposition closure, not extraction coverage |
| Card quality | Schema, extraction coverage, field reliability, and validation evidence are recorded separately | validation_evidence is derived from M6/M8 provenance; do not create a separate persistent validation_status |
| Publication | not_requested / not_published / published / publication_failed | M11/M13 store independent publication facts |

See M9 for the availability/governance axis of model profiles, and M13 for the data usage/versioning axis. Similar names do not imply the same fact; no display or statistic may replace these states with a single unnamed active or completed status.

### 5.2 Data Disposition Authority Across the Full Chain

Data ownership and sensitivity are distinct attributes. Current Cards use self / entrusted; self-owned content is used according to approved configurations, while entrusted data, data without disposition rights, or data with unknown ownership defaults to local processing. If local capabilities are insufficient, the corresponding invocation is halted. Additional authorization usage generates auditable policy/decision records and does not independently add schema enumerations.

Whenever data is handed to a model—including OCR, embedding, distillation, analysis, review, caching, and controlled CLI—ownership is checked first. OCR checks ownership before encoding and upload; Context Pack aggregation and downstream transmission must not lose constraints. Launching channels locally, using local ports, or switching to another vendor does not substitute for proof of no external transmission.

Session collection, model refinement/external transmission, and formal pool entry are authorized separately. Diagnostics use only approved probe materials and must not test external capabilities with entrusted data. Credentials are managed by controlled credential storage; configurations store only references, and logs and exports contain no plaintext keys/tokens, cookies, or browser-storage values.

### 5.3 Partial Output and Failure

Ordinary and separable issues allow degradation, omission, needs_review, or tombstone, retaining usable parts and listing gaps; blocking occurs only when core evidence fails, core disposition is unclosed, critical provenance is incompatible, or output would be misleading. Quality judgments are formed by the corresponding owner, and status changes are executed by the implemented M8 scope. Responsibilities and budgets for technical retries, semantic rework, and new runs cannot substitute for one another.

### 5.4 Execution Classification and Cost Evidence

Each invocation separately records process_transport, inference_location, access_mode, and billing_mode. An API issued via CLI remains an API; a local process does not imply local inference. Per-invocation fee applicability meeting subscription_session and subscription_entitlement can be N/A, while local computation separately records resource consumption.

Fee applicability, whether the amount is known, and the amount source are distinct fields. If the current currency amount for a subscription CLI is unavailable, it can be UNKNOWN, which does not conflict with applicability N/A; neither can be filled as 0 or free. APIs use the price for the corresponding valid date, with actual_cost, estimates, and missing prices separated; failures and retries are still accounted for.

The current desktop cost view deduplicates by run/stage/physical attempt, covers only connected receipts for the current profile, and distinguishes ACTUAL, ESTIMATED, UNKNOWN, and CONFLICT by currency; it is not a complete account bill. New backend fields do not automatically imply frontend display.

Usage retains cached hit/miss input, total output, reasoning, visible output, and full latency. Reasoning is a subset of output and cannot be added again; when splittable, visible output = output − reasoning, and missing values remain unknown. Cumulative session usage must be diffed field-by-field against the same precise session and previous basis; cumulative regression or inconsistency is not treated as verified current usage.

### 5.5 Input Caching and Session Continuation

Caching does not reuse review conclusions. Stable rules and approved sources for the same article are placed first, while dynamic tasks and precise repair targets are placed last, allowing vendors to reuse input computation; each task still sends a new request, generates new output, and executes original schema, provenance, and review checks. This desktop path does not reuse Phase1's old answer cache.

Caching capabilities are identified by API, model, and actual channel. Unknown capabilities remain UNKNOWN, and unconfirmed vendor parameters are not sent. Repairs only organize originally approved candidate sources; they must not stuff full text into short repairs to force a hit, nor expand the scope of references allowed by the target.

The verified Codex route uses exec to create sessions and resume with an exact UUID, disabling --last. Role, model/profile, source, service, workspace, transport schema, and implementation revision participate in identity binding; each run still independently verifies business schema, UUID, usage, and receipts. Application idle reuse duration is not equivalent to server-side TTL. On expiry, prior failure, incompleteness, or unknown identity, the system performs a cold request per contract, creates an isolated scope, or blocks; concurrent lock conflicts and corrupted records must not reuse old sessions.

This round of testing is limited to the DeepSeek API and Codex CLI. Shared code and evidence for C59–C65 have been formalized and pushed, but no repackaging, deployment, or P08 completion has occurred based on this. Cost comparisons must retain original costs, wall-clock time, workload, and output volume; pricing counterfactuals do not equal causal code benefits, and full hit rates or universal speedups are not guaranteed.

## 6. Desktop Application and Interaction

### 6.1 Application Layer

The Windows stack uses pywebview + PyInstaller + Edge WebView2 over the Python ApplicationFacade. The design baseline records pywebview 6.2.1 and PyInstaller 6.22.2; each exact build follows its frozen dependency manifest. Electron is only a conditional alternative triggered by later evidence and a design decision record.

Call chain: HTML/CSS/JS → IPC command/event → Application Service → ApplicationFacade → business modules. Control Store persists state for jobs, views, and recovery, rebuilding projections after disconnection or restart. The UI does not directly write module state; the library reads canonical sources, artifacts, and provenance, without duplicating a second knowledge authority.

Pages use the adopted HTML as the visual baseline, retaining porting mappings, and verify screenshots, styles, layout, and interaction under identical WebView2, viewport, DPI, theme, font, and fixture conditions. FakeFacade is used only for visual flow verification; real state persistence and backend calls are verified separately; normal startup does not use sample data to impersonate user data.

### 6.2 Main Interfaces

| Interface | Core Behavior and Boundaries |
| --- | --- |
| Settings | Three interface languages, model tiers, Thinking, credential references, and capability status; verify first then apply atomically, restoring old values on failure |
| Inbox/Current Task | Manage intake, deduplication, conflicts, dispatch, and nodes; pause, cancel, resume, retry, or override configuration per contract |
| Messages/Work Log | Distinguish read from acknowledged; use stable locators to navigate tasks, nodes, and artifacts; reports retain event provenance |
| Library/Session Store | Read sources and output projections; separate permissions for historical collection and knowledge ingestion, with explicit source types |
| Model Exams/Costs | Display specific role, Form, version, and time-based eligibility; show costs by evidence type, without presenting them as complete invoices |
| Radar/Research Reports | Inherit M16/M17 dual gates and M15 change-only reporting boundaries; do not assume full functionality is enabled merely because the page exists |

The interface separately displays runtime, paper results, releases, structure, audit basis, field reliability, coverage, and provenance. passed_with_omissions shows available artifacts and specific reasons for deletions; unreviewed items, unknown provenance, and stale combinations are clearly indicated. Technical retries and business rework are counted separately.

CapabilityState availability is ready/disabled/not_configured/not_applicable/blocked, qualification is qualified/not_qualified/not_assessed, and effective_enabled is an independent boolean. If required capabilities are unavailable, block execution; if optional capabilities are unavailable, list degradations.

Normal, Advanced, and Developer modes do not alter data or production permissions. Safe Mode reverts to read-only official_core, does not load custom code, does not merge overlays, keeps user data read-only, and disables production writers. doctor/support provides diagnostics and public safe exports, without implying repairs or enabling authorization.

### 6.3 Desktop Nine-Node Execution Graph

| Node | Responsibility | Key Dependencies |
| --- | --- | --- |
| 01 DOCUMENT_INGEST | Document Processing | M1 transformation, cleaning, chunking, and provenance |
| 02 CHUNK_EMBEDDING | Source Text Vectorization | Fixes the vector space for this run |
| 03 CARD_DISTILL | Generate Cards | M2 and local hard gates |
| 04 CARD_CROSS_CHECK | Verify Cards | Triggered by the frozen review contract |
| 05 CARD_ADMISSION | Card Acceptance | M8 Basis and Scope |
| 06 CONTEXT_PACK | Organize Analysis Materials | Retrieval space from 02, capacity budget from 07 |
| 07 ANALYSIS | Generate Analysis | M4 and current Pack |
| 08 JUDGMENT_CROSS_CHECK | Verify Analysis | Independent review or explicit skip rationale |
| 09 HUMAN_FINAL | Human Judgment | Present evidence, objections, and omissions |

06 is not a newly added tenth reranker node. Separating embedding and reranker in the configuration layer does not imply that the runtime has enabled the latter; currently, the desktop bridge.rerank is disabled and falls back to distance ordering, and the OCR adapter is not automatically enabled based on the eligibility of other modules. Long-running tasks have budgets set according to capabilities, using spool/resume, heartbeats, and streaming IPC, while retaining limits on cancellation, costs, and retries.

### 6.4 Limited Process for Desktop Review Maintenance

Card: Distillation → Initial Review → One Round of Issue Remediation → Delta Re-review of Changed Items Only → Removal of Tracing Logic if Necessary → Acceptance based on Existing Evidence. passed_with_omissions is a usable result with omissions and should not be misjudged as a total Card failure.

Analysis: Generation → Representative Independent Review → One Round of Exact-Target Revision → Independent Delta → Pass or Restricted Logic Removal → HumanReview. Unchanged claim hashes are preserved; only independent judgments that remain disputed after a true Delta have eligibility for removal, while missing, expired, or deferred reviews do not count. At least one piece of literature support must be retained, and the last support for an existing facet must not be deleted, otherwise blocking occurs. Original text, revisions, sources, objections, lineage, and tombstones are all preserved.

Representative review does not imply that all claims have been reviewed. For C63 long-form comparisons, each group has 42 judgments, while standard review selects only 2; true revisions passed, and Analysis judgments were not actually deleted, with the removal branch evidenced only by offline test cases. The aforementioned desktop maintenance does not prove integration with the complete P06 Full/Repair/Delta or atomic publishing components.

### 6.5 Session Collection, Refinement, and Built-in Documentation

The session library uses PREFLIGHT to verify identity and authorization, FULL to save history, and INCREMENTAL to update from the successful cursor; it retains immutable snapshots, conversation graphs, last-complete status, lineage, and heartbeats. Long-running tasks with continuous progress cannot be deemed inactive solely because they were created earlier.

Users explicitly select Edge, Chrome, or Brave, and the browser, extension version/directory, and session identity are recorded. Read-only collection is limited to authorized official exports/APIs, UI visible in the existing login state, or exact local CLI roots; it does not read cookie/storage values, does not independently log in or handle 2FA, and does not call hidden interfaces. If the browser is not installed, it is reported as an environment deficiency.

After collection, same-file deduplication and full or incremental refinement are performed, forming 11 types of refinement candidates and 5 types of controlled destination selections. Users can ACCEPT, EDIT, REJECT, or DEFER; collection authorization does not automatically authorize external transmission or pool entry. Conversation content is not a source of literature facts, and entering the knowledge pool still requires the corresponding destination contract and review.

P08 has limited FULL evidence for Gemini, which cannot be extrapolated to the entire library of any account. The out-of-order, identity, and tail-only refinement issues in build132 are retained as a failure baseline; build133/134 added full citations and browser identity, but fresh capture and scrolling boundaries still await verification. Collection completeness and scrolling boundaries are recorded separately as PASS/PARTIAL.

Built-in operational documentation uses independent HTML/CSS/JS and replaceable Markdown; the prototype contains 6 chapters and 24 articles, loads offline, and restricts external resources. Prototype check passing does not imply integration into Memorive.exe; “Confirm Review” only indicates that it has been read/reviewed and does not directly change authority, formal pool entry, or publication.

### 6.6 Native Interaction, Leaderboards, and Installation Entry

Native windows, menus, 2D desktop assistant, status animations, and single EXE are verified item by item; local window fixes cannot be extended to claim that all cross-screen, Snap, DPI, or exit behaviors have passed.

The Console prioritizes event processing, applies throttling and bounded backoff to heavy snapshots, and reduces re-projection during active business operations. Shutdown sequentially waits for UI requests, IPC/Console acknowledgments, cleanup, and process exit; connection labels or single HTTP timeouts do not substitute for actual exit evidence.

External model leaderboards are for reference only and do not replace Memorive role examinations. Data sources and versions must be locatable, and strict TLS must be maintained. A fresh isolated data directory with no cache and refresh disabled displays NO_DATA_IN_ISOLATED_TEST; request failures are FETCH_FAILED, and old projections are STALE_PROJECTION. Normal online source chains, data freshness, and Console connections are verified separately.

Installer browser callbacks must verify source/navigation before scheduling the native folder dialog; direct manipulation in unsafe callbacks or incorrect threads is prohibited. Author machine interactions do not replace testing on user laptops or clean Windows environments.

## 7. Infrastructure, Development, and Maintenance

### 7.1 Directory and Recovery

Literature is organized by single-article folders, with names formatted as "[First Author Surname + Formal Volume/Issue/Year] Title". Illegal characters are cleaned, and names are appropriately truncated, with the full title stored in metadata. Critical confirmations regarding authors and identity retain human decision-making. File prefixes include [PDF], [Original], [RawMD], [CleanMD], [Chunks], [Card], and [Fig]; body figures and daily attachments are managed separately.

Code repositories, isolated sandboxes, operations records, and path-sensitive vector stores have fixed locations; temporary top-level categories are not created for one-off experiments. The product application root, version directories, user data root, Control Store, and canonical data are separated. Materials and Markdown remain independently readable when the system is unavailable; manual tool bypasses do not bypass data disposition authority.

Backups distinguish between working copies, local mirrors, and authorized off-site assets. GitHub only receives authorized exact-sets; private Gold, restricted sources, complete PDFs, secrets, and materials not released to the counterpart must not be uploaded to the cloud as exceptions due to private repositories or encryption. The existence of a backup does not imply recovery has been verified; local backup and recovery for restricted data are verified separately.

### 7.2 Authority and Collaboration

CurrentAuthorityMatrix selects a unique selected_current for each authority object; low revisions retain HISTORICAL_IMMUTABLE. EVIDENCE_LOCATOR_ONLY containers provide only location and do not participate in current authority competition. Development assistants and MCP receive only controlled access required for tasks, not permanent read/write access to the entire Vault.

GitHub relay only transfers authorized relay_shared exact-sets, freezing input/return commits and verifying remote OIDs. Authorization for relay, formalization, merge, push, deployment, and cleanup is handled separately.

Task branches use a unique append-only branch log; when no branch is used, run_journal is employed. The central daily summary is aggregated into stable fragments after a single-writer pre-check, with failures marked as CENTRAL_LOG_PENDING. Automatic log commits do not automatically authorize product commits or pushes, and product work logs are not mixed with central development logs.

### 7.3 Asset Disposition and Capacity Governance

Archiving, pre-deletion isolation, and final deletion are distinct actions. First, perform a read-only inventory of file hashes, active references, retention reasons, and frozen evidence relationships to form a keep/cleanup manifest; isolate by a single batch_id, and after independent review and recoverability verification, execute final deletion authorization from the same batch and retain receipts.

Formal code, the sole release candidate, manifests, logs, and immutable evidence are handled according to retention rules. Duplicate bytes or reclaimable capacity estimates only indicate potential redundancy and do not grant deletion permission; build* wildcards and cross-root bulk cleanup are prohibited. The historical practice of deleting before isolating is retained as a process deviation and does not become a subsequent convention.

### 7.4 Development, Freezing, and Acceptance

The basis for starting work is the current manual, the unique run/branch, fresh readiness, and an explicit user command. First, perform an offline, low-cost equivalent full-chain rehearsal, freeze the rehearsal receipt, and then conduct high-cost call pre-checks and budget reserves. Substantive changes invalidate the old rehearsal; the rehearsal does not consume formal Gold and does not substitute for target model qualification.

Before formal acceptance, freeze the code, tester, input set, and configuration. A normal termination is recorded as completed; a cancellation or external abort with an intact frozen boundary is recorded as aborted; modifying the frozen boundary during execution is recorded as invalidated. The latter two do not enter the canonical set or release candidate, but all logs, artifacts, failures, and manifests are still retained. Recovery uses only checkpoints matching the frozen hash.

Frozen B cannot modify code, problem statements, or criteria in place. If a fix is needed, return to the authorized A scope, fix the failure and change surface, and create a new candidate and B; repeated sampling cannot turn a semantic FAIL into a PASS, and consumed holdout data cannot continue to be treated as blind testing. User exception acceptance is recorded separately as a successor and does not rewrite machine results.

When conditions for machine freezing, B, integrity, and consumer replay are met, TECHNICAL_HANDOFF_READY may be provided, allowing provisional consumption within the contract scope; upon invalidation, only the relevant downstream subtree is revoked. Technical handoff, user acceptance, formalization, and deployment are conducted separately.

P08 can advance across T within continuous construction commands precisely covered by H0, without waiting for per-T replies or commits; it still checks handoff and readiness each time. When reverting, return to the earliest responsible party and invalidate only the affected downstream. The T15 terminal manual is written only after T14 forms the exact output.

### 7.5 Updates, Installation, and Release

ModuleManifest/SystemUpdateManifest constrain dependencies, compatibility, updates, and rollbacks. Extensions are disabled by default; synthetic trust profiles and offline contract verification do not constitute a production trust root or a production extension installer.

Installation, upgrade, repair, rollback, uninstallation, and data migration are conducted around controlled version directories, manifests, atomic switching, and recoverable states. Application and user data are physically separated; uninstallation retains user data by default, and programs cannot implicitly approve data cleanup.

Release also requires a clean Windows environment, resource minimums, license/rights/bundling, third-party resource licensing, SBOM, security hardening, and signing qualification. A single EXE being runnable, PASS on the author's machine, PASS on a local VM, and legal distributability are different proofs.

TemplateRegistry resolves one canonical version per template_key and never falls back to an older revision. Registering a template does not qualify it to generate a finished artifact; only fully rendered and verified templates may be used. Others remain limited to skeleton, reference, or copy use. The formal-root OutputPlacementPlanner remains PLAN_ONLY with write_authorized=false.

## 8. Phase Status and Version Snapshot

This section records the evidence as of 24 September 2026. Phase completion, scoped verification, user acceptance, production qualification, and public release are separate decisions; this design document does not change any of them.

| Phase | Design or Construction Status | Boundaries Requiring Attention |
| --- | --- | --- |
| P00 | Overall Design v14 Simplified Draft; this release document is a revision candidate | No formal design approval yet |
| P01–P07 | Completion artifacts exist for each phase | Historical failures, retained debt, and restricted eligibility remain valid |
| P08 T01–T12 | User acceptance completed | Historical machine failures and supplementary evidence items are not rewritten by phase acceptance |
| P08 T13 | Memorive v1.01 / build136_r41_path_compatibility installation lifecycle and path compatibility specific PASS | T13 manual compliance and user acceptance are both NOT_ASSESSED; the candidate is for unsigned private testing only. |
| P08 T14–T15 | T14 has not started; T15 is pending subsequent inputs. | Clean Windows, resource and licensing, SBOM, security hardening, signing, and public release eligibility are incomplete. |

## 9. Open Issues and Future Boundaries

### 9.1 Data, Models, and Research Chain

Historical Card identity backfill, cross-section retrieval coverage, Marker real-run eligibility, private data local chain, model role production eligibility, complete quality reports, real historical migration, and long-term recommendation utility must still be evaluated according to their respective contracts and evidence. Existing module designs or partial passes do not automatically extend to these scopes.

### 9.2 Desktop and Release

The selected installer and main program for r41 remain TEST_ONLY_UNSIGNED_PRIVATE. A PASS for the installation lifecycle and path compatibility does not constitute completion of the entire T13 manual, nor does it constitute approval for public release. The official product source code has not yet completed migration, commit, or push for r41.

The installer UI update of 24 September 2026 passed only a targeted local review; no real installation transaction or laptop acceptance was rerun. The English and Japanese UI and typography patch also passed local review but has not been merged into the selected main application. The three-language built-in design and operation documents must enter a later offline candidate and be verified against the exact build. Clean Windows, minimum resources, licensing and SBOM, security hardening, signing qualification, full user acceptance, and a release decision remain for later stages.

## Appendix A: Module and Responsibility Overview

| Module | Core Responsibility |
| --- | --- |
| M1–M2 | Preserve originals, perform conversion and segmentation, then form traceable Card candidates |
| M3–M4 | Construct controlled Context Packs and generate analysis recommendations with citations |
| M5–M6 | Identify research opportunities and perform verification based on evidence |
| M7–M9 | Manage ranking weights, object states, and model execution channels |
| M10–M13 | Control knowledge feedback, final state records, design decisions, and data provenance |
| M14–M17 | Monitor quality, aggregate research changes, and discover and recommend external candidates |

## Appendix B: Status Terminology

| Term | Meaning in this document |
| --- | --- |
| Design Responsibility | Indicates expected ownership by a module; does not imply an available implementation exists. |
| Scoped Validation Passed | Tests are valid only within the specified objects, environment, and denominator. |
| User Acceptance | User acceptance conclusion for the precise scope; does not retroactively cover historical failures. |
| Production Eligibility | Independent determination that data, resource, security, and operational contracts are met. |
| Public Release | Requires a separate release decision; private test candidates do not automatically qualify. |

## Appendix C: Offline Documentation Resources

| Language | Local File | Purpose |
| --- | --- | --- |
| Simplified Chinese | zh-CN/design.md; zh-CN/design.pdf | Settings → About and Version → Design Document |
| English | en-US/design.md; en-US/design.pdf | Settings → About and Version → Design Document |
| Japanese | ja-JP/design.md; ja-JP/design.pdf | Settings → About and Version → Design Document |

## 10. Model leaderboard and public sources

The leaderboard supports external model selection. It should show model, provider, benchmark, input and output prices, currency, source date, and refresh status. Filters, sorting, and favorites retain their visible scope. A source failure must appear as a failure, not as evidence that no models exist. Public scores cover particular tasks and dates; they do not validate a model for a user's research workflow. [R1][R2]

LiveBench supplies public benchmark context. Its core repository publishes evaluation tasks and results; new-livebench presents release dated tables, subtasks, and cost versus quality views. Memorive uses these as data and information design references. Catalog prices belong to their stated provider and are not a user's charged amount. This attribution does not imply copied code, algorithms, or scores. [R1][R2]

| Element | Memorive presentation | Boundary |
| --- | --- | --- |
| Capability | Benchmark, score, and date | Do not merge unlike benchmarks |
| Price | Input/output per million tokens, currency | Reference price, not a bill |
| Status | Refresh time, source error, missing value | Keep unknown values unknown |

## 11. Session management and web sources

Session management brings together source filtering, search, list limits, detail reading, and refinement. Codex, Claude Code, imported files, and browser sources retain source identity, sync time, first and last messages, and read-only scope. Current-page capture, initial full collection, and later incremental collection need separate status and coverage. Refinement creates a new task and a reviewable artifact version; it does not overwrite the source session.

The interaction and information architecture reference CC Switch Session Manager: one place to browse and search sessions across tools and inspect their details. Memorive adds a research-material workflow with refinement tasks, library artifacts, and human review. The existing detailed design explicitly named this reference. The citation does not imply shared session storage or copied code. [R3][R4]

## 12. Usage and Cost bottom panel

The collapsible Usage and Cost bottom panel is an aggregate view. Its toolbar selects all records or an attributable current job, a 7-day, 30-day, or current-month range, model, and refresh. Its chart follows the selected cost (including estimates), request count, or token metric. When job attribution is incomplete, the current-job scope stays disabled; global spending cannot be assigned to one paper. Cost splits actual and estimated amounts, requests include failures and retries, and tokens split input and output. Missing evidence remains unknown.

The field grouping references CC Switch Usage Statistics, which separates API and CLI log sources and presents tokens, cache, estimated cost, filters, and request logs. Memorive's bottom panel has its own filters and chart; its detailed billing list is a separate view. A per-answer receipt below research chat is another distinct view. The citation does not claim copied proxy interception or every CC Switch statistic. [R5]

| Field | Display rule | Cannot infer |
| --- | --- | --- |
| Scope | Keep all records and current job separate | Global cost is not one job's cost |
| Cost and usage | Split actual/estimated, failed/retried requests, input/output | Unknown is not zero; estimate is not settled |
| Route | Mark API, CLI, or local | A connection pass is not quality approval |

## 13. Referenced projects and scope

These GitHub references identify specific data sources or design comparisons. They do not establish code reuse, licensing status, or historical implementation provenance on their own.

| ID | Project and URL | Scope |
| --- | --- | --- |
| R1 | [LiveBench/LiveBench](https://github.com/LiveBench/LiveBench) | Public benchmark tasks and results |
| R2 | [LiveBench/new-livebench](https://github.com/LiveBench/new-livebench) | Release dated, subtask and cost views |
| R3 | [farion1231/cc-switch](https://github.com/farion1231/cc-switch) | Cross-tool session architecture |
| R4 | [CC Switch Session Manager](https://github.com/farion1231/cc-switch/blob/main/session-manager.md) | Session list and detail interactions |
| R5 | [CC Switch Usage Statistics](https://github.com/farion1231/cc-switch/blob/main/docs/user-manual/en/4-proxy/4.4-usage.md) | Usage, cache, and estimated cost presentation |
