"""Ensure trial preconditions fail before hardware or network discovery."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from unittest.mock import Mock
import tempfile,json
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import sensor_web_batch as web


class WebBatchGuards(unittest.TestCase):
    def test_read_only_gate_panic_stops_without_http_or_recording(self):
        port=Mock();port.read.return_value=b'assert failed: task notification\n'
        session=Mock()
        with patch.object(web.time,'monotonic',side_effect=[0,.1]),patch.object(web,'status') as status:
            with self.assertRaisesRegex(RuntimeError,'Panic/reset during read-only'):
                web.read_only_gate(session,port,'http://192.168.1.2/',{'boot':'abc'},'test')
            status.assert_not_called()
        session.collect.assert_called_once_with(port,.5)
    def test_read_only_gate_rejects_unrequested_recording_or_reset(self):
        idle={'boot':'abc','connected':True,'ready':True,'phase':'idle','errors':0}
        web.check_idle(idle,'abc')
        for key,value in [('boot','different'),('phase','recording'),('errors',1),('ready',False),('connected',False)]:
            with self.subTest(key=key),self.assertRaisesRegex(RuntimeError,'Read-only startup gate'):
                web.check_idle(dict(idle,**{key:value}),'abc')
    def test_known_failed_candidate_stops_before_baseline_or_hardware(self):
        with tempfile.TemporaryDirectory() as directory:
            failure=Path(directory)/'failure.json'
            failure.write_text(json.dumps({'candidate_sha256':web.CANDIDATE_SHA,'passed':False}),encoding='utf-8')
            with patch.object(web,'REJECTED',failure),patch.object(web,'baseline_errors') as baseline:
                with self.assertRaisesRegex(RuntimeError,'Known failed'):web.inputs()
                baseline.assert_not_called()

    def test_startup_panic_split_across_reads_stops_before_identity(self):
        port=Mock();port.read.side_effect=[b'assert fa',b'iled: example\n']
        session=Mock()
        with patch.object(web.time,'monotonic',side_effect=[0,.1,.2]):
            with self.assertRaisesRegex(RuntimeError,'startup panic detected'):web.collect_startup(session,port,10)
        session.collect.assert_called_once_with(port,.5)
        self.assertEqual(session.emit.call_count,2)
    def test_missing_approval_stops_before_loading_inputs_or_session(self):
        with patch.object(web,'inputs') as inputs,patch.object(web.tf,'Session') as session:
            with self.assertRaisesRegex(RuntimeError,'genuine candidate approval'):web.initial(None)
            inputs.assert_not_called();session.assert_not_called()

    def test_changed_scope_stops_before_device_discovery(self):
        packet={'scope_proposed':{'main_record_seconds':300}}
        with patch.object(web,'inputs',return_value=(packet,None,None,[])),patch.object(web,'require_approval',return_value={'scope':{}}),patch.object(web.tf,'Session') as session:
            with self.assertRaisesRegex(RuntimeError,'scope mismatch'):web.initial(Path('fixture-only'))
            session.assert_not_called()

    def test_readiness_not_inferred_from_overall_authorization(self):
        packet={'scope_proposed':{'main_record_seconds':300}}
        with patch.object(web,'inputs',return_value=(packet,None,None,[])),patch.object(web,'require_approval',return_value={'scope':packet['scope_proposed']}),patch.object(web.tf,'Session') as session:
            with self.assertRaisesRegex(RuntimeError,'readiness not confirmed'):web.initial(Path('fixture-only'))
            session.assert_not_called()


if __name__=='__main__':unittest.main()
