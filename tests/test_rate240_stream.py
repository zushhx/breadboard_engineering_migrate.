"""Compile and fault-test the exact byte FIFO used by firmware; no device I/O."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Rate240StreamTests(unittest.TestCase):
    def test_actual_fifo_backpressure_and_session_reset(self):
        compiler = ROOT / 'local-data/tooling/ziglang-0.13.0/ziglang/zig.exe'
        self.assertTrue(compiler.is_file(), 'Pinned Zig 0.13.0 compiler required')
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'rate240_stream.exe'
            compile_result = subprocess.run([str(compiler), 'c++', '-std=c++17',
                '-Wall', '-Wextra', '-Werror', '-I',
                str(ROOT / '面包板样机_固件/diagnostics/sensor_web_recorder/src'),
                str(ROOT / 'tests/rate240_stream_test.cpp'), '-o', str(binary)],
                capture_output=True, text=True, timeout=180)
            self.assertEqual(compile_result.returncode, 0, compile_result.stdout + compile_result.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn('PASS:', run.stdout)


if __name__ == '__main__':
    unittest.main()
