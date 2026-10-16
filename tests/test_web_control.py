"""Run actual firmware control-state logic and local credential isolation checks."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from build_sensor_web_recorder import credentials, credential_header


class WebControl(unittest.TestCase):
    def test_owner_state_and_network_loss(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        zig = ROOT/'local-data/tooling/ziglang-0.13.0/ziglang/zig.exe'
        command = [compiler] if compiler else [str(zig), 'c++']
        if not compiler and not zig.is_file():
            self.fail('Native compiler or pinned Zig required')
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory)/('web_model.exe' if os.name == 'nt' else 'web_model')
            result = subprocess.run(command + ['-std=c++17', '-Wall', '-Wextra', '-Werror',
                '-I', str(ROOT/'面包板样机_固件/diagnostics/sensor_web_recorder/src'),
                str(ROOT/'tests/web_model_test.cpp'), '-o', str(binary)], capture_output=True, text=True, timeout=180)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            self.assertIn('PASS:', result.stdout)

    def test_ci_has_no_network_credentials(self):
        self.assertEqual(credentials(None), ('', ''))
        self.assertIn('#define WIFI_SSID ""', credential_header(*credentials(None)))

    def test_credentials_cannot_come_from_public_source(self):
        with self.assertRaises(ValueError):
            credentials(ROOT/'README.md')

    def test_header_escapes_source_and_unicode(self):
        header = credential_header('中文"\\x', 'abc\"\\1234')
        self.assertNotIn('中文', header)
        self.assertNotIn('abc', header)
        self.assertIn('\\042', header)
        self.assertIn('\\134', header)


if __name__ == '__main__':
    unittest.main()
