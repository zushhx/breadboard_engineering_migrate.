from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from rate_test_compiler import binary_path, compiler_command

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from test_controller_v2 import HEADERS


class FSessionRegression(unittest.TestCase):
    def test_existing_session_and_mutex_boundaries(self):
        compiler = compiler_command(ROOT)
        with tempfile.TemporaryDirectory() as directory:
            include = Path(directory)/'include'
            for name in ('freertos/FreeRTOS.h', 'freertos/semphr.h'):
                path = include/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(HEADERS[name], encoding='utf-8')
            for name in ('controller_v2_test', 'controller_mutex_test'):
                with self.subTest(name=name):
                    binary = binary_path(directory, name)
                    result = subprocess.run(compiler + ['-std=c++17', '-Wall', '-Wextra', '-Werror',
                        '-I'+str(ROOT/'面包板样机_固件/diagnostics/sensor_web_recorder/src'),
                        '-I'+str(include),
                        str(ROOT/'tests'/(name+'.cpp')), '-o', str(binary)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
                    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stdout+result.stderr)


if __name__ == '__main__':
    unittest.main()
