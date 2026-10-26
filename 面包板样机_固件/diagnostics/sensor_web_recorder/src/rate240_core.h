#pragma once
#include <cstdint>
namespace rate240core {
constexpr uint64_t rate = 240000;
constexpr unsigned blockBytes = 2400;
// Same payload headroom as the original 40 x 448 x 7 acquisition pool.
constexpr uint64_t backlogLimit = 40ull * 448 * 7;
inline uint64_t due(uint64_t elapsedUs, uint64_t total) {
  const uint64_t count = (elapsedUs / 10000) * blockBytes;
  return count < total ? count : total;
}
inline uint8_t pattern(uint64_t offset) { return static_cast<uint8_t>(offset * 31 + 17); }
inline bool cadenceAccepted(uint64_t bytes, unsigned seconds, uint64_t elapsedUs) {
  const uint64_t expected = static_cast<uint64_t>(seconds) * rate;
  const uint64_t nominal = static_cast<uint64_t>(seconds) * 1000000;
  return seconds >= 1 && seconds <= 300 && bytes == expected &&
      elapsedUs >= nominal && elapsedUs <= nominal + nominal / 100;
}
}
