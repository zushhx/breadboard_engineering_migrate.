/* eslint-disable */
/**
 * ui_selftest.js —— 浏览器 UI 的离线自测（不需要硬件、不需要浏览器）
 *
 * 做法：把 webui/index.html 里的内联脚本抠出来，在 Node 里配一套最小 DOM 桩跑起来，
 *      然后断言四件容易出错的事：
 *        ① 语法 / 运行时无误（渲染循环连跑 400 帧不抛异常）
 *        ② 环形缓冲索引不越界、无 NaN、min ≤ max
 *        ③ **WebSocket 二进制帧解码与设备端 src/netui.cpp 的序列化逐字节对得上**
 *        ④ 演示模式统计量的**量纲**正确（mg，不是 g —— 差 1000 倍这种错肉眼看不出来）
 *
 * 为什么值得做：这个 UI 的两处真 bug 都是它抓出来的 ——
 *    · 演示模式时间换算混了单位（拿抽帧比 26 当门槛），波形点数凭空少 26 倍
 *    · 演示模式把 g 直接写进 mg 字段，RMS 永远显示 0.0 mg
 *   这两个在浏览器里都「看起来能跑」，只有断言才拦得住。
 *
 * 用法：
 *   node tools/ui_selftest.js
 * 退出码 0 = 全过，非 0 = 有失败项。
 */

const fs = require('fs');
const path = require('path');

const HTML = path.join(__dirname, '..', 'webui', 'index.html');
const html = fs.readFileSync(HTML, 'utf8');
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.error('[x] 在 index.html 里没找到 <script> 块'); process.exit(1); }

// ---------------- 最小 DOM / 浏览器桩 ----------------
const rafQ = [];
const ctxStub = new Proxy({}, {
  get(t, k) {
    if (k in t) return t[k];
    if (k === 'measureText') return () => ({ width: 10 });
    return () => {};
  },
  set(t, k, v) { t[k] = v; return true; },
});

function mkEl(id) {
  return {
    id, className: '', textContent: '', innerHTML: '', value: '',
    style: {}, dataset: {}, children: [],
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    setAttribute() {}, getAttribute() { return null; }, addEventListener() {},
    closest() { return null; }, querySelector() { return null; }, appendChild() {},
    getContext() { return ctxStub; },
    clientWidth: 900, clientHeight: 330, width: 900, height: 330,
    onclick: null,
  };
}
const els = {};
global.document = {
  getElementById: id => (els[id] || (els[id] = mkEl(id))),
  querySelector: () => null,
  querySelectorAll: () => [els.main || (els.main=mkEl("main")),els.sidebar || (els.sidebar=mkEl("sidebar"))],
  addEventListener() {},
};
global.window = { devicePixelRatio: 1, addEventListener() {} };
global.location = { protocol: 'file:', host: '192.168.4.1' };
let T = 0;
global.performance = { now: () => T };
global.requestAnimationFrame = fn => { rafQ.push(fn); return rafQ.length; };
global.cancelAnimationFrame = () => {};
global.confirm = () => false;

let pass = 0, fail = 0;
function chk(name, cond, extra) {
  if (cond) { pass++; console.log('  OK   ' + name); }
  else { fail++; console.log('  FAIL ' + name + (extra !== undefined ? '  → ' + extra : '')); }
}

// ---------------- 载入并被测脚本 ----------------
const code = m[1] +
  '\n;globalThis.__T = { state, demoTick, demoSignal, onFrame, onStatus, draw, render, fmtMg, at, switchPage, parseHeader, runAnalysis, loadAnalysisFile, makeSyntheticRecord, analysis, fft, stftFeatures };';
try {
  (0, eval)(code);
} catch (e) {
  console.error('[x] 脚本执行抛异常：', e.message);
  console.error(e.stack.split('\n').slice(0, 5).join('\n'));
  process.exit(2);
}
const A = globalThis.__T;

console.log('\n=== 1. 启动状态 ===');
chk('检测到 file:// → 已进入演示模式', A.state.demo === true);
chk('抽帧后速率 = 1026 Hz', A.state.rateHz === 1026, A.state.rateHz);
chk('抽帧比 = 26', A.state.decim === 26, A.state.decim);
chk('环形缓冲容量 = 4096 点', A.state.cap === 4096, A.state.cap);

console.log('\n=== 2. 驱动 400 帧（约 6.7 秒模拟时间）===');
let errs = 0;
for (let i = 0; i < 400; i++) {
  const q = rafQ.splice(0);
  T += 16.7;
  for (const fn of q) { try { fn(T); } catch (e) { errs++; if (errs < 3) console.log('  渲染异常: ' + e.message); } }
}
chk('渲染循环无异常', errs === 0, errs + ' 次');
chk('缓冲已填满（4096 点）', A.state.len === 4096, A.state.len);
chk('已产生波形帧', A.state.frames > 100, A.state.frames + ' 帧');

console.log('\n=== 3. 缓冲内容健全性 ===');
let bad = 0, maxAbs = 0;
for (let a = 0; a < 3; a++) {
  for (let i = 0; i < 1000; i++) {
    const p = A.at(a, i);
    if (!isFinite(p[0]) || !isFinite(p[1])) bad++;
    if (p[0] > p[1]) bad++;                       // min 必须 <= max
    maxAbs = Math.max(maxAbs, Math.abs(p[0]), Math.abs(p[1]));
  }
}
chk('无 NaN / 无 min>max', bad === 0, bad + ' 处');
chk('幅度在合理范围 (±2g 内)', maxAbs < 2.0, maxAbs.toFixed(4) + ' g');
chk('非全零（确实有信号）', maxAbs > 0.001, maxAbs.toFixed(5) + ' g');

console.log('\n=== 4. 三轴统计量（★ 单位必须是 mg，与设备 status JSON 一致）===');
// 只校验量级：若单位写成 g，读数会是这里的 1/1000（<0.05 mg）→ 立刻暴露。
// 上界留松是因为合成信号每 3 秒有一次冲击，会把 0.5 s 窗口的幅度抬起来。
for (let a = 0; a < 3; a++) {
  const r = A.state.rms[a], p = A.state.pk[a];
  chk(`轴${'XYZ'[a]} RMS=${A.fmtMg(r)} 峰值=${A.fmtMg(p)}`,
      r > 4 && r < 40 && p > 50 && p < 1500,
      `rms=${r.toFixed(2)} mg  pk=${p.toFixed(1)} mg`);
}

console.log('\n=== 5. WebSocket 二进制帧解码（对照 src/netui.cpp 的序列化）===');
A.state.len = 0; A.state.head = 0;
const nb = 32, nA = 3;
const buf = new ArrayBuffer(14 + nb * nA * 4);
const u8 = new Uint8Array(buf), dv = new DataView(buf);
u8[0] = 0xAA; u8[1] = 0x01;
dv.setUint16(2, 7, true);         // seq
dv.setUint32(4, 12345, true);     // tMs
dv.setUint16(8, 1026, true);      // rate
u8[10] = 26; u8[11] = 3;          // decim / nAxes
dv.setUint16(12, nb, true);       // nPts
let o = 14;
for (let p = 0; p < nb; p++) {
  for (let a = 0; a < nA; a++) {
    dv.setInt16(o, -100 - p - a, true);      // min
    dv.setInt16(o + 2, 200 + p + a, true);   // max
    o += 4;
  }
}
A.state.dcMode = 'ac';
A.onFrame(buf);
const K = 0.061 / 1000;
chk('解析出 32 个点（head 前进 32）', A.state.head === 32, A.state.head);
const first = A.at(0, 31);   // 第 0 个点回看 31 个
chk('X 轴第 1 点 min 解对', Math.abs(first[0] - (-100 * K)) < 1e-9, first[0] + ' vs ' + (-100 * K));
chk('X 轴第 1 点 max 解对', Math.abs(first[1] - (200 * K)) < 1e-9, first[1] + ' vs ' + (200 * K));
const lastZ = A.at(2, 0);    // 最后一点是 Z
chk('Z 轴最后点 min 解对', Math.abs(lastZ[0] - (-(100 + 31 + 2) * K)) < 1e-9, lastZ[0]);
chk('Z 轴最后点 max 解对', Math.abs(lastZ[1] - ((200 + 31 + 2) * K)) < 1e-9, lastZ[1]);
chk('seq 记录正确', A.state.seq === 7, A.state.seq);
chk('tMs / rate / decim 解对',
    A.state.rateHz === 1026 && A.state.decim === 26, A.state.rateHz + '/' + A.state.decim);

console.log('\n=== 6. 直流模式（应把 dc 加回去）===');
A.state.len = 0; A.state.head = 0;
A.state.dcMode = 'dc';
A.state.dc = [0.010, 0, 1.000];
A.onFrame(buf);
const z = A.at(2, 0);
// 容差 1e-6：波形缓冲是 Float32Array（精度约 1e-7），不是 bug。
// 对 <2 g 的振动量，float32 绝对误差 < 1e-7 g，远好于传感器本身的 ±2%。
chk('Z 轴叠加了 1 g 直流', Math.abs(z[1] - ((200 + 31 + 2) * K + 1.0)) < 1e-6, z[1]);
A.state.dcMode = 'ac';

console.log('\n=== 7. 状态 JSON 解析 ===');
A.onStatus(JSON.stringify({
  ty: 'st', tm: 1000, bat_v: 4.05, bat_pct: 88, bat_tr: 1,
  qi: 1, qi_v: 5.02, bus_v: 5.04, bus_ma: 312.5, bus_mw: 1574.9,
  sdp: 1, sdf: 'vib_0001.bin', sd_mb: 2.41, cap: 421000, drop: 0,
  rec: 1, sec: 12, tgt: 90, fs: 1026, decim: 26,
  dc: [1.2, -0.4, 998.1], rms: [9.2, 6.5, 41.3], pk: [31, 22, 180],
  fps: 31.2, cl: 1, qdrop: 0, heap: 210000, psram: 6300000,
}));
chk('电量百分比 = 88', A.state.bat.pct === 88, A.state.bat.pct);
chk('无线充在场', A.state.qi === 1);
chk('采集进行中', A.state.recording === true);
chk('文件名解析', A.state.sd.file === 'vib_0001.bin', A.state.sd.file);
chk('RMS 解析', A.state.rms[2] === 41.3, A.state.rms[2]);
chk('设备信息面板已更新', els['dgWs'].textContent.indexOf('演示') === 0, els['dgWs'].textContent);

console.log('\n=== 8. 演示信号频谱粗检（冲击应周期性出现）===');
let peakHit = 0;
for (let t = 0; t < 3.0; t += 0.002) {
  const v = A.demoSignal(t);
  if (Math.abs(v[0]) > 0.15) peakHit++;      // 冲击峰值应明显高于底噪
}
chk('每 3 秒出现一次冲击（0.014s 窗口内）', peakHit >= 4 && peakHit <= 60, peakHit + ' 次采样超阈');

console.log('\n=== 9. 录制保真模式 / 原始数据下载 ===');
chk('UI 默认「录制时关 WiFi」= 关', A.state.recWifiOff === false, A.state.recWifiOff);
chk('按钮文案已初始化',
    els['btnRecWifi'].textContent.indexOf('录制时关 WiFi') >= 0, els['btnRecWifi'].textContent);
A.onStatus(JSON.stringify({ ty: 'st', rw: 1, rec: 0 }));
chk('设备上报 rw=1 → UI 同步为开', A.state.recWifiOff === true, A.state.recWifiOff);
chk('按钮文案同步更新',
    els['btnRecWifi'].textContent.indexOf('开（采集中断网）') > 0, els['btnRecWifi'].textContent);

// 文件列表：空闲时给下载链接；采集中必须一律不给（否则会与写卡争用 SD 总线）
//   注意先把 state.demo 置 false —— 演示模式下故意不给可点链接（会 404）
const wasDemo = A.state.demo;
A.state.demo = false;
A.state.recording = false;
A.onStatus(JSON.stringify({ ty: 'files', items: [{ n: 'vib_0001.bin', b: 2 * 1048576 }] }));
chk('空闲 + 真机时给出 /dl 下载链接',
    els['fileList'].innerHTML.indexOf('/dl?f=vib_0001.bin') > 0,
    els['fileList'].innerHTML.slice(0, 140));
A.state.recording = true;
A.onStatus(JSON.stringify({ ty: 'files', items: [{ n: 'vib_0001.bin', b: 2 * 1048576 }] }));
chk('采集中拒绝给下载链接',
    els['fileList'].innerHTML.indexOf('/dl?f=') < 0 &&
    els['fileList'].innerHTML.indexOf('采集中不可下载') > 0,
    els['fileList'].innerHTML.slice(0, 140));
A.state.recording = false;
A.state.demo = true;
A.onStatus(JSON.stringify({ ty: 'files', items: [{ n: 'vib_0001.bin', b: 2 * 1048576 }] }));
chk('演示模式不给可点链接（避免 404）',
    els['fileList'].innerHTML.indexOf('/dl?f=') < 0 &&
    els['fileList'].innerHTML.indexOf('真机可用') > 0,
    els['fileList'].innerHTML.slice(0, 140));
A.state.demo = wasDemo;
A.state.recording = false;

console.log('\n=== 10. 丢样本语义分离（① 缓冲溢出 / ② 写卡失败）===');
// 字段名必须与 src/main.cpp 的 buildStatusJson() 一致：
//   drop = 环形缓冲溢出丢的样本；sd_fail = 写卡失败丢的样本；sd_drops = 失败次数；sd_free = 卡剩余 MB
A.state.demo = false;
A.state.recording = true;
A.onStatus(JSON.stringify({
  ty: 'st', sdp: 1, sdf: 'vib_00003.bin', sd_mb: 12.5, cap: 2100000,
  drop: 1520, sd_fail: 0, sd_drops: 0, sd_free: 4096,
  rec: 1, sec: 65, tgt: 90,
}));
chk('① 缓冲溢出样本数解析', A.state.sd.drop === 1520, A.state.sd.drop);
chk('② 写卡失败样本数解析', A.state.sd.fail === 0, A.state.sd.fail);
chk('卡剩余解析', A.state.sd.free === 4096, A.state.sd.free);
A.render(1000);
chk('① 缓冲溢出显示为 1520', String(els['sdDrop'].textContent) === '1520', els['sdDrop'].textContent);
chk('② 写卡失败显示为 0', els['sdFail'].textContent === '0', els['sdFail'].textContent);
chk('卡剩余显示含 MB', els['sdFree'].textContent.indexOf('MB') > 0, els['sdFree'].textContent);

// 写卡失败时必须能看出"失败了几次"，且卡快满时要有告警色
A.onStatus(JSON.stringify({
  ty: 'st', sdp: 1, sdf: 'vib_00003.bin', sd_fail: 8400, sd_drops: 12, sd_free: 160, rec: 1,
}));
A.render(2000);
chk('写卡失败显示含失败次数', els['sdFail'].textContent.indexOf('失败 12 次') > 0, els['sdFail'].textContent);
chk('卡剩余 160MB 触发告警色', els['sdFree'].style.color.indexOf('bad') > 0, els['sdFree'].style.color);

// 两个计数都归零才是"这段数据完整"
A.onStatus(JSON.stringify({ ty: 'st', sdp: 1, drop: 0, sd_fail: 0, sd_drops: 0, sd_free: 30000, rec: 1 }));
A.render(3000);
chk('全零时不告警', els['sdDrop'].style.color.indexOf('warn') < 0 &&
                    els['sdFail'].style.color.indexOf('bad') < 0 &&
                    els['sdFree'].style.color.indexOf('bad') < 0);
A.state.recording = false;
A.state.demo = wasDemo;

console.log('\n=== 11. 演示采集按钮与计时 ===');
A.state.demo = true;
A.state.demoRec = false;
els.btnRec.onclick();
A.demoTick(20000);
A.render(20000);
chk('点击开始后进入演示采集', A.state.recording && els.btnRec.textContent === '停止采集');
chk('演示计时是有效数值', Number.isFinite(A.state.recSec) && A.state.recSec >= 0);
els.btnRec.onclick();
A.demoTick(21000);
A.render(21000);
chk('点击停止后恢复空闲', !A.state.recording && els.btnRec.textContent === '开始采集');

A.state.dcMode = 'dc';
A.state.head = A.state.len = 0;
A.state.rms = [0,0,0]; A.state.pk = [0,0,0];
for(let t=22000;t<25000;t+=17) A.demoTick(t);
A.render(25000);
chk('含重力时说明同步', els.badgeProc.textContent.includes('含重力'));
chk('含重力演示的振动 RMS 不包含 1g 基线', A.state.rms[2] < 100);
A.state.dcMode = 'ac'; A.render(25001);
chk('去直流时说明同步', els.badgeProc.textContent.includes('去直流'));
console.log('\n=== 12. 监控 / 分析页及原始记录 ===');
A.switchPage('analysis');
chk('可切换到分析页', els.analysisPage.hidden === false && els.main.hidden === true);
A.switchPage('monitor');
chk('可返回监控页', els.analysisPage.hidden === true && els.main.hidden === false);
const synthetic = fs.readFileSync(path.join(__dirname,'..','samples','vib_ui_synthetic_1000hz.bin'));
const syntheticAB = synthetic.buffer.slice(synthetic.byteOffset,synthetic.byteOffset+synthetic.byteLength);
const hdr = A.parseHeader(syntheticAB.slice(0,32));
chk('原始文件头解析',hdr.fs===26667 && hdr.bps===6);
document.getElementById('analysisAxis').value='2';
const fre = Float64Array.from({length:1024},(_,i)=>Math.cos(2*Math.PI*64*i/1024));
const fim = new Float64Array(1024); A.fft(fre,fim);
chk('FFT 频点正确',Math.hypot(fre[64],fim[64])>500);
A.loadAnalysisFile(new File([synthetic], 'vib_ui_synthetic_1000hz.bin')).then(async()=>{
  chk('整段原始信号概览已建立',A.analysis.overview?.buckets===1024);
  chk('默认选择原始记录内的时间段',A.analysis.selStart===0&&A.analysis.selEnd>0&&A.analysis.selEnd<=A.analysis.total);
  chk('时域特征已计算',els.featuresTime.innerHTML.includes('RMS') && els.featuresTime.innerHTML.includes('353'));
  chk('频域特征已计算',els.featuresFrequency.innerHTML.includes('谱重心'));
  chk('时频域特征已计算',els.featuresTimeFrequency.innerHTML.includes('最大短时 RMS'));
  chk('分析信息标明采样率',els.analysisInfo.textContent.includes('26667 Hz'));
  chk('频谱主频约 1 kHz',Math.abs(Number((els.featuresFrequency.innerHTML.match(/主频 <b>([0-9.]+) Hz/)||[])[1])-1000)<5, els.featuresFrequency.innerHTML);
  chk('频谱直接显示到奈奎斯特频率',els.analysisInfo.textContent.includes('全频谱 0–13334 Hz'),els.analysisInfo.textContent);
  A.analysis.selStart=1000;A.analysis.selEnd=9000;await A.runAnalysis();
  chk('拖选时间段后分析信息同步',els.analysisInfo.textContent.includes('0.037–0.337 s'),els.analysisInfo.textContent);
  const tone=Float64Array.from({length:8192},(_,i)=>500*Math.sin(2*Math.PI*1000*i/26667));
  const tf=A.stftFeatures(tone,26667,2000);
  chk('稳定正弦的短时 RMS 约 354 mg',Math.abs(tf.peakRms-353.6)<5,tf.peakRms);
  chk('稳定正弦的短时 RMS 变化很小',tf.variation<0.02,tf.variation);
  console.log('\n----------------------------------------');
  console.log(pass + ' 通过 / ' + fail + ' 失败');
  process.exit(fail ? 1 : 0);
}).catch(e=>{console.error(e);process.exit(2);});


