# Window pages

The desktop window shows two pages. Both are generated from this folder:

| Folder | Page |
| --- | --- |
| `integrated/` | `product/desktop/bundle_integrated.html`, the main window |
| `assistant/` | `product/desktop/bundle_assistant.html`, the floating assistant |

Edit the files here, then run from the repository root:

```powershell
python .\memorive_desktop\tools\build_pages.py
python .\memorive_desktop\tools\sync_manifests.py
```

Do not edit the two pages themselves: the build stops when a page differs from its sources.

## How `integrated/` is laid out

- `page.html` is the page with an include line in place of each style, script and route section. It fixes the order: styles first, then the nine route sections, then the scripts. The order matters, because later rules and scripts build on earlier ones.
- `routes/<route>/` holds what belongs to one route of the window: `markup.html` (the route's section of the page), and usually `view.js` (what it shows) and `bridge.js` (its calls to the desktop service). The routes are settings, inbox, tasks (current task), messages, sessions, library, worklog (work log), leaderboard (model rankings) and chat (research Q&A).
- `shared/route-base.css` holds the base styles of all nine routes (the leaderboard's root is `#desktop-partleaderboard-preview`, research Q&A's `#desktop-partchat-preview`). A rule that several of them share is written once inside `:is(#desktop-<route>-preview, ...) { ... }`, where `&` stands for the route's root; a rule that only one route has keeps its `#desktop-<route>-preview` prefix. For each route its rules stay in their order. `shared/toolbar-icons.css` holds the toolbar icon buttons of every route.
- Every route section carries a copy of the window frame: the icons, the global bar, the navigation and the bottom (accounting) panel, around the route's own page and right panel. The frame's markup is written once under `shared/frame/`, and each `markup.html` includes those parts (an included file may include others). `shared/window-frame.js` gives every copy the same behaviour — panels, resizers, shortcuts, tooltips, switches — and holds the one thing that differs by route, the size rule of the right panel.
- `shared/` also holds what several routes use: the application shell (`app.js`), window chrome, controls, typography, the review and research panels.

The chat route's section also carries parts of the shared window (the right panel and the bottom accounting panel), because the page has always been laid out that way.

Files are copied byte for byte, so keep each file's line endings: CRLF in `integrated/`, LF in `assistant/`.

Every source file starts with a comment that says what it is for and who uses it. Read it before you change a file.

## Changing the page

### A route

1. Add `routes/<route>/markup.html`. Copy the section of a route that looks like yours: `<section class="desktop-route-surface" data-desktop-route="<route>" hidden>` around the route's root `<div id="desktop-<component>-preview" class="desktop-page-root">`, which includes the frame parts from `shared/frame/`.
2. Add the include line for the markup to `page.html`, after the other route sections, and the include lines for its scripts where they belong in the order of scripts.
3. Add the route to `routes` at the top of `shared/app.js` (its key, its label and its component, the folder name), a button to `shared/frame/navigation.html` whose `data-page` is the label, and the component with its right-panel size rule (one of `RIGHT_PANEL_RULES`) to `ROUTES` in `shared/window-frame.js`.
4. If the route reads data, give it a `bridge.js` that calls the desktop service (below) and publishes `window.__Desktop_<ROUTE>_BRIDGE__` with at least `refresh()` and `inspect()`.

`shared/window-frame.js` publishes each route's frame as `window.DesktopPreview_<component>` (panels, resizers, shortcuts, tooltips); route scripts find their root and panels through it.

### A setting

The settings page has a category list on the left (`<button class="desktop-settings-category" data-category="...">` in `routes/settings/markup.html`) and one panel per category (`data-panel="..."`). `routes/settings/model-picker.js` switches between them.

A panel that saves its own data registers with the page's Save and Revert buttons:

```js
window.__Desktop_SETTINGS_BRIDGE__.registerSaveParticipant('my-panel', {
  isDirty: () => ...,          // true while the panel has unsaved changes
  validate: () => ...,         // optional; false keeps Save disabled
  save: async () => { ... },   // write the draft, then show the saved state
  discard: async () => { ... } // go back to the saved state
});
```

`shared/local-user.js` is a short, complete example. A setting that belongs to the general settings record is read and written by `routes/settings/settings.js` itself (`collectSettings`).

### A dialog or a button that confirms

Copy an existing one so that it looks the same: the close dialog in `shared/window-chrome.js` (`desktop-close-dialog`), the project materials dialog in `routes/chat/markup.html` (`memo-dialog`), or the filter popover of the work log. For a button that asks "click again to confirm", use `window.__Desktop_UI_CONTROLS__.confirmAction(button, key)` from `shared/controls.js`, as the inbox and the current-task page do.

Compare a new dialog with an existing one in the running window (fonts, sizes, colours, spacing) before you commit it.

### Text and translations

Write the page's text in Chinese, in the markup or in the script. When the language is English or Japanese, `shared/app.js` translates every text node and the `title`, `aria-label`, `aria-description`, `placeholder` and `data-desktop-tip` attributes, except inside `[data-desktop-no-translate]` and user content such as messages and answers:

- `translations` in `shared/app.js` maps each Chinese text to its English and Japanese text. Add a line for every new text.
- Text that contains a number or a name is matched by a pattern in `translated()` in the same file.
- Text that a script sets later is translated as it appears; a script that needs the translated string itself calls `window.__Desktop_INTEGRATED_APP__.translate(text)`.

Check every new text in all three languages.

### Styles

- Colours: use the variables in `shared/tokens.css` (`--desktop-*`, `--surface-*`, `--text-*`, `--border-*`). Add a colour there only if none of them fits.
- Type: do not set font sizes or families on text. `shared/app.js` gives every element that shows text a role in `data-memo-type` (page, section, body, empty, control, value, code, small); `shared/typography.css` sets the typeface and the size of each role, times the font size setting.
- A rule that several routes share goes into `shared/route-base.css` (see above) or `shared/cross-route.css`; a rule for one route stays with that route.

## How the page and the Python code talk

The window runs the page in pywebview. `memorive/desktop/api/` (in `source/`) holds `ProductApi`, the object the page reaches as `window.pywebview.api`; its package docstring says which file holds which group of calls.

### From the page to Python

Most calls go through one function, `shared/service.js`:

```js
const result = await window.__Desktop_SERVICE__.call('library.list_artifacts', {});
```

It sends `window.pywebview.api.call(method, params)`; while a route waits for its first list, it shows the route's
loading placeholder (`DesktopDecorations.trackCall` in `shared/decorations.js`). Before pywebview is ready the
promise is rejected with `DESKTOP_SERVICE_NOT_CONNECTED`. A route bridge wraps it only to add what is its own, such
as a request number or a receipt event.

`ProductApi.call` (`memorive/desktop/api/product_api.py`) passes the method to the part of the app that owns it. The method names start with their area (`settings.`, `inbox.`, `current_task.`, `messages.`, `sessions.`, `library.`, `work_log.`, `accounting.`, `local_models.`, `memo.` for research Q&A, ...); the names each area accepts are listed in `memorive/desktop/api/common.py`. To add a method, add it to its set there and to the controller that runs it.

A few desktop actions are their own methods of `ProductApi`, because they open a file picker, Explorer or another window:

| Method | File in `memorive/desktop/api/` | Used by |
| --- | --- | --- |
| `get_control_state`, `persist_panel_state` | `window.py` | `shared/app.js` |
| `dispatch` (`navigation.select`) | `product_api.py` | `shared/product-bridge.js` |
| `assistant_shortcut`, `assistant_ui_signal` | `assistant.py` | `shared/app.js`, `shared/decorations.js` |
| `pick_inbox_files`, `get_inbox_preview`, `open_inbox_item`, `locate_inbox_item`, `pick_research_files` | `inbox_files.py` | `routes/inbox/bridge.js`, `routes/chat/workspace.js` |
| `memo_agent_config`, `memo_desktop_apps`, `memo_desktop_handoff_action`, `memo_open_evidence`, `memo_pick_agent_app`, `memo_pick_materials`, `memo_pick_vault`, `memo_prepare_desktop_handoff` | `research_memo.py` | `routes/chat/workspace.js`, `routes/settings/research-connections.js` |
| `get_console_connection`, `set_console_connection` | `settings_page.py` | `routes/settings/settings.js` |
| `open_structure_original` | `artifacts.py` | `shared/structure-review.js` |
| `open_developer_sdk` | `links.py` | `routes/settings/developer-clients.js` |

A script that calls Python waits for the `pywebviewready` event when `window.pywebview.api` is not there yet.

### From Python to the page

Python runs JavaScript in the page with `evaluate_js`. It uses these names, so keep them, and the methods listed, when you change the scripts that publish them:

| Name in the page | Published by | Used from Python |
| --- | --- | --- |
| `__Desktop_INTEGRATED_APP__`: `activate(route)`, `inspect()`, `applyPreferences(preferences)` | `shared/app.js` | `memorive/desktop/api/notifications.py` and `product_api.py`, `memorive/desktop/assistant/desktop.py`, `memorive/documents/data_review/health.py` |
| `__Desktop_INBOX_BRIDGE__`, `__Desktop_CURRENT_TASK_BRIDGE__`, `__Desktop_MESSAGES_BRIDGE__`, `__Desktop_LIBRARY_BRIDGE__`, `__Desktop_WORK_LOG_BRIDGE__`: `refresh()` | `routes/<route>/bridge.js` | `memorive/desktop/api/page_events.py`, after a session refinement changes |
| `__Desktop_WINDOW_CHROME__.requestClose(askAlways)` | `shared/window-chrome.js` | `product/desktop/window_chrome.py`, when the window is closed |
| `__Desktop_ASSISTANT_RUNTIME__`: `applyPresentation(...)`, `inspect()`, `setAutoRefreshEnabled(on)` | `assistant/runtime-bridge.js` | `memorive/desktop/assistant/desktop.py`, `product/desktop/console_expressions.py` |
| `DesktopPreview_settings.root` | `shared/window-frame.js` | `memorive/desktop/assistant/desktop.py`, to open a settings category |
| `__MEMORIVE_REVIEW_UI__` and the bridges above | `shared/review.js` and the bridges | `memorive/documents/data_review/health.py`, the window's health check |

Python also sends two events to the page:

| Event | Sent by | Listened to by |
| --- | --- | --- |
| `desktop:core-durable-event`, with the changed document jobs in `detail` | `memorive/desktop/api/page_events.py` | `shared/product-bridge.js`, which refreshes the routes that show them |
| `desktop:research-published` | `memorive/research/runtime.py` (through `notify_window` in `memorive/research/host.py`) | `shared/research.js`, `routes/inbox/research-home.js` |

and reads a few things straight from the page: the `[data-console-expression]` elements and the `desktop-hide-expressions` class (`product/desktop/console_expressions.py`), the settings category buttons (`[data-category="..."]`, `assistant/desktop.py`) and the data-review step of the workflow list (`#settings-flow-list [data-step="data-review"]`, `data_review/health.py`).

`product/desktop/app.py` has a self-test that calls many more of the page's methods (`inspect()` of every bridge, the settings bridge's `call`, `refresh`, `developerDocument` and others, `__MEMO_WORKSPACE__.importFiles`). Run the full test suite after renaming anything a bridge publishes.

### Between the page's scripts

Scripts talk to each other through the globals they publish (each file's header comment names them) and through events on `window`:

| Event | Sent by | Listened to by |
| --- | --- | --- |
| `desktop-integrated-route` (the route was shown) | `shared/app.js` | the inbox, current-task, messages, library and work log bridges, `shared/window-frame.js`, `shared/decorations.js`, `routes/chat/workspace.js`, `routes/leaderboard/view.js`, `routes/inbox/research-home.js` |
| `desktop:inspector-closed` (a right panel was closed) | `shared/controls.js` | the inbox, library, messages and work log bridges, `shared/structure-review.js` |
| `desktop:settings-locale-change` | `routes/settings/settings.js` | the settings scripts, `shared/local-user.js`, `shared/reports.js`, `shared/window-chrome.js` |
| `desktop:settings-category-changed`, `desktop:settings-mode-changed`, `desktop:settings-mode-scope-changed`, `desktop:workflow-model-selected` | `routes/settings/model-picker.js` | the settings scripts, `routes/chat/workspace.js`, `routes/inbox/research-home.js` |
| `desktop:workflow-config-updated` | `routes/settings/settings.js` | `routes/settings/external-sources.js`, `routes/tasks/view.js` |
| `desktop:external-sources-changed`, `desktop:external-model-reference` | `routes/settings/external-sources.js` | `routes/inbox/research-home.js`, `routes/leaderboard/view.js`, `routes/settings/exam-panel.js` |
| `desktop:research-preferences-saved` | `shared/research.js` | `shared/research.js`, `routes/settings/external-sources.js` |
| `desktop:browser-companion-updated` | `routes/sessions/bridge.js`, `routes/settings/browser-sites.js` | `routes/settings/settings.js` |
| `desktop:display-currency-changed` | `shared/accounting.js`, `routes/leaderboard/view.js` | the same two |
| `desktop:model-commerce-context-changed` | `routes/leaderboard/view.js` | `shared/accounting.js` |
| `desktop:current-task-production-mode`, `-production-bindings`, `-refinement-bindings`, `-override-state` | `routes/tasks/bridge.js` | `routes/tasks/view.js` |
| `desktop:session-refinement-task-updated` | `routes/sessions/bridge.js` | `routes/tasks/bridge.js` |
| `desktop:open-session` | `routes/inbox/bridge.js` | `routes/sessions/view.js` |
| `memorive:open-current-task` | `routes/worklog/bridge.js` | `routes/tasks/bridge.js` |
| `desktop:library-query` | `routes/library/view.js` | `routes/library/bridge.js` |
| `desktop:library-selection` | `routes/library/bridge.js` | `shared/structure-review.js` |
| `desktop:messages-navigation-result` | `routes/messages/bridge.js` | `routes/messages/view.js` |

## Building and checking

From the repository root:

```powershell
python .\memorive_desktop\tools\build_pages.py           # build both pages and their script hashes
python .\memorive_desktop\tools\build_pages.py --check   # stops when a page differs from its sources
python .\memorive_desktop\tools\sync_manifests.py        # after every change to files under ui/
python .\memorive_desktop\tools\check_internal_codes.py
```

Then run the tests through `python .\memorive_desktop\tests\run_tests.py` (see `DEVELOPING.md`). Tests that look at the page's code read these sources through `tests/page_sources.py`.

To see what a change does to the page itself, run the page check before and after it and compare the two runs; `tools/page_check/README.md` says how. It records every element of the main window in three languages, at a larger font size and in a narrow window, with made-up records, and after the usual actions (opening details, saving and reverting settings, dialogs).

## Conventions

- Keep the window frame in `shared/frame/` and `shared/window-frame.js`; a route only adds what is its own.
- Name a global that other scripts or Python use `window.__Desktop_<NAME>__` and freeze it (`Object.freeze`). Mention it in the file's header comment.
- Comments are in English. Text shown to the user is in Chinese in the source and translated as above.
