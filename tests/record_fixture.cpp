#include "../面包板样机_固件/diagnostics/sensor_tf_recorder/src/record_format.h"
#include "../面包板样机_固件/diagnostics/sensor_tf_recorder/src/record_request.h"
#include <cstdio>
#include <cstring>
#include <initializer_list>
using namespace recorder;
static void block(FILE* f, unsigned kind, unsigned seq, unsigned count, unsigned sample,
                  uint64_t first, uint64_t last, const void* data, unsigned n) {
  BlockHeader h{0x314B4C42, kind, seq, n, count, first, last, sample, 0}; h.crc = blockCrc(h, data);
  fwrite(&h, sizeof(h), 1, f); fwrite(data, n, 1, f);
}
int main(int argc, char** argv) {
  if (argc != 2) return 2;
  Request request{};
  if (!parseRequest("REC_START 10044BA5 7948206080 DEADBEEF 300", request) || !request.write ||
      request.serial != 0x10044BA5 || request.capacity != 7948206080ULL || request.seconds != 300) return 5;
  for (const char* bad : {"REC_START10044BA5 7948206080 DEADBEEF 1", "REC_START 10044BA5 7948206080 DEADBEEF 0",
      "REC_START 10044BA5 7948206080 DEADBEEF 301", "REC_START 10044BA5 -1 DEADBEEF 1",
      "REC_START 10044BA5 18446744073709551616 DEADBEEF 1", "REC_START 10044BA5 1 DEADBEEF 1 trailing",
      "REC_VERIFY DEADBEEF/", "REC_VERIFYdeadbeef", "REC_VERIFY DEADBEEF\nREC_START"})
    if (parseRequest(bad, request)) return 6;
  if (!parseRequest("REC_VERIFY DEADBEEF", request) || request.write) return 7;
  FILE* f = fopen(argv[1], "wb"); if (!f) return 3;
  Header h{}; memcpy(h.magic, "I3REC001", 8); h.bytes = sizeof(h); h.version = 1;
  h.cardSerial = 0x10044BA5; h.cardBytes = 7948206080ULL; h.seconds = 1;
  h.odr = 26667; h.spiHz = 8000000; h.scaleMicroG = 61; h.flags = 1;
  h.shuntMicroOhm = 100000; h.currentLsbNanoA = 25000; h.powerLsbNanoW = 625000; h.inaAddress = 0x44;
  memset(h.source, 'a', 40); memset(h.inputs, 'b', 64);
  strcpy(h.mac, "44:1b:f6:81:91:10"); strcpy(h.wiring, "20261015-IO13-TFdirect");
  h.crc = crc32(&h, offsetof(Header, crc)); fwrite(&h, sizeof(h), 1, f);
  uint8_t frames[14] = {0x10, 0, 0x80, 0xff, 0x7f, 0x34, 0x12, 0x16, 1, 0, 2, 0, 3, 0};
  block(f, 1, 0, 2, 0, 1000000, 1000075, frames, sizeof(frames));
  InaSample ina{1000010, 0x4527, 0x800, 8, 50, 2632, 50, 8, 1};
  block(f, 2, 1, 1, 2, ina.timeUs, ina.timeUs, &ina, sizeof(ina));
  Footer foot{}; foot.samples = 2; foot.startUs = 1000000; foot.stopUs = 1000075;
  foot.inaSamples = 1; foot.saturation = 2;
  block(f, 3, 2, 1, 2, foot.startUs, foot.stopUs, &foot, sizeof(foot));
  return fclose(f) ? 4 : 0;
}
