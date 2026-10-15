"""Explicitly gated module bringup; build/CI never invokes this hardware entry."""
from pathlib import Path
import argparse
import datetime
import json
import re
import secrets
import subprocess
import time

from operation_gate import require_approval
from project_check import ROOT, BUNDLE, sha
import tf_write_batch as tf

MANIFEST = ROOT / 'docs/reports/SENSOR_MONITOR_BUILD_20261015.json'
CANDIDATE_SHA = '7aba58e53173011d88a42d7ce11e409134c49c235fa8ffa7adb0535a7bdddcb5'
SOURCE_COMMIT = '0965b47fb6de8d95240c7c702bbe1af68a345f7e'
SCOPE = {'sensor_branch_only': True, 'tf_direct_3v3': True, 'hold_seconds': 60,
         'new_files': 1, 'mcu_reset': True, 'cold_starts': 2,
         'restore_on_failure': True, 'card_data_disposable': True}


def inputs():
    previous, old_files = tf.inputs()  # Read/validate only; no serial access.
    record = json.loads(MANIFEST.read_text(encoding='utf-8'))
    if (record.get('source_commit') != SOURCE_COMMIT or record.get('working_tree_dirty_at_start') is not False
            or record.get('sdkconfig_differences') != [] or record.get('build_passed') is not True):
        raise RuntimeError('Unexpected module build record')
    subprocess.run(['git', 'merge-base', '--is-ancestor', SOURCE_COMMIT, 'HEAD'], cwd=ROOT, check=True)
    files = [Path(__file__).resolve(), Path(tf.__file__).resolve(),
             ROOT / 'tools/operation_gate.py', ROOT / 'tools/project_check.py', MANIFEST, *old_files]
    for name, digest in record['inputs'].items():
        path = (ROOT / name).resolve()
        if not path.is_relative_to(ROOT) or sha(path) != digest:
            raise RuntimeError('Module input changed: ' + name)
        files.append(path)
    candidate = None
    for item in record['artifacts']:
        path = (ROOT / item['path']).resolve()
        if not path.is_relative_to(ROOT) or sha(path) != item['sha256'] or path.stat().st_size != item['bytes']:
            raise RuntimeError('Module artifact changed')
        if path.name == 'firmware.bin':
            candidate = path
            files.append(path)
    if candidate is None or sha(candidate) != CANDIDATE_SHA:
        raise RuntimeError('Unexpected module candidate')
    files.append(previous)
    return candidate, previous, list(dict.fromkeys(files))


def approved(path):
    candidate, previous, files = inputs()
    record = require_approval(ROOT, path, 'sensor-monitor-bringup', tf.MAC, files)
    if record.get('scope') != SCOPE:
        raise RuntimeError('Module bringup scope mismatch')
    return candidate, previous, record


def snapshot_valid(text):
    # Require a complete terminal reply, explicit identity/config validity and no reset/error.
    if any(token in text for token in ['Guru Meditation', 'rst:', ' error=', 'INIT failed']):
        return False
    identity = re.search(r'INA identity address=4[0-9A-F] transport=1 maker=5449 die=226[01] valid=1\r?\n', text)
    config = re.search(r'INA transport=1 config=4527 cal=0800 mask=([0-9A-F]{4}) data_valid=1\r?\n', text)
    mask = int(config.group(1), 16) if config else 0
    return (identity is not None and config is not None and mask & 8 != 0 and mask & 4 == 0
            and 'IIS identity transport=1 who=7B expected=7B valid=1' in text
            and bool(re.search(r'\[modules\] snapshot_end iis_valid=1 ina_valid=1\r?\n', text)))


def module_check(session, port, initialize=False):
    if initialize:
        text = session.query(port, 'INIT_MODULES', 1)
        if 'INIT writes_completed=1' not in text or not snapshot_valid(text):
            raise RuntimeError('Module initialization/readback did not pass')
        return text
    text = session.query(port, 'm', 1)
    if not snapshot_valid(text):
        raise RuntimeError('Module read-only snapshot invalid; no automatic repair')
    return text


def boot_check(session, port):
    text = session.collect(port, 10)
    banner = f'diagnostic=sensor_monitor_v1 source={SOURCE_COMMIT}'
    if banner not in text or 'Guru Meditation' in text:
        raise RuntimeError('Candidate boot identity missing or panic; no automatic reset retry')
    session.metadata(port)
    listing = session.query(port, 'l', 3)
    if 'root open=0' not in listing or listing.count('PC_MATCH=1') != 2 or 'sample reads consistent=1' not in listing:
        raise RuntimeError('TF directory/reference file regression')


def restore(session, snapshot):
    # Restore only the backed-up app range; never touch NVS, OTA or bootloader.
    recovery = session.folder / 'restore_app_range.bin'
    recovery.write_bytes(snapshot.read_bytes()[0x10000:])
    # Check MAC before a recovery write too; a post-write identity check is too late.
    session.esptool(tf.unique_port(), ['flash_id'])
    session.esptool(tf.unique_port(), ['write_flash', '--flash_mode', 'keep', '--flash_freq', 'keep',
                                     '--flash_size', 'keep', '0x10000', str(recovery)])
    with session.serial() as port:
        text = session.collect(port, 8)
        if 'diagnostic=v1 source=31dda6f23cab21f9a07330a829bae747211334b0' not in text:
            raise RuntimeError('Recovery identity missing')
        session.metadata(port)
        text = session.query(port, 'l', 3)
        if text.count('PC_MATCH=1') != 2 or 'root open=0' not in text:
            raise RuntimeError('Recovery TF read check failed')


def run_initial(approval):
    candidate, previous, record = approved(approval)  # Before Session or device discovery.
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    session = tf.Session(ROOT / 'local-data/hardware/sensor-monitor' / stamp)
    result = {'source_commit': SOURCE_COMMIT, 'operation_commit': record['source_commit'],
              'candidate_sha256': CANDIDATE_SHA, 'passed': False, 'cold_starts_completed': 0}
    changed_flash = False
    snapshot = session.folder / 'controller_prefix_0x70000.bin'
    try:
        port_name = tf.unique_port()
        identity = session.esptool(port_name, ['flash_id'])
        if 'Detected flash size: 8MB' not in identity:
            raise RuntimeError('Unexpected flash size')
        session.esptool(port_name, ['read_flash', '0', '0x70000', str(snapshot)])
        data = snapshot.read_bytes()
        boot = (BUNDLE / 'artifacts/bootloader.bin').read_bytes()
        partitions = (BUNDLE / 'artifacts/partitions.bin').read_bytes()
        old = previous.read_bytes()
        if (len(data) != 0x70000 or data[:len(boot)] != boot or data[0x8000:0x8000 + len(partitions)] != partitions
                or data[0x10000:0x10000 + len(old)] != old):
            raise RuntimeError('Current TF app/boot/partition baseline differs; no flash write')
        if 0x10000 + ((candidate.stat().st_size + 4095) // 4096) * 4096 > len(data):
            raise RuntimeError('Snapshot does not cover candidate write range')
        result['snapshot_sha256'] = sha(snapshot)
        changed_flash = True
        session.esptool(port_name, ['write_flash', '--flash_mode', 'keep', '--flash_freq', 'keep',
                                   '--flash_size', 'keep', '0x10000', str(candidate)])
        with session.serial() as port:
            boot_check(session, port)
            module_check(session, port, initialize=True)
            start = time.monotonic()
            count = 0
            while time.monotonic() - start < 60:
                module_check(session, port)
                count += 1
            result['hold_snapshots'] = count
            result['hold_elapsed_seconds'] = time.monotonic() - start
            run = f'{secrets.randbits(32):08X}'
            result['run'] = run
            result['write'] = session.test(port, run, True)
            module_check(session, port)
            session.emit('MCU EN RESET; modules and TF remain powered; NOT a cold start')
            port.rts = True; time.sleep(0.1); port.rts = False
            boot_check(session, port)
            module_check(session, port)  # No INIT after MCU reset: observe preserved register state.
            result['after_mcu_reset'] = session.test(port, run, False)
        result['passed'] = True
        session.emit('INITIAL PASS; two genuine full-power-off checks still pending')
    except Exception as error:
        result['error'] = repr(error)
        session.emit('FAIL: ' + repr(error))
        if changed_flash:
            try:
                restore(session, snapshot)
                result['restored_previous_tf_candidate'] = True
            except Exception as recovery_error:
                result['recovery_error'] = repr(recovery_error)
        raise
    finally:
        (session.folder / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        session.emit('RESULT=' + str(session.folder / 'result.json'))
        session.log.close()


def run_cold(approval, folder, number, confirmation):
    _, _, record = approved(approval)
    if number not in (1, 2) or not confirmation or not confirmation.strip():
        raise RuntimeError('Requires numbered genuine user power-off confirmation')
    folder = folder.resolve()
    allowed = (ROOT / 'local-data/hardware/sensor-monitor').resolve()
    if not folder.is_relative_to(allowed) or folder == allowed:
        raise RuntimeError('Unexpected initial evidence folder')
    result_path = folder / 'result.json'
    result = json.loads(result_path.read_text(encoding='utf-8'))
    snapshot = folder / 'controller_prefix_0x70000.bin'
    run = result.get('run', '')
    if (result.get('passed') is not True or result.get('candidate_sha256') != CANDIDATE_SHA
            or result.get('operation_commit') != record['source_commit']
            or result.get('cold_starts_completed') != number - 1 or not re.fullmatch('[0-9A-F]{8}', run)
            or snapshot.stat().st_size != 0x70000 or sha(snapshot) != result.get('snapshot_sha256')):
        raise RuntimeError('Initial evidence or cold-start sequence invalid')
    session = tf.Session(folder / f'cold-start-{number}')
    cold = {'number': number, 'user_power_off_confirmation': confirmation, 'passed': False}
    try:
        with session.serial() as port:
            # Power-on banner may have elapsed while the user answered; reset is not needed.
            session.collect(port, 1)
            identity = session.query(port, 'help', 1)
            if (f'identity source={SOURCE_COMMIT} ' not in identity
                    or f'mac_status=ESP_OK mac={tf.MAC}' not in identity):
                raise RuntimeError('Module source/controller identity absent')
            session.metadata(port)
            text = session.query(port, 'l', 3)
            if 'root open=0' not in text or text.count('PC_MATCH=1') != 2 or 'sample reads consistent=1' not in text:
                raise RuntimeError('Cold-start TF reference read failed')
            module_check(session, port, initialize=True)  # One init per genuine power cycle.
            module_check(session, port)
            cold['verify'] = session.test(port, run, False)
        cold['passed'] = True
        result['cold_starts_completed'] = number
        result['entire_round_passed'] = number == 2
    except Exception as error:
        cold['error'] = repr(error)
        result['passed'] = False
        try:
            restore(session, snapshot)
            cold['restored_previous_tf_candidate'] = True
        except Exception as recovery_error:
            cold['recovery_error'] = repr(recovery_error)
        raise
    finally:
        (session.folder / 'result.json').write_text(json.dumps(cold, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        session.log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--approval-record', type=Path)
    parser.add_argument('--cold-start-folder', type=Path)
    parser.add_argument('--cold-start-number', type=int)
    parser.add_argument('--cold-start-confirmation')
    args = parser.parse_args()
    if args.prepare_only:
        candidate, previous, files = inputs()
        print(json.dumps({'candidate': candidate.relative_to(ROOT).as_posix(), 'sha256': sha(candidate),
                          'previous': previous.relative_to(ROOT).as_posix(), 'scope': SCOPE,
                          'files': {p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else p.as_posix(): sha(p) for p in files},
                          'hardware_accessed': False}, ensure_ascii=False, indent=2))
        return
    if args.cold_start_folder:
        run_cold(args.approval_record, args.cold_start_folder, args.cold_start_number, args.cold_start_confirmation)
    else:
        if args.cold_start_number is not None or args.cold_start_confirmation:
            parser.error('Cold-start options require --cold-start-folder')
        run_initial(args.approval_record)


if __name__ == '__main__':
    main()
