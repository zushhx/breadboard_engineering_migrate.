"""Explicitly approved recorder flash/record/reset/cold checks. Never invoked by build or CI."""
import argparse
import datetime
import json
from pathlib import Path
import re
import secrets
import subprocess
import time

from operation_gate import require_approval
from project_check import ROOT, BUNDLE, sha
import tf_write_batch as tf
import sensor_monitor_batch as previous

MANIFEST = ROOT/'docs/reports/SENSOR_RECORDER_BUILD_20261016.json'
SCOPE = dict(sensor_branch_only=True, tf_direct_3v3=True, seconds=300, short_seconds=10,
             mcu_reset=True, cold_starts=2, restore_on_failure=True, card_data_disposable=True)


def inputs():
    old, _, old_files = previous.inputs()
    record = json.loads(MANIFEST.read_text(encoding='utf-8'))
    if (record.get('working_tree_dirty_at_start') is not False or record.get('build_passed') is not True
            or record.get('sdkconfig_differences') != []):
        raise RuntimeError('Recorder must come from clean, configuration-matched build')
    subprocess.run(['git', 'merge-base', '--is-ancestor', record['source_commit'], 'HEAD'], cwd=ROOT, check=True)
    files = [Path(__file__).resolve(), Path(previous.__file__).resolve(), ROOT/'tools/operation_gate.py',
             ROOT/'tools/parse_sensor_record.py', MANIFEST, *old_files]
    candidate = None
    for name, digest in record['inputs'].items():
        path = (ROOT/name).resolve()
        if not path.is_relative_to(ROOT) or sha(path) != digest:
            raise RuntimeError('Recorder build input changed: '+name)
        files.append(path)
    for item in record['artifacts']:
        path = (ROOT/item['path']).resolve()
        if not path.is_relative_to(ROOT) or sha(path) != item['sha256'] or path.stat().st_size != item['bytes']:
            raise RuntimeError('Recorder artifact changed')
        if path.name == 'firmware.bin': candidate = path; files.append(path)
    if candidate is None: raise RuntimeError('No candidate')
    return candidate, old, record, list(dict.fromkeys(files))


def approved(path):
    # Missing approval fails before loading local build artifacts or discovering devices.
    if path is None: raise RuntimeError('Requires --approval-record with genuine candidate confirmation')
    candidate, old, build, files = inputs()
    approval = require_approval(ROOT, path, 'sensor-recorder-round', tf.MAC, files)
    if approval.get('scope') != SCOPE: raise RuntimeError('Recorder scope mismatch')
    return candidate, old, build, approval


def boot_check(session, port, source, cold=False, warm=False):
    session.collect(port, 1)
    text = session.query(port, 'help', 1)
    if f'identity source={source} ' not in text or f'mac_status=ESP_OK mac={tf.MAC}' not in text:
        raise RuntimeError('Recorder source/controller identity mismatch')
    session.metadata(port)
    text = session.query(port, 'l', 3)
    if 'root open=0' not in text or text.count('PC_MATCH=1') != 2 or 'sample reads consistent=1' not in text:
        raise RuntimeError('TF reference regression')
    if cold:
        text = session.query(port, 'm', 1)
        if ('init_attempted=0' not in text or 'IIS ctrl1=00 ctrl3=04 ctrl6=00' not in text or
                'INA transport=1 config=4127 cal=0000' not in text):
            raise RuntimeError('Registers inconsistent with confirmed full power-off; preserve evidence')
    if warm:
        text = session.query(port, 'm', 1)
        if ('init_attempted=0' not in text or 'IIS ctrl1=00 ctrl3=44 ctrl6=00' not in text or
                'INA transport=1 config=4527 cal=0800' not in text):
            raise RuntimeError('Unexpected register loss across MCU-only reset; no reinitialization')


def accepted_reply(text, run, seconds):
    match = re.search(r'\[rec\] PASS run='+run+r' samples=(\d+) errors=0 fifo_high=(\d+) queue_high=(\d+) '
                      r'write_us=(\d+) sync_us=(\d+) sha256=([0-9a-f]{64}) write_gate=0 precision_accepted=0\r?\n', text)
    if not match or 'verify=1 ' not in text: raise RuntimeError('Incomplete/failed recorder terminal reply')
    samples = int(match[1])
    if not seconds*25333 <= samples <= seconds*28001+1024:
        raise RuntimeError('Duration/sample mismatch')
    return dict(run=run, seconds=seconds, samples=samples, sha256=match[6], terminal=match[0].strip())


def capture(session, port, seconds):
    run = f'{secrets.randbits(32):08X}'
    command = f'REC_START {tf.CARD_SERIAL} {tf.CAPACITY} {run} {seconds}'
    session.emit('SEND: '+command); port.write((command+'\n').encode('ascii')); port.flush()
    text = ''; deadline = time.monotonic()+seconds+120
    while time.monotonic() < deadline:
        chunk = port.read(4096).decode('utf-8', errors='replace')
        if chunk: session.emit(chunk.rstrip('\r\n')); text += chunk
        if any(token in text for token in ('[rec] FAIL', '[rec] rejected', 'Guru Meditation', 'rst:', ' error=')):
            raise RuntimeError('Recorder failed/rebooted; no retry')
        if re.search(r'\[rec\] PASS run='+run+r'[^\r\n]*\r?\n', text):
            return accepted_reply(text, run, seconds)
    raise RuntimeError('Recorder timeout; no pass')


def verify(session, port, item):
    command = 'REC_VERIFY '+item['run']
    session.emit('SEND: '+command); port.write((command+'\n').encode('ascii')); port.flush()
    text = ''; deadline = time.monotonic()+120
    while time.monotonic() < deadline:
        chunk = port.read(4096).decode('utf-8', errors='replace')
        if chunk: session.emit(chunk.rstrip('\r\n')); text += chunk
        if any(t in text for t in ('VERIFY_FAIL', 'Guru Meditation', 'rst:')): raise RuntimeError('Readback failed')
        if f'VERIFY_PASS run={item["run"]} write_gate=0' in text:
            if 'verify=1 ' not in text or 'sha256='+item['sha256'] not in text: raise RuntimeError('Readback digest mismatch')
            return
    raise RuntimeError('Readback timeout')


def restore(session, snapshot, source):
    path = session.folder/'restore_app_range.bin'; path.write_bytes(snapshot.read_bytes()[0x10000:])
    session.esptool(tf.unique_port(), ['flash_id'])  # Identity check before write, too.
    session.esptool(tf.unique_port(), ['write_flash', '--flash_mode', 'keep', '--flash_freq', 'keep',
                                     '--flash_size', 'keep', '0x10000', str(path)])
    with session.serial() as port:
        session.collect(port, 8); boot_check(session, port, source)


def initial(approval_path):
    candidate, old, build, approval = approved(approval_path)
    stamp = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y%m%d_%H%M%S')
    session = tf.Session(ROOT/'local-data/hardware/sensor-recorder'/stamp)
    result = dict(passed=False, source_commit=build['source_commit'], operation_commit=approval['source_commit'],
                  candidate_sha256=sha(candidate), cold_starts_completed=0)
    extent = 0x10000+((max(candidate.stat().st_size, old.stat().st_size)+4095)//4096)*4096
    snapshot = session.folder/'controller_prefix.bin'; changed = False
    try:
        port_name = tf.unique_port()
        identity = session.esptool(port_name, ['flash_id'])
        if 'Detected flash size: 8MB' not in identity: raise RuntimeError('Flash size mismatch')
        session.esptool(port_name, ['read_flash', '0', hex(extent), str(snapshot)])
        data = snapshot.read_bytes(); boot = (BUNDLE/'artifacts/bootloader.bin').read_bytes()
        partitions = (BUNDLE/'artifacts/partitions.bin').read_bytes(); app = old.read_bytes()
        if (len(data) != extent or data[:len(boot)] != boot or data[0x8000:0x8000+len(partitions)] != partitions or
                data[0x10000:0x10000+len(app)] != app): raise RuntimeError('Current baseline differs; no write')
        result['snapshot_sha256'] = sha(snapshot); result['snapshot_bytes'] = extent
        changed = True
        session.esptool(port_name, ['write_flash', '--flash_mode', 'keep', '--flash_freq', 'keep',
                                   '--flash_size', 'keep', '0x10000', str(candidate)])
        with session.serial() as port:
            session.collect(port, 8); boot_check(session, port, build['source_commit'])
            result['main_record'] = capture(session, port, SCOPE['seconds'])
            session.emit('MCU EN reset, card/modules remain powered; not a cold start')
            port.rts = True; time.sleep(.1); port.rts = False; session.collect(port, 8)
            boot_check(session, port, build['source_commit'], warm=True)
            verify(session, port, result['main_record'])
            result['after_reset_record'] = capture(session, port, SCOPE['short_seconds'])
        result['passed'] = True
    except Exception as exc:
        result['error'] = repr(exc); session.emit('FAIL: '+repr(exc))
        if changed:
            try: restore(session, snapshot, previous.SOURCE_COMMIT); result['restored_previous'] = True
            except Exception as recovery: result['recovery_error'] = repr(recovery)
        raise
    finally:
        (session.folder/'result.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
        session.emit('RESULT='+str(session.folder/'result.json')); session.log.close()


def cold(approval_path, folder, number, confirmation):
    _, _, build, approval = approved(approval_path)
    allowed = (ROOT/'local-data/hardware/sensor-recorder').resolve(); folder = folder.resolve()
    if not folder.is_relative_to(allowed) or folder == allowed or number not in (1, 2) or not confirmation.strip():
        raise RuntimeError('Requires genuine numbered full-power-off confirmation/evidence folder')
    path = folder/'result.json'; result = json.loads(path.read_text(encoding='utf-8'))
    snapshot = folder/'controller_prefix.bin'
    if (result.get('passed') is not True or result.get('operation_commit') != approval['source_commit'] or
            result.get('source_commit') != build['source_commit'] or result.get('cold_starts_completed') != number-1 or
            snapshot.stat().st_size != result.get('snapshot_bytes') or sha(snapshot) != result.get('snapshot_sha256')):
        raise RuntimeError('Evidence/sequence mismatch')
    session = tf.Session(folder/f'cold-{number}'); outcome = dict(passed=False, user_confirmation=confirmation)
    try:
        with session.serial() as port:
            boot_check(session, port, build['source_commit'], cold=True)
            verify(session, port, result['main_record'])
            outcome['record'] = capture(session, port, SCOPE['short_seconds'])
        outcome['passed'] = True; result['cold_starts_completed'] = number
        result['device_round_passed'] = number == 2
        result['host_real_file_parse_passed'] = False  # Separate physical reader export remains required.
    except Exception as exc:
        outcome['error'] = repr(exc); result['passed'] = False
        try: restore(session, snapshot, previous.SOURCE_COMMIT); outcome['restored_previous'] = True
        except Exception as recovery: outcome['recovery_error'] = repr(recovery)
        raise
    finally:
        (session.folder/'result.json').write_text(json.dumps(outcome, indent=2)+'\n', encoding='utf-8')
        path.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8'); session.log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--approval-record', type=Path)
    parser.add_argument('--cold-folder', type=Path)
    parser.add_argument('--cold-number', type=int)
    parser.add_argument('--cold-confirmation', default='')
    args = parser.parse_args()
    if args.prepare_only:
        candidate, old, build, files = inputs()
        print(json.dumps(dict(source=build['source_commit'], candidate=str(candidate.relative_to(ROOT)),
            sha256=sha(candidate), previous=str(old.relative_to(ROOT)), scope=SCOPE, hardware_accessed=False,
            files={p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else p.as_posix(): sha(p) for p in files}), indent=2))
    elif args.cold_folder:
        cold(args.approval_record, args.cold_folder, args.cold_number, args.cold_confirmation)
    else:
        if args.cold_number is not None or args.cold_confirmation: parser.error('Cold options require --cold-folder')
        initial(args.approval_record)


if __name__ == '__main__': main()
