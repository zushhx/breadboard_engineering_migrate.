import unittest
from pathlib import Path
import sys
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import release_v010_ap_fifo_trial as fifo
import release_v010_ap_writer_trial as writer


class APSchedulingGuards(unittest.TestCase):
    def test_missing_approval_loads_no_candidate_or_private_proof(self):
        for trial in (fifo,writer):
            with self.subTest(trial=trial.OPERATION),patch.object(trial,'inputs',side_effect=AssertionError('must not load inputs')):
                with self.assertRaisesRegex(RuntimeError,'Explicit candidate approval'):
                    trial.validate(None)

    def test_a_scope_change_is_rejected(self):
        with patch.object(fifo,'inputs',return_value=('candidate','old',[])),patch.object(fifo,'require_approval',return_value={'scope':{}}):
            with self.assertRaisesRegex(RuntimeError,'scope mismatch'):fifo.validate('fixture')

    def test_b_cannot_skip_a_actual_smoke_proof(self):
        with patch.object(writer,'PREVIOUS_SMOKE',None),patch.object(writer,'inputs',side_effect=AssertionError('must not load inputs')):
            with self.assertRaisesRegex(RuntimeError,'Previous candidate smoke proof required'):writer.validate('fixture')

    def test_b_rejects_proof_outside_hardware_evidence(self):
        with patch.object(writer,'PREVIOUS_SMOKE',Path(__file__)),patch.object(writer,'inputs',return_value=('candidate','old',[])):
            with self.assertRaisesRegex(RuntimeError,'local hardware result'):writer.validate('fixture')

    def test_affected_sectors_and_comparison_chain(self):
        for trial in (fifo,writer):
            self.assertEqual(trial.SCOPE['backup_end'],0x123000)
            self.assertFalse(trial.SCOPE['reset_from_saved_panic'])
        self.assertEqual(writer.OLD_SOURCE,fifo.SOURCE)
        self.assertEqual(writer.OLD_SHA,fifo.CANDIDATE)
        self.assertFalse(fifo.SCOPE['requires_previous_smoke'])
        self.assertTrue(writer.SCOPE['requires_previous_smoke'])
