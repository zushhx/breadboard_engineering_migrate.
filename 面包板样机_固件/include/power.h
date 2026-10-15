#pragma once
#include <Arduino.h>

// ============================================================
//  电源状态监测：电池电量 + 无线充（Qi）在场判定
//
//  ● 为什么 Qi 检测要单独一路 ADC
//    系统 5V 轨上，USB（经 D1）与 Qi 输出（经 SS34）是"二极管或"关系，
//    两者叠在同一条轨上 —— 量 5V 轨电压**分不出是 USB 还是无线充**。
//    所以 Qi 那一路必须取在**它自己模块的输出端、SS34 之前**：
//        有 Qi：读回 ~5.0V；没 Qi：0V。判定干净、无歧义。
//
//  ● 为什么电量只能"估"
//    没有库仑计（原设计的 INA226 分流是串在系统回路上的，
//    测的是系统总电流，不是电池电流），所以只能用**开路电压法**：
//    静置电压 → 查 SOC 曲线。缺点很实在：
//      ① 锂电 3.7~3.9V 是平台区，一小段电压覆盖大半容量 → 平台区误差可达 ±15%
//      ② 带负载时内阻压降会让读数偏低 → 大电流时 SOC 会"掉一格"
//    缓解：只在总线电流较小时才更新 SOC，并做缓变（一次最多动 1%）。
//    要精确必须加库仑计（如 BQ27441 / MAX17048），属后续升级项。
// ============================================================

enum ChgState : uint8_t {
  CHG_NONE = 0,   // 无外部供电
  CHG_WIRELESS,   // Qi 在场（无线供电/充电）
  CHG_USB,        // 有 5V 但不是 Qi → USB
};

class PowerMon {
 public:
  void begin();

  // 由 netTask 每 STATUS_PERIOD_MS 调一次；不在采集核心路径上
  void update();

  // 主循环把 INA226 的系统电流喂进来：① 判断"是否轻载"（决定能否刷新 SOC）
  //   ② 判断"有没有 5V 且不在充"（USB 供电判据）
  void setLoadMA(float ma) { loadMA_ = ma; }
  void setBusVolt_V(float v) { busV_ = v; }

  // ---- 电池 ----
  float   vbat()      const { return vbat_; }        // V
  uint8_t socPct()    const { return soc_; }         // 0~100
  int8_t  socTrend()  const { return trend_; }       // -1 下降 / 0 平 / +1 上升

  // ---- 无线充 ----
  bool      qiPresent() const { return qi_; }
  float     qiVolt()    const { return qiV_; }
  ChgState  chgState()  const { return chg_; }
  const char* chgText() const;

  // 原始 ADC 读数（排障用）
  uint16_t rawVbat() const { return rawVbat_; }
  uint16_t rawQi()   const { return rawQi_; }

 private:
  float readChannel(uint8_t pin, float trim);

  float     vbat_ = 0.0f;
  float     qiV_  = 0.0f;
  uint8_t   soc_  = 0;
  int8_t    trend_ = 0;
  bool      qi_   = false;
  ChgState  chg_  = CHG_NONE;
  uint16_t  rawVbat_ = 0;
  uint16_t  rawQi_   = 0;
  float     loadMA_  = 0.0f;
  float     busV_    = 0.0f;

  // SOC 缓变 + 趋势判定用的历史
  uint32_t  lastUpdateMs_ = 0;
  uint32_t  lastTrendMs_  = 0;
  float     trendRefV_    = 0.0f;
  bool      socInit_      = false;
  uint8_t   socTarget_    = 0;
};

// 电量估算：开路电压 → SOC（分段线性）。表来自单节 Li-ion 典型放电曲线。
uint8_t socFromVoltage(float v);
