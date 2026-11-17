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
- `shared/route-base.css` holds the base styles of settings, inbox, tasks, messages, sessions, library and worklog. A rule that several of them share is written once inside `:is(#desktop-<route>-preview, ...) { ... }`, where `&` stands for the route's root; a rule that only one route has keeps its `#desktop-<route>-preview` prefix. For each route its rules stay in their order. The leaderboard keeps its own `routes/leaderboard/base.css`, which the page loads later.
- Every route section carries a copy of the window frame: the icons, the global bar, the navigation and the bottom (accounting) panel, around the route's own page and right panel. The frame's markup is written once under `shared/frame/`, and each `markup.html` includes those parts (an included file may include others). `shared/window-frame.js` gives every copy the same behaviour — panels, resizers, shortcuts, tooltips, switches — and holds the one thing that differs by route, the size rule of the right panel.
- `shared/` also holds what several routes use: the application shell (`app.js`), window chrome, controls, typography, the review and research panels.

The chat route's section also carries parts of the shared window (the right panel and the bottom accounting panel), because the page has always been laid out that way.

Files are copied byte for byte, so keep each file's line endings: CRLF in `integrated/`, LF in `assistant/`.
