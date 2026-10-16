#pragma once
#include <cstdint>

namespace control {
constexpr uint32_t kWaveBin = 533; // ~20ms; all samples contribute to extrema.
constexpr unsigned kWavePoints = 512;
struct WaveBucket {
  uint32_t endSample = 0;
  int16_t minimum[3] = {}, maximum[3] = {};
};
static_assert(sizeof(WaveBucket) == 16, "Bounded wave history storage");
class WaveAccumulator {
  uint32_t total = 0, count = 0;
  int16_t minimum[3] = {}, maximum[3] = {};
 public:
  void reset() { total = count = 0; }
  bool add(const int16_t (&xyz)[3], WaveBucket& out) {
    for (unsigned a = 0; a < 3; ++a) {
      if (!count || xyz[a] < minimum[a]) minimum[a] = xyz[a];
      if (!count || xyz[a] > maximum[a]) maximum[a] = xyz[a];
    }
    ++total;
    if (++count != kWaveBin) return false;
    out.endSample = total;
    for (unsigned a = 0; a < 3; ++a) {
      out.minimum[a] = minimum[a]; out.maximum[a] = maximum[a];
    }
    count = 0;
    return true;
  }
};
// All access to the history is protected by the shared control lock.
class WaveHistory {
  WaveBucket data[kWavePoints] = {};
  unsigned next = 0, size = 0;
 public:
  void reset() { next = size = 0; }
  void push(const WaveBucket& point) {
    data[next] = point; next = (next + 1) % kWavePoints;
    if (size < kWavePoints) ++size;
  }
  unsigned copy(WaveBucket* out) const {
    const unsigned first = (next + kWavePoints - size) % kWavePoints;
    for (unsigned i = 0; i < size; ++i) out[i] = data[(first + i) % kWavePoints];
    return size;
  }
};
}
