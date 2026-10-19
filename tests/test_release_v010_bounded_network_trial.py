import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch,Mock

ROOT=Path(__file__).resolve().parents[1] if Path(__file__).parent.name=='tests' else Path.cwd()
sys.path.insert(0,str(ROOT/'tools'))
GATE=ROOT/'tools/release_v010_bounded_network_trial.py'
if not GATE.exists():GATE=ROOT/'local-data/release-v010/release_v010_bounded_network_trial.py'
spec=importlib.util.spec_from_file_location('bounded_network_trial',GATE)
trial=importlib.util.module_from_spec(spec);spec.loader.exec_module(trial)


class BoundedNetworkTrialGuards(unittest.TestCase):
    def test_missing_approval_loads_no_inputs_or_discovers_device(self):
        with patch.object(trial,'inputs',side_effect=AssertionError('private read forbidden')):
            with self.assertRaisesRegex(RuntimeError,'Explicit candidate approval'):trial.validate(None)

    def test_unbound_candidate_rejected_before_manifest_read(self):
        with patch.object(trial,'SOURCE','UNBOUND_C_SOURCE'):
            with self.assertRaisesRegex(RuntimeError,'unbound'):trial.inputs()

    def test_b_cannot_be_reused_as_c(self):
        with patch.object(trial,'SOURCE',trial.OLD_SOURCE),patch.object(trial,'CANDIDATE',trial.OLD_SHA),patch.object(trial,'MANIFEST',Path('fixture')):
            with self.assertRaisesRegex(RuntimeError,'unbound'):trial.bound_candidate()

    def test_ab_approval_scope_is_not_c_scope(self):
        with patch.object(trial,'inputs',return_value=('candidate','old',[])),patch.object(trial,'require_approval',return_value={'scope':{},'hardware_ready_confirmed':True}) as gate:
            with self.assertRaisesRegex(RuntimeError,'scope/readiness'):trial.validate('fixture')
            self.assertEqual(gate.call_args.args[2],'release-v010-bounded-network-smoke')

    def test_readiness_required(self):
        with patch.object(trial,'inputs',return_value=('candidate','old',[])),patch.object(trial,'require_approval',return_value={'scope':trial.SCOPE}):
            with self.assertRaisesRegex(RuntimeError,'scope/readiness'):trial.validate('fixture')

    def test_gdb_checksum_and_incomplete_packets(self):
        self.assertEqual(trial.gdb_packet(b'+$T04#b8'),b'T04')
        for raw in (b'+$T04#b9',b'$T04#',b'idle',b'$T04#zz'):
            with self.subTest(raw=raw),self.assertRaises(RuntimeError):trial.gdb_packet(raw)

    def test_stub_only_sends_stop_reason_and_ack(self):
        class Port:
            def __init__(self):self.writes=[]
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,count):return b'+$T04#b8'
            def write(self,value):self.writes.append(value)
            def flush(self):pass
        class Session:
            def __init__(self):self.port=Port()
            def serial(self):return self.port
            def emit(self,value):pass
        session=Session()
        with patch.object(trial,'saved_panic',return_value={'packets':[{'command':'?','response':b'+$T04#b8'.hex()}]}):
            trial.verify_saved_stub(session)
        self.assertEqual(session.port.writes,[b'+$?#3f',b'+'])

    def test_fresh_backup_rejects_wrong_app_and_nvs(self):
        with tempfile.TemporaryDirectory() as folder:
            bundle=Path(folder);(bundle/'artifacts').mkdir()
            (bundle/'artifacts/bootloader.bin').write_bytes(b'boot')
            (bundle/'artifacts/partitions.bin').write_bytes(b'parts')
            data=bytearray(trial.SCOPE['backup_end']);data[:4]=b'boot';data[0x8000:0x8005]=b'parts';data[0x10000:0x10003]=b'old'
            nvs=trial.hashlib.sha256(data[0x9000:0xe000]).hexdigest()
            with patch.object(trial,'BUNDLE',bundle),patch.object(trial,'EXPECTED_NVS',nvs):
                trial.check_backup(bytes(data),b'old')
                for offset in (0,0x8000,0x9000,0x10000):
                    corrupt=bytearray(data);corrupt[offset]^=1
                    with self.subTest(offset=offset),self.assertRaisesRegex(RuntimeError,'no write'):trial.check_backup(bytes(corrupt),b'old')

    def test_failure_recovery_writes_only_app_if_nvs_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);snapshot=root/'prefix.bin';snapshot.write_bytes(b'snapshot');old=root/'old.bin';old.write_bytes(b'old');nvs=root/'nvs-before.bin';nvs.write_bytes(b'nvs')
            class Port:
                def __enter__(self):return self
                def __exit__(self,*args):pass
            class Session:
                def __init__(self):self.folder=root;self.commands=[]
                def esptool(self,port,command):
                    self.commands.append(command)
                    if command[0]=='read_flash':Path(command[-1]).write_bytes(b'nvs')
                def serial(self):return Port()
                def collect(self,*args):pass
            session=Session();backup={'sha256':trial.sha(snapshot),'nvs_sha256':trial.sha(nvs)}
            with patch.object(trial,'OLD_SHA',trial.sha(old)),patch.object(trial.tf,'unique_port',return_value='mock'),patch.object(trial.recorder,'boot_check'):
                trial.restore_application(session,snapshot,backup,old)
            writes=[c for c in session.commands if c[0]=='write_flash']
            self.assertEqual(len(writes),1);self.assertEqual(writes[0][-2:],['0x10000',str(old)])
            self.assertNotIn(str(snapshot),writes[0])

    def test_nvs_restore_requires_changed_bytes_and_verified_same_round_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);snapshot=root/'prefix.bin';snapshot.write_bytes(b'snapshot');old=root/'old.bin';old.write_bytes(b'old')
            nvs=root/'nvs-before.bin';nvs.write_bytes(b'nvs')
            class Port:
                def __enter__(self):return self
                def __exit__(self,*args):pass
            class Session:
                def __init__(self):self.folder=root;self.commands=[]
                def esptool(self,port,command):
                    self.commands.append(command)
                    if command[0]=='read_flash':Path(command[-1]).write_bytes(b'changed')
                def serial(self):return Port()
                def collect(self,*args):pass
            backup={'sha256':trial.sha(snapshot),'nvs_sha256':trial.sha(nvs)}
            with patch.object(trial,'OLD_SHA',trial.sha(old)),patch.object(trial.tf,'unique_port',return_value='mock'),patch.object(trial.recorder,'boot_check'):
                session=Session();trial.restore_application(session,snapshot,backup,old)
                writes=[c for c in session.commands if c[0]=='write_flash']
                self.assertEqual([c[-2] for c in writes],['0x10000','0x9000'])
                self.assertEqual(writes[1][-1],str(nvs))
                nvs.write_bytes(b'corrupt snapshot');session=Session()
                with self.assertRaisesRegex(RuntimeError,'Same-round NVS snapshot changed'):
                    trial.restore_application(session,snapshot,backup,old)
                self.assertEqual([c[-2] for c in session.commands if c[0]=='write_flash'],['0x10000'])

    def test_fixed_boundaries(self):
        self.assertTrue(trial.SCOPE['reset_from_saved_panic'])
        self.assertTrue(trial.SCOPE['restore_app0_only'])
        self.assertFalse(trial.SCOPE['change_network'])
        self.assertFalse(trial.SCOPE['delete_format_repair'])
        self.assertEqual(trial.SCOPE['backup_end'],0x123000)
        self.assertEqual(trial.SCOPE['cold_starts'],0)

    def test_stubborn_child_killed_after_bounded_wait(self):
        process=Mock();process.poll.return_value=None
        process.wait.side_effect=[trial.subprocess.TimeoutExpired('child',5),None]
        trial.stop_owned(process)
        process.terminate.assert_called_once();process.kill.assert_called_once()
        self.assertEqual(process.wait.call_count,2)
        for call in process.wait.call_args_list:self.assertEqual(call.kwargs['timeout'],5)

    def test_cleanup_errors_do_not_skip_host_or_escape_rollback_path(self):
        result={}
        with patch.object(trial,'stop_owned',side_effect=OSError('kill failed')),patch.object(trial,'close_host') as close:
            trial.cleanup_owned('browser','host',result)
            close.assert_called_once_with('host')
        self.assertEqual(len(result['cleanup_errors']),1)

    def test_bad_host_readiness_reaps_owned_process_and_closes_log(self):
        from types import SimpleNamespace
        process=Mock();process.poll.return_value=None;process.stdout.readline.return_value='not ready'
        with tempfile.TemporaryDirectory() as folder,patch('serial.tools.list_ports.comports',return_value=[SimpleNamespace(device='mock',vid=0x303a,pid=0x1001)]),patch.object(trial.subprocess,'Popen',return_value=process):
            with self.assertRaisesRegex(RuntimeError,'USB busy/unavailable'):
                trial.start_host(Path(folder)/'approval.json',Path(folder),'failed')
            process.terminate.assert_called_once();process.wait.assert_called_once_with(timeout=5)

    def test_host_readiness_timeout_reaps_own_process(self):
        import queue
        from types import SimpleNamespace
        process=Mock();process.poll.return_value=None;process.stdout.readline.return_value=''
        pending=Mock();pending.get.side_effect=queue.Empty
        with tempfile.TemporaryDirectory() as folder,patch('serial.tools.list_ports.comports',return_value=[SimpleNamespace(device='mock',vid=0x303a,pid=0x1001)]),patch.object(trial.subprocess,'Popen',return_value=process),patch('queue.Queue',return_value=pending):
            with self.assertRaisesRegex(RuntimeError,'readiness timeout'):
                trial.start_host(Path(folder)/'approval.json',Path(folder),'timeout')
            process.terminate.assert_called_once();process.wait.assert_called_once_with(timeout=5)


if __name__=='__main__':unittest.main()
