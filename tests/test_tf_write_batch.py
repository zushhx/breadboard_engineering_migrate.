from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import tf_write_batch as batch


class HardwareBatchGuardTests(unittest.TestCase):
    def test_missing_approval_stops_before_device_discovery(self):
        with patch.object(batch, 'inputs', return_value=(Path('candidate.bin'), [])), \
             patch.object(batch, 'unique_port') as discover:
            with self.assertRaisesRegex(RuntimeError, 'approval-record'):
                batch.run_batch(None)
            discover.assert_not_called()

    def test_incorrect_scope_stops_before_device_discovery(self):
        with patch.object(batch, 'inputs', return_value=(Path('candidate.bin'), [])), \
             patch.object(batch, 'require_approval', return_value={'scope': {}}), \
             patch.object(batch, 'unique_port') as discover:
            with self.assertRaisesRegex(RuntimeError, 'scope'):
                batch.run_batch(Path('test-only.json'))
            discover.assert_not_called()

    def test_hardware_reply_requires_correct_phase_counts_hash_and_closed_gate(self):
        class Port:
            def __init__(self, text): self.text = text
            def write(self, _): pass
            def flush(self): pass
            def read(self, _): return self.text.encode('ascii')
        session = object.__new__(batch.Session)
        good = ('[tfrw] PASS phase=write_sync_close_readback run=A1000001 '
                'bytes_written=1048576 bytes_verified=1048576 sha256=' + batch.PAYLOAD_SHA +
                ' elapsed_ms=100 write_gate=0 write_locked=0\r\n')
        with patch.object(session, 'emit'), patch.object(session, 'collect', return_value=''):
            self.assertIn('PASS', session.test(Port(good), 'A1000001', True))
            for bad in [good.replace('write_gate=0', 'write_gate=1'),
                        good.replace('bytes_written=1048576', 'bytes_written=4095'),
                        good.replace(batch.PAYLOAD_SHA, 'incorrect'),
                        good.replace('write_sync_close_readback', 'read_only_verify'),
                        good.replace('PASS', 'FAIL')]:
                with self.subTest(reply=bad), self.assertRaises(RuntimeError):
                    session.test(Port(bad), 'A1000001', True)


if __name__ == '__main__':
    unittest.main()
