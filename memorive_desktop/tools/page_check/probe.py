"""Open the main window page with the real desktop service on a throw-away profile and record what it shows.

run.py starts this with every user folder pointed at a throw-away folder; do not run it on its own.
    python probe.py <memorive_desktop root> <output dir> <throw-away folder>

Environment (run.py sets these from its options):
    CHECK_MODES    comma-separated modes from scenarios.MODES (default: all)
    CHECK_PICTURES "all" pictures every scenario, "routes" (default) only the plain route views, "none" none
    CHECK_ONLY     only scenarios whose name contains this text
    CHECK_DATA     "sample" (default) puts made-up records in first (seed.py), "empty" leaves the profile empty

Writes into the output dir:
    run.json              status, timings, page errors, the window's inner size per mode
    rules.json.gz         every style rule the browser parsed, with its enclosing at-rules
    globals.json          the page globals and their keys
    scenes/<name>.json.gz per scenario: the visible elements with label, text, attributes, size and position,
                          and their computed styles (stored once per distinct style)
    pictures/<name>.png   pictures of the window
The window is shown far outside the screen (a hidden window never paints) and does not take the focus.
"""
import gzip
import json
import os
import sys
import time
import traceback
from pathlib import Path

ROOT, OUT, SANDBOX = (Path(argument).resolve() for argument in sys.argv[1:4])
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "scenes").mkdir(exist_ok=True)
(OUT / "pictures").mkdir(exist_ok=True)
sys.path[:0] = [str(ROOT / "source"), str(ROOT / "product" / "desktop"), str(Path(__file__).parent)]

from page_js import EARLY, HELPERS  # noqa: E402
from scenarios import MODES, walk, preferences  # noqa: E402

modes = [m for m in os.environ.get("CHECK_MODES", ",".join(MODES)).split(",") if m]
pictures = os.environ.get("CHECK_PICTURES", "routes")
only = os.environ.get("CHECK_ONLY", "")
data = os.environ.get("CHECK_DATA", "sample")
result = {"status": "RUNNING", "modes": modes, "data": data, "sandbox": str(SANDBOX), "scenes": {}, "errors": []}


def say(text):
    print(time.strftime('%H:%M:%S'), text, file=sys.stderr, flush=True)


def write_result():
    (OUT / "run.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")


def on_ui(form, fn):
    from System import Action
    holder = {}

    def run():
        holder["value"] = fn()

    form.Invoke(Action(run))
    return holder.get("value")


def picture(window, destination):
    import clr
    clr.AddReference("System.IO")
    from Microsoft.Web.WebView2.Core import CoreWebView2CapturePreviewImageFormat
    from System.IO import MemoryStream
    form, holder = window.native, {}

    def start():
        holder["stream"] = MemoryStream()
        holder["task"] = form.webview.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, holder["stream"])

    on_ui(form, start)
    if not holder["task"].Wait(15000):
        raise TimeoutError("picture timed out")
    destination.write_bytes(bytes(holder["stream"].ToArray()))


api = None
try:
    sources = {name: SANDBOX / "session-sources" / name for name in ("codex_home", "claude_home")}
    for path in sources.values():
        path.mkdir(parents=True, exist_ok=True)
    from memorive.desktop.api import ProductApi

    api = ProductApi(SANDBOX / "state", ROOT / "source", ROOT / "source" / "facade_method_binding.json",
                     system_effects_enabled=False, session_source_roots=sources,
                     session_visible_ui_reader={}, test_fixture_mode=False)
    # The settings page checks for updates by itself 30 seconds after start; which scene that lands in varies,
    # and the check's button state with it. The sandbox profile turns the automatic check off.
    result["automatic_updates"] = api.call("updates.preference", {"enabled": False}).get("automatic_enabled")
    if data == "sample":
        from seed import seed

        result["seed"] = seed(api, SANDBOX, sources["codex_home"])
    import webview

    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
    page = (ROOT / "product" / "desktop" / "bundle_integrated.html").as_uri()
    first = MODES[modes[0]]["size"]
    window = webview.create_window("page check", html="<!doctype html><title>blank</title>", js_api=api, hidden=False,
                                   focus=False, x=-20000, y=-20000, width=first[0], height=first[1],
                                   background_color="#F7F5F1", text_select=True, zoomable=False)
    api.attach_window(window)
    from window_chrome import MainWindowChrome

    api._main_chrome = MainWindowChrome(window, api)
    window.events.before_show += api._main_chrome.install

    def js(expression):
        return window.evaluate_js(expression)

    def settle(quiet=0.45, longest=8.0):
        """Wait until the page has made no DOM change and has no bridge call in flight for `quiet` seconds."""
        begin = time.monotonic()
        last, quiet_since = None, begin
        while time.monotonic() - begin < longest:
            state = js("window.__check.tick()")
            if state != last or state[1] > 0:
                last, quiet_since = state, time.monotonic()
            elif time.monotonic() - quiet_since >= quiet:
                return {"waited": round(time.monotonic() - begin, 2), "settled": True}
            time.sleep(0.06)
        return {"waited": round(time.monotonic() - begin, 2), "settled": False, "pending": last and last[1]}

    def drive():
        try:
            # Touch the WebView2 objects only on the window's own thread: reading them from this thread can hang.
            if not window.events.shown.wait(60):
                raise RuntimeError("window not shown")
            form = window.native
            say('window shown')
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and not on_ui(form, lambda: form.webview.CoreWebView2 is not None):
                time.sleep(0.2)
            on_ui(form, lambda: form.webview.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(EARLY))
            say('webview ready')
            time.sleep(0.5)
            window.load_url(page)
            deadline, ready = time.monotonic() + 90, None
            while time.monotonic() < deadline:
                ready = js("({ready: !!window.__check && !!window.__Desktop_INTEGRATED_APP__ && !!window.pywebview?.api?.call})")
                if ready and ready.get("ready"):
                    break
                time.sleep(0.25)
            say(f'page ready: {ready}')
            if not (ready and ready.get("ready")):
                raise RuntimeError(f"page not ready: {ready}")
            time.sleep(1.5)   # the page's own start-up requests (pywebviewready) go out first
            result["helpers"] = js(HELPERS)
            result["startup_settle"] = settle()
            say(f'start-up settled: {result["startup_settle"]}')
            (OUT / "globals.json").write_text(js("window.__check.globals()"), encoding="utf-8")
            with gzip.open(OUT / "rules.json.gz", "wt", encoding="utf-8") as handle:
                handle.write(js("window.__check.rules()"))
            started = time.monotonic()
            for mode_name in modes:
                mode = MODES[mode_name]
                window.resize(*mode["size"])
                js(preferences(mode))
                settle()
                for name, steps, plain in walk(mode_name):
                    if only and only not in name:
                        continue
                    outcome = []
                    for index, step in enumerate(steps):
                        if index:
                            settle()    # let the page answer the step before (a route loads its list)
                        if step.startswith("wait "):
                            time.sleep(float(step[5:]))
                            outcome.append(step)
                        else:
                            outcome.append(js(step))
                    # A countdown changes the page every quarter second, so the page never stays still.
                    settled = settle(longest=1.5) if name.endswith("+armed") else settle()
                    text = js("window.__check.capture()")
                    safe = name.replace("/", "__")
                    with gzip.open(OUT / "scenes" / f"{safe}.json.gz", "wt", encoding="utf-8") as handle:
                        handle.write(text)
                    entry = {"steps": outcome, "settle": settled}
                    if pictures == "all" or (pictures == "routes" and plain):
                        picture(window, OUT / "pictures" / f"{safe}.png")
                    result["scenes"][name] = entry
                    say(f'{name}: {settled}')
                result.setdefault("viewport", {})[mode_name] = js("[innerWidth, innerHeight]")
            result["seconds"] = round(time.monotonic() - started, 1)
            result["errors"] = js("window.__check.errors")
            result["status"] = "DONE"
        except Exception:
            result["status"] = "FAIL"
            result["drive_error"] = traceback.format_exc()
            try:
                result["errors"] = js("window.__check && window.__check.errors")
            except Exception:
                pass
        finally:
            write_result()
            window.destroy()

    webview.start(drive, gui="edgechromium", debug=False, http_server=False, private_mode=True,
                  storage_path=str(SANDBOX / "webview"))
except Exception:
    result.update(status="FAIL", error=traceback.format_exc())
finally:
    if api is not None:
        try:
            api.close()
        except Exception:
            result["close_error"] = traceback.format_exc()
    write_result()
