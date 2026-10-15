#pragma once
#include <Arduino.h>
#include <atomic>
#include "config.h"

// ============================================================
//  显示抽帧流水线（把 26.667 kHz 压成能上网的 "小信号"）
//
//  数据流：
//      原始 26667 Hz ──┬──> 环形缓冲 ──> SD 卡（未处理的原始 int16）
//                      │
//                      └──> 本模块 ──> min/max 包络 + 减去直流
//                                 ──> 1026 Hz / 32点每帧 / ~32fps / 12.7KB/s
//                                 ──> WebSocket 二进制帧 ──> 浏览器 UI
//
//  三步处理（这就是"被处理过的"含义，处理全在设备端做）：
//    ① 去直流：用 3 秒一阶低通估出重力/零点 dc，信号减去它 → 交流分量。
//       好处：静置时 Z 轴显示 0 g 而不是 1 g，振动幅度一目了然。
//    ② min/max 包络抽帧：每 26 个原始样本压成 1 个抽帧点，
//       同时保留这 26 个样本里的最小值与最大值。
//       好处：冲击/敲击这类窄脉冲一个都不丢（隔点抽样会整段抽没）。
//    ③ 统计量：区间真 RMS（对全速原始样本求均方）+ 峰值保持。
//       RMS 用的是**未抽帧的原始样本**，所以数值是真实的振动有效值。
//
//  并发模型（重要）：
//    · push() / service() 只在 **core 1**（Arduino loop）执行 —— 单线程，无需加锁
//    · pop() 在 **core 0**（netTask）执行
//    · 两者通过 std::atomic 的 acquire/release 交接帧，无锁、不阻塞采集
//      （采集循环只有 37.5µs 预算，任何 mutex/临界区都不能进）
// ============================================================

struct DispFrame {
  uint16_t seq;
  uint32_t tMs;
  uint16_t nPts;
  uint8_t  nAxes;                       // 固定 3
  int16_t  mn[DISP_AXES][DISP_PTS];     // 原始 LSB 单位的交流分量
  int16_t  mx[DISP_AXES][DISP_PTS];
};

struct DispStats {
  float    rmsMg[DISP_AXES];
  float    pkMg[DISP_AXES];
  float    dcMg[DISP_AXES];             // 被减掉的直流，UI 切"直流显示"时要加回去
  uint32_t framesTotal;
  uint16_t lastSeq;
  uint16_t rateHz;                      // 抽帧后等效采样率
};

class DispPipeline {
 public:
  void begin();

  // ---- core 1 ----
  void push(int16_t x, int16_t y, int16_t z);   // 每个原始样本调一次
  void service();                               // loop() 里高频调；负责 500ms 发布 + 处理清零请求

  // ---- core 0（netTask）----
  bool pop(DispFrame& out);                     // 取一帧，无帧返回 false
  const DispStats& stats() const { return stats_; }
  void requestZero() { zeroReq_ = true; }

  // 诊断
  uint32_t frameDropCount() const { return frameDrop_.load(std::memory_order_relaxed); }
  uint32_t queueDepth() const;

 private:
  void publishStats();
  void emitPoint();

  // ---- 直流估计 ----
  float dc_[DISP_AXES] = {0, 0, 0};
  bool  dcInit_ = false;
  float dcAlpha_ = 0.0f;

  // ---- 抽帧累加器（当前抽帧点覆盖的窗口） ----
  //  ★ 必须是 float：窗口初值用 ±100000 表示"还没收到样本"，
  //    若声明成 int16_t 会溢出成乱值，包络就全错了。
  //    （首版就是这样，靠编译警告 overflow in conversion from 'float' to 'int16_t' 抓出来的）
  uint16_t decCnt_ = 0;
  float    wMin_[DISP_AXES] = {0, 0, 0};
  float    wMax_[DISP_AXES] = {0, 0, 0};

  // ---- 当前正在填的帧 ----
  DispFrame cur_{};
  uint16_t  curPts_ = 0;
  uint32_t  curStartMs_ = 0;

  // ---- 统计量累加器（每个 500ms 区间重置） ----
  double   ss_[DISP_AXES] = {0, 0, 0};   // 平方和（LSB²）
  uint32_t ssN_ = 0;
  float    pkAcc_[DISP_AXES] = {0, 0, 0};
  float    rmsSm_[DISP_AXES] = {0, 0, 0}; // RMS 平滑
  float    pkSm_[DISP_AXES] = {0, 0, 0};  // 峰值保持 + 衰减

  int16_t  lastRaw_[DISP_AXES] = {0, 0, 0};

  // ---- 帧队列（core1 生产 / core0 消费） ----
  DispFrame             q_[DISP_QUEUE];
  std::atomic<uint32_t> qHead_{0};
  std::atomic<uint32_t> qTail_{0};
  std::atomic<uint32_t> frameDrop_{0};
  uint16_t              seq_ = 0;

  volatile bool zeroReq_ = false;
  uint32_t      lastPubMs_ = 0;

  DispStats stats_{};
};
