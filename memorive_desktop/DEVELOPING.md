# Developing Memorive

This guide is for changing the code: where things are, how to run the tests, and which file to open
for a given change. Building the installer is described in [BUILDING.md](BUILDING.md).

## Folders

| Folder | What it holds |
| --- | --- |
| `source/` | The Python code of the desktop app, one package per feature (`memorive_research_workspace`, `memorive_library`, `memorive_settings`, `memorive_model_execution`, ...). `runtime.py` holds `ProductApi`, which receives every call from the window and passes it to the package that owns it. |
| `ui/` | The sources of the window's two pages. `tools/build_pages.py` builds `product/desktop/bundle_integrated.html` and `bundle_assistant.html` from them; see `ui/README.md`. |
| `tests/` | The tests. Run them with `tests/run_tests.py` (below). |
| `examples/` | Small programs that run one feature without the window. |
| `tools/` | Build, manifest and release tools. |

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
| `ProductApi.call` hands the research workspace's methods (`DESKTOP_METHODS` in `service.py`) to the workspace. On first use it creates the workspace with `create(api)`, which also gives it the model function. | `source/runtime.py`, `source/memorive_research_workspace/desktop.py` |
| `ResearchWorkspace.call` dispatches the method; `ask()` passes the question on. | `memorive_research_workspace/service.py` |
| `AnswerVersions.enqueue` checks the request, freezes the model choice, answer style, memories and research topic in a `QUEUED` job, and hands the job to the worker pool. Answering again (`memo.answer_retry`) goes through `AnswerVersions.retry` to the same place. | `memorive_research_workspace/answer_versions.py` |
| The worker runs `answer_job()`: `AnswerJob.start`, `gather_evidence`, `build_request`, `ask_model`, `check_answer`, `save_answer`; any error goes to `record_failure`. The module docstring lists what each step does. | `memorive_research_workspace/answering.py` |
| The model function freezes the chosen model profile (`settings.research_chat_freeze`) and runs it (`settings.research_chat_execute`). | `memorive_research_workspace/desktop.py` |
| The settings service runs the call: the snapshot is sent once, temporary failures are tried again, the call is counted. | `memorive_settings/controller.py`, `memorive_model_execution/` |
| The request itself is sent by the model validation runner. | `memorive_settings/model_validation.py` (`execute_structured_chat`) |

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
| When a failed model call is tried again, how often, how long to wait | `memorive_model_execution/retry.py` | `test_model_execution.py` |
| How a model profile is frozen and a call is sent once | `memorive_model_execution/execution.py` | `test_model_execution.py` |
| Which model profiles research may choose | `profile_options` in `memorive_research_runtime/report_models.py`; the desktop supplies its data in `catalog()`, the window-less process in `memorive_research_workspace/headless_models.py` | `test_research_model_catalog.py` |
| The instructions the model is given | `CHAT_RULES` in `model_gateway/research_prompt_cache.py`; for an agent job `instructions()` in `research_opportunities/research_opportunities.py`; `AnswerJob.build_request` chooses between them | `test_research_qa_flow.py` |
| The answer format the model must return | `ANSWER_SCHEMA` in `memorive_research_workspace/desktop.py`; `AnswerJob.build_request` ties it to the excerpt ids | `test_research_qa_flow.py` |
| Which excerpts go into the prompt | `AnswerJob.gather_evidence` and `AnswerJob.model_context` in `answering.py`; `context_selection.py` | `test_research_qa_flow.py` |
| How an answer is checked (text present, only shown excerpts cited) | `AnswerJob.check_answer` | `test_research_qa_flow.py` |
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
| Custom command-line model tools: the template, how it is run, how its output is read | `memorive_settings/cli_templates.py`, `cli_template_execution.py` | `characterize/test_cli_templates.py` |
| How a model request is sent: API, command-line agents, embeddings, OCR | `memorive_settings/model_validation.py`; model-name patterns and API endpoints in `model_endpoints.py`; the workflow exam's own part in `workflow_exam_runner.py` | `test_analysis_provider_transport.py`, `test_model_execution.py` |
| The front matter at the top of RawMD, CleanMD and card files | `memorive_front_matter.py`; every reader of those files uses it | `test_front_matter.py` |
| Which files research may read: below the root, not through links, not in private folders; bundles added as attachments | `safe_path` in `memorive_research_workspace/evidence.py`; `load_bundle` in `skill_bridge.py` | `characterize/test_path_checks.py` |
| What happens to a question that was running when the app restarted | `_recover_after_restart` and `finish_answers_received_before_restart` in `memorive_research_workspace/service.py`; `stored_result` in `memorive_model_execution/execution.py` | `test_research_qa_flow.py`, `test_model_execution.py` |
| The name an exam's scores are saved under, and when a saved score is reused | `EXECUTOR_REF` of each exam executor; `ExamScoreHistory` in `memorive_settings/exam_score_history.py`; `build_cache_binding` in `exam_successor.py` | `test_scores.py` |

Run the whole suite before you finish; the files above are the quick check while you work.

## Code the window does not start

Some packages are not reached from the window's own code. They run when an operator calls
them, when a run loads them by name, or when a caller passes them the data they need. They are
kept and shipped; this list says how each one is entered.

| Code | How it is entered | Notes |
| --- | --- | --- |
| Exam modules, `memorive_model_evaluation/` | The settings page starts an exam (`settings.start_workflow_exam`); `service_main._quality_exam_package()` names this package and `workflow_exam.py` imports its submodules with `importlib.import_module`. | A search for import statements does not find them. |
| Inherited exam scores: `_validate_workflow_exam_catalog` and `_validate_workflow_exam_catalog_v8` in `memorive_settings/workflow_exam_runner.py` | `LiveModelValidationRunner(workflow_exam_catalog=...)`. The desktop passes `None` (`service_main.py`: new installations have no inherited results), so the reader runs only when a caller supplies a catalog. | Reads score catalogs saved by earlier versions; kept so those stay readable. |
| Command-line agent adapters in `model_gateway/execution_core/adapters/`: `claude_code.py`, `codebuddy_code.py`, `gemini_cli.py`, `github_copilot_cli.py`, `kimi_code.py`, `qwen_code.py` | Component API: build the adapter with its policy and call it. | The window runs command-line models through Settings instead: `memorive_settings/model_validation.py` builds the command (`_cli_argv`) and runs it. Of the adapters in that folder only `codex_cli.py` is used on that path: its parser reads the event stream a Codex run prints. |
| Field-vector and shadow ranking in `retrieval_weighting/` (`candidate_index.py`, `migration.py`, `locator.py`, `field_vectors.py`, `shadow_contracts.py`, `semantic_shadow_contracts.py` and the modules they use) | `python -m retrieval_weighting --input <request> --output <receipt> --root <run folder> --cutoff <UTC time>`, where the request's `operation` is build, migrate or validate; or the functions the package exports. | Research Q&A uses the same package's `research_policy.py`, `research_ranking.py` and `research_rule.py`. |
| Knowledge feedback contracts: `knowledge_feedback/contracts.py`, `eligibility.py`, `lineage.py`, `penalty.py`, with `knowledge_admission/knowledge_submission.py`, `derived_knowledge_artifact.py` and `artifact_registry/knowledge_feedback_adapter.py` | Component API: contract checks and dry-run adapters; none of them writes a card, the registry or an index. | Saving an answer as knowledge in the window goes through `knowledge_feedback/research_ingest.py`, not through these. |
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
