"""Compile the production network implementation with host SDK/NVS boundaries.

No serial, network connection, board NVS or card operations. Requires gcc and g++.
Uses byte-pinned cJSON from the same ESP-IDF SDK; no host JSON development package.
"""
from pathlib import Path
import subprocess
import tempfile
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / '面包板样机_固件/diagnostics/sensor_web_recorder/src'
VENDOR = ROOT / 'tests/vendor/cjson'


def run():
    record = json.loads((VENDOR / 'manifest.json').read_text(encoding='utf-8'))
    for name, digest in record['sha256'].items():
        if hashlib.sha256((VENDOR / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError('Pinned test cJSON changed: ' + name)
    with tempfile.TemporaryDirectory(prefix='controller-network-host-') as folder:
        out = Path(folder)
        for name in ('esp_wifi.h', 'esp_event.h', 'esp_netif.h', 'esp_mac.h',
                     'esp_random.h', 'esp_timer.h', 'nvs_flash.h', 'nvs.h',
                     'freertos/FreeRTOS.h', 'freertos/semphr.h'):
            path = out / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('#include "network_sdk_stubs.h"\n')
        (out / 'cJSON.h').write_text('#include "vendor/cjson/cJSON.h"\n')
        (out / 'wifi_credentials.h').write_text('#define WIFI_SSID ""\n#define WIFI_PASSWORD ""\n')
        binary = out / 'network'
        library = out / 'cJSON.o'
        subprocess.run(['gcc', '-std=c99', '-Wall', '-Wextra', '-Werror', '-c',
                        str(VENDOR / 'cJSON.c'), '-o', str(library)], check=True)
        subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                        '-I' + str(SRC), '-I' + str(out), '-I' + str(ROOT / 'tests'),
                        str(ROOT / 'tests/controller_network_host.cpp'), str(library), '-lm',
                        '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    run()
