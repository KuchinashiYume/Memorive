# Tests

Automated tests for the Memorive desktop application. They are not part of the release
packages, and `.gitattributes` keeps them out of GitHub's source archives.

## Running them

```
python memorive_desktop/tests/run_tests.py
```

Use a Python that has the application's packages and pytest (pytest-timeout is optional).
`run_tests.py` points every data folder the application uses (library, operations data,
logs, document sandbox, vector store, AppData) at a new temporary folder, so a run never
touches your own data; `conftest.py` stops the run when those folders are not set. Tests
cannot open network connections beyond this computer.

Pass pytest arguments to run part of the suite:

```
python memorive_desktop/tests/run_tests.py memorive_desktop/tests/test_update_maintenance.py -k junction
```

## Layout

| Folder | Contents |
| --- | --- |
| `tests/` | desktop application: settings, sessions, browser companion, inbox, updates, window |
| `tests/modules/` | the packages under `source/`, one folder per package |
| `tests/characterize/` | recorded behaviour; the expected results are in `golden/` |

Some tests skip on purpose: the directory-junction tests outside Windows, and the browser
companion smoke tests when Node.js with Playwright is not installed.
