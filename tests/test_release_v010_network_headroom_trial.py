"""D hardware gate tests use fake sessions only; no serial/USB discovery."""
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
spec=importlib.util.spec_from_file_location('network_headroom_trial',ROOT/'tools/release_v010_network_headroom_trial.py')
trial=importlib.util.module_from_spec(spec);spec.loader.exec_module(trial)

def status(**changes):
    value=dict(phase='passed',errors=0,ready=True,files_busy=False,card_serial=trial.tf.CARD_SERIAL,
               card_bytes=str(trial.tf.CAPACITY),boot='a'*32,run='today-new-run')
    value.update(changes);return value

class NetworkHeadroomGuards(unittest.TestCase):
    def test_no_approval_reads_no_private_inputs(self):
        with patch.object(trial,'inputs',side_effect=AssertionError('private inputs')):
            with self.assertRaisesRegex(RuntimeError,'Explicit candidate approval'):trial.validate(None)

    def test_unbound_D_rejected_before_manifest_read(self):
        with patch.object(trial,'SOURCE','UNBOUND_D_SOURCE'):
            with self.assertRaisesRegex(RuntimeError,'Candidate D is unbound'):trial.inputs()

    def test_C_image_is_not_D(self):
        with patch.object(trial,'SOURCE',trial.OLD_SOURCE),patch.object(trial,'CANDIDATE',trial.OLD_SHA),patch.object(trial,'MANIFEST',Path('unused')):
            with self.assertRaisesRegex(RuntimeError,'unbound'):trial.bound_candidate()

    def test_operation_and_scope_distinct_from_C(self):
        with patch.object(trial,'inputs',return_value=('new','old',[])),patch.object(trial,'require_approval',return_value={'scope':{},'hardware_ready_confirmed':True}) as gate:
            with self.assertRaisesRegex(RuntimeError,'scope/readiness'):trial.validate('fixture')
            self.assertEqual(gate.call_args.args[2],'release-v010-network-headroom-smoke')

    def test_readiness_required(self):
        with patch.object(trial,'inputs',return_value=('new','old',[])),patch.object(trial,'require_approval',return_value={'scope':trial.SCOPE}):
            with self.assertRaisesRegex(RuntimeError,'scope/readiness'):trial.validate('fixture')

    def test_healthy_C_does_not_bind_a_historical_run(self):
        for phase in ('idle','passed'):
            before=status(phase=phase,run='new-run-on-new-date')
            trial.check_quiescent(before);trial.check_same_runtime(before,dict(before))

    def test_busy_or_invalid_C_rejected(self):
        cases=[dict(phase=p) for p in ('queued','preparing','recording','draining','closing','verifying','failed','rejected')]
        cases += [dict(errors=1),dict(ready=False),dict(files_busy=True),dict(card_serial='WRONG'),dict(card_bytes='1')]
        for changes in cases:
            with self.subTest(changes=changes),self.assertRaisesRegex(RuntimeError,'no reset or write'):
                trial.check_quiescent(status(**changes))

    def test_boot_and_run_change_rejected(self):
        for changes in (dict(boot='b'*32),dict(run='changed')):
            with self.subTest(changes=changes),self.assertRaisesRegex(RuntimeError,'changed'):
                trial.check_same_runtime(status(),status(**changes))

    def test_busy_preflight_performs_no_uart_storage_query_or_reset(self):
        class Port:
            def __enter__(self):return self
            def __exit__(self,*args):pass
        class Session:
            folder=Path('unused')
            def serial(self):return Port()
            def collect(self,*args):return ''
            def esptool(self,*args):raise AssertionError('must not reset')
        class Opener:
            def open(self,*args,**kwargs):return io.StringIO(json.dumps(status(phase='recording')))
        with patch.object(trial,'start_host',return_value=('host',io.StringIO(),'http://local/')),patch.object(trial,'close_host'),patch.object(trial.recorder,'boot_check',side_effect=AssertionError('no storage query while busy')),patch('urllib.request.build_opener',return_value=Opener()):
            with self.assertRaisesRegex(RuntimeError,'no reset or write'):trial.read_only_preflight(Session(),Path('approval'))

    def test_preflight_panic_preserved_not_reset(self):
        class Port:
            def __enter__(self):return self
            def __exit__(self,*args):pass
        class Session:
            folder=Path('unused')
            def serial(self):return Port()
            def collect(self,*args):return 'Entering gdb stub'
        with patch.object(trial,'start_host',side_effect=RuntimeError('USB failed')),patch.object(trial,'close_host'):
            with self.assertRaisesRegex(RuntimeError,'Panic/reset'):trial.read_only_preflight(Session(),Path('approval'))

    def test_backup_requires_exact_C_app_and_same_AP_nvs(self):
        with tempfile.TemporaryDirectory() as folder:
            bundle=Path(folder);(bundle/'artifacts').mkdir()
            (bundle/'artifacts/bootloader.bin').write_bytes(b'boot');(bundle/'artifacts/partitions.bin').write_bytes(b'parts')
            data=bytearray(trial.SCOPE['backup_end']);data[:4]=b'boot';data[0x8000:0x8005]=b'parts';data[0x10000:0x10001]=b'C'
            nvs=trial.hashlib.sha256(data[0x9000:0xe000]).hexdigest()
            with patch.object(trial,'BUNDLE',bundle),patch.object(trial,'EXPECTED_NVS',nvs):
                trial.check_backup(bytes(data),b'C')
                for offset in (0,0x8000,0x9000,0x10000):
                    bad=bytearray(data);bad[offset]^=1
                    with self.subTest(offset=offset),self.assertRaisesRegex(RuntimeError,'no write'):trial.check_backup(bytes(bad),b'C')

    def test_recovery_only_C_application_and_necessary_same_round_nvs(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);snapshot=root/'prefix';snapshot.write_bytes(b'prefix');old=root/'C.bin';old.write_bytes(b'C')
            nvs=root/'nvs-before.bin';nvs.write_bytes(b'nvs')
            backup=dict(sha256=trial.sha(snapshot),nvs_sha256=trial.sha(nvs))
            class Port:
                def __enter__(self):return self
                def __exit__(self,*args):pass
            class Session:
                def __init__(self,current):self.folder=root;self.commands=[];self.current=current
                def esptool(self,port,command):
                    self.commands.append(command)
                    if command[0]=='read_flash':Path(command[-1]).write_bytes(self.current)
                def serial(self):return Port()
                def collect(self,*args):pass
            with patch.object(trial,'OLD_SHA',trial.sha(old)),patch.object(trial.tf,'unique_port',return_value='fake'),patch.object(trial.recorder,'boot_check'):
                for current,expected in ((b'nvs',['0x10000']),(b'changed',['0x10000','0x9000'])):
                    session=Session(current);trial.restore_application(session,snapshot,backup,old)
                    writes=[c for c in session.commands if c[0]=='write_flash']
                    self.assertEqual([c[-2] for c in writes],expected)
                    self.assertNotIn(str(snapshot),str(writes))
                nvs.write_bytes(b'corrupt');session=Session(b'changed')
                with self.assertRaisesRegex(RuntimeError,'Same-round'):trial.restore_application(session,snapshot,backup,old)
                self.assertEqual([c[-2] for c in session.commands if c[0]=='write_flash'],['0x10000'])

    def test_boundaries(self):
        self.assertEqual(trial.OLD_SOURCE,'9ce3f4e1d317bb23c5dcd55fa78cb1687b969b9b')
        self.assertTrue(trial.SCOPE['requires_healthy_C']);self.assertFalse(trial.SCOPE['reset_from_saved_panic'])
        self.assertEqual(trial.SCOPE['backup_end'],0x123000)
        for key in ('change_network','delete_format_repair','main_merge_release'):self.assertFalse(trial.SCOPE[key])

if __name__=='__main__':unittest.main()
