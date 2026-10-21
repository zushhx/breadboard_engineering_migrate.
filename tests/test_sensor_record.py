import io
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
import parse_sensor_record as p


class SensorRecord(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.temp.name)
        compiler = shutil.which('g++') or shutil.which('clang++')
        zig = ROOT/'local-data/tooling/ziglang-0.13.0/ziglang/zig.exe'
        if compiler:
            command = [compiler]
        elif os.name == 'nt' and zig.is_file():
            command = [str(zig), 'c++']
        else:
            raise RuntimeError('Native C++ compiler required, or pinned local Zig 0.13.0 on Windows')
        binary = cls.folder/('fixture.exe' if os.name == 'nt' else 'fixture')
        subprocess.run(command + ['-std=c++17', '-Wall', '-Wextra', '-Werror',
            str(ROOT/'tests/record_fixture.cpp'), '-o', str(binary)], check=True, timeout=180)
        cls.path = cls.folder/'good.bin'
        subprocess.run([str(binary), str(cls.path)], check=True)
        cls.good = cls.path.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def check_bad(self, data):
        path = self.folder/'bad.bin'; path.write_bytes(data)
        with self.assertRaises(p.RecordError): p.validate(path)

    def change_block(self, offset, fields=None, payload=None):
        data = bytearray(self.good)
        values = list(p.BLOCK.unpack_from(data, offset))
        if fields:
            for key, value in fields.items(): values[key] = value
        size = values[3]
        content = bytes(data[offset+p.BLOCK.size:offset+p.BLOCK.size+size]) if payload is None else payload
        head = p.BLOCK.pack(*values)
        values[-1] = zlib.crc32(head[:-4] + content)
        data[offset:offset+p.BLOCK.size] = p.BLOCK.pack(*values)
        data[offset+p.BLOCK.size:offset+p.BLOCK.size+len(content)] = content
        return data

    def test_cpp_wire_and_signed_conversion(self):
        xyz, ina = io.StringIO(), io.StringIO()
        result = p.validate(self.path, xyz, ina)
        self.assertEqual(result['footer']['samples'], 2)
        self.assertEqual(result['preview'][0][2:5], (-32768, 32767, 0x1234))
        self.assertIn('3.29', ina.getvalue())
        self.assertFalse(result['hardware_accuracy_accepted'])

    def test_truncation_at_header_payload_and_footer(self):
        for size in (1, p.HEADER.size-1, p.HEADER.size+20, len(self.good)-1, len(self.good)-p.FOOTER.size-p.BLOCK.size):
            with self.subTest(size=size): self.check_bad(self.good[:size])

    def test_crc_detects_payload_damage(self):
        data = bytearray(self.good); data[p.HEADER.size+p.BLOCK.size+2] ^= 1; self.check_bad(data)

    def test_sequence_count_and_unknown_type_even_with_good_crc(self):
        for fields in ({2: 1}, {4: 3}, {1: 9}, {7: 2}, {3: 0xffffffff}, {5: 9, 6: 8}):
            with self.subTest(fields=fields): self.check_bad(self.change_block(p.HEADER.size, fields))

    def test_bad_tag_even_with_good_crc(self):
        content = bytearray(self.good[p.HEADER.size+p.BLOCK.size:p.HEADER.size+p.BLOCK.size+14]); content[0] = 0
        self.check_bad(self.change_block(p.HEADER.size, payload=content))

    def test_footer_error_and_wrong_totals(self):
        offset = len(self.good)-p.FOOTER.size-p.BLOCK.size
        for index in (0, 3, 4, 5, 6):
            foot = list(p.FOOTER.unpack(self.good[-p.FOOTER.size:])); foot[index] += 1
            with self.subTest(index=index): self.check_bad(self.change_block(offset, payload=p.FOOTER.pack(*foot)))

    def test_trailing_data_rejected(self): self.check_bad(self.good+b'garbage')

    def test_cli_rejects_incomplete_without_export(self):
        path = self.folder/'incomplete.bin'; path.write_bytes(self.good[:-1]); output = self.folder/'should-not-exist'
        run = subprocess.run([sys.executable, str(ROOT/'tools/parse_sensor_record.py'), str(path), '--output', str(output)], capture_output=True)
        self.assertEqual(run.returncode, 2); self.assertFalse(output.exists())


if __name__ == '__main__': unittest.main()
