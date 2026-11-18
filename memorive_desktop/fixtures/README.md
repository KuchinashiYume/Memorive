# Sample data for sample mode

Sample mode starts the app on made-up data instead of the user's files: `app.py --desktop-test-mode
--desktop-test-pdf <a PDF>`, or `ProductApi(..., test_fixture_mode=True)` from code. In that mode the
pages read the files below. Nothing here comes from a user or a real run; every file was written for
sample mode, and the documents name only "Memorive sample data" as their author.

| Folder | Read by | What it holds |
| --- | --- | --- |
| `fixtures/source/` | `build_inbox_controller` in `memorive/desktop/service/service_main.py` (six files imported as the inbox's sample track); `get_inbox_fixture_paths` in `memorive/desktop/api/inbox_files.py` (`sample.docx`, `sample.png`) | Small files of the kinds the inbox takes: a one-page paper (PDF), reading notes, a research note, a results table (xlsx), meeting minutes (docx), a picture. `sample.pptx` is an empty shell. `unsupported.exe` is a text file with an `.exe` name: it shows how the inbox rejects a type it cannot read. |
| `fixtures_retrieval/` | `MessagesProductController` in `memorive/desktop/messages/product_controller.py` | `locator_catalog.json`: the targets the sample messages open. |
| `fixtures_integration/` | `LibraryProductController` in `memorive/documents/library/product_controller.py`, and `open_library_sample_artifact` in `memorive/desktop/api/artifacts.py` | `synthetic_library_catalog.json`: library rows covering the states the library page shows (ready, pending, processing, stale, missing file, blocked rights, unsupported type); `files/`: the papers, reports and notes those rows point to. |
| `fixtures_logging/` | `WorkLogProductController` in `memorive/desktop/work_log/product_controller.py` | Work log entries (task records and daily summaries; ready, partial and failed parsing; info, warning and error), the words the export must never contain, and the hash records of the log sources. |

The app reads these files as they are. `tests/test_sample_mode_data.py` checks that each row holds the fields
its reader uses, that ids are unique and locators match them, and that the library rows point to files that
exist. Rows and fields can be added without changing code.

The public repository did not carry these folders up to v2.00, so sample mode stopped at start with a
missing file. They were written then, following the cases of the earlier project's samples, with new text
and the names the current code expects.
