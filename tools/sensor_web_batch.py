"""Explicitly approved web-recorder trial. No hardware access without a matching approval.

Commands split initial flash/main+early+warm from each user-confirmed full-power-off.
Build and CI do not invoke this entry. Host-reader export is a separate read-only step.
"""
import argparse
import datetime
import ipaddress
import json
from pathlib import Path
import re
import subprocess
import time
import urllib.request
from project_check import ROOT, BUNDLE, sha, baseline_errors
from operation_gate import require_approval
from prepare_sensor_web_trial import prepare
import tf_write_batch as tf
import sensor_recorder_batch as base

PUBLIC = ROOT/'docs/reports/SENSOR_WEB_BUILD_20261016.json'
NODE = Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
NODE_MODULES = NODE.parent.parent/'node_modules'
BROWSER = ROOT/'tools/run_sensor_web_browser.cjs'
CANDIDATE_SHA = 'e1dae22f0714218c7961519be1b9f9af1c52062982e4619609b0019b37387a68'
SOURCE = 'c921bef8f14e8c18ce06cd5574c241377562e971'
OPERATION = 'sensor-web-round'


def inputs():
    errors = baseline_errors()
    if errors: raise RuntimeError('; '.join(errors))
    public = json.loads(PUBLIC.read_text(encoding='utf-8'))
    manifest = ROOT/public['manifest_local_path']
    packet = prepare(manifest)
    if packet['candidate_source'] != SOURCE or packet['candidate']['sha256'] != CANDIDATE_SHA:
        raise RuntimeError('Unexpected authorized web candidate')
    candidate = ROOT/packet['candidate']['path']
    recovery = ROOT/packet['recovery']['path']
    files = [Path(__file__), BROWSER, PUBLIC, manifest, candidate, recovery, tf.ESPTOOL, NODE,
        ROOT/'tools/operation_gate.py', ROOT/'tools/prepare_sensor_web_trial.py',
        ROOT/'tools/build_sensor_web_recorder.py', ROOT/'tools/tf_write_batch.py',
        ROOT/'tools/sensor_recorder_batch.py', ROOT/'tools/parse_sensor_record.py',
        BUNDLE/'artifacts/bootloader.bin', BUNDLE/'artifacts/partitions.bin']
    return packet, candidate, recovery, list(dict.fromkeys(p.resolve() for p in files))


def approved(path):
    if path is None: raise RuntimeError('Requires genuine candidate approval before any device discovery')
    packet, candidate, recovery, files = inputs()
    approval = require_approval(ROOT, path, OPERATION, tf.MAC, files)
    if approval.get('scope') != packet['scope_proposed']:
        raise RuntimeError('Web trial scope mismatch')
    if approval.get('hardware_ready_confirmed') is not True:
        raise RuntimeError('Physical card/power/wiring readiness not confirmed')
    return packet, candidate, recovery, approval


def status(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url+'api/status', timeout=3) as response:
        data = json.load(response)
    if (data.get('card_serial') != tf.CARD_SERIAL or data.get('card_bytes') != str(tf.CAPACITY) or
            not data.get('connected') or not data.get('ready')):
        raise RuntimeError('Web status identity/readiness mismatch')
    return data


def address(session, port, preferred=None):
    deadline = time.monotonic()+45
    while time.monotonic()<deadline:
        text = (session.folder/'hardware.log').read_text(encoding='utf-8')
        matches = re.findall(r'\[web\] address=http://([0-9.]+)/', text)
        if not matches and preferred:
            matches=re.findall(r'^http://([0-9.]+)/$',preferred)
        if matches:
            ip = matches[-1]
            if not ipaddress.ip_address(ip).is_private: raise RuntimeError('Expected private STA address')
            url = 'http://'+ip+'/'
            try:
                info=status(url)
                session.emit('WEB_READY='+json.dumps(info, ensure_ascii=False)); return url,info
            except (OSError, ValueError, RuntimeError):
                pass
        session.collect(port,1)
    raise RuntimeError('STA address/HTTP readiness not established; preserve logs')


def run_browser(session, port, url, mode, seconds, approval_path):
    folder = session.folder/('browser-'+mode)
    folder.mkdir(exist_ok=False)
    import os
    env = dict(os.environ); env['NODE_PATH']=str(NODE_MODULES)
    env['BREADBOARD_APPROVAL_PATH']=str(approval_path.resolve());env['BREADBOARD_PYTHON']=__import__('sys').executable
    text = ''; deadline = time.monotonic()+seconds+150
    with (folder/'browser-console.log').open('x',encoding='utf-8') as log:
        process=subprocess.Popen([str(NODE),str(BROWSER),url,str(folder),mode,str(seconds)],
            stdout=log,stderr=subprocess.STDOUT,env=env,cwd=ROOT)
        try:
            while process.poll() is None:
                chunk=port.read(4096).decode('utf-8',errors='replace')
                if chunk: session.emit(chunk.rstrip('\r\n')); text+=chunk
                if time.monotonic()>deadline: raise RuntimeError('Browser/device run timeout')
                if any(x in text for x in ['Guru Meditation','[rec] FAIL','rst:']):
                    raise RuntimeError('Actual recorder failure/reset; stop expansion')
            text += session.collect(port,1)
            if process.returncode: raise RuntimeError('Actual browser trial failed; see browser console')
        finally:
            if process.poll() is None: process.terminate(); process.wait(timeout=10)
    result=json.loads((folder/'browser-result.json').read_text(encoding='utf-8'))
    final=result['final']
    accepted=base.accepted_reply(text,result['run'],final['elapsed_us']/1000000)
    if accepted['sha256']!=final['sha256'] or accepted['samples']!=final['written']:
        raise RuntimeError('Browser/serial terminal mismatch')
    accepted.update(browser_result=str((folder/'browser-result.json').relative_to(ROOT)),
        elapsed_us=final['elapsed_us'],mode=mode,web_boot=final['boot'])
    session.emit('WEB_RECORD_PASS='+json.dumps(accepted)); return accepted


def restore(session, snapshot):
    # Restore every affected application sector, excluding boot/partition/NVS.
    data=snapshot.read_bytes()
    path=session.folder/'restore_app_range.bin'; path.write_bytes(data[0x10000:])
    session.esptool(tf.unique_port(),['flash_id'])
    session.esptool(tf.unique_port(),['write_flash','--flash_mode','keep','--flash_freq','keep','--flash_size','keep','0x10000',str(path)])
    with session.serial() as port:
        session.collect(port,8); base.boot_check(session,port,'203a60b563708ec96d1e7a883faff059af4a9a7c')


def stop_before_restore(session):
    try:
        with session.serial() as port:
            session.query(port,'REC_STOP',2)
            session.collect(port,8)
    except Exception as e: session.emit('STOP_BEFORE_RESTORE='+repr(e))


def initial(path):
    packet,candidate,recovery,approval=approved(path)
    stamp=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y%m%d_%H%M%S')
    session=tf.Session(ROOT/'local-data/hardware/sensor-web'/stamp)
    result=dict(passed=False,source_commit=SOURCE,operation_commit=approval['source_commit'],
        candidate_sha256=sha(candidate),cold_starts_completed=0,host_real_file_parse_passed=False)
    snapshot=session.folder/'controller_prefix.bin'; changed=False
    try:
        with session.serial() as port:
            session.collect(port,1); base.boot_check(session,port,'203a60b563708ec96d1e7a883faff059af4a9a7c')
        identity=session.esptool(tf.unique_port(),['flash_id'])
        if 'Detected flash size: 8MB' not in identity: raise RuntimeError('Flash identity mismatch')
        extent=packet['required_prewrite_backup_bytes']
        session.esptool(tf.unique_port(),['read_flash','0',hex(extent),str(snapshot)])
        data=snapshot.read_bytes();boot=(BUNDLE/'artifacts/bootloader.bin').read_bytes()
        parts=(BUNDLE/'artifacts/partitions.bin').read_bytes();old=recovery.read_bytes()
        if (len(data)!=extent or data[:len(boot)]!=boot or data[0x8000:0x8000+len(parts)]!=parts or
                data[0x10000:0x10000+len(old)]!=old): raise RuntimeError('Existing boot/partition/app differs; no write')
        result.update(snapshot_bytes=extent,snapshot_sha256=sha(snapshot))
        changed=True
        session.esptool(tf.unique_port(),['write_flash','--flash_mode','keep','--flash_freq','keep','--flash_size','keep','0x10000',str(candidate)])
        with session.serial() as port:
            session.collect(port,10); base.boot_check(session,port,SOURCE)
            url,info=address(session,port); result['initial_web']=info; result['initial_url']=url
            result['main_record']=run_browser(session,port,url,'main',300,path)
            browser_main=json.loads((ROOT/result['main_record']['browser_result']).read_text(encoding='utf-8'))
            result['public_network_access_verified']=browser_main['public_connect_test_success']
            result['early_record']=run_browser(session,port,url,'early',300,path)
            session.emit('MCU-only EN reset; card/modules remain powered; not full power-off')
            port.rts=True;time.sleep(.1);port.rts=False;session.collect(port,10)
            base.boot_check(session,port,SOURCE,warm=True)
            url,warm=address(session,port,result['initial_url'])
            if warm['boot']==info['boot']: raise RuntimeError('Web boot token did not change across MCU reset')
            base.verify(session,port,result['main_record']);base.verify(session,port,result['early_record'])
            result['warm_record']=run_browser(session,port,url,'short',10,path)
        result['passed']=True
    except Exception as e:
        result['error']=repr(e);session.emit('FAIL='+repr(e))
        if changed:
            stop_before_restore(session)
            try: restore(session,snapshot);result['restored_previous']=True
            except Exception as r: result['recovery_error']=repr(r)
        raise
    finally:
        (session.folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        session.emit('RESULT='+str(session.folder/'result.json'));session.log.close()


def cold(path,folder,number,confirmation):
    packet,_,_,approval=approved(path)
    folder=folder.resolve();allowed=(ROOT/'local-data/hardware/sensor-web').resolve()
    if not folder.is_relative_to(allowed) or folder==allowed or number not in (1,2) or not confirmation.strip():
        raise RuntimeError('Requires actual numbered full-power-off confirmation')
    target=folder/'result.json';result=json.loads(target.read_text(encoding='utf-8'))
    snapshot=folder/'controller_prefix.bin'
    if (not result.get('passed') or result.get('operation_commit')!=approval['source_commit'] or
            result.get('candidate_sha256')!=CANDIDATE_SHA or result.get('cold_starts_completed')!=number-1 or
            snapshot.stat().st_size!=packet['required_prewrite_backup_bytes'] or sha(snapshot)!=result['snapshot_sha256']):
        raise RuntimeError('Cold sequence/backup evidence mismatch')
    session=tf.Session(folder/f'cold-{number}');outcome=dict(passed=False,user_confirmation=confirmation)
    try:
        with session.serial() as port:
            session.collect(port,2);base.boot_check(session,port,SOURCE,cold=True)
            url,info=address(session,port,result['initial_url'])
            for item in [result['main_record'],result['early_record'],result['warm_record']]: base.verify(session,port,item)
            outcome['web']=info;outcome['record']=run_browser(session,port,url,'short',10,path)
        outcome['passed']=True;result['cold_starts_completed']=number
        result['device_round_passed']=number==2
    except Exception as e:
        outcome['error']=repr(e);result['passed']=False;stop_before_restore(session)
        try:restore(session,snapshot);outcome['restored_previous']=True
        except Exception as r:outcome['recovery_error']=repr(r)
        raise
    finally:
        (session.folder/'result.json').write_text(json.dumps(outcome,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        target.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');session.log.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only',action='store_true');parser.add_argument('--approval-record',type=Path)
    parser.add_argument('--validate-approval',action='store_true')
    parser.add_argument('--cold-folder',type=Path);parser.add_argument('--cold-number',type=int)
    parser.add_argument('--cold-confirmation',default='');args=parser.parse_args()
    if args.validate_approval:
        approved(args.approval_record);print('APPROVAL_VALID no device discovery or I/O')
    elif args.prepare_only:
        packet,_,_,files=inputs();print(json.dumps(dict(packet=packet,files={p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else p.as_posix():sha(p) for p in files}),indent=2))
    elif args.cold_folder:cold(args.approval_record,args.cold_folder,args.cold_number,args.cold_confirmation)
    else:
        if args.cold_number is not None or args.cold_confirmation:parser.error('Cold options require --cold-folder')
        initial(args.approval_record)


if __name__=='__main__':main()
