import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import rate240_f_trial as trial


class TraceTests(unittest.TestCase):
    def test_waits_ten_seconds_of_same_boot_before_test(self):
        elapsed=[0]
        def status(path):return {'boot':'a' if elapsed[0]<5 else 'b'}
        http=SimpleNamespace(request=status)
        with patch.object(trial,'quiet'):
            boot=trial.wait_stable_status(http,clock=lambda:elapsed[0],
                sleep=lambda seconds:elapsed.__setitem__(0,elapsed[0]+seconds))
        self.assertEqual(boot,'b');self.assertEqual(elapsed[0],15)

    def test_repeated_reboots_cannot_pass_wait_gate(self):
        elapsed=[0]
        http=SimpleNamespace(request=lambda path:{'boot':str(elapsed[0])})
        with patch.object(trial,'quiet'),self.assertRaisesRegex(RuntimeError,'stable'):
            trial.wait_stable_status(http,clock=lambda:elapsed[0],
                sleep=lambda seconds:elapsed.__setitem__(0,elapsed[0]+seconds))
        self.assertEqual(elapsed[0],90)

    def test_http_reconnect_resets_wait_even_if_boot_unchanged(self):
        elapsed=[0];http=SimpleNamespace(stability_generation=0)
        def status(path):
            if elapsed[0]==5:http.stability_generation+=1
            return {'boot':'same'}
        http.request=status
        with patch.object(trial,'quiet'):
            trial.wait_stable_status(http,clock=lambda:elapsed[0],
                sleep=lambda seconds:elapsed.__setitem__(0,elapsed[0]+seconds))
        self.assertEqual(elapsed[0],15)

    def listener_fixture(self):
        elapsed=[0]
        session=SimpleNamespace(folder=Path('missing'),query=MagicMock(return_value='[rate240] explicit service arm=1'))
        http=SimpleNamespace(host='192.168.4.1',request_once=MagicMock(return_value={'boot':'same'}))
        return elapsed,session,http

    def test_listener_refusal_rearms_without_submitting_source(self):
        elapsed,session,http=self.listener_fixture();sock=MagicMock()
        with patch.object(trial,'quiet'),patch.object(trial.socket,'create_connection',side_effect=[ConnectionRefusedError(10061,'refused'),sock]):
            actual=trial.connect_service(session,None,http,'same',clock=lambda:elapsed[0],sleep=lambda n:elapsed.__setitem__(0,elapsed[0]+n))
        self.assertIs(actual,sock);self.assertEqual(elapsed[0],2)
        self.assertEqual(session.query.call_count,2);sock.sendall.assert_not_called()

    def test_listener_timeout_is_not_retried(self):
        _,session,http=self.listener_fixture()
        with patch.object(trial,'quiet'),patch.object(trial.socket,'create_connection',side_effect=TimeoutError),self.assertRaises(TimeoutError):
            trial.connect_service(session,None,http,'same')
        self.assertEqual(session.query.call_count,1)

    def test_listener_changed_boot_prevents_arm(self):
        _,session,http=self.listener_fixture();http.request_once.return_value={'boot':'changed'}
        with patch.object(trial,'quiet'),self.assertRaisesRegex(RuntimeError,'Boot changed'):
            trial.connect_service(session,None,http,'same')
        session.query.assert_not_called()

    def test_listener_refusal_window_ends_without_source(self):
        elapsed,session,http=self.listener_fixture()
        with patch.object(trial,'quiet'),patch.object(trial.socket,'create_connection',side_effect=ConnectionRefusedError),self.assertRaisesRegex(RuntimeError,'150-second'):
            trial.connect_service(session,None,http,'same',clock=lambda:elapsed[0],sleep=lambda n:elapsed.__setitem__(0,elapsed[0]+n))
        self.assertEqual(elapsed[0],150);self.assertEqual(session.query.call_count,75)

    def test_listener_ambiguous_arm_stops(self):
        _,session,http=self.listener_fixture();session.query.return_value='no reply'
        with patch.object(trial,'quiet'),patch.object(trial.socket,'create_connection') as connect,self.assertRaisesRegex(RuntimeError,'Ambiguous'):
            trial.connect_service(session,None,http,'same')
        connect.assert_not_called()

    def test_listener_malformed_arm_value_is_rejected(self):
        _,session,http=self.listener_fixture();session.query.return_value='[rate240] explicit service arm=10'
        with patch.object(trial,'quiet'),patch.object(trial.socket,'create_connection') as connect,self.assertRaisesRegex(RuntimeError,'Ambiguous'):
            trial.connect_service(session,None,http,'same')
        connect.assert_not_called()

    def test_listener_connect_after_deadline_is_closed(self):
        elapsed,session,http=self.listener_fixture();sock=MagicMock()
        def connect(*args):elapsed[0]=151;return sock
        with patch.object(trial,'quiet'),patch.object(trial.socket,'create_connection',side_effect=connect),self.assertRaisesRegex(RuntimeError,'150-second'):
            trial.connect_service(session,None,http,'same',clock=lambda:elapsed[0])
        sock.close.assert_called_once();sock.sendall.assert_not_called()

    def test_listener_arm_zero_is_logged_then_probes_connection(self):
        _,session,http=self.listener_fixture();session.query.return_value='[rate240] explicit service arm=0'
        with patch.object(trial,'quiet'),patch.object(trial.socket,'create_connection') as connect:
            self.assertIs(trial.connect_service(session,None,http,'same'),connect.return_value)

    def test_events_preserve_order_and_clock_domains(self):
        with tempfile.TemporaryDirectory() as directory:
            trial.trace(directory,'receiver_failure',run='ABCDEF12',error='EOF')
            trial.trace(directory,'stop_submit')
            events=[json.loads(line) for line in (Path(directory)/'events.jsonl').read_text().splitlines()]
            self.assertEqual([e['event'] for e in events],['receiver_failure','stop_submit'])
            self.assertLessEqual(events[0]['monotonic_ns'],events[1]['monotonic_ns'])
            self.assertTrue(all(e['wall_utc'].endswith('+00:00') for e in events))

    def test_missing_evidence_folder_does_not_block_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            trial.trace(Path(directory)/'missing','recovery_esptool_begin')

    def test_ram_failure_uses_no_tf_source_command(self):
        with tempfile.TemporaryDirectory() as directory:
            session=SimpleNamespace(folder=Path(directory),query=MagicMock(return_value='[rate240] explicit service arm=1'),emit=MagicMock())
            port=MagicMock();port.read.return_value=b''
            http=SimpleNamespace(host='192.168.4.1',request=MagicMock(return_value={'boot':'a'*32}))
            with patch.object(trial,'quiet'),patch.object(trial.socket,'create_connection') as connect, \
                 patch.object(trial.rx,'receive',side_effect=ValueError('truncated stream')):
                with self.assertRaisesRegex(ValueError,'truncated'):
                    trial.ram_stage(session,port,http,{'source':'fixture'})
                command=connect.return_value.__enter__.return_value.sendall.call_args.args[0]
                self.assertIn(b'SYNTH 30',command)
                port.write.assert_not_called()
                self.assertEqual(session.query.call_args.args[1],'RATE240_ARM')
            self.assertFalse(json.loads((Path(directory)/'ram-wifi-30/failure.json').read_text())['passed'])

    def test_boot_change_after_wait_prevents_arming(self):
        session=SimpleNamespace(query=MagicMock())
        http=SimpleNamespace(request=lambda path:{'boot':'changed'})
        with patch.object(trial,'quiet'),self.assertRaisesRegex(RuntimeError,'after stability'):
            trial.ram_stage(session,None,http,{},expected_boot='waited')
        session.query.assert_not_called()

    def test_last_status_reconnect_after_wait_prevents_arming(self):
        session=SimpleNamespace(query=MagicMock());http=SimpleNamespace(stability_generation=0)
        def status(path):
            http.stability_generation+=1
            return {'boot':'same'}
        http.request=status
        with patch.object(trial,'quiet'),self.assertRaisesRegex(RuntimeError,'reconnected after stability'):
            trial.ram_stage(session,None,http,{},expected_boot='same',expected_generation=0)
        session.query.assert_not_called()


if __name__=='__main__':unittest.main()
