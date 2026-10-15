"""Read the current board's self-test and INA226 values without interactive input."""

import sys
import time

import serial
from serial.tools import list_ports

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def collect(port, seconds):
    end = time.monotonic() + seconds
    data = bytearray()
    while time.monotonic() < end:
        chunk = port.read(4096)
        if chunk:
            data.extend(chunk)
    return data.decode("utf-8", errors="replace")


def main():
    if len(sys.argv) > 1:
        port_name = sys.argv[1]
    else:
        ports = list(list_ports.comports())
        candidates = [
            port for port in ports
            if "BTHENUM" not in port.hwid.upper()
            and "BLUETOOTH" not in port.description.upper()
        ]
        uart_ports = [port for port in candidates if "CH340K" in port.description.upper()]
        if len(uart_ports) == 1:
            candidates = uart_ports
        if len(candidates) != 1:
            print("Available serial ports:")
            for port in ports:
                print(f"  {port.device}: {port.description} [{port.hwid}]")
            print("No unique USB serial port found. Reconnect the board with a data USB cable.")
            return 1
        port_name = candidates[0].device
        print(f"Using {port_name}: {candidates[0].description}")
    try:
        with serial.Serial(port_name, 115200, timeout=0.2, write_timeout=1) as port:
            time.sleep(1)
            port.reset_input_buffer()
            for command, seconds in (("i", 2), ("w", 1), ("c", 3)):
                print(f"\n--- Sent {command} ---", flush=True)
                port.write((command + "\n").encode("ascii"))
                port.flush()
                output = collect(port, seconds)
                print(output if output else "No reply received.", flush=True)
    except serial.SerialException as exc:
        print(f"Cannot open {port_name}: {exc}")
        print("Close any other serial monitor and run this again.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
