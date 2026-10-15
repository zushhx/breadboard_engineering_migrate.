"""Read IIS3DWB registers and stationary data from the unique CH340K board.

Requires the diagnostic firmware with the 'j' command. Never starts recording,
changes calibration, resets the board, or sends storage commands.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import sys
import time

import serial
from serial.tools import list_ports


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pose-label", default="not recorded", help="Physical orientation recorded by the operator")
    parser.add_argument("--rounds", type=int, choices=(1, 2, 3), default=3)
    args = parser.parse_args()
    ports = [p for p in list_ports.comports() if p.vid == 0x1A86 and p.pid == 0x7522]
    if len(ports) != 1:
        raise SystemExit(f"Expected one CH340K board, found {len(ports)}; no commands sent.")
    device = ports[0]
    logs = Path(__file__).resolve().parents[1] / "logs"
    logs.mkdir(exist_ok=True)
    log = logs / ("iis3dwb_readcheck_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + device.device + ".log")
    failed = None
    rounds = []
    register_count = 0
    cal_ok = False
    with log.open("x", encoding="utf-8") as output:
        def emit(value):
            # Self-report includes the prototype WiFi password; it is not needed here.
            value = re.sub(r"(\b密码\s+)\S+", r"\1[redacted]", value)
            print(value, flush=True)
            output.write(value + "\n")
            output.flush()

        def collect(port, seconds):
            end = time.monotonic() + seconds
            received = bytearray()
            while time.monotonic() < end:
                received.extend(port.read(4096))
            return received.decode("utf-8", errors="replace")

        def command(port, key, seconds):
            emit(f"--- Sent {key} at {datetime.now().isoformat(timespec='seconds')} ---")
            port.write((key + "\n").encode("ascii"))
            port.flush()
            reply = collect(port, seconds)
            emit(reply)
            return reply

        def registers(port):
            nonlocal register_count
            reply = command(port, "j", 1)
            checks = re.findall(
                r"\[iischeck\] WHO=([0-9A-F]{2}) CTRL1=([0-9A-F]{2}) CTRL3=([0-9A-F]{2}) "
                r"CTRL6=([0-9A-F]{2}) STATUS=([0-9A-F]{2}) identity=(OK|FAIL) config=(OK|FAIL)",
                reply,
            )
            if len(checks) != 1 or checks[0][5:] != ("OK", "OK"):
                raise RuntimeError("Realtime sensor identity/configuration missing or invalid; remaining commands skipped.")
            register_count += 1

        emit(f"Device: {device.device}; {device.description}; {device.hwid}")
        emit(f"Pose: {args.pose_label}")
        emit("Read-only sensor baseline; not a sensitivity, bandwidth, or continuous-recording acceptance test.")
        try:
            with serial.Serial(port=None, baudrate=115200, timeout=0.2, write_timeout=1) as port:
                port.dtr = False
                port.rts = False
                port.port = device.device
                port.open()
                emit("--- Initial output ---\n" + collect(port, 2))
                status = command(port, "i", 3)
                cal_ok = "CAL=2048 (设定 2048)" in status
                if not cal_ok:
                    emit("INA226 calibration is unconfirmed; current/power data must not be used.")
                for round_number in range(1, args.rounds + 1):
                    emit(f"--- Stationary round {round_number}/{args.rounds} ---")
                    for _ in range(3):
                        registers(port)
                    reply = command(port, "v", 3)
                    samples = re.findall(r"^[-+]?\d+\.\d+\t[-+]?\d+\.\d+\t[-+]?\d+\.\d+\t\d+\.\d+\r?$", reply, re.MULTILINE)
                    if not samples:
                        raise RuntimeError("No acceleration rows received; remaining commands skipped.")
                    reply = command(port, "k", 6)
                    count = re.search(r"有效样本 (\d+) 个", reply)
                    if not count or int(count.group(1)) != 400 or "本次仅记录静置基线" not in reply:
                        raise RuntimeError("Stationary summary incomplete or diagnostic firmware unconfirmed; remaining commands skipped.")
                    mean = re.search(
                        r"平均加速度\s+X=([+-]?\d+\.\d+)\s+Y=([+-]?\d+\.\d+)\s+Z=([+-]?\d+\.\d+)\s+\(g\)",
                        reply,
                    )
                    magnitude = re.search(r"合模量 \|a\|\s+(\d+\.\d+) g", reply)
                    if not mean or not magnitude:
                        raise RuntimeError("Stationary values missing; remaining commands skipped.")
                    rounds.append({
                        "samples": int(count.group(1)),
                        "mean_g": dict(zip(("X", "Y", "Z"), map(float, mean.groups()))),
                        "magnitude_g": float(magnitude.group(1)),
                    })
                    registers(port)
                emit("Capture complete; assess the logged values with the actual physical orientation.")
        except (serial.SerialException, OSError, RuntimeError) as error:
            failed = str(error)
            emit("STOP: " + failed)
    print(f"Saved: {log}", flush=True)
    if failed:
        raise SystemExit(1)
    report = {
        "source": str(log), "pose": args.pose_label,
        "register_checks": register_count, "register_checks_all_OK": True,
        "INA_CAL_2048_reported": cal_ok, "rounds": rounds,
        "interpretation": "Short stationary baseline only. Does not establish sensitivity, noise, bandwidth, or intermittent-contact acceptance.",
    }
    report_path = log.with_suffix(".json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Summary: {report_path}", flush=True)


if __name__ == "__main__":
    main()
