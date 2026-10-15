#include <Arduino.h>
#include "driver/sdmmc_host.h"
#include "esp_heap_caps.h"
#include "esp_idf_version.h"
#include "sdmmc_cmd.h"
#include <cstring>

#ifndef TF_WAIT_MS
#define TF_WAIT_MS 300
#endif

// Same electrical endpoints, width, frequency limit and external pull-ups as
// the main firmware. No SPI/I2C/Wi-Fi, filesystem mount, linker wrappers or writes.
static constexpr int kClock = 38;
static constexpr int kCommand = 39;
static constexpr int kData0 = 40;
static constexpr int kData1 = 41;
static constexpr int kData2 = 42;
static constexpr int kData3 = 47;
static sdmmc_card_t card;
static bool cardReady = false;

static void printCard() {
  if (!cardReady) {
    Serial.println("[tfdiag] card initialization did not succeed");
    return;
  }
  const uint64_t bytes = static_cast<uint64_t>(card.csd.capacity) * card.csd.sector_size;
  Serial.printf("[tfdiag] OCR=%08lX RCA=%04X CID=%.8s serial=%08lX CSD=%d sectors=%d sector_bytes=%d bytes=%llu\n",
                static_cast<unsigned long>(card.ocr), card.rca, card.cid.name,
                static_cast<unsigned long>(static_cast<uint32_t>(card.cid.serial)),
                card.csd.csd_ver, card.csd.capacity, card.csd.sector_size,
                static_cast<unsigned long long>(bytes));
}

static void readBootSector() {
  if (!cardReady || card.csd.sector_size != 512 || card.csd.capacity < 1) {
    Serial.println("[tfdiag] read skipped: card/512-byte sector geometry not ready");
    return;
  }
  auto* data = static_cast<uint8_t*>(heap_caps_malloc(512, MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA));
  if (!data) {
    Serial.println("[tfdiag] internal DMA allocation failed");
    return;
  }
  for (unsigned attempt = 0; attempt < 2; ++attempt) {
    memset(data, attempt ? 0x5A : 0xA5, 512);
    const esp_err_t result = sdmmc_read_sectors(&card, data, 0, 1);
    Serial.printf("[tfdiag] direct LBA0 attempt=%u result=%s (0x%X)\n",
                  attempt + 1, esp_err_to_name(result), static_cast<unsigned>(result));
    if (result != ESP_OK) continue;
    unsigned nonzero = 0;
    uint32_t hash = 2166136261UL;
    for (size_t i = 0; i < 512; ++i) {
      nonzero += data[i] != 0;
      hash = (hash ^ data[i]) * 16777619UL;
    }
    Serial.printf("[tfdiag] jump=%02X-%02X-%02X signature=%02X-%02X nonzero=%u fnv1a=%08lX\n",
                  data[0], data[1], data[2], data[510], data[511], nonzero,
                  static_cast<unsigned long>(hash));
  }
  free(data);
}

void setup() {
  Serial.begin(115200);
  delay(TF_WAIT_MS);
  Serial.printf("\n[tfdiag] build=%s %s wait_ms=%u uptime_ms=%lu IDF=%s\n",
                __DATE__, __TIME__, static_cast<unsigned>(TF_WAIT_MS),
                static_cast<unsigned long>(millis()), esp_get_idf_version());
  Serial.println("[tfdiag] READ ONLY; 4-bit; max=20000 kHz; no filesystem or application tasks");

  sdmmc_host_t host = SDMMC_HOST_DEFAULT();
  host.flags = SDMMC_HOST_FLAG_4BIT;
  host.slot = SDMMC_HOST_SLOT_1;
  host.max_freq_khz = 20000;
  sdmmc_slot_config_t slot = SDMMC_SLOT_CONFIG_DEFAULT();
  slot.clk = static_cast<gpio_num_t>(kClock);
  slot.cmd = static_cast<gpio_num_t>(kCommand);
  slot.d0 = static_cast<gpio_num_t>(kData0);
  slot.d1 = static_cast<gpio_num_t>(kData1);
  slot.d2 = static_cast<gpio_num_t>(kData2);
  slot.d3 = static_cast<gpio_num_t>(kData3);
  slot.width = 4;
  // Use the existing five external pull-ups; keep Arduino's slot flags unchanged.
  esp_err_t result = host.init();
  Serial.printf("[tfdiag] host init=%s\n", esp_err_to_name(result));
  if (result != ESP_OK) return;
  result = sdmmc_host_init_slot(host.slot, &slot);
  Serial.printf("[tfdiag] slot init=%s\n", esp_err_to_name(result));
  if (result != ESP_OK) {
    sdmmc_host_deinit();
    return;
  }
  memset(&card, 0, sizeof(card));
  result = sdmmc_card_init(&host, &card);
  Serial.printf("[tfdiag] card init=%s (0x%X)\n", esp_err_to_name(result), static_cast<unsigned>(result));
  cardReady = result == ESP_OK;
  printCard();
  readBootSector();
  Serial.println("[tfdiag] commands: i = card metadata; b = read LBA0 twice; no write commands");
}

void loop() {
  if (Serial.available()) {
    const char command = Serial.read();
    if (command == 'i') printCard();
    if (command == 'b') readBootSector();
  }
  delay(5);
}
