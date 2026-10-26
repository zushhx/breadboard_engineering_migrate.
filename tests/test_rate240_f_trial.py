from pathlib import Path
import sys
import unittest
import tempfile
from unittest.mock import patch
from types import SimpleNamespace
from contextlib import nullcontext
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import rate240_f_trial as trial


class FTrialGuards(unittest.TestCase):
    def test_recovery_waits_for_storage_close_and_checks_identity(self):
        status=dict(boot='a'*32,phase='failed',files_busy=False,pending_owner='',
                    card_serial=trial.tf.CARD_SERIAL,card_bytes=str(trial.tf.CAPACITY))
        session=Mock();session.serial.side_effect=lambda:nullcontext('serial-fixture')
        session.query.side_effect=['stop','stop','identity source='+('b'*40)+' mac_status=ESP_OK']
        http=Mock();http.request_once.side_effect=[dict(status,phase='closing'),dict(status,files_busy=True),status,status]
        result={};clock=[0]
        with patch.object(trial,'trace'):
            trial.stop_and_wait_closed(session,http,dict(source='b'*40),result,
                clock=lambda:clock[0],sleep=lambda seconds:clock.__setitem__(0,clock[0]+seconds))
        self.assertEqual(clock[0],1)
        self.assertTrue(result['tf_closed_before_recovery']['confirmed'])
        self.assertEqual([call.args[1] for call in session.query.call_args_list],['RATE240_STOP','REC_STOP','help'])
        session.esptool.assert_not_called()

    def test_recovery_timeout_retains_evidence_without_reset(self):
        session=Mock();session.serial.side_effect=lambda:nullcontext('serial-fixture')
        http=Mock();http.request_once.return_value=dict(phase='verifying')
        result={};clock=[0]
        with patch.object(trial,'trace'),self.assertRaisesRegex(RuntimeError,'confirmed TF closure'):
            trial.stop_and_wait_closed(session,http,dict(source='b'*40),result,
                clock=lambda:clock[0],sleep=lambda seconds:clock.__setitem__(0,clock[0]+seconds))
        self.assertEqual(clock[0],120);self.assertNotIn('tf_closed_before_recovery',result)
        session.esptool.assert_not_called()

    def test_recovery_rejects_changed_boot_and_missing_busy_fields(self):
        status=dict(boot='a'*32,phase='failed',files_busy=False,pending_owner='',
                    card_serial=trial.tf.CARD_SERIAL,card_bytes=str(trial.tf.CAPACITY))
        for field in ('files_busy','pending_owner','card_serial','card_bytes','boot'):
            incomplete=dict(status);incomplete.pop(field)
            with self.subTest(field=field):self.assertFalse(trial.recovery_terminal(incomplete))
        session=Mock();session.serial.side_effect=lambda:nullcontext('serial-fixture')
        session.query.return_value='identity source='+('b'*40)+' '
        http=Mock();http.request_once.side_effect=[status,dict(status,boot='c'*32)]
        result={}
        with patch.object(trial,'trace'),self.assertRaisesRegex(RuntimeError,'storage/boot changed'):
            trial.stop_and_wait_closed(session,http,dict(source='b'*40),result)
        self.assertNotIn('tf_closed_before_recovery',result);session.esptool.assert_not_called()

    def test_test_recovery_rejects_absent_closure_before_any_reset(self):
        with tempfile.TemporaryDirectory() as temp:
            session=Mock();session.folder=Path(temp)
            result=dict(backup={},test_started=True,source=dict(source='b'*40))
            with self.assertRaisesRegex(RuntimeError,'TF closure required'):
                trial.restore(session,result,Path(temp)/'old.bin')
            session.esptool.assert_not_called()

    def test_missing_approval_stops_before_discovery(self):
        with patch.object(trial,'inputs') as inputs,patch.object(trial.tf,'unique_port') as discover:
            with self.assertRaisesRegex(RuntimeError,'Genuine'):
                trial.approved(SimpleNamespace(approval_record=None))
            inputs.assert_not_called();discover.assert_not_called()

    def test_exclusive_hardware_confirmation_required(self):
        args=SimpleNamespace(approval_record='fixture-never-authorizes-hardware',manifest=None,old_manifest=None,host='192.168.4.1')
        scope={'test':'fixture'}
        with patch.object(trial,'inputs',return_value=(1,2,3,[],scope)),patch.object(trial,'require_approval',return_value=dict(scope=scope,hardware_ready_confirmed=True)):
            with self.assertRaisesRegex(RuntimeError,'exclusive'):
                trial.approved(args)

    def test_files_busy_and_wrong_card_block_quiescence(self):
        status=dict(boot='a'*32,phase='passed',errors=0,ready=True,files_busy=False,card_serial=trial.tf.CARD_SERIAL,card_bytes=str(trial.tf.CAPACITY))
        trial.quiet(status)
        for key,value in (('phase','recording'),('files_busy',True),('card_serial','00000000'),('errors',1)):
            with self.subTest(key=key),self.assertRaises(RuntimeError):trial.quiet(dict(status,**{key:value}))

    def test_http_constructor_does_not_connect(self):
        with patch('urllib.request.OpenerDirector.open') as opened:
            trial.Http('192.168.4.1')
            opened.assert_not_called()

    def test_cold_confirmation_rejects_non_strings_before_device_access(self):
        for value in (None,False,{},1,'','  '):
            with self.subTest(value=value),patch.object(trial,'Http') as http:
                with self.assertRaisesRegex(RuntimeError,'genuine user confirmation'):
                    trial.cold_check(SimpleNamespace(phase='cold1'),{},dict(cold_start_confirmation=dict(number=1,user_confirmation=value)))
                http.assert_not_called()

    def test_repeated_boot_cannot_count_as_second_power_off(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);folder=root/'local-data/hardware/rate240-f/fixture';folder.mkdir(parents=True)
            identity=dict(source='fixture',inputs='fixture')
            result=dict(source=identity,flash_verified=True,initial_stage_passed=True,cold_starts_completed=1,
                operation_commit='fixture',candidate_sha256='fixture',cold_boots=['a'*32],stages=[],passed=False)
            trial.save(folder/'result.json',result)
            status=dict(boot='a'*32,phase='idle',errors=0,ready=True,files_busy=False,
                        card_serial=trial.tf.CARD_SERIAL,card_bytes=str(trial.tf.CAPACITY))
            approval=dict(source_commit='fixture',scope=dict(candidate_sha256='fixture'),
                cold_start_confirmation=dict(number=2,user_confirmation='Test fixture only; never approves hardware'))
            with patch.object(trial,'ROOT',root),patch.object(trial,'Http') as http,patch.object(trial.prior.TrialSession,'serial') as serial:
                http.return_value.request.return_value=status
                with self.assertRaisesRegex(RuntimeError,'New boot absent'):
                    trial.cold_check(SimpleNamespace(phase='cold2',folder=folder,host='192.168.4.1'),identity,approval)
                serial.assert_not_called()
            self.assertFalse(__import__('json').loads((folder/'result.json').read_text())['passed'])


if __name__=='__main__':unittest.main()
