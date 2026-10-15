#pragma once
#include <cstdio>
#include <cstdint>
#include "driver/uart.h"
#include "esp_idf_version.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "mbedtls/version.h"
#if MBEDTLS_VERSION_MAJOR >= 3
#define mbedtls_sha256_starts_ret mbedtls_sha256_starts
#define mbedtls_sha256_update_ret mbedtls_sha256_update
#define mbedtls_sha256_finish_ret mbedtls_sha256_finish
#endif

struct SerialCompat {
  void begin(int baud);
  void println(const char* text) { std::puts(text); }
  template<typename... Args> int printf(const char* format, Args... args) { return std::printf(format, args...); }
  int available();
  int read();
};
extern SerialCompat Serial;
inline void delay(unsigned ms) { vTaskDelay(pdMS_TO_TICKS(ms ? ms : 1)); }
