#pragma once
#include <cstdint>

// DS12569 Rev 8, tables 3 and 30. This product exposes exactly these three ranges.
namespace sensor_range {
constexpr bool valid(unsigned g) { return g == 2 || g == 8 || g == 16; }
constexpr uint32_t scaleMicroG(unsigned g) { return g == 2 ? 61 : g == 8 ? 244 : g == 16 ? 488 : 0; }
constexpr uint8_t ctrl1(unsigned g) { return g == 2 ? 0xA0 : g == 8 ? 0xAC : g == 16 ? 0xA4 : 0; }
constexpr bool validScale(uint32_t scale) { return scale == 61 || scale == 244 || scale == 488; }
constexpr unsigned fromCtrl1(uint8_t value) { return value == 0xA0 ? 2 : value == 0xAC ? 8 : value == 0xA4 ? 16 : 0; }
}
