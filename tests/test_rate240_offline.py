import sys
import unittest
import tempfile
import json
import ctypes
import uuid
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import rate240_offline_trial as runner
import wlan_profile


class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.profile=dict(interface_guid='f875fc68-ca14-4ca3-82c6-688c30100663',
                          profile_name='original',ssid_hex='61')

    def test_guid_fixed_windows_size_and_field_offsets(self):
        self.assertEqual(ctypes.sizeof(wlan_profile.GUID),16)
        self.assertEqual([getattr(wlan_profile.GUID,name).offset for name in ('a','b','d','tail')],
                         [0,4,6,8])

    def test_profile_guid_uuid_roundtrip_preserves_windows_bytes(self):
        guid,profile=wlan_profile.validate_snapshot(self.profile)
        expected=uuid.UUID(self.profile['interface_guid'])
        self.assertEqual(bytes(guid),expected.bytes_le)
        self.assertEqual(uuid.UUID(bytes_le=bytes(guid)),expected)
        self.assertEqual((guid.a,guid.b,guid.d),(0xf875fc68,0xca14,0x4ca3))
        self.assertEqual(bytes(guid.tail),bytes.fromhex('82c6688c30100663'))
        self.assertEqual(profile,self.profile['profile_name'])

    def test_missing_approval_precedes_network(self):
        with patch.object(wlan_profile,'current_connection') as query:
            with self.assertRaises(RuntimeError):
                runner.approved(SimpleNamespace(approval_record=None))
            query.assert_not_called()

    def test_invalid_existing_profile(self):
        for key,value in [('interface_guid','bad'),('profile_name','x\n')]:
            with self.assertRaises(ValueError):
                wlan_profile.validate_snapshot(dict(self.profile,**{key:value}))

    def test_already_restored_does_not_connect(self):
        state={}
        with patch.object(wlan_profile,'current_connection',return_value=self.profile), \
             patch.object(wlan_profile,'connect_profile') as connect:
            runner.restore_pc(self.profile,state,lambda:None)
            connect.assert_not_called()
        self.assertTrue(state['pc_profile_restored'])

    def test_restore_requests_only_saved_profile_and_verifies(self):
        state={};clock=[0]
        ap=dict(self.profile,profile_name='ap',ssid_hex='62')
        with patch.object(wlan_profile,'current_connection',side_effect=[ap,self.profile]), \
             patch.object(wlan_profile,'connect_profile') as connect:
            runner.restore_pc(self.profile,state,lambda:None,lambda:clock[0],
                              lambda seconds:clock.__setitem__(0,clock[0]+seconds))
            connect.assert_called_once_with(self.profile)
        self.assertTrue(state['pc_profile_restored'])

    def test_return_timeout_is_bounded(self):
        state={};clock=[0]
        with patch.object(wlan_profile,'current_connection',side_effect=OSError('offline')), \
             patch.object(wlan_profile,'connect_profile') as connect:
            with self.assertRaises(RuntimeError):
                runner.restore_pc(self.profile,state,lambda:None,lambda:clock[0],
                                  lambda seconds:clock.__setitem__(0,clock[0]+seconds))
        self.assertEqual(clock[0],90)
        self.assertEqual(connect.call_count,4)
        self.assertNotIn('pc_profile_restored',state)

    def test_recovery_does_not_repeat_completed_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            (folder/'result.json').write_text(json.dumps(dict(restored_f=True)),encoding='utf-8')
            with patch.object(runner,'approved'), \
                 patch.object(runner.trial,'approved',return_value=(None,None,None,None,None)), \
                 patch.object(runner.trial,'restore') as restore:
                runner.recover_after_flash(None,folder)
                restore.assert_not_called()

    def test_recovery_cannot_bypass_approval(self):
        with patch.object(runner,'approved'), \
             patch.object(runner.trial,'approved',side_effect=RuntimeError('blocked')), \
             patch.object(runner.trial,'restore') as restore:
            with self.assertRaises(RuntimeError):runner.recover_after_flash(None,Path('unused'))
            restore.assert_not_called()

    def test_status_reconnects_observed_profile_after_network_loss(self):
        responses=[runner.urllib.error.URLError('offline'),{'ready':True}]
        class Base:
            def request(self,*args):
                value=responses.pop(0)
                if isinstance(value,Exception):raise value
                return value
        with patch.object(wlan_profile,'current_connection',side_effect=OSError('disconnected')), \
             patch.object(wlan_profile,'connect_profile') as connect, \
             patch.object(runner.time,'sleep'):
            http=runner.reconnecting_http(Base,self.profile)()
            self.assertEqual(http.request('/api/status'),{'ready':True})
            connect.assert_called_once_with(self.profile)

    def test_mutating_request_is_never_replayed(self):
        class Base:
            calls=0
            def request(self,*args):
                self.calls+=1
                raise runner.urllib.error.URLError('uncertain write')
        http=runner.reconnecting_http(Base,self.profile)()
        with patch.object(wlan_profile,'connect_profile') as connect:
            with self.assertRaises(runner.urllib.error.URLError):http.request('/api/v2/start',{'start':1})
            self.assertEqual(http.calls,1);connect.assert_not_called()


class OfflinePhaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name).resolve()
        self.folder=self.root/'local-data/hardware/rate240-f/prior'
        self.folder.mkdir(parents=True)
        prefix=self.folder/'controller_prefix.bin';prefix.write_bytes(b'original-flash-prefix')
        nvs=self.folder/'nvs-before.bin';nvs.write_bytes(bytes(0x5000))
        self.identity=dict(source='a'*40,inputs='b'*64)
        self.scope=dict(candidate=self.identity,candidate_sha256='c'*64,backup_end=prefix.stat().st_size)
        self.result=dict(flash_verified=True,source=self.identity,candidate_sha256='c'*64,
                         operation_commit='d'*40,stages=[],backup=dict(bytes=prefix.stat().st_size,
                         sha256=runner.sha(prefix),nvs_sha256=runner.sha(nvs)))
        self.save_result()
        self.profile=dict(interface_guid='f875fc68-ca14-4ca3-82c6-688c30100663',
                          profile_name='original',ssid_hex='61',expected_ap_ssid_hex='62')
        profile_path=self.root/'local-data/return-profile.json'
        profile_path.write_text(json.dumps(self.profile),encoding='utf-8')
        self.args=SimpleNamespace(manifest=self.root/'candidate.json',old_manifest=self.root/'old.json',
            host='192.168.4.1',wait_seconds=30,return_profile=profile_path,
            approval_record=self.root/'approval.json',offline_phase='test',folder=self.folder)
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(runner,'ROOT',self.root))
        self.stack.enter_context(patch.object(runner.trial,'inputs',return_value=(None,None,self.identity,[],self.scope)))
        self.gate=self.stack.enter_context(patch.object(runner,'require_approval'))
        self.network=self.stack.enter_context(patch.object(wlan_profile,'current_connection'))
        self.connect=self.stack.enter_context(patch.object(wlan_profile,'connect_profile'))
        self.execute=self.stack.enter_context(patch.object(runner.trial,'run'))
        self.restore=self.stack.enter_context(patch.object(runner,'restore_pc'))
        self.recover=self.stack.enter_context(patch.object(runner,'recover_after_flash'))
        self.stack.enter_context(patch('builtins.print'))

    def save_result(self):
        (self.folder/'result.json').write_text(json.dumps(self.result),encoding='utf-8')

    def approve(self):
        _,self.files,scope,offline=runner.prepare(self.args)
        self.record=dict(scope=scope,offline_scope=offline,source_commit='d'*40,
                         hardware_exclusive_confirmed=True,hardware_ready_confirmed=True)
        self.gate.return_value=self.record

    def run_offline(self,error=None):
        clock=[0]
        ap=dict(self.profile,profile_name='controller-ap',ssid_hex='62')
        self.network.return_value=ap
        class Http:
            def __init__(self,host):pass
            def request(self,path):return dict(boot='e'*32)
        self.stack.enter_context(patch.object(runner.trial,'Http',Http))
        self.stack.enter_context(patch.object(runner.trial,'quiet'))
        self.stack.enter_context(patch.object(runner,'reconnecting_http',return_value=Http))
        self.stack.enter_context(patch.object(runner.time,'monotonic',side_effect=lambda:clock[0]))
        self.stack.enter_context(patch.object(runner.time,'sleep',side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds)))
        phases=[]
        def perform(args):
            phases.append((args.phase,args.folder))
            if error:raise error
            return self.folder
        self.execute.side_effect=perform
        runner.run(self.args)
        states=list((self.root/'local-data/hardware/rate240-offline').glob('*/state.json'))
        self.assertEqual(len(states),1)
        self.assertFalse((states[0].parent.parent/'active.lock').exists())
        self.restore.assert_called_once()
        return phases,json.loads(states[0].read_text(encoding='utf-8'))

    def test_flash_runs_only_flash_and_records_next_phase(self):
        self.args.offline_phase='flash';self.args.folder=None;self.approve()
        phases,state=self.run_offline()
        self.assertEqual(phases,[('flash',None)])
        self.assertEqual(state['trial_folder'],str(self.folder))
        self.assertEqual(state['next_phase'],'test')
        self.assertTrue(state['flash_verified']);self.assertTrue(state['phase_completed'])
        self.assertFalse(state['passed']);self.recover.assert_not_called()

    def test_test_runs_only_test_with_bound_flash_folder(self):
        self.approve();phases,state=self.run_offline()
        self.assertEqual(phases,[('test',self.folder)])
        self.assertEqual(self.record['offline_scope']['flash_receipt']['folder'],str(self.folder))
        self.assertIn((self.folder/'controller_prefix.bin').resolve(),self.files)
        self.assertIn((self.folder/'nvs-before.bin').resolve(),self.files)
        self.assertNotIn((self.folder/'result.json').resolve(),self.files)
        self.assertTrue(state['phase_completed']);self.recover.assert_not_called()

    def test_combined_flash_then_test_rejected_before_hardware(self):
        self.args.offline_phase='full';self.args.folder=None
        with self.assertRaisesRegex(RuntimeError,'separate phases'):runner.run(self.args)
        self.network.assert_not_called();self.execute.assert_not_called()

    def test_missing_folder_rejected_before_hardware_io(self):
        self.args.folder=None
        with self.assertRaisesRegex(RuntimeError,'requires --folder'):runner.run(self.args)
        self.network.assert_not_called();self.execute.assert_not_called();self.restore.assert_not_called()

    def test_wrong_receipt_identity_and_path_rejected_before_hardware_io(self):
        for field,value in [('source',dict(source='f'*40,inputs='b'*64)),('candidate_sha256','f'*64)]:
            with self.subTest(field=field):
                original=self.result[field];self.result[field]=value;self.save_result()
                with self.assertRaisesRegex(RuntimeError,'identity mismatch'):runner.run(self.args)
                self.result[field]=original
        self.save_result();self.args.folder=self.root
        with self.assertRaisesRegex(RuntimeError,'prior flash folder'):runner.run(self.args)
        self.network.assert_not_called();self.execute.assert_not_called()

    def test_operation_commit_mismatch_rejected_before_hardware_io(self):
        self.result['operation_commit']='f'*40;self.save_result();self.approve()
        with self.assertRaisesRegex(RuntimeError,'operation commit'):runner.run(self.args)
        self.network.assert_not_called();self.execute.assert_not_called()

    def test_attempted_or_restored_test_rejected_before_hardware_io(self):
        self.approve()
        for field,value in [('test_started',True),('restored_f',True),('stages',[{'passed':False}])]:
            with self.subTest(field=field):
                original=self.result.get(field);self.result[field]=value;self.save_result()
                with self.assertRaisesRegex(RuntimeError,'already attempted'):runner.run(self.args)
                self.result.pop(field) if original is None else self.result.__setitem__(field,original)
        self.network.assert_not_called();self.execute.assert_not_called()

    def test_dynamic_result_updates_leave_recovery_approval_valid(self):
        self.approve()
        self.result.update(test_started=True,stages=[dict(passed=False)],error='failed in test')
        self.save_result();runner.approved(self.args)
        self.assertNotIn((self.folder/'result.json').resolve(),self.files)

    def test_test_failure_keeps_safe_recovery_and_pc_return(self):
        self.approve();phases,state=self.run_offline(RuntimeError('test failed'))
        self.assertEqual(phases,[('test',self.folder)])
        self.recover.assert_called_once_with(self.args,self.folder)
        self.assertIn('test failed',state['error']);self.assertEqual(state['phase'],'finished')

    def test_modified_backup_rejected_before_hardware_io(self):
        self.approve();(self.folder/'controller_prefix.bin').write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError,'backup mismatch'):runner.run(self.args)
        self.network.assert_not_called();self.execute.assert_not_called()


if __name__=='__main__':unittest.main()
