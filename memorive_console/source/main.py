from pathlib import Path
import argparse
import ctypes
import json
import os
import subprocess
import sys
import threading
import urllib.request
import uuid
from datetime import datetime

SOURCE_ROOT = Path(__file__).resolve().parent
FROZEN = bool(getattr(sys, 'frozen', False))
ROOT = Path(getattr(sys, '_MEIPASS', SOURCE_ROOT))
sys.dont_write_bytecode = True


def default_state_root():
    """Keep installed runtime state writable and separate from program files."""
    local = os.environ.get('LOCALAPPDATA')
    base = Path(local) if local else Path.home() / 'AppData' / 'Local'
    return (base / 'Memorive Test Console' / 'State').resolve()


def requested_state_root():
    """Best-effort failure path without reparsing the complete CLI."""
    try:
        index = sys.argv.index('--state-root')
        return Path(sys.argv[index + 1]).resolve()
    except (ValueError, IndexError, OSError):
        return default_state_root() if FROZEN else SOURCE_ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--reference-bridge', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--port', type=int, default=8877)
    parser.add_argument('--watchdog', nargs=2, type=int)
    parser.add_argument('--state-root', type=Path, default=default_state_root() if FROZEN else SOURCE_ROOT)
    args = parser.parse_args()
    args.state_root = args.state_root.resolve()
    args.state_root.mkdir(parents=True, exist_ok=True)
    if args.reference_bridge:
        from reference_bridge import main as reference_main
        reference_main()
        return 0
    from process_owner import process_identity, watchdog
    if args.watchdog:
        watchdog(args.state_root.resolve(), *args.watchdog)
        return 0
    if not args.headless:
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            base = f'http://127.0.0.1:{args.port}'
            request = urllib.request.Request(base + '/api/snapshot', headers={'X-Memorive-Console': '1'})
            with opener.open(request, timeout=1) as response:
                previous = json.load(response)
            if previous.get('schema_version') != 'Memorive-IndependentConsoleSnapshot-v1' or previous.get('console_root') != str(args.state_root.resolve()):
                raise RuntimeError('CONSOLE_PORT_OCCUPIED')
            with opener.open(urllib.request.Request(base + '/api/window/show', data=b'{}', headers={'X-Memorive-Console': '1', 'Content-Type': 'application/json'}), timeout=2):
                pass
            return 0
        except (OSError, ValueError):
            pass
    from common import create_ui_profile, delete_ui_profile, read_json, write_json
    from manager import Manager
    from server import make_server
    manager = Manager(args.state_root)
    for path in (args.state_root / 'ui_profiles').glob('Memorive-Console-*.json'):
        row = read_json(path)
        if row['status'] != 'CLEANED' and process_identity(row['owner_pid']) != row['owner_identity']:
            delete_ui_profile(args.state_root, row['profile_id'])
    window = None
    server = make_server(manager, args.port, show_window=lambda: window.show() if window else None,
        close_window=lambda: threading.Timer(.2, lambda: window.destroy() if window else None).start())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    write_json(args.state_root / 'console_endpoint.json', {'schema_version': 'Memorive-IndependentConsoleEndpoint-v1', 'pid': os.getpid(), 'url': f'http://127.0.0.1:{server.server_port}', 'protocol_version': '1.0', 'console_root': str(args.state_root.resolve())})
    if FROZEN:
        watchdog_command = [sys.executable, '--state-root', str(args.state_root), '--watchdog',
            str(os.getpid()), str(process_identity(os.getpid()))]
        watchdog_cwd = Path(sys.executable).resolve().parent
    else:
        watchdog_command = [str(Path(sys.executable).with_name('pythonw.exe')), str(ROOT / 'main.py'),
            '--state-root', str(args.state_root), '--watchdog', str(os.getpid()), str(process_identity(os.getpid()))]
        watchdog_cwd = ROOT
    subprocess.Popen(watchdog_command, creationflags=subprocess.CREATE_NO_WINDOW, cwd=watchdog_cwd,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ui_id = None
    try:
        if args.headless:
            print(json.dumps({'url': f'http://127.0.0.1:{server.server_port}', 'pid': os.getpid()}), flush=True)
            threading.Event().wait()
        else:
            dependencies = ROOT.parent / 'runtime' / 'dependency_overlay'
            if dependencies.is_dir():
                sys.path.insert(0, str(dependencies))
            import webview
            from build_drop import attach_build_drop_handler
            from window_lifecycle import WindowCloseController
            ui_id, ui_root = create_ui_profile(args.state_root, os.getpid(), process_identity(os.getpid()))

            class WindowApi:
                def choose_build(self):
                    selected = window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False, file_types=('Memorive main program (Memorive.exe)',))
                    return str(selected[0]) if selected else None

            window = webview.create_window('Memorive 测试控制台', f'http://127.0.0.1:{server.server_port}', js_api=WindowApi(), width=1440, height=900, min_size=(720, 620), background_color='#F4F5F1')
            close_controller = WindowCloseController(manager, window)
            window.events.closing += close_controller.closing
            import faulthandler
            trace_file = (args.state_root / f'desktop_startup_trace_{os.getpid()}.log').open('x', encoding='utf-8')
            faulthandler.dump_traceback_later(12, file=trace_file)
            drop_runtime = {}
            def loaded():
                faulthandler.cancel_dump_traceback_later()
                window.show()
                try:
                    drop_runtime.update(attach_build_drop_handler(window))
                    drop_status = 'ATTACHED'
                except Exception:
                    drop_status = 'UNAVAILABLE'
                    window.evaluate_js('window.MemoriveBuildConsoleDrop.unavailable()')
                write_json(args.state_root / 'desktop_window_ready.json', {'pid': os.getpid(), 'status': 'WINDOW_LOADED', 'native_build_drop': drop_status, 'storage_path_characters': len(str(ui_root / 'webview'))})
            window.events.loaded += loaded
            try:
                webview.start(gui='edgechromium', debug=False, private_mode=True, storage_path=str(ui_root / 'webview'))
            finally:
                faulthandler.cancel_dump_traceback_later()
                trace_file.close()
    finally:
        manager.close()
        server.shutdown()
        server.server_close()
        if ui_id:
            try:
                delete_ui_profile(args.state_root, ui_id)
            except Exception:
                pass  # The external watchdog retries after this process exits.
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        from common import cleanup_failure, now, write_json
        failure = cleanup_failure(error)
        failure_root = requested_state_root()
        failure_root.mkdir(parents=True, exist_ok=True)
        failure_path = failure_root / 'startup_failure.json'
        write_json(failure_path, {'recorded_at': now(), 'error_type': type(error).__name__,
            'error_code': str(error) if str(error).isascii() and len(str(error)) < 160 else 'STARTUP_ERROR', 'cause': failure})
        if '--headless' not in sys.argv and '--watchdog' not in sys.argv:
            message = '控制台未能启动。\n具体原因：' + failure['error_code']
            if failure.get('path'):
                message += '\n相关路径：' + failure['path']
            message += '\n详细记录：' + str(failure_path) + '。\n临时数据清理失败时不会标记为已清理。'
            ctypes.windll.user32.MessageBoxW(None, message, 'Memorive 测试控制台', 0x10)
        raise
