"""Explicitly approved TF hardware batch; never invoked by a build or CI."""
from pathlib import Path
import argparse
import datetime
import hashlib
import json
import re
import secrets
import subprocess
import sys
import time

from operation_gate import require_approval
from project_check import ROOT, BUNDLE, baseline_errors, sha

MAC = '44:1b:f6:81:91:10'
CARD_SERIAL = '10044BA5'
CAPACITY = 7948206080
PAYLOAD_SHA = '7c79a64ca466f57076b4fa369dd7861cf4657c7b00ed8c1bad0c9c9336cc87b7'
EXPECTED_CANDIDATE = '728e50380e55a08c653142721f064dd5ef9028fe28bb7ed197ef561f10d08ec3'
MANIFEST = ROOT / 'docs/reports/TF_WRITE_BUILD_20261015.json'
ESPTOOL = Path.home() / '.platformio/packages/tool-esptoolpy/esptool.py'


def inputs():
    errors = baseline_errors()
    if errors:
        raise RuntimeError('; '.join(errors))
    record = json.loads(MANIFEST.read_text(encoding='utf-8'))
    if record['source_commit'] != '31dda6f23cab21f9a07330a829bae747211334b0':
        raise RuntimeError('Unexpected candidate source')
    for name, digest in record['inputs'].items():
        path = (ROOT / name).resolve()
        if not path.is_relative_to(ROOT) or sha(path) != digest:
            raise RuntimeError('Candidate input changed: ' + name)
    artifacts = {}
    for item in record['artifacts']:
        path = (ROOT / item['path']).resolve()
        if not path.is_relative_to(ROOT) or sha(path) != item['sha256'] or path.stat().st_size != item['bytes']:
            raise RuntimeError('Candidate artifact mismatch')
        artifacts[path.name] = path
    if sha(artifacts['firmware.bin']) != EXPECTED_CANDIDATE:
        raise RuntimeError('Unapproved candidate')
    files = [Path(__file__).resolve(), MANIFEST, artifacts['firmware.bin'],
             BUNDLE / 'artifacts/firmware.bin', BUNDLE / 'artifacts/bootloader.bin',
             BUNDLE / 'artifacts/partitions.bin', ESPTOOL]
    return artifacts['firmware.bin'], files


def unique_port():
    from serial.tools import list_ports
    ports = [port for port in list_ports.comports() if port.vid == 0x1A86 and port.pid == 0x7522]
    if len(ports) != 1:
        raise RuntimeError('Expected exactly one CH340K controller')
    return ports[0].device


class Session:
    esptool_baud = 921600
    def __init__(self, folder):
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=False)
        self.log = (folder / 'hardware.log').open('x', encoding='utf-8')

    def emit(self, value):
        line = str(value)
        print(line, flush=True)
        self.log.write(line + '\n')
        self.log.flush()

    def esptool(self, port, command):
        argv = [sys.executable, str(ESPTOOL), '--chip', 'esp32s3', '--port', port,
                '--baud', str(self.esptool_baud), '--before', 'default_reset', '--after', 'hard_reset', *command]
        self.emit('ESPTOOL: ' + repr(argv))
        result = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
        self.emit(result.stdout)
        self.emit(result.stderr)
        if result.returncode:
            raise RuntimeError('esptool failed: ' + str(result.returncode))
        match = re.search(r'MAC:\s*([0-9a-fA-F:]{17})', result.stdout)
        if not match or match.group(1).lower() != MAC:
            raise RuntimeError('Controller MAC mismatch')
        return result.stdout

    def serial(self):
        import serial
        port = serial.Serial(port=None, baudrate=115200, timeout=0.1, write_timeout=2)
        port.dtr = False
        port.rts = False
        port.port = unique_port()
        port.open()
        return port

    def collect(self, port, seconds):
        end = time.monotonic() + seconds
        data = bytearray()
        while time.monotonic() < end:
            data.extend(port.read(4096))
        text = data.decode('utf-8', errors='replace')
        if text:
            self.emit(text.rstrip('\r\n'))
        return text

    def query(self, port, command, seconds=2):
        self.emit('SEND: ' + command)
        port.write((command + '\n').encode('ascii'))
        port.flush()
        return self.collect(port, seconds)

    def test(self, port, run, writing):
        command = f'WRITE_TEST {CARD_SERIAL} {CAPACITY} {run}' if writing else f'VERIFY_TEST {run}'
        self.emit('SEND: ' + command)
        port.write((command + '\n').encode('ascii'))
        port.flush()
        data = ''
        end = time.monotonic() + 90
        while time.monotonic() < end:
            chunk = port.read(4096).decode('utf-8', errors='replace')
            if chunk:
                self.emit(chunk.rstrip('\r\n'))
                data += chunk
                match = re.search(r'\[tfrw\] (PASS|FAIL) phase=(\S+) run=' + run + r'[^\r\n]*[\r\n]', data)
                if match:
                    line = match.group(0)
                    phase = 'write_sync_close_readback' if writing else 'read_only_verify'
                    expected_written = 1048576 if writing else 0
                    required = [f'bytes_written={expected_written} ', 'bytes_verified=1048576 ',
                                'sha256=' + PAYLOAD_SHA + ' ', 'write_gate=0 ', 'write_locked=0']
                    if match.group(1) != 'PASS' or match.group(2) != phase or not all(x in line for x in required):
                        raise RuntimeError('Test failed or reply invalid: ' + line.strip())
                    self.collect(port, 0.1)
                    return line.strip()
                if '[tfrw] FAIL' in data or 'Guru Meditation' in data or 'rst:' in data:
                    raise RuntimeError('Hardware failure or unexpected reboot')
        raise RuntimeError('Test timeout; result is not a pass')

    def metadata(self, port):
        text = self.query(port, 'i', 1)
        if f'serial={CARD_SERIAL}' not in text or f'bytes={CAPACITY}' not in text:
            raise RuntimeError('Card identity/capacity not confirmed')


def run_batch(approval):
    candidate, files = inputs()
    record = require_approval(ROOT, approval, 'tf-write-batch', MAC, files)
    expected_scope = {'card_serial': CARD_SERIAL, 'capacity': CAPACITY, 'writes': 10,
                      'mcu_reset': True, 'restore_on_failure': True, 'card_data_disposable': True}
    if record.get('scope') != expected_scope:
        raise RuntimeError('Batch scope mismatch')
    stamp = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y%m%d_%H%M%S')
    session = Session(ROOT / 'local-data/hardware/tf-write' / stamp)
    result = {'approval_record': str(approval), 'source_commit': record['source_commit'],
              'candidate_sha256': sha(candidate), 'card_serial': CARD_SERIAL, 'capacity': CAPACITY,
              'cold_starts_tested': 0, 'writes': [], 'after_mcu_reset': [], 'passed': False}
    wrote_flash = False
    can_restore = False
    try:
        port_name = unique_port()
        identity = session.esptool(port_name, ['flash_id'])
        if 'Detected flash size: 8MB' not in identity:
            raise RuntimeError('Expected 8 MiB controller flash')
        # Snapshot covers all sectors touched by the approved app write. It also
        # contains private configuration; it stays under ignored local-data.
        snapshot = session.folder / 'controller_prefix_0x60000.bin'
        session.esptool(port_name, ['read_flash', '0x0', '0x60000', str(snapshot)])
        data = snapshot.read_bytes()
        frozen_app = (BUNDLE / 'artifacts/firmware.bin').read_bytes()
        frozen_boot = (BUNDLE / 'artifacts/bootloader.bin').read_bytes()
        partitions = (BUNDLE / 'artifacts/partitions.bin').read_bytes()
        if len(data) != 0x60000 or data[:len(frozen_boot)] != frozen_boot or data[0x8000:0x8000 + len(partitions)] != partitions:
            raise RuntimeError('Current bootloader/partitions differ; no flash writes')
        if data[0x10000:0x10000 + len(frozen_app)] != frozen_app:
            raise RuntimeError('Current running app0 differs from frozen baseline; no flash writes')
        if 0x10000 + ((candidate.stat().st_size + 4095) // 4096) * 4096 > len(data):
            raise RuntimeError('Snapshot does not cover write range')
        (session.folder / 'snapshot.json').write_text(json.dumps({
            'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'mac': MAC,
            'scope': '0x0..0x60000 snapshot; not full flash; covers changed app sectors',
            'app0_matches_frozen': True, 'bootloader_matches_frozen': True, 'partitions_match_frozen': True}, indent=2), encoding='utf-8')
        can_restore = True
        wrote_flash = True  # mark before invocation: partial writes must also restore
        session.esptool(port_name, ['write_flash', '--flash_mode', 'keep', '--flash_freq', 'keep',
                                   '--flash_size', 'keep', '0x10000', str(candidate)])
        with session.serial() as port:
            boot = session.collect(port, 8)
            if '[tfrw] diagnostic=v1 source=31dda6f23cab21f9a07330a829bae747211334b0' not in boot:
                # A slow host might miss the banner. Reset once, within approval.
                port.rts = True; time.sleep(0.1); port.rts = False
                boot = session.collect(port, 8)
                if '[tfrw] diagnostic=v1 source=31dda6f23cab21f9a07330a829bae747211334b0' not in boot:
                    raise RuntimeError('Candidate identity banner missing')
            session.metadata(port)
            runs = [f'{(secrets.randbits(24) << 8) | index:08X}' for index in range(10)]
            result['runs'] = runs
            for run in runs:
                result['writes'].append(session.test(port, run, True))
            session.emit('MCU EN RESET; card remains powered; NOT a cold start')
            port.rts = True; time.sleep(0.1); port.rts = False
            session.collect(port, 8)
            session.metadata(port)
            for run in runs:
                result['after_mcu_reset'].append(session.test(port, run, False))
            session.query(port, 'l', 3)
        result['passed'] = True
        session.emit('BATCH PASS: 10 write/sync/close/readback + 10 post-MCU-reset verifies; cold starts pending')
    except Exception as error:
        result['error'] = repr(error)
        session.emit('BATCH FAIL: ' + repr(error))
        if wrote_flash and can_restore:
            try:
                session.emit('APPROVED RECOVERY: restore frozen read-only app0; keep bootloader/OTA/NVS/partitions')
                session.esptool(unique_port(), ['write_flash', '--flash_mode', 'keep', '--flash_freq', 'keep',
                    '--flash_size', 'keep', '0x10000', str(BUNDLE / 'artifacts/firmware.bin')])
                with session.serial() as port:
                    session.collect(port, 8)
                    session.metadata(port)
                    text = session.query(port, 'l', 3)
                    result['recovery_read_ok'] = 'PC_MATCH=1' in text and 'root open=0' in text
            except Exception as recovery_error:
                result['recovery_error'] = repr(recovery_error)
                session.emit('RECOVERY FAILED: ' + repr(recovery_error))
        raise
    finally:
        (session.folder / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        session.emit('RESULT=' + str(session.folder / 'result.json'))
        session.log.close()


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--approval-record', type=Path)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    if args.prepare_only:
        candidate, files = inputs()
        print(json.dumps({'candidate': str(candidate), 'files': {path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path): sha(path) for path in files},
                          'hardware_accessed': False}, ensure_ascii=False, indent=2))
        return
    run_batch(args.approval_record)


if __name__ == '__main__':
    main()
