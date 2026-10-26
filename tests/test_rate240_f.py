from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from rate190_receive import request_line


class BoundRequestTests(unittest.TestCase):
    def test_mirror_binds_run_format_and_duration(self):
        self.assertEqual(request_line('a'*32, 'MIRROR', 200, 'AB123456', 'pattern'),
                         ('BENCH2 '+'a'*32+' MIRROR 200 AB123456 pattern\n').encode())

    def test_invalid_bound_requests_are_rejected(self):
        for run, fmt in ((None,'record'), ('abcdef12','record'), ('AB123456','other')):
            with self.subTest(run=run,fmt=fmt), self.assertRaises(ValueError):
                request_line('a'*32,'MIRROR',30,run,fmt)
        with self.assertRaises(ValueError):
            request_line('a'*32+'\n','SYNTH',30)

    def test_synth_only_uses_ram_protocol(self):
        self.assertEqual(request_line('a'*32,'SYNTH',30),('BENCH1 '+'a'*32+' SYNTH 30\n').encode())


if __name__ == '__main__':
    unittest.main()
