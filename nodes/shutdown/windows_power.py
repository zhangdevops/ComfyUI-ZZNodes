"""Request a local, non-forced Windows shutdown without a command interpreter.

Only call after the controller has checked consent and 30 seconds of queue idle.
Importing this module never loads a DLL or changes the machine's power state.
"""

import ctypes
from ctypes import wintypes
import os
import threading

class LUID(ctypes.Structure):
    _fields_ = [('LowPart', wintypes.DWORD), ('HighPart', wintypes.LONG)]


class LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [('Luid', LUID), ('Attributes', wintypes.DWORD)]


class TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [('PrivilegeCount', wintypes.DWORD),
                ('Privileges', LUID_AND_ATTRIBUTES * 1)]


_power_lock = threading.Lock()
TOKEN_QUERY = 0x0008
TOKEN_ADJUST_PRIVILEGES = 0x0020
SE_PRIVILEGE_ENABLED = 0x0002
# Planned shutdown, initiated by an application, other reason.
SHUTDOWN_REASON = 0x80000000 | 0x00040000


def _function(dll, name, restype, argtypes):
    function = getattr(dll, name)
    function.restype = restype
    function.argtypes = argtypes
    return function


def shutdown_windows(log_error):
    """Ask Windows to shut down this PC; raise OSError if not accepted.

No arbitrary command, remote target, restart or forced-close option is exposed.
Enable only an existing shutdown privilege and restore its previous state.
Cleanup failures are reported to the controller's in-memory log callback.
"""
    if os.name != 'nt':
        raise OSError('自动关机仅支持 Windows')
    with _power_lock:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        security = ctypes.WinDLL('advapi32', use_last_error=True)
        get_process = _function(kernel, 'GetCurrentProcess', wintypes.HANDLE, [])
        close_handle = _function(kernel, 'CloseHandle', wintypes.BOOL, [wintypes.HANDLE])
        open_token = _function(security, 'OpenProcessToken', wintypes.BOOL,
                               [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)])
        lookup = _function(security, 'LookupPrivilegeValueW', wintypes.BOOL,
                           [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.POINTER(LUID)])
        adjust = _function(security, 'AdjustTokenPrivileges', wintypes.BOOL,
                           [wintypes.HANDLE, wintypes.BOOL, ctypes.POINTER(TOKEN_PRIVILEGES),
                            wintypes.DWORD, ctypes.POINTER(TOKEN_PRIVILEGES), ctypes.POINTER(wintypes.DWORD)])
        shutdown = _function(security, 'InitiateSystemShutdownExW', wintypes.BOOL,
                             [wintypes.LPWSTR, wintypes.LPWSTR, wintypes.DWORD,
                              wintypes.BOOL, wintypes.BOOL, wintypes.DWORD])

        token = wintypes.HANDLE()
        if not open_token(get_process(), TOKEN_QUERY | TOKEN_ADJUST_PRIVILEGES, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        previous = TOKEN_PRIVILEGES()
        adjusted = False
        try:
            requested = TOKEN_PRIVILEGES()
            requested.PrivilegeCount = 1
            requested.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED
            if not lookup(None, 'SeShutdownPrivilege', ctypes.byref(requested.Privileges[0].Luid)):
                raise ctypes.WinError(ctypes.get_last_error())
            size = wintypes.DWORD()
            ctypes.set_last_error(0)
            if not adjust(token, False, ctypes.byref(requested), ctypes.sizeof(previous),
                          ctypes.byref(previous), ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            adjusted = True
            # Success can still mean ERROR_NOT_ALL_ASSIGNED (1300).
            error = ctypes.get_last_error()
            if error:
                raise ctypes.WinError(error)
            if not shutdown(None, None, 0, False, False, SHUTDOWN_REASON):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            # Cleanup must not hide the original error or a successful request.
            if adjusted and previous.PrivilegeCount:
                ctypes.set_last_error(0)
                restored = adjust(token, False, ctypes.byref(previous), 0, None, None)
                error = ctypes.get_last_error()
                if not restored or error:
                    log_error('恢复关机权限失败：WinError=%s', error)
            if not close_handle(token):
                log_error('关闭 Windows 令牌句柄失败：WinError=%s', ctypes.get_last_error())
