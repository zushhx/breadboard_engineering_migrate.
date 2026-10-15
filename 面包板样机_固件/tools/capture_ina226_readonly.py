"""Read one INA226 snapshot by default; opt into timed observation with --seconds."""
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
    parser.add_argument(
        "--seconds", type=int, choices=(10, 30, 90, 180), default=None,
        help="Opt into a timed observation; omitted reads one snapshot only",
    )
    args = parser.parse_args()
    snapshot_only = args.seconds is None
    ports = [p for p in list_ports.comports() if p.vid == 0x1A86 and p.pid == 0x7522]
    if len(ports) != 1:
        raise SystemExit(f"Expected one CH340K board, found {len(ports)}; no commands sent.")
    device = ports[0]
    logs = Path(__file__).resolve().parents[1] / "logs"
    logs.mkdir(exist_ok=True)
    log = logs / (("ina226_snapshot_" if snapshot_only else "ina226_retention_")
                  + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + device.device + ".log")
    observations = []
    failure = None
    elapsed = 0.0
    with log.open("x", encoding="utf-8") as output:
        def emit(value):
            print(value, flush=True)
            output.write(value + "\n")
            output.flush()

        emit(f"Device: {device.device}; {device.description}; {device.hwid}")
        if snapshot_only:
            emit("One read-only 'u' snapshot; no retention wait, reset or register writes.")
        else:
            emit(f"Read-only 'u' snapshots every ~2 s for {args.seconds} s; no reset or register writes.")
        try:
            with serial.Serial(port=None, baudrate=115200, timeout=0.1, write_timeout=1) as port:
                port.dtr = False
                port.rts = False
                port.port = device.device
                port.open()
                # Drain startup output without copying credentials into the diagnosis log.
                drain_end = time.monotonic() + 2
                while time.monotonic() < drain_end:
                    port.read(4096)
                started = time.monotonic()
                while snapshot_only or time.monotonic() - started < args.seconds:
                    sent = time.monotonic()
                    stamp = datetime.now().isoformat(timespec="seconds")
                    port.write(b"u\n")
                    port.flush()
                    received = bytearray()
                    while time.monotonic() - sent < 1:
                        received.extend(port.read(4096))
                    reply = received.decode("utf-8", errors="replace")
                    matches = re.findall(
                        r"\[inacheck\] MANUF=([0-9A-F]{4}) DIE=([0-9A-F]{4}) CONFIG=([0-9A-F]{4}) "
                        r"CAL=(\d+) identity=(OK|FAIL) config=(OK|FAIL) calibration=(OK|FAIL)", reply,
                    )
                    if len(matches) != 1:
                        raise RuntimeError("INA snapshot missing or ambiguous; stopped without resetting or writing calibration.")
                    m = matches[0]
                    observation = {
                        "time": stamp, "elapsed_s": round(sent - started, 3),
                        "MANUF": m[0], "DIE": m[1], "CONFIG": m[2], "CAL": int(m[3]),
                        "identity": m[4], "config": m[5], "calibration": m[6],
                    }
                    observations.append(observation)
                    emit(f"{stamp} +{sent - started:.1f}s " + " ".join(f"{k}={v}" for k, v in observation.items() if k not in ("time", "elapsed_s")))
                    if m[:4] != ("5449", "2260", "4527", "2048") or m[4:] != ("OK", "OK", "OK"):
                        raise RuntimeError("Identity/configuration/calibration differs from the initialized state; retain this fault for diagnosis.")
                    if snapshot_only:
                        break
                    time.sleep(max(0, 2 - (time.monotonic() - sent)))
                elapsed = time.monotonic() - started
        except (serial.SerialException, OSError, RuntimeError) as error:
            failure = str(error)
            elapsed = time.monotonic() - started if "started" in locals() else 0.0
            emit("STOP: " + failure)
        if not failure:
            if snapshot_only:
                emit("Single snapshot complete. No time-retention or measurement-accuracy acceptance.")
            else:
                emit(f"Short observation complete: {len(observations)} snapshots over {elapsed:.1f} s. Not a voltage/current accuracy or long-term stability acceptance.")
    report = {
        "source": str(log), "requested_seconds": args.seconds,
        "check_kind": "single_snapshot" if snapshot_only else "timed_observation",
        "requested_snapshots": 1 if snapshot_only else None,
        "elapsed_seconds": round(elapsed, 3), "failure": failure,
        "observation_completed": failure is None, "observations": observations,
    }
    report_path = log.with_suffix(".json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved: {log}\nSummary: {report_path}", flush=True)
    if failure:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
