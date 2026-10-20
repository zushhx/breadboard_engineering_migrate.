"""Unbound F app0 trial from verified E. User delegated this round candidate confirmation to lead; never claim a new human exact-F confirmation. Same-round NVS only."""
from pathlib import Path
import argparse
import datetime
import json
import hashlib
import os
import subprocess
import sys
import time

from project_check import ROOT, BUNDLE, sha
from operation_gate import require_approval
from ui_live_trial import TrialSession
import tf_write_batch as tf
import sensor_recorder_batch as recorder
import parse_sensor_record

SOURCE = 'UNBOUND_F_SOURCE'
CANDIDATE = 'UNBOUND_F_SHA256'
OLD_SOURCE = '4aa27b2c90902d03e262c23cad5eb65f812bc524'
OLD_SHA = '81a84cabc93c30934122c33d5a6ca6945a6da3ff05292b94cef5cb39ebceade7'
MANIFEST = None
OLD_MANIFEST = ROOT/'local-data/build/sensor-web/250563438c572116/20261020_162052_054082/build_manifest.json'
EXPECTED_NETWORK = ROOT/'local-data/hardware/release-v010/E-usb-network-before-private.json'
HOST = ROOT/'local-data/release-v010/device-host-E/DeviceHost.exe'
BROWSER = ROOT/'tools/release_v010_live.cjs'
NODE = Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
OPERATION = 'release-v010-transfer-cancel-smoke'
SCOPE = dict(app0_only=True, requires_quiescent_E=True, candidate_confirmation_authority='user_delegated_lead_for_current_round', backup_end=0x124000, reset_from_saved_panic=False, backup_nvs=True,
             restore_app0_only=True, restore_nvs_only_if_changed=True, records=2, seconds_each=3,
             preserve_existing_files=True, delete_format_repair=False, change_network=False,
             restore_on_failure=True, cold_starts=0, main_merge_release=False)

def bound_candidate():
    import re
    if (not re.fullmatch(r'[0-9a-f]{40}',SOURCE) or not re.fullmatch(r'[0-9a-f]{64}',CANDIDATE) or
            MANIFEST is None or SOURCE==OLD_SOURCE or CANDIDATE==OLD_SHA):
        raise RuntimeError('Candidate F is unbound; freeze source, image and manifest before prepare/run')


def required_backup_end(candidate_bytes,old_bytes):
    return ((0x10000+max(candidate_bytes,old_bytes)+4095)//4096)*4096


def validate_backup_extent(candidate_bytes,old_bytes):
    required=required_backup_end(candidate_bytes,old_bytes)
    if required!=SCOPE['backup_end']:
        raise RuntimeError('Backup extent does not match affected application sectors: required '+hex(required))


def inputs():
    bound_candidate()
    build=json.loads(MANIFEST.read_text(encoding='utf-8'))
    old_manifest=OLD_MANIFEST
    old_build=json.loads(old_manifest.read_text(encoding='utf-8'))
    old=ROOT/next(a['path'] for a in old_build['artifacts'] if a['path'].endswith('/firmware.bin'))
    candidate=ROOT/next(a['path'] for a in build['artifacts'] if a['path'].endswith('/firmware.bin'))
    if (build['source_commit']!=SOURCE or build['working_tree_dirty_at_start'] or not build['build_passed'] or
        old_build['source_commit']!=OLD_SOURCE or not old_build['build_passed'] or
        old_build['working_tree_dirty_at_start'] or
        sha(candidate)!=CANDIDATE or sha(old)!=OLD_SHA):
        raise RuntimeError('Candidate or recovery identity mismatch')
    for name,digest in build['inputs'].items():
        if not name.startswith('PRIVATE_GENERATED/'):
            # Each frozen candidate is checked against its own committed inputs.
            # F and E intentionally differ; verify each frozen source explicitly.
            blob=subprocess.check_output(['git','show',SOURCE+':'+name],cwd=ROOT)
            if hashlib.sha256(blob).hexdigest()!=digest:
                raise RuntimeError('Frozen candidate input differs from its commit: '+name)
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
    validate_backup_extent(candidate.stat().st_size,old.stat().st_size)
    files=[Path(__file__),EXPECTED_NETWORK,BROWSER,MANIFEST,old_manifest,candidate,old,HOST,Path(str(HOST)+'.config'),NODE,tf.ESPTOOL,
           BUNDLE/'artifacts/bootloader.bin',BUNDLE/'artifacts/partitions.bin']
    files += [ROOT/'tools'/name for name in ('operation_gate.py','project_check.py','ui_live_trial.py','tf_write_batch.py','sensor_recorder_batch.py','parse_sensor_record.py')]
    files += [ROOT/'desktop-controller'/name for name in ('src/Core.cs','src/DeviceBridge.cs','tools/DeviceHost.cs','build-device-host.ps1','toolchain.json')]
    return candidate,old,files

def validate(path):
    if path is None:raise RuntimeError('Explicit candidate approval required before loading private inputs or device discovery')
    candidate,old,files=inputs()
    approval=require_approval(ROOT,path,OPERATION,tf.MAC,files)
    if approval.get('scope')!=SCOPE or approval.get('hardware_ready_confirmed') is not True or approval.get('candidate_confirmation_delegated') is not True:
        raise RuntimeError('Trial scope/readiness mismatch')
    return candidate,old,approval

def check_quiescent(status):
    if (status.get('phase') not in ('idle','passed') or status.get('errors')!=0 or
            status.get('ready') is not True or status.get('files_busy') is not False or
            status.get('card_serial')!=tf.CARD_SERIAL or str(status.get('card_bytes'))!=str(tf.CAPACITY)):
        raise RuntimeError('Current E busy, failed, not ready, or wrong TF; no reset or write')


def check_same_runtime(before,after):
    check_quiescent(after)
    if not before.get('boot') or after.get('boot')!=before['boot'] or after.get('run')!=before.get('run'):
        raise RuntimeError('E boot/run changed during preflight; no reset or write')


def read_only_preflight(session,approval_path):
    import urllib.request
    host=log=None
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    # UART is opened without DTR/RTS reset. Keep it open across all USB checks;
    # collect its buffered bytes even when a USB query raises or the host stops.
    with session.serial() as port:
        try:
            host,log,url=start_host(approval_path,session.folder,'before')
            def get(path):
                with opener.open(url+path,timeout=15) as response:return json.load(response)
            before=get('api/status');check_quiescent(before)
            recorder.boot_check(session,port,OLD_SOURCE)
            network=get('api/v2/network/state')
            expected=json.loads(EXPECTED_NETWORK.read_text(encoding='utf-8'))
            check_ap_network(network,expected)
            after=get('api/status');check_same_runtime(before,after)
            recorder.boot_check(session,port,OLD_SOURCE)
            return dict(source=OLD_SOURCE,boot=before['boot'],run=before.get('run'),phase=before['phase'],
                        errors=0,files_busy=False,ap_only=True,existing_references_unchanged=True,reset=False)
        finally:
            try:close_host(host)
            finally:
                text=session.collect(port,1)
                if log:log.close()
                if any(token in text for token in ('Guru Meditation','assert failed:','abort() was called','Entering gdb stub','rst:')):
                    raise RuntimeError('Panic/reset during E preflight; preserve evidence, no reset/write')


def check_ap_network(network,expected):
    if (network.get('ap_enabled') is not True or network.get('sta_enabled') is not False or
        network.get('running') is not True or network.get('apply_ok') is not True or network.get('applying') is not False):
        raise RuntimeError('Current E is not applied AP-only config; no reset or write')
    for key in ('sta_enabled','ap_enabled','ssid','ap_ssid'):
        if key not in expected or network.get(key)!=expected[key]:
            raise RuntimeError('Current E logical network config differs; no reset or write')
    passwords=[value.get('ap_password') for value in (network,expected)]
    if any(not isinstance(value,str) or len(value)<8 for value in passwords) or hashlib.sha256(passwords[0].encode()).digest()!=hashlib.sha256(passwords[1].encode()).digest():
        raise RuntimeError('Current E AP password digest differs; no reset or write')


def check_backup(data,old,nvs_data):
    boot=(BUNDLE/'artifacts/bootloader.bin').read_bytes();parts=(BUNDLE/'artifacts/partitions.bin').read_bytes()
    if (len(data)!=SCOPE['backup_end'] or data[:len(boot)]!=boot or
            data[0x8000:0x8000+len(parts)]!=parts or data[0x10000:0x10000+len(old)]!=old or
            len(nvs_data)!=0x5000 or data[0x9000:0xe000]!=nvs_data):
        raise RuntimeError('Fresh boot/partition/E app/same-round NVS mismatch; no write')


def restore_application(session,snapshot,backup,old):
    if sha(snapshot)!=backup['sha256'] or sha(old)!=OLD_SHA:
        raise RuntimeError('Recovery inputs changed')
    # Write app0 image only, never the prefix snapshot.
    session.esptool(tf.unique_port(),['flash_id'])
    session.esptool(tf.unique_port(),['write_flash','--flash_mode','keep','--flash_freq','keep','--flash_size','keep','0x10000',str(old)])
    session.esptool(tf.unique_port(),['verify_flash','0x10000',str(old)])
    after=session.folder/'nvs-after-recovery.bin'
    session.esptool(tf.unique_port(),['read_flash','0x9000','0x5000',str(after)])
    if sha(after)!=backup['nvs_sha256']:
        if not SCOPE['restore_nvs_only_if_changed']:raise RuntimeError('NVS changed; recovery not authorized')
        nvs=session.folder/'nvs-before.bin'
        if sha(nvs)!=backup['nvs_sha256']:raise RuntimeError('Same-round NVS snapshot changed')
        session.esptool(tf.unique_port(),['write_flash','--flash_mode','keep','--flash_freq','keep','--flash_size','keep','0x9000',str(nvs)])
        session.esptool(tf.unique_port(),['verify_flash','0x9000',str(nvs)])
    with session.serial() as port:
        session.collect(port,6);recorder.boot_check(session,port,OLD_SOURCE)


def start_host(approval,folder,tag):
    from serial.tools import list_ports
    ports=[p.device for p in list_ports.comports() if p.vid==0x303a and p.pid==0x1001]
    if len(ports)!=1:raise RuntimeError('Expected unique ESP32 native USB')
    log=(folder/('host-'+tag+'.log')).open('x',encoding='utf-8')
    process=None
    try:
        process=subprocess.Popen([str(HOST),'--approved-usb-host',ports[0]],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                 stderr=log,text=True,encoding='utf-8',env=dict(os.environ,BREADBOARD_V2_APPROVAL=str(approval.resolve())))
        # A pipe readline has no timeout. Read it on a daemon so failed startup
        # cannot indefinitely retain the owned USB host or prevent rollback.
        import threading,queue
        ready=queue.Queue(maxsize=1)
        def read_ready():
            try:ready.put((True,process.stdout.readline().strip()))
            except Exception as error:ready.put((False,error))
        threading.Thread(target=read_ready,daemon=True).start()
        try:ok,line=ready.get(timeout=10)
        except queue.Empty:raise RuntimeError('USB host readiness timeout') from None
        if not ok:raise RuntimeError('USB host readiness read failed') from line
        if not line.startswith('BRIDGE_URL='):
            raise RuntimeError('USB busy/unavailable; no forced port reconfiguration')
        return process,log,line.split('=',1)[1]
    except Exception:
        try:stop_owned(process)
        finally:log.close()
        raise


def stop_owned(process):
    if process is None or process.poll() is not None:return
    try:
        process.terminate();process.wait(timeout=5)
    except (OSError,subprocess.TimeoutExpired):
        process.kill();process.wait(timeout=5)

def close_host(process):
    if process and process.poll() is None:
        try:
            process.stdin.write('exit\n');process.stdin.flush()
            process.wait(timeout=5)
        except (OSError,subprocess.TimeoutExpired):stop_owned(process)


def cleanup_owned(browser,host,result):
    # Every cleanup is independent; a stubborn child cannot skip restoration.
    for tag,cleanup in [('browser',lambda:stop_owned(browser)),('host',lambda:close_host(host))]:
        try:cleanup()
        except Exception as error:result.setdefault('cleanup_errors',[]).append(tag+': '+repr(error))

def run(path):
    candidate,old,approval=validate(path)
    session=TrialSession(ROOT/'local-data/hardware/release-v010/transfer-cancel-smoke'/datetime.datetime.now().strftime('%Y%m%d_%H%M%S'))
    result=dict(passed=False,candidate_source=SOURCE,candidate_sha256=CANDIDATE,operation_commit=approval['source_commit'],cold_starts=0)
    snapshot=session.folder/'controller_prefix.bin';nvs=session.folder/'nvs-before.bin'
    changed=False;host=hostlog=browser=None
    try:
        result['preflight']=read_only_preflight(session,path)
        session.emit('USER_DELEGATED_LEAD_CONFIRMED_F_TRIAL_AFTER_QUIESCENT_E_PREFLIGHT; NOT A COLD START')
        identity=session.esptool(tf.unique_port(),['flash_id'])
        if 'Detected flash size: 8MB' not in identity:raise RuntimeError('Flash size mismatch')
        session.esptool(tf.unique_port(),['read_flash','0',hex(SCOPE['backup_end']),str(snapshot)])
        data=snapshot.read_bytes()
        session.esptool(tf.unique_port(),['verify_flash','0',str(snapshot)])
        session.esptool(tf.unique_port(),['read_flash','0x9000','0x5000',str(nvs)])
        session.esptool(tf.unique_port(),['verify_flash','0x9000',str(nvs)])
        check_backup(data,old.read_bytes(),nvs.read_bytes())
        result['backup']=dict(path=snapshot.relative_to(ROOT).as_posix(),bytes=len(data),sha256=sha(snapshot),nvs_sha256=sha(nvs),verified_against_chip=True,nvs_separately_read_and_verified=True,prefix_nvs_cross_checked=True)
        (session.folder/'backup.json').write_text(json.dumps(result['backup'],indent=2)+'\n',encoding='utf-8')
        changed=True
        session.esptool(tf.unique_port(),['write_flash','--flash_mode','keep','--flash_freq','keep','--flash_size','keep','0x10000',str(candidate)])
        with session.serial() as port:
            startup=session.collect(port,6)
            if any(token in startup for token in ('Guru Meditation','assert failed:','abort() was called','Entering gdb stub')):raise RuntimeError('Startup panic')
            recorder.boot_check(session,port,SOURCE)
            host,hostlog,url=start_host(path,session.folder,'candidate')
            env=dict(os.environ,NODE_PATH=str(NODE.parent.parent/'node_modules'),BREADBOARD_V2_APPROVAL=str(path.resolve()))
            with (session.folder/'browser-console.log').open('x',encoding='utf-8') as browser_log:
                browser=subprocess.Popen([str(NODE),str(BROWSER),url,str(session.folder)],stdout=browser_log,stderr=subprocess.STDOUT,env=env)
                deadline=time.monotonic()+180;text=''
                while browser.poll() is None:
                    chunk=port.read(4096).decode('utf-8',errors='replace')
                    if chunk:session.emit(chunk.rstrip());text+=chunk
                    if any(token in text for token in ('Guru Meditation','assert failed:','[rec] FAIL','rst:','abort() was called','Entering gdb stub')):raise RuntimeError('Recording panic/failure/reset')
                    if time.monotonic()>deadline:raise RuntimeError('Two-record trial timeout')
                session.collect(port,1)
            if browser.returncode:raise RuntimeError('Naming/readback browser trial failed; inspect browser-console.log')
            result['browser']=json.loads((session.folder/'browser-result.json').read_text(encoding='utf-8'))
            if not result['browser']['passed'] or len(result['browser']['records'])!=2:raise RuntimeError('Expected two verified recordings')
            for record in result['browser']['records']:
                decoded=parse_sensor_record.validate(session.folder/(record['name']+'.BIN'),preview_limit=0);decoded.pop('preview',None)
                if decoded['metadata']['source']!=SOURCE or decoded['sha256']!=record['sha256']:raise RuntimeError('PC binary structure/source/hash mismatch')
                if any(decoded['footer'][k]!=0 for k in ('errors','ina_invalid','saturation')):raise RuntimeError('Record errors/invalid/saturation')
                (session.folder/(record['name']+'-summary.json')).write_text(json.dumps(decoded,indent=2)+'\n',encoding='utf-8')
            final=session.query(port,'l',3)
            if final.count('PC_MATCH=1')!=2 or 'sample reads consistent=1' not in final:raise RuntimeError('Existing reference files changed')
        close_host(host);host=None;hostlog.close();hostlog=None
        result['passed']=True;result['full_release_acceptance']=False;session.emit('RELEASE_V010_SMOKE_PASS')
    except Exception as error:
        result['error']=repr(error);session.emit('RELEASE_V010_SMOKE_FAIL='+repr(error))
        cleanup_owned(browser,host,result);host=None
        if changed:
            try:
                with session.serial() as port:session.query(port,'REC_STOP',2);session.collect(port,10)
                if sha(snapshot)!=result['backup']['sha256']:raise RuntimeError('Recovery snapshot changed')
                restore_application(session,snapshot,result['backup'],old)
                result['restored_before_flash']=True
            except Exception as recovery:result['recovery_error']=repr(recovery);session.emit('RECOVERY_ERROR='+repr(recovery))
        raise
    finally:
        if hostlog:
            try:hostlog.close()
            except Exception as error:result.setdefault('cleanup_errors',[]).append('hostlog: '+repr(error))
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
