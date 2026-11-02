"""Explicit temporary media-mode comparison around existing test stages only."""
import argparse
import datetime
import functools
import json
from pathlib import Path

import rate240_offline_trial as offline
import wlan_media_mode as media
from operation_gate import require_approval

POLICY = dict(opcode=3, expected_ap=media.AP_SSID_HEX, scope='each-ram-or-mirror-stage',
              require_original_false=True, verify_readback=True,
              withdraw_own_request=True, permanent_adapter_changes=False)


def approved(args):
    if args.offline_phase != 'test':
        raise RuntimeError('Media comparison only wraps the separately verified test phase')
    profile, record = offline.approved(args)
    _, files, _, _ = offline.prepare(args)
    files += [Path(__file__), Path(media.__file__)]
    require_approval(offline.ROOT, args.approval_record, 'rate240-f-trial', offline.trial.tf.MAC, files)
    if record.get('media_mode_policy') != POLICY:
        raise RuntimeError('Explicit temporary media-mode comparison scope missing')
    return profile, record


def wrap_stage(function):
    @functools.wraps(function)
    def stage(session, *args, **kwargs):
        snapshot = offline.wlan_profile.current_connection()
        def emit(**row):
            entry = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                         stage=function.__name__, **row)
            with (session.folder/'media-mode.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(entry, ensure_ascii=False)+'\n')
        backend = media.NativeBackend(snapshot)
        with media.temporary_media_mode(snapshot, emit, backend=backend):
            result = function(session, *args, **kwargs)
            current = offline.wlan_profile.current_connection()
            valid = media.same_ap(snapshot, current) and backend.query()
            emit(event='media_end_check', valid=bool(valid))
            if not valid:
                raise RuntimeError('Media mode/AP changed during stage; inspect ETW')
            return result
    return stage


def run(args):
    approved(args)
    original_ram, original_stage = offline.trial.ram_stage, offline.trial.stage
    offline.trial.ram_stage = wrap_stage(original_ram)
    offline.trial.stage = wrap_stage(original_stage)
    try:
        return offline.run(args)
    finally:
        offline.trial.ram_stage, offline.trial.stage = original_ram, original_stage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--old-manifest', type=Path, required=True)
    parser.add_argument('--host', default='192.168.4.1')
    parser.add_argument('--return-profile', type=Path, required=True)
    parser.add_argument('--approval-record', type=Path, required=True)
    parser.add_argument('--wait-seconds', type=int, default=1800)
    parser.add_argument('--offline-phase', choices=['test'], default='test')
    parser.add_argument('--folder', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    approved(args)
    if args.execute:
        run(args)
    else:
        print(json.dumps(dict(hardware_accessed=False, media_mode_policy=POLICY)))


if __name__ == '__main__':
    main()
