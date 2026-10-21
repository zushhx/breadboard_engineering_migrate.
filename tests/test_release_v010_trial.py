import unittest
from unittest.mock import patch
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import release_v010_trial as trial


class ReleaseTrialGuards(unittest.TestCase):
    def test_missing_approval_does_not_load_inputs(self):
        with patch.object(trial,'inputs',side_effect=AssertionError('must not read inputs')):
            with self.assertRaisesRegex(RuntimeError,'Explicit candidate approval'):
                trial.validate(None)

    def test_changed_scope_stops_before_host_or_serial(self):
        with patch.object(trial,'inputs',return_value=('candidate','old',[])),patch.object(trial,'require_approval',return_value={'scope':{}}):
            with self.assertRaisesRegex(RuntimeError,'scope mismatch'):
                trial.validate('fixture')

