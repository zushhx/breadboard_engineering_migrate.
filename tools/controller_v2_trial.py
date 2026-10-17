"""Version-bound v2 hardware trial. Not called by builds, installation or CI."""
from pathlib import Path
import argparse,datetime,json,os,re,subprocess,sys,time
from project_check import ROOT,BUNDLE,sha
from operation_gate import require_approval
import tf_write_batch as tf
import sensor_recorder_batch as recorder
from ui_live_trial import TrialSession
SOURCE='e470e40057b3c2d4488b61275926bad88d2b8170'
CANDIDATE='d91e26a2157e6ab9fe46f34bef3e81e01e3cbe655046579f737b142303752033'
OLD_SOURCE='eb4aba6340ba26093cd8f1849714b8f335bf4792'
OLD_SHA='400192859ee7e4ac3723120397af1e804bec8c0baea5545bac07b46f8c65dc16'
MANIFEST=ROOT/'local-data/build/sensor-web/7b8e69162f22b766/20261017_214409_349597/build_manifest.json'
BASE_SNAPSHOT=ROOT/'local-data/hardware/controller-v2/20261017_213827/controller_prefix.bin'
BASE_SNAPSHOT_SHA='48e0da0f4a735707b6fe12daf46a0859fa9796d9f40a19c68bd9aafcbe6fa4b1'
OLD=ROOT/'local-data/controller-v2/recovery-eb4aba6/firmware.bin'
HOST=ROOT/'local-data/controller-v2/native-wire-fixed/DeviceHost.exe'
NODE=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
MODULES=NODE.parent.parent/'node_modules'
SCOPE=dict(app0_only=True,backup_nvs=True,backup_end=0x121000,preserve_old_files=True,new_test_directory_only=True,
           usb=True,network_config=True,ownership=True,multi_page=True,max_record_seconds=90,short_checks_only=True,restore_on_failure=True,
           format_repair=False,cold_starts_manual_only=True,main_merge_release=False)
def inputs():
    build=json.loads(MANIFEST.read_text(encoding='utf-8'))
    if build['source_commit']!=SOURCE or build['working_tree_dirty_at_start'] or not build['build_passed']:raise RuntimeError('Build identity mismatch')
    candidate=ROOT/next(x['path'] for x in build['artifacts'] if x['path'].endswith('/firmware.bin'))
    if sha(candidate)!=CANDIDATE or sha(OLD)!=OLD_SHA or sha(BASE_SNAPSHOT)!=BASE_SNAPSHOT_SHA:raise RuntimeError('Candidate/recovery hash mismatch')
    files=[Path(__file__),ROOT/'tools/controller_v2_live.cjs',ROOT/'desktop-controller/tools/DeviceHost.cs',HOST,MANIFEST,candidate,OLD,BASE_SNAPSHOT,NODE,tf.ESPTOOL,
           BUNDLE/'artifacts/bootloader.bin',BUNDLE/'artifacts/partitions.bin']
    files += [ROOT/'tools'/n for n in ['operation_gate.py','project_check.py','tf_write_batch.py','sensor_recorder_batch.py','ui_live_trial.py']]
    files += [ROOT/'desktop-controller'/n for n in ['src/Core.cs','src/DeviceBridge.cs','build-device-host.ps1','src/app.config','toolchain.json']]
    return candidate,list(dict.fromkeys(p.resolve() for p in files))
def validate(path):
    candidate,files=inputs();record=require_approval(ROOT,path,'controller-v2-trial',tf.MAC,files)
    if record.get('scope')!=SCOPE:raise RuntimeError('Trial scope mismatch')
    return candidate,record
def start_host(approval,folder,suffix):
    from serial.tools import list_ports
    ports=[p.device for p in list_ports.comports() if p.vid==0x303a and p.pid==0x1001]
    if len(ports)!=1:raise RuntimeError('Expected unique ESP32 native USB')
    env=dict(os.environ,BREADBOARD_V2_APPROVAL=str(approval.resolve()))
    error=(folder/('usb-host-'+suffix+'.log')).open('w',encoding='utf-8')
    host=subprocess.Popen([str(HOST),'--approved-usb-host',ports[0]],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=error,text=True,encoding='utf-8',env=env)
    line=host.stdout.readline().strip()
    if not line.startswith('BRIDGE_URL='):host.wait(timeout=10);error.close();raise RuntimeError('USB bridge did not start')
    return host,error,line.split('=',1)[1]
def close_host(host):
    if host and host.poll() is None:
        host.stdin.write('exit\n');host.stdin.flush()
        try:host.wait(timeout=10)
        except subprocess.TimeoutExpired:host.terminate();host.wait(timeout=5)
def probe_usb(path):
    validate(path)
    from serial.tools import list_ports
    ports=[p.device for p in list_ports.comports() if p.vid==0x303a and p.pid==0x1001]
    if len(ports)!=1:raise RuntimeError('Expected unique native USB')
    folder=ROOT/'local-data/hardware/controller-v2'/('usb-open-'+datetime.datetime.now().strftime('%Y%m%d_%H%M%S'))
    session=TrialSession(folder)
    try:
        with session.serial() as port:
            session.collect(port,1);before=session.query(port,'help',1)
            if OLD_SOURCE not in before:raise RuntimeError('Probe requires restored old controller')
            process=subprocess.run([str(HOST),'--approved-usb-open',ports[0]],capture_output=True,text=True,env=dict(os.environ,BREADBOARD_V2_APPROVAL=str(path.resolve())),timeout=10)
            session.emit(process.stdout);session.emit(process.stderr)
            if process.returncode:raise RuntimeError('Native open probe failed')
            output=session.collect(port,3)
            if 'rst:' in output or 'ESP-ROM:' in output:raise RuntimeError('USB open/close still resets controller')
            after=session.query(port,'help',1)
            if OLD_SOURCE not in after:raise RuntimeError('Controller not responsive after USB open')
        (folder/'result.json').write_text(json.dumps(dict(passed=True,open_close_without_reset=True,source=OLD_SOURCE)),encoding='utf-8')
        session.emit('USB_OPEN_CLOSE_NO_RESET_PASS')
    finally:session.log.close()
def run(path):
    candidate,approval=validate(path)
    stamp=datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    session=TrialSession(ROOT/'local-data/hardware/controller-v2'/stamp)
    result=dict(passed=False,candidate_source=SOURCE,candidate_sha256=CANDIDATE,operation_commit=approval['source_commit'],cold_starts=0)
    snapshot=session.folder/'controller_prefix.bin';second=session.folder/'controller_prefix_second.bin'
    changed=False;host=None;browser=None;hostlog=None
    try:
        with session.serial() as port:
            recorder.boot_check(session,port,OLD_SOURCE)
            before=session.query(port,'m',1)
            if 'active=1' in before:raise RuntimeError('Controller active; no flash')
        identity=session.esptool(tf.unique_port(),['flash_id'])
        if 'Detected flash size: 8MB' not in identity:raise RuntimeError('Flash size mismatch')
        # Reuse the already double-read, restored baseline; verify it against the current chip before writing.
        snapshot.write_bytes(BASE_SNAPSHOT.read_bytes())
        session.esptool(tf.unique_port(),['verify_flash','0',str(snapshot)])
        data=snapshot.read_bytes();boot=(BUNDLE/'artifacts/bootloader.bin').read_bytes();parts=(BUNDLE/'artifacts/partitions.bin').read_bytes();old=OLD.read_bytes()
        if len(data)!=SCOPE['backup_end'] or data[:len(boot)]!=boot or data[0x8000:0x8000+len(parts)]!=parts or data[0x10000:0x10000+len(old)]!=old:raise RuntimeError('Current boot/partition/app differs; no write')
        nvs=session.folder/'nvs-before.bin';nvs.write_bytes(data[0x9000:0xe000])
        result['backup']=dict(path=snapshot.relative_to(ROOT).as_posix(),sha256=sha(snapshot),bytes=len(data),reused_double_read_baseline=True,verified_against_current_flash=True,nvs_sha256=sha(nvs))
        (session.folder/'backup.json').write_text(json.dumps(result['backup'],indent=2),encoding='utf-8')
        changed=True
        written=session.esptool(tf.unique_port(),['write_flash','--flash_mode','keep','--flash_freq','keep','--flash_size','keep','0x10000',str(candidate)])
        if 'Hash of data verified' not in written:raise RuntimeError('Write verification missing')
        with session.serial() as port:
            text=session.collect(port,6)
            if 'Guru Meditation' in text or 'assert failed:' in text:raise RuntimeError('Candidate startup panic')
            recorder.boot_check(session,port,SOURCE)
            host,hostlog,url=start_host(path,session.folder,'initial');result['usb_bridge']=url
            env=dict(os.environ,NODE_PATH=str(MODULES),BREADBOARD_V2_APPROVAL=str(path.resolve()))
            browser_log=(session.folder/'browser-console.log').open('w',encoding='utf-8')
            browser=subprocess.Popen([str(NODE),str(ROOT/'tools/controller_v2_live.cjs'),url,str(session.folder)],stdin=subprocess.PIPE,stdout=browser_log,stderr=subprocess.STDOUT,text=True,encoding='utf-8',env=env)
            deadline=time.monotonic()+720;all_text=''
            signal=session.folder/'request-close-host.json'
            while browser.poll() is None:
                chunk=port.read(4096).decode('utf-8',errors='replace')
                if chunk:session.emit(chunk.rstrip());all_text+=chunk
                if any(token in all_text for token in ['Guru Meditation','assert failed:','[rec] FAIL','rst:']):raise RuntimeError('Candidate reset/panic/recording failure')
                if signal.exists():
                    signal.unlink();close_host(host);hostlog.close();host=None
                    session.emit('HOST_CLOSE_REQUESTED; same DeviceBridge.Dispose as desktop')
                    session.collect(port,12)
                    host,hostlog,url=start_host(path,session.folder,'reopened')
                    browser.stdin.write(url+'\n');browser.stdin.flush()
                if time.monotonic()>deadline:raise RuntimeError('Functional trial timeout')
            all_text+=session.collect(port,2);browser_log.close()
            if browser.returncode:raise RuntimeError('Functional browser trial failed; see browser-console.log')
            result['browser']=json.loads((session.folder/'browser-result.json').read_text(encoding='utf-8'))
            if not result['browser'].get('passed'):raise RuntimeError('Browser did not confirm pass')
            result['records']=[dict(run=m[1],samples=int(m[2]),sha256=m[3]) for m in re.finditer(r'\[rec\] PASS run=([0-9A-F]{8}) samples=(\d+).*?sha256=([0-9a-f]{64})',all_text)]
            final=session.query(port,'l',3)
            if final.count('PC_MATCH=1')!=2 or 'sample reads consistent=1' not in final:raise RuntimeError('Reference TF files regression')
        close_host(host);host=None
        result['passed']=True;session.emit('CONTROLLER_V2_TRIAL_PASS')
    except Exception as error:
        result['error']=repr(error);session.emit('CONTROLLER_V2_TRIAL_FAIL='+repr(error))
        if browser and browser.poll() is None:browser.terminate();browser.wait(timeout=10)
        close_host(host);host=None
        if changed:
            try:
                with session.serial() as port:session.query(port,'REC_STOP',2);session.collect(port,12)
                if sha(snapshot)!=result['backup']['sha256']:raise RuntimeError('Recovery snapshot changed')
                recorder.restore(session,snapshot,OLD_SOURCE)
                current=session.folder/'nvs-after-failure.bin'
                session.esptool(tf.unique_port(),['read_flash','0x9000','0x5000',str(current)])
                if sha(current)!=result['backup']['nvs_sha256']:
                    session.esptool(tf.unique_port(),['write_flash','--flash_mode','keep','--flash_freq','keep','--flash_size','keep','0x9000',str(nvs)])
                    with session.serial() as port:session.collect(port,5);recorder.boot_check(session,port,OLD_SOURCE)
                result['restored_before_flash']=True
            except Exception as recovery:result['recovery_error']=repr(recovery);session.emit('RECOVERY_ERROR='+repr(recovery))
        raise
    finally:
        if hostlog:hostlog.close()
        (session.folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        session.emit('RESULT='+str(session.folder/'result.json'));session.log.close()
def main():
    sys.stdout.reconfigure(encoding='utf-8',errors='replace');sys.stderr.reconfigure(encoding='utf-8',errors='replace')
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--prepare-only',action='store_true');parser.add_argument('--probe-usb-only',action='store_true');parser.add_argument('--approval-record',type=Path);args=parser.parse_args()
    if args.prepare_only:
        candidate,files=inputs();print(json.dumps(dict(hardware_accessed=False,scope=SCOPE,files={p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else p.as_posix():sha(p) for p in files}),indent=2))
    else:
        if args.approval_record is None:raise RuntimeError('Explicit approval required before device discovery')
        if args.probe_usb_only:probe_usb(args.approval_record)
        else:run(args.approval_record)
if __name__=='__main__':main()
