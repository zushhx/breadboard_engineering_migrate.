#pragma once
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <cerrno>
#include <cstdint>
#include "sensor_range.h"
namespace recorder {
struct Request { bool write; char run[9]; uint32_t serial; uint64_t capacity; unsigned seconds; unsigned rangeG = 2; };
inline bool runId(const char* run) {
  if (strlen(run) != 8) return false;
  for (unsigned i = 0; i < 8; ++i) if (!((run[i] >= '0' && run[i] <= '9') || (run[i] >= 'A' && run[i] <= 'F'))) return false;
  return true;
}
inline bool parseRequest(const char* line, Request& r) {
  r = {}; char serial[9] = {}, capacity[24] = {}, duration[8] = {}, range[8] = {}; int end = 0;
  int fields = 0;
  if (strncmp(line, "REC_START ", 10) == 0) {
    fields = sscanf(line, "REC_START %8s %23s %8s %7s %7s%n", serial, capacity, r.run, duration, range, &end);
    if (fields == 4) {
      end = 0;
      fields = sscanf(line, "REC_START %8s %23s %8s %7s%n", serial, capacity, r.run, duration, &end);
    }
  }
  if (strncmp(line, "REC_START ", 10) == 0 &&
      (fields == 4 || fields == 5) && !line[end]) {
    bool digits = capacity[0] && duration[0];
    for (const char* p = capacity; *p; ++p) digits = digits && *p >= '0' && *p <= '9';
    for (const char* p = duration; *p; ++p) digits = digits && *p >= '0' && *p <= '9';
    for (const char* p = range; *p; ++p) digits = digits && *p >= '0' && *p <= '9';
    errno = 0; r.capacity = strtoull(capacity, nullptr, 10); bool fits = errno != ERANGE;
    r.seconds = strtoul(duration, nullptr, 10); r.serial = strtoul(serial, nullptr, 16); r.write = true;
    r.rangeG = fields == 5 ? strtoul(range, nullptr, 10) : 2;
    return fits && digits && runId(serial) && runId(r.run) && r.seconds >= 1 && r.seconds <= 300 && sensor_range::valid(r.rangeG);
  }
  return strncmp(line, "REC_VERIFY ", 11) == 0 &&
      sscanf(line, "REC_VERIFY %8s%n", r.run, &end) == 1 && !line[end] && runId(r.run);
}
}
