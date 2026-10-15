"""Check a hardware operation record before device discovery; human approval remains required."""
from pathlib import Path
import datetime
import hashlib
import json
import subprocess

def require_approval(root, record_path, operation, expected_mac, files):
    root = Path(root).resolve()
    if record_path is None:
        raise RuntimeError('Hardware operation blocked: requires --approval-record after explicit user confirmation')
    path = Path(record_path).resolve()
    if not path.is_relative_to(root / 'local-data/approvals'):
        raise RuntimeError('Approval records must remain in local-data/approvals/')
    record = json.loads(path.read_text(encoding='utf-8-sig'))
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(['git', 'status', '--porcelain'], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    if dirty or record.get('source_commit') != head:
        raise RuntimeError('Approval requires a clean, matching source commit')
    if record.get('operation') != operation or record.get('controller_mac') != expected_mac:
        raise RuntimeError('Approval operation or controller identity mismatch')
    if not str(record.get('user_confirmation', '')).strip():
        raise RuntimeError('Missing genuine user confirmation reference')
    expiry = datetime.datetime.fromisoformat(record['expires_at'])
    now = datetime.datetime.now(datetime.timezone.utc)
    if expiry.tzinfo is None or not now < expiry <= now + datetime.timedelta(hours=24):
        raise RuntimeError('Approval is expired or exceeds the 24-hour operation window')
    approved = record.get('files', {})
    for file in files:
        file = Path(file).resolve()
        key = file.relative_to(root).as_posix() if file.is_relative_to(root) else file.as_posix()
        digest = hashlib.sha256(file.read_bytes()).hexdigest()
        if approved.get(key) != digest:
            raise RuntimeError('Approved input missing or changed: ' + key)
    return record
