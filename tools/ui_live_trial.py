"""One approved UI flash + 30s real recording. Never called by build or CI."""
import argparse
import datetime
from http.client import RemoteDisconnected
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from operation_gate import require_approval
from prepare_sensor_web_trial import prepare
from project_check import ROOT, BUNDLE, sha
import tf_write_batch as tf
import sensor_recorder_batch as base
import sensor_web_batch as web

SOURCE = 'eb4aba6340ba26093cd8f1849714b8f335bf4792'
CANDIDATE = '400192859ee7e4ac3723120397af1e804bec8c0baea5545bac07b46f8c65dc16'
OLD_SOURCE = '1f6917870705b29d8697e20ad1f72c09ca4baf3a'
OLD_SHA = 'fd9dd5d241301f68b56664c3fcf02cc8fc143a2bfcde5f57172570bb028aac54'
MANIFEST = ROOT/'local-data/build/sensor-web/2bdecf2aaa77fc65/20261016_210308_503769/build_manifest.json'
OLD_PUBLIC = ROOT/'docs/reports/UI_RAW_WINDOW_STORAGE_EVIDENCE_20261016.json'
BROWSER = ROOT/'tools/run_ui_live_browser.cjs'
OPERATION = 'ui-live-30s'
SCOPE = dict(seconds=30, app0_only=True, backup_end=0x102000, restore_before_flash_on_failure=True,
             preserve_existing_files=True, format_delete_repair=False, cold_starts=0,
             main_merge_release=False)


class TrialSession(tf.Session):
    # Reduce only this trial's USB transfer rate after a corrupted prewrite read.
    esptool_baud = 460800
    def emit(self, value):
        # Preserve evidence before best-effort terminal output. A Windows console
        # encoding failure must never abort the hardware recovery path.
        line = str(value)
        self.log.write(line+'\n'); self.log.flush()
        try:
            print(line, flush=True)
        except (UnicodeError, OSError):
            pass


def inputs():
    packet = prepare(MANIFEST)
    if packet['candidate_source'] != SOURCE or packet['candidate']['sha256'] != CANDIDATE:
        raise RuntimeError('UI candidate mismatch')
    public = json.loads(OLD_PUBLIC.read_text(encoding='utf-8'))
    old_manifest = ROOT/public['build_manifest_local']
    old_build = json.loads(old_manifest.read_text(encoding='utf-8'))
    old = ROOT/next(a['path'] for a in old_build['artifacts'] if a['path'].endswith('/firmware.bin'))
    candidate = ROOT/packet['candidate']['path']
    if old_build['source_commit'] != OLD_SOURCE or sha(old) != OLD_SHA:
        raise RuntimeError('Retained before-flash image mismatch')
    if packet['required_prewrite_backup_bytes'] != SCOPE['backup_end']:
        raise RuntimeError('Backup extent mismatch')
    files = [Path(__file__), BROWSER, MANIFEST, OLD_PUBLIC, old_manifest, old, candidate, tf.ESPTOOL,
             web.NODE, BUNDLE/'artifacts/bootloader.bin', BUNDLE/'artifacts/partitions.bin']
    files += [ROOT/'tools'/name for name in ('operation_gate.py', 'prepare_sensor_web_trial.py',
        'project_check.py', 'build_sensor_web_recorder.py', 'build_sensor_tf_recorder.py',
        'tf_write_batch.py', 'sensor_recorder_batch.py', 'sensor_monitor_batch.py',
        'sensor_web_batch.py', 'parse_sensor_record.py')]
    return candidate, old, list(dict.fromkeys(p.resolve() for p in files))


def approved(path):
    if path is None:
        raise RuntimeError('Requires explicit approval before loading inputs or hardware discovery')
    candidate, old, files = inputs()
    record = require_approval(ROOT, path, OPERATION, tf.MAC, files)
    if record.get('scope') != SCOPE or record.get('hardware_ready_confirmed') is not True:
        raise RuntimeError('UI trial scope/readiness mismatch')
    return candidate, old, record


def check_backup(data, extent, boot, parts, old):
    if (len(data) != extent or extent < 0x10000 + len(old) or
            data[:len(boot)] != boot or data[0x8000:0x8000+len(parts)] != parts or
            data[0x10000:0x10000+len(old)] != old):
        raise RuntimeError('Current boot/partition/app or backup coverage mismatch; no write')


def check_quiescent(before):
    # Preserve the user's completed record; no preflight reset is needed to make it idle.
    if (before.get('phase') not in ('idle', 'passed') or before.get('errors') != 0 or
            not before.get('connected') or not before.get('ready')):
        raise RuntimeError('Current device busy, failed or not ready; no write')


def startup_gate(session, port, url, initial):
    # A peer may close a socket (the firmware has three HTTP slots and LRU purge).
    # Retry only this observed transport exception. Wrong boot/state, panic/reset,
    # HTTP errors and repeated disconnects still fail and restore the candidate.
    for attempt in range(3):
        try:
            return web.read_only_gate(session, port, url, initial, 'ui-startup', seconds=3)
        except RemoteDisconnected as error:
            session.emit(f'STARTUP_HTTP_DISCONNECT attempt={attempt+1} error={error!r}')
            if attempt == 2:
                raise
            time.sleep(.5)


def browser(session, port, url, approval):
    folder = session.folder/'browser'; folder.mkdir()
    env = dict(os.environ, NODE_PATH=str(web.NODE_MODULES), BREADBOARD_APPROVAL_PATH=str(approval.resolve()),
               BREADBOARD_PYTHON=sys.executable)
    text = ''; deadline = time.monotonic()+180
    with (folder/'console.log').open('x', encoding='utf-8') as log:
        process = subprocess.Popen([str(web.NODE), str(BROWSER), url, str(folder)], cwd=ROOT,
                                   env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            while process.poll() is None:
                chunk = port.read(4096).decode('utf-8', errors='replace')
                if chunk: session.emit(chunk.rstrip()); text += chunk
                if any(t in text for t in ('assert failed:', 'Guru Meditation', '[rec] FAIL', 'rst:')):
                    raise RuntimeError('Recording panic/failure/reset; stop trial')
                if time.monotonic() > deadline:
                    raise RuntimeError('30s browser trial timed out')
            text += session.collect(port, 1)
            if process.returncode:
                raise RuntimeError('Browser functional check failed; see browser/console.log')
        finally:
            if process.poll() is None:
                process.terminate(); process.wait(timeout=10)
    result = json.loads((folder/'result.json').read_text(encoding='utf-8'))
    accepted = base.accepted_reply(text, result['run'], result['final']['elapsed_us']/1e6)
    if accepted['samples'] != result['final']['written'] or accepted['sha256'] != result['final']['sha256']:
        raise RuntimeError('HTTP/serial completed-file totals mismatch')
    return result, accepted


def run(path):
    candidate, old, approval = approved(path)
    stamp = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y%m%d_%H%M%S')
    session = TrialSession(ROOT/'local-data/hardware/ui-live'/stamp)
    result = dict(passed=False, candidate_source=SOURCE, candidate_sha256=CANDIDATE,
                  operation_commit=approval['source_commit'], cold_starts=0,
                  five_minute_tested=False, accuracy_accepted=False)
    snapshot = session.folder/'controller_prefix.bin'; changed = False; recording_possible = False
    try:
        # Opening serial explicitly leaves DTR/RTS deasserted; metadata/help/l only read.
        with session.serial() as port:
            base.boot_check(session, port, OLD_SOURCE)
            initial_url, before = web.address(session, port, 'http://172.28.247.119/')
            check_quiescent(before)
            result['before'] = before
        identity = session.esptool(tf.unique_port(), ['flash_id'])
        if 'Detected flash size: 8MB' not in identity:
            raise RuntimeError('Unexpected flash size')
        extent = SCOPE['backup_end']
        session.esptool(tf.unique_port(), ['read_flash', '0', hex(extent), str(snapshot)])
        check_backup(snapshot.read_bytes(), extent, (BUNDLE/'artifacts/bootloader.bin').read_bytes(),
                     (BUNDLE/'artifacts/partitions.bin').read_bytes(), old.read_bytes())
        result['backup'] = dict(bytes=extent, sha256=sha(snapshot), matched_old_source=OLD_SOURCE,
                                path=snapshot.relative_to(ROOT).as_posix())
        (session.folder/'backup.json').write_text(json.dumps(result['backup'], indent=2), encoding='utf-8')
        changed = True  # A partial write must also enter the recovery path.
        written = session.esptool(tf.unique_port(), ['write_flash', '--flash_mode', 'keep', '--flash_freq', 'keep',
            '--flash_size', 'keep', '0x10000', str(candidate)])
        if 'Hash of data verified' not in written:
            raise RuntimeError('Flash write verification missing')
        with session.serial() as port:
            web.collect_startup(session, port, 8); base.boot_check(session, port, SOURCE)
            url, initial = web.address(session, port, initial_url)
            web.check_idle(initial, initial['boot'])
            result['url'] = url; result['initial'] = initial
            startup_gate(session, port, url, initial)
            recording_possible = True
            result['browser'], result['record'] = browser(session, port, url, path)
            # The recording already closes and fully reads back its file; preserve its terminal evidence.
        result['passed'] = True
        session.emit('UI_LIVE_PASS='+json.dumps(dict(url=result['url'],run=result['record']['run'])))
    except Exception as error:
        result['error'] = repr(error); session.emit('UI_LIVE_FAIL='+repr(error))
        if changed:
            if recording_possible:
                web.stop_before_restore(session)
            try:
                if sha(snapshot) != result['backup']['sha256']:
                    raise RuntimeError('Backup changed; do not restore')
                base.restore(session, snapshot, OLD_SOURCE)
                result['restored_before_flash'] = True
            except Exception as recovery:
                result['recovery_error'] = repr(recovery)
        raise
    finally:
        (session.folder/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        session.emit('RESULT='+str(session.folder/'result.json')); session.log.close()


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--validate-approval', action='store_true')
    parser.add_argument('--approval-record', type=Path)
    args = parser.parse_args()
    if args.prepare_only:
        candidate, old, files = inputs()
        print(json.dumps(dict(hardware_accessed=False, scope=SCOPE,
            files={p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else p.as_posix():sha(p) for p in files}), indent=2))
    elif args.validate_approval:
        approved(args.approval_record); print('APPROVAL_VALID; no device discovery or I/O')
    else:
        run(args.approval_record)


if __name__ == '__main__':
    main()
