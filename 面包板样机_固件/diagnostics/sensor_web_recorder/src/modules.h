#pragma once
#include "module_values.h"
namespace modules {
void beginReadOnly();
bool command(const char* line);
bool prepareRecord();
bool startFifo();
bool stopInput();
bool finishFifo();
bool fifoStatus(uint16_t& count, bool& overrun);
bool fifoFrame(uint8_t* sevenBytes);
bool monitorRead(InaRegisters& values, bool& valid);
unsigned monitorAddressValue();
}
