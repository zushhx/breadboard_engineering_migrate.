"""Compile and fault-test the exact byte FIFO used by firmware; no device I/O."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from rate_test_compiler import binary_path, compiler_command

ROOT = Path(__file__).resolve().parents[1]


class Rate240StreamTests(unittest.TestCase):
    def test_actual_fifo_backpressure_and_session_reset(self):
        compiler = compiler_command(ROOT)
        with tempfile.TemporaryDirectory() as directory:
            binary = binary_path(directory,'rate240_stream')
            compile_result = subprocess.run(compiler + ['-std=c++17',
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
