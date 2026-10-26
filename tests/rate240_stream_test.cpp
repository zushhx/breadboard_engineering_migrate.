#include "rate240_stream_core.h"
#include <cassert>
#include <cstdio>
#include <deque>
#include <vector>
using rate240stream::ByteFifo;
int main() {
  uint8_t storage[25600], payload[3136], header[48], output[3200];
  for (size_t i=0;i<sizeof(payload);++i) payload[i]=uint8_t(i*31+17);
  for (size_t i=0;i<sizeof(header);++i) header[i]=uint8_t(i);
  ByteFifo fifo; assert(!fifo.push(header,sizeof(header)));
  fifo.reset(storage,sizeof(storage));
  // Sixteen alternating header/payload calls fit eight complete blocks.
  for(unsigned block=0;block<8;++block) {
    assert(fifo.push(header,sizeof(header)));assert(fifo.push(payload,sizeof(payload)));
  }
  assert(fifo.size()==25472);
  assert(!fifo.push(payload,sizeof(payload)));assert(fifo.size()==25472);
  // Reject oversized writes atomically; all earlier bytes remain in order.
  std::vector<uint8_t> expected;
  for(unsigned block=0;block<8;++block) {
    expected.insert(expected.end(),header,header+sizeof(header));
    expected.insert(expected.end(),payload,payload+sizeof(payload));
  }
  size_t offset=0;
  while(size_t got=fifo.pop(output,sizeof(output))) {
    for(size_t i=0;i<got;++i)assert(output[i]==expected[offset+i]);offset+=got;
  }
  assert(offset==expected.size());
  // Exercise wrap and producer/consumer fragmentation against a reference FIFO.
  std::deque<uint8_t> reference;
  uint32_t random=12345;
  for(unsigned turn=0;turn<10000;++turn) {
    random=random*1664525u+1013904223u;
    size_t bytes=1+(random%sizeof(payload));
    if(random&0x80000000u) {
      bool accepted=fifo.push(payload,bytes);
      assert(accepted==(reference.size()+bytes<=sizeof(storage)));
      if(accepted)reference.insert(reference.end(),payload,payload+bytes);
    } else {
      size_t want=reference.size()<bytes?reference.size():bytes;
      assert(fifo.pop(output,bytes)==want);
      for(size_t i=0;i<want;++i){assert(output[i]==reference.front());reference.pop_front();}
    }
    assert(fifo.size()==reference.size());
  }
  // A cancelled/disconnected session reset exposes no bytes to a later session.
  fifo.reset(storage,sizeof(storage));assert(fifo.size()==0);
  assert(fifo.pop(output,sizeof(output))==0);
  assert(fifo.push(header,sizeof(header)));assert(fifo.pop(output,sizeof(output))==sizeof(header));
  for(size_t i=0;i<sizeof(header);++i)assert(output[i]==header[i]);
  puts("PASS: actual byte FIFO full/rejection/wrap/fragmentation/cancel-reset boundaries");
}
