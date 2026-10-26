"""Local AP trial runner; restores the existing PC WiFi profile even on failure.

Default prepares only. Never depends on cloud tools once the AP test starts.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import time
import urllib.error

from project_check import ROOT, sha
from operation_gate import require_approval
import rate240_f_trial as trial
import wlan_profile


def profile_input(path):
    path=Path(path).resolve()
    if not path.is_relative_to(ROOT/'local-data'):
        raise RuntimeError('Return profile must remain under private local-data')
    profile=json.loads(path.read_text(encoding='utf-8'))
    required=('interface_guid','profile_name','ssid_hex','expected_ap_ssid_hex')
    if any(not isinstance(profile.get(k),str) or not profile[k] for k in required):
        raise RuntimeError('Original PC profile snapshot missing fields')
    wlan_profile.validate_snapshot(profile)
    for key in ('ssid_hex','expected_ap_ssid_hex'):
        raw=bytes.fromhex(profile[key])
        if not 1<=len(raw)<=32 or raw.hex()!=profile[key]:
            raise RuntimeError('Invalid canonical WLAN SSID bytes')
    if profile['ssid_hex']==profile['expected_ap_ssid_hex']:
        raise RuntimeError('Return profile cannot be the tested controller AP')
    return path,profile


def flash_receipt(folder,scope):
    folder=Path(folder).resolve()
    root=(ROOT/'local-data/hardware/rate240-f').resolve()
    if folder==root or not folder.is_relative_to(root) or not folder.is_dir():
        raise RuntimeError('Expected prior flash folder under local-data/hardware/rate240-f')
    result_path=(folder/'result.json').resolve()
    if not result_path.is_relative_to(folder):
        raise RuntimeError('Prior flash receipt must remain inside its evidence folder')
    result=json.loads(result_path.read_text(encoding='utf-8'))
    if (not isinstance(result,dict) or result.get('flash_verified') is not True or
        result.get('source')!=scope['candidate'] or
        result.get('candidate_sha256')!=scope['candidate_sha256'] or
        not re.fullmatch('[0-9a-f]{40}',str(result.get('operation_commit','')))):
        raise RuntimeError('Prior flash candidate/operation identity mismatch')
    backup=result.get('backup')
    if not isinstance(backup,dict) or backup.get('bytes')!=scope['backup_end']:
        raise RuntimeError('Prior flash backup extent mismatch')
    prefix=(folder/'controller_prefix.bin').resolve();nvs=(folder/'nvs-before.bin').resolve()
    if not prefix.is_relative_to(folder) or not nvs.is_relative_to(folder):
        raise RuntimeError('Prior flash backups must remain inside their evidence folder')
    if (prefix.stat().st_size!=scope['backup_end'] or nvs.stat().st_size!=0x5000 or
        sha(prefix)!=backup.get('sha256') or sha(nvs)!=backup.get('nvs_sha256')):
        raise RuntimeError('Prior flash immutable backup mismatch')
    receipt=dict(folder=str(folder),source=result['source'],candidate_sha256=result['candidate_sha256'],
                 operation_commit=result['operation_commit'],backup=dict(bytes=backup['bytes'],
                 sha256=backup['sha256'],nvs_sha256=backup['nvs_sha256']))
    return folder,receipt,result,[prefix,nvs]


def prepare(args):
    if args.offline_phase not in ('flash','test','cold1','cold2'):
        raise RuntimeError('Flash and test must be requested as separate phases')
    if args.offline_phase=='test' and args.folder is None:
        raise RuntimeError('Offline test requires --folder from a verified flash phase')
    if args.offline_phase=='flash' and args.folder is not None:
        raise RuntimeError('Offline flash creates a new folder; omit --folder')
    path,profile=profile_input(args.return_profile)
    _,_,_,files,scope=trial.inputs(args.manifest,args.old_manifest,args.host)
    if args.host!='192.168.4.1' or not 30<=args.wait_seconds<=1800:
        raise RuntimeError('Expected explicit controller AP and bounded connection wait')
    offline=dict(phase=args.offline_phase,host=args.host,wait_seconds=args.wait_seconds,
        return_profile_sha256=sha(path),manual_ap_connection=True,restore_original_pc_profile=True,
        device_network_change=False,restore_timeout_seconds=90)
    offline['reconnect_observed_ap_after_reset']=True
    offline['ap_stable_seconds_before_flash']=10
    if args.offline_phase=='test':
        _,receipt,_,backups=flash_receipt(args.folder,scope)
        offline['flash_receipt']=receipt
        # result.json changes during test/recovery; only immutable backups are hashed.
        files += backups
    files += [Path(__file__),Path(wlan_profile.__file__),path]
    return profile,list(dict.fromkeys(p.resolve() for p in files)),scope,offline


def approved(args):
    if args.approval_record is None:
        raise RuntimeError('Genuine trial and PC network-return approval required before I/O')
    profile,files,scope,offline=prepare(args)
    record=require_approval(ROOT,args.approval_record,'rate240-f-trial',trial.tf.MAC,files)
    if (record.get('scope')!=scope or record.get('offline_scope')!=offline or
        record.get('hardware_exclusive_confirmed') is not True or record.get('hardware_ready_confirmed') is not True):
        raise RuntimeError('Trial/offline scope or exclusive readiness mismatch')
    if args.offline_phase=='test' and offline['flash_receipt']['operation_commit']!=record['source_commit']:
        raise RuntimeError('Offline test operation commit differs from verified flash')
    if args.offline_phase in ('cold1','cold2'):
        confirmation=record.get('cold_start_confirmation',{})
        if (not isinstance(confirmation,dict) or type(confirmation.get('number')) is not int or
            confirmation.get('number')!=int(args.offline_phase[-1]) or
            not isinstance(confirmation.get('user_confirmation'),str) or not confirmation['user_confirmation'].strip()):
            raise RuntimeError('Numbered actual cold-start confirmation required before offline I/O')
    return profile,record


def same_interface(current,original):
    return current['interface_guid'].lower()==original['interface_guid'].lower()


def reconnecting_http(base,ap_profile):
    class ReconnectingHttp(base):
        def request(self,path,data=None,key=None):
            # Retry only an idempotent status read; never replay session/start writes.
            if path!='/api/status' or data is not None:
                return super().request(path,data,key)
            deadline=time.monotonic()+60;next_connect=0
            while True:
                try:return super().request(path,data,key)
                except (urllib.error.URLError,TimeoutError,ConnectionError):
                    self.stability_generation=getattr(self,'stability_generation',0)+1
                    if time.monotonic()>=deadline:raise
                    if time.monotonic()>=next_connect:
                        try:
                            current=wlan_profile.current_connection()
                            associated=(same_interface(current,ap_profile) and
                                        current['ssid_hex']==ap_profile['ssid_hex'])
                        except Exception:associated=False
                        if not associated:wlan_profile.connect_profile(ap_profile)
                        next_connect=time.monotonic()+10
                    time.sleep(2)
    return ReconnectingHttp


def restore_pc(profile,state,write,clock=time.monotonic,sleep=time.sleep):
    deadline=clock()+90;next_attempt=0
    state['phase']='returning_original_wifi';write()
    while clock()<deadline:
        try:
            current=wlan_profile.current_connection()
            if (same_interface(current,profile) and current['profile_name']==profile['profile_name'] and
                current['ssid_hex']==profile['ssid_hex']):
                state['pc_profile_restored']=True;write();return
        except Exception:
            pass
        if clock()>=next_attempt:
            try:wlan_profile.connect_profile(profile)
            except Exception as error:
                state.setdefault('network_return_attempt_errors',[]).append(repr(error));write()
            next_attempt=clock()+25
        sleep(2)
    raise RuntimeError('Original WiFi reconnection timed out; local evidence retained')


def recover_after_flash(args,folder):
    # Covers failures before test.run enters its own recovery block. Never bypass approval.
    approved(args)
    _,old,identity,approval,_=trial.approved(args)
    result=json.loads((folder/'result.json').read_text(encoding='utf-8'))
    if result.get('restored_f'):return
    if (not result.get('flash_verified') or result.get('initial_stage_passed') or
        result.get('candidate_sha256')!=approval['scope']['candidate_sha256'] or
        result.get('operation_commit')!=approval['source_commit'] or result.get('source')!=identity):
        raise RuntimeError('Recovery requires matching incomplete flashed trial')
    session=object.__new__(trial.prior.TrialSession);session.folder=folder
    session.log=(folder/'hardware.log').open('a',encoding='utf-8')
    try:
        trial.stop_and_wait_closed(session,trial.Http(args.host),identity,result)
        trial.restore(session,result,old)
    finally:
        trial.save(folder/'result.json',result);session.log.close()


def run(args):
    profile,record=approved(args)
    test_folder=None
    if args.offline_phase=='test':
        test_folder,receipt,result,_=flash_receipt(args.folder,record['scope'])
        if (receipt!=record['offline_scope'].get('flash_receipt') or
            receipt['operation_commit']!=record['source_commit'] or
            result.get('restored_f') or result.get('test_started') or result.get('stages')!=[] or
            result.get('initial_stage_passed')):
            raise RuntimeError('Offline test receipt changed or batch already attempted')
    stamp=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y%m%d_%H%M%S')
    folder=ROOT/'local-data/hardware/rate240-offline'/stamp;folder.mkdir(parents=True)
    state=dict(pid=os.getpid(),phase='waiting_for_manual_ap',candidate_sha256=record['scope']['candidate_sha256'],
               operation_commit=record['source_commit'],pc_profile_restored=False,
               trial_folder=str(test_folder) if test_folder else None,passed=False)
    def write():trial.save(folder/'state.json',state)
    lock=ROOT/'local-data/hardware/rate240-offline/active.lock'
    # A prior owner is never silently displaced. The operator can inspect its PID/state.
    with lock.open('x',encoding='utf-8') as f:json.dump(dict(pid=os.getpid(),folder=str(folder)),f)
    write()
    print('OFFLINE_READY='+str(folder/'state.json'),flush=True)
    try:
        deadline=time.monotonic()+args.wait_seconds
        stable_since=None;stable_boot=None
        while time.monotonic()<deadline:
            try:
                current=wlan_profile.current_connection()
                if same_interface(current,profile) and current['ssid_hex']==profile['expected_ap_ssid_hex']:
                    status=trial.Http(args.host).request('/api/status');trial.quiet(status)
                    if stable_since is None or stable_boot!=status['boot']:
                        stable_since=time.monotonic();stable_boot=status['boot']
                        state['phase']='waiting_for_ap_stable_10s';write()
                    if time.monotonic()-stable_since>=10:
                        # Snapshot the profile actually associated by the user, without credentials.
                        wlan_profile.validate_snapshot(current)
                        trial.Http=reconnecting_http(trial.Http,current)
                        break
                else:stable_since=None;stable_boot=None
            except Exception:
                stable_since=None;stable_boot=None
            time.sleep(2)
        else:raise RuntimeError('Manual AP connection/readiness wait expired; requested phase not started')
        if args.offline_phase=='flash':
            state['phase']='flash';write();args.phase='flash';args.folder=None
            trial_folder=trial.run(args)
            state['trial_folder']=str(trial_folder);state['next_phase']='test';write()
        elif args.offline_phase=='test':
            state['phase']='test';write();args.phase='test';args.folder=test_folder
            trial.run(args)
        else:
            if args.folder is None:raise RuntimeError('Cold check requires prior trial folder')
            state['trial_folder']=str(args.folder);state['phase']=args.offline_phase;write()
            args.phase=args.offline_phase;trial.run(args)
        result=json.loads(Path(state['trial_folder'],'result.json').read_text(encoding='utf-8'))
        state['flash_verified']=result.get('flash_verified',False)
        state['phase_completed']=True
        state['initial_stage_passed']=result.get('initial_stage_passed',False)
        state['passed']=result.get('passed',False)
        state['cold_starts_completed']=result.get('cold_starts_completed',0)
    except Exception as error:
        state['error']=repr(error);write()
        if args.offline_phase in ('flash','test') and state['trial_folder']:
            try:recover_after_flash(args,Path(state['trial_folder']))
            except Exception as recovery:
                state['manual_recovery_required']=True;state['recovery_error']=repr(recovery);write()
    finally:
        try:restore_pc(profile,state,write)
        except Exception as error:state['network_return_error']=repr(error)
        state['phase']='finished';write()
        # Only this process removes its own lock, including error and timeout paths.
        try:
            owner=json.loads(lock.read_text(encoding='utf-8'))
            if owner.get('pid')==os.getpid():lock.unlink()
        except OSError:pass
        print('OFFLINE_FINISHED='+str(folder/'state.json'),flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,required=True);p.add_argument('--old-manifest',type=Path,required=True)
    p.add_argument('--approval-record',type=Path);p.add_argument('--host',default='192.168.4.1')
    p.add_argument('--return-profile',type=Path,required=True);p.add_argument('--wait-seconds',type=int,default=600)
    p.add_argument('--offline-phase',choices=('flash','test','cold1','cold2'),default='flash');p.add_argument('--folder',type=Path)
    p.add_argument('--execute',action='store_true');args=p.parse_args()
    if args.execute:run(args)
    else:
        _,files,scope,offline=prepare(args)
        print(json.dumps(dict(hardware_accessed=False,scope=scope,offline_scope=offline,
            files={p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else p.as_posix():sha(p) for p in files}),indent=2))


if __name__=='__main__':main()
