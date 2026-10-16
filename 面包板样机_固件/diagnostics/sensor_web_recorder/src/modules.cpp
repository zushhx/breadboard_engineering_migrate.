#include "modules.h"
#include "module_values.h"
#include "serial_compat.h"
#include "driver/spi_master.h"
#include "driver/i2c_master.h"
#include "esp_timer.h"
#include <cstring>

namespace modules {
static spi_device_handle_t sensor = nullptr;
static i2c_master_bus_handle_t bus = nullptr;
static i2c_master_dev_handle_t monitor = nullptr;
static unsigned monitorAddress = 0;
static bool initAttempted = false;

static bool spiRead(uint8_t reg, uint8_t* out, unsigned count) {
  if (!sensor || count > 7) return false;
  uint8_t tx[8] = {}, rx[8] = {};
  tx[0] = reg | 0x80;
  spi_transaction_t t = {};
  t.length = (count + 1) * 8; t.tx_buffer = tx; t.rx_buffer = rx;
  esp_err_t error = spi_device_polling_transmit(sensor, &t);
  if (error != ESP_OK) {
    Serial.printf("[modules] SPI read reg=%02X error=%s\n", reg, esp_err_to_name(error));
    return false;
  }
  memcpy(out, rx + 1, count);
  return true;
}
static bool spiWrite(uint8_t reg, uint8_t value) {
  uint8_t tx[2] = {reg, value};
  spi_transaction_t t = {};
  t.length = 16; t.tx_buffer = tx;
  esp_err_t error = spi_device_polling_transmit(sensor, &t);
  Serial.printf("[modules] SPI write reg=%02X value=%02X status=%s\n", reg, value, esp_err_to_name(error));
  return error == ESP_OK;
}
static bool inaRead(uint8_t reg, uint16_t& out) {
  if (!monitor) return false;
  uint8_t rx[2] = {};
  esp_err_t error = i2c_master_transmit_receive(monitor, &reg, 1, rx, 2, 100);
  if (error != ESP_OK) {
    Serial.printf("[modules] I2C read reg=%02X error=%s\n", reg, esp_err_to_name(error));
    return false;
  }
  out = be16(rx);
  return true;
}
static bool inaWrite(uint8_t reg, uint16_t value) {
  uint8_t tx[3] = {reg, static_cast<uint8_t>(value >> 8), static_cast<uint8_t>(value)};
  esp_err_t error = i2c_master_transmit(monitor, tx, 3, 100);
  Serial.printf("[modules] I2C write reg=%02X value=%04X status=%s\n", reg, value, esp_err_to_name(error));
  return error == ESP_OK;
}
static bool sensorIdentity() {
  uint8_t who = 0;
  bool ok = spiRead(0x0F, &who, 1);
  Serial.printf("[modules] IIS identity transport=%u who=%02X expected=7B valid=%u\n", ok, who, ok && who == 0x7B);
  return ok && who == 0x7B;
}
static bool monitorIdentity() {
  uint16_t maker = 0, die = 0;
  bool a = inaRead(0xFE, maker), b = inaRead(0xFF, die);
  bool ok = a && b && inaIdentity(maker, die);
  Serial.printf("[modules] INA identity address=%02X transport=%u maker=%04X die=%04X valid=%u\n",
                monitorAddress, a && b, maker, die, ok);
  return ok;
}
static void discoverMonitor() {
  if (!bus) return;
  unsigned found = 0;
  for (unsigned address = 0x40; address <= 0x4F; ++address) {
    esp_err_t probe = i2c_master_probe(bus, address, 50);
    if (probe == ESP_ERR_NOT_FOUND) continue;
    if (probe != ESP_OK) {
      Serial.printf("[modules] I2C probe address=%02X error=%s; discovery stopped\n", address, esp_err_to_name(probe));
      if (monitor) { i2c_master_bus_rm_device(monitor); monitor = nullptr; }
      return;
    }
    i2c_device_config_t device = {};
    device.dev_addr_length = I2C_ADDR_BIT_LEN_7;
    device.device_address = address; device.scl_speed_hz = 100000;
    i2c_master_dev_handle_t candidate = nullptr;
    if (i2c_master_bus_add_device(bus, &device, &candidate) != ESP_OK) continue;
    uint8_t reg = 0xFE, rx[2] = {};
    bool a = i2c_master_transmit_receive(candidate, &reg, 1, rx, 2, 100) == ESP_OK;
    uint16_t maker = be16(rx);
    reg = 0xFF;
    bool b = i2c_master_transmit_receive(candidate, &reg, 1, rx, 2, 100) == ESP_OK;
    uint16_t die = be16(rx);
    Serial.printf("[modules] discovery address=%02X transport=%u maker=%04X die=%04X\n", address, a && b, maker, die);
    if (a && b && inaIdentity(maker, die)) {
      ++found;
      if (found == 1) { monitor = candidate; monitorAddress = address; }
      else i2c_master_bus_rm_device(candidate);
    } else i2c_master_bus_rm_device(candidate);
  }
  if (found != 1 && monitor) { i2c_master_bus_rm_device(monitor); monitor = nullptr; }
  Serial.printf("[modules] INA discovery matched=%u usable=%u historical_address=44\n", found, monitor != nullptr);
}
static void snapshot() {
  Serial.printf("[modules] snapshot time_us=%lld init_attempted=%u readonly_registers=1\n",
                static_cast<long long>(esp_timer_get_time()), initAttempted);
  bool identity = sensorIdentity();
  uint8_t c1 = 0, c3 = 0, c6 = 0, irq = 0, status = 0, xyz[6] = {};
  bool a = spiRead(0x10, &c1, 1), b = spiRead(0x12, &c3, 1), c = spiRead(0x15, &c6, 1);
  bool d = spiRead(0x0D, &irq, 1), e = spiRead(0x1E, &status, 1), f = spiRead(0x28, xyz, 6);
  bool valid = identity && a && b && c && d && e && f && c1 == 0xA0 && c3 == 0x44 && c6 == 0 && irq == 0 && (status & 1);
  Serial.printf("[modules] IIS ctrl1=%02X ctrl3=%02X ctrl6=%02X int1=%02X status=%02X data_valid=%u\n", c1, c3, c6, irq, status, valid);
  if (valid) Serial.printf("[modules] IIS snapshot_mg x=%.3f y=%.3f z=%.3f fullrate_capture=0\n",
      leSigned16(xyz) * 0.061, leSigned16(xyz + 2) * 0.061, leSigned16(xyz + 4) * 0.061);
  bool identified = monitorIdentity();
  InaRegisters r = {};
  bool ok = inaRead(0, r.config);
  ok = inaRead(5, r.calibration) && ok;
  ok = inaRead(1, r.shunt) && ok;
  ok = inaRead(2, r.bus) && ok;
  ok = inaRead(4, r.current) && ok;
  ok = inaRead(3, r.power) && ok;
  ok = inaRead(6, r.mask) && ok;
  bool inaOk = inaValid(ok, identified, r);
  Serial.printf("[modules] INA transport=%u config=%04X cal=%04X mask=%04X data_valid=%u\n", ok, r.config, r.calibration, r.mask, inaOk);
  if (inaOk) Serial.printf("[modules] INA sensor_branch bus_V=%.5f shunt_uV=%.3f current_mA=%.5f power_mW=%.5f\n",
      busVolts(r), shuntMicroVolts(r), currentMilliAmps(r), powerMilliWatts(r));
  else Serial.println("[modules] INA INVALID; current/power suppressed; no calibration rewrite");
  Serial.printf("[modules] snapshot_end iis_valid=%u ina_valid=%u\n", valid, inaOk);
}
bool monitorRead(InaRegisters& r, bool& valid) {
  uint16_t maker = 0, die = 0;
  bool ok = inaRead(0xFE, maker) && inaRead(0xFF, die) && inaIdentity(maker, die);
  ok = inaRead(0, r.config) && ok;
  ok = inaRead(5, r.calibration) && ok;
  ok = inaRead(1, r.shunt) && ok;
  ok = inaRead(2, r.bus) && ok;
  ok = inaRead(4, r.current) && ok;
  ok = inaRead(3, r.power) && ok;
  ok = inaRead(6, r.mask) && ok;
  valid = inaValid(ok, true, r);
  return ok && r.config == kInaConfig && r.calibration == kInaCal && !(r.mask & 4);
}
unsigned monitorAddressValue() { return monitorAddress; }
static bool check(uint8_t reg, uint8_t expected) {
  uint8_t value = 0;
  return spiRead(reg, &value, 1) && value == expected;
}
bool prepareRecord() {
  bool a = sensorIdentity(), b = monitorIdentity();
  if (!a || !b) return false;
  // Only one INA configuration attempt per MCU boot; subsequent starts verify it.
  if (!initAttempted) {
    initAttempted = true;
    if (!inaWrite(0, kInaConfig) || !inaWrite(5, kInaCal)) return false;
    delay(100);
  }
  InaRegisters r = {}; bool valid = false;
  if (!monitorRead(r, valid) || !valid) return false;
  bool ok = spiWrite(0x10, 0x00); // Axis configuration changes require power-down first.
  ok = ok && spiWrite(0x0D, 0x00);
  ok = ok && spiWrite(0x0E, 0x00);
  ok = ok && spiWrite(0x12, 0x44);
  ok = ok && spiWrite(0x15, 0x00);
  ok = ok && spiWrite(0x16, 0x00) && spiWrite(0x17, 0x00); // No user offset/HPF/LPF2.
  ok = ok && spiWrite(0x19, 0x00); // Timestamp batching disabled.
  ok = ok && spiWrite(0x0A, 0x00); // Clear FIFO only before a new record.
  ok = ok && spiWrite(0x07, 0x80) && spiWrite(0x08, 0x00);
  ok = ok && spiWrite(0x09, 0x0A);
  return ok && check(0x10, 0) && check(0x12, 0x44) && check(0x15, 0) &&
      check(0x0D, 0) && check(0x0E, 0) && check(0x16, 0) && check(0x17, 0) &&
      check(0x19, 0) && check(0x07, 0x80) && check(0x08, 0) && check(0x09, 0x0A) && check(0x0A, 0);
}
bool startFifo() {
  if (!spiWrite(0x10, 0xA0) || !check(0x10, 0xA0)) return false;
  delay(10); // Datasheet power-on settling before starting the recording FIFO.
  return spiWrite(0x0A, 0x06) && check(0x0A, 0x06);
}
bool stopInput() {
  bool a = spiWrite(0x09, 0x00);
  bool b = spiWrite(0x10, 0x00);
  return a && b && check(0x09, 0) && check(0x10, 0);
}
bool finishFifo() { return spiWrite(0x0A, 0) && check(0x0A, 0); }
bool fifoStatus(uint16_t& count, bool& overrun) {
  uint8_t status[2] = {};
  if (!spiRead(0x3A, status, 2)) return false;
  count = status[0] | ((status[1] & 3) << 8);
  overrun = (status[1] & 0x68) != 0; // Full, overrun or latched overrun all fail the run.
  return true;
}
bool fifoFrame(uint8_t* frame) {
  // Exactly one TAG+XYZ transaction, 0x78..0x7E. No assumed multi-frame wrap.
  return spiRead(0x78, frame, 7);
}
void beginReadOnly() {
  Serial.println("[modules] startup read-only; SPI2 mode3 8MHz CS10 MOSI11 SCK12 MISO13; I2C SDA16 SCL17 100kHz");
  spi_bus_config_t cfg = {};
  cfg.mosi_io_num = 11; cfg.miso_io_num = 13; cfg.sclk_io_num = 12;
  cfg.quadwp_io_num = -1; cfg.quadhd_io_num = -1; cfg.max_transfer_sz = 8;
  esp_err_t error = spi_bus_initialize(SPI2_HOST, &cfg, SPI_DMA_DISABLED);
  Serial.printf("[modules] SPI bus=%s\n", esp_err_to_name(error));
  if (error == ESP_OK) {
    spi_device_interface_config_t device = {};
    device.mode = 3; device.clock_speed_hz = 8000000; device.spics_io_num = 10; device.queue_size = 1;
    error = spi_bus_add_device(SPI2_HOST, &device, &sensor);
    Serial.printf("[modules] SPI device=%s\n", esp_err_to_name(error));
  }
  i2c_master_bus_config_t icfg = {};
  icfg.i2c_port = I2C_NUM_0; icfg.sda_io_num = GPIO_NUM_16; icfg.scl_io_num = GPIO_NUM_17;
  icfg.clk_source = I2C_CLK_SRC_DEFAULT; icfg.glitch_ignore_cnt = 7;
  icfg.flags.enable_internal_pullup = false; // Existing module pull-ups must go to 3V3.
  error = i2c_new_master_bus(&icfg, &bus);
  Serial.printf("[modules] I2C bus=%s external_3V3_pullups_required=1\n", esp_err_to_name(error));
  if (error == ESP_OK) discoverMonitor();
  snapshot();
}
bool command(const char* line) {
  if (strcmp(line, "m") == 0) { snapshot(); return true; }
  if (strcmp(line, "INIT_MODULES") == 0) {
    Serial.println("[modules] use explicit REC_START; no standalone initialization in recorder"); return true;
  }
  return false;
}
}
