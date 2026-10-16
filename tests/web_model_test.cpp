#include "web_model.h"
#include <cassert>
#include <cstdio>
#include <initializer_list>
int main() {
  control::Model m;
  assert(!m.submit(0, 0, "AABBCCDD", 10));
  m.s.ready = true; m.s.serial = 0x10044BA5; m.s.capacity = 7948206080ULL;
  const auto serial = m.s.serial; const auto capacity = m.s.capacity;
  assert(!m.submit(serial+1, capacity, "AABBCCDD", 10));
  assert(!m.submit(serial, capacity-1, "AABBCCDD", 10));
  assert(!m.submit(serial, capacity, "aabbccdd", 10));
  assert(!m.submit(serial, capacity, "AABBCCDD", 0));
  assert(!m.submit(serial, capacity, "AABBCCDD", 301));
  assert(m.submit(serial, capacity, "AABBCCDD", 300));
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
  puts("PASS: identity/range/single-slot/cancel/stop/owner/network-loss/finalization/failure lock");
}
