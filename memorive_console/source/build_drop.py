"""Native WebView file locators; connection still uses the existing session API."""
from pathlib import Path
import json


def dropped_build_path(event):
    transfer = event.get('dataTransfer') if isinstance(event, dict) else None
    files = transfer.get('files') if isinstance(transfer, dict) else None
    if not isinstance(files, list) or not files:
        raise ValueError('DROP_FILE_REQUIRED')
    if len(files) != 1:
        raise ValueError('DROP_ONE_BUILD_ONLY')
    row = files[0]
    locator = row.get('pywebviewFullPath') if isinstance(row, dict) else None
    if not isinstance(locator, str) or not locator or '\x00' in locator:
        raise ValueError('DROP_NATIVE_PATH_REQUIRED')
    path = Path(locator)
    if not path.is_absolute():
        raise ValueError('DROP_NATIVE_PATH_REQUIRED')
    if path.name.lower() != 'memorive.exe':
        raise ValueError('DROP_BUILD_EXE_REQUIRED')
    try:
        path = path.resolve(strict=True)
        if not path.is_file() or path.name.lower() != 'memorive.exe':
            raise ValueError('DROP_BUILD_EXE_REQUIRED')
    except OSError as error:
        raise ValueError('DROP_FILE_UNAVAILABLE') from error
    return str(path)


def deliver_build_drop(window, event):
    try:
        payload = {'path': dropped_build_path(event)}
    except ValueError as error:
        payload = {'error_code': str(error)}
    # JSON escaping preserves Unicode, quotes and backslashes in native paths.
    # Do not return a JS Promise through the synchronous pywebview evaluator.
    window.evaluate_js('void window.MemoriveBuildConsoleDrop.accept('
                       + json.dumps(payload, ensure_ascii=True) + ')')
    return payload


def attach_build_drop_handler(window):
    from webview.dom import DOMEventHandler
    element = window.dom.get_element('html')
    if element is None:
        raise RuntimeError('DROP_TARGET_NOT_FOUND')
    handler = DOMEventHandler(lambda event: deliver_build_drop(window, event),
                              prevent_default=True)
    element.on('drop', handler)
    window.evaluate_js('window.MemoriveBuildConsoleDrop.ready()')
    return {'element': element, 'handler': handler}
