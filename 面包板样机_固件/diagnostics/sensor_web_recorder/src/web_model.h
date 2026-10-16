#pragma once
#include <cstdint>
#include <cstring>
#include <cstdio>
#include "record_request.h"
namespace control {
constexpr unsigned kPreview = 272; // >= 10ms at nominal 26667Hz, including t=0.
enum class Phase { Idle, Queued, Preparing, Recording, Draining, Closing, Verifying, Passed, Rejected, Failed };
inline const char* phaseName(Phase p) {
  static const char* names[] = {"idle", "queued", "preparing", "recording", "draining", "closing", "verifying", "passed", "rejected", "failed"};
  return names[static_cast<unsigned>(p)];
}
inline bool busy(Phase p) { return p >= Phase::Queued && p <= Phase::Verifying; }
struct Snapshot {
  Phase phase = Phase::Idle;
  bool ready = false, connected = false, stop = false, inaValid = false, spaceValid = false;
  uint32_t serial = 0, seconds = 0, errors = 0, queue = 0, queueHigh = 0, fifoHigh = 0;
  uint32_t maxWriteUs = 0, maxSyncUs = 0, freeHeap = 0, largest = 0;
  uint64_t capacity = 0, written = 0, elapsedUs = 0, previewUs = 0, inaUs = 0, freeBytes = 0;
  uint32_t previewSeq = 0, previewCount = 0;
  int16_t xyz[kPreview][3] = {};
  int32_t busMv = 0, currentUa = 0, powerUw = 0;
  char run[9] = {}, detail[96] = "等待显式开始；INA精度未验收", sha[65] = {}, ip[16] = {};
};
// Owner holds the same lock for every access. No storage, peripheral or network calls here.
struct Model {
  Snapshot s;
  void storageSpace(bool valid, uint64_t bytes) {
    s.spaceValid = valid && bytes <= s.capacity;
    s.freeBytes = s.spaceValid ? bytes : 0;
  }
  bool submit(uint32_t serial, uint64_t capacity, const char* run, unsigned seconds) {
    if (!s.ready || busy(s.phase) || s.phase == Phase::Failed || serial != s.serial || capacity != s.capacity ||
        !recorder::runId(run) || seconds < 1 || seconds > 300) return false;
    if (!begin(run, seconds)) return false;
    s.phase = Phase::Queued; return true;
  }
  bool take(char* run, unsigned& seconds) {
    if (s.phase != Phase::Queued) return false;
    snprintf(run, 9, "%s", s.run); seconds = s.seconds; s.phase = Phase::Preparing; return true;
  }
  bool begin(const char* run, unsigned seconds) {
    if (!s.ready || s.phase == Phase::Failed) return false;
    if (busy(s.phase) && !(s.phase == Phase::Preparing && !strcmp(s.run, run))) return false;
    bool connected = s.connected, stop = s.phase == Phase::Preparing && s.stop;
    uint32_t serial = s.serial; uint64_t capacity = s.capacity;
    bool spaceValid = s.spaceValid; uint64_t freeBytes = s.freeBytes;
    char ip[16]; memcpy(ip, s.ip, sizeof(ip));
    s = {}; s.ready = true; s.connected = connected; s.serial = serial; s.capacity = capacity;
    s.spaceValid = spaceValid; s.freeBytes = freeBytes;
    s.stop = stop; s.phase = Phase::Preparing; s.seconds = seconds;
    memcpy(s.ip, ip, sizeof(ip)); snprintf(s.run, sizeof(s.run), "%s", run);
    return true;
  }
  void requestStop() {
    if (s.phase == Phase::Queued) { s.phase = Phase::Rejected; snprintf(s.detail, sizeof(s.detail), "开始请求已取消，未写卡"); }
    else if (busy(s.phase)) s.stop = true;
  }
};
}
