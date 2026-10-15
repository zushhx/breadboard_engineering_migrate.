#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bin2csv.py —— 把固件写出的 .bin 转成 CSV

★ 纯标准库实现，不依赖 numpy / pandas，任何 Python 3.8+ 都能跑。

用法：
    python bin2csv.py vib_00000.bin
    python bin2csv.py vib_00000.bin -o out.csv
    python bin2csv.py *.bin            # 批量（合并成一个 CSV，带文件列）
"""

import argparse
import csv
import glob
import os
import struct
import sys

# 与固件 recorder.h 的 RecHeader 严格对应，小端
HEADER_FMT = '<4sHIIH16s'
HEADER_SIZE = struct.calcsize(HEADER_FMT)   # = 32
MAGIC = b'VIBB'


def read_header(raw: bytes) -> dict:
    if len(raw) < HEADER_SIZE:
        raise ValueError('文件太短，连文件头都不完整')
    magic, ver, fs, t0, bps, _res = struct.unpack_from(HEADER_FMT, raw, 0)
    if magic != MAGIC:
        raise ValueError(f'magic 不是 {MAGIC!r}，这不是本固件写出的数据文件')
    return {
        'version': ver,
        'sample_rate_hz': fs,
        'start_millis': t0,
        'bytes_per_sample': bps,
    }


def iter_samples(raw: bytes, bps: int):
    """逐样本产出 (x, y, z) 原始 int16"""
    body = raw[HEADER_SIZE:]
    body = body[: len(body) - (len(body) % bps)]      # 丢掉尾部残缺字节
    n = len(body) // bps
    if bps == 6:
        for i in range(n):
            x, y, z = struct.unpack_from('<hhh', body, i * 6)
            yield x, y, z
    else:
        raise ValueError(f'暂不支持 {bps} 字节/样本的格式')


def convert_one(path: str, out_rows: list, with_file_col: bool, sens_mg_per_lsb: float):
    with open(path, 'rb') as f:
        raw = f.read()
    hdr = read_header(raw)
    sr = hdr['sample_rate_hz']
    total = 0
    for idx, (x, y, z) in enumerate(iter_samples(raw, hdr['bytes_per_sample'])):
        # 加速度换算（±2g 时 0.061 mg/LSB）
        ax = x * sens_mg_per_lsb / 1000.0
        ay = y * sens_mg_per_lsb / 1000.0
        az = z * sens_mg_per_lsb / 1000.0
        row = [round(idx / sr, 6), x, y, z,
               round(ax, 6), round(ay, 6), round(az, 6)]
        if with_file_col:
            row.insert(0, os.path.basename(path))
        out_rows.append(row)
        total += 1
    return hdr, total


def main():
    ap = argparse.ArgumentParser(description='把固件 .bin 转成 CSV')
    ap.add_argument('inputs', nargs='+', help='bin 文件（支持通配符）')
    ap.add_argument('-o', '--out', default=None, help='输出 CSV 路径')
    ap.add_argument('--sens', type=float, default=0.061,
                    help='灵敏度 mg/LSB（±2g 默认 0.061；以 DS12569 为准）')
    args = ap.parse_args()

    files = []
    for pat in args.inputs:
        files.extend(sorted(glob.glob(pat)) or [pat])
    files = [f for f in files if os.path.isfile(f)]
    if not files:
        print('没有找到输入文件', file=sys.stderr)
        return 1

    multi = len(files) > 1
    rows = []
    print(f'{"文件":<26}{"采样率":>10}{"样本数":>12}{"时长(s)":>10}')
    total_all = 0
    for p in files:
        try:
            hdr, n = convert_one(p, rows, multi, args.sens)
        except Exception as e:
            print(f'{os.path.basename(p):<26}  跳过：{e}', file=sys.stderr)
            continue
        total_all += n
        dur = n / hdr['sample_rate_hz'] if hdr['sample_rate_hz'] else 0
        print(f'{os.path.basename(p):<26}{hdr["sample_rate_hz"]:>10}{n:>12}{dur:>10.3f}')

    if not rows:
        print('没有转换出任何样本', file=sys.stderr)
        return 1

    out = args.out or (os.path.splitext(files[0])[0] + '.csv')
    header = (['file'] if multi else []) + ['t_s', 'x_lsb', 'y_lsb', 'z_lsb',
                                            'ax_g', 'ay_g', 'az_g']
    with open(out, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)

    print(f'\n共 {total_all} 样本 → {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
