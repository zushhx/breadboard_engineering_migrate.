#include "iis3dwb.h"
#include "config.h"

// ============================================================
//  IIS3DWB 实现
//  SPI：Mode 3、MSB first（DS12569 表 5 SPI slave timing）
// ============================================================

bool IIS3DWB::begin(SPIClass* spi, uint8_t csPin, uint32_t hz) {
  spi_ = spi;
  cs_ = csPin;
  hz_ = hz;

  pinMode(cs_, OUTPUT);
  digitalWrite(cs_, HIGH);

  // 拉高 CS 后再建立一次总线配置，确保空闲电平正确
  spi_->beginTransaction(SPISettings(hz_, MSBFIRST, SPI_MODE3));
  spi_->endTransaction();

  uint8_t id = whoAmI();
  if (id != iis3::WHO_AM_I_VALUE) {
    lastErr_ = id;
    return false;
  }
  return true;
}

uint8_t IIS3DWB::readReg(uint8_t reg) {
  uint8_t v = 0;
  readRegs(reg, &v, 1);
  return v;
}

void IIS3DWB::writeReg(uint8_t reg, uint8_t val) {
  spi_->beginTransaction(SPISettings(hz_, MSBFIRST, SPI_MODE3));
  digitalWrite(cs_, LOW);
  spi_->transfer(reg & 0x7F);   // bit7 = 0 → 写
  spi_->transfer(val);
  digitalWrite(cs_, HIGH);
  spi_->endTransaction();
}

void IIS3DWB::readRegs(uint8_t reg, uint8_t* buf, size_t n) {
  spi_->beginTransaction(SPISettings(hz_, MSBFIRST, SPI_MODE3));
  digitalWrite(cs_, LOW);
  spi_->transfer(reg | iis3::SPI_READ);   // bit7 = 1 → 读
  for (size_t i = 0; i < n; i++) {
    buf[i] = spi_->transfer(0x00);
  }
  digitalWrite(cs_, HIGH);
  spi_->endTransaction();
}

uint8_t IIS3DWB::whoAmI() {
  // 连续读两次取一致值，滤掉上电瞬间的假读
  uint8_t a = readReg(iis3::WHO_AM_I);
  delayMicroseconds(50);
  uint8_t b = readReg(iis3::WHO_AM_I);
  return (a == b) ? a : 0x00;
}

bool IIS3DWB::selfCheck() {
  return whoAmI() == iis3::WHO_AM_I_VALUE;
}

IIS3DWB::RegisterSnapshot IIS3DWB::registerSnapshot() {
  return {whoAmI(), readReg(iis3::CTRL1_XL), readReg(iis3::CTRL3_C),
          readReg(iis3::CTRL6_C), readReg(iis3::STATUS_REG)};
}

bool IIS3DWB::configure() {
  // ---- AN5444 官方初始化序列 ----
  // 1) INT1 上使能 Acc 数据就绪中断
  writeReg(iis3::INT1_CTRL, iis3::INT1_DRDY_XL);
  // 2) 使能三轴模式
  writeReg(iis3::CTRL6_C, iis3::CTRL6_C_3AXIS);
  // 3) 使能加速度计：ODR = 26.667 kHz，FS = ±2 g
  writeReg(iis3::CTRL1_XL, iis3::CTRL1_XL_26K7_2G);

  // ---- 加固：开 BDU，防止读到跨帧拼接的脏数据 ----
  //      CTRL3_C 默认 0x04（IF_INC 已置位），此处显式重写为 BDU|IF_INC
  writeReg(iis3::CTRL3_C, iis3::CTRL3_BDU | iis3::CTRL3_IFINC);

  delay(50);

  // ---- 回读校验 ----
  uint8_t c1 = readReg(iis3::CTRL1_XL);
  if (c1 != iis3::CTRL1_XL_26K7_2G) { lastErr_ = c1; return false; }

  uint8_t c3 = readReg(iis3::CTRL3_C);
  const uint8_t c3Expected = iis3::CTRL3_BDU | iis3::CTRL3_IFINC;
  if ((c3 & c3Expected) != c3Expected) { lastErr_ = c3; return false; }
  uint8_t c6 = readReg(iis3::CTRL6_C);
  if ((c6 & 0x03) != iis3::CTRL6_C_3AXIS) { lastErr_ = c6; return false; }

  return true;
}

bool IIS3DWB::dataReady() {
  return (readReg(iis3::STATUS_REG) & iis3::STATUS_XLDA) != 0;
}

bool IIS3DWB::readRaw(int16_t& x, int16_t& y, int16_t& z) {
  uint8_t b[6];
  // IF_INC 使能后，从 OUTX_L_A 起可一次连续读 6 字节
  readRegs(iis3::OUTX_L_A, b, 6);
  x = (int16_t)((b[1] << 8) | b[0]);
  y = (int16_t)((b[3] << 8) | b[2]);
  z = (int16_t)((b[5] << 8) | b[4]);
  return true;
}

float IIS3DWB::rawToG(int16_t raw) const {
  return (float)raw * SENS_MG_PER_LSB / 1000.0f;   // mg/LSB → g
}

bool IIS3DWB::temperatureReady() {
  return (readReg(iis3::STATUS_REG) & iis3::STATUS_TDA) != 0;
}

float IIS3DWB::temperatureC() {
  uint8_t b[2];
  readRegs(iis3::OUT_TEMP_L, b, 2);
  int16_t t = (int16_t)((b[1] << 8) | b[0]);   // 16 位补码

  // ✅ 已核实：DS12569 Rev8 §2.3「Temperature sensor characteristics」
  //    · 25 °C 时输出 0 LSB（typ.）→ 参考点
  //    · 灵敏度 256 LSB/°C          → 除数
  //    · 刷新率 104 Hz，稳定时间 10 ms，ADC 分辨率 16 bit
  //    · 温度失调 ±15 °C：绝对精度一般，可用室温单点校准（OPC）改善
  return iis3::TEMP_REF_C + (float)t / iis3::TEMP_LSB_PER_C;
}

bool IIS3DWB::setAxisMode(uint8_t sel) {
  // 单轴模式可把噪声密度从 75 µg/√Hz 降到 60 µg/√Hz（DS12569 特性页）
  if (sel > 3) return false;
  uint8_t v = (uint8_t)(sel & (iis3::CTRL6_AXIS_SEL_1 | iis3::CTRL6_AXIS_SEL_0));
  writeReg(iis3::CTRL6_C, v);
  delay(5);
  return (readReg(iis3::CTRL6_C) & 0x03) == v;
}
