"""Read-only validator/exporter for I3REC001. Never opens serial devices or repairs a card."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import struct
import zlib

HEADER = struct.Struct('<8sIIIQIIIII41s65s18s32s5I')
BLOCK = struct.Struct('<IIIIIQQQI')
INA = struct.Struct('<Q8H')
FOOTER = struct.Struct('<QQQ11I')
FOOTER_FIELDS = ('samples start_us stop_us ina_samples ina_invalid errors saturation fifo_high '
                 'queue_high max_write_us max_sync_us min_heap sensor_stack ina_stack').split()
MAX_PAYLOAD = 4096
SCALE_RANGES = {61: 2, 244: 8, 488: 16}


class RecordError(ValueError):
    pass


def text(value):
    return value.split(b'\0', 1)[0].decode('ascii', errors='strict')


def validate(path, xyz_csv=None, ina_csv=None, preview_limit=2000):
    """Stream complete files. Export time is nominal sample-index time, not an ADC timestamp."""
    digest = hashlib.sha256()
    samples = ina_count = invalid = sequence = saturation = 0
    preview = []
    footer = None
    xyz_writer = csv.writer(xyz_csv) if xyz_csv else None
    ina_writer = csv.writer(ina_csv) if ina_csv else None
    if xyz_writer:
        xyz_writer.writerow(('sample', 'nominal_seconds', 'x_raw', 'y_raw', 'z_raw', 'x_mg', 'y_mg', 'z_mg'))
    if ina_writer:
        ina_writer.writerow(('mcu_us', 'valid', 'config', 'cal', 'mask', 'shunt_raw', 'bus_raw',
                             'current_raw', 'power_raw', 'bus_V', 'current_mA', 'power_mW'))
    with Path(path).open('rb') as stream:
        raw = stream.read(HEADER.size)
        if len(raw) != HEADER.size:
            raise RecordError('truncated header')
        h = HEADER.unpack(raw)
        if h[0] != b'I3REC001' or h[1] != HEADER.size or h[2] != 1 or zlib.crc32(raw[:-4]) != h[-1]:
            raise RecordError('invalid header/version/CRC')
        if ((h[6], h[7], h[9]) != (26667, 8000000, 1) or h[8] not in SCALE_RANGES or
                h[14:17] != (100000, 25000, 625000) or not 0x40 <= h[17] <= 0x4f):
            raise RecordError('unsupported rate/SPI/scale/precision flags')
        meta = dict(card_serial=f'{h[3]:08X}', card_bytes=h[4], requested_seconds=h[5],
                    odr=h[6], spi_hz=h[7], scale_micro_g=h[8], range_g=SCALE_RANGES[h[8]],
                    ina_precision_accepted=False,
                    source=text(h[10]), inputs=text(h[11]), mac=text(h[12]), wiring=text(h[13]),
                    shunt_micro_ohm=h[14], current_lsb_nA=h[15], power_lsb_nW=h[16], ina_address=h[17])
        if not 1 <= h[5] <= 300 or len(meta['source']) != 40 or len(meta['inputs']) != 64:
            raise RecordError('invalid metadata')
        scale_mg = meta['scale_micro_g'] / 1000
        digest.update(raw)
        while footer is None:
            raw = stream.read(BLOCK.size)
            if len(raw) != BLOCK.size:
                raise RecordError(f'incomplete: no complete footer after {samples} samples/{sequence} blocks')
            magic, kind, seq, size, count, first, last, first_sample, crc = BLOCK.unpack(raw)
            if magic != 0x314B4C42 or seq != sequence or size > MAX_PAYLOAD:
                raise RecordError('invalid block magic/sequence/size')
            if first > last or first_sample != samples:
                raise RecordError('invalid block time/sample continuity')
            data = stream.read(size)
            if len(data) != size or zlib.crc32(raw[:-4] + data) != crc:
                raise RecordError('truncated/corrupt block')
            digest.update(raw); digest.update(data)
            sequence += 1
            if kind == 1:
                if not 1 <= count <= 448 or size != count * 7:
                    raise RecordError('invalid frame count')
                for index, (tag, x, y, z) in enumerate(struct.iter_unpack('<Bhhh', data)):
                    if tag >> 3 != 2:
                        raise RecordError('unexpected FIFO tag')
                    saturation += sum(value <= -32760 or value >= 32760 for value in (x, y, z))
                    row = (samples + index, (samples + index) / meta['odr'], x, y, z,
                           x * scale_mg, y * scale_mg, z * scale_mg)
                    if xyz_writer:
                        xyz_writer.writerow(row)
                    # Bounded preview of first samples, not an antialias-filtered downsample.
                    if len(preview) < preview_limit:
                        preview.append(row)
                samples += count
            elif kind == 2:
                if count != 1 or size != INA.size:
                    raise RecordError('invalid INA block')
                stamp, config, cal, mask, shunt, bus, current, power, valid = INA.unpack(data)
                if valid not in (0, 1) or first != stamp or last != stamp:
                    raise RecordError('invalid INA status/time')
                if valid and (config != 0x4527 or cal != 0x0800 or not mask & 8 or mask & 4):
                    raise RecordError('INA marked valid with invalid configuration')
                ina_count += 1; invalid += not valid
                signed_current = current - 65536 if current & 0x8000 else current
                if ina_writer:
                    ina_writer.writerow((stamp, valid, config, cal, mask, shunt, bus, current, power,
                        bus * .00125 if valid else '', signed_current * .025 if valid else '',
                        power * .625 if valid else ''))
            elif kind == 3:
                if count != 1 or size != FOOTER.size:
                    raise RecordError('invalid footer layout')
                footer = dict(zip(FOOTER_FIELDS, FOOTER.unpack(data)))
                if (footer['samples'] != samples or footer['ina_samples'] != ina_count or
                    footer['ina_invalid'] != invalid or footer['saturation'] != saturation or
                    not samples or ina_count <= invalid or footer['stop_us'] <= footer['start_us'] or
                    first != footer['start_us'] or last != footer['stop_us']):
                    raise RecordError('footer totals/time mismatch')
                if footer['errors']:
                    raise RecordError(f"recording failed, firmware error mask={footer['errors']}")
                rate = samples * 1e6 / (footer['stop_us'] - footer['start_us'])
                if not 25333 <= rate <= 28001:
                    raise RecordError('observed functional rate outside predetermined +/-5% band')
                footer['observed_samples_per_second'] = rate
            else:
                raise RecordError('unknown block type')
        if stream.read(1):
            raise RecordError('trailing bytes after footer')
    return dict(complete=True, structural_integrity_passed=True, hardware_accuracy_accepted=False,
                metadata=meta, blocks=sequence, footer=footer, sha256=digest.hexdigest(), preview=preview,
                time_note='XYZ time is sample index / nominal ODR; block MCU times are read windows')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path)
    parser.add_argument('--output', type=Path, help='new output directory for JSON, CSV and optional PNG preview')
    parser.add_argument('--csv', action='store_true', help='export all XYZ and INA rows; can be much larger than binary')
    parser.add_argument('--plot', action='store_true', help='plot first 2000 samples, requires matplotlib')
    args = parser.parse_args()
    # Validate first so rejected input never produces CSV looking like an accepted record.
    try:
        result = validate(args.file)
    except (RecordError, UnicodeError, OSError) as exc:
        print(json.dumps({'complete': False, 'accepted': False, 'error': str(exc)}, ensure_ascii=False))
        raise SystemExit(2)
    preview = result.pop('preview')
    if args.output:
        args.output.mkdir(parents=True, exist_ok=False)
        if args.csv:
            with (args.output/'xyz.csv').open('x', newline='', encoding='utf-8') as xyz, \
                 (args.output/'ina.csv').open('x', newline='', encoding='utf-8') as ina:
                again = validate(args.file, xyz, ina, preview_limit=0)
                if again['sha256'] != result['sha256']:
                    raise SystemExit('Input changed during export; output is not accepted')
        if args.plot:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            figure, axis = plt.subplots(figsize=(12, 4))
            for column, label in ((5, 'X'), (6, 'Y'), (7, 'Z')):
                axis.plot([row[1] for row in preview], [row[column] for row in preview], label=label, lw=.8)
            axis.set(xlabel='Nominal time (s)', ylabel='Acceleration (mg)',
                     title='First 2000 samples; nominal time, not a calibrated measurement')
            axis.legend(); figure.tight_layout(); figure.savefig(args.output/'preview.png', dpi=150)
            plt.close(figure)
        (args.output/'summary.json').write_text(json.dumps(result, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    elif args.csv or args.plot:
        parser.error('--csv/--plot require --output')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
