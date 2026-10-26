"""Bounded R240 receiver. Default is offline preparation; never starts a recording."""
import argparse
import datetime
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import socket
import struct
import time
import urllib.request

from operation_gate import require_approval
from parse_sensor_record import validate

ROOT = Path(__file__).resolve().parents[1]
FRAME = struct.Struct('<4sIII')
PATTERN = bytes((i * 31 + 17) & 255 for i in range(256))


def pattern(offset, count):
    return (PATTERN * ((count + 511) // 256))[offset % 256:offset % 256 + count]


def exact(sock, count, deadline, clock=time.monotonic):
    result = bytearray()
    while len(result) < count:
        remaining = deadline - clock()
        if remaining <= 0:
            raise ValueError('overall receive deadline exceeded')
        sock.settimeout(min(remaining, 10))
        part = sock.recv(count - len(result))
        if not part:
            raise ValueError('truncated stream')
        result.extend(part)
    return bytes(result)


def receive(sock, mode, seconds, output, clock=time.monotonic, expected_format='record',
            identity=None, on_ready=None, expected_run=None, background_write=False):
    """Preserve bounded receive evidence even when validation fails; do not relax validation."""
    output=Path(output)
    evidence_path=output.with_name(output.name+'.receive.json')
    summary_path=output.with_name(output.name+'.summary.raw')
    if any(path.exists() for path in (output,evidence_path,summary_path)):
        raise FileExistsError('Receive output/evidence already exists; preserve prior evidence')
    evidence=dict(mode=mode,requested_seconds=seconds,payload_bytes=0,data_frames=0,
                  max_data_gap_seconds=0,summary_received=False,validation_complete=False,passed=False)
    try:
        evidence['background_write']=background_write
        result=_receive(sock,mode,seconds,output,clock,expected_format,identity,on_ready,expected_run,evidence,background_write)
    except Exception as error:
        evidence.update(error=repr(error),failure_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        failure_clock_seconds=clock())
        try:
            with evidence_path.open('x',encoding='utf-8') as target:
                json.dump(evidence,target,ensure_ascii=False,indent=2)
        except OSError as evidence_error:
            error.add_note('Receive evidence could not be saved: '+repr(evidence_error))
        raise
    evidence.update(validation_complete=True,passed=result['passed'])
    with evidence_path.open('x',encoding='utf-8') as target:
        json.dump(evidence,target,ensure_ascii=False,indent=2)
    return result


def _receive(sock, mode, seconds, output, clock, expected_format, identity, on_ready, expected_run, evidence, background_write=False):
    """Validate framing online; SHA/record validation occur after receive timing ends."""
    started = clock()
    evidence['started_clock_seconds']=started
    deadline = started + seconds + 140  # arm 30, device verify 90, drain 10, margin 10
    seq = total = 0
    first = last = None
    first_size = 0
    maximum = seconds * 260000 + 65536
    ready = None
    max_gap = 0
    if background_write:
        from rate240_receive_sink import BoundedFileSink
        sink = BoundedFileSink(output)
    else:
        sink = Path(output).open('xb')
    try:
        with sink as stream:
            while True:
                raw = exact(sock, FRAME.size, deadline, clock)
                magic, kind, number, size = FRAME.unpack(raw)
                if magic != b'R240' or number != seq or kind not in (1, 2, 3) or not 1 <= size <= 4096:
                    raise ValueError('invalid frame magic/sequence/type/length')
                seq += 1
                data = exact(sock, size, deadline, clock)
                now = clock()
                if kind == 3:
                    if ready is not None or total:
                        raise ValueError('duplicate or late READY')
                    ready = json.loads(data)
                    if (ready.get('mode') != mode or ready.get('seconds') != seconds or
                            ready.get('armed') is not True or ready.get('rate_bps') != 240000 or
                            not re.fullmatch('[0-9a-f]{40}', str(ready.get('source', ''))) or
                            not re.fullmatch('[0-9a-f]{64}', str(ready.get('inputs', '')))):
                        raise ValueError('invalid READY identity/mode/rate')
                    if identity and any(ready.get(k) != identity[k] for k in ('source', 'inputs')):
                        raise ValueError('candidate identity mismatch')
                    if on_ready:
                        on_ready(ready)
                    continue
                if ready is None:
                    raise ValueError('missing READY')
                if kind == 2:
                    summary_time = now
                    evidence.update(summary_received=True,summary_clock_seconds=now,
                        summary_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
                    # Save the exact bounded frame body before parsing or checking its fields.
                    summary_path=Path(output).with_name(Path(output).name+'.summary.raw')
                    with summary_path.open('xb') as target:
                        target.write(data);target.flush()
                    summary = json.loads(data)
                    evidence['device_summary']=summary
                    break
                if total + size > maximum:
                    raise ValueError('payload exceeds bounded run size')
                if not background_write and (mode == 'SYNTH' or expected_format == 'pattern') and data != pattern(total, size):
                    raise ValueError('synthetic payload mismatch')
                if first is None:
                    first = now
                    first_size = size
                else:
                    max_gap = max(max_gap, now-last)
                last = now
                stream.write(data)
                total += size
                evidence.update(payload_bytes=total,data_frames=evidence['data_frames']+1,
                                first_payload_clock_seconds=first,last_payload_clock_seconds=last,
                                max_data_gap_seconds=max_gap)
    finally:
        if background_write:
            evidence.update(pc_sink_high_water_bytes=sink.high_water_bytes,
                            pc_sink_written_bytes=sink.written_bytes,
                            pc_sink_pending_bytes=sink.pending_bytes)
    if background_write and sink.written_bytes != total:
        raise ValueError('background output byte count mismatch')
    sock.settimeout(min(2, max(.01, deadline-clock())))
    if sock.recv(1):
        raise ValueError('trailing stream after summary')
    if first is None or last <= first:
        raise ValueError('insufficient timed payload')
    integers = ('errors', 'input_bytes', 'sent_bytes', 'dropped_bytes', 'start_us', 'stop_us', 'elapsed_us')
    if any(type(summary.get(k)) is not int or summary[k] < 0 for k in integers):
        raise ValueError('invalid summary fields')
    elapsed = summary['elapsed_us'] / 1e6
    if (summary.get('mode') != mode or summary.get('source_ok') is not True or summary['errors'] or
            summary['dropped_bytes'] or summary['input_bytes'] != total or summary['sent_bytes'] != total or
            summary['stop_us'] - summary['start_us'] != summary['elapsed_us'] or elapsed <= 0):
        raise ValueError('source failure/count/timing mismatch')
    # Include an estimated first payload interval, avoiding a one-frame rate overestimate.
    is_pattern = mode == 'SYNTH' or expected_format == 'pattern'
    if summary.get('format') != ('pattern' if is_pattern else 'record'):
        raise ValueError('source format mismatch')
    if summary.get('target_bps') != 240000 or summary.get('planned_us') != seconds*1000000:
        raise ValueError('source target/duration mismatch')
    pc_span = last - first + (first_size/240000 if is_pattern else 0)
    with Path(output).open('rb') as source:
        if background_write and is_pattern:
            hasher = hashlib.sha256(); offset = 0
            while True:
                chunk = source.read(65536)
                if not chunk: break
                if chunk != pattern(offset,len(chunk)):
                    raise ValueError('synthetic payload mismatch')
                hasher.update(chunk); offset += len(chunk)
            digest = hasher.hexdigest()
        else:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
    record = None
    if is_pattern:
        if total != seconds * 240000:
            raise ValueError('incomplete synthetic byte count')
    if mode == 'MIRROR':
        if not re.fullmatch('[0-9A-F]{8}', str(summary.get('run', ''))):
            raise ValueError('invalid mirrored run')
        if expected_run is not None and summary['run'] != expected_run:
            raise ValueError('unexpected mirrored run')
        if not re.fullmatch('[0-9a-f]{64}', str(summary.get('sha256', ''))) or digest != summary['sha256']:
            raise ValueError('PC/TF SHA mismatch')
    if mode == 'MIRROR' and expected_format == 'record':
        record = validate(output, preview_limit=0)
        if record['metadata']['requested_seconds'] != seconds or record['metadata']['mac'].lower() != '44:1b:f6:81:91:10':
            raise ValueError('record duration/controller mismatch')
        if identity and (record['metadata']['source'] != identity['source'] or record['metadata']['inputs'] != identity['inputs']):
            raise ValueError('record candidate identity mismatch')
    duration_complete = seconds * .99 <= elapsed <= seconds * 1.01
    captured = None
    if record is not None:
        captured = (record['footer']['stop_us']-record['footer']['start_us'])/1e6
        # A real record stops acquisition before draining, writing its footer and
        # synchronizing/closing. Judge acquisition duration from the verified footer;
        # report the bounded closing tail separately rather than calling it sampling.
        duration_complete = (seconds*.99 <= captured <= seconds*1.01 and
                             captured <= elapsed <= captured+5)
    source_rate = total / elapsed
    pc_rate = total / pc_span
    rate_pass = not is_pattern or (source_rate >= 240000 * .99 and pc_rate >= 240000 * .99)
    passed = duration_complete and rate_pass
    return dict(complete=True, passed=passed, mode=mode, payload_bytes=total,
                source_seconds=elapsed, source_payload_Bps=source_rate, pc_payload_seconds=pc_span,
                pc_payload_Bps=pc_rate, wait_before_data_seconds=first-started,
                summary_wait_after_data_seconds=summary_time-last, sha256=digest,
                max_data_gap_seconds=max_gap, ready=ready, expected_format=expected_format,
                capture_seconds=captured, source_post_capture_seconds=None if captured is None else elapsed-captured,
                duration_complete=duration_complete, tolerance_fraction=.01,
                formal_300s_complete=passed and seconds == 300,
                target_240k_test=is_pattern, target_Bps=240000, zero_reported_drops=True,
                device_summary=summary, record=record)


def candidate_identity(manifest):
    manifest = Path(manifest).resolve()
    if not manifest.is_relative_to(ROOT/'local-data'):
        raise ValueError('candidate manifest must remain under local-data')
    build = json.loads(manifest.read_text(encoding='utf-8'))
    source, inputs = build.get('source_commit'), build.get('input_sha256')
    if (build.get('build_passed') is not True or build.get('working_tree_dirty_at_start') is not False or
            not re.fullmatch('[0-9a-f]{40}', str(source)) or
            not re.fullmatch('[0-9a-f]{64}', str(inputs))):
        raise ValueError('expected clean, completed candidate build')
    artifacts = [x for x in build['artifacts'] if x['path'].endswith('/firmware.bin')]
    if len(artifacts) != 1:
        raise ValueError('candidate firmware missing/ambiguous')
    artifact = artifacts[0]
    firmware = (ROOT/artifact['path']).resolve()
    if not firmware.is_relative_to(ROOT/'local-data'):
        raise ValueError('candidate firmware must remain under local-data')
    with firmware.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if firmware.stat().st_size != artifact['bytes'] or digest != artifact['sha256']:
        raise ValueError('candidate firmware mismatch')
    return dict(source=source, inputs=inputs), [manifest, firmware]


def http_get(host, path, timeout):
    # No environment proxies or redirects; never forward the boot key.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            raise ValueError('redirect rejected')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open('http://' + host + path, timeout=timeout) as response:
        body = response.read(65537)
    if len(body) > 65536:
        raise ValueError('HTTP response too large')
    return body


def request_line(key, mode, seconds, run=None, expected_format='record'):
    if not re.fullmatch('[0-9a-fA-F]{32}', key) or mode not in ('SYNTH', 'MIRROR') or not 1 <= seconds <= 300:
        raise ValueError('invalid benchmark request')
    if mode == 'SYNTH':
        return f'BENCH1 {key} SYNTH {seconds}\n'.encode('ascii')
    if not re.fullmatch('[0-9A-F]{8}', run or '') or expected_format not in ('pattern', 'record'):
        raise ValueError('MIRROR must bind run and format')
    return f'BENCH2 {key} MIRROR {seconds} {run} {expected_format}\n'.encode('ascii')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True, help='explicit private IPv4 address, no discovery')
    parser.add_argument('--mode', choices=('SYNTH', 'MIRROR'), required=True)
    parser.add_argument('--expected-format', choices=('record', 'pattern'), default='record')
    parser.add_argument('--candidate-manifest', type=Path)
    parser.add_argument('--run', help='expected 8 uppercase hex run for MIRROR')
    parser.add_argument('--seconds', type=int, choices=range(1, 301), default=30)
    parser.add_argument('--timeout', type=float, default=5)
    parser.add_argument('--output', type=Path, required=True, help='new directory under local-data')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--approval-record', type=Path)
    args = parser.parse_args()
    if args.mode == 'SYNTH':
        args.expected_format = 'pattern'
    addr = ipaddress.IPv4Address(args.host)
    if not any(addr in ipaddress.IPv4Network(net) for net in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')):
        parser.error('host must be an RFC1918 literal address')
    if not 0 < args.timeout <= 10:
        parser.error('timeout must be >0 and <=10 seconds')
    if args.mode == 'MIRROR' and not re.fullmatch('[0-9A-F]{8}', args.run or ''):
        parser.error('MIRROR requires --run with 8 uppercase hexadecimal digits')
    output = args.output.resolve()
    if not output.is_relative_to(ROOT/'local-data') or output.exists():
        parser.error('output must be a new directory under local-data')
    identity = None
    candidate_files = []
    if args.candidate_manifest:
        identity, candidate_files = candidate_identity(args.candidate_manifest)
    scope = dict(host=args.host, mode=args.mode, seconds=args.seconds, starts_recording=False,
                 expected_format=args.expected_format, candidate=identity, run=args.run)
    if not args.execute:
        print(json.dumps(dict(prepared=True, executed=False, scope=scope,
                              requires_candidate_manifest=identity is None)))
        return
    if identity is None:
        parser.error('--execute requires --candidate-manifest')
    approval = require_approval(ROOT, args.approval_record, 'rate240-receive', '44:1b:f6:81:91:10',
                                [Path(__file__), ROOT/'tools/parse_sensor_record.py', ROOT/'tools/operation_gate.py'] + candidate_files)
    if approval.get('scope') != scope:
        raise ValueError('approval scope mismatch')
    status = json.loads(http_get(args.host, '/api/status', args.timeout))
    if status.get('phase') not in ('idle', 'passed') or not status.get('ready') or not status.get('connected') or status.get('errors'):
        raise ValueError('device is not idle and ready')
    page = http_get(args.host, '/', args.timeout).decode('utf-8')
    match = re.search(r"const sessionKey='([0-9a-fA-F]{32})';", page)
    if not match or match[1] != status.get('boot'):
        raise ValueError('boot identity mismatch')
    output.mkdir(parents=True)
    def ready_received(info):
        (output/'ready.json').write_text(json.dumps(info, indent=2)+'\n', encoding='utf-8')
        print(json.dumps(dict(ready_received=True, mode=args.mode,
                              awaiting_explicit_record_command=args.mode == 'MIRROR')), flush=True)
    try:
        with socket.create_connection((args.host, 19000), args.timeout) as sock:
            sock.sendall(request_line(match[1], args.mode, args.seconds, args.run, args.expected_format))
            result = receive(sock, args.mode, args.seconds, output/'payload.bin',
                             expected_format=args.expected_format, identity=identity, on_ready=ready_received,
                             expected_run=args.run)
        if args.mode == 'MIRROR' and result['record'] is not None:
            meta = result['record']['metadata']
            if meta['card_serial'] != status.get('card_serial') or str(meta['card_bytes']) != status.get('card_bytes'):
                raise ValueError('card identity changed')
        (output/'result.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
        print(json.dumps(dict(passed=result['passed'], output=str(output))))
        if not result['passed']:
            raise SystemExit(2)
    except Exception as exc:
        (output/'failure.json').write_text(json.dumps(dict(passed=False, error=type(exc).__name__))+'\n')
        raise SystemExit('Receive failed; partial evidence retained (credentials omitted)') from None


if __name__ == '__main__':
    main()
