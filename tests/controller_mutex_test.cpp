#include "mutex_lock.h"
#include <cassert>
#include <cstdio>
int main(){
  StaticSemaphore_t network,files;
  {devicev2::MutexLock outer(&files);
    {devicev2::MutexLock inner(&network);assert(!files.try_lock());assert(!network.try_lock());}
    assert(!files.try_lock());assert(network.try_lock());network.unlock();
  }
  assert(files.try_lock());files.unlock();
  puts("PASS: nested network/file guards release their own handles");
}
