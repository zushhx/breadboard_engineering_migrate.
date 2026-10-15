#include "module_values.h"
#include <cassert>
#include <cmath>
#include <cstdio>
using namespace modules;
int main() {
  assert(inaIdentity(0x5449, 0x2260));
  assert(inaIdentity(0x5449, 0x2261));
  assert(!inaIdentity(0xFFFF, 0xFFFF));
  assert(!inaIdentity(0x5449, 0x2262));
  InaRegisters r = {0x4527, 2048, 0x0008, 0xFFF0, 2640, 0xFFF0, 32};
  assert(inaValid(true, true, r));
  assert(std::abs(busVolts(r) - 3.3) < 1e-9);
  assert(std::abs(currentMilliAmps(r) + 0.4) < 1e-9);
  assert(std::abs(shuntMicroVolts(r) + 40.0) < 1e-9);
  assert(std::abs(powerMilliWatts(r) - 20.0) < 1e-9);
  assert(!inaValid(false, true, r));
  assert(!inaValid(true, false, r));
  r.calibration = 0; r.current = 0; r.power = 0;
  assert(!inaValid(true, true, r)); // Power-cycle zeros must never become valid zero current.
  r.calibration = 2048; r.mask = 0x000C;
  assert(!inaValid(true, true, r)); // Arithmetic overflow even if conversion ready.
  r.mask = 0;
  assert(!inaValid(true, true, r)); // Conversion not ready.
  r.mask = 8; r.config = 0x4127;
  assert(!inaValid(true, true, r)); // Wrong averaging or reset configuration.
  const uint8_t be[] = {0x80, 0x01}, le[] = {0x01, 0x80};
  assert(be16(be) == 0x8001);
  assert(leSigned16(le) == -32767);
  assert(signed16(0x8000) == -32768);
  assert(signed16(0x7FFF) == 32767);
  puts("PASS: module identity, signed/endian conversion, calibration loss, transport, overflow and readiness guards");
}
