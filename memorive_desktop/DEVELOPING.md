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
`ResearchWorkspace.__init__`. The user can ask again from the window.

## Where to change what

| To change | Open | Run |
| --- | --- | --- |
| When a failed model call is tried again, how often, how long to wait | `memorive_model_execution/retry.py` | `test_model_execution.py` |
| How a model profile is frozen and a call is sent once | `memorive_model_execution/execution.py` | `test_model_execution.py` |
| The instructions the model is given | `CHAT_RULES` in `model_gateway/research_prompt_cache.py`; for an agent job `instructions()` in `research_opportunities/research_opportunities.py`; `AnswerJob.build_request` chooses between them | `test_research_qa_flow.py` |
| The answer format the model must return | `ANSWER_SCHEMA` in `memorive_research_workspace/desktop.py`; `AnswerJob.build_request` ties it to the excerpt ids | `test_research_qa_flow.py` |
| Which excerpts go into the prompt | `AnswerJob.gather_evidence` and `AnswerJob.model_context` in `answering.py`; `context_selection.py` | `test_research_qa_flow.py` |
| How an answer is checked (text present, only shown excerpts cited) | `AnswerJob.check_answer` | `test_research_qa_flow.py` |
| What is saved with an answer, answer versions | `AnswerJob.save_answer`, `answer_versions.py` | `test_research_qa_flow.py`, `test_workspace_answer_context.py` |
| How a document becomes numbered, searchable excerpts | `memorive_research_workspace/evidence.py` (`EvidenceIndex`) | `test_research_qa_flow.py` |
| Which processed papers can be added to a research project | `memorive_research_workspace/library_bridge.py` | `test_library_for_research.py` |
| What a connected device is shown when its document is processed | `job_results` in `memorive_research_workspace/desktop_connections.py` | `test_library_for_research.py` |

Run the whole suite before you finish; the files above are the quick check while you work.

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
