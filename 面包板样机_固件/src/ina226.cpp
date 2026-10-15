#include "ina226.h"

// ============================================================
//  INA226 实现（TI SBOS547）
//  寄存器为 16 位、大端（高字节先）
// ============================================================

bool INA226::begin(TwoWire* wire, uint8_t addr, float rshuntOhm, float maxCurrentA) {
  wire_ = wire;
  addr_ = addr;
  rshunt_ = rshuntOhm;
  if (!regMutex_) regMutex_ = xSemaphoreCreateMutex();
  if (!regMutex_) return false;

  // Current_LSB = 满量程电流 / 32768
  currentLsb_ = maxCurrentA / 32768.0f;

  if (!isConnected()) return false;
  if (!configure()) return false;
  if (!setCalibration()) return false;
  return true;
}

bool INA226::writeReg(uint8_t reg, uint16_t val) {
  if (!regMutex_ || xSemaphoreTake(regMutex_, portMAX_DELAY) != pdTRUE) return false;
  wire_->beginTransmission(addr_);
  wire_->write(reg);
  wire_->write((uint8_t)(val >> 8));
  wire_->write((uint8_t)(val & 0xFF));
  bool ok = wire_->endTransmission() == 0;
  xSemaphoreGive(regMutex_);
  return ok;
}

uint16_t INA226::readReg(uint8_t reg) {
  if (!regMutex_ || xSemaphoreTake(regMutex_, portMAX_DELAY) != pdTRUE) return 0xFFFF;
  // 串口诊断与 netTask 共用实例；保护选寄存器、读回和 RX 缓冲取数的整个过程。
  uint16_t value = 0xFFFF;
  wire_->beginTransmission(addr_);
  wire_->write(reg);
  if (wire_->endTransmission(false) == 0 && wire_->requestFrom((int)addr_, 2) == 2) {
    uint16_t hi = wire_->read();
    uint16_t lo = wire_->read();
    value = (uint16_t)((hi << 8) | lo);
  }
  xSemaphoreGive(regMutex_);
  return value;
}

bool INA226::isConnected() {
  uint16_t m = manufacturerId();
  uint16_t d = dieId();
  return (m == ina::MANUF_ID_EXPECT) && (d == ina::DIE_ID_EXPECT);
}

uint16_t INA226::manufacturerId() { return readReg(ina::REG_MANUF_ID); }
uint16_t INA226::dieId()          { return readReg(ina::REG_DIE_ID); }

bool INA226::configure() {
  // D14 的保留位上电值为 1；写配置时保留它，否则回读不会等于写入值。
  const uint16_t cfg = ina::CONFIG_EXPECT;
  if (!writeReg(ina::REG_CONFIG, cfg)) return false;
  delay(2);
  return readReg(ina::REG_CONFIG) == cfg;
}

bool INA226::setCalibration() {
  // CAL = 0.00512 / (Current_LSB × Rshunt)
  float fcal = 0.00512f / (currentLsb_ * rshunt_);
  if (fcal < 1.0f || fcal > 65535.0f) return false;
  cal_ = (uint16_t)(fcal + 0.5f);
  return writeReg(ina::REG_CALIB, cal_) && readReg(ina::REG_CALIB) == cal_;
}

bool INA226::conversionReady() {
  return (readReg(ina::REG_MASK_EN) & ina::MASK_CVRF) != 0;
}

uint16_t INA226::alertFlags() { return readReg(ina::REG_MASK_EN); }

float INA226::shuntVoltage_mV() {
  int16_t raw = (int16_t)readReg(ina::REG_SHUNT_V);
  return (float)raw * ina::LSB_SHUNT_V * 1000.0f;    // V → mV
}

float INA226::busVoltage_V() {
  uint16_t raw = readReg(ina::REG_BUS_V);
  return (float)raw * ina::LSB_BUS_V;
}

float INA226::current_mA() {
  int16_t raw = (int16_t)readReg(ina::REG_CURRENT);
  return (float)raw * currentLsb_ * 1000.0f;         // A → mA
}

float INA226::power_mW() {
  uint16_t raw = readReg(ina::REG_POWER);
  // Power_LSB = 25 × Current_LSB
  return (float)raw * 25.0f * currentLsb_ * 1000.0f; // W → mW
}

int INA226::scanBus(TwoWire* wire, uint8_t* found, int maxFound) {
  int n = 0;
  for (uint8_t a = 1; a < 127; a++) {
    wire->beginTransmission(a);
    if (wire->endTransmission() == 0) {
      if (n < maxFound) found[n] = a;
      n++;
    }
  }
  return n;
}
