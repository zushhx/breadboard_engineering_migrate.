"""Validate project files without importing serial, contacting devices or changing tracked files."""
from pathlib import Path
import argparse
import datetime
import hashlib
import json
import re
import subprocess
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / 'baselines/tf-readonly-20261015'

def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def baseline_errors():
    manifest = json.loads((BUNDLE / 'manifest.json').read_text(encoding='utf-8'))
    errors = []
    for item in manifest['files']:
        path = (BUNDLE / item['path']).resolve()
        if not path.is_relative_to(BUNDLE.resolve()) or not path.is_file():
            errors.append('Missing/invalid frozen input: ' + item['path'])
        elif path.stat().st_size != item['bytes'] or sha(path) != item['sha256']:
            errors.append('Frozen file mismatch: ' + item['path'])
    return errors

def git(*args):
    return subprocess.run(['git', *args], cwd=ROOT, check=True, capture_output=True).stdout

def check(local=False):
    errors = baseline_errors()
    checks = []
    tracked = [s for s in git('ls-files', '-z').decode('utf-8').split('\0') if s]
    manifest_name = 'baselines/tf-readonly-20261015/manifest.json'
    indexed_manifest_data = git('show', ':' + manifest_name)
    indexed_manifest = json.loads(indexed_manifest_data)
    if indexed_manifest_data != (ROOT / manifest_name).read_bytes():
        errors.append('Frozen manifest differs between Git index and working tree')
    indexed_expected = {'baselines/tf-readonly-20261015/' + item['path']: item
                        for item in indexed_manifest['files']}
    for name in indexed_expected.keys() - set(tracked):
        errors.append('Frozen file missing from Git index: ' + name)
    migration = json.loads((ROOT / 'docs/MIGRATION.json').read_text(encoding='utf-8'))
    for item in migration['files']:
        if item['after'] in migration.get('authorized_edits', {}):
            continue
        if not local and not item.get('tracked_after_migration'):
            continue
        path = ROOT / item['after']
        if not path.is_file() or path.stat().st_size != item['bytes'] or sha(path) != item['sha256']:
            errors.append('Original content mismatch: ' + item['after'])
    checks.append('frozen_inputs_and_migration_hashes')
    documents = ['README.md', 'AGENTS.md', 'docs/STATUS.md', 'docs/ROADMAP.md', 'docs/WORKFLOW.md',
                 'docs/history/README.md', 'docs/hardware/README.md', 'docs/reports/README.md',
                 '面包板样机_固件/README.md', 'baselines/tf-readonly-20261015/README.md',
                 'docs/plans/TF_WRITE_DIAGNOSTIC_20261015.md',
                 '面包板样机_固件/diagnostics/tf_write_probe/README.md',
                 'docs/plans/SENSOR_MONITOR_BRINGUP_20261015.md',
                 'docs/hardware/wiring/传感器与电流监测接入_2026-10-15.md',
                 '面包板样机_固件/diagnostics/sensor_monitor_probe/README.md',
                 'docs/plans/SENSOR_TF_RECORDER_20261015.md',
                 '面包板样机_固件/diagnostics/sensor_tf_recorder/README.md']
    for name in documents:
        path = ROOT / name
        if not path.is_file():
            errors.append('Missing canonical document: ' + name)
            continue
        text = re.sub(r'```.*?```', '', path.read_text(encoding='utf-8-sig'), flags=re.S)
        for target in re.findall(r'\[[^\]\n]+\]\(([^)\n]+)\)', text):
            target = target.strip('<>')
            if urlsplit(target).scheme or target.startswith('#'):
                continue
            destination = (path.parent / unquote(target.split('#')[0])).resolve()
            if not destination.is_relative_to(ROOT) or not destination.exists():
                errors.append('Broken canonical link: ' + name + ' -> ' + target)
    checks.append('canonical_document_links')
    secret = re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----)')
    for name in tracked:
        parts = Path(name).parts
        if any(s in parts for s in ['local-data', '.pio', 'node_modules', 'backups', 'logs', 'PowerShell-7.6.6']):
            errors.append('Local/private/generated path tracked: ' + name)
        data = git('show', ':' + name)
        expected = indexed_expected.get(name)
        if expected and (len(data) != expected['bytes'] or hashlib.sha256(data).hexdigest() != expected['sha256']):
            errors.append('Frozen file mismatch in Git index: ' + name)
        if len(data) > 10 * 1024 * 1024:
            errors.append('Unexpected large tracked file: ' + name)
        if secret.search(data):
            errors.append('Credential pattern in Git index: ' + name)
    checks.append('git_index_frozen_hashes_paths_size_and_credential_patterns')
    for name in ['platformio.ini', 'platformio.tf_protocol_probe.ini', 'platformio.tf_software_probe.ini',
                 'diagnostics/tf_idf_probe/platformio.ini', 'diagnostics/tf_write_probe/platformio.ini',
                 'diagnostics/sensor_monitor_probe/platformio.ini',
                 'diagnostics/sensor_tf_recorder/platformio.ini']:
        text = (ROOT / '面包板样机_固件' / name).read_text(encoding='utf-8-sig')
        if 'block_implicit_upload.py' not in text:
            errors.append('Missing upload guard: ' + name)
    checks.append('default_upload_guards')
    if local:
        manifest = json.loads((BUNDLE / 'manifest.json').read_text(encoding='utf-8'))
        item = manifest['original_backup']
        path = ROOT / item['local_path']
        if not path.is_file() or sha(path) != item['sha256']:
            errors.append('Original controller backup missing or mismatched')
        home = Path('/mnt/c/Users/acer/.codex') if str(ROOT).startswith('/mnt/c/') else Path.home() / '.codex'
        source = home / 'engineering-policy/POLICY.md'
        active = home / 'AGENTS.md'
        if not source.is_file() or not active.is_file() or source.read_bytes() != active.read_bytes():
            errors.append('Global policy deployment differs from reviewed source')
        override = home / 'AGENTS.override.md'
        if override.is_file() and override.read_text(encoding='utf-8-sig').strip():
            errors.append('Global policy is shadowed by AGENTS.override.md')
        checks.append('local_backup_and_global_policy')
    return {'date': datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(),
            'head': git('rev-parse', 'HEAD').decode().strip(), 'mode': 'local' if local else 'checkout',
            'checks': checks, 'passed': not errors, 'errors': errors, 'hardware_accessed': False}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = check(args.local)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        destination = args.output.resolve()
        allowed = (ROOT / 'local-data').resolve()
        if not destination.is_relative_to(allowed):
            raise SystemExit('Check output must stay under local-data/')
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text + '\n', encoding='utf-8')
    print(text)
    raise SystemExit(0 if result['passed'] else 1)

if __name__ == '__main__':
    main()
