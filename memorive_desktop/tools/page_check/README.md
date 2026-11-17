# Page check

Records what the main window page shows, element by element, so that a run before a change and a run after it
can be compared. Use it after every change to `ui/` (or to code the page shows data from) to see that the page
looks and behaves as before, or differs only where you meant it to.

## Running it

From the repository root, in the Python environment of `DEVELOPING.md` (it needs pywebview, which the app uses):

```powershell
# the page as it is now
python .\memorive_desktop\tools\build_pages.py
python .\memorive_desktop\tools\page_check\run.py ..\page-runs\after

# the page before the change: export the older commit and check its memorive_desktop folder
git worktree add ..\before HEAD~1
python .\memorive_desktop\tools\page_check\run.py ..\page-runs\before --desktop ..\before\memorive_desktop

# what differs
python .\memorive_desktop\tools\page_check\compare.py ..\page-runs\before ..\page-runs\after --report ..\page-runs\compare.md
```

Run the same copy of the tool for both runs, so both walk the same scenes. Run one check at a time. A full run
takes about seven minutes; `--modes zh` or `--only <text>` runs a part.

| Option of `run.py` | Meaning |
| --- | --- |
| `--desktop DIR` | the `memorive_desktop` folder to check (default: the one the tool is in) |
| `--modes zh,en` | only these modes (`zh`, `en`, `ja`, `zh130`, `narrow`, `actions`) |
| `--only TEXT` | only the scenes whose name contains `TEXT` |
| `--pictures KIND` | `routes` (default) pictures each route's plain view, `all` every scene, `none` none |
| `--data KIND` | `sample` (default) puts made-up records in first, `empty` leaves the profile empty |

`run.py` opens `product/desktop/bundle_integrated.html` with the real desktop service (`ProductApi`) on a new
throw-away profile. Every user folder points at a throw-away folder, as in `tests/run_tests.py`, and the network is
limited to this computer, so the page shows the same thing in every run. The window opens far outside the
screen and does not take the focus.

## What it records

- **Scenes** (`scenarios.py`): five view modes (Chinese, English, Japanese, Chinese at 130 % font size, a narrow
  960 × 640 window), each with the home view, the nine routes, the seventeen settings categories, and every route
  with its right and bottom panels opened and closed. Then the `actions` mode does what a person does: opens a
  record's detail on the inbox, messages and sessions pages, edits a setting and reverts it, edits, saves and
  reads it back, arms and cancels the buttons that ask for a second click, waits out a reset, opens the dialog
  that asks before the window closes, and opens and closes the work log filter.
- **Made-up records** (`seed.py`): two files in the inbox, three messages, one Codex conversation. They go in
  through the product's own entry points before the page opens.
- **Per scene** (`out/scenes/*.json.gz`): every visible element with a label (tag, id, classes, key attribute,
  own text, and its number among equal labels), its text, common attributes, position and size, and the full
  computed style of the element and of its `::before` and `::after`.
- **Style rules** (`rules.json.gz`): every rule the browser parsed, with its enclosing `@media`, `@container`
  and `@scope`. This covers dialogs and menus no scene opens.
- **Page globals** (`globals.json`), **script errors** (`errors` in `run.json`: errors while loading, refused
  scripts and `console.error`), and **pictures** (`pictures/`).

After each step the check waits until the page has made no change for 0.45 seconds and no call to the desktop
service is open. Animations with an end are finished; looping ones stop at their start.

## Comparing

`compare.py` pairs the elements of two runs by label and compares text, attributes, position and size (more
than half a pixel) and every computed style. Equal differences are grouped into one line with the number of
places and a few examples. `--map pairs.json` takes `[[old, new], ...]` and replaces each old text in the first
run before comparing, to hide an intended change (for example one colour replaced by another). With no
difference it prints `NO DIFFERENCES` and exits with 0.

Both runs are normalised first: paths of the checked folder and of the run's own folders, dates and clock times,
and the digits of countdown texts in scenes whose name ends in `+armed`.

## Adding a scene

Add it to `scenarios.py`. A step is a JavaScript expression run in the page (`activate`, `click`, `type_text`,
`key`, `run` build the usual ones) or `"wait N"`. Use the page's own entry points and visible buttons, as the
existing scenes do. A new scene only appears in runs made after it was added, so make both runs again.
