"""No serial/device access: check bringup approval boundary and failure replies."""
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import sensor_monitor_batch as batch


class ModuleBatchTests(unittest.TestCase):
    def test_missing_approval_stops_before_session(self):
        with patch.object(batch, 'inputs', return_value=(None, None, [])), patch.object(batch.tf, 'Session') as session:
            with self.assertRaises(RuntimeError):
                batch.run_initial(None)
            session.assert_not_called()

    def test_invalid_module_reply_never_passes(self):
        good = ('[modules] IIS identity transport=1 who=7B expected=7B valid=1\n'
                '[modules] INA identity address=44 transport=1 maker=5449 die=2260 valid=1\n'
                '[modules] INA transport=1 config=4527 cal=0800 mask=0008 data_valid=1\n'
                '[modules] snapshot_end iis_valid=1 ina_valid=1\n')
        self.assertTrue(batch.snapshot_valid(good))
        for old, new in [('cal=0800', 'cal=0000'), ('who=7B', 'who=FF'), ('ina_valid=1', 'ina_valid=0'),
                         ('transport=1 maker=', 'transport=0 maker='), ('die=2260', 'die=FFFF'),
                         ('mask=0008', 'mask=000C')]:
            self.assertFalse(batch.snapshot_valid(good.replace(old, new)))
        self.assertFalse(batch.snapshot_valid(good.rstrip()))
        self.assertFalse(batch.snapshot_valid(good + 'rst: unexpected\n'))

    def test_wrong_scope_stops_before_session(self):
        with patch.object(batch, 'inputs', return_value=(None, None, [])), patch.object(batch, 'require_approval', return_value={'scope': {}}), patch.object(batch.tf, 'Session') as session:
            with self.assertRaises(RuntimeError):
                batch.run_initial(None)
            session.assert_not_called()

    def test_recovery_identity_checked_before_write(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as directory:
            session = Mock(folder=Path(directory))
            session.esptool.side_effect = RuntimeError('Controller MAC mismatch')
            snapshot = Mock()
            snapshot.read_bytes.return_value = bytes(0x70000)
            with patch.object(batch.tf, 'unique_port', return_value='fake'):
                with self.assertRaises(RuntimeError):
                    batch.restore(session, snapshot)
            session.esptool.assert_called_once_with('fake', ['flash_id'])


if __name__ == '__main__':
    unittest.main()
