#include "serial_compat.h"

SerialCompat Serial;
void SerialCompat::begin(int baud) {
  // Unbuffered newlib printf uses an extra BUFSIZ-sized stack buffer and a
  // nested formatter. Keep newline flushing without that per-call stack cost.
  static char outputBuffer[256];
  setvbuf(stdout, outputBuffer, _IOLBF, sizeof(outputBuffer));
  uart_set_baudrate(UART_NUM_0, baud);
  if (!uart_is_driver_installed(UART_NUM_0)) uart_driver_install(UART_NUM_0, 2048, 0, 0, nullptr, 0);
}
int SerialCompat::available() {
  size_t size = 0;
  uart_get_buffered_data_len(UART_NUM_0, &size);
  return static_cast<int>(size);
}
int SerialCompat::read() {
  uint8_t byte = 0;
  return uart_read_bytes(UART_NUM_0, &byte, 1, 0) == 1 ? byte : -1;
}
void setup();
void loop();
extern "C" void app_main() {
  setup();
  for (;;) loop();
}
