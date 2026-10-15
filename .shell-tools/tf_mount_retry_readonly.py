"""Check one CH340K controller and retry an unmounted TF card once."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import sys
import time

import serial
from serial.tools import list_ports


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    devices = [p for p in list_ports.comports() if p.vid == 0x1A86 and p.pid == 0x7522]
    if len(devices) != 1:
        raise SystemExit(f"Expected one CH340K controller, found {len(devices)}; no commands sent.")
    device = devices[0]
    workspace = Path(__file__).resolve().parents[1]
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S_%f")
    log_path = workspace / "面包板样机_固件" / "logs" / f"tf_wsl_mount_retry_{stamp}_{device.device}.log"
    mounted = False

    with log_path.open("x", encoding="utf-8") as log:
        def emit(value):
            print(value, flush=True)
            log.write(value + "\n")
            log.flush()

        def collect(port, seconds):
            end = time.monotonic() + seconds
            data = bytearray()
            while time.monotonic() < end:
                data.extend(port.read(4096))
            return data.decode("utf-8", errors="replace")

        def query(port, command, seconds):
            emit(f"--- Sent {command} ---")
            port.write((command + "\n").encode("ascii"))
            port.flush()
            reply = collect(port, seconds)
            emit(reply)
            return reply

        emit(f"WSL entry; user-requested read-only mount retry; time={stamp}")
        emit(f"Device: {device.device}; {device.description}; {device.hwid}")
        emit(f"Saved: {log_path}")
        with serial.Serial(port=None, baudrate=115200, timeout=0.2, write_timeout=2) as port:
            port.dtr = False
            port.rts = False
            port.port = device.device
            port.open()
            emit("--- Initial output ---\n" + collect(port, 1))
            status = query(port, "i", 3)
            mounted = bool(re.search(r"TF.*SDMMC.*:\s*OK", status))
            if not mounted:
                emit("--- Sent r ---")
                port.write(b"r\n")
                port.flush()
                end = time.monotonic() + 45
                complete = False
                while time.monotonic() < end:
                    raw = port.readline()
                    if not raw:
                        continue
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    emit(line)
                    if line.startswith(("[sdprobe] done", "[sdprobe] already mounted", "[sdprobe] busy")):
                        complete = True
                        break
                if not complete:
                    emit("HOST STOP: probe did not complete in 45 seconds; no follow-up commands sent.")
                    return 2
                status = query(port, "i", 3)
                mounted = bool(re.search(r"TF.*SDMMC.*:\s*OK", status))
            else:
                emit("HOST: already mounted; reinitialization unnecessary.")
            directory = query(port, "l", 3)
            if mounted and "TF 卡未挂载" not in directory and directory.strip():
                emit("HOST RESULT: mounted; live root-directory command returned output.")
            else:
                mounted = False
                emit("HOST RESULT: mounted directory access not confirmed.")
        emit(f"Saved: {log_path}")
    return 0 if mounted else 1


if __name__ == "__main__":
    raise SystemExit(main())
