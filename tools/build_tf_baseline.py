"""Build only the frozen TF input; never opens a serial port or invokes upload."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys

from project_check import ROOT, BUNDLE, baseline_errors, sha

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    errors = baseline_errors()
    if errors:
        raise SystemExit('\n'.join(errors))
    manifest_hash = sha(BUNDLE / 'manifest.json')
    if os.name == 'nt':
        stage = Path.home() / '.platformio/breadboard_baseline_builds' / manifest_hash[:16]
    else:
        stage = ROOT / 'local-data/build/tf-baseline' / manifest_hash[:16] / 'project'
    stage.mkdir(parents=True, exist_ok=True)
    for source in (BUNDLE / 'source').rglob('*'):
        if source.is_file():
            target = stage / source.relative_to(BUNDLE / 'source')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    command = [sys.executable, '-m', 'platformio', 'run', '--project-dir', str(stage), '--environment', 'tf_idf542_4bit_20m']
    print('FROZEN_MANIFEST_SHA256=' + manifest_hash, flush=True)
    print('STAGING=' + str(stage), flush=True)
    if args.prepare_only:
        print('Prepared verified inputs only; no compiler or hardware access', flush=True)
        return
    output = ROOT / 'local-data/build/tf-baseline' / manifest_hash[:16]
    output.mkdir(parents=True, exist_ok=True)
    with (output / 'build.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
        for line in process.stdout:
            print(line, end='', flush=True)
            log.write(line)
        status = process.wait()
    if status:
        raise SystemExit(status)
    artifacts = []
    for name in ['firmware.bin', 'bootloader.bin', 'partitions.bin', 'firmware.elf']:
        source = stage / '.pio/build/tf_idf542_4bit_20m' / name
        destination = output / 'artifacts' / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        artifacts.append({'path': str(destination.relative_to(ROOT)), 'bytes': destination.stat().st_size, 'sha256': sha(destination)})
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
    record = {'source_commit': head, 'frozen_manifest_sha256': manifest_hash, 'command': command,
              'artifacts': artifacts, 'build_passed': True, 'hardware_tested': False,
              'note': 'New build, not flashed; original tested artifacts remain unchanged'}
    (output / 'build_manifest.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Build only complete: ' + str(output / 'build_manifest.json'), flush=True)

if __name__ == '__main__':
    main()
