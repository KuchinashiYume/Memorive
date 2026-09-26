"""A Windows job contains only the executable launched for a review session."""
from __future__ import annotations

import ctypes
from ctypes import wintypes as W
import os
import subprocess
import time

k = ctypes.WinDLL('kernel32', use_last_error=True)
SIZE_T = ctypes.c_size_t


class BasicLimits(ctypes.Structure):
    _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
        ('LimitFlags', W.DWORD), ('MinimumWorkingSetSize', SIZE_T), ('MaximumWorkingSetSize', SIZE_T),
        ('ActiveProcessLimit', W.DWORD), ('Affinity', SIZE_T), ('PriorityClass', W.DWORD), ('SchedulingClass', W.DWORD)]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [('BasicLimitInformation', BasicLimits), ('IoInfo', ctypes.c_uint64 * 6),
        ('ProcessMemoryLimit', SIZE_T), ('JobMemoryLimit', SIZE_T), ('PeakProcessMemoryUsed', SIZE_T), ('PeakJobMemoryUsed', SIZE_T)]


class StartupInfo(ctypes.Structure):
    _fields_ = [('cb', W.DWORD), ('lpReserved', W.LPWSTR), ('lpDesktop', W.LPWSTR), ('lpTitle', W.LPWSTR),
        ('dwX', W.DWORD), ('dwY', W.DWORD), ('dwXSize', W.DWORD), ('dwYSize', W.DWORD),
        ('dwXCountChars', W.DWORD), ('dwYCountChars', W.DWORD), ('dwFillAttribute', W.DWORD),
        ('dwFlags', W.DWORD), ('wShowWindow', W.WORD), ('cbReserved2', W.WORD), ('lpReserved2', ctypes.c_void_p),
        ('hStdInput', W.HANDLE), ('hStdOutput', W.HANDLE), ('hStdError', W.HANDLE)]


class ProcessInfo(ctypes.Structure):
    _fields_ = [('hProcess', W.HANDLE), ('hThread', W.HANDLE), ('dwProcessId', W.DWORD), ('dwThreadId', W.DWORD)]


k.CreateJobObjectW.argtypes = [ctypes.c_void_p, W.LPCWSTR]
k.CreateJobObjectW.restype = W.HANDLE
k.SetInformationJobObject.argtypes = [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD]
k.AssignProcessToJobObject.argtypes = [W.HANDLE, W.HANDLE]
k.TerminateJobObject.argtypes = [W.HANDLE, W.UINT]
k.CloseHandle.argtypes = [W.HANDLE]
k.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
k.GetExitCodeProcess.argtypes = [W.HANDLE, ctypes.POINTER(W.DWORD)]
k.ResumeThread.argtypes = [W.HANDLE]
k.ResumeThread.restype = W.DWORD
k.TerminateProcess.argtypes = [W.HANDLE, W.UINT]
k.CreateProcessW.argtypes = [W.LPCWSTR, W.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, W.BOOL, W.DWORD,
    ctypes.c_void_p, W.LPCWSTR, ctypes.POINTER(StartupInfo), ctypes.POINTER(ProcessInfo)]
k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
k.OpenProcess.restype = W.HANDLE
k.GetProcessTimes.argtypes = [W.HANDLE, ctypes.POINTER(W.FILETIME), ctypes.POINTER(W.FILETIME), ctypes.POINTER(W.FILETIME), ctypes.POINTER(W.FILETIME)]


def process_identity(pid):
    handle = k.OpenProcess(0x1000 | 0x100000, False, pid)
    if not handle:
        return None
    try:
        if k.WaitForSingleObject(handle, 0) == 0:
            return None
        times = [W.FILETIME() for _ in range(4)]
        if not k.GetProcessTimes(handle, *(ctypes.byref(item) for item in times)):
            return None
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:
        k.CloseHandle(handle)


class OwnedProcess:
    def __init__(self, command, environment, cwd):
        self.job = k.CreateJobObjectW(None, None)
        self.handle = None
        if not self.job:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())
        startup, result = StartupInfo(), ProcessInfo()
        startup.cb = ctypes.sizeof(startup)
        environment_block = ctypes.create_unicode_buffer('\0'.join(f'{key}={value}' for key, value in sorted(environment.items(), key=lambda row: row[0].upper())) + '\0\0')
        command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline([str(item) for item in command]))
        # Suspend before assigning the job: a child can never escape this ownership boundary.
        accepted = k.CreateProcessW(str(command[0]), command_line, None, None, False,
            0x00000004 | 0x00000400 | 0x08000000, environment_block, str(cwd), ctypes.byref(startup), ctypes.byref(result))
        if not accepted:
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())
        self.handle, self.pid = result.hProcess, int(result.dwProcessId)
        try:
            if not k.AssignProcessToJobObject(self.job, self.handle):
                k.TerminateProcess(self.handle, 1)
                raise ctypes.WinError(ctypes.get_last_error())
            if k.ResumeThread(result.hThread) == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
        except Exception:
            self.close()
            raise
        finally:
            k.CloseHandle(result.hThread)

    def poll(self):
        if not self.handle:
            return 0
        code = W.DWORD()
        if not k.GetExitCodeProcess(self.handle, ctypes.byref(code)):
            return -1
        return None if code.value == 259 else code.value

    def close(self):
        if self.job:
            k.TerminateJobObject(self.job, 0)
            k.CloseHandle(self.job)
            self.job = None
        if self.handle:
            k.WaitForSingleObject(self.handle, 5000)
            k.CloseHandle(self.handle)
            self.handle = None


def watchdog(root, parent_pid, identity):
    """Kernel kills the owned job on host death; this helper removes its data."""
    from common import cleanup_failure, delete_session, delete_ui_profile, read_json, write_json, now
    while process_identity(parent_pid) == identity:
        time.sleep(.3)
    time.sleep(.5)
    for path in (root / 'owned_sessions').glob('review-*.json'):
        row = read_json(path)
        if row.get('owner_pid') != parent_pid or row.get('owner_identity') != identity or row.get('status') == 'CLEANED':
            continue
        try:
            receipt = delete_session(root, row['session_id'])
            receipt['trigger'] = 'CONSOLE_PROCESS_EXIT'
            write_json(root / 'receipts' / path.name, receipt)
            write_json(root / 'monitor' / 'latest_snapshot.json', {'state': 'CLEANED', 'observed_at': now(), 'last_cleanup': receipt,
                'connected_to_memorive': False, 'business_content_retained': False})
        except Exception as error:
            write_json(root / 'receipts' / path.name, {'session_id': row['session_id'], 'status': 'CLEANUP_FAILED',
                **cleanup_failure(error), 'completed_at': now(), 'remaining_data_not_claimed_clean': True})
    for path in (root / 'ui_profiles').glob('Memorive-Console-*.json'):
        row = read_json(path)
        if row.get('owner_pid') == parent_pid and row.get('owner_identity') == identity and row.get('status') != 'CLEANED':
            try:
                delete_ui_profile(root, row['profile_id'])
            except Exception as error:
                write_json(root / 'receipts' / path.name, {'profile_id': row['profile_id'], 'status': 'CLEANUP_FAILED', **cleanup_failure(error)})
