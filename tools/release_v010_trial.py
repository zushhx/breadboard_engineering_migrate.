"""Bound release network-repair app0 trial with two 3s smoke records.

This is an initial candidate smoke check, not the full v0.1.0 acceptance matrix.
"""
from pathlib import Path
import argparse
import datetime
import json
import os
import subprocess
import sys
import time
from urllib.request import urlopen

from project_check import ROOT, BUNDLE, sha
from operation_gate import require_approval
from ui_live_trial import TrialSession
import tf_write_batch as tf
import sensor_recorder_batch as recorder
import parse_sensor_record

SOURCE = '3a365412d55320f1508b3e24e8d19c8d3582bdeb'
CANDIDATE = '91c2c9d153dd35d05b9dc5856f68080fbdb300f1d43b8a3df6252e3e87df029c'
OLD_SOURCE = '6ec57c38b1c73159ac987a34541ae0e5da681eab'
OLD_SHA = '86a1c917f1267005d4de103fffdfb0162ad93691f7f37d4cb9be286a66407789'
MANIFEST = ROOT/'local-data/build/sensor-web/f1409ecdf87fc74d/20261018_164522_110269/build_manifest.json'
BASELINE = ROOT/'local-data/baselines/release-v010/manifest.json'
HOST = ROOT/'local-data/release-v010/device-host/DeviceHost.exe'
BROWSER = ROOT/'tools/release_v010_live.cjs'
NODE = Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
OPERATION = 'release-v010-network-repair-smoke'
SCOPE = dict(app0_only=True, backup_end=0x122000, backup_nvs=True, records=2, seconds_each=3,
             preserve_existing_files=True, delete_format_repair=False, change_network=False,
             restore_on_failure=True, cold_starts=0, main_merge_release=False)

def inputs():
    build=json.loads(MANIFEST.read_text(encoding='utf-8'))
    baseline=json.loads(BASELINE.read_text(encoding='utf-8-sig'))
    retained=next(x for x in baseline['files'] if x['name']=='last-verified-firmware')
    old=Path(retained['path']).resolve()
    old_manifest=old.parent.parent/'build_manifest.json'
    old_build=json.loads(old_manifest.read_text(encoding='utf-8'))
    candidate=ROOT/next(a['path'] for a in build['artifacts'] if a['path'].endswith('/firmware.bin'))
    if (build['source_commit']!=SOURCE or build['working_tree_dirty_at_start'] or not build['build_passed'] or
        old_build['source_commit']!=OLD_SOURCE or not old_build['build_passed'] or
        old_build['working_tree_dirty_at_start'] or retained['sha256']!=OLD_SHA or
        sha(candidate)!=CANDIDATE or sha(old)!=OLD_SHA):
        raise RuntimeError('Candidate or recovery identity mismatch')
    for name,digest in build['inputs'].items():
        if not name.startswith('PRIVATE_GENERATED/') and sha(ROOT/name)!=digest:
            raise RuntimeError('Candidate build input changed: '+name)
    if sha(MANIFEST.parent/'sdkconfig.generated')!=build['sdkconfig_generated_sha256']:
        raise RuntimeError('Generated SDK evidence changed')
    for name in ('sdkconfig_generated_sha256',):
        if build[name]!=old_build[name]:raise RuntimeError('Unapproved SDK configuration difference')
    for key in ('wifi_credentials.h',):
        value=build['staged_generated_sha256'][key]
        if not value or value!=old_build['staged_generated_sha256'][key]:raise RuntimeError('Generated credentials changed')
    if build['credentials_present'] or build['private_firmware_do_not_publish']:
        raise RuntimeError('Release candidate must not contain local network credentials')
    for item in build['artifacts']:
        if item['path'].endswith('/partitions.bin') and sha(ROOT/item['path'])!=sha(BUNDLE/'artifacts/partitions.bin'):
            raise RuntimeError('Partition table changed')
    if ((0x10000+max(candidate.stat().st_size,old.stat().st_size)+4095)//4096)*4096!=SCOPE['backup_end']:
        raise RuntimeError('Backup extent does not match affected application sectors')
    files=[Path(__file__),BROWSER,MANIFEST,BASELINE,old_manifest,candidate,old,HOST,Path(str(HOST)+'.config'),NODE,tf.ESPTOOL,
           BUNDLE/'artifacts/bootloader.bin',BUNDLE/'artifacts/partitions.bin']
    files += [ROOT/'tools'/name for name in ('operation_gate.py','project_check.py','ui_live_trial.py','tf_write_batch.py','sensor_recorder_batch.py','parse_sensor_record.py')]
    files += [ROOT/'desktop-controller'/name for name in ('src/Core.cs','src/DeviceBridge.cs','tools/DeviceHost.cs','build-device-host.ps1','toolchain.json')]
    return candidate,old,files

def validate(path):
    if path is None:raise RuntimeError('Explicit candidate approval required before loading private inputs or device discovery')
    candidate,old,files=inputs()
    approval=require_approval(ROOT,path,OPERATION,tf.MAC,files)
    if approval.get('scope')!=SCOPE:raise RuntimeError('Trial scope mismatch')
    return candidate,old,approval

def start_host(approval,folder,tag):
    from serial.tools import list_ports
    ports=[p.device for p in list_ports.comports() if p.vid==0x303a and p.pid==0x1001]
    if len(ports)!=1:raise RuntimeError('Expected unique ESP32 native USB')
    log=(folder/('host-'+tag+'.log')).open('x',encoding='utf-8')
    process=subprocess.Popen([str(HOST),'--approved-usb-host',ports[0]],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                             stderr=log,text=True,encoding='utf-8',env=dict(os.environ,BREADBOARD_V2_APPROVAL=str(approval.resolve())))
    line=process.stdout.readline().strip()
    if not line.startswith('BRIDGE_URL='):
        process.wait(timeout=10);log.close();raise RuntimeError('USB busy/unavailable; no forced port reconfiguration')
    return process,log,line.split('=',1)[1]

def close_host(process):
    if process and process.poll() is None:
        process.stdin.write('exit\n');process.stdin.flush()
        process.wait(timeout=12)

def run(path):
    candidate,old,approval=validate(path)
    session=TrialSession(ROOT/'local-data/hardware/release-v010/candidate-smoke'/datetime.datetime.now().strftime('%Y%m%d_%H%M%S'))
    result=dict(passed=False,candidate_source=SOURCE,candidate_sha256=CANDIDATE,operation_commit=approval['source_commit'],cold_starts=0)
    snapshot=session.folder/'controller_prefix.bin';nvs=session.folder/'nvs-before.bin'
    changed=False;host=hostlog=browser=None
    try:
        host,hostlog,url=start_host(path,session.folder,'preflight')
        with urlopen(url+'api/status',timeout=5) as response:before=json.load(response)
        if (before['phase'] not in ('idle','passed') or before['errors']!=0 or not before['ready'] or
            before['card_serial']!=tf.CARD_SERIAL or int(before['card_bytes'])!=tf.CAPACITY):
            raise RuntimeError('Device not idle/ready or wrong card; no reset/flash')
        result['before']=before
        with session.serial() as port:recorder.boot_check(session,port,OLD_SOURCE)
        close_host(host);host=None;hostlog.close();hostlog=None
        identity=session.esptool(tf.unique_port(),['flash_id'])
        if 'Detected flash size: 8MB' not in identity:raise RuntimeError('Flash size mismatch')
        session.esptool(tf.unique_port(),['read_flash','0',hex(SCOPE['backup_end']),str(snapshot)])
        data=snapshot.read_bytes();boot=(BUNDLE/'artifacts/bootloader.bin').read_bytes();parts=(BUNDLE/'artifacts/partitions.bin').read_bytes()
        if (len(data)!=SCOPE['backup_end'] or data[:len(boot)]!=boot or data[0x8000:0x8000+len(parts)]!=parts or
            data[0x10000:0x10000+old.stat().st_size]!=old.read_bytes()):raise RuntimeError('Backup differs from expected running firmware/partition; no write')
        session.esptool(tf.unique_port(),['verify_flash','0',str(snapshot)])
        nvs.write_bytes(data[0x9000:0xe000])
        result['backup']=dict(path=snapshot.relative_to(ROOT).as_posix(),bytes=len(data),sha256=sha(snapshot),nvs_sha256=sha(nvs),verified_against_chip=True)
        (session.folder/'backup.json').write_text(json.dumps(result['backup'],indent=2)+'\n',encoding='utf-8')
        changed=True
        session.esptool(tf.unique_port(),['write_flash','--flash_mode','keep','--flash_freq','keep','--flash_size','keep','0x10000',str(candidate)])
        with session.serial() as port:
            startup=session.collect(port,6)
            if 'Guru Meditation' in startup or 'assert failed:' in startup:raise RuntimeError('Startup panic')
            recorder.boot_check(session,port,SOURCE)
            host,hostlog,url=start_host(path,session.folder,'candidate')
            env=dict(os.environ,NODE_PATH=str(NODE.parent.parent/'node_modules'),BREADBOARD_V2_APPROVAL=str(path.resolve()))
            with (session.folder/'browser-console.log').open('x',encoding='utf-8') as browser_log:
                browser=subprocess.Popen([str(NODE),str(BROWSER),url,str(session.folder)],stdout=browser_log,stderr=subprocess.STDOUT,env=env)
                deadline=time.monotonic()+180;text=''
                while browser.poll() is None:
                    chunk=port.read(4096).decode('utf-8',errors='replace')
                    if chunk:session.emit(chunk.rstrip());text+=chunk
                    if any(token in text for token in ('Guru Meditation','assert failed:','[rec] FAIL','rst:')):raise RuntimeError('Recording panic/failure/reset')
                    if time.monotonic()>deadline:raise RuntimeError('Two-record trial timeout')
                session.collect(port,1)
            if browser.returncode:raise RuntimeError('Naming/readback browser trial failed; inspect browser-console.log')
            result['browser']=json.loads((session.folder/'browser-result.json').read_text(encoding='utf-8'))
            if not result['browser']['passed'] or len(result['browser']['records'])!=2:raise RuntimeError('Expected two verified recordings')
            for record in result['browser']['records']:
                decoded=parse_sensor_record.validate(session.folder/(record['name']+'.BIN'),preview_limit=0);decoded.pop('preview',None)
                if decoded['metadata']['source']!=SOURCE or decoded['sha256']!=record['sha256']:raise RuntimeError('PC binary structure/source/hash mismatch')
                (session.folder/(record['name']+'-summary.json')).write_text(json.dumps(decoded,indent=2)+'\n',encoding='utf-8')
            final=session.query(port,'l',3)
            if final.count('PC_MATCH=1')!=2 or 'sample reads consistent=1' not in final:raise RuntimeError('Existing reference files changed')
        close_host(host);host=None;hostlog.close();hostlog=None
        result['passed']=True;result['full_release_acceptance']=False;session.emit('RELEASE_V010_SMOKE_PASS')
    except Exception as error:
        result['error']=repr(error);session.emit('RELEASE_V010_SMOKE_FAIL='+repr(error))
        if browser and browser.poll() is None:browser.terminate();browser.wait(timeout=10)
        try:close_host(host);host=None
        except Exception:pass
        if changed:
            try:
                with session.serial() as port:session.query(port,'REC_STOP',2);session.collect(port,10)
                if sha(snapshot)!=result['backup']['sha256']:raise RuntimeError('Recovery snapshot changed')
                recorder.restore(session,snapshot,OLD_SOURCE)
                after=session.folder/'nvs-after-recovery.bin'
                session.esptool(tf.unique_port(),['read_flash','0x9000','0x5000',str(after)])
                if sha(after)!=result['backup']['nvs_sha256']:
                    session.esptool(tf.unique_port(),['write_flash','--flash_mode','keep','--flash_freq','keep','--flash_size','keep','0x9000',str(nvs)])
                    with session.serial() as port:session.collect(port,6);recorder.boot_check(session,port,OLD_SOURCE)
                result['restored_before_flash']=True
            except Exception as recovery:result['recovery_error']=repr(recovery);session.emit('RECOVERY_ERROR='+repr(recovery))
        raise
    finally:
        if hostlog:hostlog.close()
        (session.folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        session.emit('RESULT='+str(session.folder/'result.json'));session.log.close()

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8',errors='replace');sys.stderr.reconfigure(encoding='utf-8',errors='replace')
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--prepare-only',action='store_true');parser.add_argument('--approval-record',type=Path);args=parser.parse_args()
    if args.prepare_only:
        _,_,files=inputs();print(json.dumps(dict(hardware_accessed=False,scope=SCOPE,files={p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else p.as_posix():sha(p) for p in files}),indent=2))
    else:
        if args.approval_record is None:raise RuntimeError('Explicit approval required before device discovery')
        run(args.approval_record)
