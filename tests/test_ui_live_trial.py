from pathlib import Path
import sys
import unittest
import tempfile
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import ui_live_trial as trial

class UiLiveTrialTests(unittest.TestCase):
    def test_startup_transient_disconnect_retry_is_bounded_and_records_failure(self):
        from http.client import RemoteDisconnected
        from unittest.mock import Mock
        session=Mock()
        with patch.object(trial.web,'read_only_gate',side_effect=[RemoteDisconnected('closed'),{'passed':True}]) as gate,patch.object(trial.time,'sleep'):
            self.assertEqual(trial.startup_gate(session,None,'url',{}),{'passed':True})
            self.assertEqual(gate.call_count,2);session.emit.assert_called_once()
        with patch.object(trial.web,'read_only_gate',side_effect=RemoteDisconnected('closed')) as gate,patch.object(trial.time,'sleep'):
            with self.assertRaises(RemoteDisconnected):trial.startup_gate(session,None,'url',{})
            self.assertEqual(gate.call_count,3)
        for error in (RuntimeError('wrong boot or panic'),ValueError('invalid JSON')):
            with patch.object(trial.web,'read_only_gate',side_effect=error) as gate:
                with self.assertRaises(type(error)):trial.startup_gate(session,None,'url',{})
                self.assertEqual(gate.call_count,1)

    def test_completed_record_allowed_but_active_or_failed_device_rejected(self):
        ready=dict(phase='passed',errors=0,connected=True,ready=True)
        trial.check_quiescent(ready)
        trial.check_quiescent(dict(ready,phase='idle'))
        for change in ({'phase':'recording'},{'phase':'verifying'},{'phase':'failed'},{'errors':1},{'ready':False},{'connected':False}):
            with self.assertRaisesRegex(RuntimeError,'no write'):
                trial.check_quiescent(dict(ready,**change))

    def test_console_encoding_failure_preserves_log_and_does_not_interrupt_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            session=trial.TrialSession(Path(directory)/'capture')
            try:
                with patch('builtins.print',side_effect=UnicodeEncodeError('gbk','\ufffd',0,1,'invalid character')):
                    session.emit('startup replacement: \ufffd; 中文日志')
                self.assertIn('\ufffd; 中文日志',(session.folder/'hardware.log').read_text(encoding='utf-8'))
            finally:
                session.log.close()

    def test_missing_approval_before_inputs_or_hardware(self):
        with patch.object(trial,'inputs') as inputs,patch.object(trial,'TrialSession') as session:
            with self.assertRaisesRegex(RuntimeError,'explicit approval'):trial.run(None)
            inputs.assert_not_called();session.assert_not_called()

    def test_wrong_scope_or_readiness_before_hardware(self):
        for record in ({'scope':{}},{'scope':trial.SCOPE}):
            with patch.object(trial,'inputs',return_value=(None,None,[])),patch.object(trial,'require_approval',return_value=record),patch.object(trial,'TrialSession') as session:
                with self.assertRaisesRegex(RuntimeError,'scope/readiness'):trial.run(Path('fixture'))
                session.assert_not_called()

    def test_backup_covers_and_matches_before_write(self):
        extent=0x11000;boot=b'boot';parts=b'part';old=b'old app'
        data=bytearray(extent);data[:4]=boot;data[0x8000:0x8004]=parts;data[0x10000:0x10007]=old
        trial.check_backup(data,extent,boot,parts,old)
        for offset in (0,0x8000,0x10000):
            bad=data.copy();bad[offset]^=1
            with self.assertRaisesRegex(RuntimeError,'no write'):trial.check_backup(bad,extent,boot,parts,old)
        with self.assertRaisesRegex(RuntimeError,'no write'):trial.check_backup(data[:-1],extent,boot,parts,old)

if __name__=='__main__':unittest.main()
