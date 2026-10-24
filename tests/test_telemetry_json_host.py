import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from test_telemetry_json import run


class TelemetryJson(unittest.TestCase):
    def test_production_renderers_with_no_cpp_heap(self):
        run()
