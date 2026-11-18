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

- `page.html` is the page with an include line in place of each style, script and route section. It fixes the order: styles first, then the window frame with the nine route sections inside it, then the scripts. The order matters, because later rules and scripts build on earlier ones.
- `routes/<route>/` holds what belongs to one route of the window: `markup.html` (the route's section of the page), and usually `view.js` (what it shows) and `bridge.js` (its calls to the desktop service). The routes are settings, inbox, tasks (current task), messages, sessions, library, worklog (work log), leaderboard (model rankings) and chat (research Q&A).
- `shared/route-base.css` holds the base styles of all nine routes (the leaderboard's root is `#desktop-leaderboard-preview`, research Q&A's `#desktop-chat-preview`). A rule that several of them share is written once inside `:is(#desktop-<route>-preview, ...) { ... }`, where `&` stands for the route's root; a rule that only one route has keeps its `#desktop-<route>-preview` prefix. For each route its rules stay in their order. `shared/toolbar-icons.css` holds the toolbar icon buttons of every route.
- The window frame is in `page.html` once, around the routes (`#desktop-shell`): the icons, the global bar, the navigation, the bottom (accounting) panel and its resizer, the tooltip. Its parts are written under `shared/frame/` (an included file may include others). A route section brings only its own central area, right panel and that panel's resizer; the section and its root are `display: contents`, so these sit in the frame's workspace grid. `shared/window-frame.js` gives the frame its behaviour — panels, resizers, shortcuts, tooltips, switches. The sidebar and the bottom panel belong to the whole window; the right panel belongs to its route (the root's `data-right` and the width it was given), and `show()` makes the open route's right panel the window's. The size rule of the right panel is the one thing that differs by route. In `route-base.css`, a shared group whose rules also style the frame names `#desktop-shell` beside the route roots, and a frame rule for some routes only reads the open route from `.desktop-window[data-route]`.
- `shared/` also holds what several routes use: the application shell (`app.js`), window chrome, controls, typography, the review and research panels.

The chat route's section also carries parts of the shared window (the right panel and the bottom accounting panel), because the page has always been laid out that way.

Files are copied byte for byte, so keep each file's line endings: CRLF in `integrated/`, LF in `assistant/`.

Every source file starts with a comment that says what it is for and who uses it. Read it before you change a file.

## Changing the page

### A route

1. Add `routes/<route>/markup.html`. Copy the section of a route that looks like yours: `<section class="desktop-route-surface" data-desktop-route="<route>" hidden>` around the route's root `<div id="desktop-<component>-preview" class="desktop-page-root">`, which holds the route's `main.desktop-central`, and if it has one its `aside#desktop-right-panel` and `#desktop-resize-right`.
2. Add the include line for the markup to `page.html`, after the other route sections, and the include lines for its scripts where they belong in the order of scripts.
3. Add the route to `routes` at the top of `shared/app.js` (its key and its component, the folder name), its names to `shared/frame/messages.js` (`frame.nav.<route>` on the button, `frame.route.<route>` in the top bar and the window title), a button to `shared/frame/navigation.html` whose `data-route` is the route's key, and the component with its right-panel size rule (one of `RIGHT_PANEL_RULES`) to `ROUTES` in `shared/window-frame.js`.
4. If the route reads data, give it a `bridge.js` that calls the desktop service (below) and publishes `Memorive.<route>Bridge` with at least `refresh()` and `inspect()`.

`shared/window-frame.js` publishes each route's frame as `Memorive.frames.<component>` (its root, its right panel, the window's sidebar and bottom panel, the live region); route scripts find their root and panels through it. A route that handles the right panel button itself (say what to select first, close its detail with the panel) listens for `desktop:right-button` on its root and cancels it.

### A setting

The settings page has a category list on the left (`<button class="desktop-settings-category" data-category="...">` in `routes/settings/markup.html`) and one panel per category (`data-panel="..."`). `routes/settings/model-picker.js` switches between them.

A panel with much code of its own gets its own script next to `settings.js`, as the browser companion
(`browser-companion.js`), the browser sites (`browser-sites.js`) and the reset of a category to its defaults
(`scoped-reset.js`) have; the rules for model services that the forms and the developer editor share are in
`model-rules.js`. A panel that saves its own data registers with the page's Save and Revert buttons:

```js
Memorive.settingsBridge.registerSaveParticipant('my-panel', {
  isDirty: () => ...,          // true while the panel has unsaved changes
  validate: () => ...,         // optional; false keeps Save disabled
  save: async () => { ... },   // write the draft, then show the saved state
  discard: async () => { ... } // go back to the saved state
});
```

`shared/local-user.js` is a short, complete example. A setting that belongs to the general settings record is read and written by `routes/settings/settings.js` itself (`collectSettings`).

### A dialog or a button that confirms

Copy an existing one so that it looks the same: the close dialog in `shared/window-chrome.js` (`desktop-close-dialog`), the project materials dialog in `routes/chat/markup.html` (`memo-dialog`), or the filter popover of the work log. For a button that asks "click again to confirm", use `Memorive.controls.confirmAction(button, key)` from `shared/controls.js`, as the inbox and the current-task page do.

Compare a new dialog with an existing one in the running window (fonts, sizes, colours, spacing) before you commit it.

### Text and translations

The page shows its text in Chinese, English or Japanese. Text gets there in one of two ways; give new text a key.

**Messages with a key.** The window frame uses them: the navigation, the top bar, the bottom accounting panel, the window buttons and the close dialog. `shared/i18n.js` (`Memorive.i18n`) holds every message in the three languages, one row per key, in the order zh-CN, en-US, ja-JP:

```js
'accounting.sync.synced': ['已同步 · {time}', 'Synced · {time}', '同期済み · {time}'],
```

- The frame's messages are in `shared/frame/messages.js`. A route that moves to keys gets its own `routes/<route>/messages.js`, included in `page.html` after `shared/frame/messages.js`. Keys start with the part they belong to (`frame.`, `window.`, `accounting.`, the route's name).
- In markup, `data-i18n="key"` sets the element's text, and `data-i18n-aria-label`, `data-i18n-aria-description`, `data-i18n-aria-valuetext`, `data-i18n-title`, `data-i18n-tip` (for `data-desktop-tip`), `data-i18n-placeholder` and `data-i18n-label` set an attribute. Keep the Chinese text in the markup as well: `<span data-i18n="frame.nav.inbox">收件箱</span>`.
- In a script, `Memorive.i18n.set(element, key, values, attribute)` writes the same attributes, so the text changes with the language. `Memorive.i18n.text(key, values)` returns the text for something shown once, such as a tooltip or a screen-reader announcement.
- Values are named: `{ time: '12:30' }`. A value `{ key, values }` is a message itself and follows the language too, as the route name in `'Memorive {route}'` does.
- A keyed text or attribute belongs to its key, so no other script should write it. To take it over, call `set(element, null, null, attribute)` first (`routes/sessions/view.js` does that for the right panel button).
- `shared/app.js` shows every keyed element again when the language changes and when an element is added to the page.

**Translation by the Chinese wording.** The routes still work this way. When the language is English or Japanese, `shared/app.js` translates every text node and the `title`, `aria-label`, `aria-description`, `placeholder` and `data-desktop-tip` attributes that have no key, except inside `[data-desktop-no-translate]` and user content such as messages and answers:

- `translations` in `shared/app.js` maps each Chinese text to its English and Japanese text.
- Text that contains a number or a name is matched by a pattern in `translated()` in the same file.
- Text that a script sets later is translated as it appears; a script that needs the translated string itself calls `Memorive.app.translate(text)`.

Check every new text in all three languages.

### Styles

- Colours: use the variables in `shared/tokens.css` (`--desktop-*`, `--surface-*`, `--text-*`, `--border-*`). Add a colour there only if none of them fits.
- Type: the window has one type rule, in `shared/typography.css`, and nothing outside it. Three typefaces: the interface face (`--memo-font-ui`, Arial / SimHei) for all interface text, the reading face (`--memo-font-read`, Times New Roman / SimSun) only for running text — research Q&A questions and answers, message bodies, documents and reports — and the code face (`--memo-font-code`). Three sizes, times the font size setting: `--memo-size-title` (26px, page titles), `--memo-size-body` (16px, text and controls), `--memo-size-small` (14px, notes, counts, times). Two weights: 400, and 700 for titles and emphasis. `shared/app.js` gives every element that shows text a role in `data-memo-type` (page, section, body, empty, control, value, code, small) and `typography.css` gives each role its face, size and weight; a style sheet that needs a face, size or weight anyway (an icon's letter, a chart label) names one of these variables. `tests/test_page_type_rule.py` fails on any other value, so a new page or a later version cannot bring its own fonts or sizes. To add running text, add its container to the list in `typography.css` and in `typeRole` in `app.js`.
- A rule that several routes share goes into `shared/route-base.css` (see above) or `shared/cross-route.css`; a rule for one route stays with that route.

## How the page and the Python code talk

The window runs the page in pywebview. `memorive/desktop/api/` (in `source/`) holds `ProductApi`, the object the page reaches as `window.pywebview.api`; its package docstring says which file holds which group of calls.

### From the page to Python

Most calls go through one function, `shared/service.js`:

```js
const result = await Memorive.service.call('library.list_artifacts', {});
```

It sends `window.pywebview.api.call(method, params)`; while a route waits for its first list, it shows the route's
loading placeholder (`Memorive.decorations.trackCall` in `shared/decorations.js`). Before pywebview is ready the
promise is rejected with `DESKTOP_SERVICE_NOT_CONNECTED`. A route bridge wraps it only to add what is its own, such
as a request number or a receipt event.

`ProductApi.call` (`memorive/desktop/api/product_api.py`) passes the method to the part of the app that owns it. The method names start with their area (`settings.`, `inbox.`, `current_task.`, `messages.`, `sessions.`, `library.`, `work_log.`, `accounting.`, `local_models.`, `memo.` for research Q&A, ...); the names each area accepts are listed in `memorive/desktop/api/common.py`. To add a method, add it to its set there and to the controller that runs it.

The window's own actions are methods of `ProductApi` that the page calls by name,
`window.pywebview.api.<method>(...)`: they open a file picker, Explorer or another window, or work on the window
itself. `settings.js` and `browser-companion.js` call them through the settings bridge's `nativeAction`.

| File in `memorive/desktop/api/` | Methods | Used by |
| --- | --- | --- |
| `window.py` | `get_control_state`, `persist_panel_state`, `reset_panel_layout`, `get_window_state`, `window_action`, `update_window_regions`, `prepare_window_close`, `commit_window_close`, `cancel_window_close` | `shared/app.js`, `shared/window-chrome.js`, `routes/settings/settings.js` |
| `product_api.py`, `page_events.py` | `dispatch` (`navigation.select`), `core_event_poll` | `shared/product-bridge.js` |
| `assistant.py` | `assistant_shortcut`, `assistant_ui_signal` | `shared/app.js`, `shared/decorations.js`, `routes/settings/settings.js` |
| `notifications.py` | `test_windows_notification` | `routes/settings/settings.js` |
| `inbox_files.py` | `pick_inbox_files`, `get_inbox_preview`, `open_inbox_item`, `locate_inbox_item`, `pick_research_files` | `routes/inbox/bridge.js`, `routes/chat/workspace.js` |
| `research_memo.py` | `memo_agent_config`, `memo_desktop_apps`, `memo_desktop_handoff_action`, `memo_open_evidence`, `memo_pick_agent_app`, `memo_pick_materials`, `memo_pick_vault`, `memo_prepare_desktop_handoff` | `routes/chat/workspace.js`, `routes/settings/research-connections.js` |
| `artifacts.py` | `open_library_core_artifact`, `open_library_refinement_artifact`, `locate_library_core_source`, `locate_library_refinement_artifact`, `open_structure_original` | `routes/library/bridge.js`, `shared/structure-review.js` |
| `settings_page.py` | `api_call_readiness`, `get_console_connection`, `set_console_connection`, `pick_settings_directory`, `open_settings_log_directory`, `clear_temporary_cache`, `test_network_connection`, `test_external_library`, `import_redacted_settings_file`, `export_redacted_settings_file`, `export_support_bundle_file`, `export_cli_template_file`, `record_settings_save_receipt` | `routes/settings/settings.js` |
| `browser_companion.py` | `browser_companion_status`, `install_browser_companion`, `open_browser_companion_folder`, `open_browser_companion_site`, `save_browser_companion_sites`, `run_browser_companion_sync`, `run_browser_companion_full_sync`, `run_browser_companion_reload`, `run_browser_companion_test_read`, `sync_browser_sessions_from_downloads`, `import_browser_sessions_file` | `routes/settings/browser-companion.js`, `routes/settings/browser-sites.js`, `routes/sessions/bridge.js` |
| `links.py` | `open_project_page`, `open_local_help`, `open_developer_sdk` | `routes/settings/settings.js`, `routes/settings/developer-clients.js` |

A script that calls Python waits for the `pywebviewready` event when `window.pywebview.api` is not there yet.

### From Python to the page

Python runs JavaScript in the page with `evaluate_js`. It uses these names, so keep them, and the methods listed, when you change the scripts that publish them:

| Name in the page | Published by | Used from Python |
| --- | --- | --- |
| `Memorive.app`: `activate(route)`, `inspect()`, `applyPreferences(preferences)` | `shared/app.js` | `memorive/desktop/api/notifications.py` and `product_api.py`, `memorive/desktop/assistant/desktop.py`, `memorive/documents/data_review/health.py` |
| `Memorive.inboxBridge`, `Memorive.currentTaskBridge`, `Memorive.messagesBridge`, `Memorive.libraryBridge`, `Memorive.workLogBridge`: `refresh()` | `routes/<route>/bridge.js` | `memorive/desktop/api/page_events.py`, after a session refinement changes |
| `Memorive.windowChrome.requestClose(askAlways)` | `shared/window-chrome.js` | `product/desktop/window_chrome.py`, when the window is closed |
| `Memorive.inboxBridge.reportNativeDropFailure(reason)`, `Memorive.chat.importFiles(paths)` and `Memorive.chat.reportImportFailure(reason)`, and the elements `#desktop-inbox-preview` and `#desktop-chat-preview` that take dropped files | `routes/inbox/bridge.js`, `routes/chat/workspace.js` | `product/desktop/app.py` (`attach_inbox_drop_handler`), when files are dropped on the window |
| `__Desktop_ASSISTANT_RUNTIME__`: `applyPresentation(...)`, `inspect()`, `setAutoRefreshEnabled(on)` | `assistant/runtime-bridge.js` | `memorive/desktop/assistant/desktop.py`, `product/desktop/console_expressions.py` |
| `Memorive.frames.settings.root` | `shared/window-frame.js` | `memorive/desktop/assistant/desktop.py`, to open a settings category |
| `Memorive.review` and the bridges above | `shared/review.js` and the bridges | `memorive/documents/data_review/health.py`, the window's health check |

Python also sends two events to the page:

| Event | Sent by | Listened to by |
| --- | --- | --- |
| `desktop:core-durable-event`, with the changed document jobs in `detail` | `memorive/desktop/api/page_events.py` | `shared/product-bridge.js`, which refreshes the routes that show them |
| `desktop:research-published` | `memorive/research/runtime.py` (through `notify_window` in `memorive/research/host.py`) | `shared/research.js`, `routes/inbox/research-home.js` |

and reads a few things straight from the page: the `[data-console-expression]` elements and the `desktop-hide-expressions` class (`product/desktop/console_expressions.py`), the settings category buttons (`[data-category="..."]`, `assistant/desktop.py`) and the data-review step of the workflow list (`#settings-flow-list [data-step="data-review"]`, `data_review/health.py`).

`product/desktop/app.py` has a self-test that calls many more of the page's methods (`inspect()` of every bridge, the settings bridge's `call`, `refresh`, `developerDocument` and others, `Memorive.chat.importFiles`). Run the full test suite after renaming anything a bridge publishes.

### Between the page's scripts

Scripts talk to each other through what they publish in `window.Memorive` (each file's header comment names it) and through events on `window`:

| Event | Sent by | Listened to by |
| --- | --- | --- |
| `desktop-integrated-route` (the route was shown) | `shared/app.js` | the inbox, current-task, messages, library and work log bridges, `shared/research.js`, `shared/decorations.js`, `routes/chat/workspace.js`, `routes/leaderboard/view.js`, `routes/inbox/research-home.js` |
| `desktop:right-button` (the right panel button or Ctrl+Alt+B, sent to the open route's root; cancelling it stops the default toggle) | `shared/window-frame.js` | `routes/messages/view.js`, `routes/settings/model-picker.js`, `routes/tasks/view.js` |
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
- Publish what other scripts or Python use as a member of `window.Memorive` (`shared/namespace.js` creates it, as the first script), in lower camel case, and freeze it (`Object.freeze`). Mention it in the file's header comment. Python reaches it as `window.Memorive?.<name>`, since it may run before the page's scripts.
- Comments are in English. Text shown to the user is written in Chinese in the source; new text gets a key (see "Text and translations").
