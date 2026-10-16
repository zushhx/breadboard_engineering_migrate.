#include "vibration_trend.h"
#include "vibration_wave.h"
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
  WaveAccumulator wave; WaveBucket bucket;
  for (unsigned i = 1; i < kWaveBin; ++i) assert(!wave.add(dc, bucket));
  const int16_t hit[3] = {-32768, 32767, -123};
  assert(wave.add(hit, bucket)); assert(bucket.endSample == kWaveBin);
  assert(bucket.minimum[0] == -32768 && bucket.maximum[0] == 1234);
  assert(bucket.minimum[1] == -2345 && bucket.maximum[1] == 32767);
  assert(bucket.minimum[2] == -123 && bucket.maximum[2] == 32767);
  // Next interval must not inherit extrema. Incomplete tail stays unpublished.
  for (unsigned i = 1; i < kWaveBin; ++i) assert(!wave.add(dc, bucket));
  assert(wave.add(dc, bucket)); assert(bucket.endSample == 2 * kWaveBin);
  for (unsigned a = 0; a < 3; ++a) assert(bucket.minimum[a] == dc[a] && bucket.maximum[a] == dc[a]);
  WaveHistory wh; static WaveBucket wc[kWavePoints];
  assert(wh.copy(wc) == 0);
  for (unsigned i = 1; i <= kWavePoints + 9; ++i) { bucket.endSample = i; wh.push(bucket); }
  assert(wh.copy(wc) == kWavePoints);
  assert(wc[0].endSample == 10 && wc[kWavePoints-1].endSample == kWavePoints + 9);
  wh.reset(); assert(wh.copy(wc) == 0);
  wave.reset();
  for (unsigned i = 0; i < kWaveBin; ++i) wave.add(dc, bucket);
  assert(bucket.endSample == kWaveBin);
  puts("PASS: RMS/DC/extremes/windows/reset; wave signed extrema/DC/peak preservation/bounded ring/reset");
}
