from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import sensor_recorder_batch as batch


class RecorderBatch(unittest.TestCase):
    def test_no_approval_never_loads_inputs_or_opens_session(self):
        with patch.object(batch, 'inputs') as inputs, patch.object(batch.tf, 'Session') as session:
            with self.assertRaisesRegex(RuntimeError, 'approval'): batch.initial(None)
            inputs.assert_not_called(); session.assert_not_called()

    def test_wrong_scope_stops_before_device(self):
        with patch.object(batch, 'inputs', return_value=(None, None, {}, [])), \
             patch.object(batch, 'require_approval', return_value={'scope': {}}), \
             patch.object(batch.tf, 'Session') as session:
            with self.assertRaisesRegex(RuntimeError, 'scope'): batch.initial(Path('test-fixture'))
            session.assert_not_called()

    def test_terminal_reply_requires_readback_samples_digest_and_gate(self):
        text = '[rec] verify=1 samples=266670 blocks=1\n[rec] PASS run=DEADBEEF samples=266670 errors=0 fifo_high=55 queue_high=3 write_us=2000 sync_us=3000 sha256='+('a'*64)+' write_gate=0 precision_accepted=0\n'
        self.assertEqual(batch.accepted_reply(text, 'DEADBEEF', 10)['samples'], 266670)
        for bad in (text.replace('errors=0', 'errors=2'), text.replace('verify=1', 'verify=0'),
                    text.replace('write_gate=0', 'write_gate=1'), text.replace('samples=266670', 'samples=20'),
                    text.rstrip('\n'), text.replace('sha256='+('a'*64), 'sha256=BAD')):
            with self.subTest(bad=bad), self.assertRaises(RuntimeError): batch.accepted_reply(bad, 'DEADBEEF', 10)


if __name__ == '__main__': unittest.main()
