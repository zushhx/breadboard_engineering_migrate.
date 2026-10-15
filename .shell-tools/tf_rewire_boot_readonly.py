import sys
import time
from pathlib import Path
from datetime import datetime
import serial
from serial.tools import list_ports

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ports = [p for p in list_ports.comports() if p.vid == 0x1A86 and p.pid == 0x7522]
if len(ports) != 1:
    raise SystemExit(f'Expected one CH340K, found {len(ports)}; stopped')
logpath = Path(__file__).resolve().parents[1] / '面包板样机_固件' / 'logs' / ('tf_rewire_boot_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.log')
with logpath.open('x', encoding='utf-8') as log:
    def emit(s):
        print(s, flush=True)
        log.write(s + '\n')
        log.flush()
    def collect(port, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            data = port.read(4096)
            if data:
                emit(data.decode('utf-8', errors='replace').rstrip())
    emit(f'LOG: {logpath}\nDEVICE: {ports[0].device} {ports[0].hwid}\nController warm reset; card power not removed; no TF write or flash commands.')
    with serial.Serial(port=None, baudrate=115200, timeout=0.2, write_timeout=2) as port:
        port.dtr = False
        port.rts = False
        port.port = ports[0].device
        port.open()
        port.rts = True
        time.sleep(0.1)
        port.reset_input_buffer()
        port.rts = False
        collect(port, 16)
        for command in ['i', 'b', 'l']:
            emit('SEND: ' + command)
            port.write((command + '\n').encode('ascii'))
            port.flush()
            collect(port, 3)
