import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from test_controller_network import run


class NetworkV2(unittest.TestCase):
    def test_production_network_password_persistence_and_failures(self):
        run()
