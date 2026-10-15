#pragma once
#include <Arduino.h>
#include <SPI.h>

// ============================================================
//  IIS3DWB 驱动（超宽带 3 轴数字振动加速度计）
//
//  依据：ST DS12569 **Rev 8**（已下载官方原件逐条核对，非二手转述）
//        寄存器地址表 = Table 7；机械特性 = Table 3；温度特性 = §2.3
//        ST AN5444（官方初始化序列）
//
//  ✅ 关键值核实记录（2026-09-18，对照 DS12569 Rev 8 原件）：
//     · WHO_AM_I(0Fh) 默认值 = 01111011b = 0x7B      （Table 7）
//     · STATUS_REG(1Eh) 位序 bit7→bit0 = 0,0,0,0,0,TDA,0,XLDA
//                                            → XLDA = bit0（§9.21 Table D2/D3）
//     · 片内温度：25 °C 时输出 0 LSB（typ.），256 LSB/°C，刷新 104 Hz（§2.3）
//     · 灵敏度 @FS=±2g = 0.061 mg/LSB，公差 ±2%（Table 3）
//
//  CTRL1_XL、CTRL3_C 的寄存器地址分别为 0x10、0x12。
//  DS12569 Rev8 §3.1 明确支持 SPI Mode 0 和 3；本驱动采用 Mode 3、
//  MSB first。不能把 Mode 0 本身归为错误配置。
// ============================================================

namespace iis3 {

// ---- 寄存器地址（DS12569 Rev8 Table 7 Register address map） ----
// 注：名字统一带 REG_ 前缀，避免与 ESP-IDF 的宏撞名
//     （首次编译实测：PIN_CTRL 已被 ESP-IDF 的 IO MUX 宏占用）
constexpr uint8_t REG_PIN_CTRL      = 0x02;
constexpr uint8_t FIFO_CTRL1        = 0x07;
constexpr uint8_t FIFO_CTRL2        = 0x08;
constexpr uint8_t FIFO_CTRL3        = 0x09;
constexpr uint8_t FIFO_CTRL4        = 0x0A;
constexpr uint8_t COUNTER_BDR_REG1  = 0x0B;
constexpr uint8_t COUNTER_BDR_REG2  = 0x0C;
constexpr uint8_t INT1_CTRL         = 0x0D;
constexpr uint8_t INT2_CTRL         = 0x0E;
constexpr uint8_t WHO_AM_I          = 0x0F;
constexpr uint8_t CTRL1_XL          = 0x10;
constexpr uint8_t CTRL3_C           = 0x12;
constexpr uint8_t CTRL4_C           = 0x13;
constexpr uint8_t CTRL5_C           = 0x14;
constexpr uint8_t CTRL6_C           = 0x15;
constexpr uint8_t CTRL7_C           = 0x16;
constexpr uint8_t CTRL8_XL          = 0x17;
constexpr uint8_t CTRL10_C          = 0x19;
constexpr uint8_t ALL_INT_SRC       = 0x1A;
constexpr uint8_t WAKE_UP_SRC       = 0x1B;
constexpr uint8_t STATUS_REG        = 0x1E;
constexpr uint8_t OUT_TEMP_L        = 0x20;
constexpr uint8_t OUT_TEMP_H        = 0x21;
constexpr uint8_t OUTX_L_A          = 0x28;
constexpr uint8_t OUTX_H_A          = 0x29;
constexpr uint8_t OUTY_L_A          = 0x2A;
constexpr uint8_t OUTY_H_A          = 0x2B;
constexpr uint8_t OUTZ_L_A          = 0x2C;
constexpr uint8_t OUTZ_H_A          = 0x2D;
constexpr uint8_t FIFO_STATUS1      = 0x3A;
constexpr uint8_t FIFO_STATUS2      = 0x3B;
constexpr uint8_t TIMESTAMP0        = 0x40;
constexpr uint8_t INTERNAL_FREQ_FINE= 0x63;
constexpr uint8_t FIFO_DATA_OUT_TAG = 0x78;
constexpr uint8_t FIFO_DATA_OUT_X_L = 0x79;

// ---- 关键常量 ----
constexpr uint8_t WHO_AM_I_VALUE = 0x7B;   // ★ 已核实：DS12569 Rev8，默认值 01111011b

// AN5444 官方初始化序列用到的值
constexpr uint8_t CTRL1_XL_26K7_2G = 0xA0; // ODR = 26.667 kHz, FS = ±2 g
constexpr uint8_t INT1_DRDY_XL     = 0x01; // INT1_CTRL：Acc 数据就绪中断
constexpr uint8_t CTRL6_C_3AXIS    = 0x00; // 使能三轴

// CTRL3_C 位（默认 0x04，IF_INC 已置位）
constexpr uint8_t CTRL3_BDU   = 0x40;  // Block Data Update：防高低字节跨帧
constexpr uint8_t CTRL3_IFINC = 0x04;  // 自动地址递增（多字节连续读的前提）

// ---- STATUS_REG (1Eh) 位 ----
// ✅ 已核实：DS12569 Rev8 §9.21 Table D2/D3
//    位序 bit7→bit0 = 0, 0, 0, 0, 0, TDA, 0, XLDA
constexpr uint8_t STATUS_XLDA = 0x01;  // bit0：加速度新数据就绪
constexpr uint8_t STATUS_TDA  = 0x04;  // bit2：温度新数据就绪

// ---- 灵敏度 ----
// ✅ 已核实：DS12569 Rev8 Table 3「Mechanical characteristics」
//    工厂校准并修调后的值，公差 ±2%（@Vdd=3.0V, T=25°C）
constexpr float SENS_MG_LSB_2G  = 0.061f;
constexpr float SENS_MG_LSB_4G  = 0.122f;
constexpr float SENS_MG_LSB_8G  = 0.244f;
constexpr float SENS_MG_LSB_16G = 0.488f;

// ---- 片内温度 ----
// ✅ 已核实：DS12569 Rev8 §2.3「Temperature sensor characteristics」
//    · 温度刷新率 104 Hz
//    · 温度失调 ±15 °C（注：可用室温单点校准 OPC 改善）
//    · 灵敏度 256 LSB/°C
//    · 25 °C 时输出 0 LSB（typ.）  → 因此 T(°C) = 25 + raw/256
//    · 稳定时间 10 ms；ADC 分辨率 16 bit
constexpr float TEMP_REF_C      = 25.0f;
constexpr float TEMP_LSB_PER_C  = 256.0f;
constexpr float TEMP_REFRESH_HZ = 104.0f;

// ---- CTRL6_C 轴选择 ----
// 单轴模式下噪声密度可低至 60 µg/√Hz（三轴模式为 75 µg/√Hz）
// 只关心单一方向时切单轴可获得更低噪声底
constexpr uint8_t CTRL6_AXIS_SEL_1 = 0x02;  // bit1
constexpr uint8_t CTRL6_AXIS_SEL_0 = 0x01;  // bit0

// 多字节读时地址须置 bit7
constexpr uint8_t SPI_READ  = 0x80;
constexpr uint8_t SPI_WRITE = 0x00;

}  // namespace iis3

class IIS3DWB {
 public:
  struct RegisterSnapshot {
    uint8_t identity, ctrl1, ctrl3, ctrl6, status;
  };
  // 返回值：true = 通信建立且 WHO_AM_I 正确
  bool begin(SPIClass* spi, uint8_t csPin, uint32_t hz);

  uint8_t whoAmI();                 // 读 0x0F，应为 0x7B
  bool    selfCheck();              // WHO_AM_I == 0x7B
  RegisterSnapshot registerSnapshot(); // 实时只读身份与配置，不使用上电缓存

  // AN5444 官方序列 + BDU 加固。返回 false 表示某步回读校验失败。
  bool configure();

  bool dataReady();                 // 轮询 STATUS_REG.XLDA（bit0）
  bool temperatureReady();          // 轮询 STATUS_REG.TDA （bit2）
  bool readRaw(int16_t& x, int16_t& y, int16_t& z);   // 一次连续读 6 字节

  float rawToG(int16_t raw) const;  // 换算成 g
  float temperatureC();             // 片内温度 = 25 + raw/256（已核实 DS12569 Rev8 §2.3）

  // 可选：切单轴模式可把噪声密度从 75 µg/√Hz 降到 60 µg/√Hz。
  // sel 取值：0 = 三轴（默认）, 1 = 仅 X, 2 = 仅 Y, 3 = 仅 Z
  bool setAxisMode(uint8_t sel);

  uint8_t lastError() const { return lastErr_; }

 private:
  uint8_t readReg(uint8_t reg);
  void    writeReg(uint8_t reg, uint8_t val);
  void    readRegs(uint8_t reg, uint8_t* buf, size_t n);

  SPIClass* spi_ = nullptr;
  uint8_t   cs_  = 255;
  uint32_t  hz_  = 1000000;
  uint8_t   lastErr_ = 0;
};
