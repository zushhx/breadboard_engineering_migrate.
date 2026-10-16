#include "vibration_trend.h"
#include <cassert>
#include <cstdio>
int main() {
  using namespace control;
  TrendAccumulator accumulator; TrendPoint point;
  const int16_t dc[3] = {1234, -2345, 32767};
  for (unsigned i = 1; i < kTrendWindow; ++i) assert(!accumulator.add(dc, point));
  assert(accumulator.add(dc, point)); assert(point.endSample == kTrendWindow);
  for (unsigned a = 0; a < 3; ++a) assert(point.rmsUg[a] == 0);
  assert(point.nearFullScale == kTrendWindow);
  accumulator.reset();
  // Odd window: alternating signal + DC offset; integer analytical variance.
  for (unsigned i = 0; i < kTrendWindow; ++i) {
    const int16_t sign = i % 2 ? -1000 : 1000;
    const int16_t sample[3] = {sign, static_cast<int16_t>(sign + 10000), 0};
    assert(accumulator.add(sample, point) == (i + 1 == kTrendWindow));
  }
  assert(point.rmsUg[0] == 61000 && point.rmsUg[1] == 61000 && point.rmsUg[2] == 0);
  assert(point.nearFullScale == 0);
  // Cross-call windows and total sample numbering: incomplete tail is never published.
  for (unsigned i = 0; i < kTrendWindow - 1; ++i) assert(!accumulator.add(dc, point));
  assert(accumulator.add(dc, point)); assert(point.endSample == 2 * kTrendWindow);
  accumulator.reset();
  for (unsigned i = 0; i < kTrendWindow; ++i) {
    const int16_t extreme[3] = {static_cast<int16_t>(i % 2 ? -32768 : 32767), 32767, -32768};
    accumulator.add(extreme, point);
  }
  assert(point.rmsUg[0] > 1998000 && point.rmsUg[0] < 2000000);
  assert(point.rmsUg[1] == 0 && point.rmsUg[2] == 0);
  assert(point.nearFullScale == 3 * kTrendWindow);
  TrendHistory history; static TrendPoint copy[kTrendPoints];
  assert(history.copy(copy) == 0);
  for (unsigned i = 1; i <= kTrendPoints + 17; ++i) { point.endSample = i; history.push(point); }
  assert(history.copy(copy) == kTrendPoints);
  assert(copy[0].endSample == 18 && copy[kTrendPoints-1].endSample == kTrendPoints+17);
  history.reset(); assert(history.copy(copy) == 0);
  accumulator.reset();
  assert(!accumulator.add(dc, point));
  puts("PASS: DC/known RMS/extremes/windows/ring/reset");
}
