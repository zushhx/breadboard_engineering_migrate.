"""Offline fragmented socket simulation; never connects to a device or network."""
import importlib.util
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import struct
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import rate190_receive as r
import parse_sensor_record as rec


class Clock:
    value = 0
    def __call__(self):
        return self.value


class EvidenceTests(unittest.TestCase):
    def test_failed_summary_is_preserved_exactly_before_rejection(self):
        frames=synthetic(source_ok=False,errors=24)
        raw=frames[-1][r.FRAME.size:]
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'data.bin';clock=Clock()
            with self.assertRaisesRegex(ValueError,'source failure'):
                r.receive(Fragmented(frames,clock),'SYNTH',1,output,clock)
            self.assertEqual(output.with_name('data.bin.summary.raw').read_bytes(),raw)
            evidence=json.loads(output.with_name('data.bin.receive.json').read_text())
            self.assertEqual(evidence['device_summary']['errors'],24)
            self.assertEqual(evidence['payload_bytes'],240000)
            self.assertEqual(evidence['data_frames'],100)
            self.assertGreater(evidence['max_data_gap_seconds'],0)
            self.assertFalse(evidence['passed'])
            self.assertFalse(evidence['validation_complete'])

    def test_invalid_json_summary_raw_survives_parse_failure(self):
        frames=synthetic();raw=b'{not json'
        frames[-1]=r.FRAME.pack(b'R240',2,len(frames)-1,len(raw))+raw
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'data.bin';clock=Clock()
            with self.assertRaises(json.JSONDecodeError):
                r.receive(Fragmented(frames,clock),'SYNTH',1,output,clock)
            self.assertEqual(output.with_name('data.bin.summary.raw').read_bytes(),raw)
            evidence=json.loads(output.with_name('data.bin.receive.json').read_text())
            self.assertTrue(evidence['summary_received'])
            self.assertNotIn('device_summary',evidence)

    def test_eof_preserves_completed_frames_without_inventing_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'data.bin';clock=Clock()
            with self.assertRaisesRegex(ValueError,'truncated'):
                r.receive(Fragmented(synthetic()[:3],clock),'SYNTH',1,output,clock)
            evidence=json.loads(output.with_name('data.bin.receive.json').read_text())
            self.assertEqual(evidence['payload_bytes'],4800)
            self.assertEqual(evidence['data_frames'],2)
            self.assertFalse(evidence['summary_received'])
            self.assertFalse(output.with_name('data.bin.summary.raw').exists())

    def test_slow_run_remains_failed_after_complete_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'data.bin';clock=Clock()
            result=r.receive(Fragmented(synthetic(),clock,delay=.02),'SYNTH',1,output,clock)
            evidence=json.loads(output.with_name('data.bin.receive.json').read_text())
            self.assertFalse(result['passed'])
            self.assertFalse(evidence['passed'])
            self.assertTrue(evidence['validation_complete'])

    def test_existing_evidence_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'data.bin';raw_path=output.with_name('data.bin.summary.raw')
            raw_path.write_bytes(b'previous evidence');clock=Clock()
            with self.assertRaises(FileExistsError):
                r.receive(Fragmented(synthetic(),clock),'SYNTH',1,output,clock)
            self.assertEqual(raw_path.read_bytes(),b'previous evidence')
            self.assertFalse(output.exists())


class Fragmented:
    def __init__(self, frames, clock, delay=.01, fragment=73):
        self.frames = list(frames)
        self.clock = clock
        self.delay = delay
        self.fragment = fragment
        self.part = b''
    def settimeout(self, timeout):
        pass
    def recv(self, n):
        if not self.part:
            if not self.frames:
                return b''
            self.part = self.frames.pop(0)
            self.clock.value += self.delay
        data, self.part = self.part[:min(n, self.fragment)], self.part[min(n, self.fragment):]
        return data


def synthetic(seconds=1, **updates):
    ready = dict(mode='SYNTH', armed=True, rate_bps=240000, seconds=seconds,
                 format='pattern', source='a'*40, inputs='b'*64)
    body = json.dumps(ready).encode()
    frames = [r.FRAME.pack(b'R240', 3, 0, len(body))+body]
    for seq in range(seconds * 100):
        data = r.pattern(seq*2400, 2400)
        frames.append(r.FRAME.pack(b'R240', 1, seq+1, len(data))+data)
    summary = dict(mode='SYNTH', source_ok=True, errors=0, input_bytes=240000*seconds,
                   sent_bytes=240000*seconds, dropped_bytes=0, start_us=100,
                   stop_us=100+seconds*1000000, elapsed_us=seconds*1000000,
                   format='pattern', target_bps=240000, planned_us=seconds*1000000)
    summary.update(updates)
    data = json.dumps(summary).encode()
    frames.append(r.FRAME.pack(b'R240', 2, len(frames), len(data))+data)
    return frames


def record_stream(capture_us=1000000, error=0, source_elapsed_us=1000000):
    header = rec.HEADER.pack(b'I3REC001', rec.HEADER.size, 1, 0x10044BA5, 7948206080,
                            1,26667,8000000,61,1,b'a'*40,b'b'*64,b'44:1b:f6:81:91:10',
                            b'20261015-IO13-TFdirect',100000,25000,625000,0x44,0)
    data = header[:-4]+struct.pack('<I',zlib.crc32(header[:-4]))
    sequence = 0
    def block(kind, count, sample, first, last, payload):
        nonlocal sequence
        head=rec.BLOCK.pack(0x314B4C42,kind,sequence,len(payload),count,first,last,sample,0)
        sequence+=1
        return head[:-4]+struct.pack('<I',zlib.crc32(head[:-4]+payload))+payload
    samples=round(26667*capture_us/1000000)
    first=1000000
    for offset in range(0,samples,448):
        count=min(448,samples-offset)
        payload=struct.pack('<Bhhh',0x10,0,0,1000)*count
        data+=block(1,count,offset,first+offset*37,first+(offset+count)*37,payload)
    ina=rec.INA.pack(first,0x4527,0x800,8,50,2632,50,8,1)
    data+=block(2,1,samples,first,first,ina)
    footer=rec.FOOTER.pack(samples,first,first+capture_us,1,0,error,0,100,5,0,0,20000,100,100)
    data+=block(3,1,samples,first,first+capture_us,footer)
    ready=dict(mode='MIRROR',armed=True,rate_bps=240000,seconds=1,source='a'*40,inputs='b'*64)
    raw=json.dumps(ready).encode();frames=[r.FRAME.pack(b'R240',3,0,len(raw))+raw]
    for offset in range(0,len(data),3200):
        chunk=data[offset:offset+3200]
        frames.append(r.FRAME.pack(b'R240',1,len(frames),len(chunk))+chunk)
    summary=dict(mode='MIRROR',format='record',run='DEADBEEF',source_ok=True,errors=0,
                 input_bytes=len(data),sent_bytes=len(data),dropped_bytes=0,start_us=100,
                 stop_us=100+source_elapsed_us,elapsed_us=source_elapsed_us,target_bps=240000,planned_us=1000000,
                 sha256=hashlib.sha256(data).hexdigest())
    raw=json.dumps(summary).encode()
    frames.append(r.FRAME.pack(b'R240',2,len(frames),len(raw))+raw)
    return frames


class ReceiverTests(unittest.TestCase):
    def run_stream(self, frames, delay=.01):
        with tempfile.TemporaryDirectory() as tmp:
            clock = Clock()
            return r.receive(Fragmented(frames, clock, delay), 'SYNTH', 1, Path(tmp)/'data.bin', clock)
    def test_fragmented_complete_source_and_pc_rate(self):
        result = self.run_stream(synthetic())
        self.assertTrue(result['passed'])
        self.assertAlmostEqual(result['pc_payload_Bps'], 240000)
        self.assertFalse(result['formal_300s_complete'])
    def test_slow_transport_is_not_a_rate_pass(self):
        self.assertFalse(self.run_stream(synthetic(), .02)['passed'])
    def test_slow_source_is_not_a_rate_pass(self):
        self.assertFalse(self.run_stream(synthetic(stop_us=2000100, elapsed_us=2000000))['passed'])
    def test_reported_drop_rejected(self):
        with self.assertRaisesRegex(ValueError, 'source failure'):
            self.run_stream(synthetic(dropped_bytes=1))
    def test_bad_pattern_rejected(self):
        frames = synthetic()
        frames[1] = frames[1][:-1]+bytes([frames[1][-1]^1])
        with self.assertRaisesRegex(ValueError, 'payload mismatch'):
            self.run_stream(frames)
    def test_missing_summary_rejected(self):
        with self.assertRaisesRegex(ValueError, 'truncated'):
            self.run_stream(synthetic()[:-1])
    def test_wrong_sequence_rejected(self):
        frames = synthetic()
        frames[1] = r.FRAME.pack(b'R240', 1, 5, 2400)+frames[1][r.FRAME.size:]
        with self.assertRaisesRegex(ValueError, 'sequence'):
            self.run_stream(frames)
    def test_oversize_header_rejected_before_read(self):
        with self.assertRaisesRegex(ValueError, 'length'):
            self.run_stream([r.FRAME.pack(b'R240', 1, 0, 4097)])
    def test_pattern_unaligned_large_span(self):
        self.assertEqual(r.pattern(255, 4096), bytes(((255+i)*31+17)&255 for i in range(4096)))

    def test_missing_ready_rejected(self):
        frames = synthetic()[1:]
        frames[0] = r.FRAME.pack(b'R240',1,0,2400)+frames[0][r.FRAME.size:]
        with self.assertRaisesRegex(ValueError,'missing READY'):
            self.run_stream(frames)

    def test_trailing_bytes_rejected(self):
        with self.assertRaisesRegex(ValueError,'trailing'):
            self.run_stream(synthetic()+[b'extra'])

    def test_candidate_identity_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock=Clock()
            with self.assertRaisesRegex(ValueError,'candidate identity'):
                r.receive(Fragmented(synthetic(),clock),'SYNTH',1,Path(tmp)/'data.bin',clock,
                          identity=dict(source='c'*40,inputs='b'*64))

    def test_mirror_pattern_requires_actual_tf_hash(self):
        def frames(digest):
            result=synthetic()
            ready=json.loads(result[0][r.FRAME.size:]); ready['mode']='MIRROR'
            raw=json.dumps(ready).encode();result[0]=r.FRAME.pack(b'R240',3,0,len(raw))+raw
            summary=json.loads(result[-1][r.FRAME.size:])
            summary.update(mode='MIRROR',run='DEADBEEF',sha256=digest)
            raw=json.dumps(summary).encode()
            result[-1]=r.FRAME.pack(b'R240',2,len(result)-1,len(raw))+raw
            return result
        digest=hashlib.sha256(r.pattern(0,240000)).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            clock=Clock()
            result=r.receive(Fragmented(frames(digest),clock),'MIRROR',1,Path(tmp)/'ok.bin',clock,
                             expected_format='pattern',expected_run='DEADBEEF')
            self.assertTrue(result['passed'])
            self.assertIsNone(result['record'])
            clock=Clock()
            with self.assertRaisesRegex(ValueError,'PC/TF SHA'):
                r.receive(Fragmented(frames('0'*64),clock),'MIRROR',1,Path(tmp)/'bad.bin',clock,
                          expected_format='pattern')

    def test_no_execute_does_not_access_network(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp, patch.object(r,'ROOT',Path(tmp)):
            argv=['rate190_receive.py','--host','192.168.1.2','--mode','SYNTH',
                  '--output',str(Path(tmp)/'local-data/new')]
            with patch.object(sys,'argv',argv), patch.object(r,'http_get') as http, \
                 patch.object(r.socket,'create_connection') as tcp:
                r.main();http.assert_not_called();tcp.assert_not_called()

    def test_real_record_mirror_validates_complete_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock=Clock()
            result=r.receive(Fragmented(record_stream(),clock),'MIRROR',1,Path(tmp)/'data.bin',clock,
                             identity=dict(source='a'*40,inputs='b'*64),expected_run='DEADBEEF')
            self.assertTrue(result['passed'])
            self.assertFalse(result['target_240k_test'])
            self.assertEqual(result['record']['footer']['samples'],26667)

    def test_short_capture_not_hidden_by_summary_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock=Clock()
            result=r.receive(Fragmented(record_stream(100000),clock),'MIRROR',1,Path(tmp)/'data.bin',clock)
            self.assertFalse(result['passed'])

    def test_real_acquisition_duration_excludes_bounded_close_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock=Clock()
            result=r.receive(Fragmented(record_stream(source_elapsed_us=1200000),clock),'MIRROR',1,Path(tmp)/'data.bin',clock)
            self.assertTrue(result['passed'])
            self.assertEqual(result['capture_seconds'],1)
            self.assertAlmostEqual(result['source_post_capture_seconds'],.2)

    def test_real_source_duration_cannot_precede_capture_or_hide_unbounded_tail(self):
        for elapsed in (900000,6100000):
            with self.subTest(elapsed=elapsed),tempfile.TemporaryDirectory() as tmp:
                clock=Clock()
                result=r.receive(Fragmented(record_stream(source_elapsed_us=elapsed),clock),'MIRROR',1,Path(tmp)/'data.bin',clock)
                self.assertFalse(result['passed'])

    def test_record_error_not_hidden_by_summary_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock=Clock()
            with self.assertRaisesRegex(ValueError,'recording failed'):
                r.receive(Fragmented(record_stream(error=2),clock),'MIRROR',1,Path(tmp)/'data.bin',clock)

    def test_candidate_binary_hash_is_bound_before_network(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp, patch.object(r,'ROOT',Path(tmp)):
            local=Path(tmp)/'local-data';local.mkdir()
            firmware=local/'firmware.bin';firmware.write_bytes(b'candidate')
            build=dict(source_commit='a'*40,input_sha256='b'*64,build_passed=True,
                       working_tree_dirty_at_start=False,
                       artifacts=[dict(path='local-data/firmware.bin',bytes=9,
                                       sha256=hashlib.sha256(b'candidate').hexdigest())])
            manifest=local/'build_manifest.json';manifest.write_text(json.dumps(build))
            identity,files=r.candidate_identity(manifest)
            self.assertEqual(identity,dict(source='a'*40,inputs='b'*64))
            self.assertIn(firmware,files)
            firmware.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'firmware mismatch'):
                r.candidate_identity(manifest)

    def test_execute_without_candidate_is_rejected_before_network(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp, patch.object(r,'ROOT',Path(tmp)):
            argv=['rate190_receive.py','--host','192.168.1.2','--mode','SYNTH',
                  '--output',str(Path(tmp)/'local-data/new'),'--execute']
            with patch.object(sys,'argv',argv), patch.object(r,'http_get') as http, \
                 patch.object(r.socket,'create_connection') as tcp:
                with self.assertRaises(SystemExit):r.main()
                http.assert_not_called();tcp.assert_not_called()


class BackgroundReceiveTests(unittest.TestCase):
    def run_capture(self, frames, output):
        from unittest.mock import patch
        from rate240_receive_sink import BoundedFileSink
        # Fake socket has no wall-time pacing; retain a bounded whole-run budget here.
        def sink(path):
            return BoundedFileSink(path,max_queue_bytes=262144)
        clock=Clock()
        with patch('rate240_receive_sink.BoundedFileSink',side_effect=sink):
            return r.receive(Fragmented(frames,clock),'SYNTH',1,output,clock,background_write=True)

    def test_background_capture_preserves_payload_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'data.bin'
            result=self.run_capture(synthetic(),output)
            self.assertTrue(result['passed'])
            self.assertEqual(output.read_bytes(),r.pattern(0,240000))
            evidence=json.loads(output.with_name('data.bin.receive.json').read_text())
            self.assertTrue(evidence['background_write'])
            self.assertEqual(evidence['pc_sink_written_bytes'],240000)
            self.assertLessEqual(evidence['pc_sink_high_water_bytes'],262144)

    def test_background_pattern_corruption_still_fails(self):
        frames=synthetic(); bad=bytearray(frames[1]);bad[-1]^=1;frames[1]=bytes(bad)
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'data.bin'
            with self.assertRaisesRegex(ValueError,'synthetic payload mismatch'):
                self.run_capture(frames,output)
            self.assertEqual(output.stat().st_size,240000)
            evidence=json.loads(output.with_name('data.bin.receive.json').read_text())
            self.assertFalse(evidence['passed'])

    def test_background_eof_drains_prior_complete_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'data.bin'
            with self.assertRaisesRegex(ValueError,'truncated'):
                self.run_capture(synthetic()[:3],output)
            self.assertEqual(output.read_bytes(),r.pattern(0,4800))


if __name__ == '__main__':
    unittest.main()
