"""Run the board's synthetic SD soak test, keeping its full serial evidence."""
import argparse
from datetime import datetime
from pathlib import Path
import sys
import time
import serial


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM5")
    parser.add_argument("--seconds", type=int, default=600)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 600:
        parser.error("seconds must be 1..600")
    logdir = Path(__file__).resolve().parents[1] / "logs"
    logdir.mkdir(exist_ok=True)
    logpath = logdir / ("sd_soak_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".log")
    print(f"Evidence: {logpath}", flush=True)
    with logpath.open("w", encoding="utf-8") as log:
        def report(line):
            print(line, flush=True)
            log.write(line + "\n")
            log.flush()
        with serial.Serial(args.port, 115200, timeout=0.5, write_timeout=2) as port:
            time.sleep(2)
            port.reset_input_buffer()
            command = f"p soak {args.seconds}\n"
            report(f"Command: {command.strip()} on {args.port}, {datetime.now().isoformat()}")
            port.write(command.encode("ascii"))
            deadline = time.monotonic() + args.seconds + 180
            cancel_deadline = None
            last_output = time.monotonic()
            while True:
                try:
                    line = port.readline().decode("utf-8", errors="replace").strip()
                except KeyboardInterrupt:
                    port.write(b"x\n")
                    report("Host requested cancellation; waiting for file cleanup.")
                    cancel_deadline = time.monotonic() + 20
                    continue
                if line:
                    report(line)
                    last_output = time.monotonic()
                    if line.startswith("[sdsoak] PASS"):
                        return 0 if cancel_deadline is None else 1
                    if line.startswith(("[sdsoak] FAIL", "[sdsoak] CANCELLED")):
                        return 1
                now = time.monotonic()
                if cancel_deadline is None and (now > deadline or now - last_output > 90):
                    port.write(b"x\n")
                    report("Host timeout: cancellation requested; result is NOT a pass.")
                    cancel_deadline = now + 20
                if cancel_deadline is not None and now > cancel_deadline:
                    report("No confirmed cleanup reply; test status unknown. Do not remove the card yet.")
                    return 2


if __name__ == "__main__":
    raise SystemExit(main())
