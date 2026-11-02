"""Read-only CLI and explicitly scoped temporary WLAN media-mode request.

No registry/service changes. A dedicated WLAN handle owns the request.
Mode is a driver hint; actual scanning and timing must be measured separately.
"""
import ctypes as c
from ctypes import wintypes as w
from contextlib import contextmanager
import json

import wlan_profile as wlan

OPCODE = 3
AP_SSID_HEX = b'Breadboard-819110'.hex()


class NativeBackend:
    def __init__(self, snapshot):
        self.guid, _ = wlan.validate_snapshot(snapshot)
        self.dll = wlan.api()
        if c.sizeof(w.BOOL) != 4:
            raise RuntimeError('Unexpected BOOL ABI')
        self.dll.WlanSetInterface.argtypes = [w.HANDLE, c.POINTER(wlan.GUID),
                                             w.DWORD, w.DWORD, c.c_void_p, c.c_void_p]
        self.dll.WlanSetInterface.restype = w.DWORD

    def __enter__(self):
        version = w.DWORD()
        self.handle = w.HANDLE()
        wlan.check(self.dll.WlanOpenHandle(2, None, c.byref(version), c.byref(self.handle)),
                   'Open media-mode WLAN handle')
        return self

    def __exit__(self, *exc):
        wlan.check(self.dll.WlanCloseHandle(self.handle, None), 'Close media-mode WLAN handle')

    def query(self):
        data = c.c_void_p()
        size, kind = w.DWORD(), w.DWORD()
        code = self.dll.WlanQueryInterface(self.handle, c.byref(self.guid), OPCODE,
                                          None, c.byref(size), c.byref(data), c.byref(kind))
        try:
            wlan.check(code, 'Query media streaming mode')
            if not data.value or size.value != c.sizeof(w.BOOL):
                raise RuntimeError('Unexpected media-mode response size')
            value = c.cast(data, c.POINTER(w.BOOL)).contents.value
            return bool(value)
        finally:
            if data.value:
                self.dll.WlanFreeMemory(data)

    def set(self, enabled):
        value = w.BOOL(bool(enabled))
        wlan.check(self.dll.WlanSetInterface(self.handle, c.byref(self.guid), OPCODE,
                                            c.sizeof(value), c.byref(value), None),
                   'Set media streaming mode')


def same_ap(expected, current):
    return (expected['ssid_hex'] == AP_SSID_HEX and
            current['ssid_hex'] == AP_SSID_HEX and
            current['interface_guid'].lower() == expected['interface_guid'].lower())


@contextmanager
def temporary_media_mode(snapshot, emit, *, backend=None, current_connection=None):
    """Enable only on the expected connected AP; always withdraw our request.

    Caller must keep this context around the test, and stop the test if AP
    association changes. Original true is rejected to preserve a clear control.
    Other clients can keep aggregate mode true after we withdraw our request.
    """
    current_connection = current_connection or wlan.current_connection
    if not same_ap(snapshot, current_connection()):
        raise RuntimeError('Expected controller AP/interface before media-mode change')
    with (backend if backend is not None else NativeBackend(snapshot)) as api:
        before = api.query()
        emit(event='media_before', enabled=before)
        if before:
            raise RuntimeError('Media mode already enabled; no distinct off/on control')
        owned = False
        try:
            # Recheck immediately before mutation; a disconnect also makes Set fail.
            if not same_ap(snapshot, current_connection()):
                raise RuntimeError('AP association changed before media-mode enable')
            api.set(True)
            owned = True
            enabled = api.query()
            emit(event='media_enabled', enabled=enabled)
            if not enabled:
                raise RuntimeError('Media mode enable readback failed')
            yield
        finally:
            if owned:
                try:
                    api.set(False)
                    remaining = api.query()
                except Exception as error:
                    emit(event='media_cleanup_error', error=repr(error))
                    raise
                emit(event='media_request_withdrawn', aggregate_enabled=remaining)


def main():
    # Deliberately no CLI setting option: executing this file only queries.
    snapshot = wlan.current_connection()
    with NativeBackend(snapshot) as api:
        enabled = api.query()
    print(json.dumps(dict(interface_guid=snapshot['interface_guid'],
                          ssid_hex=snapshot['ssid_hex'], media_streaming_mode=enabled,
                          changed=False), indent=2))


if __name__ == '__main__':
    main()
