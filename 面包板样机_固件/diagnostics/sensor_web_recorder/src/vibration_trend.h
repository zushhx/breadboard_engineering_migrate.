#pragma once
#include <cmath>
#include <cstdint>

namespace control {
constexpr uint32_t kNominalRate = 26667;
constexpr uint32_t kTrendWindow = 5333; // ~200ms of consecutive samples, not wall-clock buckets.
constexpr unsigned kTrendPoints = 150;
struct TrendPoint {
  uint32_t endSample = 0;
  uint32_t rmsUg[3] = {}; // 1 ug = 0.001 mg. Rounded only after complete-window calculation.
  uint32_t nearFullScale = 0; // Number of axis values at the preexisting +/-32760 threshold.
};

// Recording owner only. Integer accumulation covers every input sample before preview throttling.
class TrendAccumulator {
  uint32_t total = 0, count = 0, clipped = 0;
  int64_t sum[3] = {}, square[3] = {};
 public:
  void reset() { total = count = clipped = 0; for (unsigned a = 0; a < 3; ++a) sum[a] = square[a] = 0; }
  bool add(const int16_t (&xyz)[3], TrendPoint& point) {
    for (unsigned a = 0; a < 3; ++a) {
      const int64_t value = xyz[a];
      sum[a] += value; square[a] += value * value;
      if (value <= -32760 || value >= 32760) ++clipped;
    }
    ++total;
    if (++count != kTrendWindow) return false;
    point.endSample = total; point.nearFullScale = clipped;
    for (unsigned a = 0; a < 3; ++a) {
      const double mean = static_cast<double>(sum[a]) / count;
      const double variance = static_cast<double>(square[a]) / count - mean * mean;
      point.rmsUg[a] = static_cast<uint32_t>(std::sqrt(variance > 0 ? variance : 0) * 61.0 + 0.5);
      sum[a] = square[a] = 0;
    }
    count = clipped = 0;
    return true;
  }
};

// Owner must hold the shared lock for publish/reset/copy; no allocation or I/O here.
class TrendHistory {
  TrendPoint data[kTrendPoints] = {};
  unsigned next = 0, size = 0;
 public:
  void reset() { next = size = 0; }
  void push(const TrendPoint& point) {
    data[next] = point; next = (next + 1) % kTrendPoints;
    if (size < kTrendPoints) ++size;
  }
  unsigned copy(TrendPoint* out) const {
    const unsigned first = (next + kTrendPoints - size) % kTrendPoints;
    for (unsigned i = 0; i < size; ++i) out[i] = data[(first + i) % kTrendPoints];
    return size;
  }
};
}
