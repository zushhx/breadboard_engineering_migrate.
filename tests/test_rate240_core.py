"""Run the actual bounded source schedule logic; no device or network access."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Rate240CoreTests(unittest.TestCase):
    def test_source_and_backpressure_boundaries(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        zig = ROOT / 'local-data/tooling/ziglang-0.13.0/ziglang/zig.exe'
        command = [compiler] if compiler else [str(zig), 'c++']
        self.assertTrue(compiler or zig.is_file(), 'Pinned native compiler required')
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / ('rate240.exe' if os.name == 'nt' else 'rate240')
            compiled = subprocess.run(command + ['-std=c++17', '-Wall', '-Wextra', '-Werror',
                '-I', str(ROOT / '面包板样机_固件/diagnostics/sensor_web_recorder/src'),
                str(ROOT / 'tests/rate240_core_test.cpp'), '-o', str(binary)],
                capture_output=True, text=True, timeout=180)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn('PASS:', run.stdout)


if __name__ == '__main__':
    unittest.main()
