#include "rate240_core.h"
#include <cassert>
#include <cstdio>
int main() {
  using namespace rate240core;
  // A sustained source reaches exactly 72 MB in 300 seconds; it never emits
  // future samples during startup, nor extra data after the bounded duration.
  const uint64_t total=300*rate;
  assert(due(0,total)==0);
  assert(due(9999,total)==0);
  assert(due(10000,total)==2400);
  assert(due(300000000,total)==72000000);
  assert(due(400000000,total)==72000000);
  // A 100ms card pause fits the original logical backlog budget; a 600ms
  // pause does not. This tests pacing/backpressure bounds, not physical FIFO.
  assert(due(100000,total)<backlogLimit);
  assert(due(600000,total)>backlogLimit);
  assert(cadenceAccepted(total,300,300001000));
  assert(!cadenceAccepted(total,300,299000000));
  assert(!cadenceAccepted(total,300,304000000));
  assert(!cadenceAccepted(total-2400,300,300001000));
  assert(!cadenceAccepted(0,0,0));
  assert(pattern(0)==17 && pattern(1)==48);
  assert(pattern(255)==242 && pattern(256)==17);
  assert(pattern(71999999)==pattern(71999999%256));
  std::puts("PASS: rate240 duration, payload, backlog and pattern boundaries");
}
