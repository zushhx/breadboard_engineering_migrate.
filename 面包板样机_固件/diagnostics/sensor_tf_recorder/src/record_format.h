#pragma once
#include <cstdint>
#include <cstddef>
namespace recorder {
constexpr unsigned kFrames = 448, kSlots = 40, kPayload = kFrames * 7;
// All integers little endian; packed wire structures are used only on ESP32/little-endian hosts.
#pragma pack(push, 1)
struct Header {
  char magic[8]; // I3REC001
  uint32_t bytes, version, cardSerial;
  uint64_t cardBytes;
  uint32_t seconds, odr, spiHz, scaleMicroG, flags;
  char source[41], inputs[65], mac[18], wiring[32];
  uint32_t shuntMicroOhm, currentLsbNanoA, powerLsbNanoW, inaAddress;
  uint32_t crc;
};
struct BlockHeader {
  uint32_t magic, type, sequence, bytes, count;
  uint64_t firstUs, lastUs, firstSample;
  uint32_t crc;
};
struct InaSample {
  uint64_t timeUs;
  uint16_t config, cal, mask, shunt, bus, current, power, valid;
};
struct Footer {
  uint64_t samples, startUs, stopUs;
  uint32_t inaSamples, inaInvalid, errors, saturation, fifoHigh, queueHigh;
  uint32_t maxWriteUs, maxSyncUs, minHeap, sensorStack, inaStack;
};
#pragma pack(pop)
static_assert(sizeof(Header) == 224, "Header wire layout");
static_assert(sizeof(BlockHeader) == 48, "Block wire layout");
static_assert(sizeof(InaSample) == 24, "INA wire layout");
static_assert(sizeof(Footer) == 68, "Footer wire layout");
inline uint32_t crcAdd(uint32_t crc, const void* data, size_t n) {
  const auto* p = static_cast<const uint8_t*>(data);
  while (n--) {
    crc ^= *p++;
    for (int b = 0; b < 8; ++b) crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1)));
  }
  return crc;
}
inline uint32_t crc32(const void* data, size_t n) { return ~crcAdd(0xFFFFFFFFu, data, n); }
inline uint32_t blockCrc(const BlockHeader& h, const void* payload) {
  return ~crcAdd(crcAdd(0xFFFFFFFFu, &h, offsetof(BlockHeader, crc)), payload, h.bytes);
}
enum Error : uint32_t { Spi = 1, Overflow = 2, Tag = 4, Queue = 8, Monitor = 16,
  Write = 32, Config = 64, Stop = 128, Rate = 256, Memory = 512 };
}
