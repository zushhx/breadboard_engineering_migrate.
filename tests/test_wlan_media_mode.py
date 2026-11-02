"""Ownership/failure paths without calling Windows or accessing a device."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import wlan_media_mode as mode

AP = dict(interface_guid='00000000-0000-0000-0000-000000000001',
          ssid_hex=mode.AP_SSID_HEX)


class Backend:
    def __init__(self, values=(False, True, False), fail_set=None):
        self.values = iter(values)
        self.writes = []
        self.fail_set = fail_set
        self.closed = False
    def __enter__(self): return self
    def __exit__(self, *exc): self.closed = True
    def query(self):
        value = next(self.values)
        if isinstance(value, Exception): raise value
        return value
    def set(self, value):
        self.writes.append(value)
        if value is self.fail_set: raise OSError('set failed')


class MediaModeTests(unittest.TestCase):
    def test_native_nonzero_bool_and_memory_free(self):
        for value in (0, 1, -1, 7):
            with self.subTest(value=value):
                class Dll:
                    def __init__(self): self.value = mode.w.BOOL(value); self.freed = False
                    def WlanQueryInterface(self, handle, guid, opcode, reserved, size, data, kind):
                        mode.c.cast(size, mode.c.POINTER(mode.w.DWORD))[0] = 4
                        mode.c.cast(data, mode.c.POINTER(mode.c.c_void_p))[0] = mode.c.addressof(self.value)
                        return 0
                    def WlanFreeMemory(self, data): self.freed = True
                api = object.__new__(mode.NativeBackend)
                api.dll = Dll(); api.handle = None; api.guid = mode.wlan.GUID()
                self.assertEqual(api.query(), bool(value)); self.assertTrue(api.dll.freed)

    def test_native_close_failure_keeps_exception_chain(self):
        class Dll:
            def WlanCloseHandle(self, handle, reserved): return 5
        api = object.__new__(mode.NativeBackend); api.dll = Dll(); api.handle = None
        try:
            try: raise ValueError('original test failure')
            finally: api.__exit__(None, None, None)
        except OSError as error:
            self.assertIsInstance(error.__context__, ValueError)
        else: self.fail('close error hidden')

    def use(self, api, events=None, current=None):
        events = events if events is not None else []
        return mode.temporary_media_mode(AP, lambda **row: events.append(row),
            backend=api, current_connection=current or (lambda: AP))

    def test_success_withdraws_own_request_and_closes(self):
        api = Backend(); events = []
        with self.use(api, events): self.assertEqual(api.writes, [True])
        self.assertEqual(api.writes, [True, False]); self.assertTrue(api.closed)
        self.assertFalse(events[-1]['aggregate_enabled'])

    def test_already_enabled_is_read_only(self):
        api = Backend((True,))
        with self.assertRaisesRegex(RuntimeError, 'already enabled'):
            with self.use(api): self.fail('body entered')
        self.assertEqual(api.writes, []); self.assertTrue(api.closed)

    def test_enable_failure_does_not_revoke_another_client(self):
        api = Backend(fail_set=True)
        with self.assertRaises(OSError):
            with self.use(api): self.fail('body entered')
        self.assertEqual(api.writes, [True]); self.assertTrue(api.closed)

    def test_failed_readback_still_withdraws(self):
        for readback in (False, OSError('query failed')):
            with self.subTest(readback=readback):
                api = Backend((False, readback, False))
                with self.assertRaises((OSError, RuntimeError)):
                    with self.use(api): self.fail('body entered')
                self.assertEqual(api.writes, [True, False]); self.assertTrue(api.closed)

    def test_body_failure_withdraws(self):
        api = Backend()
        with self.assertRaisesRegex(ValueError, 'test failure'):
            with self.use(api): raise ValueError('test failure')
        self.assertEqual(api.writes, [True, False]); self.assertTrue(api.closed)

    def test_cleanup_failure_is_visible(self):
        api = Backend(fail_set=False); events = []
        with self.assertRaises(OSError):
            with self.use(api, events): pass
        self.assertEqual(events[-1]['event'], 'media_cleanup_error')
        self.assertTrue(api.closed)

    def test_other_client_aggregate_true_is_reported(self):
        api = Backend((False, True, True)); events = []
        with self.use(api, events): pass
        self.assertEqual(api.writes, [True, False])
        self.assertTrue(events[-1]['aggregate_enabled'])

    def test_wrong_or_changed_association_never_sets(self):
        other = dict(AP, ssid_hex=b'other'.hex())
        for sequence in ((other,), (AP, other)):
            api = Backend(); it = iter(sequence)
            with self.assertRaisesRegex(RuntimeError, 'AP'):
                with self.use(api, current=lambda: next(it)): self.fail('body entered')
            self.assertEqual(api.writes, [])

    def test_log_failure_after_enable_still_withdraws(self):
        api = Backend()
        def emit(**row):
            if row['event'] == 'media_enabled': raise OSError('log failure')
        with self.assertRaisesRegex(OSError, 'log failure'):
            with mode.temporary_media_mode(AP, emit, backend=api, current_connection=lambda: AP):
                self.fail('body entered')
        self.assertEqual(api.writes, [True, False]); self.assertTrue(api.closed)


if __name__ == '__main__': unittest.main()
