#pragma once
#include "web_model.h"
#include "vibration_trend.h"
#include <cinttypes>
#include <cstdio>

namespace control {
// One shared, mutex-protected buffer serves status and trend, including USB.
// Five uint32 values require at most 57 characters per trend point.
constexpr size_t kTelemetryJsonCapacity = 9216;
static_assert(kPreview * 24 + 1500 < kTelemetryJsonCapacity, "Status JSON bound");
static_assert(kTrendPoints * 57 + 256 < kTelemetryJsonCapacity, "Trend JSON bound");
class TelemetryWriter {
  char* data;
  size_t capacity, used = 0;
  bool valid;
 public:
  TelemetryWriter(char* target, size_t size):data(target),capacity(size),valid(target && size) {}
  template<class... Args> void add(const char* format, Args... args) {
    if (!valid) return;
    int n = snprintf(data + used, capacity - used, format, args...);
    if (n < 0 || static_cast<size_t>(n) >= capacity - used) {valid = false;return;}
    used += static_cast<size_t>(n);
  }
  size_t size() const {return valid ? used : 0;}
};
inline size_t renderStatusJson(char* out, size_t capacity, const char* key,
                              const Snapshot& s, const char* owner, const char* waiting, bool files) {
  if (s.previewCount > kPreview) return 0;
  TelemetryWriter w(out,capacity);
  w.add("{\"boot\":\"%s\",\"phase\":\"%s\",\"ready\":%s,\"connected\":%s,\"ip\":\"%s\","
      "\"card_serial\":\"%08" PRIX32 "\",\"card_bytes\":\"%" PRIu64 "\",\"space_valid\":%s,\"free_bytes\":\"%" PRIu64 "\",\"run\":\"%s\",\"seconds\":%u,\"range_g\":%u,\"scale_micro_g\":%u,"
      "\"written\":%" PRIu64 ",\"elapsed_us\":%" PRIu64 ",\"errors\":%u,\"queue\":%u,"
      "\"fifo_high\":%u,\"queue_high\":%u,\"write_us\":%u,\"sync_us\":%u,\"free_heap\":%u,"
      "\"ina_valid\":%s,\"ina_us\":%" PRIu64 ",\"bus_mv\":%" PRId32 ",\"current_ua\":%" PRId32 ",\"power_uw\":%" PRId32 ","
      "\"detail\":\"%s\",\"sha256\":\"%s\",\"record_name\":\"%s\",\"record_path\":\"%s\",\"preview_seq\":%u,\"preview_us\":%" PRIu64 ",\"preview\":[",
      key,phaseName(s.phase),s.ready?"true":"false",s.connected?"true":"false",s.ip,
      s.serial,s.capacity,s.spaceValid?"true":"false",s.freeBytes,s.run,unsigned(s.seconds),s.rangeG,unsigned(sensor_range::scaleMicroG(s.rangeG)),
      s.written,s.elapsedUs,unsigned(s.errors),unsigned(s.queue),unsigned(s.fifoHigh),unsigned(s.queueHigh),
      unsigned(s.maxWriteUs),unsigned(s.maxSyncUs),unsigned(s.freeHeap),s.inaValid?"true":"false",s.inaUs,
      s.busMv,s.currentUa,s.powerUw,s.detail,s.sha,s.recordName,s.recordPath,unsigned(s.previewSeq),s.previewUs);
  for (unsigned i=0;i<s.previewCount;++i)
    w.add("%s[%d,%d,%d]",i?",":"",s.xyz[i][0],s.xyz[i][1],s.xyz[i][2]);
  w.add("],\"owner\":\"%s\",\"pending_owner\":\"%s\",\"files_busy\":%s}",owner,waiting,files?"true":"false");
  return w.size();
}
inline size_t renderTrendJson(char* out, size_t capacity, const char* key,
                             const char* run, const TrendPoint* points, unsigned count, unsigned rangeG = 2) {
  if (count>kTrendPoints || (count && !points)) return 0;
  TelemetryWriter w(out,capacity);
  w.add("{\"boot\":\"%s\",\"run\":\"%s\",\"range_g\":%u,\"scale_micro_g\":%u,\"sample_rate\":26667,\"window_samples\":5333,\"points\":[",key,run,rangeG,unsigned(sensor_range::scaleMicroG(rangeG)));
  for(unsigned i=0;i<count;++i) {
    const auto& p=points[i];
    w.add("%s[%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 "]",i?",":"",p.endSample,
          p.rmsUg[0],p.rmsUg[1],p.rmsUg[2],p.nearFullScale);
  }
  w.add("%s","]}");return w.size();
}
}
