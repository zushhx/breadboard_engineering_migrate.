"""Install this repository's local helper hooks; no global Git settings are changed."""
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
subprocess.run(['git', 'config', '--local', 'core.hooksPath', '.githooks'], cwd=root, check=True)
for name in ['pre-commit', 'pre-push']:
    path = root / '.githooks' / name
    path.chmod(path.stat().st_mode | 0o111)
print('Local hooks installed; they do not replace server protection or user approval')
