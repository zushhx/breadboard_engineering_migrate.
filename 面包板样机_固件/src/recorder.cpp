#include "recorder.h"
#include "config.h"
#include "esp_heap_caps.h"
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/stat.h>
#include <errno.h>
#include "ff.h"
#include "diskio.h"
#include "sdmmc_cmd.h"
#include "driver/sdmmc_host.h"
#include "soc/gpio_periph.h"
#include "soc/io_mux_reg.h"

// 预编译 SDK 只保留错误级别日志，f_mount 的详细错误码原本不会输出。
// 链接器包装记录返回码，失败时只读引导扇区；挂载与卸载仍由原实现执行。
extern "C" FRESULT __real_f_mount(FATFS* fs, const TCHAR* path, BYTE opt);
extern "C" esp_err_t __real_sdmmc_card_init(const sdmmc_host_t* host, sdmmc_card_t* card);
extern "C" esp_err_t __real_sdmmc_host_do_transaction(int slot, sdmmc_command_t* cmd);

struct SdCommandTrace {
  uint32_t count, firstOrder, lastOrder, arg;
  sdmmc_response_t response;
  esp_err_t result, transferError;
};
static SdCommandTrace sdInitTrace[64];
static uint32_t sdTraceOrder = 0;
static bool sdTraceActive = false;

// 初始化期间仅在 RAM 中记录每种命令的次数与末次结果，结束后统一打印，
// 避免每次串口输出打断握手时序。不追加命令或修改原始返回值。
extern "C" esp_err_t __wrap_sdmmc_host_do_transaction(int slot, sdmmc_command_t* cmd) {
  esp_err_t result = __real_sdmmc_host_do_transaction(slot, cmd);
  if (sdTraceActive && cmd != nullptr && cmd->opcode < 64) {
    SdCommandTrace& trace = sdInitTrace[cmd->opcode];
    ++sdTraceOrder;
    if (trace.count++ == 0) trace.firstOrder = sdTraceOrder;
    trace.lastOrder = sdTraceOrder;
    trace.arg = cmd->arg;
    memcpy(trace.response, cmd->response, sizeof(trace.response));
    trace.result = result;
    trace.transferError = cmd->error;
  }
  return result;
}

void Recorder::printPinDiagnostics(const char* stage) {
  const int pins[] = {PIN_SD_CMD, PIN_SD_D0, PIN_SD_D1, PIN_SD_D2, PIN_SD_D3};
  const char* names[] = {"CMD", "D0", "D1", "D2", "D3"};
  Serial.printf("[sdpins] %s", stage);
  for (unsigned i = 0; i < 5; ++i) {
    // 输入未启用时 gpio_get_level 返回 0，不能将其描述为管脚实际低电平。
    const bool inputEnabled = (REG_READ(GPIO_PIN_MUX_REG[pins[i]]) & FUN_IE) != 0;
    if (inputEnabled) Serial.printf(" %s(IO%d)=%d", names[i], pins[i], digitalRead(pins[i]));
    else Serial.printf(" %s(IO%d)=NA(input-disabled)", names[i], pins[i]);
  }
  Serial.println();
}

static const char* fatMountResultName(FRESULT result) {
  switch (result) {
    case FR_OK: return "FR_OK";
    case FR_DISK_ERR: return "FR_DISK_ERR";
    case FR_INT_ERR: return "FR_INT_ERR";
    case FR_NOT_READY: return "FR_NOT_READY";
    case FR_NO_FILESYSTEM: return "FR_NO_FILESYSTEM";
    default: return "other";
  }
}

static uint16_t bootLE16(const uint8_t* data, size_t offset) {
  return (uint16_t)data[offset] | ((uint16_t)data[offset + 1] << 8);
}

static uint32_t bootLE32(const uint8_t* data, size_t offset) {
  return (uint32_t)data[offset] | ((uint32_t)data[offset + 1] << 8) |
         ((uint32_t)data[offset + 2] << 16) | ((uint32_t)data[offset + 3] << 24);
}

static void printBootHeader(const char* source, const uint8_t* data) {
  Serial.printf("[sdsector] %s jump=%02X-%02X-%02X sig=%02X-%02X bps=%u spc=%u reserved=%u fats=%u total=%lu fatsz=%lu root=%lu\n",
                source, data[0], data[1], data[2], data[510], data[511],
                (unsigned)bootLE16(data, 11), (unsigned)data[13],
                (unsigned)bootLE16(data, 14), (unsigned)data[16],
                (unsigned long)bootLE32(data, 32), (unsigned long)bootLE32(data, 36),
                (unsigned long)bootLE32(data, 44));
  unsigned nonzero = 0;
  unsigned sentinelA5 = 0;
  unsigned sentinel5A = 0;
  uint32_t hash = 2166136261UL;
  for (unsigned index = 0; index < 512; ++index) {
    if (data[index] != 0) ++nonzero;
    if (data[index] == 0xA5) ++sentinelA5;
    if (data[index] == 0x5A) ++sentinel5A;
    hash = (hash ^ data[index]) * 16777619UL;
  }
  Serial.printf("[sdsector] %s bytes=512 nonzero=%u A5=%u 5A=%u fnv1a=%08lX\n",
                source, nonzero, sentinelA5, sentinel5A, (unsigned long)hash);
}

// 在 FatFS 和 diskio 接管前读取同一个物理 LBA0，区分底层读卡与文件系统路径。
// 只读两个扇区快照，不写卡、不格式化；初始化行为和返回码仍由原实现决定。
extern "C" esp_err_t __wrap_sdmmc_card_init(const sdmmc_host_t* host, sdmmc_card_t* card) {
  if (host != nullptr) {
    Serial.printf("[sdcard] init slot=%d host_flags=%08lX requested_max_khz=%d\n",
                  host->slot, (unsigned long)host->flags, host->max_freq_khz);
  }
  Recorder::printPinDiagnostics("before-card-init");
  memset(sdInitTrace, 0, sizeof(sdInitTrace));
  sdTraceOrder = 0;
  sdTraceActive = true;
  esp_err_t result = __real_sdmmc_card_init(host, card);
  sdTraceActive = false;
  Serial.printf("[sdcard] init result=%s (0x%X)\n", esp_err_to_name(result), (unsigned)result);
  Recorder::printPinDiagnostics("after-card-init-before-cleanup");
  for (unsigned opcode = 0; opcode < 64; ++opcode) {
    const SdCommandTrace& trace = sdInitTrace[opcode];
    if (trace.count == 0) continue;
    Serial.printf("[sdcmd] CMD%u count=%lu first=%lu last=%lu last_arg=%08lX result=%s transfer_error=%s response_w0_w3=%08lX %08lX %08lX %08lX\n",
                  opcode, (unsigned long)trace.count, (unsigned long)trace.firstOrder,
                  (unsigned long)trace.lastOrder, (unsigned long)trace.arg,
                  esp_err_to_name(trace.result), esp_err_to_name(trace.transferError),
                  (unsigned long)trace.response[0], (unsigned long)trace.response[1],
                  (unsigned long)trace.response[2], (unsigned long)trace.response[3]);
  }
  Serial.println("[sdcmd] failed-command response words are not valid card data");
  if (result != ESP_OK || card == nullptr) return result;

  Serial.printf("[sdcard] OCR=%08lX RCA=%04X is_mem=%u is_sdio=%u is_mmc=%u bus_width=%u\n",
                (unsigned long)card->ocr, (unsigned)card->rca,
                (unsigned)card->is_mem, (unsigned)card->is_sdio, (unsigned)card->is_mmc,
                card->host.get_bus_width ? (unsigned)card->host.get_bus_width(card->host.slot) : 0u);
  Serial.printf("[sdcard] CID mfg=%02X oem=%04X name=%.8s revision=%02X serial=%08lX date=%04X\n",
                (unsigned)card->cid.mfg_id, (unsigned)card->cid.oem_id, card->cid.name,
                (unsigned)card->cid.revision, (unsigned long)(uint32_t)card->cid.serial,
                (unsigned)card->cid.date);
  Serial.printf("[sdcard] CSD version=%d sectors=%d sector_bytes=%d read_block_len=%d speed=%d SCR spec=%d widths=%X\n",
                card->csd.csd_ver, card->csd.capacity, card->csd.sector_size,
                card->csd.read_block_len, card->csd.tr_speed,
                card->scr.sd_spec, (unsigned)card->scr.bus_width);

  if (!card->is_mem || card->csd.capacity <= 0 || card->csd.sector_size != 512) {
    Serial.println("[sdcard] direct read skipped: not confirmed as memory card with 512-byte sectors");
    return result;
  }
  uint8_t* sector0 = (uint8_t*)heap_caps_malloc(512, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
  if (!sector0) {
    Serial.println("[sdcard] direct read skipped: no internal DMA buffer");
    return result;
  }
  for (unsigned attempt = 0; attempt < 2; ++attempt) {
    memset(sector0, attempt == 0 ? 0xA5 : 0x5A, 512);
    esp_err_t readResult = sdmmc_read_sectors(card, sector0, 0, 1);
    Serial.printf("[sdcard] direct LBA0 attempt=%u result=%s (0x%X)\n",
                  attempt + 1, esp_err_to_name(readResult), (unsigned)readResult);
    if (readResult == ESP_OK) printBootHeader("sdmmc-direct-LBA0", sector0);
  }
  free(sector0);
  return result;
}

extern "C" FRESULT __wrap_f_mount(FATFS* fs, const TCHAR* path, BYTE opt) {
  FRESULT result = __real_f_mount(fs, path, opt);
  if (fs != nullptr) {
    Serial.printf("[sdmount] FatFS result=%u (%s), immediate=%u\n",
                  (unsigned)result, fatMountResultName(result), (unsigned)opt);
    if (result == FR_NO_FILESYSTEM || result == FR_DISK_ERR) {
      Serial.printf("[sdsector] pdrv=%u FatFS window sector=%lu\n",
                    (unsigned)fs->pdrv, (unsigned long)fs->winsect);
      printBootHeader("FatFS-window", fs->win);
      DWORD sectorCount = 0;
      WORD sectorSize = 0;
      DRESULT countResult = disk_ioctl(fs->pdrv, GET_SECTOR_COUNT, &sectorCount);
      DRESULT sizeResult = disk_ioctl(fs->pdrv, GET_SECTOR_SIZE, &sectorSize);
      Serial.printf("[sdsector] geometry count_result=%u sectors=%lu size_result=%u sector_bytes=%u\n",
                    (unsigned)countResult, (unsigned long)sectorCount,
                    (unsigned)sizeResult, (unsigned)sectorSize);
      uint8_t* sector0 = (uint8_t*)heap_caps_malloc(512, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
      if (sector0) {
        // 不同预填值可区分全零数据与缓冲区根本没有被读取函数覆盖。
        if (sizeResult == RES_OK && sectorSize == 512) {
          for (unsigned attempt = 0; attempt < 2; ++attempt) {
            memset(sector0, attempt == 0 ? 0xA5 : 0x5A, 512);
            DRESULT io = disk_read(fs->pdrv, sector0, 0, 1);
            Serial.printf("[sdsector] read LBA0 attempt=%u result=%u\n",
                          attempt + 1, (unsigned)io);
            if (io == RES_OK) printBootHeader("LBA0", sector0);
          }
        } else {
          Serial.println("[sdsector] raw read skipped: sector size is not confirmed as 512 bytes");
        }
        free(sector0);
      } else {
        Serial.println("[sdsector] no DMA buffer for read-only diagnostic");
      }
    }
  }
  return result;
}

// ============================================================
//  TF 卡记录器实现
//  注：SD_MMC 的引脚 API 在 Arduino-ESP32 2.x / 3.x 间有差异，
//      这里按本工程实测的 2.0.17 写法（setPins + begin，频率单位 kHz）。
// ============================================================

bool Recorder::begin(uint32_t freqKhz, bool mode1bit) {
  freqKhz_ = freqKhz;
  mode1bit_ = mode1bit;

  // 本工程用的是 PlatformIO 的 framework-arduinoespressif32（Arduino-ESP32 2.0.17）。
  // 实测该版本 SD_MMC 的 API 是：
  //     bool setPins(int clk, int cmd, int d0, int d1, int d2, int d3);
  //     bool begin(const char* mountpoint, bool mode1bit, bool format_if_mount_failed,
  //                int sdmmc_frequency, uint8_t maxOpenFiles);
  // 即「引脚用 setPins 指定，begin 不带引脚参数」，且频率单位是 **kHz**。
  // （初次编译时按 3.x 的 10 参数写法调用 begin 会报 no matching function，已修正。）
  if (!SD_MMC.setPins(PIN_SD_CLK, PIN_SD_CMD,
                      PIN_SD_D0, PIN_SD_D1, PIN_SD_D2, PIN_SD_D3)) {
    mounted_ = false;
    return false;
  }

  bool ok = SD_MMC.begin("/sdcard", mode1bit_,
                         /*format_if_mount_failed=*/false, (int)freqKhz_);
  mounted_ = ok;
  if (ok) scanNextIndex();     // ★ 必须在挂载之后：否则重启会覆盖旧数据
  return ok;
}

// ------------------------------------------------------------
//  上电扫描卡上已有的 vib_*.bin，把编号接在最大值之后
//
//  ★ 这是修掉的一个真 bug：此前 fileIndex_ 是普通成员、初值 0，
//    每次重启都从 /vib_00000.bin 开始，并以 FILE_WRITE 直接覆盖 ——
//    上一轮实验的数据会被静默抹掉，而且界面上完全看不出来。
// ------------------------------------------------------------
void Recorder::scanNextIndex() {
  File root = SD_MMC.open("/");
  if (!root) return;

  int32_t maxIdx = -1;
  File f = root.openNextFile();
  while (f) {
    if (!f.isDirectory()) {
      const char* nm = f.name();
      const char* slash = strrchr(nm, '/');
      if (slash) nm = slash + 1;
      // 只认自己写出来的名字：vib_ + 5 位数字 + .bin = 恰好 13 个字符
      if (strlen(nm) == 13 && strncmp(nm, "vib_", 4) == 0 &&
          strcmp(nm + 9, ".bin") == 0) {
        const int32_t idx = (int32_t)atoi(nm + 4);
        if (idx > maxIdx) maxIdx = idx;
      }
    }
    f = root.openNextFile();
  }
  root.close();

  fileIndex_ = (uint32_t)(maxIdx + 1);
  Serial.printf("[rec] 卡上已有 %d 个数据文件，本次将从 vib_%05u.bin 开始写\n",
                (int)(maxIdx + 1), (unsigned)fileIndex_);
}

uint32_t Recorder::cardSizeMB() {
  if (!mounted_) return 0;
  return (uint32_t)(SD_MMC.cardSize() / (1024ULL * 1024ULL));
}

uint32_t Recorder::cardFreeMB() {
  if (!mounted_) return 0;
  const uint64_t total = SD_MMC.totalBytes();
  const uint64_t used  = SD_MMC.usedBytes();
  if (total <= used) return 0;
  return (uint32_t)((total - used) / (1024ULL * 1024ULL));
}

uint8_t Recorder::cardType() {
  if (!mounted_) return 0;
  return (uint8_t)SD_MMC.cardType();
}

bool Recorder::startSegment() {
  if (!mounted_) return false;
  if (segOpen_) endSegment();

  snprintf(curName_, sizeof(curName_), "/vib_%05u.bin", (unsigned)fileIndex_);

  // 双保险：万一 scanNextIndex 没跑到（例如卡是热插入后才挂载的），
  // 这里再查一次同名文件，有就往后跳 —— 绝不覆盖已有数据。
  while (SD_MMC.exists(curName_)) {
    fileIndex_++;
    if (fileIndex_ > 99999) return false;
    snprintf(curName_, sizeof(curName_), "/vib_%05u.bin", (unsigned)fileIndex_);
  }

  file_ = SD_MMC.open(curName_, FILE_WRITE);
  if (!file_) return false;
  fileIndex_++;

  // 开段时先写一个占位头；段结束时会把 reserved 回填后重写整个头
  hdr_ = RecHeader{};
  memcpy(hdr_.magic, "VIBB", 4);
  hdr_.version        = 1;
  hdr_.sampleRateHz   = SAMPLE_RATE_HZ;
  hdr_.startMillis    = millis();
  hdr_.bytesPerSample = 6;

  if (file_.write((const uint8_t*)&hdr_, sizeof(hdr_)) != sizeof(hdr_)) {
    file_.close();
    return false;
  }

  segOpen_       = true;
  segSamples_    = 0;
  segWriteFails_ = 0;
  segLost_       = 0;
  closeDrop_     = 0;
  return true;
}

size_t Recorder::writeSamples(const int16_t* xyz, size_t n) {
  // 没有打开的段：样本进不来就直接计入丢失，不能静默
  if (!segOpen_ || !file_) {
    lostSamples_ += (uint32_t)n;
    segLost_     += (uint32_t)n;
    return 0;
  }

  const size_t bytes = n * 6;
  const size_t w     = file_.write((const uint8_t*)xyz, bytes);
  const size_t got   = w / 6;

  totalSamples_ += got;
  segSamples_   += (uint32_t)got;

  if (got < n) {
    // 卡满 / 掉卡 / 坏块 / 写超时都会走到这里。
    // ★ 必须计数并上报 —— 这类问题最怕静默：UI 上少一点数据，用户根本发现不了。
    const uint32_t lost = (uint32_t)(n - got);
    writeFails_  += 1;
    segWriteFails_ += 1;
    lostSamples_ += lost;
    segLost_     += lost;
  }
  return w;
}

bool Recorder::endSegment() {
  if (!segOpen_) return true;

  // ★ 回填文件头：把"这段数据可不可信"写进文件本身。
  //   这样事后分析（bin2csv / analyze）不依赖串口日志就能判断某段是否丢过样本。
  RecTail t{};
  t.dropSamples    = closeDrop_;
  t.writtenSamples = segSamples_;
  t.writeFails     = segWriteFails_;
  t.lostByWrite    = segLost_;
  memcpy(hdr_.reserved, &t, sizeof(t));

  bool ok = true;
  if (file_) {
    if (file_.seek(0)) {
      if (file_.write((const uint8_t*)&hdr_, sizeof(hdr_)) != sizeof(hdr_)) ok = false;
    } else {
      ok = false;
    }
    file_.flush();
    file_.close();
  }
  segOpen_ = false;

  if (closeDrop_ || segWriteFails_ || segLost_) {
    Serial.printf("[rec] %s 收尾：落盘 %u 样本，缓冲丢 %u，写失败 %u 次（共丢 %u 样本）★ 本段数据不完整\n",
                  curName_, (unsigned)segSamples_, (unsigned)closeDrop_,
                  (unsigned)segWriteFails_, (unsigned)segLost_);
  } else {
    Serial.printf("[rec] %s 收尾：落盘 %u 样本，零丢失\n",
                  curName_, (unsigned)segSamples_);
  }
  closeDrop_ = 0;
  return ok;
}

double Recorder::selfTestThroughput(size_t nSamples, bool direct) {
  if (!mounted_ || segOpen_ || nSamples == 0) return 0.0;

  static const char* kTest = "/speedtest.bin";
  if (SD_MMC.exists(kTest)) {
    Serial.println("[sdtest] FAIL: speedtest.bin already exists; preserved");
    return 0.0;
  }
  File f;
  int fd = -1;
  if (direct) fd = open("/sdcard/speedtest.bin", O_WRONLY | O_CREAT | O_EXCL, 0600);
  else f = SD_MMC.open(kTest, FILE_WRITE);
  if (direct ? fd < 0 : !f) return 0.0;
  FATFS* fsinfo = nullptr;
  DWORD freeClusters;
  if (f_getfree("0:", &freeClusters, &fsinfo) == FR_OK)
    Serial.printf("[sdtest] API=%s, cluster=%u bytes, block=%u bytes\n",
                  direct ? "POSIX" : "File", (unsigned)fsinfo->csize * 512,
                  (unsigned)BUF_SAMPLES * 6);

  int16_t* buf = (int16_t*)heap_caps_malloc(BUF_SAMPLES * 6,
                                            MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA);
  if (!buf) {
    buf = (int16_t*)malloc(BUF_SAMPLES * 6);
  }
  if (!buf) { if (direct) close(fd); else f.close(); SD_MMC.remove(kTest); return 0.0; }

  uint32_t t0 = millis();
  size_t done = 0;
  bool ok = true;
  while (done < nSamples) {
    size_t chunk = min((size_t)BUF_SAMPLES, nSamples - done);
    for (size_t i = 0; i < chunk * 3; ++i)
      buf[i] = (int16_t)(((done * 3 + i) * 251 + 17) & 0x7FFF);
    const int w = direct ? (int)write(fd, buf, chunk * 6)
                         : (int)f.write((const uint8_t*)buf, chunk * 6);
    if (w != (int)(chunk * 6)) {
      Serial.println("[sdtest] FAIL: short write");
      ok = false;
      break;
    }
    done += chunk;
  }
  uint32_t writeMs = millis() - t0;
  if (direct) {
    if (fsync(fd) != 0) ok = false;
    if (close(fd) != 0) ok = false;
  } else {
    f.flush();
    f.close();
  }
  double ms = (double)(millis() - t0);
  Serial.printf("[sdtest] write=%u ms, sync+close=%u ms\n",
                (unsigned)writeMs, (unsigned)ms - writeMs);
  if (ok) {
    f = SD_MMC.open(kTest, FILE_READ);
    if (!f || f.size() != nSamples * 6) {
      Serial.println("[sdtest] FAIL: reopen or file size");
      ok = false;
    }
    done = 0;
    while (ok && done < nSamples) {
      size_t chunk = min((size_t)BUF_SAMPLES, nSamples - done);
      if (f.read((uint8_t*)buf, chunk * 6) != chunk * 6) {
        Serial.println("[sdtest] FAIL: short read");
        ok = false;
        break;
      }
      for (size_t i = 0; i < chunk * 3; ++i) {
        if (buf[i] != (int16_t)(((done * 3 + i) * 251 + 17) & 0x7FFF)) {
          Serial.printf("[sdtest] FAIL: data mismatch at byte %u\n",
                        (unsigned)((done * 3 + i) * 2));
          ok = false;
          break;
        }
      }
      done += chunk;
    }
    if (f) f.close();
  }
  if (!SD_MMC.remove(kTest)) {
    Serial.println("[sdtest] FAIL: test file cleanup");
    ok = false;
  }
  free(buf);

  if (!ok || ms <= 0) return 0.0;
  Serial.printf("[sdtest] PASS: %u bytes written, reopened and verified; test file removed\n",
                (unsigned)(nSamples * 6));
  return (double)nSamples * 1000.0 / ms;   // 样本/秒
}

bool Recorder::selfTestSustained(uint32_t seconds) {
  Serial.println("[sdsoak] Storage-only simulated load; not a real sensor zero-loss test. Send x to cancel.");
  Serial.println("[sdsoak] bytes_written counts write() acceptance; sync_returned means flush() returned, not verified durability.");
  auto reject = [](const char* reason) {
    Serial.printf("[sdsoak] FAIL reason=%s bytes_written=0 duration_ms=0 max_write_ms=0 max_sync_ms=0 max_backlog_samples=0\n", reason);
    return false;
  };
  if (!mounted_ || segOpen_) return reject("not_ready_or_recording");
  if (seconds == 0 || seconds > 600) return reject("seconds_must_be_1_to_600");
  const uint32_t target = seconds * SAMPLE_RATE_HZ * 6UL;
  const uint32_t duration = seconds * 1000UL;
  const size_t block = BUF_SAMPLES * 6;
  const uint32_t capacity = 32768UL * 6; // 保守模型：积压包含正在写入的这一个块。
  FATFS* fs = nullptr;
  DWORD freeClusters = 0;
  if (f_getfree("0:", &freeClusters, &fs) != FR_OK || !fs) return reject("capacity_query");
  const uint64_t clusterBytes = (uint64_t)fs->csize * 512;
  if ((uint64_t)freeClusters * clusterBytes < (uint64_t)target + clusterBytes)
    return reject("insufficient_free_space");
  uint8_t* buf = (uint8_t*)heap_caps_malloc(block, MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA);
  if (!buf) return reject("internal_dma_allocation");
  char path[32] = {}, fullPath[48] = {};
  bool created = false, cancelled = false, cleanup = true;
  const char* error = nullptr;
  File f;
  uint32_t written = 0, verified = 0, maxWriteUs = 0, maxBacklog = 0, writeDuration = 0;
  uint32_t maxSyncUs = 0, nextSyncAt = 10000;
  uint32_t start = millis(), lastProgress = start;
  auto cancel = [&]() {
    while (Serial.available()) if (Serial.read() == 'x') cancelled = true;
    return cancelled;
  };
  auto produced = [&](uint32_t now) -> uint32_t {
    const uint32_t elapsed = min(now - start, duration);
    return (uint32_t)(((uint64_t)elapsed * SAMPLE_RATE_HZ / 1000) * 6);
  };
  // 每32位字使用其绝对位置，避免原短测模式每64KiB重复；按小端逐字节校验尾部。
  auto pattern = [](uint32_t pos) -> uint8_t {
    const uint32_t word = (pos / 4) * 2654435761UL ^ 0xA53C96E7UL;
    return (uint8_t)(word >> ((pos % 4) * 8));
  };
  auto backlog = [&](uint32_t now) {
    const uint32_t pending = produced(now) - written;
    if (pending > maxBacklog) maxBacklog = pending;
    if (pending > capacity && !error) error = "simulated_ring_overflow";
    return pending;
  };
  auto sync = [&](const char* beginPhase, const char* returnPhase) {
    Serial.printf("[sdsoak] %s elapsed_ms=%u bytes_accepted=%u\n", beginPhase,
                  (unsigned)(millis() - start), (unsigned)written);
    uint32_t before = micros();
    f.flush(); // File API不返回同步错误码；最终仍须关闭后完整读回。
    uint32_t took = micros() - before;
    backlog(millis()); // 墙钟生成不停：同步阻塞同样消耗模拟环形缓冲容量。
    if (took > maxSyncUs) maxSyncUs = took;
    Serial.printf("[sdsoak] %s elapsed_ms=%u sync_ms=%.3f bytes_accepted=%u\n", returnPhase,
                  (unsigned)(millis() - start), took / 1000.0, (unsigned)written);
  };
  auto progress = [&](const char* phase) {
    uint32_t now = millis();
    if (now - lastProgress >= 30000) {
      lastProgress = now;
      Serial.printf("[sdsoak] progress phase=%s elapsed_ms=%u bytes_written=%u bytes_verified=%u max_backlog_samples=%u\n",
                    phase, (unsigned)(now - start), (unsigned)written, (unsigned)verified,
                    (unsigned)((maxBacklog + 5) / 6));
    }
  };
  do {
    for (uint32_t index = 1; index <= 99999; ++index) {
      if (cancel()) break;
      snprintf(path, sizeof(path), "/sdsoak_%05u.bin", (unsigned)index);
      snprintf(fullPath, sizeof(fullPath), "/sdcard%s", path);
      int fd = open(fullPath, O_WRONLY | O_CREAT | O_EXCL, 0600);
      if (fd >= 0) { created = true; if (close(fd) != 0) error = "create_close"; break; }
      if (errno != EEXIST) { error = "exclusive_create"; break; }
    }
    if (cancelled || error) break;
    if (!created) { error = "no_free_test_name"; break; }
    f = SD_MMC.open(path, FILE_WRITE); // O_EXCL已确认此路径由本次测试新建。
    if (!f) { error = "open_for_write"; break; }
    Serial.printf("[sdsoak] begin file=%s seconds=%u target_bytes=%u rate_Bps=%u block_bytes=%u capacity_samples=32768\n",
                  path, (unsigned)seconds, (unsigned)target, (unsigned)(SAMPLE_RATE_HZ * 6), (unsigned)block);
    start = lastProgress = millis();
    while (!error && written < target && !cancel()) {
      uint32_t now = millis(), pending = backlog(now);
      if (error) break;
      progress("write");
      if (now - start >= nextSyncAt) {
        sync("sync_begin", "sync_returned");
        now = millis();
        pending = backlog(now);
        nextSyncAt = ((now - start) / 10000 + 1) * 10000; // 不集中补做错过的同步。
        if (error || cancel()) break;
      }
      size_t chunk = min(block, (size_t)(target - written));
      if (pending < chunk) { delay(1); continue; }
      for (size_t i = 0; i < chunk; ++i) buf[i] = pattern(written + i);
      uint32_t before = micros();
      size_t got = f.write(buf, chunk);
      uint32_t took = micros() - before;
      if (took > maxWriteUs) maxWriteUs = took;
      backlog(millis()); // 写阻塞结束、抵扣当前块之前，捕捉最坏积压。
      written += got;
      if (got != chunk) error = "short_write";
      delay(1); // 给空闲任务/看门狗及网络服务让出CPU。
    }
    Serial.printf("[sdsoak] write_done bytes_accepted=%u target_bytes=%u complete=%u\n",
                  (unsigned)written, (unsigned)target, written == target ? 1 : 0);
    sync("end_sync_begin", "end_sync_returned");
    Serial.println("[sdsoak] close_begin phase=write");
    f.close();
    Serial.println("[sdsoak] close_returned phase=write");
    writeDuration = millis() - start;
    if (cancel() || error) break;
    Serial.println("[sdsoak] reopen_begin");
    f = SD_MMC.open(path, FILE_READ);
    if (!f || f.size() != target || written != target) { error = "reopen_or_size"; break; }
    Serial.printf("[sdsoak] verify_begin target_bytes=%u\n", (unsigned)target);
    while (!error && verified < target && !cancel()) {
      size_t chunk = min(block, (size_t)(target - verified));
      if (f.read(buf, chunk) != chunk) { error = "short_read"; break; }
      for (size_t i = 0; i < chunk; ++i) {
        if (buf[i] != pattern(verified + i)) { error = "data_mismatch"; break; }
      }
      if (!error) verified += chunk;
      progress("verify");
      delay(1);
    }
    Serial.printf("[sdsoak] verify_done bytes_verified=%u complete=%u\n",
                  (unsigned)verified, verified == target ? 1 : 0);
  } while (false);
  if (f) {
    Serial.println("[sdsoak] close_begin phase=cleanup");
    f.close();
    Serial.println("[sdsoak] close_returned phase=cleanup");
  }
  if (created) {
    Serial.printf("[sdsoak] cleanup_begin file=%s\n", path);
    cleanup = SD_MMC.remove(path);
    Serial.printf("[sdsoak] cleanup_returned cleanup_ok=%u\n", cleanup ? 1 : 0);
    if (!cleanup && !error) error = "test_file_cleanup";
  }
  free(buf);
  const bool passed = !error && !cancelled && written == target && verified == target;
  Serial.printf("[sdsoak] %s reason=%s file=%s bytes_written=%u bytes_verified=%u duration_ms=%u total_ms=%u max_write_ms=%.3f max_sync_ms=%.3f max_backlog_samples=%u cleanup_ok=%u\n",
                cancelled ? "CANCELLED" : (passed ? "PASS" : "FAIL"), cancelled ? "user_cancel" : (error ? error : "none"),
                created ? path : "none", (unsigned)written, (unsigned)verified, (unsigned)writeDuration,
                (unsigned)(millis() - start), maxWriteUs / 1000.0, maxSyncUs / 1000.0,
                (unsigned)((maxBacklog + 5) / 6), cleanup ? 1 : 0);
  return passed;
}
