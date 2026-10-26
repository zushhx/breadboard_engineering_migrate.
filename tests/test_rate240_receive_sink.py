"""Offline disk/task controls for the bounded receive file sink."""
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from rate240_receive_sink import BoundedFileSink,SinkQueueFull,SinkWriteError


class ControlledFile:
    def __init__(self,*,blocked=False,error=None,partial=None,flush_error=None):
        self.started=threading.Event();self.release=threading.Event();self.closed=threading.Event()
        if not blocked:self.release.set()
        self.error=error;self.partial=partial;self.flush_error=flush_error
        self.data=bytearray();self.chunks=[];self.flushed=False

    def write(self,data):
        self.started.set()
        if not self.release.wait(3):raise OSError('Test writer release timed out')
        if self.error:raise self.error
        count=min(len(data),self.partial) if self.partial else len(data)
        self.data.extend(data[:count]);self.chunks.append(count);return count

    def flush(self):
        if self.flush_error:raise self.flush_error
        self.flushed=True

    def close(self):self.closed.set()


class ReceiveSinkTests(unittest.TestCase):
    def sink(self,file,**options):
        with patch('rate240_receive_sink.Path.open',return_value=file) as opened:
            sink=BoundedFileSink('unused.bin',**options)
            opened.assert_called_once_with('xb',buffering=0)
        def cleanup():
            file.release.set()
            try:sink.close()
            except (SinkQueueFull,SinkWriteError):pass
        self.addCleanup(cleanup)
        return sink

    def test_slow_disk_does_not_block_enqueue_and_close_waits_for_flush(self):
        file=ControlledFile(blocked=True)
        sink=self.sink(file,max_queue_bytes=16,batch_bytes=4,flush_interval=.001)
        sink.write(b'abcd');self.assertTrue(file.started.wait(1))
        enqueued=threading.Event()
        def receive():sink.write(b'efgh');enqueued.set()
        receiver=threading.Thread(target=receive);receiver.start()
        self.assertTrue(enqueued.wait(.5),'Enqueue waited for a blocked disk write')
        receiver.join();self.assertEqual(sink.pending_bytes,8)
        completed=threading.Event()
        def close():sink.close();completed.set()
        closer=threading.Thread(target=close);closer.start()
        self.assertFalse(completed.wait(.05))
        file.release.set();self.assertTrue(completed.wait(1));closer.join()
        self.assertEqual(bytes(file.data),b'abcdefgh')
        self.assertTrue(file.flushed);self.assertTrue(file.closed.is_set())
        self.assertEqual(sink.pending_bytes,0);self.assertEqual(sink.written_bytes,8)
        self.assertEqual(sink.high_water_bytes,8)

    def test_inflight_bytes_count_toward_full_queue_and_failure_stays_visible(self):
        file=ControlledFile(blocked=True)
        sink=self.sink(file,max_queue_bytes=8,batch_bytes=4,flush_interval=.001)
        sink.write(b'abcd');self.assertTrue(file.started.wait(1));sink.write(b'efgh')
        with self.assertRaises(SinkQueueFull):sink.write(b'i')
        self.assertEqual(sink.pending_bytes,8)
        with self.assertRaises(SinkQueueFull):sink.write(b'j')
        file.release.set()
        with self.assertRaises(SinkQueueFull):sink.close()
        self.assertEqual(bytes(file.data),b'abcdefgh')
        self.assertEqual(sink.high_water_bytes,8)

    def test_writer_exception_propagates_to_write_and_close(self):
        failure=OSError('disk offline');file=ControlledFile(error=failure)
        sink=self.sink(file,max_queue_bytes=8,batch_bytes=4,flush_interval=.001)
        sink.write(b'abcd');self.assertTrue(file.closed.wait(1))
        with self.assertRaises(SinkWriteError) as raised:sink.write(b'e')
        self.assertIs(raised.exception.__cause__,failure)
        with self.assertRaises(SinkWriteError):sink.close()
        self.assertEqual(sink.written_bytes,0)

    def test_close_timeout_returns_while_disk_remains_blocked_and_stays_failed(self):
        file=ControlledFile(blocked=True)
        sink=self.sink(file,max_queue_bytes=8,batch_bytes=4,flush_interval=.001,close_timeout=.02)
        sink.write(b'abcd');self.assertTrue(file.started.wait(1))
        started=time.monotonic()
        with self.assertRaises(SinkWriteError) as raised:sink.close()
        self.assertLess(time.monotonic()-started,.5)
        self.assertIsInstance(raised.exception.__cause__,TimeoutError)
        self.assertIn('pending_bytes=4',str(raised.exception))
        self.assertTrue(sink.writer_alive);self.assertFalse(file.closed.is_set())
        with self.assertRaises(SinkWriteError):sink.write(b'e')
        # Keep the writer blocked until cleanup; no successful close is reported.
        with self.assertRaises(SinkWriteError):sink.close()
        self.assertFalse(file.release.is_set())

    def test_context_preserves_receive_error_when_close_times_out(self):
        file=ControlledFile(blocked=True)
        sink=self.sink(file,max_queue_bytes=8,batch_bytes=4,flush_interval=.001,close_timeout=.02)
        original=ValueError('network receive failed');started=time.monotonic()
        with self.assertRaises(ValueError) as raised:
            with sink:
                sink.write(b'abcd');self.assertTrue(file.started.wait(1))
                raise original
        self.assertIs(raised.exception,original)
        self.assertLess(time.monotonic()-started,.5)
        self.assertTrue(any('Output close also failed' in note for note in original.__notes__))
        self.assertTrue(sink.writer_alive);self.assertFalse(file.release.is_set())
        self.assertFalse(file.closed.is_set())

    def test_flush_exception_is_reported_and_output_is_closed(self):
        file=ControlledFile(flush_error=OSError('flush failed'))
        sink=self.sink(file,max_queue_bytes=8,batch_bytes=4)
        sink.write(b'abcd')
        with self.assertRaises(SinkWriteError):sink.close()
        self.assertTrue(file.closed.is_set())

    def test_partial_disk_writes_are_retried_and_input_is_snapshotted(self):
        file=ControlledFile(blocked=True,partial=3)
        sink=self.sink(file,max_queue_bytes=16,batch_bytes=8,flush_interval=.001)
        data=bytearray(b'abcdefgh');sink.write(data);data[:]=b'XXXXXXXX'
        file.release.set();sink.close()
        self.assertEqual(bytes(file.data),b'abcdefgh')
        self.assertEqual(sink.written_bytes,8);self.assertEqual(sink.pending_bytes,0)
        self.assertGreater(len(file.chunks),1)

    def test_batches_preserve_all_output_and_close_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'received.bin'
            payload=bytes(range(256))*8
            with BoundedFileSink(path,max_queue_bytes=4096,batch_bytes=128) as sink:
                for offset in range(0,len(payload),17):sink.write(payload[offset:offset+17])
            sink.close();self.assertEqual(path.read_bytes(),payload)
            self.assertEqual(sink.written_bytes,len(payload));self.assertEqual(sink.pending_bytes,0)
            self.assertLessEqual(sink.high_water_bytes,4096)
            with self.assertRaises(ValueError):sink.write(b'late')

    def test_existing_output_is_exclusive_and_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'existing.bin';path.write_bytes(b'keep')
            with self.assertRaises(FileExistsError):BoundedFileSink(path)
            self.assertEqual(path.read_bytes(),b'keep')

    def test_invalid_bounds_fail_before_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'invalid.bin'
            for options in (dict(max_queue_bytes=0),dict(max_queue_bytes=4,batch_bytes=8),
                            dict(flush_interval=0),dict(flush_interval=float('nan')),
                            dict(close_timeout=0),dict(close_timeout=float('inf'))):
                with self.subTest(options=options),self.assertRaises(ValueError):BoundedFileSink(path,**options)
            self.assertFalse(path.exists())


if __name__=='__main__':unittest.main()
