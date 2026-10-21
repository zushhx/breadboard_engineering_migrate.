"""Build the independent TF write diagnostic. No serial, upload, reset or card I/O."""
from pathlib import Path
import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys

from project_check import ROOT, BUNDLE, baseline_errors, sha

SOURCE = ROOT / '面包板样机_固件/diagnostics/tf_write_probe'
ENVIRONMENT = 'tf_write_idf542_4bit_20m'


def config_values(path):
    return {line.split('=', 1)[0]: line for line in path.read_text(encoding='utf-8').splitlines()
            if line.startswith('CONFIG_')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    errors = baseline_errors()
    if errors:
        raise SystemExit('\n'.join(errors))
    if subprocess.check_output([sys.executable, '-m', 'platformio', '--version'], text=True).strip() != 'PlatformIO Core, version 6.2.0':
        raise SystemExit('Expected PlatformIO Core 6.2.0')
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT))
    inputs = sorted(path for path in SOURCE.rglob('*') if path.is_file() and
                    not any(part in {'.pio', '__pycache__'} for part in path.relative_to(SOURCE).parts))
    input_record = {path.relative_to(ROOT).as_posix(): sha(path) for path in inputs}
    input_record[Path(__file__).resolve().relative_to(ROOT).as_posix()] = sha(Path(__file__))
    input_hash = hashlib.sha256(json.dumps(input_record, sort_keys=True).encode('utf-8')).hexdigest()
    # ASCII path avoids the Windows IDF build issues observed with the workspace name.
    stage = (Path.home() / '.platformio/breadboard_write_builds' / input_hash[:16]
             if os.name == 'nt' else ROOT / 'local-data/build/tf-write' / input_hash[:16] / 'project')
    stage.mkdir(parents=True, exist_ok=True)
    for source in inputs:
        target = stage / source.relative_to(SOURCE)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    (stage / 'src/build_identity.h').write_text(
        '#pragma once\n#define TF_SOURCE_COMMIT ' + json.dumps(head) +
        '\n#define TF_INPUT_SHA256 ' + json.dumps(input_hash) + '\n', encoding='utf-8')
    print('SOURCE_COMMIT=' + head, flush=True)
    print('WORKING_TREE_DIRTY=' + str(dirty), flush=True)
    print('INPUT_SHA256=' + input_hash, flush=True)
    print('STAGING=' + str(stage), flush=True)
    if args.prepare_only:
        print('Inputs prepared only; no compiler or device access', flush=True)
        return
    stamp = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y%m%d_%H%M%S_%f')
    output = ROOT / 'local-data/build/tf-write' / input_hash[:16] / stamp
    output.mkdir(parents=True, exist_ok=False)
    command = [sys.executable, '-m', 'platformio', 'run', '--project-dir', str(stage), '--environment', ENVIRONMENT]
    with (output / 'build.log').open('x', encoding='utf-8') as log:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding='utf-8', errors='replace')
        for line in process.stdout:
            print(line, end='', flush=True)
            log.write(line)
        status = process.wait()
    if status:
        raise SystemExit(status)
    actual_config = stage / ('sdkconfig.' + ENVIRONMENT)
    frozen_config = BUNDLE / 'source/sdkconfig.tf_idf542_4bit_20m'
    before, after = config_values(frozen_config), config_values(actual_config)
    changed = sorted(key for key in before.keys() | after.keys() if before.get(key) != after.get(key))
    if changed:
        raise SystemExit('Unexpected generated SDK configuration differences: ' + ', '.join(changed))
    artifacts = []
    for name in ['firmware.bin', 'bootloader.bin', 'partitions.bin', 'firmware.elf']:
        source = stage / '.pio/build' / ENVIRONMENT / name
        target = output / 'artifacts' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        artifacts.append({'path': target.relative_to(ROOT).as_posix(), 'bytes': target.stat().st_size, 'sha256': sha(target)})
    shutil.copy2(actual_config, output / 'sdkconfig.generated')
    if sha(output / 'artifacts/partitions.bin') != sha(BUNDLE / 'artifacts/partitions.bin'):
        raise SystemExit('Candidate partition table differs from frozen baseline')
    record = {'source_commit': head, 'working_tree_dirty_at_start': dirty,
              'input_sha256': input_hash, 'inputs': input_record,
              'staged_identity_sha256': sha(stage / 'src/build_identity.h'),
              'baseline_manifest_sha256': sha(BUNDLE / 'manifest.json'), 'command': command,
              'sdkconfig_differences': changed, 'sdkconfig_generated_sha256': sha(actual_config),
              'artifacts': artifacts, 'build_passed': True, 'hardware_tested': False,
              'note': 'Software candidate only; not flashed, no TF writes or durability test'}
    manifest = output / 'build_manifest.json'
    manifest.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('BUILD_MANIFEST=' + str(manifest), flush=True)


if __name__ == '__main__':
    main()
