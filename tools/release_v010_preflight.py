"""Read-only existing-bridge/UART identity, card and reference checks. No esptool/reset/write.

UART uses the existing audited no-DTR/no-RTS Session.serial path. Active acquisition is
rejected before opening UART. Network state is saved privately for later restoration.
"""
import argparse
import datetime
import json
import sys
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener
from project_check import ROOT
from ui_live_trial import TrialSession
import sensor_recorder_batch as recorder

EXPECTED_SOURCE='6ec57c38b1c73159ac987a34541ae0e5da681eab'


def run(url):
    parsed=urlsplit(url)
    if parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or not parsed.port or parsed.path!='/':
        raise ValueError('Use the existing controller loopback bridge URL')
    folder=ROOT/'local-data/hardware/release-v010'/('preflight-'+datetime.datetime.now().strftime('%Y%m%d_%H%M%S'))
    session=TrialSession(folder)
    opener=build_opener(ProxyHandler({}))
    result={'passed':False,'firmware_written':False,'network_changed':False,'reset_requested':False,'uart_opened':False}
    try:
        with opener.open(url+'api/status',timeout=5) as response:before=json.load(response)
        result['before']=before
        if before['phase'] not in ('idle','passed') or before['errors'] or not before['ready']:
            raise RuntimeError('Active or unsuccessful device; do not interrupt')
        with opener.open(url+'api/v2/network/state',timeout=5) as response:network=json.load(response)
        (folder/'network-before-private.json').write_text(json.dumps(network,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        with session.serial() as port:
            result['uart_opened']=True
            recorder.boot_check(session,port,EXPECTED_SOURCE)
        with opener.open(url+'api/status',timeout=5) as response:after=json.load(response)
        if before['boot']!=after['boot'] or before['run']!=after['run'] or after['phase'] not in ('idle','passed'):
            raise RuntimeError('Device state changed during read-only preflight')
        result.update(passed=True,source=EXPECTED_SOURCE,after=after,boot_unchanged=True)
    except Exception as error:
        result['error']=repr(error)
        raise
    finally:
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        session.emit('PREFLIGHT_RESULT='+str(folder/'result.json'));session.log.close()


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    sys.stderr.reconfigure(encoding='utf-8',errors='replace')
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--bridge-url',required=True)
    run(parser.parse_args().bridge_url)
