"""Explicit WiFi panic inspection; preserve application snapshot and restore in finally."""
import argparse,datetime,json,subprocess,time
from pathlib import Path
from project_check import ROOT,BUNDLE,sha
from prepare_sensor_web_trial import prepare
from operation_gate import require_approval
import tf_write_batch as tf
import sensor_recorder_batch as base
import sensor_web_batch as web
GDB=Path.home()/'.platformio/packages/tool-xtensa-esp-elf-gdb/bin/xtensa-esp32s3-elf-gdb.exe'

def inputs(manifest):
    packet=prepare(manifest)
    candidate=ROOT/packet['candidate']['path']
    elf=candidate.with_name('firmware.elf')
    files=[Path(__file__),manifest,candidate,elf,GDB,tf.ESPTOOL,
        ROOT/'tools/operation_gate.py',ROOT/'tools/prepare_sensor_web_trial.py',
        ROOT/'tools/build_sensor_web_recorder.py',ROOT/'tools/tf_write_batch.py',
        ROOT/'tools/sensor_recorder_batch.py',ROOT/'tools/sensor_web_batch.py',
        ROOT/packet['recovery']['path'],BUNDLE/'artifacts/bootloader.bin',BUNDLE/'artifacts/partitions.bin']
    return packet,candidate,elf,files

def run(manifest,approval):
    packet,candidate,elf,files=inputs(manifest)
    require_approval(ROOT,approval,'wifi-notify-debug',tf.MAC,files)
    folder=ROOT/'local-data/hardware/wifi-notify'/datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    session=tf.Session(folder); snapshot=folder/'controller_prefix.bin'; changed=False
    result={'candidate_sha256':sha(candidate),'source':packet['candidate_source'],'passed':False}
    try:
        with session.serial() as port:
            base.boot_check(session,port,'203a60b563708ec96d1e7a883faff059af4a9a7c')
        session.esptool(tf.unique_port(),['read_flash','0',hex(packet['required_prewrite_backup_bytes']),str(snapshot)])
        data=snapshot.read_bytes();old=(ROOT/packet['recovery']['path']).read_bytes()
        for offset,payload in [(0,(BUNDLE/'artifacts/bootloader.bin').read_bytes()),(0x8000,(BUNDLE/'artifacts/partitions.bin').read_bytes()),(0x10000,old)]:
            if data[offset:offset+len(payload)]!=payload:raise RuntimeError('Existing baseline mismatch; no write')
        changed=True
        session.esptool(tf.unique_port(),['write_flash','--flash_mode','keep','--flash_freq','keep','--flash_size','keep','0x10000',str(candidate)])
        text='';deadline=time.monotonic()+35
        with session.serial() as port:
            while time.monotonic()<deadline:
                chunk=port.read(4096).decode('utf-8',errors='replace');text+=chunk
                if chunk:session.emit(chunk.rstrip())
                if 'Entering gdb stub' in text:break
        if 'Entering gdb stub' not in text:raise RuntimeError('No GDB panic stop; preserve startup log')
        commands=['set pagination off','set print pretty on','set serial baud 115200','set remotetimeout 8',
            'target remote '+tf.unique_port(),'info threads','thread apply all bt',
            "p 'esp_timer.c'::s_timer_task","p *'esp_timer.c'::s_timer_task",
            "p 'tasks.c'::xPendingReadyList","p 'tasks.c'::uxSchedulerSuspended",
            "p 'tasks.c'::pxCurrentTCBs", "p *'tasks.c'::pxCurrentTCBs[0]",
            "x/24wx ('tasks.c'::pxCurrentTCBs[0])->pxStack",'info registers']
        argv=[str(GDB),'-nx','-batch','-q',str(elf)]
        for command in commands:argv+=['-ex',command]
        debug=subprocess.run(argv,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=80)
        (folder/'gdb.txt').write_text(debug.stdout+'\n'+debug.stderr,encoding='utf-8',newline='\n')
        result['gdb_exit']=debug.returncode;result['gdb_captured']=bool(debug.stdout)
    finally:
        if changed:
            web.restore(session,snapshot);result['restored_previous']=True
        (folder/'result.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8',newline='\n')
        session.log.close();print('EVIDENCE='+str(folder),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--manifest',type=Path,required=True);parser.add_argument('--approval',type=Path,required=True)
    args=parser.parse_args();run(args.manifest,args.approval)
