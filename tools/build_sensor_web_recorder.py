"""Build the independent WiFi recorder; never open a serial port or write devices.

Credentials only enter an ignored local config and private staging/artifacts. Default
CI builds have WiFi disabled. Firmware files contain credentials; do not publish them.
"""
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
from build_sensor_tf_recorder import config_values

SOURCE = ROOT / '面包板样机_固件/diagnostics/sensor_web_recorder'
ENVIRONMENT = 'sensor_web_recorder_idf542'
EXPECTED_SDKCONFIG_DIFF = {name: {'before': name+'=y', 'after': None} for name in (
    'CONFIG_ESP_PHY_CALIBRATION_AND_DATA_STORAGE',
    'CONFIG_ESP32_PHY_CALIBRATION_AND_DATA_STORAGE',
    'CONFIG_ESP_WIFI_STA_DISCONNECTED_PM_ENABLE')}
EXPECTED_SDKCONFIG_DIFF.update({
    'CONFIG_ESP_CONSOLE_SECONDARY_NONE': {'before':None,'after':'CONFIG_ESP_CONSOLE_SECONDARY_NONE=y'},
    'CONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG': {'before':'CONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG=y','after':None},
    'CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG_ENABLED': {'before':'CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG_ENABLED=y','after':None},
    'CONFIG_HTTPD_MAX_URI_LEN': {'before':'CONFIG_HTTPD_MAX_URI_LEN=512','after':'CONFIG_HTTPD_MAX_URI_LEN=1024'},
    'CONFIG_HTTPD_WS_SUPPORT': {'before':None,'after':'CONFIG_HTTPD_WS_SUPPORT=y'},
    'CONFIG_LWIP_MAX_SOCKETS': {'before':'CONFIG_LWIP_MAX_SOCKETS=10','after':'CONFIG_LWIP_MAX_SOCKETS=12'},
    'CONFIG_ESP_SYSTEM_PANIC_PRINT_REBOOT': {'before':'CONFIG_ESP_SYSTEM_PANIC_PRINT_REBOOT=y','after':None},
    'CONFIG_ESP_SYSTEM_PANIC_GDBSTUB': {'before':None,'after':'CONFIG_ESP_SYSTEM_PANIC_GDBSTUB=y'},
    'CONFIG_ESP_SYSTEM_PANIC_REBOOT_DELAY_SECONDS': {'before':'CONFIG_ESP_SYSTEM_PANIC_REBOOT_DELAY_SECONDS=0','after':None},
})


def credentials(path):
    if path is None:
        return '', ''
    path = path.resolve()
    if not path.is_relative_to((ROOT / 'local-data').resolve()):
        raise ValueError('Credentials must be stored under ignored local-data/')
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    ssid, password = data.get('ssid'), data.get('password')
    if not isinstance(ssid, str) or not isinstance(password, str):
        raise ValueError('Expected string ssid/password')
    if not 1 <= len(ssid.encode('utf-8')) <= 32 or not 8 <= len(password.encode('utf-8')) <= 63:
        raise ValueError('SSID/password length invalid')
    if any(ord(c) < 32 or ord(c) == 127 for c in ssid + password):
        raise ValueError('Control characters forbidden')
    return ssid, password


def credential_header(ssid, password):
    # Emit UTF-8 bytes through independent octal escapes; no C preprocessor injection.
    def literal(value):
        return '"' + ''.join('\\%03o' % b for b in value.encode('utf-8')) + '"'
    return '#pragma once\n#define WIFI_SSID ' + literal(ssid) + '\n#define WIFI_PASSWORD ' + literal(password) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--wifi-config', type=Path)
    args = parser.parse_args()
    try:
        ssid, password = credentials(args.wifi_config)
    except (OSError, ValueError):
        raise SystemExit('Invalid/missing local WiFi configuration (values withheld)')
    errors = baseline_errors()
    if errors:
        raise SystemExit('\n'.join(errors))
    if subprocess.check_output([sys.executable, '-m', 'platformio', '--version'], text=True).strip() != 'PlatformIO Core, version 6.2.0':
        raise SystemExit('Expected PlatformIO Core 6.2.0')
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT))
    inputs = sorted(path for path in SOURCE.rglob('*') if path.is_file() and
                    not any(part in {'.pio', '__pycache__'} for part in path.relative_to(SOURCE).parts))
    if any(p.name == 'wifi_credentials.h' for p in inputs):
        raise SystemExit('Credentials header must never exist in the source tree')
    input_record = {path.relative_to(ROOT).as_posix(): sha(path) for path in inputs}
    for script in [Path(__file__), ROOT/'tools/build_sensor_tf_recorder.py', ROOT/'tools/project_check.py']:
        input_record[script.resolve().relative_to(ROOT).as_posix()] = sha(script)
    secret_header = credential_header(ssid, password)
    input_record['PRIVATE_GENERATED/wifi_credentials.h'] = hashlib.sha256(secret_header.encode()).hexdigest()
    input_hash = hashlib.sha256(json.dumps(input_record, sort_keys=True).encode()).hexdigest()
    stage = (Path.home()/'.platformio/breadboard_web_builds'/input_hash[:16]
             if os.name == 'nt' else ROOT/'local-data/build/sensor-web'/input_hash[:16]/'project')
    stage.mkdir(parents=True, exist_ok=True)
    for source in inputs:
        target = stage/source.relative_to(SOURCE)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    (stage/'src/wifi_credentials.h').write_text(secret_header, encoding='utf-8', newline='\n')
    if sha(stage/'src/wifi_credentials.h') != input_record['PRIVATE_GENERATED/wifi_credentials.h']:
        raise SystemExit('Generated credentials bytes differ from the bound input hash')
    page = (SOURCE/'src/index.html').read_text(encoding='utf-8')
    page = page.replace('/*CONTROLLER_V2*/', '\n'.join((SOURCE/'src'/name).read_text(encoding='utf-8') for name in ('sha256.js', 'controller_v2.js')) + '\nv2Bootstrap();')
    if ')I3PAGE"' in page or page.count('{{KEY}}') != 1:
        raise SystemExit('Invalid page delimiter/session placeholder')
    (stage/'src/web_asset.h').write_text('#pragma once\nstatic constexpr char webPage[] = R"I3PAGE(' + page + ')I3PAGE";\n', encoding='utf-8', newline='\n')
    (stage/'src/build_identity.h').write_text(
        '#pragma once\n#define TF_SOURCE_COMMIT ' + json.dumps(head) +
        '\n#define TF_INPUT_SHA256 ' + json.dumps(input_hash) + '\n', encoding='utf-8', newline='\n')
    print('SOURCE_COMMIT=' + head, flush=True)
    print('WORKING_TREE_DIRTY=' + str(dirty), flush=True)
    print('INPUT_SHA256=' + input_hash, flush=True)
    print('WIFI_CREDENTIALS_PRESENT=' + str(bool(ssid)), flush=True)
    print('STAGING=' + str(stage), flush=True)
    if args.prepare_only:
        print('Prepared only; no compiler or device access', flush=True)
        return
    stamp = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y%m%d_%H%M%S_%f')
    output = ROOT/'local-data/build/sensor-web'/input_hash[:16]/stamp
    output.mkdir(parents=True, exist_ok=False)
    # Bound host compiler concurrency; this does not change target flags or firmware configuration.
    command = [sys.executable, '-m', 'platformio', 'run', '--jobs', '2', '--project-dir', str(stage), '--environment', ENVIRONMENT]
    with (output/'build.log').open('x', encoding='utf-8') as log:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding='utf-8', errors='replace')
        for line in process.stdout:
            print(line, end='', flush=True); log.write(line)
        status = process.wait()
    if status:
        raise SystemExit(status)
    actual_config = stage/('sdkconfig.' + ENVIRONMENT)
    before = config_values(ROOT/'面包板样机_固件/diagnostics/sensor_tf_recorder/sdkconfig.sensor_recorder_idf542')
    after = config_values(actual_config)
    changed = {key: {'before': before.get(key), 'after': after.get(key)}
               for key in sorted(before.keys() | after.keys()) if before.get(key) != after.get(key)}
    # PHY also persists calibration independently of wifi_init_config_t.nvs_enable.
    # esp_phy/sdkconfig.rename maps the ESP32_ legacy name to this same option.
    if changed != EXPECTED_SDKCONFIG_DIFF:
        (output/'unexpected_sdkconfig_diff.json').write_text(json.dumps(changed, indent=2)+'\n', encoding='utf-8')
        print('ACTUAL_SDKCONFIG_DIFF=' + json.dumps(changed, sort_keys=True), flush=True)
        raise SystemExit('Unexpected SDK configuration differences; inspect private diff before proceeding')
    artifacts = []
    for name in ['firmware.bin', 'bootloader.bin', 'partitions.bin', 'firmware.elf']:
        source = stage/'.pio/build'/ENVIRONMENT/name
        target = output/'artifacts'/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        artifacts.append({'path': target.relative_to(ROOT).as_posix(), 'bytes': target.stat().st_size, 'sha256': sha(target)})
    shutil.copy2(actual_config, output/'sdkconfig.generated')
    if sha(output/'artifacts/partitions.bin') != sha(BUNDLE/'artifacts/partitions.bin'):
        raise SystemExit('Candidate partition table differs from frozen baseline')
    record = {'source_commit': head, 'working_tree_dirty_at_start': dirty, 'input_sha256': input_hash,
              'staged_generated_sha256': {name: sha(stage/'src'/name) for name in
                  ['wifi_credentials.h', 'web_asset.h', 'build_identity.h']},
              'inputs': input_record, 'command': command, 'sdkconfig_differences': changed,
              'sdkconfig_generated_sha256': sha(actual_config), 'artifacts': artifacts,
              'build_passed': True, 'hardware_tested': False, 'credentials_present': bool(ssid),
              'private_firmware_do_not_publish': bool(ssid),
              'note': 'WiFi recorder candidate only; no serial, flash, card, mobile hotspot or network acceptance'}
    manifest = output/'build_manifest.json'
    manifest.write_text(json.dumps(record, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print('BUILD_MANIFEST=' + str(manifest), flush=True)


if __name__ == '__main__':
    main()
