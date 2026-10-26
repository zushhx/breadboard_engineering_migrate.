#pragma once
#include <cstddef>
#include <cstdint>
namespace rate190 {
bool start(const char* key);
// Hooks run only on the recording owner. Scope covers every early return.
struct Recording {
  Recording(const char* run, unsigned seconds, const char* format = "record");
  ~Recording();
  void start(uint64_t timeUs);
  void stop();
  void finish(bool ok, const char* readbackSha);
  unsigned generation = 0;
};
void tee(const void* data, size_t bytes);
}
