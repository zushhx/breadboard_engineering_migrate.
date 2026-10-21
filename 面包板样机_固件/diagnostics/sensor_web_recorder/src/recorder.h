#pragma once
#include <cstdint>
#include <cstddef>
namespace recorder {
struct Store {
  const char* drive;
  uint32_t serial;
  uint64_t capacity;
  void (*gate)(bool);
};
void command(const char* line, const Store& store);
bool reservePool();
void* idleWorkspace(size_t bytes);
}
