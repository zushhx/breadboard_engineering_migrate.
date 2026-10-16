from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import ui_live_trial as trial

class UiLiveTrialTests(unittest.TestCase):
    def test_missing_approval_before_inputs_or_hardware(self):
        with patch.object(trial,'inputs') as inputs,patch.object(trial.tf,'Session') as session:
            with self.assertRaisesRegex(RuntimeError,'explicit approval'):trial.run(None)
            inputs.assert_not_called();session.assert_not_called()

    def test_wrong_scope_or_readiness_before_hardware(self):
        for record in ({'scope':{}},{'scope':trial.SCOPE}):
            with patch.object(trial,'inputs',return_value=(None,None,[])),patch.object(trial,'require_approval',return_value=record),patch.object(trial.tf,'Session') as session:
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
