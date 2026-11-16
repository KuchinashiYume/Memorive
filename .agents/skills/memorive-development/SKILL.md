---
name: memorive-development
description: How to work on the Memorive code in this repository - finding the module that owns a change, running the tests without touching real data, and the updates and checks a change needs before it is committed. Use when changing, testing or reviewing code, tests, UI pages or manifests here. For using Memorive as a research tool, see the skills in memorive_desktop/integrations/skills instead.
---

# Working on Memorive

Read `memorive_desktop/DEVELOPING.md` first. It describes the folders, how a feature runs from the
window to its package, a "Where to change what" table (the file to open and the test to run for each
kind of change), the code the window does not start, and where data is checked. Each package's
`__init__.py` says what the package does, who calls it and where its data lives.

## Where things are

| Path | What it holds |
| --- | --- |
| `memorive_desktop/source/` | The desktop app's Python packages. `runtime.py` holds `ProductApi`, which passes every call from the window to the package that owns it. |
| `memorive_desktop/ui/` | Sources of the window's pages. The built pages in `product/desktop/` are generated from them. |
| `memorive_desktop/tests/` | Tests: `tests/modules/<package>/` per package, `characterize/` for recorded behaviour, the rest for the desktop app. |
| `memorive_desktop/examples/` | Small programs that run one feature without the window, e.g. `research_qa.py`. |
| `memorive_desktop/integrations/` | Obsidian and Zotero companions, the developer API, and the research skills shipped to users. |
| `memorive_desktop/tools/` | Build, manifest and release tools. |
| `contracts/` | Data format descriptions that tests validate against. |
| `memorive_console/` | The console app, with its own manifest. |

## Before you change code

- Find the owner in DEVELOPING.md ("Where to change what") and in the package's `__init__.py`.
- A module that no import statement reaches may still be in use: loaded by name, run from the command
  line, or called as a component. DEVELOPING.md lists how each is entered. Do not delete code because a
  search finds no caller.
- Data is checked where it enters the app: requests from the window, files on disk, model and network
  replies, imports, data written by older versions, and before anything is deleted. Do not add checks
  that re-check what other code inside the app produced.

## Running the tests

- Always run them through `python memorive_desktop/tests/run_tests.py`, with test paths or `-k` to run
  part of the suite. It points every data folder at a throw-away folder. Plain `pytest` would write into
  the real Memorive data; `tests/conftest.py` stops such a run.
- While working, run the tests DEVELOPING.md names for the area; run the whole suite before you finish.
- Real papers, reference exam packs and frozen development runs are not in the repository. Tests that
  need them are kept outside it; `test_card_exam_synthetic_pack.py` and
  `modules/artifact_registry/test_paper_chain_synthetic.py` cover the same code with made-up data.

## After you change files

- UI: edit the sources under `memorive_desktop/ui/`, then run `python memorive_desktop/tools/build_pages.py`.
  Never edit `product/desktop/bundle_*.html` by hand.
- Every published file is listed with its hash in the manifests. After changing files under `source/`,
  `product/`, `ui/`, `integrations/`, `contracts/` or `memorive_console/`, run
  `python memorive_desktop/tools/sync_manifests.py`. The build refuses a file whose hash differs.
- Before a commit or a release, run the three checks in `memorive_desktop/RELEASE_CHECKS.md`:
  `build_pages.py --check`, `sync_manifests.py --check` and `check_internal_codes.py`.
- Names and text carry no internal development codes: no phase, task or module numbers, build,
  candidate or run ids, or project codenames. A few such spellings are saved in users' data or
  compared by installed copies; they are listed, each with its reason, in
  `memorive_desktop/tools/historical_names.json` and change only together with a data upgrade.
  Keep them exactly as written, in code and in test data alike.
- Keep each file's line endings as they are; the repository does not convert them (`* -text`).
  `README.md` and `README.en.md` at the root have the same content; change both.
