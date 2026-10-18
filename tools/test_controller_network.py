"""Compile the production network implementation with host SDK/NVS boundaries.

No serial, network connection, board NVS or card operations. Requires g++ and libcjson-dev.
"""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / '面包板样机_固件/diagnostics/sensor_web_recorder/src'


def run():
    with tempfile.TemporaryDirectory(prefix='controller-network-host-') as folder:
        out = Path(folder)
        for name in ('esp_wifi.h', 'esp_event.h', 'esp_netif.h', 'esp_mac.h',
                     'esp_random.h', 'esp_timer.h', 'nvs_flash.h', 'nvs.h',
                     'freertos/FreeRTOS.h', 'freertos/semphr.h'):
            path = out / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('#include "network_sdk_stubs.h"\n')
        (out / 'cJSON.h').write_text('#include <cjson/cJSON.h>\n')
        (out / 'wifi_credentials.h').write_text('#define WIFI_SSID ""\n#define WIFI_PASSWORD ""\n')
        binary = out / 'network'
        subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                        '-I' + str(SRC), '-I' + str(out), '-I' + str(ROOT / 'tests'),
                        str(ROOT / 'tests/controller_network_host.cpp'), '-lcjson',
                        '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    run()
