"""Portable recording-name checks in temporary host folders; no device access."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / '面包板样机_固件/diagnostics/sensor_web_recorder/src'

if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='record-time-names-') as folder:
        out = Path(folder)
        subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-I'+str(SRC),
                        str(ROOT/'tests/record_time_names_test.cpp'), '-o', str(out/'names')], check=True)
        subprocess.run([str(out/'names'), str(out/'card')], check=True)
