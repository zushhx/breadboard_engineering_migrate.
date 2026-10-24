"""Explicitly approved F range/AP trial. Build and CI never call this entry point."""
from pathlib import Path
import argparse, contextlib, datetime, hashlib, json, os, secrets, subprocess, sys, time
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from project_check import ROOT, BUNDLE, sha
from operation_gate import require_approval
from ui_live_trial import TrialSession
import tf_write_batch as tf
import sensor_recorder_batch as rec
import parse_sensor_record as parser

SOURCE='421da251210e54a7fcd1eea12d10104c117a0f83'
OLD_SOURCE='360522d53544cdafdf5e0a33da9dd1e7d2e7543f'
CANDIDATE_SHA='46aa94883f68c783ee2050ef54139e1e91eba035036ee5dda21e6bbfa49bc944'
OLD_SHA='7fa116e326f59c194172607ee52305078729bcc9b409d56f6949874e780d25fe'
MANIFEST=ROOT/'local-data/build/sensor-web/5a5e2d3126851a59/20261024_114245_417538/build_manifest.json'
OLD_ROOT=Path('C:/Users/acer/.codex/worktrees/wifi-tf-240-f/直插式面包板样机')
OLD_MANIFEST=OLD_ROOT/'local-data/build/sensor-web/f90de84d6ec276bb/20261020_222729_256699/build_manifest.json'
HOST=ROOT/'local-data/hardware/f-range-hotspot/host/DeviceHost.exe'
OPERATION='f-range-hotspot-trial'
SCOPE=dict(app0_only=True,backup_end=0x125000,backup_nvs=True,ranges=[2,8,16],seconds_each=3,
           temporary_ap_password=True,restore_user_network=True,preserve_existing_files=True,
           delete_format_repair=False,cold_starts_manual_only=True,restore_on_failure=True,
           desktop_candidate='b9e190d',main_merge_release=False)

def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')

def inputs():
    build=json.loads(MANIFEST.read_text(encoding='utf-8'))
    old_build=json.loads(OLD_MANIFEST.read_text(encoding='utf-8'))
    candidate=ROOT/next(a['path'] for a in build['artifacts'] if a['path'].endswith('/firmware.bin'))
    old=OLD_ROOT/next(a['path'] for a in old_build['artifacts'] if a['path'].endswith('/firmware.bin'))
    for b,source in ((build,SOURCE),(old_build,OLD_SOURCE)):
        if b['source_commit']!=source or b['working_tree_dirty_at_start'] or not b['build_passed']:
            raise RuntimeError('Build identity mismatch')
    if sha(candidate)!=CANDIDATE_SHA or sha(old)!=OLD_SHA:raise RuntimeError('Image hash mismatch')
    for name,digest in build['inputs'].items():
        if not name.startswith('PRIVATE_GENERATED/') and sha(ROOT/name)!=digest:
            raise RuntimeError('Candidate input changed: '+name)
    if build['sdkconfig_generated_sha256']!=old_build['sdkconfig_generated_sha256']:
        raise RuntimeError('SDK configuration differs from F')
    if build['staged_generated_sha256']['wifi_credentials.h']!=old_build['staged_generated_sha256']['wifi_credentials.h']:
        raise RuntimeError('Generated credentials differ from F')
    if build['credentials_present'] or build['private_firmware_do_not_publish']:
        raise RuntimeError('Expected empty build credentials')
    if ((0x10000+max(candidate.stat().st_size,old.stat().st_size)+4095)//4096)*4096!=SCOPE['backup_end']:
        raise RuntimeError('Backup extent mismatch')
    files=[Path(__file__),MANIFEST,OLD_MANIFEST,candidate,old,HOST,Path(str(HOST)+'.config'),tf.ESPTOOL,
           BUNDLE/'artifacts/bootloader.bin',BUNDLE/'artifacts/partitions.bin']
    files += [ROOT/'tools'/p for p in ('operation_gate.py','project_check.py','ui_live_trial.py','tf_write_batch.py','sensor_recorder_batch.py','parse_sensor_record.py','f_range_wifi_check.py')]
    files += [ROOT/'desktop-controller'/p for p in ('src/Core.cs','src/DeviceBridge.cs','tools/DeviceHost.cs','toolchain.json')]
    return candidate,old,files

@contextlib.contextmanager
def bridge(approval,folder):
    from serial.tools import list_ports
    ports=[p.device for p in list_ports.comports() if p.vid==0x303a and p.pid==0x1001]
    if len(ports)!=1:raise RuntimeError('Expected unique native USB device')
    log=(folder/('host-'+secrets.token_hex(4)+'.log')).open('x',encoding='utf-8')
    process=subprocess.Popen([str(HOST),'--approved-usb-host',ports[0]],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                             stderr=log,text=True,encoding='utf-8',env=dict(os.environ,BREADBOARD_V2_APPROVAL=str(approval.resolve())))
    try:
        line=process.stdout.readline().strip()
        if not line.startswith('BRIDGE_URL='):raise RuntimeError('USB host did not start')
        yield line.split('=',1)[1]
    finally:
        if process.poll() is None:
            process.stdin.write('exit\n');process.stdin.flush()
            try:process.wait(timeout=15)
            except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
        log.close()

def api(url,path,body=None):
    headers={'Origin':url.rstrip('/')}
    if body is not None:headers['Content-Type']='application/json'
    request=Request(url+path.lstrip('/'),data=None if body is None else json.dumps(body).encode(),headers=headers)
    with urlopen(request,timeout=20) as response:return json.load(response)

def ready(url):
    s=api(url,'api/status')
    if (s['phase'] not in ('idle','passed') or s['errors'] or not s['ready'] or s['files_busy'] or
        s['card_serial']!=tf.CARD_SERIAL or int(s['card_bytes'])!=tf.CAPACITY):
        raise RuntimeError('Device busy/unready or wrong card')
    return s

def network_save(url,config):
    body={k:config[k] for k in ('sta_enabled','ap_enabled','ssid','password','ap_ssid','ap_password')}
    body['boot']=ready(url)['boot']
    api(url,'api/v2/network/save',body)
    for _ in range(40):
        time.sleep(.25);n=api(url,'api/v2/network/state')
        if n['applying']:continue
        if not n['apply_ok'] or any(n[k]!=body[k] for k in ('sta_enabled','ap_enabled','ssid','ap_ssid','ap_password')):
            raise RuntimeError('Network application/readback mismatch')
        return n
    raise RuntimeError('Network application timeout')

def capture(session,port,url,range_g):
    s=ready(url);client=secrets.token_hex(16);run=secrets.token_hex(4).upper()
    base={'boot':s['boot'],'client':client}
    api(url,'api/v2/session/open',base)
    try:
        api(url,'api/v2/session/start',dict(base,run=run,seconds=3,range_g=range_g,
            local_time=datetime.datetime.now().strftime('%y-%m-%d-%H.%M.%S'),card_serial=tf.CARD_SERIAL,card_bytes=str(tf.CAPACITY)))
        deadline=time.monotonic()+40;uart='';started=False;touch=time.monotonic()
        while time.monotonic()<deadline:
            uart+=session.collect(port,.2)
            if any(p in uart for p in ('Guru Meditation','assert failed:','[rec] FAIL','rst:')):
                raise RuntimeError('Capture failure/reset')
            if time.monotonic()-touch>2:api(url,'api/v2/session/touch',base);touch=time.monotonic()
            state=api(url,'api/status')
            if state['boot']!=base['boot']:raise RuntimeError('Boot changed during capture')
            if state['run']==run:
                started=True
                if state['range_g']!=range_g or state['scale_micro_g']!={2:61,8:244,16:488}[range_g]:raise RuntimeError('Status range mismatch')
                if state['phase']=='failed':raise RuntimeError('Capture terminal failure')
                if state['phase']=='passed':break
        else:raise RuntimeError('Capture timeout')
        if not started or state['errors']:raise RuntimeError('Capture did not complete')
        uart+=session.collect(port,.5)
        terminal=rec.accepted_reply(uart,run,3)
        request=Request(url+'api/v2/download?'+urlencode({'path':state['record_path']}))
        with urlopen(request,timeout=60) as response:data=response.read(4*1024*1024+1)
        path=session.folder/(run+'.BIN');path.write_bytes(data)
        decoded=parser.validate(path,preview_limit=0);decoded.pop('preview',None)
        if decoded['sha256']!=state['sha256'] or decoded['metadata']['source']!=SOURCE or decoded['metadata']['scale_micro_g']!={2:61,8:244,16:488}[range_g]:
            raise RuntimeError('Downloaded record metadata/hash mismatch')
        if terminal['sha256']!=decoded['sha256'] or terminal['samples']!=decoded['footer']['samples']:
            raise RuntimeError('UART terminal differs from downloaded footer/hash')
        if decoded['footer']['ina_invalid'] or decoded['footer']['saturation']:raise RuntimeError('INA invalid or saturation')
        if f'range_g={range_g} scale_micro_g=' not in uart:raise RuntimeError('Missing actual start range evidence')
        write(session.folder/(run+'-summary.json'),decoded)
        return dict(run=run,range_g=range_g,path=state['record_path'],sha256=decoded['sha256'],footer=decoded['footer'])
    finally:api(url,'api/v2/session/release',base)

def validate(approval):
    candidate,old,files=inputs()
    record=require_approval(ROOT,approval,OPERATION,tf.MAC,files)
    if record.get('scope')!=SCOPE:raise RuntimeError('Approval scope mismatch')
    return candidate,old,record

def run(approval,action,folder=None,confirmation=None):
    candidate,old,record=validate(approval)
    if action=='cold' and not confirmation:raise RuntimeError('Human full-power-off confirmation required')
    if action=='initial':
        folder=ROOT/'local-data/hardware/f-range-hotspot'/datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    if folder is None or not folder.resolve().is_relative_to((ROOT/'local-data/hardware/f-range-hotspot').resolve()):
        raise RuntimeError('Invalid trial folder')
    root_folder=folder
    if action!='initial':folder=folder/('check-'+action+'-'+datetime.datetime.now().strftime('%H%M%S'))
    session=TrialSession(folder);changed=False;result={'action':action,'passed':False,'operation_commit':record['source_commit']}
    snapshot=folder/'controller-prefix.bin';nvs=folder/'nvs-before.bin'
    try:
        if action=='initial':
            with bridge(approval,folder) as url:result['before']=ready(url)
            with session.serial() as port:rec.boot_check(session,port,OLD_SOURCE)
            identity=session.esptool(tf.unique_port(),['flash_id'])
            if 'Detected flash size: 8MB' not in identity:raise RuntimeError('Flash size mismatch')
            session.esptool(tf.unique_port(),['read_flash','0',hex(SCOPE['backup_end']),str(snapshot)])
            data=snapshot.read_bytes();boot=(BUNDLE/'artifacts/bootloader.bin').read_bytes();parts=(BUNDLE/'artifacts/partitions.bin').read_bytes()
            if len(data)!=SCOPE['backup_end'] or data[:len(boot)]!=boot or data[0x8000:0x8000+len(parts)]!=parts or data[0x10000:0x10000+old.stat().st_size]!=old.read_bytes():
                raise RuntimeError('Current backup differs from F; no write')
            session.esptool(tf.unique_port(),['verify_flash','0',str(snapshot)])
            nvs.write_bytes(data[0x9000:0xe000])
            result['backup']={'sha256':sha(snapshot),'nvs_sha256':sha(nvs),'bytes':len(data),'chip_verified':True}
            write(folder/'backup.json',result['backup']);changed=True
            identity=session.esptool(tf.unique_port(),['flash_id'])
            if 'Detected flash size: 8MB' not in identity:raise RuntimeError('Prewrite identity changed')
            session.esptool(tf.unique_port(),['write_flash','--flash_mode','keep','--flash_freq','keep','--flash_size','keep','0x10000',str(candidate)])
            session.esptool(tf.unique_port(),['verify_flash','0x10000',str(candidate)])
        with session.serial() as port:
            if action=='initial':session.collect(port,6)
            rec.boot_check(session,port,SOURCE,cold=action=='cold')
            if action=='cold':result['power_off_confirmation']=confirmation
            with bridge(approval,folder) as url:
                result['status']=ready(url)
                if action in ('initial','capture'):
                    result['records']=[capture(session,port,url,g) for g in (2,8,16)]
                if action=='network':
                    original=json.loads((root_folder/'network-private.json').read_text(encoding='utf-8'))
                    original['password']=''  # Same STA SSID retains its existing secret in device NVS.
                    test=dict(original,ap_enabled=True,ap_password=secrets.token_hex(8))
                    write(root_folder/'network-test-private.json',test)
                    applied=network_save(url,test)
                    result['new_ap_password_applied_and_readback']=True
                    from f_range_wifi_check import check_password_sequence
                    session.emit('AP_READBACK_CONFIRMED; starting isolated new/old/new Wi-Fi authentication checks')
                    checked=check_password_sequence(ssid=test['ap_ssid'],new_password=test['ap_password'],
                        old_password=original['ap_password'],profile_path=root_folder/'wifi-test-private.xml',approval=True)
                    write(folder/'wifi-result-private.json',checked)
                    result['wifi']=checked
                    if not checked.get('password_change_confirmed'):
                        network_save(url,original)
                        result['original_network_restored']=True
                        raise RuntimeError('Wi-Fi new/old/new sequence not confirmed; original network restored')
                if action=='restore-network':
                    original=json.loads((root_folder/'network-private.json').read_text(encoding='utf-8'))
                    original['password']=''
                    network_save(url,original);result['original_network_restored']=True
                state=api(url,'api/v2/network/state')
                if action=='cold' and (root_folder/'network-test-private.json').exists():
                    expected=json.loads((root_folder/'network-test-private.json').read_text(encoding='utf-8'))
                    if any(state[k]!=expected[k] for k in ('ap_enabled','ap_ssid','ap_password','sta_enabled','ssid')):
                        raise RuntimeError('Cold-start network persistence mismatch')
                    result['new_ap_password_persisted']=True
                # Secrets stay only in ignored private files, never in console or result.
                write(folder/'network-private.json',state)
                result['network']={k:state[k] for k in ('running','apply_ok','applying','connected','sta_enabled','ap_enabled')}
            refs=session.query(port,'l',3)
            if refs.count('PC_MATCH=1')!=2 or 'sample reads consistent=1' not in refs:raise RuntimeError('Reference regression')
        result['passed']=True
    except Exception as error:
        result['error']=type(error).__name__+': '+str(error)
        if action=='network':
            try:
                original=json.loads((root_folder/'network-private.json').read_text(encoding='utf-8'))
                original['password']=''
                with bridge(approval,folder) as url:network_save(url,original)
                result['original_network_restored']=True
            except Exception as recovery:result['network_recovery_error']=repr(recovery)
        if changed:
            try:
                if sha(snapshot)!=result['backup']['sha256'] or sha(nvs)!=result['backup']['nvs_sha256']:raise RuntimeError('Recovery snapshot changed')
                with session.serial() as port:
                    session.query(port,'REC_STOP',2)
                    help_text=session.query(port,'help',1)
                    if f'identity source={SOURCE} ' not in help_text:raise RuntimeError('Recovery source unconfirmed; preserve evidence')
                with bridge(approval,folder) as url:
                    for _ in range(60):
                        state=api(url,'api/status')
                        if state['phase'] in ('idle','passed','failed','rejected') and not state['files_busy'] and not state['pending_owner']:break
                        time.sleep(.5)
                    else:raise RuntimeError('Recovery waits for confirmed TF closure; preserve evidence')
                rec.restore(session,snapshot,OLD_SOURCE)
                session.esptool(tf.unique_port(),['verify_flash','0x10000',str(folder/'restore_app_range.bin')])
                after=folder/'nvs-after-recovery.bin'
                session.esptool(tf.unique_port(),['read_flash','0x9000','0x5000',str(after)])
                if sha(after)!=sha(nvs):
                    session.esptool(tf.unique_port(),['write_flash','--flash_mode','keep','--flash_freq','keep','--flash_size','keep','0x9000',str(nvs)])
                    session.esptool(tf.unique_port(),['verify_flash','0x9000',str(nvs)])
                with session.serial() as port:session.collect(port,6);rec.boot_check(session,port,OLD_SOURCE)
                result['restored_f']=True
            except Exception as recovery:result['recovery_error']=repr(recovery)
        raise
    finally:
        write(folder/'result.json',result);session.emit('RESULT='+str(folder/'result.json'));session.log.close()

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--prepare-only',action='store_true')
    ap.add_argument('--approval-record',type=Path);ap.add_argument('--action',choices=['initial','capture','network','restore-network','cold'],default='initial')
    ap.add_argument('--folder',type=Path);ap.add_argument('--power-off-confirmation');args=ap.parse_args()
    if args.prepare_only:
        _,_,files=inputs();print(json.dumps({'scope':SCOPE,'files':{p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else p.as_posix():sha(p) for p in files}},indent=2))
    else:run(args.approval_record,args.action,args.folder,args.power_off_confirmation)
