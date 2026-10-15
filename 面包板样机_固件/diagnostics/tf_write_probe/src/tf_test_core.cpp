#include "tf_test_core.h"
#include <cstring>
#include <limits>

namespace tftest {
static bool hex8(const char* value, uint32_t& number) {
  if (std::strlen(value) != 8) return false;
  number = 0;
  for (unsigned i = 0; i < 8; ++i) {
    char c = value[i];
    unsigned digit;
    if (c >= '0' && c <= '9') digit = c - '0';
    else if (c >= 'A' && c <= 'F') digit = c - 'A' + 10;
    else if (c >= 'a' && c <= 'f') digit = c - 'a' + 10;
    else return false;
    number = number * 16 + digit;
  }
  return true;
}
bool parse(const char* line, Request& request) {
  request = Request{};
  if (!line || std::strlen(line) >= 128) return false;
  char text[128];
  std::strcpy(text, line);
  char* tokens[5] = {};
  unsigned count = 0;
  char* pos = text;
  while (*pos) {
    if (count == 5 || *pos == ' ') return false;
    tokens[count++] = pos;
    while (*pos && *pos != ' ') {
      if (*pos < 33 || *pos > 126) return false;
      ++pos;
    }
    if (*pos) { *pos++ = 0; if (!*pos) return false; }
  }
  if (count == 1) {
    if (!std::strcmp(tokens[0], "i")) request.kind = Kind::Info;
    if (!std::strcmp(tokens[0], "b")) request.kind = Kind::Boot;
    if (!std::strcmp(tokens[0], "l")) request.kind = Kind::List;
    if (!std::strcmp(tokens[0], "help")) request.kind = Kind::Help;
    return request.kind != Kind::Invalid;
  }
  const char* run = nullptr;
  if (count == 2 && !std::strcmp(tokens[0], "VERIFY_TEST")) {
    request.kind = Kind::Verify;
    run = tokens[1];
  } else if (count == 4 && !std::strcmp(tokens[0], "WRITE_TEST")) {
    if (!hex8(tokens[1], request.serial)) return false;
    for (const char* c = tokens[2]; *c; ++c) {
      if (*c < '0' || *c > '9') return false;
      const unsigned digit = *c - '0';
      if (request.capacity > (std::numeric_limits<uint64_t>::max() - digit) / 10) return false;
      request.capacity = request.capacity * 10 + digit;
    }
    if (request.capacity == 0) return false;
    request.kind = Kind::Write;
    run = tokens[3];
  } else return false;
  uint32_t unused;
  if (!hex8(run, unused)) { request.kind = Kind::Invalid; return false; }
  for (unsigned i = 0; i < 8; ++i) {
    request.run[i] = run[i] >= 'a' && run[i] <= 'f' ? run[i] - 'a' + 'A' : run[i];
  }
  return true;
}
int LineInput::push(int ch) {
  if (ch == '\r' || ch == '\n') {
    int result = invalid_ ? -1 : size_ ? 1 : 0;
    buffer_[size_] = 0;
    size_ = 0;
    invalid_ = false;
    return result;
  }
  if (ch < 32 || ch > 126 || size_ + 1 >= sizeof(buffer_)) invalid_ = true;
  if (!invalid_) buffer_[size_++] = static_cast<char>(ch);
  return 0;
}
uint8_t pattern(uint32_t offset) {
  // Mix all offset bytes so shifted/repeated sectors are detectable.
  return static_cast<uint8_t>((offset * 37u) ^ (offset >> 8) ^
                              ((offset >> 16) * 101u) ^ 0xA5u);
}
bool cardStatusError(uint32_t status) {
  // SD memory R1 error bits: 31..19, CSD overwrite, WP erase skip,
  // erase reset, switch error and authentication sequence error.
  return (status & 0xFFF9A088u) != 0;
}
bool cardStatusReady(uint32_t status) {
  return !cardStatusError(status) && (status & (1u << 8)) && ((status >> 9) & 15) == 4;
}
struct GateScope {
  Store& store;
  explicit GateScope(Store& value) : store(value) { store.writeGate(false); }
  ~GateScope() { store.writeGate(false); }
};
Result Runner::run(Store& store, const Request& request, uint32_t serial, uint64_t capacity) {
  GateScope gate(store);
  Result out;
  uint8_t block[kBlockBytes];
  const bool priorLock = locked_;
  bool writing = request.kind == Kind::Write;
  if (!writing && request.kind != Kind::Verify) { out.phase = "command"; return out; }
  uint32_t runId;
  if (!hex8(request.run, runId)) { out.phase = "run_id"; return out; }
  if (writing) {
    if (locked_) { out.phase = "write_locked_until_reboot"; return out; }
    if (request.serial != serial || request.capacity != capacity) {
      out.phase = "card_identity"; return out;
    }
    Presence presence = store.directory(request.run);
    if (presence != Presence::Missing) {
      out.phase = presence == Presence::Exists ? "directory_exists" : "directory_stat";
      if (presence == Presence::Error) locked_ = true;
      return out;
    }
    // Latch before the first mutation; clear only after the entire workflow passes.
    locked_ = true;
    store.writeGate(true);
    out.phase = "mkdir";
    if (!store.makeDirectory(request.run)) return out;
    out.phase = "create_new";
    if (!store.openNew(request.run)) return out;
    bool good = true;
    out.phase = "write";
    while (out.written < kFileBytes) {
      for (size_t i = 0; i < sizeof(block); ++i) block[i] = pattern(out.written + i);
      size_t got = 0;
      bool ok = store.write(block, sizeof(block), got);
      if (got <= sizeof(block)) out.written += static_cast<uint32_t>(got);
      if (!ok || got != sizeof(block)) { good = false; break; }
    }
    if (good) { out.phase = "sync"; good = store.sync(); }
    // Always attempt close while writes are enabled; preserve the first error.
    bool closed = store.close();
    if (good && !closed) { out.phase = "close_write"; good = false; }
    store.writeGate(false);
    if (!good) return out;
  }
  locked_ = true;
  out.phase = "reopen";
  if (!store.openRead(request.run)) return out;
  bool good = true;
  out.phase = "size";
  if (store.size() != kFileBytes) good = false;
  if (good) { out.phase = "hash_begin"; good = store.hashBegin(); }
  while (good && out.verified < kFileBytes) {
    out.phase = "read";
    size_t got = 0;
    if (!store.read(block, sizeof(block), got) || got != sizeof(block)) { good = false; break; }
    out.phase = "content";
    for (size_t i = 0; i < got; ++i) {
      if (block[i] != pattern(out.verified + i)) { good = false; break; }
    }
    if (!good) break;
    out.phase = "hash_add";
    if (!store.hashAdd(block, got)) { good = false; break; }
    out.verified += got;
  }
  if (good) { out.phase = "hash_end"; good = store.hashEnd(out.sha256); }
  if (good) { out.phase = "sha256"; good = std::strcmp(out.sha256, kExpectedSha256) == 0; }
  bool closed = store.close();
  if (good && !closed) { out.phase = "close_read"; good = false; }
  out.passed = good;
  if (good) {
    out.phase = writing ? "write_sync_close_readback" : "read_only_verify";
    locked_ = writing ? false : priorLock;
  }
  return out;
}
}  // namespace tftest
