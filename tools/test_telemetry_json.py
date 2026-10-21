"""Exercise production bounded telemetry renderers with C++ heap allocations denied.

No serial, sockets, board reads/writes or build artifacts outside a temporary folder.
"""
from pathlib import Path
import json
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]


def run():
    with tempfile.TemporaryDirectory(prefix='telemetry-json-') as folder:
        binary=Path(folder)/'telemetry'
        subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror',
                        '-I'+str(ROOT/'面包板样机_固件/diagnostics/sensor_web_recorder/src'),
                        str(ROOT/'tests/telemetry_json_host.cpp'),'-o',str(binary)],check=True)
        out=subprocess.check_output([str(binary)],text=True,encoding='utf-8',timeout=30)
    lines=dict(line.split('=',1) for line in out.splitlines())
    empty,status,trend=(json.loads(lines[name]) for name in ('EMPTY','STATUS','TREND'))
    assert empty['phase']=='idle' and empty['preview']==[] and not empty['files_busy']
    assert 'INA精度未验收' in empty['detail']
    assert set(empty)==set(status)
    expected={'boot','phase','ready','connected','ip','card_serial','card_bytes','space_valid','free_bytes',
              'run','seconds','written','elapsed_us','errors','queue','fifo_high','queue_high','write_us',
              'sync_us','free_heap','ina_valid','ina_us','bus_mv','current_ua','power_uw','detail','sha256',
              'record_name','record_path','preview_seq','preview_us','preview','owner','pending_owner','files_busy'}
    assert set(status)==expected
    assert status['phase']=='recording' and status['ready'] and status['connected'] and status['files_busy']
    assert status['card_serial']=='FFFFFFFF' and status['card_bytes']==status['free_bytes']=='18446744073709551615'
    assert status['written']==status['elapsed_us']==18446744073709551615
    assert status['bus_mv']==2147483647 and status['current_ua']==-2147483648
    assert status['owner']=='b'*32 and status['pending_owner']=='c'*32
    assert len(status['preview'])==272 and all(row[:2]==[-32768,32767] for row in status['preview'])
    assert set(trend)=={'boot','run','sample_rate','window_samples','points'}
    assert trend['sample_rate']==26667 and trend['window_samples']==5333
    assert len(trend['points'])==150 and all(row==[4294967295]*5 for row in trend['points'])
    assert 'DENIED_ALLOCATION_ATTEMPTS=0' in out
    print('Production telemetry: full JSON schema, maximum values, bounds, and 1000 allocation-denied repetitions passed')
    print('CHECKS='+lines['CHECKS'])


if __name__=='__main__':
    run()
