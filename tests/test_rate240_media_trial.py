import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import rate240_media_trial as runner

AP = dict(interface_guid='00000000-0000-0000-0000-000000000001',
          ssid_hex=runner.media.AP_SSID_HEX)


class Backend:
    def __init__(self, reads): self.reads=iter(reads); self.writes=[]; self.closed=False
    def __enter__(self): return self
    def __exit__(self, *exc): self.closed=True
    def query(self): return next(self.reads)
    def set(self, value): self.writes.append(value)


class MediaRunnerTests(unittest.TestCase):
    def test_stage_outcomes_cleanup_before_return_or_raise(self):
        for outcome in ('success', 'body_error', 'mode_lost'):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as tmp:
                reads = [False, True] + ([] if outcome=='body_error' else [outcome=='success']) + [False]
                api=Backend(reads)
                def stage(session):
                    self.assertEqual(api.writes,[True])
                    if outcome=='body_error':raise ValueError('source failed')
                    return 'result'
                with patch.object(runner.media,'NativeBackend',return_value=api), \
                     patch.object(runner.offline.wlan_profile,'current_connection',return_value=AP):
                    call=lambda:runner.wrap_stage(stage)(SimpleNamespace(folder=Path(tmp)))
                    if outcome=='success':self.assertEqual(call(),'result')
                    else:
                        with self.assertRaises((ValueError,RuntimeError)):call()
                self.assertEqual(api.writes,[True,False]);self.assertTrue(api.closed)
                self.assertIn('media_request_withdrawn',(Path(tmp)/'media-mode.jsonl').read_text())

    def test_run_restores_original_functions_after_failure(self):
        ram,stage=runner.offline.trial.ram_stage,runner.offline.trial.stage
        with patch.object(runner,'approved'),patch.object(runner.offline,'run',side_effect=ValueError('failed')):
            with self.assertRaises(ValueError):runner.run(SimpleNamespace())
        self.assertIs(runner.offline.trial.ram_stage,ram)
        self.assertIs(runner.offline.trial.stage,stage)

    def test_non_test_phase_rejected_before_device_access(self):
        with patch.object(runner.offline,'approved') as gate:
            with self.assertRaises(RuntimeError):runner.approved(SimpleNamespace(offline_phase='flash'))
            gate.assert_not_called()


if __name__=='__main__':unittest.main()
