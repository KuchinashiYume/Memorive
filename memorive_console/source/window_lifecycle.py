"""Close only after the owned test session has stopped and been discarded."""
import json
import threading

from common import cleanup_failure


class WindowCloseController:
    def __init__(self, manager, window):
        self.manager = manager
        self.window = window
        self.lock = threading.Lock()
        self.running = False
        self.allow_close = False

    def _notify(self, method, value=None):
        argument = '' if value is None else json.dumps(value, ensure_ascii=True)
        try:
            self.window.evaluate_js(f'window.MemoriveBuildConsoleLifecycle?.{method}({argument})')
        except Exception:
            pass  # Cleanup must also work if the page has not loaded.

    def closing(self):
        with self.lock:
            if self.allow_close:
                return True
            if self.running:
                return False
            self.running = True
        threading.Thread(target=self._finish, name='console-close-cleanup', daemon=False).start()
        return False

    def _finish(self):
        self._notify('closingStarted')
        try:
            self.manager.end('CONSOLE_WINDOW_CLOSE')
        except Exception as error:
            with self.lock:
                self.running = False
            self._notify('closingFailed', cleanup_failure(error))
            return
        with self.lock:
            self.allow_close = True
            self.running = False
        self.window.destroy()
