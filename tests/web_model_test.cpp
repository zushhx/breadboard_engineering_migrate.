#include "web_model.h"
#include "storage_space.h"
#include <cassert>
#include <cstdio>
#include <initializer_list>
int main() {
  // Exact ST FS encodings and scales, including unsupported ±4 g product policy.
  static_assert(sensor_range::ctrl1(2) == 0xA0 && sensor_range::scaleMicroG(2) == 61, "2g mapping");
  static_assert(sensor_range::ctrl1(8) == 0xAC && sensor_range::scaleMicroG(8) == 244, "8g mapping");
  static_assert(sensor_range::ctrl1(16) == 0xA4 && sensor_range::scaleMicroG(16) == 488, "16g mapping");
  assert(!sensor_range::valid(4) && !sensor_range::validScale(122));
  assert(sensor_range::fromCtrl1(0xA8) == 0 && sensor_range::fromCtrl1(0) == 0);
  recorder::Request request{};
  assert(recorder::parseRequest("REC_START 10044BA5 7948206080 DEADBEEF 300", request) && request.rangeG == 2);
  for (unsigned range : {2u, 8u, 16u}) {
    char command[100];
    snprintf(command, sizeof(command), "REC_START 10044BA5 7948206080 DEADBEEF 300 %u", range);
    assert(recorder::parseRequest(command, request) && request.rangeG == range && request.seconds == 300);
    control::Model ranged;
    ranged.s.ready = true; ranged.s.serial = 1; ranged.s.capacity = 100;
    assert(ranged.submit(1, 100, "AABBCCDD", 30, range));
    assert(ranged.s.rangeG == range);
    // Queued/preparing/finalizing requests cannot change a run's fixed scale.
    char nextRun[9]; unsigned duration = 0, selected = 0;
    assert(ranged.take(nextRun, duration, &selected) && selected == range && duration == 30);
    assert(!ranged.begin("AABBCCDD", 30, range == 2 ? 8 : 2));
    assert(!ranged.begin("AABBCCDD", 31, range));
    ranged.requestStop();
    assert(ranged.begin("AABBCCDD", 30, selected) && ranged.s.stop && ranged.s.rangeG == range);
    for (auto p : {control::Phase::Queued, control::Phase::Preparing, control::Phase::Recording,
                   control::Phase::Draining, control::Phase::Closing, control::Phase::Verifying}) {
      ranged.s.phase = p;
      assert(!ranged.submit(1, 100, "00000002", 30, 16));
      assert(ranged.s.rangeG == range);
    }
    ranged.s.phase = control::Phase::Passed;
    assert(!ranged.submit(1, 100, "00000002", 30, 4) && ranged.s.rangeG == range);
    assert(ranged.submit(1, 100, "00000002", 30)); // Legacy request explicitly returns to default 2g.
    assert(ranged.s.rangeG == 2 && !ranged.s.stop);
  }
  for (const char* bad : {"REC_START 10044BA5 7948206080 DEADBEEF 30 4",
      "REC_START 10044BA5 7948206080 DEADBEEF 30 0", "REC_START 10044BA5 7948206080 DEADBEEF 30 32",
      "REC_START 10044BA5 7948206080 DEADBEEF 30 -2", "REC_START 10044BA5 7948206080 DEADBEEF 30 8.0",
      "REC_START 10044BA5 7948206080 DEADBEEF 30 16 trailing", "REC_START 10044BA5 7948206080 DEADBEEF 30 16000000"})
    assert(!recorder::parseRequest(bad, request));
  assert(recorder::parseRequest("REC_VERIFY DEADBEEF", request) && !request.write);
  control::Model m;
  assert(!m.submit(0, 0, "AABBCCDD", 10));
  m.s.ready = true; m.s.serial = 0x10044BA5; m.s.capacity = 7948206080ULL;
  const auto serial = m.s.serial; const auto capacity = m.s.capacity;
  static_assert(control::kPreview >= 268, "10ms requires at least 268 samples");
  static_assert(sizeof(control::Snapshot) < 3072, "Keep HTTP stack snapshot bounded");
  uint64_t available = 0;
  assert(control::freeBytesFromClusters(1000000, 8, capacity, available));
  assert(available == 4096000000ULL); // >32-bit signed and near 32-bit unsigned limit.
  assert(control::freeBytesFromClusters(1500000, 8, capacity, available));
  assert(available == 6144000000ULL); // Must not wrap 32-bit arithmetic.
  assert(control::freeBytesFromClusters(0, 8, capacity, available) && available == 0);
  assert(!control::freeBytesFromClusters(1, 0, capacity, available));
  assert(!control::freeBytesFromClusters(UINT32_MAX, UINT32_MAX, capacity, available));
  m.storageSpace(true, 6144000000ULL);assert(m.s.spaceValid);
  assert(!m.submit(serial+1, capacity, "AABBCCDD", 10));
  assert(!m.submit(serial, capacity-1, "AABBCCDD", 10));
  assert(!m.submit(serial, capacity, "aabbccdd", 10));
  assert(!m.submit(serial, capacity, "AABBCCDD", 0));
  assert(!m.submit(serial, capacity, "AABBCCDD", 301));
  assert(m.submit(serial, capacity, "AABBCCDD", 300));
  assert(m.s.spaceValid && m.s.freeBytes == 6144000000ULL);
  m.storageSpace(true, 0);assert(m.s.spaceValid && m.s.freeBytes == 0);
  m.storageSpace(true, capacity+1);assert(!m.s.spaceValid);
  m.storageSpace(false, 1);assert(!m.s.spaceValid && m.s.freeBytes == 0);
  assert(!m.submit(serial, capacity, "00000001", 10)); // single slot and duplicate suppression
  assert(!m.begin("00000001", 10)); // UART cannot overtake a queued request
  m.requestStop(); assert(m.s.phase == control::Phase::Rejected);
  char run[9]; unsigned seconds;
  assert(!m.take(run, seconds)); // cancel before any owner/storage activity
  assert(m.submit(serial, capacity, "00000001", 10));
  assert(m.take(run, seconds)); assert(seconds == 10); assert(!strcmp(run, "00000001"));
  assert(!m.take(run, seconds)); assert(!m.begin("00000002", 10));
  m.requestStop(); assert(m.begin("00000001", 10)); assert(m.s.stop); // stop during preparation retained
  m.s.phase = control::Phase::Recording;
  m.s.connected = false; // hotspot lost: state/duration/owner unchanged
  assert(m.s.phase == control::Phase::Recording && m.s.seconds == 10);
  assert(!m.submit(serial, capacity, "00000002", 10));
  for (auto phase : {control::Phase::Draining, control::Phase::Closing, control::Phase::Verifying}) {
    m.s.phase = phase; assert(!m.submit(serial, capacity, "00000002", 10));
    assert(control::busy(phase));
  }
  m.s.phase = control::Phase::Passed;
  m.s.written = 99; m.s.previewCount = 5; m.s.inaValid = true;
  assert(m.submit(serial, capacity, "00000002", 1));
  assert(m.s.written == 0 && m.s.previewCount == 0 && !m.s.inaValid);
  assert(m.take(run, seconds));
  assert(m.begin("00000002", 1)); assert(!m.s.stop); // old stop never leaks into UART/new run
  m.s.phase = control::Phase::Failed;
  assert(!m.submit(serial, capacity, "00000003", 10)); assert(!m.begin("00000003", 10));
  m.requestStop(); assert(m.s.phase == control::Phase::Failed);
  puts("PASS: three FS mappings/legacy and ranged requests/invalid ranges/run-scale lock/identity/single-slot/cancel/stop/owner/network-loss/finalization/failure lock");
}
