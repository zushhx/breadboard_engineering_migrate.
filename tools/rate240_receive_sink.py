"""Bounded background file writes for a receive thread; wireless latency is separate.

The byte limit includes queued data and bytes currently inside a file write. It
limits unfinished application writes, not durable-media or end-to-end latency.
No device access occurs here. The output is created exclusively and retained on
failure, including a partial output when the disk or the bounded queue fails.
Closing waits at most close_timeout; a timed-out daemon keeps sole ownership of
its partial output and closes it when the outstanding disk operation returns.
"""
from collections import deque
import math
from pathlib import Path
import threading
import time


class SinkQueueFull(BufferError):
    """The byte budget was exhausted; this output is incomplete."""


class SinkWriteError(OSError):
    """The background writer failed; the original error is the cause."""


class BoundedFileSink:
    def __init__(self,path,*,max_queue_bytes=16384,batch_bytes=8192,flush_interval=0.01,
                 close_timeout=2.0):
        if (type(max_queue_bytes) is not int or max_queue_bytes<=0 or
            type(batch_bytes) is not int or not 0<batch_bytes<=max_queue_bytes or
            isinstance(flush_interval,bool) or not isinstance(flush_interval,(int,float)) or
            not math.isfinite(flush_interval) or flush_interval<=0 or
            isinstance(close_timeout,bool) or not isinstance(close_timeout,(int,float)) or
            not math.isfinite(close_timeout) or close_timeout<=0):
            raise ValueError('Expected positive byte bounds and finite flush/close intervals')
        self.max_queue_bytes=max_queue_bytes
        self.batch_bytes=batch_bytes
        self.flush_interval=flush_interval
        self.close_timeout=close_timeout
        self._condition=threading.Condition()
        self._queue=deque()
        self._pending_bytes=self._written_bytes=self._high_water_bytes=0
        self._closing=False
        self._error=None
        self._file=Path(path).open('xb',buffering=0)
        self._thread=threading.Thread(target=self._worker,name='rate240-file-sink',daemon=True)
        try:self._thread.start()
        except BaseException:
            self._file.close()
            raise

    @property
    def pending_bytes(self):
        with self._condition:return self._pending_bytes

    @property
    def written_bytes(self):
        with self._condition:return self._written_bytes

    @property
    def high_water_bytes(self):
        with self._condition:return self._high_water_bytes

    @property
    def writer_alive(self):return self._thread.is_alive()

    def _raise_error(self):
        if isinstance(self._error,SinkQueueFull):
            raise SinkQueueFull(str(self._error)) from self._error
        if self._error is not None:
            raise SinkWriteError('Background output write/flush/close failed') from self._error

    def write(self,data):
        view=memoryview(data)
        size=view.nbytes
        with self._condition:
            self._raise_error()
            if self._closing:raise ValueError('Write to closed background file sink')
            if size>self.max_queue_bytes-self._pending_bytes:
                self._error=SinkQueueFull('Background output byte budget exhausted')
                self._closing=True
                self._condition.notify_all()
                self._raise_error()
            if not size:return 0
            # Snapshot mutable buffers only after checking the byte budget.
            self._queue.append(bytes(view))
            self._pending_bytes+=size
            self._high_water_bytes=max(self._high_water_bytes,self._pending_bytes)
            self._condition.notify()
        return size

    def _next_batch(self):
        with self._condition:
            while not self._queue and not self._closing:self._condition.wait()
            if not self._queue:return None
            deadline=time.monotonic()+self.flush_interval
            while not self._closing and self._pending_bytes<self.batch_bytes:
                remaining=deadline-time.monotonic()
                if remaining<=0:break
                self._condition.wait(remaining)
            parts=[];size=0
            while self._queue and size<self.batch_bytes:
                data=self._queue.popleft()
                count=min(len(data),self.batch_bytes-size)
                parts.append(data[:count]);size+=count
                if count<len(data):self._queue.appendleft(data[count:])
            return b''.join(parts)

    def _write_all(self,data):
        view=memoryview(data)
        while view:
            count=self._file.write(view)
            if type(count) is not int or not 0<count<=len(view):
                raise OSError('Background file write made no valid progress')
            view=view[count:]
            with self._condition:
                self._pending_bytes-=count
                self._written_bytes+=count

    def _record_error(self,error):
        with self._condition:
            if self._error is None:self._error=error
            else:self._error.add_note('Additional writer failure: '+repr(error))
            self._closing=True
            self._condition.notify_all()

    def _worker(self):
        try:
            while True:
                batch=self._next_batch()
                if batch is None:break
                self._write_all(batch)
            self._file.flush()
        except BaseException as error:
            self._record_error(error)
        finally:
            try:self._file.close()
            except BaseException as error:self._record_error(error)

    def close(self):
        with self._condition:
            self._closing=True
            self._condition.notify_all()
        self._thread.join(self.close_timeout)
        if self._thread.is_alive():
            error=TimeoutError(f'Background output close timed out; pending_bytes={self.pending_bytes}; '
                               'writer_alive=true; daemon retains the partial output until I/O returns')
            self._record_error(error)
            raise SinkWriteError(str(error)) from error
        with self._condition:self._raise_error()

    def __enter__(self):return self

    def __exit__(self,kind,error,traceback):
        if error is None:self.close()
        else:
            try:self.close()
            except BaseException as close_error:error.add_note('Output close also failed: '+repr(close_error))
        return False
