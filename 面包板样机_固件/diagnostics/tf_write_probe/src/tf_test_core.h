#pragma once
#include <cstddef>
#include <cstdint>

namespace tftest {
constexpr uint32_t kFileBytes = 1024 * 1024;
constexpr size_t kBlockBytes = 4096;
constexpr char kExpectedSha256[] = "7c79a64ca466f57076b4fa369dd7861cf4657c7b00ed8c1bad0c9c9336cc87b7";
enum class Kind { Invalid, Info, Boot, List, Help, Write, Verify };
struct Request {
  Kind kind = Kind::Invalid;
  uint32_t serial = 0;
  uint64_t capacity = 0;
  char run[9] = {};
};
bool parse(const char* line, Request& request);
class LineInput {
 public:
  // 0: incomplete/empty, 1: complete, -1: rejected whole line.
  int push(int ch);
  const char* line() const { return buffer_; }
 private:
  char buffer_[128] = {};
  size_t size_ = 0;
  bool invalid_ = false;
};
uint8_t pattern(uint32_t offset);
enum class Presence { Missing, Exists, Error };
// Adapter must log concrete filesystem results. No removal or format methods.
struct Store {
  virtual ~Store() = default;
  virtual void writeGate(bool enabled) = 0;
  virtual Presence directory(const char* run) = 0;
  virtual bool makeDirectory(const char* run) = 0;
  virtual bool openNew(const char* run) = 0;
  virtual bool openRead(const char* run) = 0;
  virtual uint32_t size() = 0;
  virtual bool write(const uint8_t* data, size_t length, size_t& written) = 0;
  virtual bool sync() = 0;
  virtual bool close() = 0;
  virtual bool read(uint8_t* data, size_t length, size_t& got) = 0;
  virtual bool hashBegin() = 0;
  virtual bool hashAdd(const uint8_t* data, size_t length) = 0;
  virtual bool hashEnd(char* hex) = 0;
};
struct Result {
  bool passed = false;
  const char* phase = "not_started";
  uint32_t written = 0;
  uint32_t verified = 0;
  char sha256[65] = {};
};
// Failure locks future writes for this boot; only a fresh boot clears the latch.
class Runner {
 public:
  Result run(Store& store, const Request& request, uint32_t serial, uint64_t capacity);
  bool locked() const { return locked_; }
 private:
  bool locked_ = false;
};
bool cardStatusError(uint32_t status);
bool cardStatusReady(uint32_t status);
}  // namespace tftest
