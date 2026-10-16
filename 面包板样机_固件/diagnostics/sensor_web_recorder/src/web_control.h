#pragma once
#include "web_model.h"
namespace control {
void initialize(bool ready, uint32_t serial, uint64_t capacity);
void startNetwork();
void pollNetworkLog();
bool take(char* run, unsigned& seconds);
bool begin(const char* run, unsigned seconds);
void phase(Phase value, const char* detail);
bool stopping();
void result(bool passed, bool locked, const char* detail, const char* sha = "");
void progress(uint64_t written, uint64_t elapsed, uint32_t errors, uint32_t queue,
              uint32_t writeUs, uint32_t syncUs);
void preview(const uint8_t* frames, unsigned count, uint64_t firstUs);
void ina(uint64_t timeUs, bool valid, int32_t busMv, int32_t currentUa, int32_t powerUw);
void finalMetrics(uint32_t fifoHigh, uint32_t queueHigh);
}
