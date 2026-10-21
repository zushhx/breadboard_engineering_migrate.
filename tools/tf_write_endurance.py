"""Approved bounded file-loop and active MCU-reset experiment; no flashing."""
from pathlib import Path
import argparse
import datetime
import json
import re
import secrets
import sys
import time
from operation_gate import require_approval
from project_check import ROOT, sha
from tf_write_batch import Session, inputs, MAC, CARD_SERIAL, CAPACITY, EXPECTED_CANDIDATE

PREVIOUS = ROOT / 'local-data/hardware/tf-write/20261015_200137/result.json'
SCOPE = {'card_serial': CARD_SERIAL, 'capacity': CAPACITY, 'max_seconds': 300,
         'max_complete_files': 60, 'active_mcu_reset': True, 'card_data_disposable': True,
         'flash': False, 'format': False}


def approved_inputs():
    _, files = inputs()
    return files + [Path(__file__).resolve(), PREVIOUS]


def interrupt_during_write(session, port, run_id):
    command = f'WRITE_TEST {CARD_SERIAL} {CAPACITY} {run_id}'
    session.emit('SEND: ' + command)
    port.write((command + '\n').encode('ascii'))
    port.flush()
    text = ''
    end = time.monotonic() + 20
    while time.monotonic() < end:
        chunk = port.read(4096).decode('utf-8', errors='replace')
        if chunk:
            session.emit(chunk.rstrip('\r\n'))
            text += chunk
        if '[tfrw] FAIL' in text or '[tfrw] PASS' in text:
            raise RuntimeError('Active interruption window not reached; no reset sent')
        if '[tfrw] phase=create_new fatfs=0' in text:
            time.sleep(0.2)
            extra = port.read(port.in_waiting).decode('utf-8', errors='replace') if port.in_waiting else ''
            if extra:
                session.emit(extra.rstrip('\r\n'))
                text += extra
            if any(marker in text for marker in ['phase=f_sync', 'phase=close', '[tfrw] PASS', '[tfrw] FAIL']):
                raise RuntimeError('Write finished or failed; no reset sent')
            session.emit('ACTIVE MCU EN RESET: create_new success +200ms; no sync/close/result observed; TF power retained')
            port.rts = True
            time.sleep(0.1)
            port.rts = False
            return {'run': run_id, 'trigger': 'create_new success +200ms, before observed sync/close',
                    'mcu_reset': True, 'tf_power_removed': False,
                    'electrical_write_activity_measured': False, 'partial_file_verified': False}
    raise RuntimeError('No confirmed write phase; no reset sent')


def run(approval):
    record = require_approval(ROOT, approval, 'tf-write-endurance', MAC, approved_inputs())
    if record.get('scope') != SCOPE:
        raise RuntimeError('Endurance scope mismatch; no device access')
    previous = json.loads(PREVIOUS.read_text(encoding='utf-8'))
    if not previous['passed'] or previous['candidate_sha256'] != EXPECTED_CANDIDATE:
        raise RuntimeError('Prior hardware baseline not confirmed')
    if len(previous['runs']) != 10 or any(not re.fullmatch('[0-9A-F]{8}', v) for v in previous['runs']):
        raise RuntimeError('Invalid prior test paths')
    stamp = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y%m%d_%H%M%S')
    session = Session(ROOT / 'local-data/hardware/tf-endurance' / stamp)
    result = {'source_commit': record['source_commit'], 'approval': str(approval),
              'candidate_sha256': EXPECTED_CANDIDATE, 'writes': [], 'runs': [],
              'passed': False, 'flashed': False, 'cold_power_cycles': 0,
              'scope': 'file write/close/read loop, not continuous sensor-load or power-cut qualification'}
    def checkpoint():
        (session.folder / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    def new_run():
        while True:
            value = f'{secrets.randbits(32):08X}'
            if value not in previous['runs'] and value not in result['runs']:
                result['runs'].append(value)
                return value
    try:
        with session.serial() as port:
            session.collect(port, 2)
            session.metadata(port)
            start = time.monotonic()
            while time.monotonic() - start < 300 and len(result['writes']) < 60:
                run_id = new_run()
                result['writes'].append({'run': run_id, 'reply': session.test(port, run_id, True)})
                result['loop_elapsed_seconds'] = time.monotonic() - start
                checkpoint()
                session.emit(f'LOOP progress files={len(result["writes"])} elapsed_s={result["loop_elapsed_seconds"]:.1f}')
            result['stop_reason'] = 'time_limit' if time.monotonic() - start >= 300 else 'file_limit'
            if not result['writes']:
                raise RuntimeError('No loop writes completed')
            result['interruption'] = interrupt_during_write(session, port, new_run())
            checkpoint()
            boot = session.collect(port, 8)
            expected = '[tfrw] diagnostic=v1 source=31dda6f23cab21f9a07330a829bae747211334b0'
            if expected not in boot or 'card init=ESP_OK' not in boot or 'FatFS mount=0' not in boot:
                raise RuntimeError('Post-interruption firmware/card/mount did not recover')
            session.metadata(port)
            result['recovery_new_file'] = session.test(port, new_run(), True)
            result['completed_files_after_interrupt'] = [
                session.test(port, previous['runs'][0], False),
                session.test(port, result['writes'][-1]['run'], False)]
            text = session.query(port, 'l', 3)
            if 'root open=0' not in text or text.count('PC_MATCH=1') != 2:
                raise RuntimeError('Post-interruption directory/reference check failed')
            result['original_reference_match_count_after_interrupt'] = text.count('PC_MATCH=1')
            result['passed'] = True
            session.emit('PASS: bounded file loop + MCU reset before observed sync/close + remount/new write/selected completed-file checks')
            session.emit('Partial file not verified/recovered; TF power retained; not a true power-cut or live sampling test')
    except Exception as error:
        result['error'] = repr(error)
        session.emit('FAIL: ' + repr(error) + '; no retry, format, repair or flash')
        raise
    finally:
        checkpoint()
        session.emit('RESULT=' + str(session.folder / 'result.json'))
        session.log.close()


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--approval-record', type=Path)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    if args.prepare_only:
        print(json.dumps({'scope': SCOPE, 'files': {p.as_posix(): sha(p) for p in approved_inputs()},
                          'hardware_accessed': False}, indent=2))
    else:
        run(args.approval_record)


if __name__ == '__main__':
    main()
