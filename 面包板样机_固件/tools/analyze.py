#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze.py —— 采集数据分析：时域波形 + 频谱 + 指标

依赖：numpy、matplotlib（pip install numpy matplotlib）
若本机装不上，先用 bin2csv.py（纯标准库）转出 CSV，再拿 Origin / Excel 画。

用法：
    python analyze.py vib_00000.bin
    python analyze.py vib_seg*.bin --save out.png --no-show
    python analyze.py vib_00000.bin --fmax 3000     # 只看 0~3kHz
"""

import argparse
import glob
import os
import struct
import sys

import numpy as np

HEADER_FMT = '<4sHIIH16s'
HEADER_SIZE = struct.calcsize(HEADER_FMT)
MAGIC = b'VIBB'


def load_bin(path):
    with open(path, 'rb') as f:
        raw = f.read()
    magic, ver, fs, t0, bps, _ = struct.unpack_from(HEADER_FMT, raw, 0)
    if magic != MAGIC:
        raise ValueError(f'{path}: magic 不符，不是本固件的文件')
    if bps != 6:
        raise ValueError(f'{path}: 仅支持 6 字节/样本')

    body = raw[HEADER_SIZE:]
    body = body[: len(body) - (len(body) % bps)]
    arr = np.frombuffer(body, dtype='<i2').reshape(-1, 3).astype(np.float64)
    return fs, arr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('inputs', nargs='+')
    ap.add_argument('--sens', type=float, default=0.061, help='mg/LSB')
    ap.add_argument('--calib', action='store_true',
                    help='把数据当作「静置标定」数据处理，校验 1 g 标度是否准确')
    ap.add_argument('--fmax', type=float, default=None, help='频谱上限 Hz')
    ap.add_argument('--save', default=None, help='图片输出路径')
    ap.add_argument('--no-show', action='store_true')
    args = ap.parse_args()

    files = []
    for pat in args.inputs:
        files.extend(sorted(glob.glob(pat)) or [pat])
    files = [f for f in files if os.path.isfile(f)]
    if not files:
        print('没有找到输入文件', file=sys.stderr)
        return 1

    segs = []
    print(f'{"文件":<26}{"采样率":>10}{"样本":>12}{"时长(s)":>10}  实测均方根(g)')
    for p in files:
        try:
            fs, a = load_bin(p)
        except Exception as e:
            print(f'{os.path.basename(p):<26}  跳过：{e}', file=sys.stderr)
            continue
        g = a * args.sens / 1000.0
        dur = len(g) / fs
        rms = float(np.sqrt(np.mean(g ** 2)))
        print(f'{os.path.basename(p):<26}{fs:>10}{len(g):>12}{dur:>10.3f}  {rms:>8.4f}')
        segs.append((p, fs, g))

    if not segs:
        return 1

    # 拼接（同采样率才拼）
    fs = segs[0][1]
    same = all(s[1] == fs for s in segs)
    g = np.vstack([s[2] for s in segs]) if same else segs[0][2]
    if not same:
        print('⚠ 采样率不一致，只分析第一个文件', file=sys.stderr)

    n = len(g)
    t = np.arange(n) / fs
    print(f'\n合计 {n} 样本，{n / fs:.3f} s')
    print(f'各轴均值(g)  X={g[:,0].mean():+.4f}  Y={g[:,1].mean():+.4f}  Z={g[:,2].mean():+.4f}')
    print(f'各轴标准差   X={g[:,0].std():.4f}  Y={g[:,1].std():.4f}  Z={g[:,2].std():.4f}')

    # ---- 静置标定检查 ----
    # 原理：传感器静止时三轴合模量必然 = 1 g（它测 DC 加速度），
    #       所以 |a| 与 1.0000 g 的偏差就＝标度误差。不需要任何激振设备。
    # 判据出处：DS12569 Rev8 Table 3 —— ±2g 灵敏度 0.061 mg/LSB，公差 ±2%。
    if args.calib:
        mean = g.mean(axis=0)
        mag = float(np.sqrt((mean ** 2).sum()))
        err = (mag - 1.0) * 100.0
        print('\n' + '=' * 62)
        print('静置标定检查')
        print(f'  平均加速度   X={mean[0]:+.4f}  Y={mean[1]:+.4f}  Z={mean[2]:+.4f}  (g)')
        print(f'  合模量 |a|   {mag:.4f} g    （静置应 = 1.0000 g）')
        print(f'  标度偏差     {err:+.2f} %')
        if abs(err) <= 2.0:
            print('  判定：✅ 合格（在 DS12569 Table 3 的 ±2% 公差内）')
        elif abs(err) <= 5.0:
            print('  判定：⚠ 超出 ±2% 规格，做定性观察仍够用')
            print(f'        → 若要精确，SENS_MG_PER_LSB 由 {args.sens:.4f} 改为 {args.sens / mag:.4f}')
        else:
            print('  判定：❌ 偏差过大，先排查而不是急着改系数：')
            print('        ① 数据采集时传感器是否真的静止（手持/桌面震动会拉高 |a|）')
            print('        ② 传感器附近有无导磁金属或磁铁')
            print(f'        若以上排除，SENS_MG_PER_LSB 改为 {args.sens / mag:.4f}')
        print(f'  零偏（本姿态分量） X={mean[0]:+.4f}  Y={mean[1]:+.4f}  Z={mean[2]:+.4f} g')
        print('  注：要分离真正的 0 g 零偏，需两点法——同一轴朝上/朝下各采一次，')
        print('      offset=(a1+a2)/2；本检查只校验标度。')
        print('=' * 62)

    # ---- 频谱（Welch 法，取最长 2^18 段） ----
    try:
        from scipy.signal import welch
        def psd(x):
            f, p = welch(x, fs=fs, nperseg=min(len(x), 1 << 16))
            return f, p
    except ImportError:
        def psd(x):
            m = 1 << int(np.floor(np.log2(min(len(x), 1 << 18))))
            x = x[:m] * np.hanning(m)
            X = np.fft.rfft(x)
            f = np.fft.rfftfreq(m, 1 / fs)
            return f, (np.abs(X) ** 2) / (m * fs)

    fig, ax = plt.subplots(2, 1, figsize=(12, 7))
    k = min(n, int(fs * 0.05))          # 时域只画前 50 ms，否则线太密看不清
    for i, lbl in enumerate('XYZ'):
        ax[0].plot(t[:k], g[:k, i], lw=0.6, label=lbl)
    ax[0].set_xlabel('time (s)')
    ax[0].set_ylabel('accel (g)')
    ax[0].set_title('时域（截取前段放大）')
    ax[0].legend(loc='upper right')
    ax[0].grid(alpha=0.3)

    print()
    for i, lbl in enumerate('XYZ'):
        f, p = psd(g[:, i])
        ax[1].semilogy(f, p, lw=0.7, label=lbl)
        m = f > 1.0                       # 忽略直流附近，免得把 0 Hz 当成主峰
        if m.any():
            fp = f[m][np.argmax(p[m])]
            print(f'{lbl} 轴主峰: {fp:9.1f} Hz')
    ax[1].set_xlabel('frequency (Hz)')
    ax[1].set_ylabel('PSD')
    ax[1].set_title(f'频谱（Nyquist = {fs / 2:.0f} Hz）')
    if args.fmax:
        ax[1].set_xlim(0, args.fmax)
    ax[1].legend(loc='upper right')
    ax[1].grid(alpha=0.3)

    plt.tight_layout()
    out = args.save or (os.path.splitext(segs[0][0])[0] + '_analysis.png')
    plt.savefig(out, dpi=140)
    print(f'图已保存 → {out}')
    if not args.no_show:
        plt.show()
    return 0


try:
    import matplotlib
    import matplotlib.pyplot as plt
except ImportError:
    print('需要 matplotlib：pip install matplotlib', file=sys.stderr)
    sys.exit(2)

# 中文字体（用 Windows 自带雅黑/黑体），否则图里的中文标题会变成方块
plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

if __name__ == '__main__':
    sys.exit(main())
