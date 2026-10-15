#if defined(TF_NATIVE_IDF) && TF_NATIVE_IDF
#include "serial_compat.h"
#else
#include <Arduino.h>
#endif
#include "driver/sdmmc_host.h"
#include "driver/sdspi_host.h"
#include "driver/spi_master.h"
#include "esp_heap_caps.h"
#include "sdmmc_cmd.h"
#include "ff.h"
#include "diskio_impl.h"
#include "mbedtls/sha256.h"
#include <cstring>
#include <cstdlib>
#if defined(TF_ARDUINO_SPI) && TF_ARDUINO_SPI
#include <SD.h>
#include "sd_diskio.h"
#endif

#ifndef TF_WIDTH
#define TF_WIDTH 4
#endif
#ifndef TF_FREQ_KHZ
#define TF_FREQ_KHZ 20000
#endif
#ifndef TF_SPI
#define TF_SPI 0
#endif
#ifndef TF_ARDUINO_SPI
#define TF_ARDUINO_SPI 0
#endif

static sdmmc_card_t card;
static bool ready = false;
static FATFS filesystem;
static BYTE drive = FF_DRV_NOT_USED;
static char driveName[6];
static bool mounted = false;
static uint32_t rejectedWrites = 0;
#if TF_ARDUINO_SPI
static SPIClass cardSPI(FSPI);
static uint8_t arduinoDrive = FF_DRV_NOT_USED;
#endif
static const char* sampleName = "王俊超论文/10- 毕业设计评阅人评分表及评阅意见（评阅老师对每个评阅的学生每人一份）.docx";
static const char* sampleSHA = "a8014b2c5ca28eb98c9eaee6de86c76c2256bec875f0f90860ece355b1670b86";

// All FatFS writes are rejected at the disk layer, including metadata writes.
static esp_err_t readSectors(void* buffer, DWORD sector, UINT count) {
#if TF_ARDUINO_SPI
  for (UINT i = 0; i < count; ++i) {
    if (!sd_read_raw(arduinoDrive, static_cast<uint8_t*>(buffer) + i * 512, sector + i)) return ESP_FAIL;
  }
  return ESP_OK;
#else
  return sdmmc_read_sectors(&card, buffer, sector, count);
#endif
}
static DSTATUS diskInit(BYTE) { return ready ? STA_PROTECT : STA_NOINIT; }
static DSTATUS diskStatus(BYTE) { return diskInit(0); }
static DRESULT diskRead(BYTE, BYTE* buffer, DWORD sector, UINT count) {
  return ready && readSectors(buffer, sector, count) == ESP_OK ? RES_OK : RES_ERROR;
}
static DRESULT diskWrite(BYTE, const BYTE*, DWORD, UINT) {
  ++rejectedWrites;
  Serial.println("[tfmatrix] BLOCKED: filesystem requested a card write");
  return RES_WRPRT;
}
static DRESULT diskIoctl(BYTE, BYTE command, void* buffer) {
  if (!ready) return RES_NOTRDY;
  switch (command) {
    case CTRL_SYNC: return RES_OK;
    case GET_SECTOR_COUNT: *static_cast<DWORD*>(buffer) = card.csd.capacity; return RES_OK;
    case GET_SECTOR_SIZE: *static_cast<WORD*>(buffer) = card.csd.sector_size; return RES_OK;
    case GET_BLOCK_SIZE: *static_cast<DWORD*>(buffer) = 1; return RES_OK;
    default: return RES_PARERR;
  }
}

static void metadata() {
  if (!ready) { Serial.println("[tfmatrix] card not initialized"); return; }
#if TF_ARDUINO_SPI
  Serial.printf("[tfmatrix] Arduino SPI card_type=%u sectors=%d sector_bytes=%d bytes=%llu (CID/OCR not exposed)\n",
                (unsigned)sdcard_type(arduinoDrive), card.csd.capacity, card.csd.sector_size,
                (unsigned long long)card.csd.capacity * card.csd.sector_size);
#else
  Serial.printf("[tfmatrix] OCR=%08lX RCA=%04X CID=%.8s serial=%08lX CSD=%d sectors=%d sector_bytes=%d bytes=%llu\n",
                (unsigned long)card.ocr, card.rca, card.cid.name,
                (unsigned long)(uint32_t)card.cid.serial, card.csd.csd_ver, card.csd.capacity,
                card.csd.sector_size, (unsigned long long)card.csd.capacity * card.csd.sector_size);
#endif
}

static void bootSector() {
  if (!ready || card.csd.sector_size != 512) return;
  auto* data = static_cast<uint8_t*>(heap_caps_malloc(512, MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA));
  if (!data) return;
  for (unsigned n = 0; n < 2; ++n) {
    memset(data, n ? 0x5A : 0xA5, 512);
    esp_err_t error = readSectors(data, 0, 1);
    if (error != ESP_OK) {
      Serial.printf("[tfmatrix] LBA0 #%u = %s\n", n + 1, esp_err_to_name(error));
      continue;
    }
    unsigned nonzero = 0;
    uint32_t hash = 2166136261UL;
    for (unsigned i = 0; i < 512; ++i) { nonzero += data[i] != 0; hash = (hash ^ data[i]) * 16777619UL; }
    Serial.printf("[tfmatrix] LBA0 #%u ESP_OK jump=%02X-%02X-%02X signature=%02X-%02X nonzero=%u fnv1a=%08lX\n",
                  n + 1, data[0], data[1], data[2], data[510], data[511], nonzero, (unsigned long)hash);
  }
  free(data);
}

static bool mountReadOnly() {
  if (mounted) return true;
  if (!ready) return false;
  if (drive == FF_DRV_NOT_USED) {
    if (ff_diskio_get_drive(&drive) != ESP_OK) return false;
    static const ff_diskio_impl_t io = {diskInit, diskStatus, diskRead, diskWrite, diskIoctl};
    ff_diskio_register(drive, &io);
    snprintf(driveName, sizeof(driveName), "%u:", drive);
  }
  FRESULT result = f_mount(&filesystem, driveName, 1);
  Serial.printf("[tfmatrix] READ-ONLY FatFS mount=%u\n", (unsigned)result);
  mounted = result == FR_OK;
  return mounted;
}

static bool hashSample(char* digestText) {
  char path[512];
  snprintf(path, sizeof(path), "%s/%s", driveName, sampleName);
  FIL file;
  FRESULT result = f_open(&file, path, FA_READ | FA_OPEN_EXISTING);
  Serial.printf("[tfmatrix] sample open=%u\n", (unsigned)result);
  if (result != FR_OK) return false;
  uint32_t expectedBytes = f_size(&file), total = 0;
  uint8_t data[1024], digest[32];
  mbedtls_sha256_context hash;
  mbedtls_sha256_init(&hash);
  mbedtls_sha256_starts_ret(&hash, 0);
  bool success = true;
  for (;;) {
    UINT got = 0;
    result = f_read(&file, data, sizeof(data), &got);
    if (result != FR_OK) { success = false; break; }
    if (!got) break;
    mbedtls_sha256_update_ret(&hash, data, got);
    total += got;
  }
  mbedtls_sha256_finish_ret(&hash, digest);
  mbedtls_sha256_free(&hash);
  f_close(&file);
  for (unsigned i = 0; i < 32; ++i) sprintf(digestText + i * 2, "%02x", digest[i]);
  Serial.printf("[tfmatrix] sample bytes=%lu expected_size=%lu result=%u sha256=%s PC_MATCH=%u\n",
                (unsigned long)total, (unsigned long)expectedBytes, (unsigned)result, digestText,
                success && total == 18327 && strcmp(digestText, sampleSHA) == 0);
  return success && total == expectedBytes;
}

static void files() {
  if (!mountReadOnly()) return;
  FF_DIR dir;
  FILINFO info;
  FRESULT result = f_opendir(&dir, driveName);
  Serial.printf("[tfmatrix] root open=%u\n", (unsigned)result);
  if (result == FR_OK) {
    for (unsigned n = 0; n < 80; ++n) {
      result = f_readdir(&dir, &info);
      if (result != FR_OK || !info.fname[0]) break;
      Serial.printf("[tfmatrix] %c %lu %s\n", (info.fattrib & AM_DIR) ? 'D' : 'F',
                    (unsigned long)info.fsize, info.fname);
    }
    f_closedir(&dir);
  }
  char first[65] = {}, second[65] = {};
  bool one = hashSample(first), two = hashSample(second);
  Serial.printf("[tfmatrix] sample reads consistent=%u rejected_card_writes=%lu\n",
                one && two && strcmp(first, second) == 0, (unsigned long)rejectedWrites);
}

static esp_err_t initialize() {
#if TF_ARDUINO_SPI
  // Arduino SD implements its own SPI card handshake; it does not call IDF sdmmc_card_init.
  cardSPI.begin(38, 40, 39, 47);
  arduinoDrive = sdcard_init(47, &cardSPI, TF_FREQ_KHZ * 1000);
  if (arduinoDrive == FF_DRV_NOT_USED) return ESP_FAIL;
  DSTATUS status = disk_initialize(arduinoDrive);
  Serial.printf("[tfmatrix] Arduino SPI disk init status=%u\n", (unsigned)status);
  if (status & STA_NOINIT) return ESP_FAIL;
  card.csd.capacity = sdcard_num_sectors(arduinoDrive);
  card.csd.sector_size = sdcard_sector_size(arduinoDrive);
  return ESP_OK;
#elif TF_SPI
  // Existing wiring: CLK38, CMD39=MOSI, D0/40=MISO, D3/47=CS. No rewiring.
  spi_bus_config_t bus = {};
  bus.mosi_io_num = 39; bus.miso_io_num = 40; bus.sclk_io_num = 38;
  bus.quadwp_io_num = -1; bus.quadhd_io_num = -1; bus.max_transfer_sz = 4096;
  esp_err_t error = spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_CH_AUTO);
  Serial.printf("[tfmatrix] SPI bus init=%s\n", esp_err_to_name(error));
  if (error != ESP_OK) return error;
  sdmmc_host_t host = SDSPI_HOST_DEFAULT();
  host.slot = SPI2_HOST;
  host.max_freq_khz = TF_FREQ_KHZ;
  error = host.init();
  if (error != ESP_OK) return error;
  sdspi_device_config_t device = SDSPI_DEVICE_CONFIG_DEFAULT();
  device.host_id = SPI2_HOST; device.gpio_cs = GPIO_NUM_47;
  sdspi_dev_handle_t handle;
  error = sdspi_host_init_device(&device, &handle);
  Serial.printf("[tfmatrix] SPI device init=%s\n", esp_err_to_name(error));
  if (error != ESP_OK) return error;
  host.slot = handle;
#else
  sdmmc_host_t host = SDMMC_HOST_DEFAULT();
  host.flags = TF_WIDTH == 1 ? SDMMC_HOST_FLAG_1BIT : SDMMC_HOST_FLAG_4BIT;
  host.slot = SDMMC_HOST_SLOT_1; host.max_freq_khz = TF_FREQ_KHZ;
  sdmmc_slot_config_t slot = SDMMC_SLOT_CONFIG_DEFAULT();
  slot.clk = GPIO_NUM_38; slot.cmd = GPIO_NUM_39; slot.d0 = GPIO_NUM_40;
  slot.d1 = GPIO_NUM_41; slot.d2 = GPIO_NUM_42; slot.d3 = GPIO_NUM_47; slot.width = TF_WIDTH;
  esp_err_t error = host.init();
  Serial.printf("[tfmatrix] SDMMC host init=%s\n", esp_err_to_name(error));
  if (error != ESP_OK) return error;
  error = sdmmc_host_init_slot(host.slot, &slot);
  Serial.printf("[tfmatrix] SDMMC slot init=%s\n", esp_err_to_name(error));
  if (error != ESP_OK) return error;
#endif
#if !TF_ARDUINO_SPI
  return sdmmc_card_init(&host, &card);
#endif
}

void setup() {
  Serial.begin(115200);
  delay(5000);
  Serial.printf("\n[tfmatrix] build=%s %s IDF=%s protocol=%s width=%u freq_khz=%u wait_ms=5000\n",
                __DATE__, __TIME__, esp_get_idf_version(), TF_ARDUINO_SPI ? "Arduino-SPI" : (TF_SPI ? "IDF-SPI" : "SDMMC"), TF_WIDTH, TF_FREQ_KHZ);
  Serial.println("[tfmatrix] no application tasks; card disk writes blocked; no format/repair");
  esp_err_t error = initialize();
  Serial.printf("[tfmatrix] card init=%s (0x%X)\n", esp_err_to_name(error), (unsigned)error);
  ready = error == ESP_OK;
  metadata(); bootSector(); files();
  Serial.println("[tfmatrix] commands: i=metadata b=LBA0 l=read-only directory and sample hash");
}

void loop() {
  if (Serial.available()) {
    char c = Serial.read();
    if (c == 'i') metadata();
    if (c == 'b') bootSector();
    if (c == 'l') files();
  }
  delay(5);
}
