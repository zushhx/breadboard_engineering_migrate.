"""Back up the identified ESP32-S3 and run isolated, read-only TF probes.

Flash writes affect controller firmware only. No TF card write commands are sent.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import sys
import time

import serial
from serial.tools import list_ports

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "面包板样机_固件"
ESPTOOL = Path.home() / ".platformio/packages/tool-esptoolpy/esptool.py"
BOOT_APP0 = Path.home() / ".platformio/packages/framework-arduinoespressif32/tools/partitions/boot_app0.bin"
EXPECTED_MAC = "44:1b:f6:81:91:10"
PROTOCOL_ENVS = {"tf_1bit_20m", "tf_1bit_400k", "tf_4bit_400k", "tf_spi_1m", "tf_spi_4m",
                 "tf_arduino_spi_1m", "tf_arduino_spi_400k"}
IDF_ENVS = {"tf_idf542_4bit_20m"}


def stamp():
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S_%f")


def unique_port():
    ports = [p for p in list_ports.comports() if p.vid == 0x1A86 and p.pid == 0x7522]
    if len(ports) != 1:
        raise RuntimeError(f"Expected one CH340K controller, found {len(ports)}")
    return ports[0]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["backup", "flash", "capture", "restore"])
    parser.add_argument("--env", choices=["tf_wait_300ms", "tf_wait_5000ms", *sorted(PROTOCOL_ENVS | IDF_ENVS)])
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--seconds", type=float, default=15)
    parser.add_argument("--reset", action="store_true", help="One MCU EN reset before a capture; does not cut TF power")
    args = parser.parse_args()
    tag = stamp()
    log_path = PROJECT / "logs" / f"tf_software_{args.action}_{args.env or 'controller'}_{tag}.log"
    log_path.parent.mkdir(exist_ok=True)

    with log_path.open("x", encoding="utf-8") as log:
        def emit(message):
            print(message, flush=True)
            log.write(message + "\n")
            log.flush()

        def run_esptool(port, command):
            argv = [sys.executable, str(ESPTOOL), "--chip", "esp32s3", "--port", port,
                    "--baud", "921600", "--before", "default_reset", "--after", "hard_reset", *command]
            emit("ESPTOOL: " + repr(argv))
            result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    encoding="utf-8", errors="replace", timeout=180)
            emit(result.stdout)
            if result.returncode:
                raise RuntimeError(f"esptool exit code {result.returncode}")
            return result.stdout

        def check_identity(port):
            output = run_esptool(port, ["flash_id"])
            found = re.search(r"MAC:\s*([0-9a-fA-F:]{17})", output)
            if not found or found.group(1).lower() != EXPECTED_MAC:
                raise RuntimeError("Controller MAC mismatch; no flash writes permitted")
            if "Detected flash size: 8MB" not in output:
                raise RuntimeError("Expected detected 8 MB flash")
            emit("IDENTITY VERIFIED: " + EXPECTED_MAC)

        def validate_backup(path):
            data = path.read_bytes()
            if len(data) != 0x800000:
                raise RuntimeError("Backup must be exactly 8 MiB")
            manifest = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
            if manifest["mac"] != EXPECTED_MAC or manifest["sha256"] != sha(data):
                raise RuntimeError("Backup manifest mismatch")
            return data

        def capture(seconds, commands=b"i\nb\nl\n"):
            deadline = time.monotonic() + seconds
            port = None
            last_device = None
            queried = False
            opened_at = 0
            reset_pending = args.reset
            command_names = commands.decode("ascii").split()
            emit(f"CAPTURE: {seconds:g}s; DTR/RTS initially false; no automatic reset; commands={command_names}")
            try:
                while time.monotonic() < deadline:
                    if port is None:
                        try:
                            device = unique_port()
                            candidate = serial.Serial(port=None, baudrate=115200, timeout=0.15, write_timeout=2)
                            candidate.dtr = False
                            candidate.rts = False
                            candidate.port = device.device
                            candidate.open()
                            port = candidate
                            opened_at = time.monotonic()
                            queried = False
                            last_device = device.device
                            emit(f"SERIAL OPEN: {device.device}; {device.hwid}")
                            if reset_pending:
                                emit("MCU RESET: DTR false, RTS true 100 ms then false; card remains powered")
                                port.rts = True
                                time.sleep(0.1)
                                port.rts = False
                                reset_pending = False
                        except (RuntimeError, serial.SerialException, OSError):
                            time.sleep(0.25)
                            continue
                    try:
                        raw = port.read(4096)
                        if raw:
                            emit(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
                        if not queried and time.monotonic() - opened_at >= 10:
                            emit(f"SEND: {command_names} (read-only)")
                            port.write(commands)
                            port.flush()
                            queried = True
                    except (serial.SerialException, OSError) as exc:
                        emit(f"SERIAL DISCONNECTED ({last_device}): {exc}")
                        port.close()
                        port = None
                if port is not None:
                    emit(f"SEND: {command_names} (final read-only check)")
                    port.write(commands)
                    port.flush()
                    end = time.monotonic() + 3
                    while time.monotonic() < end:
                        raw = port.read(4096)
                        if raw:
                            emit(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
            finally:
                if port is not None:
                    port.close()

        emit(f"ACTION={args.action}; China time={tag}; TF write commands: NONE")
        emit("LOG: " + str(log_path))
        if args.action == "capture":
            capture(args.seconds)
            return

        device = unique_port()
        emit(f"DEVICE={device.device}; {device.description}; {device.hwid}")
        check_identity(device.device)
        if args.action == "backup":
            folder = PROJECT / "backups" / f"tf_software_{tag}"
            folder.mkdir(parents=True, exist_ok=False)
            path = folder / "esp32s3_full_8MiB.bin"
            run_esptool(device.device, ["read_flash", "0x0", "0x800000", str(path)])
            data = path.read_bytes()
            if len(data) != 0x800000:
                raise RuntimeError("Flash backup size incorrect")
            partitions = []
            for pos in range(0x8000, 0x8000 + 0xC00, 32):
                if data[pos:pos + 2] != b"\xAA\x50":
                    break
                _, kind, sub, offset, size, label, flags = struct.unpack("<HBBII16sI", data[pos:pos + 32])
                partitions.append(dict(type=kind, subtype=sub, offset=offset, size=size,
                                       label=label.rstrip(b"\0").decode("ascii"), flags=flags))
            manifest = dict(mac=EXPECTED_MAC, bytes=len(data), sha256=sha(data),
                            china_time=tag, partitions=partitions,
                            otadata_sector0=data[0xE000:0xE020].hex(),
                            otadata_sector1=data[0xF000:0xF020].hex())
            path.with_suffix(".json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            emit("BACKUP VERIFIED: " + str(path))
            emit(json.dumps(manifest, ensure_ascii=False, indent=2))
        elif args.action == "flash":
            if not args.env or not args.backup:
                raise RuntimeError("flash requires --env and --backup")
            old = validate_backup(args.backup)
            build_root = ".pio/tf_idf_probe" if args.env in IDF_ENVS else (".pio/tf_protocol_probe" if args.env in PROTOCOL_ENVS else ".pio/tf_software_probe")
            build = PROJECT / build_root / args.env
            table = (build / "partitions.bin").read_bytes()
            if old[0x8000:0x8000 + len(table)] != table:
                raise RuntimeError("Partition table differs; app-only update stopped")
            firmware = build / "firmware.bin"
            image = firmware.read_bytes()
            if len(image) > 0x330000 or image[0] != 0xE9:
                raise RuntimeError("Invalid/oversize firmware")
            emit(f"FIRMWARE={firmware}; bytes={len(image)}; sha256={sha(image)}")
            segments = []
            if args.env in IDF_ENVS:
                bootloader = build / "bootloader.bin"
                if not 0 < bootloader.stat().st_size <= 0x8000:
                    raise RuntimeError("Invalid bootloader size")
                segments += ["0x0", str(bootloader)]
                emit("Writing controller bootloader, app0 and default OTA selection; preserving partitions, NVS, SPIFFS")
            else:
                emit("Writing controller app0 and default OTA selection; preserving bootloader, partitions, NVS, SPIFFS")
            segments += ["0xe000", str(BOOT_APP0), "0x10000", str(firmware)]
            run_esptool(device.device, ["write_flash", "--flash_mode", "keep", "--flash_freq", "keep",
                                       "--flash_size", "keep", *segments])
            emit("FLASH DONE; following capture is a warm reset, not card power removal")
            capture(args.seconds)
        elif args.action == "restore":
            if not args.backup:
                raise RuntimeError("restore requires --backup")
            old = validate_backup(args.backup)
            segments = []
            for name, offset, size in [("original_bootloader_region.bin", 0, 0x8000),
                                       ("original_otadata.bin", 0xE000, 0x2000),
                                       ("original_app0_partition.bin", 0x10000, 0x330000)]:
                path = args.backup.parent / name
                content = old[offset:offset + size]
                if path.exists() and path.read_bytes() != content:
                    raise RuntimeError("Existing rollback segment differs")
                path.write_bytes(content)
                emit(f"ROLLBACK={path}; offset=0x{offset:X}; bytes={size}; sha256={sha(content)}")
                segments += [hex(offset), str(path)]
            emit("Restoring original bootloader, OTA selection and app0 directly from verified controller backup")
            run_esptool(device.device, ["write_flash", "--flash_mode", "keep", "--flash_freq", "keep",
                                       "--flash_size", "keep", *segments])
            emit("RESTORED: original running firmware regions; no TF write commands sent by this host helper")
            capture(args.seconds, commands=b"i\nl\n")
        emit("LOG: " + str(log_path))


if __name__ == "__main__":
    main()
