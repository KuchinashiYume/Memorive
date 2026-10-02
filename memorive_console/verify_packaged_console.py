from __future__ import annotations

from pathlib import Path
import argparse
import ctypes
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parent
EVIDENCE = ROOT / "evidence"
HEADERS = {"X-Memorive-Console": "1"}


def wait_for(predicate, timeout=60.0, interval=0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise TimeoutError("WAIT_TIMEOUT")


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def request(base, path, value=None, timeout=15):
    data = None
    headers = dict(HEADERS)
    if value is not None:
        data = json.dumps(value).encode("utf-8")
        headers["Content-Type"] = "application/json"
    with urllib.request.urlopen(urllib.request.Request(base + path, data=data, headers=headers), timeout=timeout) as response:
        return json.load(response)


def close_window_for_pid(pid: int):
    user32 = ctypes.windll.user32
    handles = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    @callback_type
    def callback(hwnd, _):
        owner = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            handles.append(hwnd)
        return True

    user32.EnumWindows(callback, 0)
    if not handles:
        raise RuntimeError("CONSOLE_WINDOW_NOT_FOUND")
    for hwnd in handles:
        user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
    return len(handles)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path, default=ROOT / "dist" / "Memorive-Test-Console.exe")
    parser.add_argument("--runtime", type=Path, default=EVIDENCE / "portable_gui_runtime_v102")
    parser.add_argument("--output", type=Path, default=EVIDENCE / "packaged_console_verification_v102.json")
    parser.add_argument("--default-state", action="store_true")
    args = parser.parse_args()
    exe = args.exe.resolve()
    runtime = ((Path(os.environ["LOCALAPPDATA"]) / "Memorive Test Console" / "State")
        if args.default_state else args.runtime.resolve())
    EVIDENCE.mkdir(exist_ok=True)
    if not args.default_state:
        runtime.mkdir(parents=True, exist_ok=False)
    elif runtime.exists():
        raise RuntimeError("DEFAULT_STATE_ALREADY_EXISTS")
    started = time.monotonic()
    command = [str(exe), "--port", "0"]
    if not args.default_state:
        command[1:1] = ["--state-root", str(runtime)]
    process = subprocess.Popen(command, cwd=exe.parent, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    result = {
        "schema_version": "Memorive-PackagedConsoleVerification-v1",
        "executable": str(exe),
        "executable_sha256": hashlib.sha256(exe.read_bytes()).hexdigest(),
        "runtime_root": str(runtime),
        "checks": {},
    }
    try:
        def ready_or_failure():
            failure = read_json(runtime / "startup_failure.json")
            if failure:
                raise RuntimeError("PACKAGED_STARTUP_FAILED:" + json.dumps(failure, ensure_ascii=False))
            endpoint = read_json(runtime / "console_endpoint.json")
            ready = read_json(runtime / "desktop_window_ready.json")
            return (endpoint, ready) if endpoint and ready else None

        endpoint, ready = wait_for(ready_or_failure, timeout=75)
        base = endpoint["url"]
        result["checks"]["native_window_loaded"] = ready.get("status") == "WINDOW_LOADED"
        result["checks"]["native_drop_handler"] = ready.get("native_build_drop") == "ATTACHED"
        result["checks"]["snapshot_schema"] = request(base, "/api/snapshot").get("schema_version") == "Memorive-IndependentConsoleSnapshot-v1"
        page = urllib.request.urlopen(base + "/", timeout=10).read().decode("utf-8")
        result["checks"]["ui_asset_served"] = ("<title>Memorive 测试控制台</title>" in page
            and 'data-expression-group="native_animation"' in page
            and 'data-view-panel="events"' in page
            and 'id="event-catalog"' in page
            and 'id="event-qa-fields"' in page
            and all(f'data-view-panel="{name}"' in page for name in (
                "overview", "events", "expressions", "suite", "manual", "records", "contract")))
        logo = urllib.request.urlopen(base + "/memorive_test_console_icon.png", timeout=10).read()
        result["checks"]["logo_asset_served"] = logo.startswith(b"\x89PNG\r\n\x1a\n") and len(logo) > 10000
        script = urllib.request.urlopen(base + "/app.js", timeout=10).read().decode("utf-8")
        result["checks"]["image_failure_fallback"] = (
            "addEventListener('load'" in script and "addEventListener('error'" in script
            and "素材加载失败" in script)
        try:
            urllib.request.urlopen(base + "/api/expression-assets/not-declared.svg", timeout=10)
            result["checks"]["browser_asset_route_headerless"] = False
        except urllib.error.HTTPError as error:
            result["checks"]["browser_asset_route_headerless"] = error.code == 404
        try:
            urllib.request.urlopen(base + "/api/snapshot", timeout=10)
            result["checks"]["other_api_routes_require_header"] = False
        except urllib.error.HTTPError as error:
            result["checks"]["other_api_routes_require_header"] = error.code == 403

        connected = request(base, "/api/session/start", {"mode": "reference"}, timeout=75)
        session_id = connected["session"]["session_id"]
        result["session_id"] = session_id
        result["checks"]["reference_bridge"] = connected.get("state") == "REFERENCE_ONLY"
        result["checks"]["automatic_execution_disabled"] = connected.get("automatic_execution") is False
        result["checks"]["reference_does_not_import_local_profile"] = (
            connected.get("session", {}).get("local_profile_seed", {}).get("status") == "NOT_APPLICABLE")
        result["checks"]["all_operations_attached"] = len(connected.get("supported_operations", [])) == 16
        expression_events = connected.get("expression_catalog", {}).get("events", [])
        expression_groups = {}
        for event in expression_events:
            expression_groups[event["group"]] = expression_groups.get(event["group"], 0) + 1
        result["checks"]["expression_catalog"] = len(expression_events) == 33 and expression_groups == {
            "static_asset": 10, "native_pose": 11, "native_animation": 9, "web_animation": 3}
        result["checks"]["expression_light_monitor"] = connected.get("expression_monitor", {}).get("poll_interval_ms") == 250
        full = next(row for row in connected["suite_catalog"] if row["preset_id"] == "full_safe")
        result["checks"]["full_suite_count"] = full.get("available_case_count") == 29
        diagnostic = next(row for row in connected["suite_catalog"] if row["preset_id"] == "diagnostic_scenarios")
        result["checks"]["diagnostic_suite_count"] = (
            diagnostic.get("available_case_count") == 6 and diagnostic.get("case_count") == 6)

        qa = request(base, "/api/commands", {
            "request_id": "packaged-research-qa",
            "operation": "research.qa.simulate",
            "params": {
                "question": "这项研究为什么重要？",
                "answer": "这是用于封装验证的临时研究问答。",
                "citations": "Demo 2026 · console://temporary-source",
            },
            "allow_model_calls": False,
        })

        def qa_done():
            value = request(base, "/api/commands/" + qa["command_id"])
            return value if value.get("state") in {"SUCCEEDED", "FAILED", "CANCELLED"} else None

        qa = wait_for(qa_done, timeout=20)
        qa_projection = qa.get("result", {}).get("result", {}).get("research_qa", {})
        result["checks"]["research_qa_simulation"] = (
            qa.get("state") == "SUCCEEDED"
            and qa_projection.get("simulated") is True
            and qa_projection.get("model_calls_started") == 0)

        launched = request(base, "/api/suites", {"preset_id": "full_safe"})
        suite_id = launched["suite_id"]

        def suite_done():
            snapshot = request(base, "/api/snapshot")
            suite = next((row for row in snapshot.get("suites", []) if row["suite_id"] == suite_id), None)
            return (snapshot, suite) if suite and suite.get("state") == "COMPLETED" else None

        snapshot, suite = wait_for(suite_done, timeout=45)
        result["suite"] = {key: suite.get(key) for key in ["suite_id", "state", "verdict", "summary"]}
        result["checks"]["full_suite_pass"] = suite.get("verdict") == "PASS" and suite.get("summary") == {
            "total": 29, "passed": 29, "failed": 0, "skipped": 0}
        result["checks"]["suite_model_calls_disabled"] = suite.get("model_calls_allowed") is False
        result["checks"]["reference_suite_marked"] = suite.get("evidence_scope") == "REFERENCE_PROTOCOL"
        for language in ['zh-CN', 'en-US', 'ja-JP']:
            request(base, '/api/preferences', {'language': language})
            resource = urllib.request.urlopen(base + '/i18n-data.js', timeout=10).read().decode('utf-8')
            payload = json.loads(resource.removeprefix('window.CONSOLE_I18N=').removesuffix(';'))
            review = request(base, '/api/review/export', {})['report']
            current = request(base, '/api/snapshot')
            result['checks']['locale_' + language] = (payload['locale'] == language and len(payload['messages']) >= 550
                and current['session']['session_id'] == session_id and review['report_language'] == language
                and review['release_verdict'] == 'NOT_ASSESSED' and review['evidence_scope'] == 'REFERENCE_PROTOCOL')
        result['checks']['reference_manual_checks_disabled'] = not request(base, '/api/review')['manual_editable']

        result["window_count_closed"] = close_window_for_pid(endpoint["pid"])
        process.wait(timeout=45)
        result["checks"]["console_process_exited"] = process.returncode == 0

        def cleanup_done():
            latest = read_json(runtime / "monitor" / "latest_snapshot.json")
            return latest if latest and latest.get("state") == "CLEANED" else None

        latest = wait_for(cleanup_done, timeout=20)
        receipt = latest.get("last_cleanup") or {}
        result["cleanup"] = receipt
        result["checks"]["session_removed"] = not (runtime / "sessions" / session_id).exists()
        result["checks"]["cleanup_clean"] = receipt.get("status") == "CLEANED" and receipt.get("remaining_entries") == 0
        result["checks"]["business_content_removed"] = receipt.get("business_content_retained") is False
        review = read_json(runtime / 'receipts' / 'reviews' / (session_id + '.json'))
        result['checks']['review_cleanup_updated'] = (review and review['cleanup_status'] == 'CLEANED'
            and review['evidence_scope'] == 'REFERENCE_PROTOCOL' and not review['manual_editable'])
        ui_registries = [read_json(path) for path in (runtime / "ui_profiles").glob("Memorive-Console-*.json")]
        result["checks"]["ui_profiles_removed"] = all(row and row.get("status") == "CLEANED"
            and not Path(row["data_root"]).exists() for row in ui_registries)
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        result["status"] = "PASS" if all(result["checks"].values()) else "FAIL"
    except Exception as error:
        result["status"] = "FAIL"
        result["error_type"] = type(error).__name__
        result["error"] = str(error)
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.resolve().write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
