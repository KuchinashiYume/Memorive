"""Opaque main-window chrome. No pet hooks, polling drag loop or model calls."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import math
import secrets
import threading


def validate_edit_state(value):
    if not isinstance(value, dict) or set(value) != {'ready', 'dirty', 'saving'}:
        raise ValueError('WINDOW_EDIT_STATE_INVALID')
    if any(type(v) is not bool for v in value.values()):
        raise ValueError('WINDOW_EDIT_STATE_INVALID')
    return dict(value)


def policy_key(policy):
    if policy.get('status') != 'READY' or type(policy.get('active_job_count')) is not int:
        raise ValueError('WINDOW_POLICY_UNAVAILABLE')
    return tuple(policy[k] for k in ('close_behavior', 'warn_on_close_running',
                                    'keep_tasks_in_background', 'active_job_count', 'action'))


def close_projection(policy, edit):
    edit = validate_edit_state(edit)
    policy_key(policy)
    return {
        **policy, 'edit': edit,
        'tray_allowed': not (policy['active_job_count'] and not policy['keep_tasks_in_background']),
        'automatic_action': policy['action'] if edit['ready'] and not edit['saving']
                            and not edit['dirty'] else 'ASK',
    }


def hit_test(x, y, width, height, border, maximized, regions):
    """Coordinates are physical client pixels; system resize strips stay native."""
    if not maximized:
        left, right = x < 0, x >= width
        top, bottom = y < 0, y >= height
        if top and left: return 13
        if top and right: return 14
        if bottom and left: return 16
        if bottom and right: return 17
        if left: return 10
        if right: return 11
        if top: return 12
        if bottom: return 15
    def inside(rect):
        return rect[0] <= x < rect[2] and rect[1] <= y < rect[3]
    if any(inside(r) for r in regions.get('maximize', [])): return 9
    if any(inside(r) for r in regions.get('exclude', [])): return 1
    if any(inside(r) for r in regions.get('drag', [])): return 2
    return 1


class MainWindowChrome:
    def __init__(self, window, api):
        self.window, self.api = window, api
        self.regions = {}
        self._ticket = None
        self._gate = threading.RLock()
        self._request_inflight = False
        self.installed = False
        self.last_error = ''
        self._normal_extent = None
        self._session_query_pending = False
        self._session_ended = False

    def session_event(self, message, confirmed):
        if message == 0x11:  # WM_QUERYENDSESSION: consent, not a user close.
            self._session_query_pending = True
            return 1
        if message == 0x16:  # WM_ENDSESSION: another app may have vetoed.
            self._session_query_pending = bool(confirmed)
            if confirmed and not self._session_ended:
                self._session_ended = True
                self.api.end_windows_session()
            return 0
        return None

    def prepare_close(self, edit):
        edit = validate_edit_state(edit)
        policy = self.api.get_close_policy()
        view = close_projection(policy, edit)
        with self._gate:
            self._ticket = (secrets.token_hex(16), policy_key(policy), edit)
            view['ticket'] = self._ticket[0]
        return view

    def cancel_close(self):
        with self._gate: self._ticket = None
        return {'status': 'CANCELLED'}

    def commit_close(self, request):
        if not isinstance(request, dict) or set(request) != {'ticket', 'choice', 'edit'}:
            raise ValueError('WINDOW_CLOSE_REQUEST_INVALID')
        choice = request['choice']
        if choice not in {'EXIT', 'HIDE'}: raise ValueError('WINDOW_CLOSE_CHOICE_INVALID')
        edit = validate_edit_state(request['edit'])
        with self._gate:
            ticket = self._ticket
            if ticket is None or request['ticket'] != ticket[0]:
                return {'status': 'RECONFIRM'}
            policy = self.api.get_close_policy()
            if not edit['ready'] or edit['saving']:
                return {'status': 'BUSY'}
            if policy_key(policy) != ticket[1] or edit != ticket[2]:
                self._ticket = None
                return {'status': 'RECONFIRM'}
            decision = self.api.get_close_policy(choice)
            # The decision read is fresh too: a task may have started after the
            # first read. Action differs by explicit choice; all other policy
            # fields must still match the confirmation the user actually saw.
            if policy_key(decision)[:-1] != ticket[1][:-1] or decision['action'] != choice:
                self._ticket = None
                return {'status': 'RECONFIRM'}
            self._ticket = None
        return self.api.exit_application({}) if choice == 'EXIT' else self.api.hide_main_window()

    def request_close(self, *, force_ask=False):
        # FormClosing runs on the UI thread. Never wait there for WebView/IPC.
        if self._session_query_pending: return False
        with self._gate:
            if self._request_inflight: return False
            self._request_inflight = True
        def request():
            try:
                result = self.window.evaluate_js(
                    'Boolean(window.__P08_WINDOW_CHROME__ && '
                    'window.__P08_WINDOW_CHROME__.requestClose('
                    + ('true' if force_ask else 'false') + '))')
                if result is not True: self.fallback()
            except Exception as error:
                self.last_error = type(error).__name__
                self.fallback()
            finally:
                with self._gate: self._request_inflight = False
        threading.Thread(target=request, daemon=True, name='PR-OS-close-intent').start()
        return False

    def fallback(self):
        # Only a failed frontend uses native recovery; unknown state is not idle.
        if self._session_query_pending: return
        from System import Action
        from System.Windows.Forms import MessageBox, MessageBoxButtons, MessageBoxIcon
        try: lang = self.api._current_preferences().get('language', 'zh-CN')
        except Exception: lang = 'zh-CN'
        text = {
            'en-US': ('Close window', 'The window is not responding. Exit? Unsaved changes and running tasks may be lost.'),
            'ja-JP': ('ウィンドウを閉じる', '画面が応答していません。終了しますか？未保存の変更や実行中の処理は失われる場合があります。'),
        }.get(lang, ('关闭窗口', '界面暂时无响应。仍要退出？未保存修改和运行中任务可能丢失。'))
        def show():
            if self._session_query_pending: return
            result = MessageBox.Show(self.window.native, text[1], text[0],
                                     MessageBoxButtons.YesNo, MessageBoxIcon.Warning)
            if str(result) == 'Yes': self.api.exit_application({})
        self.window.native.BeginInvoke(Action(show))

    def update_regions(self, value):
        if not isinstance(value, dict) or set(value) != {'scale', 'drag', 'exclude', 'maximize'}:
            raise ValueError('WINDOW_REGIONS_INVALID')
        scale = value['scale']
        if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not math.isfinite(scale) or scale <= 0:
            raise ValueError('WINDOW_REGIONS_INVALID')
        result = {}
        for name in ('drag', 'exclude', 'maximize'):
            rows = value[name]
            if not isinstance(rows, list): raise ValueError('WINDOW_REGIONS_INVALID')
            result[name] = []
            for rect in rows:
                if not isinstance(rect, list) or len(rect) != 4 or any(
                    isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
                    for x in rect
                ): raise ValueError('WINDOW_REGIONS_INVALID')
                if rect[2] < rect[0] or rect[3] < rect[1]: raise ValueError('WINDOW_REGIONS_INVALID')
                result[name].append(tuple(x * scale for x in rect))
        self.regions = result
        return self.state()

    def state(self):
        if not self.installed: return {'status': 'NATIVE_FRAME', 'maximized': False}
        rect = wintypes.RECT()
        self.user.GetClientRect(self.hwnd, ctypes.byref(rect))
        return {'status': 'READY', 'maximized': bool(self.user.IsZoomed(self.hwnd)),
                'minimized': bool(self.user.IsIconic(self.hwnd)),
                'visible': bool(self.user.IsWindowVisible(self.hwnd)),
                'client_width': rect.right, 'client_height': rect.bottom,
                'dpi': int(self.user.GetDpiForWindow(self.hwnd)),
                'native_resize_frame': True, 'native_hit_test': True,
                'native_caption_present': (self.user.GetWindowLongW(self.hwnd,-16) & 0xC00000) == 0xC00000,
                'native_caption_style': int(self.user.GetWindowLongW(self.hwnd,-16)) & 0xFFFFFFFF,
                'last_error': self.last_error}

    def command(self, action):
        if action not in {'MINIMIZE', 'MAXIMIZE', 'DRAG', 'MAXIMIZE_HOVER', 'SYSTEM_MENU'}:
            raise ValueError('WINDOW_ACTION_INVALID')
        if not self.installed: raise RuntimeError('WINDOW_CHROME_NOT_READY')
        from System import Action
        def run():
            if action == 'MINIMIZE': self.user.SendMessageW(self.hwnd, 0x112, 0xF020, 0)
            elif action == 'MAXIMIZE':
                self.user.SendMessageW(self.hwnd, 0x112, 0xF120 if self.user.IsZoomed(self.hwnd) else 0xF030, 0)
            elif action == 'DRAG':
                self.user.ReleaseCapture()
                # Windows owns move/restore/Snap. No Python mouse-follow loop.
                self.user.SendMessageW(self.hwnd, 0xA1, 2, 0)
            elif action == 'MAXIMIZE_HOVER':
                point = wintypes.POINT(); self.user.GetCursorPos(ctypes.byref(point))
                self.user.SendMessageW(self.hwnd, 0xA0, 9, (point.x & 65535) | ((point.y & 65535) << 16))
            elif action == 'SYSTEM_MENU':
                point = wintypes.POINT(); self.user.GetCursorPos(ctypes.byref(point))
                menu = self.user.GetSystemMenu(self.hwnd, False)
                chosen = self.user.TrackPopupMenu(menu, 0x100, point.x, point.y, 0, self.hwnd, None)
                if chosen: self.user.PostMessageW(self.hwnd, 0x112, chosen, 0)
        self.window.native.BeginInvoke(Action(run))
        return {'status': 'QUEUED', 'action': action}

    def install(self):
        """Called synchronously at before_show; keep WS_THICKFRAME and system menu."""
        if self.installed: return
        user = self.user = ctypes.WinDLL('user32', use_last_error=True)
        comctl = self.comctl = ctypes.WinDLL('comctl32', use_last_error=True)
        hwnd = self.hwnd = int(self.window.native.Handle.ToInt64())
        pointer = ctypes.c_ssize_t
        callback_type = ctypes.WINFUNCTYPE(pointer, wintypes.HWND, wintypes.UINT,
            ctypes.c_size_t, pointer, ctypes.c_size_t, ctypes.c_size_t)
        comctl.DefSubclassProc.argtypes = [wintypes.HWND,wintypes.UINT,ctypes.c_size_t,pointer]
        comctl.DefSubclassProc.restype = pointer
        comctl.SetWindowSubclass.argtypes = [wintypes.HWND, callback_type, ctypes.c_size_t, ctypes.c_size_t]
        comctl.RemoveWindowSubclass.argtypes = [wintypes.HWND, callback_type, ctypes.c_size_t]
        for name in ('GetDpiForWindow','IsZoomed','IsIconic','IsWindowVisible'):
            getattr(user,name).argtypes = [wintypes.HWND]
        user.GetSystemMetricsForDpi.argtypes = [ctypes.c_int,wintypes.UINT]
        user.GetClientRect.argtypes = [wintypes.HWND,ctypes.POINTER(wintypes.RECT)]
        user.GetWindowRect.argtypes = user.GetClientRect.argtypes
        user.ScreenToClient.argtypes = [wintypes.HWND,ctypes.POINTER(wintypes.POINT)]
        user.ShowWindow.argtypes = [wintypes.HWND,ctypes.c_int]
        user.GetWindowLongW.argtypes = [wintypes.HWND,ctypes.c_int]
        user.SetWindowLongW.argtypes = [wintypes.HWND,ctypes.c_int,ctypes.c_long]
        user.SendMessageW.argtypes = [wintypes.HWND,wintypes.UINT,ctypes.c_size_t,pointer]
        user.SendMessageW.restype = pointer
        user.PostMessageW.argtypes = user.SendMessageW.argtypes
        user.SetWindowPos.argtypes = [wintypes.HWND,wintypes.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.UINT]
        user.GetSystemMenu.argtypes = [wintypes.HWND,wintypes.BOOL];user.GetSystemMenu.restype = wintypes.HMENU
        user.TrackPopupMenu.argtypes = [wintypes.HMENU,wintypes.UINT,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.HWND,ctypes.c_void_p]
        def border():
            dpi = user.GetDpiForWindow(hwnd)
            return user.GetSystemMetricsForDpi(32,dpi) + user.GetSystemMetricsForDpi(92,dpi)
        self.border = border
        def proc(handle, msg, wp, lp, _id, _data):
            try:
                if msg in (0x11, 0x16):
                    result=self.session_event(msg,wp)
                    if msg == 0x11 or not wp:
                        return result
                    # Cleanup is complete and _exit_requested is set before
                    # WinForms receives confirmed session end. No JS roundtrip.
                    return comctl.DefSubclassProc(handle,msg,wp,lp)
                if msg == 0x112:  # WM_SYSCOMMAND; native menu/keyboard/buttons share this path.
                    command = wp & 0xFFF0
                    if command in (0xF020, 0xF030) and not user.IsZoomed(handle) and not user.IsIconic(handle):
                        bounds=wintypes.RECT();user.GetWindowRect(handle,ctypes.byref(bounds))
                        self._normal_extent=(bounds.right-bounds.left,bounds.bottom-bounds.top,user.GetDpiForWindow(handle))
                    result=comctl.DefSubclassProc(handle,msg,wp,lp)
                    if command == 0xF120 and self._normal_extent and not user.IsZoomed(handle) and not user.IsIconic(handle):
                        # WinForms derives restore bounds from client size using
                        # its standard caption metrics. Our custom NCCALCSIZE
                        # has no caption, so restore the measured outer extent,
                        # not a guessed caption-height offset. Windows still owns
                        # position, work-area selection and the move/resize loop.
                        width,height,dpi=self._normal_extent
                        scale=user.GetDpiForWindow(handle)/dpi
                        user.SetWindowPos(handle,None,0,0,round(width*scale),round(height*scale),0x16)
                    return result
                if msg == 0x83 and wp:  # WM_NCCALCSIZE: first RECT is new window bounds.
                    rect = ctypes.cast(lp,ctypes.POINTER(wintypes.RECT)).contents
                    edge = border()
                    rect.left += edge;rect.right -= edge;rect.bottom -= edge
                    # A normal window must let the WebView paint the whole top:
                    # reserving a partial native caption exposes a DWM color band.
                    # Maximized resize margins are outside the monitor work area.
                    if user.IsZoomed(handle):rect.top += edge
                    return 0
                if msg == 0x84:
                    point=wintypes.POINT(ctypes.c_short(lp & 65535).value,ctypes.c_short((lp>>16)&65535).value)
                    user.ScreenToClient(handle,ctypes.byref(point))
                    rect=wintypes.RECT();user.GetClientRect(handle,ctypes.byref(rect))
                    return hit_test(point.x,point.y,rect.right,rect.bottom,border(),
                                    bool(user.IsZoomed(handle)),self.regions)
                if msg == 0x82:  # WM_NCDESTROY; lifetime pinned by controller until process exit.
                    comctl.RemoveWindowSubclass(handle,self._callback,106)
                    self.installed=False
            except Exception as error:
                self.last_error=type(error).__name__
            return comctl.DefSubclassProc(handle,msg,wp,lp)
        self._callback=callback_type(proc)
        if not comctl.SetWindowSubclass(hwnd,self._callback,106,0):
            raise ctypes.WinError(ctypes.get_last_error())
        self.installed=True
        # Retain the existing WinForms sizing model and native system commands.
        # Remove the caption before Show, without switching FormBorderStyle.
        # NCCALCSIZE alone does not remove DWM's caption buttons (build106).
        # Keep the existing custom bar's drag/command code and geometry intact.
        style=user.GetWindowLongW(hwnd,-16)
        user.SetWindowLongW(hwnd,-16,(style & ~0xC00000) | 0x40000 | 0x80000 | 0x10000 | 0x20000)
        user.SetWindowPos(hwnd,None,0,0,0,0,0x27)  # FRAMECHANGED | NOMOVE | NOSIZE | NOZORDER
        def on_restored():
            # Taskbar activation / pywebview.restore can bypass WM_SYSCOMMAND.
            # Repair only the exact standard-caption inflation signature; never
            # replace an OS-selected Snap extent or an arbitrary user's resize.
            from System import Action
            def repair():
                if not self.installed or not self._normal_extent or user.IsZoomed(hwnd) or user.IsIconic(hwnd): return
                width,height,dpi=self._normal_extent
                current_dpi=user.GetDpiForWindow(hwnd)
                expected_width,expected_height=round(width*current_dpi/dpi),round(height*current_dpi/dpi)
                bounds=wintypes.RECT();user.GetWindowRect(hwnd,ctypes.byref(bounds))
                if (bounds.right-bounds.left==expected_width and
                    bounds.bottom-bounds.top==expected_height+user.GetSystemMetricsForDpi(4,current_dpi)):
                    user.SetWindowPos(hwnd,None,0,0,expected_width,expected_height,0x16)
            self.window.native.BeginInvoke(Action(repair))
        self.window.events.restored += on_restored
        self._on_restored=on_restored
        # pywebview Event stores callback results in a set; an event handler must
        # not return our (unhashable) status dictionary. Query state separately.
