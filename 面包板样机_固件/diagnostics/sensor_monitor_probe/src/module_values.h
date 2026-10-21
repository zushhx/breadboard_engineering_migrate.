#pragma once
#include <cstdint>
namespace modules {
constexpr uint16_t kInaConfig = 0x4527;
constexpr uint16_t kInaCal = 2048;
inline bool inaIdentity(uint16_t maker, uint16_t die) {
  return maker == 0x5449 && (die == 0x2260 || die == 0x2261);
}
inline int32_t signed16(uint16_t word) {
  return word & 0x8000 ? static_cast<int32_t>(word) - 65536 : word;
}
inline uint16_t be16(const uint8_t* bytes) {
  return static_cast<uint16_t>((static_cast<uint16_t>(bytes[0]) << 8) | bytes[1]);
}
inline int32_t leSigned16(const uint8_t* bytes) {
  return signed16(static_cast<uint16_t>(bytes[0] | (static_cast<uint16_t>(bytes[1]) << 8)));
}
struct InaRegisters {
  uint16_t config, calibration, mask, shunt, bus, current, power;
};
// CVRF is read-only here: no hidden calibration repair. OVF invalidates the result.
inline bool inaValid(bool transport, bool identified, const InaRegisters& r) {
  return transport && identified && r.config == kInaConfig && r.calibration == kInaCal &&
         (r.mask & 0x0008) && !(r.mask & 0x0004);
}
inline double busVolts(const InaRegisters& r) { return r.bus * 0.00125; }
inline double currentMilliAmps(const InaRegisters& r) { return signed16(r.current) * 0.025; }
inline double shuntMicroVolts(const InaRegisters& r) { return signed16(r.shunt) * 2.5; }
inline double powerMilliWatts(const InaRegisters& r) { return r.power * 0.625; }
}
