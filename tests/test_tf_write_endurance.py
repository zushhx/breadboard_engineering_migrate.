from pathlib import Path
import sys
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import tf_write_endurance as endurance


class EnduranceTests(unittest.TestCase):
    def test_missing_approval_stops_before_session(self):
        with patch.object(endurance, 'approved_inputs', return_value=[]), patch.object(endurance, 'Session') as session:
            with self.assertRaisesRegex(RuntimeError, 'approval-record'):
                endurance.run(None)
            session.assert_not_called()

    def test_wrong_scope_stops_before_session(self):
        with patch.object(endurance, 'approved_inputs', return_value=[]), \
             patch.object(endurance, 'require_approval', return_value={'scope': {}}), \
             patch.object(endurance, 'Session') as session:
            with self.assertRaisesRegex(RuntimeError, 'scope'):
                endurance.run(Path('test-only.json'))
            session.assert_not_called()

    def test_reset_only_before_observed_sync_or_completion(self):
        class Port:
            in_waiting = 0
            def __init__(self, reply): self.reply = reply; self.rts_changes = []
            def read(self, _): return self.reply.encode('ascii')
            def write(self, _): pass
            def flush(self): pass
            @property
            def rts(self): return False
            @rts.setter
            def rts(self, value): self.rts_changes.append(value)
        class Log:
            def emit(self, _): pass
        good = '[tfrw] phase=create_new fatfs=0\r\n'
        with patch.object(endurance.time, 'sleep'):
            port = Port(good)
            result = endurance.interrupt_during_write(Log(), port, 'A1000001')
            self.assertEqual(port.rts_changes, [True, False])
            self.assertFalse(result['tf_power_removed'])
            for reply in [good + '[tfrw] phase=f_sync fatfs=0', good + '[tfrw] phase=close fatfs=0',
                          '[tfrw] PASS phase=write_sync_close_readback', '[tfrw] FAIL phase=write']:
                port = Port(reply)
                with self.subTest(reply=reply), self.assertRaises(RuntimeError):
                    endurance.interrupt_during_write(Log(), port, 'A1000001')
                self.assertEqual(port.rts_changes, [])


if __name__ == '__main__':
    unittest.main()
