#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_testbin.py —— 生成一份模拟采集数据

用途：**没有硬件也能先把分析流程跑通**。
  1. 到货前先验证 bin2csv.py / analyze.py 在这台机器上能不能跑（环境是否装好）
  2. 先熟悉数据格式与出图效果，等真数据下来直接上手

合成内容（2 秒 @ 26667 Hz，对应一块静置竖放、同时被 1 kHz 激励的传感器）：
  Z 轴 = 1 g 直流 + 1000 Hz 正弦(0.5 g) + 0.01 g 噪声
  X/Y  = 纯 0.01 g 噪声

预期结果：Z 轴均值 ≈ 1.0000 g，标准差 ≈ 0.3536 g，主峰 = 1000.0 Hz。

用法：
    python make_testbin.py
    python make_testbin.py -o my.bin --seconds 5 --freq 2000
"""

import argparse
import math
import os
import random
import struct
import sys

SENS_MG = 0.061          # mg/LSB（±2g）


def build(path, fs, seconds, freq, amp_g, noise_g, seed=42):
    random.seed(seed)
    n = int(fs * seconds)
    header = struct.pack('<4sHIIH16s', b'VIBB', 1, fs, 123456, 6, b'\0' * 16)

    body = bytearray()
    for i in range(n):
        t = i / fs
        ax = random.gauss(0.0, noise_g)
        ay = random.gauss(0.0, noise_g)
        az = 1.0 + amp_g * math.sin(2 * math.pi * freq * t) + random.gauss(0.0, noise_g)
        for v in (ax, ay, az):
            raw = int(round(v * 1000.0 / SENS_MG))
            raw = max(-32768, min(32767, raw))
            body += struct.pack('<h', raw)

    with open(path, 'wb') as f:
        f.write(header + bytes(body))
    return n


def main():
    ap = argparse.ArgumentParser(description='生成模拟采集数据（.bin）')
    ap.add_argument('-o', '--out', default=None, help='输出路径（默认 ../samples/vib_test.bin）')
    ap.add_argument('--fs', type=int, default=26667, help='采样率 Hz')
    ap.add_argument('--seconds', type=float, default=2.0)
    ap.add_argument('--freq', type=float, default=1000.0, help='激励频率 Hz')
    ap.add_argument('--amp', type=float, default=0.5, help='激励幅值 g')
    ap.add_argument('--noise', type=float, default=0.01, help='噪底 g')
    args = ap.parse_args()

    out = args.out or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   '..', 'samples', 'vib_test.bin')
    out = os.path.abspath(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)

    n = build(out, args.fs, args.seconds, args.freq, args.amp, args.noise)
    print('已生成 %s' % out)
    print('  %d 样本 = %.2f s @ %d Hz，体积 %.2f MB'
          % (n, n / args.fs, args.fs, os.path.getsize(out) / 1048576))
    print('  预期：Z 均值≈1.0000 g，标准差≈%.4f g，主峰=%.1f Hz'
          % (args.amp / math.sqrt(2), args.freq))
    return 0


if __name__ == '__main__':
    sys.exit(main())
