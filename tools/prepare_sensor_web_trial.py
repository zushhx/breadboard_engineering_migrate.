"""Prepare a reviewable trial packet. No serial, device discovery, reset or flash code."""
import argparse
import json
import subprocess
from pathlib import Path
from project_check import ROOT, BUNDLE, sha


def prepare(manifest):
    manifest = manifest.resolve()
    if not manifest.is_relative_to((ROOT/'local-data/build/sensor-web').resolve()):
        raise ValueError('Expected local web build manifest')
    build = json.loads(manifest.read_text(encoding='utf-8'))
    if (build.get('working_tree_dirty_at_start') is not False or build.get('build_passed') is not True or
            build.get('hardware_tested') is not False or build.get('credentials_present') is not True or
            build.get('private_firmware_do_not_publish') is not True):
        raise ValueError('Expected clean, local-credential software candidate, untested on hardware')
    expected = {'CONFIG_ESP_PHY_CALIBRATION_AND_DATA_STORAGE': {
        'before': 'CONFIG_ESP_PHY_CALIBRATION_AND_DATA_STORAGE=y', 'after': None}}
    if build.get('sdkconfig_differences') != expected:
        raise ValueError('Unexpected SDK configuration difference')
    subprocess.run(['git', 'merge-base', '--is-ancestor', build['source_commit'], 'HEAD'], cwd=ROOT, check=True)
    for name, digest in build['inputs'].items():
        if name == 'PRIVATE_GENERATED/wifi_credentials.h':
            continue
        path = (ROOT/name).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file() or sha(path) != digest:
            raise ValueError('Build input changed: '+name)
    artifacts = {}
    for item in build['artifacts']:
        path = (ROOT/item['path']).resolve()
        if (not path.is_relative_to((ROOT/'local-data/build/sensor-web').resolve()) or
                not path.is_file() or path.stat().st_size != item['bytes'] or sha(path) != item['sha256']):
            raise ValueError('Build artifact mismatch')
        artifacts[path.name] = item
    if artifacts['partitions.bin']['sha256'] != sha(BUNDLE/'artifacts/partitions.bin'):
        raise ValueError('Partition mismatch')
    old = json.loads((ROOT/'docs/reports/SENSOR_RECORDER_BUILD_20261016.json').read_text(encoding='utf-8'))
    recovery = next(item for item in old['artifacts'] if item['path'].endswith('/firmware.bin'))
    if sha(ROOT/recovery['path']) != recovery['sha256']:
        raise ValueError('Preserved recovery firmware mismatch')
    app = artifacts['firmware.bin']
    if app['bytes'] > 0x330000:
        raise ValueError('App exceeds existing app0')
    extent = 0x10000 + ((max(app['bytes'], recovery['bytes'])+4095)//4096)*4096
    return {'hardware_accessed': False, 'authorization_granted': False,
            'candidate_source': build['source_commit'], 'candidate': app, 'input_sha256': build['input_sha256'],
            'manifest_local_path': manifest.relative_to(ROOT).as_posix(),
            'controller_mac_expected': '44:1b:f6:81:91:10', 'card_serial_expected': '10044BA5',
            'card_bytes_expected': 7948206080,
            'flash_write_offset': '0x10000', 'flash_write_scope': 'app0 only; keep current bootloader/partition/NVS',
            'required_prewrite_backup_bytes': extent, 'required_prewrite_backup_hex_end_exclusive': hex(extent),
            'recovery': recovery, 'recovery_source': old['source_commit'],
            'scope_proposed': {'main_record_seconds': 300, 'browser_disconnect_reconnect': True,
                'early_stop_record_approx_seconds': 10, 'duplicate_start_rejected': True,
                'mcu_only_reset': True, 'warm_record_seconds': 10, 'user_confirmed_full_power_offs': 2,
                'cold_record_seconds_each': 10, 'host_reader_export_and_validation': True,
                'preserve_existing_files': True, 'restore_on_failure': True,
                'format_repair_delete': False, 'power_cut_during_write': False,
                'precision_calibration': False, 'main_merge_or_release': False},
            'note': 'Preparation only. Existing recorder hardware batch targets a different candidate; do not reuse it unchanged. '
                    'Confirm hardware placement and explicit trial scope before opening a port. Never upload credential-bearing firmware.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    packet = prepare(args.manifest)
    text = json.dumps(packet, ensure_ascii=False, indent=2)+'\n'
    if args.output:
        output = args.output.resolve()
        if not output.is_relative_to((ROOT/'local-data').resolve()):
            raise ValueError('Packet output must stay under local-data')
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
