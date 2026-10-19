"""Host regression for production HTTP lifecycle policy; no board access."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / '面包板样机_固件/diagnostics/sensor_web_recorder/src'

def run():
    with tempfile.TemporaryDirectory(prefix='http-policy-') as folder:
        binary = Path(folder) / 'http-policy'
        subprocess.run(['g++', '-std=c++17', '-pthread', '-Wall', '-Wextra', '-Werror',
                        '-I'+str(ROOT/'tests/http_policy_stubs'), '-I'+str(SOURCE),
                        str(ROOT/'tests/http_response_policy_host.cpp'), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True, timeout=30)
    # Boundary guard: only rejected upgrades use the ordinary response helpers.
    # Successful upgrades and all frame handling keep their persistent socket.
    web = (SOURCE/'web_control.cpp').read_text(encoding='utf-8')
    live = web.split('static esp_err_t liveHandler(', 1)[1].split('static boundedhttp::PendingWork', 1)[0]
    accepted = live.split('slot->fd=fd;', 1)[1]
    assert 'return ESP_OK;' in accepted and 'boundedhttp::' not in accepted
    assert 'config.lru_purge_enable=false' in web and 'config.max_open_sockets=9' in web
    assert 'heartbeatPending.schedule(' in web and 'heartbeatPending.complete(token);' in web
    print('WebSocket persistent-path and fixed socket-budget integration guards passed')

if __name__ == '__main__':
    run()
