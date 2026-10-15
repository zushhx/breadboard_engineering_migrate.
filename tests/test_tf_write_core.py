"""Compile/run the actual firmware workflow with a fault-injecting host store."""
from pathlib import Path
import hashlib
import os
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / '面包板样机_固件/diagnostics/tf_write_probe/src'


class TfWriteCoreTests(unittest.TestCase):
    def test_workflow_faults_and_command_parser(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        zig = ROOT / 'local-data/tooling/ziglang-0.13.0/ziglang/zig.exe'
        if compiler:
            command = [compiler]
        elif os.name == 'nt' and zig.is_file():
            command = [str(zig), 'c++']
        else:
            self.fail('Native C++ compiler required: g++/clang++, or local pinned Zig 0.13.0 on Windows')
        output = ROOT / 'local-data/build/native-tests'
        output.mkdir(parents=True, exist_ok=True)
        binary = output / ('tf_write_core_test.exe' if os.name == 'nt' else 'tf_write_core_test')
        command += ['-std=c++11', '-Wall', '-Wextra', '-Werror', '-fno-exceptions', '-fno-rtti',
                    '-I', str(SOURCE), str(SOURCE / 'tf_test_core.cpp'),
                    str(ROOT / 'tests/tf_write_core_test.cpp'), '-o', str(binary)]
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS:', result.stdout)
        print(result.stdout.strip())
        payload = output / 'reference.bin'
        result = subprocess.run([str(binary), '--payload', str(payload)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(payload.stat().st_size, 1048576)
        self.assertEqual(hashlib.sha256(payload.read_bytes()).hexdigest(),
                         '7c79a64ca466f57076b4fa369dd7861cf4657c7b00ed8c1bad0c9c9336cc87b7')


if __name__ == '__main__':
    unittest.main()
