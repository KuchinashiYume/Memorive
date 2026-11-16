# Developing Memorive

This guide is for changing the code: where things are, how to run the tests, and which file to open
for a given change. Building the installer is described in [BUILDING.md](BUILDING.md).

## Folders

| Folder | What it holds |
| --- | --- |
| `source/` | The Python code of the desktop app, one package per feature (`memorive_research_workspace`, `memorive_library`, `memorive_settings`, `memorive/models/execution`, ...). `runtime.py` holds `ProductApi`, which receives every call from the window and passes it to the package that owns it. |
| `ui/` | The sources of the window's two pages. `tools/build_pages.py` builds `product/desktop/bundle_integrated.html` and `bundle_assistant.html` from them; see `ui/README.md`. |
| `tests/` | The tests. Run them with `tests/run_tests.py` (below). |
| `examples/` | Small programs that run one feature without the window. |
| `tools/` | Build, manifest and release tools. |

## The packages and how they fit together

Each package's `__init__.py` says what the package does, how the window or anything else reaches it,
and where its data lives. Read that first: a module that the window never imports may still be
loaded by name, run from the command line or called as a component (see "Code the window does not
start" below).

### How a feature runs

- **Documents**: the inbox (`memorive_inbox`) takes a file → the workflow (`memorive_workflow`) runs
  it through conversion, cleaning and chunking (`document_processing`), card extraction
  (`evidence_extraction`), card review (`evidence_review`, `knowledge_admission`) and analysis
  (`research_analysis`, `retrieval`) → the library (`memorive_library`) shows the results. The
  current-task page lists the jobs (`CurrentTaskProductController.core_job_rows`); the library gets
  a row for each delivered file from `memorive_library/core_products.py`.
- **Research Q&A**: the window (`memo.*`) → `memorive_research_workspace` chooses excerpts and builds
  the request → the settings service runs the model (`memorive/models/execution`,
  `memorive/models/validation/runner.py`) → the workspace saves the answer, its citations and any
  knowledge draft (`knowledge_feedback/research_ingest.py`).
- **Sessions**: imported or captured conversations (`memorive_sessions`, `memorive_session_archive`,
  `memorive_browser_companion`) → refinement through `memorive_workflow_bridge` → typed items for review.
  The browser extension's install, reload and sync, and importing session files, are in
  `memorive_browser_companion/desktop.py` (`BrowserCompanionDesktop`); `ProductApi` forwards the
  window's calls to it.
- **Exams**: the settings page → `memorive_settings` (exam jobs and executors) → the exam modules
  loaded by name (`memorive_model_evaluation`) → the model request → scoring → score history.
  `WorkflowExams` (`memorive_settings/workflow_exam_runner.py`) plans and runs a node's exam and
  keeps the scores; it holds the model runner and sends the exam's requests through it.
- **Discovery and reports**: the window (`research.*`, `ai.*`, `home.*`) or a schedule →
  `memorive_research_runtime` (`runtime.py` runs discovery and reports, `ai_briefing.py` the AI
  briefing, `home.py` the home page summary) with `literature_discovery`, `research_recommendations`
  and `research_reports`. Messages and current-task requests about a research run are answered by
  its `routes.py`. The package reaches the rest of the app only through the `ResearchHost` that
  `ProductApi` builds (`host.py`): the settings service, library, messages, work log and window events.

Calls go from the window side towards the business packages, from those to model execution and
transport (`memorive/models/execution`, `memorive_settings`, `model_gateway`), and from all of them to
the shared basics (`memorive_folder_management`, `runtime_log`, `memorive_language`, `run_ledger`,
`decision_log`). A few basics still reach into business packages; these are the places to untangle
when that code is restructured:

- `run_ledger`: the channel accounting, cost routing and analysis admission modules import
  `model_gateway`, `retrieval_weighting`, `artifact_registry` and `knowledge_admission`.
- `decision_log` and `knowledge_feedback` import each other: `knowledge_feedback` calls decision_log's
  builders (`knowledge_decision.py`, `conversation_refinement_decision.py`), and those take hashing
  helpers, `DECISION_STATES` and `validate_typed_refinement_item` from `knowledge_feedback`, and hashing
  helpers from `retrieval_weighting/contracts.py`. Several packages keep their own copy of these canonical
  hashing helpers (`research_opportunities/canonical.py`, `research_reports/canonical.py` and others).

### Which area each package belongs to

| Area | Packages |
| --- | --- |
| Desktop: window, service, panels | `runtime.py` (ProductApi), `memorive_desktop_service`, `memorive_desktop_assistant`, `memorive_current_task`, `memorive_messages`, `memorive_work_log`, `memorive_accounting`, `memorive_test_console_bridge`, `release_links.py` |
| Documents | `memorive/documents/processing/registration.py` (paper IDs), `memorive_inbox`, `memorive_workflow`, `document_processing`, `evidence_extraction`, `evidence_review`, `knowledge_admission`, `artifact_registry`, `memorive_library`, `memorive_review`, `memorive_front_matter.py` |
| Research | `memorive_research_workspace`, `memorive_research_runtime`, `literature_discovery`, `research_recommendations`, `research_reports`, `research_analysis`, `research_opportunities`, `retrieval`, `retrieval_weighting`, `knowledge_feedback` (knowledge drafts) |
| Sessions | `memorive_sessions`, `memorive_session_archive`, `memorive_browser_companion`, `memorive_workflow_bridge`, `knowledge_feedback/conversation_refinement` |
| Models | `memorive/models` (`validation/` sends every request; `execution/`, `calls/` with the call ledger, `cli/` with the command-line tools, endpoints, scheduling, identity, pricing, reply size; its docstring lists each file), `model_gateway`, `memorive_local_models` |
| Exams | `memorive_model_evaluation`, `memorive_analysis_reviewer_exam`, `memorive_review/exam.py`, and in `memorive_settings`: the `*exam*` modules, `quality_new.py`, `workflow_exam_runner.py` |
| Shared basics | `memorive/platform` (file and folder names, canonical JSON and whole-file writes, the secret check, the bundled root certificates), `memorive_folder_management`, `runtime_log`, `run_ledger`, `memorive_language`, `memorive_source_language.py`, `decision_log`, `memorive_app` (jobs, credentials, IPC), `memorive_application` (an integration harness run by hand) |

During v2.00 the packages move into `source/memorive/<area>/` (desktop, documents, research,
sessions, models, exams, platform); each row names what is already there first.
`memorive_settings` still holds code of two areas: the settings themselves and the exams. The exams
move to `memorive/exams`; the table shows where they belong, the package docstring shows where they
are today.

### Who keeps which data

| Data | Kept by |
| --- | --- |
| Settings, model profiles, CLI templates, credentials (by reference) | `memorive_settings` (`store.py`, `credentials.py`) |
| Model snapshots, call attempts and results | `memorive/models/execution` (`research_chat_profiles/` and the like), the call ledger in `memorive/models/calls/ledger.py` |
| Imported files and processing jobs | `memorive_inbox`, `memorive_workflow`, `memorive_app` (job store) |
| A paper's product files: RawMD, CleanMD, chunks, cards, analyses | `document_processing`, `evidence_extraction`, `knowledge_admission` (review state in the card); read through `memorive_library` |
| Research conversations, answers, knowledge drafts | `memorive_research_workspace` (its own SQLite store) |
| Imported chat sessions and refinement tasks | `memorive_session_archive`, `memorive_current_task/refinement_tasks.py` |
| Exam plans, results and score history | the exam code in `memorive_settings` (`exam_score_history.py`, the `workflow_exams/` run folders) |
| Messages, window state, work log | `memorive_messages`, `memorive_desktop_service/control_store.py`, `memorive_work_log` |
| Folder layout of a profile | `memorive_folder_management` |
| Runtime log | `runtime_log` |

## Running the tests

The tests are in the Git repository only: source archives (the ZIP that GitHub offers and the
release source packages) leave `memorive_desktop/tests` out, so clone the repository to run them.

Use a Python 3.13 environment with the packages from `requirements-build-agpl-candidate.lock` plus
`pytest` (`pytest-timeout` is optional). Keep it apart from the build environment of BUILDING.md,
which refuses extra packages. Run from the repository root:

```powershell
python .\memorive_desktop\tests\run_tests.py
python .\memorive_desktop\tests\run_tests.py .\memorive_desktop\tests\test_research_qa_flow.py
```

`run_tests.py` points every data folder (app data, logs, the vault, temporary files) at a new
throw-away folder and then runs pytest. Do not run plain `pytest`: the product code would write into
your real Memorive data, so `tests/conftest.py` stops any run in which these folders are not set or
lie inside a real data folder.

The background service binds every data folder to its profile when it starts. A component you call
on its own, or an example you run, uses `%LOCALAPPDATA%\Memorive\runtime` instead: the log, the
model cache, review reports and ledgers go to its `ops` folder, documents to `sandbox` and the vector
store to `vector-store` (`source/runtime_log/config.py`). Each `MEMORIVE_*` variable that
`run_tests.py` sets moves one of these folders.

After changing files under `source/`, `product/` or `ui/`, update the manifests as BUILDING.md
describes (`build_pages.py` for `ui/`, then `sync_manifests.py`); the build refuses files whose
hash differs from the manifest.

## Trying research Q&A

```powershell
python .\memorive_desktop\examples\research_qa.py
```

The example makes a research workspace in a new temporary folder, adds a synthetic paper, asks one
question with a stand-in model and prints the job status, the answer, its citations and the
excerpts the model was shown. No real data folder and no network is used. Read
`examples/research_qa.py` from the top: it follows the steps below in order.
`tests/test_research_qa_example.py` runs it, and `tests/test_research_qa_flow.py` uses the same paper
to cover every way a question can end.

## A research question, from the window to the saved answer

| Step | Where |
| --- | --- |
| The chat page sends `memo.ask` and then polls `memo.job_status`. | `ui/integrated/routes/chat/workspace.js` |
| `ProductApi.call` hands the research workspace's methods (`DESKTOP_METHODS` in `service.py`) to the workspace. On first use it creates the workspace with `create(...)`, passing the settings service, library, local models, profile folders, its own `call` and `stage_inbox_paths`; `create` also gives the workspace the model function. | `source/runtime.py`, `source/memorive_research_workspace/desktop.py` |
| `ResearchWorkspace.call` dispatches the method; `ask()` passes the question on. | `memorive_research_workspace/service.py` |
| `AnswerVersions.enqueue` checks the request, freezes the model choice, answer style, memories and research topic in a `QUEUED` job, and hands the job to the worker pool. Answering again (`memo.answer_retry`) goes through `AnswerVersions.retry` to the same place. | `memorive_research_workspace/answer_versions.py` |
| The worker runs `answer_job()`: `AnswerJob.start`, `gather_evidence`, `build_request`, `ask_model`, `check_answer`, `save_answer`; any error goes to `record_failure`. The module docstring lists what each step does. | `memorive_research_workspace/answering.py` |
| The model function freezes the chosen model profile (`settings.research_chat_freeze`) and runs it (`settings.research_chat_execute`). | `memorive_research_workspace/desktop.py` |
| The settings service runs the call: the snapshot is sent once, temporary failures are tried again, the call is counted. | `memorive_settings/controller.py`, `memorive/models/execution/` |
| The request itself is sent by the model validation runner. | `memorive/models/validation/runner.py` (`execute_structured_chat`) |

**Cancel.** `memo.cancel` (`ResearchWorkspace.cancel`) marks the job `CANCELLED` at once. The steps of
`AnswerJob` check for this between them. A model call already running is not stopped: when it
returns, `save_answer` discards the answer and keeps the call's usage on the job as `late_usage`.

**Restart.** When the app starts, the workspace is opened with `recover=True`, and jobs that were
`QUEUED` or `RUNNING` become `INTERRUPTED` ("Restarted; retry explicitly"); see
`ResearchWorkspace.__init__`. A job keeps the model snapshot its question was sent with
(`note_model_snapshot`, called by the model function in `desktop.py`). Once the desktop has wired
the workspace, `finish_answers_received_before_restart` asks the settings service for the result
it kept under that snapshot (`settings.research_chat_result`). If there is one, the job is answered
again through the normal steps with that result in place of the model call: the excerpts are
searched again, nothing is sent. Without a kept result the job stays `INTERRUPTED`, and the user
can ask again from the window.

## Where to change what

| To change | Open | Run |
| --- | --- | --- |
| When a failed model call is tried again, how often, how long to wait | `memorive/models/execution/retry.py` | `test_model_execution.py` |
| How a model profile is frozen and a call is sent once | `memorive/models/execution/runs.py` | `test_model_execution.py` |
| Which model profiles research may choose | `profile_options` in `memorive_research_runtime/report_models.py`; the desktop supplies its data in `catalog()`, the window-less process in `memorive_research_workspace/headless_models.py` | `test_research_model_catalog.py` |
| The instructions the model is given | `CHAT_RULES` in `model_gateway/research_prompt_cache.py`; for an agent job `instructions()` in `research_opportunities/research_opportunities.py`; `AnswerJob.build_request` chooses between them | `test_research_qa_flow.py` |
| The answer format the model must return | `ANSWER_SCHEMA` in `memorive_research_workspace/desktop.py`; `AnswerJob.build_request` ties it to the excerpt ids | `test_research_qa_flow.py` |
| Which excerpts go into the prompt | `AnswerJob.gather_evidence` and `AnswerJob.model_context` in `answering.py`; `context_selection.py` | `test_research_qa_flow.py` |
| How an answer is checked (text present, only shown excerpts cited) | `AnswerJob.check_answer` | `test_research_qa_flow.py` |
| The message a research run leaves when it finishes or fails | `post_result_message` and `_failure_message` in `memorive_research_runtime/runtime.py`; they hand the event to `MessagesProductController.post_event` | `test_research_discovery_feedback.py` |
| Where a message about a research run leads; research runs on the current-task page | `memorive_research_runtime/routes.py` | `test_research_discovery_feedback.py` |
| What is saved with an answer, answer versions | `AnswerJob.save_answer`, `answer_versions.py` | `test_research_qa_flow.py`, `test_workspace_answer_context.py` |
| How a document becomes numbered, searchable excerpts | `excerpts_of` and `EvidenceIndex` in `memorive_research_workspace/evidence.py` | `test_evidence_index.py`, `test_research_qa_flow.py` |
| When an excerpt counts as stale, and how often its files are hashed | `EvidenceIndex.read` and `checking_each_file_once` in `evidence.py`; the blocks are opened in `AnswerJob.run` and `AnswerVersions.validate_context` | `test_evidence_index.py`, `test_research_qa_flow.py` |
| Which material a conversation draws on; files added in the window | `scope_ids`, `configure`, `attach` in `memorive_research_workspace/attachments.py` | `test_attachments.py`, `test_workspace_vision_and_handoffs.py` |
| Agent settings; the folder prepared for a desktop agent app | `memorive_research_workspace/agents.py` | `test_research_workspace_inputs.py`, `test_workspace_vision_and_handoffs.py` |
| Which processed papers can be added to a research project | `memorive_research_workspace/library_bridge.py` | `test_library_for_research.py` |
| Connected apps: bringing papers in, outputs, note write-back, the companion's calls | `memorive_research_workspace/connections.py` | `test_connections.py` |
| What a connected device is shown when its document is processed | `job_results` in `memorive_research_workspace/desktop_connections.py` | `test_library_for_research.py` |
| Knowledge drafts from answers: proposing, reviewing, retracting, revisions, the note written on acceptance | `knowledge_feedback/research_ingest.py` | `test_knowledge_drafts.py` |
| Session refinement: what the model must answer, the typed items made from it | `memorive_workflow_bridge/conversation_refinement.py`, `knowledge_feedback/conversation_refinement/refinement.py` | `test_refinement_model_output.py` |
| Card review states (pending, active, quarantined), automatic review, the admission log | `knowledge_admission/machine.py`, `card_io.py`, `dispatch.py`, `knowledge_admission_log.py` | `test_card_review_states.py` |
| Custom command-line model tools: the template, how it is run, how its output is read | `memorive/models/cli/templates.py`, `template_execution.py` | `characterize/test_cli_templates.py` |
| How a model request is sent: API, command-line agents, embeddings, OCR | `memorive/models/validation/runner.py`: a structured chat goes `execute_structured_chat` (checks, limits, prompt) → `_send_api`, `_send_cli` or `_send_local` → `_chat_result`, with one request's state on `_ChatAttempt`; the API and command-line routes in `api_route.py` and `cli_route.py` next to it; model-name patterns and API endpoints in `memorive/models/endpoints.py`; the workflow exam's own part in `memorive_settings/workflow_exam_runner.py` | `test_analysis_provider_transport.py`, `test_model_execution.py` |
| The front matter at the top of RawMD, CleanMD and card files | `memorive_front_matter.py`; every reader of those files uses it | `test_front_matter.py` |
| Which files research may read: below the root, not through links, not in private folders; bundles added as attachments | `safe_path` in `memorive_research_workspace/evidence.py`; `load_bundle` in `skill_bridge.py` | `characterize/test_path_checks.py` |
| What happens to a question that was running when the app restarted | `_recover_after_restart` and `finish_answers_received_before_restart` in `memorive_research_workspace/service.py`; `stored_result` in `memorive/models/execution/runs.py` | `test_research_qa_flow.py`, `test_model_execution.py` |
| The name an exam's scores are saved under, and when a saved score is reused | `EXECUTOR_REF` of each exam executor; `ExamScoreHistory` in `memorive_settings/exam_score_history.py`; `build_cache_binding` in `exam_successor.py` | `test_scores.py` |

Run the whole suite before you finish; the files above are the quick check while you work.

## Code the window does not start

Some packages are not reached from the window's own code. They run when an operator calls
them, when a run loads them by name, or when a caller passes them the data they need. They are
kept and shipped; this list says how each one is entered.

| Code | How it is entered | Notes |
| --- | --- | --- |
| Exam modules, `memorive_model_evaluation/` | The settings page starts an exam (`settings.start_workflow_exam`); `service_main._quality_exam_package()` names this package and the modules of `memorive_settings/workflow_exam/` import its submodules with `importlib.import_module`. | A search for import statements does not find them. |
| Inherited exam scores: `_validate_workflow_exam_catalog` and `_validate_workflow_exam_catalog_v8` in `memorive_settings/workflow_exam_runner.py` | `WorkflowExams(catalog=...)` in `workflow_exam_runner.py`. The desktop passes `None` (`service_main.py`: new installations have no inherited results), so the reader runs only when a caller supplies a catalog. | Reads score catalogs saved by earlier versions; kept so those stay readable. |
| Command-line agent adapters in `model_gateway/execution_core/adapters/`: `claude_code.py`, `codebuddy_code.py`, `gemini_cli.py`, `github_copilot_cli.py`, `kimi_code.py`, `qwen_code.py` | Component API: build the adapter with its policy and call it. | The window runs command-line models through the model runner instead: `memorive/models/validation/cli_route.py` builds the command (`_cli_argv`) and `runner.py` runs it. Of the adapters in that folder only `codex_cli.py` is used on that path: its parser reads the event stream a Codex run prints. |
| Field-vector index tools in `retrieval_weighting/` (`cli.py`, `candidate_index.py`, `migration.py`, `locator.py`, `field_vectors.py`) | `python -m retrieval_weighting --input <request> --output <receipt> --root <run folder> --cutoff <UTC time>`, where the request's `operation` is build, migrate or validate. | Research Q&A uses `research_policy.py`, `research_ranking.py`, `research_rule.py`, and through `research_fields.py` and `query_dimensions.py` the input checks in `shadow_contracts.py`. |
| Shadow ranking modules in `retrieval_weighting/`: `aggregation.py`, `providers.py`, `candidate_union.py`, `counterevidence.py`, `field_search.py`, `semantic_field_profiles.py`, `semantic_field_search.py`, `semantic_shadow_contracts.py`, `source_snapshot.py`, `structural_candidate.py`, `retrieval_calibration_block_role_calibration.py` | Direct import only: neither the app nor the command line above reaches them. The tests import them. | |
| Knowledge feedback contracts: `knowledge_feedback/contracts.py`, `eligibility.py`, `lineage.py`, `penalty.py`, with `knowledge_admission/knowledge_submission.py`, `derived_knowledge_artifact.py` and `artifact_registry/knowledge_feedback_adapter.py` | `knowledge_admission` and `artifact_registry` import `contracts.py` and `eligibility.py`; `lineage.py` and `penalty.py` have no caller outside the package; the submission and preview modules are called by `artifact_registry` or only by tests. None of them writes a card, the registry or an index. | Saving an answer as knowledge in the window goes through `knowledge_feedback/research_ingest.py`, not through these. |
| Research opportunity pipeline: `research_opportunities/core.py`, `knowledge_admission/opportunity_submission.py`, `evidence_review/opportunity_verification.py` | Component API. | Research Q&A uses `research_opportunities/research_opportunities.py` and the eight comparison concepts `CONCEPT_ORDER`, which producer and verifier share from `research_opportunities/canonical.py`. |
| Document conversion command line | `python -m document_processing.document_conversion.cli` with probe, verify-structure or convert | Calls the same conversion facade as the app. |
| Local model components: the packages `model_gateway/local_capability/`, `local_embedding/` and `model_api_relay/` | Component API: the broker's `execute()` in `local_capability/broker.py`, `LocalEmbeddingSeam.execute()` in `local_embedding/seam.py`, `SafeRelayClient.invoke()` in `model_api_relay/client.py`. Each needs its configuration from the caller and is off by default. | `python -m model_gateway.local_capability` prints status and diagnostics only. |
| Evidence review orchestration, `evidence_review/review_orchestration/` | Its operator (`operator.py`): `create_or_get_job`, `pause`, `resume`, `cancel`, and `dispatch_once` with an execute function the caller supplies. | |

## Reading library files from other packages

The library controller (`memorive_library/product_controller.py`) keeps the files that document
processing produced. Other packages use only its public methods:

- `core_artifacts()`: every product file by id, with its kind, job, hashes and library row;
- `read_core_artifact(artifact_id)`: the path and content of a product file, read once; the content
  is checked against the hash the job recorded, so it can be handed on as it is;
- `resolve_core_private_artifact(artifact_id)`: the path of a product file, after checking that the
  file is still the one the job recorded;
- `get_artifact(artifact_id)`: one row as the library page shows it;
- `cached_rows()`: all rows as last refreshed, without asking the jobs again.

The controller's underscore fields can change without notice. If you need something they hold, add a
public method next to these.

## How checks are placed

Data is checked where it enters the app: requests from the window, files read from disk, model and
network replies, imported files, data written by older versions, and before anything is deleted.
Code inside the app does not check the results of other code inside the app again. Readers for data
written by older versions stay, even when no current code writes that form.
