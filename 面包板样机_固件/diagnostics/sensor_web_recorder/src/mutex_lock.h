#pragma once
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
namespace devicev2 {
// One shared definition: each guard releases exactly the handle that it acquired.
class MutexLock {
  SemaphoreHandle_t handle;
public:
  explicit MutexLock(SemaphoreHandle_t value):handle(value) {xSemaphoreTake(handle,portMAX_DELAY);}
  ~MutexLock() {xSemaphoreGive(handle);}
  MutexLock(const MutexLock&)=delete;
  MutexLock& operator=(const MutexLock&)=delete;
};
}
