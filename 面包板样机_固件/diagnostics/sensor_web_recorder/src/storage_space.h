#pragma once
#include <cstdint>
namespace control {
// Fixed sector size of this recorder. Validate against the physical card before multiplying.
inline bool freeBytesFromClusters(uint32_t clusters, uint32_t sectorsPerCluster,
                                  uint64_t capacity, uint64_t& bytes) {
  bytes = 0;
  if (!sectorsPerCluster) return false;
  const uint64_t clusterBytes = static_cast<uint64_t>(sectorsPerCluster) * 512;
  if (clusters > capacity / clusterBytes) return false;
  bytes = static_cast<uint64_t>(clusters) * clusterBytes;
  return true;
}
}
