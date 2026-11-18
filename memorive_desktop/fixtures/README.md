# Sample data for sample mode

Sample mode starts the app on made-up data instead of the user's files: `app.py --desktop-test-mode
--desktop-test-pdf <a PDF>`, or `ProductApi(..., test_fixture_mode=True)` from code. In that mode the
pages read the files below. Nothing here comes from a user or a real run; every file was written for
sample mode.

| Folder | Read by | What it holds |
| --- | --- | --- |
| `fixtures/source/` | `build_inbox_controller` in `memorive/desktop/service/service_main.py` (six files imported as the inbox's sample track); `get_inbox_fixture_paths` in `memorive/desktop/api/inbox_files.py` (`sample.docx`, `sample.png`) | Tiny files of the kinds the inbox takes. `unsupported.exe` is a text file with an `.exe` name: it shows how the inbox rejects a type it cannot read. |
| `fixtures_retrieval/` | `MessagesProductController` in `memorive/desktop/messages/product_controller.py` | `locator_catalog.json`: the targets the sample messages open. |
| `fixtures_integration/` | `LibraryProductController` in `memorive/documents/library/product_controller.py`, and `open_library_sample_artifact` in `memorive/desktop/api/artifacts.py` | `synthetic_library_catalog.json`: nine library rows, one for each state the library page shows; `files/`: the files those rows point to. |
| `fixtures_logging/` | `WorkLogProductController` in `memorive/desktop/work_log/product_controller.py` | Seven work log entries, the words the export must never contain, and the hash records of the log sources. |

Each controller checks its file's format when sample mode starts (`_validate_catalog`, `_validate_stream`,
`_validate_canaries`, `_validate_sentinels`); `tests/test_sample_mode_data.py` runs the same checks on
these files. When you change one of those formats, change the file here with it.

The public repository did not carry these folders up to v2.00, so sample mode stopped at start with a
missing file. They were rewritten then, following the samples of the earlier project: the same rows and
cases, new text, and the names the current code expects.
