#!/usr/bin/env python3
"""Print AXURL values from the running Huibo terminal app.

This is intentionally best-effort and read-only. It exits successfully with no
output when the app is closed, Accessibility permission is unavailable, or the
current window does not expose URL attributes.
"""
from __future__ import annotations

import ctypes
import re
import subprocess
import sys
from ctypes import c_bool, c_char_p, c_int, c_long, c_ulong, c_void_p
from ctypes.util import find_library

TARGET_BUNDLE_ID = "com.shhy.macHB"
MAX_DEPTH = 12
K_CF_STRING_ENCODING_UTF8 = 0x08000100
URL_RE = re.compile(r"https?://[^\s<>'\"]+")


def main() -> None:
    pid = _huibo_pid()
    if not pid:
        return
    try:
        ax = _AX()
    except Exception:
        return
    if not ax.is_trusted():
        return
    app = ax.create_application(pid)
    seen: set[str] = set()
    windows = ax.copy_attribute(app, "AXWindows")
    if windows and ax.is_array(windows):
        try:
            for child in ax.array_items(windows):
                _walk(ax, child, 0, seen)
        finally:
            ax.release(windows)
    else:
        if windows:
            ax.release(windows)
        _walk(ax, app, 0, seen)


def _huibo_pid() -> int | None:
    script = (
        'tell application "System Events" to get unix id of first process '
        f'whose bundle identifier is "{TARGET_BUNDLE_ID}"'
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def _walk(ax: "_AX", element: int, depth: int, seen: set[str]) -> None:
    if depth > MAX_DEPTH:
        return
    for name in ("AXURL", "AXDocument", "AXDescription", "AXValue", "AXTitle"):
        value_ref = ax.copy_attribute(element, name)
        if not value_ref:
            continue
        try:
            text = ax.to_string(value_ref)
            for url in URL_RE.findall(text):
                if url not in seen:
                    seen.add(url)
                    print(url)
        finally:
            ax.release(value_ref)
    for attr in ("AXChildren", "AXVisibleChildren"):
        children = ax.copy_attribute(element, attr)
        if not children:
            continue
        try:
            if ax.is_array(children):
                for child in ax.array_items(children):
                    _walk(ax, child, depth + 1, seen)
        finally:
            ax.release(children)


class _AX:
    def __init__(self) -> None:
        app_services = find_library("ApplicationServices")
        core_foundation = find_library("CoreFoundation")
        if not app_services or not core_foundation:
            raise RuntimeError("macOS frameworks not found")
        self.ax = ctypes.CDLL(app_services)
        self.cf = ctypes.CDLL(core_foundation)
        self._configure()
        self.cf_array_type = self.cf.CFArrayGetTypeID()
        self.cf_string_type = self.cf.CFStringGetTypeID()
        self.cf_url_type = self.cf.CFURLGetTypeID()

    def _configure(self) -> None:
        self.ax.AXIsProcessTrusted.restype = c_bool
        self.ax.AXUIElementCreateApplication.argtypes = [c_int]
        self.ax.AXUIElementCreateApplication.restype = c_void_p
        self.ax.AXUIElementCopyAttributeValue.argtypes = [c_void_p, c_void_p, ctypes.POINTER(c_void_p)]
        self.ax.AXUIElementCopyAttributeValue.restype = c_int

        self.cf.CFArrayGetTypeID.restype = c_ulong
        self.cf.CFArrayGetCount.argtypes = [c_void_p]
        self.cf.CFArrayGetCount.restype = c_long
        self.cf.CFArrayGetValueAtIndex.argtypes = [c_void_p, c_long]
        self.cf.CFArrayGetValueAtIndex.restype = c_void_p
        self.cf.CFGetTypeID.argtypes = [c_void_p]
        self.cf.CFGetTypeID.restype = c_ulong
        self.cf.CFRelease.argtypes = [c_void_p]
        self.cf.CFStringCreateWithCString.argtypes = [c_void_p, c_char_p, c_ulong]
        self.cf.CFStringCreateWithCString.restype = c_void_p
        self.cf.CFStringGetCString.argtypes = [c_void_p, c_char_p, c_long, c_ulong]
        self.cf.CFStringGetCString.restype = c_bool
        self.cf.CFStringGetLength.argtypes = [c_void_p]
        self.cf.CFStringGetLength.restype = c_long
        self.cf.CFStringGetTypeID.restype = c_ulong
        self.cf.CFURLGetString.argtypes = [c_void_p]
        self.cf.CFURLGetString.restype = c_void_p
        self.cf.CFURLGetTypeID.restype = c_ulong
        self.cf.CFCopyDescription.argtypes = [c_void_p]
        self.cf.CFCopyDescription.restype = c_void_p

    def is_trusted(self) -> bool:
        return bool(self.ax.AXIsProcessTrusted())

    def create_application(self, pid: int) -> int:
        return int(self.ax.AXUIElementCreateApplication(pid))

    def copy_attribute(self, element: int, name: str) -> int:
        attr = self.cf.CFStringCreateWithCString(None, name.encode("utf-8"), K_CF_STRING_ENCODING_UTF8)
        value = c_void_p()
        try:
            result = self.ax.AXUIElementCopyAttributeValue(c_void_p(element), attr, ctypes.byref(value))
            return int(value.value or 0) if result == 0 else 0
        finally:
            if attr:
                self.release(attr)

    def is_array(self, value: int) -> bool:
        return self.cf.CFGetTypeID(c_void_p(value)) == self.cf_array_type

    def array_items(self, value: int) -> list[int]:
        count = self.cf.CFArrayGetCount(c_void_p(value))
        return [int(self.cf.CFArrayGetValueAtIndex(c_void_p(value), i) or 0) for i in range(count)]

    def to_string(self, value: int) -> str:
        type_id = self.cf.CFGetTypeID(c_void_p(value))
        if type_id == self.cf_url_type:
            string_ref = self.cf.CFURLGetString(c_void_p(value))
            return self._cf_string_to_py(string_ref)
        if type_id == self.cf_string_type:
            return self._cf_string_to_py(value)
        desc = self.cf.CFCopyDescription(c_void_p(value))
        try:
            return self._cf_string_to_py(desc)
        finally:
            if desc:
                self.release(desc)

    def _cf_string_to_py(self, value: int) -> str:
        if not value:
            return ""
        length = self.cf.CFStringGetLength(c_void_p(value))
        buf = ctypes.create_string_buffer(max(1024, length * 4 + 1))
        if not self.cf.CFStringGetCString(c_void_p(value), buf, len(buf), K_CF_STRING_ENCODING_UTF8):
            return ""
        return buf.value.decode("utf-8", errors="replace")

    def release(self, value: int) -> None:
        self.cf.CFRelease(c_void_p(value))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
