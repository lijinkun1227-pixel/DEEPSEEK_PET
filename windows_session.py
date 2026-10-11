"""Observe workstation lock and power notifications without monitoring apps/input."""
import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QObject, QAbstractNativeEventFilter, Signal


def desktop_available() -> bool:
    if sys.platform != "win32":
        return True
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    handle = user32.OpenInputDesktop(0, False, 0x0001)
    if not handle:
        return False
    try:
        name = ctypes.create_unicode_buffer(256)
        needed = wintypes.DWORD()
        user32.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        if not user32.GetUserObjectInformationW(handle, 2, name, ctypes.sizeof(name), ctypes.byref(needed)):
            return False
        return name.value.lower() == "default"
    finally:
        user32.CloseDesktop(handle)


class SessionFilter(QAbstractNativeEventFilter):
    def __init__(self, monitor):
        super().__init__()
        self.monitor = monitor

    def nativeEventFilter(self, event_type, message):
        if sys.platform == "win32":
            msg = wintypes.MSG.from_address(int(message))
            self.monitor.handle_message(msg.message, int(msg.wParam))
        return False, 0


class WindowsSession(QObject):
    changed = Signal(bool)

    def __init__(self, app, window, parent=None):
        super().__init__(parent)
        self.app = app
        self.available = desktop_available()
        self.suspended = False
        self.hwnd = int(window.winId())
        self.filter = SessionFilter(self)
        self.app.installNativeEventFilter(self.filter)
        self.wts = None
        if sys.platform == "win32":
            self.wts = ctypes.WinDLL("wtsapi32")
            self.wts.WTSRegisterSessionNotification.argtypes = [wintypes.HWND, wintypes.DWORD]
            self.wts.WTSUnRegisterSessionNotification.argtypes = [wintypes.HWND]
            self.wts.WTSRegisterSessionNotification(self.hwnd, 0)

    def set_available(self, value):
        if self.available != value:
            self.available = value
            self.changed.emit(value)

    def handle_message(self, message, parameter):
        if message == 0x02B1:  # WM_WTSSESSION_CHANGE
            if parameter in (0x7, 0x2, 0x4):  # Lock or session disconnect.
                self.set_available(False)
            elif parameter in (0x8, 0x1, 0x3):
                self.refresh()
        elif message == 0x0218:  # WM_POWERBROADCAST
            if parameter == 0x4:
                self.suspended = True
                self.set_available(False)
            elif parameter in (0x7, 0x12):
                self.suspended = False
                self.refresh()

    def refresh(self):
        self.set_available(not self.suspended and desktop_available())

    def close(self):
        self.app.removeNativeEventFilter(self.filter)
        if self.wts is not None:
            self.wts.WTSUnRegisterSessionNotification(self.hwnd)
