"""Record the selected read-only TF firmware from the completed board evidence."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
project = root / "面包板样机_固件"
evidence = [
    ("user_confirmed_cold_start_1", "tf_software_capture_controller_20261015_153147_355088.log"),
    ("mcu_en_reset_card_power_retained", "tf_software_capture_controller_20261015_153255_128860.log"),
    ("user_confirmed_cold_start_2", "tf_software_capture_controller_20261015_153406_884020.log"),
]
results = []
for kind, filename in evidence:
    path = project / "logs" / filename
    text = path.read_text(encoding="utf-8")
    expected = ["CID=SU08G", "bytes=7948206080", "signature=55-AA", "root open=0",
                "sample reads consistent=1 rejected_card_writes=0"]
    if any(value not in text for value in expected) or text.count("PC_MATCH=1") < 2:
        raise RuntimeError("Successful read evidence incomplete: " + str(path))
    if kind.startswith("mcu_") and ("MCU RESET:" not in text or "IDF=5.4.2" not in text):
        raise RuntimeError("MCU reset/version evidence missing")
    results.append({"type": kind, "log": str(path.relative_to(project)),
                    "matching_sample_reads": text.count("PC_MATCH=1")})

build = project / ".pio/tf_idf_probe/tf_idf542_4bit_20m"
artifacts = []
for filename in ("firmware.bin", "bootloader.bin", "partitions.bin"):
    path = build / filename
    data = path.read_bytes()
    artifacts.append({"path": str(path.relative_to(project)), "bytes": len(data),
                      "sha256": hashlib.sha256(data).hexdigest()})
if artifacts[0]["sha256"] != "60b50aac7657f47fe375751bcc11f8e321247dffbe44415af9af846ab7c07fb8":
    raise RuntimeError("Selected firmware differs from tested upload")

selection = {
    "recorded_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
    "selected_environment": "tf_idf542_4bit_20m",
    "framework": "ESP-IDF 5.4.2",
    "controller_mac": "44:1b:f6:81:91:10",
    "role": "read_only_tf_diagnostic",
    "configuration": {"protocol": "SDMMC", "width": 4, "max_frequency_khz": 20000,
                      "startup_wait_ms": 5000, "uart_baud": 115200,
                      "clk": 38, "cmd": 39, "d0": 40, "d1": 41, "d2": 42, "d3": 47},
    "verified_card": {"cid_name": "SU08G", "serial_hex": "10044BA5", "ocr_hex": "C0FF8000",
                      "rca_hex": "E624", "sectors": 15523840, "sector_bytes": 512, "bytes": 7948206080,
                      "lba0_fnv1a_hex": "1410CC45"},
    "verified_sample": {"bytes": 18327,
                        "sha256": "a8014b2c5ca28eb98c9eaee6de86c76c2256bec875f0f90860ece355b1670b86",
                        "matches_pc_reader_record": True,
                        "matching_reads_in_selected_logs": sum(item["matching_sample_reads"] for item in results)},
    "evidence": results,
    "artifacts": artifacts,
    "build_entry": ".shell-tools/build_tf_idf_probe_pwsh7.ps1 (relative to workspace root)",
    "host_capture_entry": ".shell-tools/run_tf_software_trials_pwsh7.ps1 -Action capture (relative to workspace root)",
    "original_backup": "backups/tf_software_20261015_125041_550332/esp32s3_full_8MiB.bin",
    "original_business_firmware_restored": False,
    "scope": {"card_data_writes_tested": False, "usb_msc_implemented": False,
              "original_acquisition_wifi_integrated": False, "long_term_stability_tested": False},
    "interpretation": "Selected after two user-confirmed full power cycles and one MCU reset; does not isolate a single SDK bug or prove long-term hardware stability."
}
output = project / "diagnostics/tf_selected_firmware.json"
output.write_text(json.dumps(selection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
inventory_path = project / "logs/tf_software_artifacts_20261015.json"
inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
inventory["latest_status"] = "Selected IDF 5.4.2 read-only firmware: two cold starts and one MCU reset passed; directory and sample hash match PC; original business firmware remains backed up."
inventory["selection_record"] = str(output.relative_to(project))
inventory_path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("Selected: " + str(output))
print("Verified matching sample reads: " + str(selection["verified_sample"]["matching_reads_in_selected_logs"]))
