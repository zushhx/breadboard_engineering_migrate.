#pragma once
#include <Arduino.h>
#include <Wire.h>
#include <freertos/semphr.h>

// ============================================================
//  INA226 驱动（36V / 16bit 电流-功率监测）
//  依据：TI SBOS547 数据手册
//
//  ★ 2026-09-21 决定：**不更换分流电阻**，直接用模块自带的 0.1 Ω。
//    （理由与"何时改回"见 config.h 的 INA226 段注释）
//
//  0.1Ω 下的关键数值：
//    Vsense 满量程 = ±81.92 mV  →  电流满量程 ±0.8192 A
//    Current_LSB   = 25 µA
//    CAL 寄存器    = 0.00512 / (Current_LSB × Rshunt) = 2048
//    Power_LSB     = 25 × Current_LSB = 0.625 mW
//    Vbus_LSB      = 1.25 mV（满量程 40.96 V）
//
//  ★ 为什么 CAL 恒为 2048：本驱动取 Current_LSB = 满量程电流 / 32768，
//    此时 1 个电流 LSB 恰好等于 1 个分流电压 LSB（2.5 µV），于是
//    CAL = 0.00512 / (I_LSB × R) = 0.00512 / 2.5µV = 2048，与 R 无关。
//    → 换分流电阻只需改 config.h 的 SHUNT_OHM / INA_MAX_CURRENT 两个宏。
// ============================================================

namespace ina {

// 寄存器地址（SBOS547 表 6）
constexpr uint8_t REG_CONFIG    = 0x00;
constexpr uint8_t REG_SHUNT_V   = 0x01;
constexpr uint8_t REG_BUS_V     = 0x02;
constexpr uint8_t REG_POWER     = 0x03;
constexpr uint8_t REG_CURRENT   = 0x04;
constexpr uint8_t REG_CALIB     = 0x05;
constexpr uint8_t REG_MASK_EN   = 0x06;
constexpr uint8_t REG_ALERT_LIM = 0x07;
constexpr uint8_t REG_MANUF_ID  = 0xFE;   // 应读 0x5449 = "TI"
constexpr uint8_t REG_DIE_ID    = 0xFF;   // 应读 0x2260

// CONFIG 位域取值（SBOS547 表 7）
constexpr uint16_t AVG_16      = 2;   // 16 次平均
constexpr uint16_t CT_1P1MS    = 4;   // 1.1 ms 转换时间
constexpr uint16_t MODE_CONT   = 7;   // 连续测分流 + 总线
constexpr uint16_t CONFIG_EXPECT = 0x4000 | (AVG_16 << 9) | (CT_1P1MS << 6) |
                                  (CT_1P1MS << 3) | MODE_CONT; // 保留 bit14，=0x4527

// Mask/Enable 位
constexpr uint16_t MASK_CVRF   = 0x0008;   // 转换就绪标志（读后自清）

// LSB
constexpr float LSB_SHUNT_V = 0.0000025f;  // 2.5 µV
constexpr float LSB_BUS_V   = 0.00125f;    // 1.25 mV

constexpr uint16_t MANUF_ID_EXPECT = 0x5449;
constexpr uint16_t DIE_ID_EXPECT   = 0x2260;

}  // namespace ina

class INA226 {
 public:
  // rshunt / maxCurrent 用于计算 CAL；默认取 0.1Ω / 0.8192A（模块自带分流，未更换）
  bool begin(TwoWire* wire, uint8_t addr = 0x40,
             float rshuntOhm = 0.100f, float maxCurrentA = 0.8192f);

  bool isConnected();             // 读制造商 ID / 芯片 ID
  uint16_t manufacturerId();
  uint16_t dieId();

  bool configure();               // 写 CONFIG
  bool setCalibration();          // 写 CAL，返回 false 表示 CAL 超出 16 位

  bool     conversionReady();
  uint16_t alertFlags();

  float shuntVoltage_mV();        // 分流压降（带符号）
  float busVoltage_V();
  float current_mA();
  float power_mW();

  uint16_t calibrationValue() const { return cal_; }
  uint16_t configurationRegisterValue() { return readReg(ina::REG_CONFIG); }
  uint16_t calibrationRegisterValue() { return readReg(ina::REG_CALIB); }
  float currentLsb_A() const { return currentLsb_; }

  // I2C 全地址扫描（排障用），返回扫到的地址个数
  static int scanBus(TwoWire* wire, uint8_t* found, int maxFound);

 private:
  uint16_t readReg(uint8_t reg);
  bool     writeReg(uint8_t reg, uint16_t val);

  TwoWire* wire_ = nullptr;
  SemaphoreHandle_t regMutex_ = nullptr;
  uint8_t  addr_ = 0x40;
  float    rshunt_ = 0.100f;
  float    currentLsb_ = 0.000025f;   // 满量程 0.8192A / 32768 = 25µA
  uint16_t cal_ = 0;
};
