#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>
namespace rate240stream {
// Caller serializes push/pop/reset. A rejected push changes no queued byte.
class ByteFifo {
  uint8_t* buffer = nullptr;
  size_t capacity = 0, head = 0, tail = 0, count = 0;
public:
  void reset(uint8_t* storage, size_t bytes) {
    buffer = storage; capacity = bytes; head = tail = count = 0;
  }
  size_t size() const { return count; }
  bool push(const void* data, size_t bytes) {
    if (!buffer || !capacity || bytes > capacity - count) return false;
    const size_t first = bytes < capacity - head ? bytes : capacity - head;
    memcpy(buffer + head, data, first);
    memcpy(buffer, static_cast<const uint8_t*>(data) + first, bytes - first);
    head = (head + bytes) % capacity; count += bytes; return true;
  }
  size_t pop(void* data, size_t maximum) {
    const size_t bytes = count < maximum ? count : maximum;
    if (!bytes) return 0;
    const size_t first = bytes < capacity - tail ? bytes : capacity - tail;
    memcpy(data, buffer + tail, first);
    memcpy(static_cast<uint8_t*>(data) + first, buffer, bytes - first);
    tail = (tail + bytes) % capacity; count -= bytes; return bytes;
  }
};
}
