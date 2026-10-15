"""Capture status and optional read-only mount/directory checks from one CH340K board."""
import argparse
from datetime import datetime
from pathlib import Path
import sys
import time

import serial
from serial.tools import list_ports

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
parser = argparse.ArgumentParser()
parser.add_argument("--probe", action="store_true", help="Also run the read-only r mount probe")
parser.add_argument("--list", action="store_true", help="Also read the card root directory")
parser.add_argument("--reset", action="store_true", help="Reset the board via RTS and capture boot diagnostics")
args = parser.parse_args()
ports = [p for p in list_ports.comports() if p.vid == 0x1A86 and p.pid == 0x7522]
if len(ports) != 1:
    raise SystemExit(f"Expected one CH340K board, found {len(ports)}; no commands sent.")
device = ports[0]
log = Path(__file__).resolve().parents[1] / "logs" / (
    ("tf_readcheck_" if args.list else "tf_probe_" if args.probe else "tf_status_")
    + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + device.device + ".log"
)
with log.open("x", encoding="utf-8") as output:
    def emit(text):
        print(text, flush=True)
        output.write(text + "\n")
        output.flush()

    def collect(port, seconds):
        end = time.monotonic() + seconds
        received = bytearray()
        while time.monotonic() < end:
            received.extend(port.read(4096))
        return received.decode("utf-8", errors="replace")

    emit(f"Device: {device.device}; {device.description}; {device.hwid}")
    with serial.Serial(port=None, baudrate=115200, timeout=0.2, write_timeout=1) as port:
        port.dtr = False
        port.rts = False
        port.port = device.device
        port.open()
        if args.reset:
            # CH340K RTS controls EN; DTR stays inactive so GPIO0 is not forced low.
            # Match esptool's non-USB HardReset sequence and retain startup logs.
            port.rts = True
            time.sleep(0.1)
            port.reset_input_buffer()
            port.rts = False
            emit("--- Board reset; boot output ---\n" + collect(port, 6))
        else:
            emit("--- Initial output ---\n" + collect(port, 2))
        commands = (("i", 3), ("r", 12), ("i", 2), ("c", 3)) if args.probe else (("i", 3), ("c", 3))
        if args.list:
            commands = commands[:-1] + (("l", 3),) + commands[-1:]
        for command, seconds in commands:
            emit(f"--- Sent {command} ---")
            port.write((command + "\n").encode("ascii"))
            port.flush()
            reply = collect(port, seconds)
            emit(reply)
            if args.probe and command == "i" and "CAL=2048 (设定 2048)" not in reply:
                raise SystemExit("Expected calibrated self-report missing; remaining commands skipped.")
print(f"Saved: {log}")
