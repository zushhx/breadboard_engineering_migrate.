"""E hardware gate tests use fake sessions only; no serial/USB discovery."""
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
spec=importlib.util.spec_from_file_location('file_paths_trial',ROOT/'tools/release_v010_file_paths_trial.py')
trial=importlib.util.module_from_spec(spec);spec.loader.exec_module(trial)

def status(**changes):
    value=dict(phase='passed',errors=0,ready=True,files_busy=False,card_serial=trial.tf.CARD_SERIAL,
               card_bytes=str(trial.tf.CAPACITY),boot='a'*32,run='today-new-run')
    value.update(changes);return value

class FilePathsGuards(unittest.TestCase):
    def test_no_approval_reads_no_private_inputs(self):
        with patch.object(trial,'inputs',side_effect=AssertionError('private inputs')):
            with self.assertRaisesRegex(RuntimeError,'Explicit candidate approval'):trial.validate(None)

    def test_unbound_E_rejected_before_manifest_read(self):
        with patch.object(trial,'SOURCE','UNBOUND_E_SOURCE'):
            with self.assertRaisesRegex(RuntimeError,'Candidate E is unbound'):trial.inputs()

    def test_D_image_is_not_E(self):
        with patch.object(trial,'SOURCE',trial.OLD_SOURCE),patch.object(trial,'CANDIDATE',trial.OLD_SHA),patch.object(trial,'MANIFEST',Path('unused')):
            with self.assertRaisesRegex(RuntimeError,'unbound'):trial.bound_candidate()

    def test_operation_and_scope_distinct_from_D(self):
        with patch.object(trial,'inputs',return_value=('new','old',[])),patch.object(trial,'require_approval',return_value={'scope':{},'hardware_ready_confirmed':True}) as gate:
            with self.assertRaisesRegex(RuntimeError,'scope/readiness'):trial.validate('fixture')
            self.assertEqual(gate.call_args.args[2],'release-v010-file-paths-smoke')

    def test_readiness_required(self):
        with patch.object(trial,'inputs',return_value=('new','old',[])),patch.object(trial,'require_approval',return_value={'scope':trial.SCOPE}):
            with self.assertRaisesRegex(RuntimeError,'scope/readiness'):trial.validate('fixture')

    def test_quiescent_D_does_not_bind_a_historical_run(self):
        for phase in ('idle','passed'):
            before=status(phase=phase,run='new-run-on-new-date')
            trial.check_quiescent(before);trial.check_same_runtime(before,dict(before))

    def test_busy_or_invalid_D_rejected(self):
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

    def test_backup_requires_exact_D_app_and_same_AP_nvs(self):
        with tempfile.TemporaryDirectory() as folder:
            bundle=Path(folder);(bundle/'artifacts').mkdir()
            (bundle/'artifacts/bootloader.bin').write_bytes(b'boot');(bundle/'artifacts/partitions.bin').write_bytes(b'parts')
            data=bytearray(trial.SCOPE['backup_end']);data[:4]=b'boot';data[0x8000:0x8005]=b'parts';data[0x10000:0x10001]=b'D'
            nvs=trial.hashlib.sha256(data[0x9000:0xe000]).hexdigest()
            with patch.object(trial,'BUNDLE',bundle),patch.object(trial,'EXPECTED_NVS',nvs):
                trial.check_backup(bytes(data),b'D')
                for offset in (0,0x8000,0x9000,0x10000):
                    bad=bytearray(data);bad[offset]^=1
                    with self.subTest(offset=offset),self.assertRaisesRegex(RuntimeError,'no write'):trial.check_backup(bytes(bad),b'D')

    def test_recovery_only_D_application_and_necessary_same_round_nvs(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);snapshot=root/'prefix';snapshot.write_bytes(b'prefix');old=root/'D.bin';old.write_bytes(b'D')
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

    def test_sector_extent_cannot_silently_under_cover_E(self):
        self.assertEqual(trial.required_backup_end(0x113000,0x112000),0x123000)
        with patch.dict(trial.SCOPE,backup_end=0x123000):
            trial.validate_backup_extent(0x113000,0x112000)
            with self.assertRaisesRegex(RuntimeError,'required 0x124000'):
                trial.validate_backup_extent(0x113001,0x112000)
        with patch.dict(trial.SCOPE,backup_end=0x124000):
            trial.validate_backup_extent(0x113001,0x112000)
            with self.assertRaisesRegex(RuntimeError,'required 0x123000'):
                trial.validate_backup_extent(0x113000,0x112000)

    def test_boundaries(self):
        self.assertEqual(trial.OLD_SOURCE,'9cc8c174f628ed0654403b248c72389a426f2ef6')
        self.assertTrue(trial.SCOPE['requires_quiescent_D']);self.assertFalse(trial.SCOPE['reset_from_saved_panic'])
        self.assertTrue(trial.SCOPE['D_known_INA_quality_failure'])
        self.assertEqual(trial.HOST,trial.ROOT/'local-data/release-v010/device-host-E/DeviceHost.exe')
        self.assertEqual(trial.SCOPE['backup_end']%4096,0)
        for key in ('change_network','delete_format_repair','main_merge_release'):self.assertFalse(trial.SCOPE[key])

if __name__=='__main__':unittest.main()
