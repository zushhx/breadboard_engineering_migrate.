from pathlib import Path
import datetime
import json
import runpy
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from operation_gate import require_approval

class UploadGuardTest(unittest.TestCase):
    def run_target(self, target, guard='面包板样机_固件/tools/block_implicit_upload.py'):
        package = types.ModuleType('SCons')
        script = types.ModuleType('SCons.Script')
        script.COMMAND_LINE_TARGETS = [target]
        with patch.dict(sys.modules, {'SCons': package, 'SCons.Script': script}):
            runpy.run_path(str(ROOT / guard))

    def test_build_is_allowed_and_device_write_targets_are_blocked(self):
        self.run_target('buildprog')
        for target in ['upload', 'uploadfs', 'erase', 'program']:
            with self.subTest(target=target), self.assertRaises(RuntimeError):
                self.run_target(target)

    def test_write_diagnostic_guard_blocks_device_writes(self):
        guard = '面包板样机_固件/diagnostics/tf_write_probe/block_implicit_upload.py'
        self.run_target('buildprog', guard)
        for target in ['upload', 'uploadfs', 'uploadfsota', 'erase', 'program']:
            with self.subTest(target=target), self.assertRaises(RuntimeError):
                self.run_target(target, guard)

class ApprovalGuardTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.records = self.root / 'local-data/approvals'
        self.records.mkdir(parents=True)
        self.path = self.records / 'test.json'
        self.file = self.root / 'image.bin'
        self.file.write_bytes(b'candidate')
        self.commit = 'a' * 40
        self.record = {'source_commit': self.commit, 'operation': 'flash', 'controller_mac': 'test-device',
                       'user_confirmation': 'test fixture; never authorizes real hardware',
                       'expires_at': (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)).isoformat(),
                       'files': {}}

    def fake_git(self, command, **kwargs):
        return subprocess.CompletedProcess(command, 0, self.commit + '\n' if 'rev-parse' in command else '', '')

    def invoke(self, record=True):
        if record:
            self.path.write_text(json.dumps(self.record), encoding='utf-8')
        with patch('operation_gate.subprocess.run', side_effect=self.fake_git):
            return require_approval(self.root, self.path if record else None, 'flash', 'test-device', [self.file])

    def test_missing_record_stops_before_device_access(self):
        with self.assertRaises(RuntimeError):
            self.invoke(False)

    def test_changed_candidate_is_rejected(self):
        self.record['files']['image.bin'] = '0' * 64
        with self.assertRaisesRegex(RuntimeError, 'changed'):
            self.invoke()

    def test_wrong_commit_is_rejected(self):
        self.record['source_commit'] = 'b' * 40
        with self.assertRaisesRegex(RuntimeError, 'commit'):
            self.invoke()

    def test_null_or_nontext_confirmation_is_rejected(self):
        for value in [None, False, 123, '', '   ']:
            with self.subTest(value=value):
                self.record['user_confirmation'] = value
                with self.assertRaisesRegex(RuntimeError, 'confirmation'):
                    self.invoke()

    def test_matching_record_is_only_a_file_validation(self):
        import hashlib
        self.record['files']['image.bin'] = hashlib.sha256(self.file.read_bytes()).hexdigest()
        self.assertEqual(self.invoke()['operation'], 'flash')

if __name__ == '__main__':
    unittest.main()
