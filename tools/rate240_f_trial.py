"""Gated F/range benchmark. Prepare, flash and test are separate explicit phases."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import datetime
import ipaddress
import json
from pathlib import Path
import re
import secrets
import socket
import threading
import time
import urllib.request

from operation_gate import require_approval
from project_check import ROOT, BUNDLE, sha
import tf_write_batch as tf
import sensor_recorder_batch as recorder
import ui_live_trial as prior
import sensor_web_batch as web
import rate190_receive as rx

OLD_SOURCE = '421da251210e54a7fcd1eea12d10104c117a0f83'
OLD_SHA = '46aa94883f68c783ee2050ef54139e1e91eba035036ee5dda21e6bbfa49bc944'
OLD_BYTES = 1133392
TRACE_LOCK=threading.Lock()


def trace(folder,event,**fields):
    entry=dict(wall_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
               monotonic_ns=time.monotonic_ns(),event=event,**fields)
    # Evidence failure must never prevent the existing stop/recovery path.
    try:
        with TRACE_LOCK,(Path(folder)/'events.jsonl').open('a',encoding='utf-8') as output:
            output.write(json.dumps(entry,ensure_ascii=False)+'\n');output.flush()
    except OSError:
        pass


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def inputs(manifest, old_manifest, host):
    addr = ipaddress.IPv4Address(host)
    if not any(addr in ipaddress.IPv4Network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')):
        raise ValueError('Explicit RFC1918 host required')
    identity, files = rx.candidate_identity(manifest)
    old_identity, old_files = rx.candidate_identity(old_manifest)
    build = json.loads(Path(manifest).read_text(encoding='utf-8'))
    old_build = json.loads(Path(old_manifest).read_text(encoding='utf-8'))
    candidate, old = files[1], old_files[1]
    if not any(n.endswith('/sensor_range.h') for n in build['inputs']):
        raise RuntimeError('Expected unified range/benchmark candidate')
    for name,digest in build['inputs'].items():
        if name.startswith('PRIVATE_GENERATED/'):
            continue
        source=(ROOT/name).resolve()
        if not source.is_relative_to(ROOT) or sha(source)!=digest:
            raise RuntimeError('Candidate build source changed: '+name)
        files.append(source)
    if old_identity['source'] != OLD_SOURCE or sha(old) != OLD_SHA or old.stat().st_size != OLD_BYTES:
        raise RuntimeError('Current F recovery identity mismatch')
    if identity['source'] == OLD_SOURCE:
        raise RuntimeError('Expected new benchmark candidate')
    for field in ('sdkconfig_generated_sha256',):
        if build[field] != old_build[field]:
            raise RuntimeError('SDK configuration changed')
    if (build['credentials_present'] or
        build['staged_generated_sha256']['wifi_credentials.h'] != old_build['staged_generated_sha256']['wifi_credentials.h']):
        raise RuntimeError('Generated credentials changed')
    for folder, record in ((Path(manifest).parent,build),(Path(old_manifest).parent,old_build)):
        config=folder/'sdkconfig.generated'
        if sha(config)!=record['sdkconfig_generated_sha256']:
            raise RuntimeError('Generated SDK evidence changed')
        files.append(config)
        for item in record['artifacts']:
            if item['path'].endswith('/partitions.bin'):
                part=ROOT/item['path']
                if sha(part)!=sha(BUNDLE/'artifacts/partitions.bin'):
                    raise RuntimeError('Partition changed')
                files.append(part)
    files += old_files+[tf.ESPTOOL,BUNDLE/'artifacts/bootloader.bin',BUNDLE/'artifacts/partitions.bin']
    files += [ROOT/'tools'/n for n in ('rate240_f_trial.py','rate190_receive.py','rate240_receive_sink.py','operation_gate.py',
        'project_check.py','tf_write_batch.py','sensor_recorder_batch.py','sensor_monitor_batch.py',
        'sensor_web_batch.py','ui_live_trial.py','prepare_sensor_web_trial.py',
        'build_sensor_web_recorder.py','build_sensor_tf_recorder.py','parse_sensor_record.py')]
    scope=dict(host=host,candidate=identity,candidate_sha256=sha(candidate),old_source=OLD_SOURCE,
        old_sha256=OLD_SHA,old_bytes=OLD_BYTES,app0_only=True,backup_end=0x10000+((max(candidate.stat().st_size,old.stat().st_size)+4095)//4096)*4096,
        ram_wifi_seconds=[30],ram_rate_policy='absolute-pacing-bounded-catchup-whole-run-1pct',
        tcp_send_policy='TCP_NODELAY-required-on-device-accepted-socket',
        pc_receive_policy='background-bounded-16k-post-capture-pattern-check',
        network_stable_seconds_before_test=10,listener_setup_retry_seconds=150,
        listener_retry_policy='refused-only-rearm-no-request-replay',
        synthetic_seconds=[30,200],real_seconds=[30,200],real_range_g=2,range_smoke_seconds=3,
        range_smoke_g=[8,16],restore_on_failure=True,restore_same_round_nvs_if_changed=True,
        recovery_policy='stop-confirm-source-boot-and-tf-closed-before-reset',
        mcu_reset=True,phases=['flash','test','cold1','cold2'],cold_failure_manual_review=True,
        preserve_existing_files=True,format_delete_repair=False,device_network_changes=False,pc_network_return_allowed=True,
        cold_starts_pending=2,main_merge_release=False)
    return candidate,old,identity,list(dict.fromkeys(p.resolve() for p in files)),scope


def approved(args):
    if args.approval_record is None:
        raise RuntimeError('Genuine new candidate approval required before discovery')
    candidate,old,identity,files,scope=inputs(args.manifest,args.old_manifest,args.host)
    record=require_approval(ROOT,args.approval_record,'rate240-f-trial',tf.MAC,files)
    if record.get('scope')!=scope or record.get('hardware_ready_confirmed') is not True or record.get('hardware_exclusive_confirmed') is not True:
        raise RuntimeError('Scope/readiness/exclusive device confirmation missing')
    return candidate,old,identity,record,scope


class Http:
    def __init__(self,host):
        self.host=host
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):
                raise RuntimeError('Redirect rejected')
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    def request(self,path,data=None,key=None):
        return self.request_once(path,data,key)

    def request_once(self,path,data=None,key=None,timeout=4):
        headers={'Content-Type':'application/json'}
        if key:headers['X-Record-Key']=key
        body=None if data is None else json.dumps(data).encode('utf-8')
        req=urllib.request.Request('http://'+self.host+path,data=body,headers=headers)
        with self.opener.open(req,timeout=timeout) as response:
            raw=response.read(65537)
        if len(raw)>65536:raise RuntimeError('Oversized HTTP response')
        return json.loads(raw)


def quiet(status):
    if (status.get('phase') not in ('idle','passed') or status.get('errors')!=0 or
        status.get('ready') is not True or status.get('files_busy') is not False or
        status.get('card_serial')!=tf.CARD_SERIAL or str(status.get('card_bytes'))!=str(tf.CAPACITY) or
        not re.fullmatch('[0-9a-fA-F]{32}',str(status.get('boot','')))):
        raise RuntimeError('Device not idle/ready, files active or wrong card')


def backup_check(data,nvs,old,extent):
    prior.check_backup(data,extent,(BUNDLE/'artifacts/bootloader.bin').read_bytes(),
                       (BUNDLE/'artifacts/partitions.bin').read_bytes(),old)
    if len(nvs)!=0x5000 or data[0x9000:0xe000]!=nvs:
        raise RuntimeError('Same-round NVS does not match prefix')


def recovery_terminal(status):
    return (status.get('phase') in ('idle','passed','failed','rejected') and
            status.get('files_busy') is False and status.get('pending_owner') == '' and
            status.get('card_serial') == tf.CARD_SERIAL and
            str(status.get('card_bytes')) == str(tf.CAPACITY) and
            re.fullmatch('[0-9a-fA-F]{32}',str(status.get('boot',''))) is not None)


def stop_and_wait_closed(session,http,identity,result,clock=time.monotonic,sleep=time.sleep):
    """Request a stop, then confirm storage quiescence before any esptool reset."""
    deadline=clock()+120
    with session.serial() as port:
        trace(session.folder,'stop_submit')
        session.query(port,'RATE240_STOP',1);session.query(port,'REC_STOP',1)
        while clock()<deadline:
            # One bounded read only; no automatic AP reconnection during recovery proof.
            status=http.request_once('/api/status',timeout=min(4,max(.01,deadline-clock())))
            if recovery_terminal(status):
                expected=f'identity source={identity["source"]} '
                if expected not in session.query(port,'help',2):
                    raise RuntimeError('Recovery candidate identity unconfirmed; preserve evidence')
                after=http.request_once('/api/status',timeout=min(4,max(.01,deadline-clock())))
                if not recovery_terminal(after) or after['boot']!=status['boot']:
                    raise RuntimeError('Recovery storage/boot changed during closure check')
                result['tf_closed_before_recovery']=dict(source=identity['source'],boot=after['boot'],
                    phase=after['phase'],files_busy=False,pending_owner='',confirmed=True)
                trace(session.folder,'tf_closed_before_recovery',phase=after['phase'],boot=after['boot'])
                return
            sleep(min(.5,max(0,deadline-clock())))
    raise RuntimeError('Recovery waits for confirmed TF closure; preserve evidence')


def restore(session,result,old):
    backup=result['backup'];snapshot=session.folder/'controller_prefix.bin';nvs=session.folder/'nvs-before.bin'
    closure=result.get('tf_closed_before_recovery',{})
    if result.get('test_started') and (closure.get('confirmed') is not True or
            closure.get('source')!=result['source']['source']):
        raise RuntimeError('TF closure required before recovery reset')
    if sha(snapshot)!=backup['sha256'] or sha(nvs)!=backup['nvs_sha256'] or sha(old)!=OLD_SHA:
        raise RuntimeError('Recovery evidence changed')
    trace(session.folder,'recovery_esptool_begin')
    session.esptool(tf.unique_port(),['flash_id'])
    session.esptool(tf.unique_port(),['write_flash','--flash_mode','keep','--flash_freq','keep','--flash_size','keep','0x10000',str(old)])
    session.esptool(tf.unique_port(),['verify_flash','0x10000',str(old)])
    after=session.folder/'nvs-after-recovery.bin'
    session.esptool(tf.unique_port(),['read_flash','0x9000','0x5000',str(after)])
    if sha(after)!=backup['nvs_sha256']:
        session.esptool(tf.unique_port(),['write_flash','--flash_mode','keep','--flash_freq','keep','--flash_size','keep','0x9000',str(nvs)])
        session.esptool(tf.unique_port(),['verify_flash','0x9000',str(nvs)])
    with session.serial() as port:
        session.collect(port,9);recorder.boot_check(session,port,OLD_SOURCE)
    result['restored_f']=True


def wait_stable_status(http,seconds=10,clock=time.monotonic,sleep=time.sleep):
    deadline=clock()+90;since=None;boot=None;generation=None
    while clock()<deadline:
        status=http.request('/api/status');quiet(status)
        current_generation=getattr(http,'stability_generation',0)
        if since is None or boot!=status['boot'] or generation!=current_generation:
            since=clock();boot=status['boot'];generation=current_generation
        if clock()-since>=seconds:return boot
        sleep(1)
    raise RuntimeError('Network/boot did not remain stable before test')


def ram_stage(session,port,http,identity,seconds=30,expected_boot=None,expected_generation=None):
    before=http.request('/api/status');quiet(before);key=before['boot']
    if expected_boot is not None and key!=expected_boot:
        raise RuntimeError('Boot changed after stability wait; no benchmark started')
    if expected_generation is not None and getattr(http,'stability_generation',0)!=expected_generation:
        raise RuntimeError('Network reconnected after stability wait; no benchmark started')
    folder=session.folder/f'ram-wifi-{seconds}';folder.mkdir()
    arm=session.query(port,'RATE240_ARM',.5)
    if '[rate240] explicit service arm=1' not in arm:raise RuntimeError('RAM WiFi service ARM failed')
    def ready(info):
        trace(session.folder,'ram_ready_received');save(folder/'ready.json',info)
    with socket.create_connection((http.host,19000),3) as sock,ThreadPoolExecutor(max_workers=1) as pool:
        trace(session.folder,'ram_request_submit',seconds=seconds)
        sock.sendall(rx.request_line(key,'SYNTH',seconds))
        future=pool.submit(rx.receive,sock,'SYNTH',seconds,folder/'payload.bin',
                           expected_format='pattern',identity=identity,on_ready=ready,background_write=True)
        deadline=time.monotonic()+seconds+30
        try:
            while not future.done():
                chunk=port.read(4096).decode('utf-8',errors='replace')
                if chunk:
                    session.emit(chunk.rstrip())
                    if any(t in chunk for t in ('Guru Meditation','assert failed:','rst:')):
                        raise RuntimeError('Reset/panic during RAM WiFi test')
                if time.monotonic()>deadline:raise RuntimeError('RAM WiFi deadline')
            network=future.result()
            if not network['passed']:raise RuntimeError('RAM WiFi rate/duration incomplete')
            after=http.request('/api/status');quiet(after)
            if after['boot']!=key:raise RuntimeError('Boot changed during RAM WiFi test')
            result=dict(passed=True,kind='ram',seconds=seconds,network=network)
            save(folder/'result.json',result);return result
        except Exception as error:
            trace(session.folder,'ram_failure',error=repr(error))
            save(folder/'failure.json',dict(passed=False,error=repr(error)));raise
        finally:
            try:sock.shutdown(socket.SHUT_RDWR)
            except OSError:pass


def connect_service(session,port,http,key,clock=time.monotonic,sleep=time.sleep):
    # No request/source is submitted here. A refused TCP open is safe to retry.
    deadline=clock()+150;attempt=0
    while clock()<deadline:
        status=http.request_once('/api/status',timeout=min(4,deadline-clock()));quiet(status)
        if clock()>=deadline:break
        if status['boot']!=key:raise RuntimeError('Boot changed during listener setup')
        attempt+=1
        reply=session.query(port,'RATE240_ARM',.5)
        matches=re.findall(r'(?m)^\[rate240\] explicit service arm=([01])\r?$',reply)
        if len(matches)!=1:raise RuntimeError('Ambiguous service ARM response')
        trace(session.folder,'listener_arm',attempt=attempt,armed=int(matches[0]))
        if clock()>=deadline:break
        try:
            sock=socket.create_connection((http.host,19000),min(3,deadline-clock()))
        except ConnectionRefusedError as error:
            trace(session.folder,'listener_refused',attempt=attempt,error=repr(error))
            sleep(min(2,max(0,deadline-clock())))
            continue
        if clock()>=deadline:
            sock.close()
            break
        trace(session.folder,'listener_connected',attempt=attempt)
        return sock
    raise RuntimeError('Listener setup refused throughout 150-second retry window')


def stage(session,port,http,identity,kind,seconds,range_g=2):
    before=http.request('/api/status');quiet(before);key=before['boot']
    run=secrets.token_hex(4).upper();client=secrets.token_hex(16)
    folder=session.folder/f'{kind}-{seconds}-{range_g}g-{run}';folder.mkdir()
    ready=threading.Event()
    def on_ready(info):
        trace(session.folder,'ready_received',run=run,kind=kind)
        save(folder/'ready.json',info);ready.set()
    with connect_service(session,port,http,key) as sock:
        sock.sendall(rx.request_line(key,'MIRROR',seconds,run,kind))
        pool=ThreadPoolExecutor(max_workers=1)
        def receive():
            try:
                return rx.receive(sock,'MIRROR',seconds,folder/'payload.bin',expected_format=kind,
                                  identity=identity,expected_run=run,on_ready=on_ready,background_write=True)
            except Exception as error:
                trace(session.folder,'receiver_failure',run=run,error=repr(error))
                raise
        future=pool.submit(receive)
        text='';prefix='[rate240tf]' if kind=='pattern' else '[rec]'
        try:
            deadline=time.monotonic()+8
            while not ready.wait(.05):
                if future.done():future.result();raise RuntimeError('Receiver ended before READY')
                if time.monotonic()>deadline:raise RuntimeError('No READY; no source started')
            if kind=='record':
                http.request('/api/v2/session/open',dict(client=client),key)
                trace(session.folder,'source_submit',run=run,kind=kind,seconds=seconds)
                http.request('/api/v2/session/start',dict(client=client,run=run,seconds=seconds,
                    card_serial=tf.CARD_SERIAL,card_bytes=str(tf.CAPACITY),range_g=range_g),key)
            else:
                command=f'RATE240_TF {tf.CARD_SERIAL} {tf.CAPACITY} {run} {seconds}'
                trace(session.folder,'source_submit',run=run,kind=kind,seconds=seconds)
                session.emit('SEND: '+command);port.write((command+'\n').encode());port.flush()
            deadline=time.monotonic()+seconds+120;touch=time.monotonic()+2
            while time.monotonic()<deadline:
                chunk=port.read(4096).decode('utf-8',errors='replace')
                if chunk:session.emit(chunk.rstrip());text+=chunk
                if any(t in text for t in ('Guru Meditation','assert failed:','rst:',prefix+' FAIL',prefix+' rejected')):
                    raise RuntimeError('Source failed/reset; stop long-test expansion')
                if kind=='record' and time.monotonic()>=touch:
                    http.request('/api/v2/session/touch',dict(client=client),key);touch=time.monotonic()+2
                terminal=re.search(re.escape(prefix)+r' PASS run='+run+r'[^\r\n]*\r?\n',text)
                if future.done():
                    network=future.result()
                    if not network['passed']:raise RuntimeError('Receive incomplete/slow')
                    if terminal:break
            else:raise RuntimeError('Source/receiver deadline')
            if kind=='record':
                record=recorder.accepted_reply(text,run,seconds)
                meta=network['record']['metadata']
                if meta.get('range_g')!=range_g or meta['card_serial']!=tf.CARD_SERIAL or str(meta['card_bytes'])!=str(tf.CAPACITY):
                    raise RuntimeError('Recorded range/card differs')
                if network['record']['footer']['ina_invalid']!=0:
                    raise RuntimeError('INA invalid samples present; acquisition regression failed')
            else:
                match=re.search(r'bytes=(\d+) elapsed_us=(\d+).*sha256=([0-9a-f]{64}) synthetic=1 write_gate=0',terminal[0])
                if not match or int(match[1])!=seconds*240000 or not seconds*990000<=int(match[2])<=seconds*1010000:
                    raise RuntimeError('TF count/duration mismatch')
                record=dict(run=run,seconds=seconds,bytes=int(match[1]),elapsed_us=int(match[2]),sha256=match[3])
            if record['sha256']!=network['sha256']:raise RuntimeError('Serial/TF/PC SHA mismatch')
            after=http.request('/api/status');quiet(after)
            if after['boot']!=key:raise RuntimeError('Boot changed')
            result=dict(passed=True,run=run,kind=kind,seconds=seconds,range_g=range_g,record=record,network=network)
            save(folder/'result.json',result);session.emit(f'STAGE_PASS {kind} {seconds}s range={range_g}g')
            return result
        except Exception as error:
            save(folder/'failure.json',dict(passed=False,error=repr(error)));raise
        finally:
            try:sock.shutdown(socket.SHUT_RDWR)
            except OSError:pass
            pool.shutdown(wait=True,cancel_futures=True)


def run(args):
    candidate,old,identity,approval,scope=approved(args)
    http=Http(args.host)
    if args.phase in ('cold1','cold2'):
        cold_check(args,identity,approval)
        return
    if args.phase=='flash':
        stamp=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y%m%d_%H%M%S')
        folder=ROOT/'local-data/hardware/rate240-f'/stamp
        result=dict(passed=False,flash_verified=False,source=identity,candidate_sha256=sha(candidate),
                    operation_commit=approval['source_commit'],stages=[],cold_starts_completed=0)
    else:
        if args.folder is None:raise RuntimeError('Test requires prior flash evidence folder')
        folder=args.folder.resolve()
        if not folder.is_relative_to(ROOT/'local-data/hardware/rate240-f'):raise RuntimeError('Invalid evidence folder')
        result=json.loads((folder/'result.json').read_text(encoding='utf-8'))
        if (not result.get('flash_verified') or result.get('restored_f') or result['source']!=identity or
                result['candidate_sha256']!=sha(candidate) or result['operation_commit']!=approval['source_commit'] or
                result.get('stages') or result.get('test_started')):
            raise RuntimeError('Flash evidence mismatch or batch already attempted')
    if args.phase=='flash':session=prior.TrialSession(folder)
    else:
        session=object.__new__(prior.TrialSession);session.folder=folder
        session.log=(folder/'hardware.log').open('a',encoding='utf-8')
    changed=False
    try:
        with session.serial() as port:
            before=http.request('/api/status');quiet(before)
            recorder.boot_check(session,port,OLD_SOURCE if args.phase=='flash' else identity['source'])
            after=http.request('/api/status');quiet(after)
            if before['boot']!=after['boot'] or before.get('run')!=after.get('run'):raise RuntimeError('Preflight changed')
        if args.phase=='flash':
            network=http.request('/api/v2/network/state')
            if network.get('applying') is True or network.get('apply_ok') is not True:raise RuntimeError('Network not quiescent')
            save(folder/'network-before-private.json',network)
            info=session.esptool(tf.unique_port(),['flash_id'])
            if 'Detected flash size: 8MB' not in info:raise RuntimeError('Flash size mismatch')
            snapshot=folder/'controller_prefix.bin';nvs=folder/'nvs-before.bin'
            session.esptool(tf.unique_port(),['read_flash','0',hex(scope['backup_end']),str(snapshot)])
            session.esptool(tf.unique_port(),['verify_flash','0',str(snapshot)])
            session.esptool(tf.unique_port(),['read_flash','0x9000','0x5000',str(nvs)])
            session.esptool(tf.unique_port(),['verify_flash','0x9000',str(nvs)])
            backup_check(snapshot.read_bytes(),nvs.read_bytes(),old.read_bytes(),scope['backup_end'])
            result['backup']=dict(sha256=sha(snapshot),nvs_sha256=sha(nvs),bytes=scope['backup_end'])
            save(folder/'result.json',result)
            changed=True
            session.esptool(tf.unique_port(),['write_flash','--flash_mode','keep','--flash_freq','keep','--flash_size','keep','0x10000',str(candidate)])
            session.esptool(tf.unique_port(),['verify_flash','0x10000',str(candidate)])
            with session.serial() as port:
                web.collect_startup(session,port,9);recorder.boot_check(session,port,identity['source'])
            quiet(http.request('/api/status'))
            after_nvs=folder/'nvs-after-flash.bin'
            session.esptool(tf.unique_port(),['read_flash','0x9000','0x5000',str(after_nvs)])
            if sha(after_nvs)!=result['backup']['nvs_sha256']:
                raise RuntimeError('NVS changed during candidate startup')
            with session.serial() as port:
                web.collect_startup(session,port,9);recorder.boot_check(session,port,identity['source'])
            quiet(http.request('/api/status'))
            result['flash_verified']=True
        else:
            changed=True
            result['test_started']=True;save(folder/'result.json',result)
            trace(session.folder,'network_stability_wait_begin',seconds=10)
            stable_boot=wait_stable_status(http)
            stable_generation=getattr(http,'stability_generation',0)
            trace(session.folder,'network_stability_wait_complete',boot=stable_boot)
            with session.serial() as port:
                result['stages'].append(ram_stage(session,port,http,identity,expected_boot=stable_boot,
                                                 expected_generation=stable_generation))
                save(folder/'result.json',result)
                for kind,seconds,range_g in (('pattern',30,2),('pattern',200,2),('record',30,2),('record',200,2),('record',3,8),('record',3,16)):
                    result['stages'].append(stage(session,port,http,identity,kind,seconds,range_g))
                    save(folder/'result.json',result)
                session.emit('MCU_ONLY_RESET; card power retained')
                port.rts=True;time.sleep(.1);port.rts=False
                web.collect_startup(session,port,9)
                # stopInput deliberately writes CTRL1=00 for every range; CTRL6 stays 00.
                recorder.boot_check(session,port,identity['source'],warm=True)
                for stage_result in result['stages']:
                    if stage_result['kind']=='record':recorder.verify(session,port,stage_result['record'])
                result['warm_readback_passed']=True
                warm=http.request('/api/status');quiet(warm);result['warm_boot']=warm['boot']
            result['initial_stage_passed']=True
            result['pending']='Two actual user-operated cold starts and reference/readback checks remain'
    except Exception as error:
        result['error']=repr(error);session.emit('TRIAL_FAIL='+repr(error))
        if changed:
            safe=not result.get('test_started')
            if result.get('test_started'):
                try:
                    stop_and_wait_closed(session,http,identity,result)
                    safe=True
                except Exception as stop_error:result['stop_error']=repr(stop_error)
            try:
                if not safe:raise RuntimeError('TF inactivity unconfirmed; manual recovery required')
                restore(session,result,old)
            except Exception as recovery:result['recovery_error']=repr(recovery)
        raise
    finally:
        save(folder/'result.json',result);session.emit('RESULT='+str(folder/'result.json'));session.log.close()
    return folder


def cold_check(args,identity,approval):
    number=int(args.phase[-1])
    confirmation=approval.get('cold_start_confirmation',{})
    text=confirmation.get('user_confirmation') if isinstance(confirmation,dict) else None
    if (not isinstance(confirmation,dict) or type(confirmation.get('number')) is not int or
        confirmation.get('number')!=number or not isinstance(text,str) or not text.strip()):
        raise RuntimeError('Requires a genuine user confirmation after this numbered full power-off')
    if args.folder is None:raise RuntimeError('Cold check requires this trial evidence folder')
    folder=args.folder.resolve()
    if not folder.is_relative_to(ROOT/'local-data/hardware/rate240-f'):raise RuntimeError('Invalid evidence folder')
    result=json.loads((folder/'result.json').read_text(encoding='utf-8'))
    if (result.get('source')!=identity or not result.get('flash_verified') or not result.get('initial_stage_passed') or result.get('restored_f') or
        result.get('cold_starts_completed')!=number-1 or result.get('operation_commit')!=approval['source_commit'] or
        result.get('candidate_sha256')!=approval['scope']['candidate_sha256']):
        raise RuntimeError('Cold evidence sequence/source mismatch')
    session=object.__new__(prior.TrialSession);session.folder=folder
    session.log=(folder/'hardware.log').open('a',encoding='utf-8')
    try:
        # No RTS/esptool/initialization: register defaults corroborate the user's real power-off.
        http=Http(args.host);before=http.request('/api/status');quiet(before)
        previous=result.get('warm_boot') if number==1 else result.get('cold_boots',[None])[-1]
        if not previous or before['boot']==previous:
            raise RuntimeError('New boot absent; cannot count repeated reads as another cold start')
        with session.serial() as port:
            recorder.boot_check(session,port,identity['source'],cold=True)
            for item in result['stages']:
                if item['kind']=='record':recorder.verify(session,port,item['record'])
            after=http.request('/api/status');quiet(after)
            if after['boot']!=before['boot'] or after.get('run')!=before.get('run'):
                raise RuntimeError('Boot/run changed during cold verification')
        result['cold_starts_completed']=number
        result.setdefault('cold_confirmations',[]).append(confirmation)
        result.setdefault('cold_boots',[]).append(before['boot'])
        if number==2:
            result['passed']=True;result.pop('pending',None)
    except Exception as error:
        result['cold_error']=repr(error)
        raise
    finally:
        save(folder/'result.json',result);session.log.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,required=True);parser.add_argument('--old-manifest',type=Path,required=True)
    parser.add_argument('--host',required=True);parser.add_argument('--approval-record',type=Path)
    parser.add_argument('--phase',choices=('prepare','flash','test','cold1','cold2'),default='prepare');parser.add_argument('--folder',type=Path)
    args=parser.parse_args()
    if args.phase=='prepare':
        _,_,_,files,scope=inputs(args.manifest,args.old_manifest,args.host)
        print(json.dumps(dict(hardware_accessed=False,scope=scope,files={p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else p.as_posix():sha(p) for p in files}),indent=2))
    else:run(args)


if __name__=='__main__':main()
