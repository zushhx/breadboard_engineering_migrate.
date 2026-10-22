#include "recorder.h"
#include "record_format.h"
#include "record_request.h"
#include "modules.h"
#include "serial_compat.h"
#include "build_identity.h"
#include "tf_test_core.h"
#include "web_control.h"
#include "storage_space.h"
#include "ff.h"
#include "esp_heap_caps.h"
#include "esp_mac.h"
#include "esp_timer.h"
#include "freertos/queue.h"
#include "mbedtls/sha256.h"
#include <atomic>
#include <cstring>
#include <cstdlib>
#include <cinttypes>

namespace recorder {
struct Chunk { uint64_t firstUs, lastUs, firstSample; uint32_t count; uint8_t data[kPayload]; };
static Chunk* pool;
static QueueHandle_t freeQ, dataQ, inaQ;
static TaskHandle_t sensorTask, monitorTask;
static std::atomic<bool> stopRequested{false}, sensorDone{false}, monitorDone{false};
static std::atomic<uint32_t> errors{0};
static Footer totals;
static uint64_t deadline;
static bool locked = false;
static FIL file; // FatFS embeds a 4 KiB sector buffer; never place FIL on task stack.
static uint8_t verifyBuffer[4096];
static void fail(uint32_t reason) { errors.fetch_or(reason); stopRequested.store(true); }
static uint64_t now() { return static_cast<uint64_t>(esp_timer_get_time()); }

static void acquire(void*) {
  ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
  Chunk* chunk = nullptr;
  bool stopped = false, drained = false;
  while (true) {
    if (!stopped && (stopRequested.load() || now() >= deadline)) {
      if (!modules::stopInput()) fail(Stop);
      totals.stopUs = now(); stopped = true;
    }
    uint16_t count = 0; bool overflow = false;
    if (!modules::fifoStatus(count, overflow)) { fail(Spi); break; }
    if (count > totals.fifoHigh) totals.fifoHigh = count;
    if (overflow) { fail(Overflow); break; }
    if (!count && stopped) { drained = true; break; }
    for (unsigned i = 0; i < count; ++i) {
      if (!chunk) {
        if (xQueueReceive(freeQ, &chunk, 0) != pdTRUE) { fail(Queue); break; }
        chunk->count = 0; chunk->firstSample = totals.samples; chunk->firstUs = now();
      }
      uint8_t* frame = chunk->data + chunk->count * 7;
      if (!modules::fifoFrame(frame)) { fail(Spi); break; }
      // DS12569 defines tag sensor 0x02. Preserve the 2-bit slot counter/parity raw;
      // no undocumented parity polarity or global sequence interpretation.
      if ((frame[0] >> 3) != 2) { fail(Tag); break; }
      for (unsigned axis = 0; axis < 3; ++axis) {
        int value = modules::leSigned16(frame + 1 + axis * 2);
        if (value <= -32760 || value >= 32760) ++totals.saturation;
      }
      ++chunk->count; ++totals.samples; chunk->lastUs = now();
      if (chunk->count == kFrames) {
        if (xQueueSend(dataQ, &chunk, 0) != pdTRUE) { fail(Queue); break; }
        chunk = nullptr;
        unsigned used = kSlots - uxQueueMessagesWaiting(freeQ);
        if (used > totals.queueHigh) totals.queueHigh = used;
      }
    }
    if (errors.load()) break;
    vTaskDelay(1); // Fixed 1 ms tick in frozen SDK configuration, allows idle/WDT service.
  }
  if (!stopped) { if (!modules::stopInput()) fail(Stop); totals.stopUs = now(); }
  // A failed run preserves unconsumed FIFO instead of silently resetting it.
  if (drained && !modules::finishFifo()) fail(Stop);
  if (chunk) {
    if (chunk->count) {
      if (xQueueSend(dataQ, &chunk, 0) != pdTRUE) fail(Queue);
    } else xQueueSend(freeQ, &chunk, 0);
  }
  totals.sensorStack = uxTaskGetStackHighWaterMark(nullptr);
  sensorDone.store(true);
  vTaskSuspend(nullptr); // Parent deletes only after observing done.
}
static void monitor(void*) {
  ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
  TickType_t wake = xTaskGetTickCount();
  while (!sensorDone.load() && !stopRequested.load()) {
    modules::InaRegisters r = {}; bool valid = false;
    uint64_t stamp = now();
    bool ok = modules::monitorRead(r, valid);
    InaSample item{stamp, r.config, r.calibration, r.mask, r.shunt, r.bus, r.current, r.power,
                   static_cast<uint16_t>(ok && valid)};
    if (xQueueSend(inaQ, &item, 0) != pdTRUE) fail(Queue);
    if (!ok) fail(Monitor);
    vTaskDelayUntil(&wake, pdMS_TO_TICKS(100));
  }
  totals.inaStack = uxTaskGetStackHighWaterMark(nullptr);
  monitorDone.store(true);
  vTaskSuspend(nullptr);
}
static void cleanup() {
  if (sensorTask) vTaskDelete(sensorTask);
  if (monitorTask) vTaskDelete(monitorTask);
  sensorTask = monitorTask = nullptr;
  if (freeQ) vQueueDelete(freeQ);
  if (dataQ) vQueueDelete(dataQ);
  if (inaQ) vQueueDelete(inaQ);
  freeQ = dataQ = inaQ = nullptr;
  // Retain the fixed pool across runs, so WiFi allocation cannot fragment it.
}
bool reservePool() {
  if (pool) return true;
  constexpr unsigned caps = MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT;
  auto beforeFree = heap_caps_get_free_size(caps);
  auto beforeLargest = heap_caps_get_largest_free_block(caps);
  pool = static_cast<Chunk*>(heap_caps_malloc(sizeof(Chunk) * kSlots, caps));
  Serial.printf("[rec] reserved_pool bytes=%u caps=%u before_free=%u before_largest=%u success=%u after_free=%u after_largest=%u\n",
      (unsigned)(sizeof(Chunk) * kSlots), caps, (unsigned)beforeFree, (unsigned)beforeLargest, pool != nullptr,
      (unsigned)heap_caps_get_free_size(caps), (unsigned)heap_caps_get_largest_free_block(caps));
  return pool != nullptr;
}
static bool resources() {
  if (!reservePool()) return false;
  freeQ = xQueueCreate(kSlots, sizeof(Chunk*)); dataQ = xQueueCreate(kSlots, sizeof(Chunk*));
  inaQ = xQueueCreate(32, sizeof(InaSample));
  if (!pool || !freeQ || !dataQ || !inaQ) return false;
  for (unsigned i = 0; i < kSlots; ++i) { Chunk* p = pool + i; xQueueSend(freeQ, &p, 0); }
  if (xTaskCreatePinnedToCore(acquire, "fifo", 4096, nullptr, 8, &sensorTask, 1) != pdPASS) return false;
  if (xTaskCreatePinnedToCore(monitor, "ina", 4096, nullptr, 2, &monitorTask, 0) != pdPASS) return false;
  return true;
}
static void hex(const uint8_t* bytes, char* output) {
  for (unsigned i = 0; i < 32; ++i) sprintf(output + i * 2, "%02x", bytes[i]);
}
static bool readExact(void* dst, unsigned bytes) {
  UINT got = 0; return f_read(&file, dst, bytes, &got) == FR_OK && got == bytes;
}
static bool verify(const char* path, char* digest) {
  if (f_open(&file, path, FA_READ | FA_OPEN_EXISTING) != FR_OK) return false;
  mbedtls_sha256_context hash; mbedtls_sha256_init(&hash); mbedtls_sha256_starts_ret(&hash, 0);
  Header head = {}; bool ok = readExact(&head, sizeof(head));
  ok = ok && memcmp(head.magic, "I3REC001", 8) == 0 && head.bytes == sizeof(head) && head.version == 1 &&
      sensor_range::validScale(head.scaleMicroG) && head.crc == crc32(&head, offsetof(Header, crc));
  if (ok) mbedtls_sha256_update_ret(&hash, reinterpret_cast<uint8_t*>(&head), sizeof(head));
  uint32_t sequence = 0, inaCount = 0, invalid = 0; uint64_t samples = 0; bool ended = false;
  while (ok && !ended) {
    BlockHeader h = {};
    ok = readExact(&h, sizeof(h)) && h.magic == 0x314B4C42 && h.sequence == sequence++ &&
        h.bytes <= sizeof(verifyBuffer) && h.firstSample == samples && h.firstUs <= h.lastUs;
    if (!ok || !readExact(verifyBuffer, h.bytes)) { ok = false; break; }
    ok = blockCrc(h, verifyBuffer) == h.crc;
    if (!ok) break;
    mbedtls_sha256_update_ret(&hash, reinterpret_cast<uint8_t*>(&h), sizeof(h));
    mbedtls_sha256_update_ret(&hash, verifyBuffer, h.bytes);
    if (h.type == 1) {
      ok = h.count > 0 && h.count <= kFrames && h.bytes == h.count * 7;
      for (unsigned i = 0; ok && i < h.count; ++i) ok = (verifyBuffer[i * 7] >> 3) == 2;
      samples += h.count;
    } else if (h.type == 2) {
      ok = h.count == 1 && h.bytes == sizeof(InaSample);
      InaSample r = {}; if (ok) { memcpy(&r, verifyBuffer, sizeof(r)); ++inaCount; invalid += !r.valid; }
    } else if (h.type == 3) {
      Footer end = {}; ok = h.count == 1 && h.bytes == sizeof(end);
      if (ok) {
        memcpy(&end, verifyBuffer, sizeof(end));
        ok = !end.errors && end.samples == samples && samples > 0 && end.inaSamples == inaCount &&
            inaCount > invalid && end.inaInvalid == invalid && end.stopUs > end.startUs;
      }
      ended = true;
    } else ok = false;
  }
  ok = ok && ended && f_tell(&file) == f_size(&file);
  uint8_t sum[32]; mbedtls_sha256_finish_ret(&hash, sum); mbedtls_sha256_free(&hash); hex(sum, digest);
  ok = f_close(&file) == FR_OK && ok;
  Serial.printf("[rec] verify=%u samples=%llu blocks=%lu ina=%lu invalid=%lu sha256=%s path=%s\n",
      ok, static_cast<unsigned long long>(samples), static_cast<unsigned long>(sequence),
      static_cast<unsigned long>(inaCount), static_cast<unsigned long>(invalid), digest, path);
  return ok;
}
static bool emit(const void* data, size_t bytes, mbedtls_sha256_context& hash) {
  UINT written = 0; uint64_t before = now();
  FRESULT result = f_write(&file, data, bytes, &written);
  uint32_t elapsed = static_cast<uint32_t>(now() - before);
  if (elapsed > totals.maxWriteUs) totals.maxWriteUs = elapsed;
  if (result != FR_OK || written != bytes) { fail(Write); return false; }
  return mbedtls_sha256_update_ret(&hash, static_cast<const uint8_t*>(data), bytes) == 0;
}
static bool block(uint32_t type, uint32_t seq, uint32_t count, uint64_t first, uint64_t last,
                  uint64_t sample, const void* data, uint32_t bytes, mbedtls_sha256_context& hash) {
  BlockHeader h{0x314B4C42, type, seq, bytes, count, first, last, sample, 0};
  h.crc = blockCrc(h, data);
  return emit(&h, sizeof(h), hash) && emit(data, bytes, hash);
}
static bool sync() {
  uint64_t before = now(); bool ok = f_sync(&file) == FR_OK;
  uint32_t elapsed = static_cast<uint32_t>(now() - before);
  if (elapsed > totals.maxSyncUs) totals.maxSyncUs = elapsed;
  if (!ok) fail(Write);
  return ok;
}
static void record(const Store& store, const char* run, unsigned seconds, unsigned rangeG) {
  if (locked) { Serial.println("[rec] rejected: previous failure; preserve evidence, reboot required"); return; }
  if (!control::begin(run, seconds, rangeG)) { Serial.println("[rec] rejected busy/unavailable control state"); return; }
  control::phase(control::Phase::Preparing, "检查卡、内存与模块；准备本轮录制");
  char directory[40], path[56];
  snprintf(directory, sizeof(directory), "%s/REC_%s", store.drive, run);
  snprintf(path, sizeof(path), "%s/DATA.BIN", directory);
  FILINFO info; FRESULT exists = f_stat(directory, &info);
  if (exists != FR_NO_FILE) { Serial.printf("[rec] rejected existing/unknown directory fatfs=%u\n", exists);
    control::result(false, false, "目录已存在或查询失败，未开始录制"); return; }
  DWORD clusters = 0; FATFS* fs = nullptr;
  uint64_t freeBytes = 0;
  const bool spaceValid = f_getfree(store.drive, &clusters, &fs) == FR_OK && fs &&
      control::freeBytesFromClusters(clusters, fs->csize, store.capacity, freeBytes);
  control::storageSpace(spaceValid, freeBytes);
  if (!spaceValid || freeBytes < static_cast<uint64_t>(seconds) * 200000 + 2097152) {
    Serial.println("[rec] rejected insufficient/free-space query failed");
    control::result(false, false, "空间不足或查询失败，未开始录制"); return;
  }
  totals = {}; errors.store(0); stopRequested.store(false); sensorDone.store(false); monitorDone.store(false);
  Serial.printf("[rec] memory pool=%u internal_free=%u largest=%u\n", (unsigned)(sizeof(Chunk) * kSlots),
      (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL), (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL));
  if (control::stopping()) { control::result(false, false, "准备阶段已取消，未写卡"); return; }
  if (!resources()) { cleanup(); locked = true; Serial.println("[rec] FAIL resource allocation; no card writes");
    control::result(false, true, "任务或队列分配失败，未写卡；保留现场"); return; }
  if (!modules::prepareRecord(rangeG)) {
    cleanup(); locked = true; Serial.println("[rec] FAIL module preparation; no card writes");
    control::result(false, true, "模块准备失败，未写卡；保留现场"); return;
  }
  Header h = {}; memcpy(h.magic, "I3REC001", 8); h.bytes = sizeof(h); h.version = 1;
  h.cardSerial = store.serial; h.cardBytes = store.capacity; h.seconds = seconds;
  h.odr = 26667; h.spiHz = 8000000; h.scaleMicroG = sensor_range::scaleMicroG(rangeG); h.flags = 1; // INA precision unaccepted.
  h.shuntMicroOhm = 100000; h.currentLsbNanoA = 25000; h.powerLsbNanoW = 625000;
  h.inaAddress = modules::monitorAddressValue();
  snprintf(h.source, sizeof(h.source), "%s", TF_SOURCE_COMMIT); snprintf(h.inputs, sizeof(h.inputs), "%s", TF_INPUT_SHA256);
  uint8_t mac[6] = {}; esp_err_t macStatus = esp_efuse_mac_get_default(mac);
  if (macStatus != ESP_OK) { cleanup(); locked = true; Serial.println("[rec] FAIL MAC; no card writes");
    control::result(false, true, "MAC读取失败，未写卡"); return; }
  snprintf(h.mac, sizeof(h.mac), "%02x:%02x:%02x:%02x:%02x:%02x", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  snprintf(h.wiring, sizeof(h.wiring), "20261015-IO13-TFdirect"); h.crc = crc32(&h, offsetof(Header, crc));
  store.gate(true); bool opened = false;
  bool ok = f_mkdir(directory) == FR_OK;
  if (ok) { opened = f_open(&file, path, FA_WRITE | FA_CREATE_NEW) == FR_OK; ok = opened; }
  mbedtls_sha256_context hash; mbedtls_sha256_init(&hash); mbedtls_sha256_starts_ret(&hash, 0);
  if (ok) ok = emit(&h, sizeof(h), hash) && sync();
  if (ok) ok = modules::startFifo(rangeG);
  if (!ok) {
    modules::stopInput(); if (opened) f_close(&file);
    store.gate(false); mbedtls_sha256_free(&hash); cleanup(); locked = true;
    Serial.println("[rec] FAIL setup; files retained, no retry");
    control::result(false, true, "启动失败，保留文件；不自动重试"); return;
  }
  totals.startUs = now(); deadline = totals.startUs + static_cast<uint64_t>(seconds) * 1000000;
  control::phase(control::Phase::Recording, "正在写入完整原始数据；页面提供限速短窗口预览");
  xTaskNotifyGive(sensorTask); xTaskNotifyGive(monitorTask);
  Serial.printf("[rec] START run=%s seconds=%u range_g=%u scale_micro_g=%lu start_us=%llu\n", run, seconds,
      rangeG, (unsigned long)h.scaleMicroG, (unsigned long long)totals.startUs);
  uint32_t seq = 0; uint64_t writtenSamples = 0, nextSync = now() + 1000000, nextReport = nextSync;
  tftest::LineInput input;
  while (true) {
    if (control::stopping()) stopRequested.store(true);
    if (stopRequested.load() || now() >= deadline) control::phase(control::Phase::Draining, "停止输入并排空；请保持供电");
    while (Serial.available()) {
      int event = input.push(Serial.read());
      if (event > 0) {
        if (strcmp(input.line(), "REC_STOP") == 0) stopRequested.store(true);
        else Serial.println("[rec] busy; only REC_STOP accepted");
      }
    }
    Chunk* chunk = nullptr;
    if (xQueueReceive(dataQ, &chunk, pdMS_TO_TICKS(2)) == pdTRUE) {
      control::preview(chunk->data, chunk->count, chunk->firstUs);
      if (ok) ok = block(1, seq++, chunk->count, chunk->firstUs, chunk->lastUs,
          writtenSamples, chunk->data, chunk->count * 7, hash);
      writtenSamples += chunk->count;
      xQueueSend(freeQ, &chunk, 0);
    }
    InaSample item;
    while (xQueueReceive(inaQ, &item, 0) == pdTRUE) {
      control::ina(item.timeUs, item.valid != 0, static_cast<int32_t>(item.bus) * 125 / 100,
          modules::signed16(item.current) * 25, static_cast<int32_t>(item.power) * 625);
      if (ok) ok = block(2, seq++, 1, item.timeUs, item.timeUs, writtenSamples, &item, sizeof(item), hash);
      ++totals.inaSamples; totals.inaInvalid += !item.valid;
    }
    if (!ok) fail(Write);
    if (sensorDone.load() && monitorDone.load() && !uxQueueMessagesWaiting(dataQ) && !uxQueueMessagesWaiting(inaQ)) break;
    if (now() >= nextSync && ok) { ok = sync(); nextSync = now() + 1000000; }
    if (now() >= nextReport) {
      // FatFS updates this cache on allocation. Do not trigger a FAT scan during recording.
      uint64_t available = 0;
      const bool cached = fs->free_clst <= fs->n_fatent - 2 &&
          control::freeBytesFromClusters(fs->free_clst, fs->csize, store.capacity, available);
      control::storageSpace(cached, available);
      control::progress(writtenSamples, now() - totals.startUs, errors.load(), uxQueueMessagesWaiting(dataQ),
          totals.maxWriteUs, totals.maxSyncUs);
      Serial.printf("[rec] progress written_samples=%llu queue=%u errors=%lu\n", (unsigned long long)writtenSamples,
          (unsigned)uxQueueMessagesWaiting(dataQ), (unsigned long)errors.load()); nextReport = now() + 1000000;
    }
  }
  if (writtenSamples != totals.samples || !totals.samples || !totals.inaSamples || totals.stopUs <= totals.startUs) fail(Queue);
  if (totals.inaSamples == totals.inaInvalid) fail(Monitor);
  if (totals.stopUs > totals.startUs) {
    double rate = totals.samples * 1000000.0 / (totals.stopUs - totals.startUs);
    if (rate < 25333 || rate > 28001) fail(Rate); // Predetermined +/-5% functional check, not timebase calibration.
  }
  totals.errors = errors.load(); totals.minHeap = esp_get_minimum_free_heap_size();
  control::phase(control::Phase::Closing, "同步并关闭文件；请保持供电");
  if (ok) ok = block(3, seq++, 1, totals.startUs, totals.stopUs, writtenSamples, &totals, sizeof(totals), hash) && sync();
  bool closed = f_close(&file) == FR_OK; store.gate(false);
  uint8_t sum[32]; mbedtls_sha256_finish_ret(&hash, sum); mbedtls_sha256_free(&hash);
  char expected[65] = {}, actual[65] = {}; hex(sum, expected);
  ok = ok && closed && !totals.errors;
  cleanup();
  control::finalMetrics(totals.fifoHigh, totals.queueHigh);
  control::progress(writtenSamples, totals.stopUs > totals.startUs ? totals.stopUs - totals.startUs : 0,
      totals.errors, 0, totals.maxWriteUs, totals.maxSyncUs);
  control::phase(control::Phase::Verifying, "完整回读与摘要比对；此阶段尚未通过");
  bool readback = verify(path, actual);
  ok = ok && readback && strcmp(expected, actual) == 0;
  if (!ok) locked = true;
  control::result(ok, locked, ok ? "文件同步、关闭、完整回读及摘要比对通过" : "本轮失败，保留文件与日志；不自动重试", actual);
  Serial.printf("[rec] %s run=%s samples=%llu errors=%lu fifo_high=%lu queue_high=%lu write_us=%lu sync_us=%lu "
      "sha256=%s write_gate=0 precision_accepted=0\n", ok ? "PASS" : "FAIL", run, (unsigned long long)totals.samples,
      (unsigned long)totals.errors, (unsigned long)totals.fifoHigh, (unsigned long)totals.queueHigh,
      (unsigned long)totals.maxWriteUs, (unsigned long)totals.maxSyncUs, actual);
}
void command(const char* line, const Store& store) {
  Request request = {};
  if (parseRequest(line, request) && request.write && request.serial == store.serial && request.capacity == store.capacity) {
    record(store, request.run, request.seconds, request.rangeG); return;
  } else if (parseRequest(line, request) && !request.write) {
    char path[56], digest[65] = {}; snprintf(path, sizeof(path), "%s/REC_%s/DATA.BIN", store.drive, request.run);
    bool ok = verify(path, digest);
    Serial.printf("[rec] VERIFY_%s run=%s write_gate=0\n", ok ? "PASS" : "FAIL", request.run); return;
  }
  Serial.println("[rec] rejected command/identity/range; no writes enabled");
}
}
