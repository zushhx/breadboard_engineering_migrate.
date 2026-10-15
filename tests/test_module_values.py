"""Exercise the value/validity logic used by the module firmware, without device I/O."""
from pathlib import Path
import os
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / '面包板样机_固件/diagnostics/sensor_monitor_probe/src'


class ModuleValuesTests(unittest.TestCase):
    def test_invalid_data_and_signed_measurements(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        zig = ROOT / 'local-data/tooling/ziglang-0.13.0/ziglang/zig.exe'
        if compiler:
            command = [compiler]
        elif os.name == 'nt' and zig.is_file():
            command = [str(zig), 'c++']
        else:
            self.fail('Native C++ compiler required')
        output = ROOT / 'local-data/build/native-tests'
        output.mkdir(parents=True, exist_ok=True)
        binary = output / ('module_values_test.exe' if os.name == 'nt' else 'module_values_test')
        command += ['-std=c++11', '-Wall', '-Wextra', '-Werror', '-fno-exceptions', '-fno-rtti',
                    '-I', str(SOURCE), str(ROOT / 'tests/module_values_test.cpp'), '-o', str(binary)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS:', result.stdout)
        print(result.stdout.strip())


if __name__ == '__main__':
    unittest.main()
