// Executes the same core used by the firmware; no serial or device dependency.
#include "tf_test_core.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <initializer_list>
using namespace tftest;
#define CHECK(test) do { if (!(test)) { std::fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #test); std::exit(1); } } while (0)
static uint8_t contents[kFileBytes];
struct FakeStore final : Store {
  const char* fault = "";
  bool gate = false;
  bool enabled = false;
  bool opened = false;
  bool reading = false;
  uint32_t length = 0;
  uint32_t cursor = 0;
  unsigned mutationCalls = 0;
  unsigned writeCalls = 0;
  unsigned closeCalls = 0;
  unsigned syncCalls = 0;
  bool is(const char* value) const { return std::strcmp(fault, value) == 0; }
  void writeGate(bool value) override { gate = value; enabled |= value; }
  Presence directory(const char*) override {
    CHECK(!gate);
    return is("exists") ? Presence::Exists : is("stat") ? Presence::Error : Presence::Missing;
  }
  bool makeDirectory(const char*) override { CHECK(gate); ++mutationCalls; return !is("mkdir"); }
  bool openNew(const char*) override {
    CHECK(gate); ++mutationCalls;
    if (is("create")) return false;
    opened = true; reading = false; length = 0; return true;
  }
  bool openRead(const char*) override {
    CHECK(!gate); CHECK(!opened || is("close_write"));
    if (is("reopen")) return false;
    opened = true; reading = true; cursor = 0; return true;
  }
  uint32_t size() override { return is("size") ? length - 1 : length; }
  bool write(const uint8_t* data, size_t count, size_t& written) override {
    CHECK(gate && opened && !reading); ++writeCalls;
    written = is("short_write") ? count - 1 : count;
    if (is("write")) { written = 0; return false; }
    CHECK(length + written <= sizeof(contents));
    std::memcpy(contents + length, data, written); length += written;
    return true;
  }
  bool sync() override { CHECK(gate && opened); ++syncCalls; return !is("sync"); }
  bool close() override {
    CHECK(opened); CHECK(reading ? !gate : gate); ++closeCalls;
    bool ok = !(reading ? is("close_read") : is("close_write"));
    opened = false; return ok;
  }
  bool read(uint8_t* data, size_t count, size_t& got) override {
    CHECK(!gate && opened && reading);
    got = is("short_read") ? count - 1 : count;
    if (is("read")) return false;
    std::memcpy(data, contents + cursor, got);
    if (is("content") && cursor == 0) data[100] ^= 1;
    cursor += got; return true;
  }
  bool hashBegin() override { return !is("hash_begin"); }
  bool hashAdd(const uint8_t*, size_t) override { return !is("hash_add"); }
  bool hashEnd(char* text) override {
    std::strcpy(text, is("wrong_hash") ? "incorrect-digest" : kExpectedSha256);
    return !is("hash_end");
  }
  void seed() {
    length = sizeof(contents);
    for (uint32_t i = 0; i < length; ++i) contents[i] = pattern(i);
  }
};
static Request writeRequest() {
  Request request;
  CHECK(parse("WRITE_TEST 10044BA5 7948206080 A1B2C3D4", request));
  return request;
}
static Result execute(Runner& runner, FakeStore& store, Request request = writeRequest()) {
  return runner.run(store, request, 0x10044BA5, 7948206080ULL);
}
static void parserTests() {
  const char* rejected[] = {"", "w", "WRITE_TEST", "WRITE_TEST 10044BA5 0 A1B2C3D4",
    "WRITE_TEST 10044BA5 -1 A1B2C3D4", "WRITE_TEST 10044BA5 18446744073709551616 A1B2C3D4",
    "WRITE_TEST 10044BA5 7948206080 ../abcde", "WRITE_TEST 10044BA5 7948206080 1234567G",
    "WRITE_TEST 10044BA5 7948206080 A1B2C3D4 extra", "WRITE_TEST 10044BA5 7948206080 A1B2C3D4 ",
    "WRITE_TEST 10044BA5  7948206080 A1B2C3D4", "VERIFY_TEST A1B2C3D4 extra", "i l", " i", "i\t", "i\n"};
  Request request;
  for (auto text : rejected) CHECK(!parse(text, request));
  CHECK(parse("VERIFY_TEST a1b2c3d4", request));
  CHECK(request.kind == Kind::Verify && !std::strcmp(request.run, "A1B2C3D4"));
  CHECK(parse("i", request) && request.kind == Kind::Info);
  CHECK(parse("b", request) && request.kind == Kind::Boot);
  CHECK(parse("l", request) && request.kind == Kind::List);
  CHECK(parse("help", request) && request.kind == Kind::Help);
  LineInput input;
  CHECK(input.push('i') == 0); CHECK(input.push('\r') == 1);
  CHECK(!std::strcmp(input.line(), "i")); CHECK(input.push('\n') == 0);
  for (int i = 0; i < 200; ++i) CHECK(input.push('A') == 0);
  CHECK(input.push('\n') == -1);
  CHECK(input.push('i') == 0); CHECK(input.push(0) == 0); CHECK(input.push('\n') == -1);
  CHECK(input.push('l') == 0); CHECK(input.push('\n') == 1); CHECK(!std::strcmp(input.line(), "l"));
}
static void runnerTests() {
  { FakeStore store; Runner runner;
    Result result = execute(runner, store);
    CHECK(result.passed && result.written == kFileBytes && result.verified == kFileBytes);
    CHECK(!store.gate && store.enabled && store.syncCalls == 1 && store.closeCalls == 2 && !runner.locked());
    CHECK(!std::strcmp(result.phase, "write_sync_close_readback"));
  }
  { FakeStore store; Runner runner; Request request = writeRequest(); request.serial ^= 1;
    CHECK(!execute(runner, store, request).passed); CHECK(!store.enabled && store.mutationCalls == 0);
    request = writeRequest(); request.capacity += 512;
    CHECK(!execute(runner, store, request).passed); CHECK(!store.enabled && store.mutationCalls == 0);
    request = Request{};
    CHECK(!execute(runner, store, request).passed); CHECK(!store.enabled);
  }
  for (const char* fault : {"exists", "stat"}) {
    FakeStore store; Runner runner; store.fault = fault;
    CHECK(!execute(runner, store).passed); CHECK(!store.enabled && store.mutationCalls == 0);
    CHECK(runner.locked() == !std::strcmp(fault, "stat"));
  }
  const char* faults[] = {"mkdir", "create", "short_write", "write", "sync", "close_write",
    "reopen", "size", "content", "short_read", "read", "close_read", "hash_begin", "hash_add", "hash_end", "wrong_hash"};
  for (const char* fault : faults) {
    FakeStore store; Runner runner; store.fault = fault;
    Result result = execute(runner, store);
    CHECK(!result.passed && !store.gate && runner.locked());
    if (!std::strcmp(fault, "short_write")) CHECK(result.written == kBlockBytes - 1);
    if (!std::strcmp(fault, "write")) CHECK(result.written == 0);
    unsigned mutations = store.mutationCalls;
    CHECK(!execute(runner, store).passed); CHECK(store.mutationCalls == mutations && !store.gate);
    if (!std::strcmp(fault, "write") || !std::strcmp(fault, "short_write")) CHECK(store.writeCalls == 1);
  }
  { FakeStore store; Runner runner; store.seed(); Request request;
    CHECK(parse("VERIFY_TEST A1B2C3D4", request));
    Result result = execute(runner, store, request);
    CHECK(result.passed && result.written == 0 && result.verified == kFileBytes);
    CHECK(!store.enabled && !store.gate && store.mutationCalls == 0 && !runner.locked());
    store.fault = "content";
    CHECK(!execute(runner, store, request).passed && runner.locked());
    store.fault = "";
    CHECK(execute(runner, store, request).passed && runner.locked()); // verify never unlocks a failed boot
    CHECK(!execute(runner, store).passed && !store.enabled);
  }
}
int main(int argc, char** argv) {
  if (argc == 3 && !std::strcmp(argv[1], "--payload")) {
    for (uint32_t i = 0; i < sizeof(contents); ++i) contents[i] = pattern(i);
    FILE* file = std::fopen(argv[2], "wb");
    CHECK(file);
    CHECK(std::fwrite(contents, 1, sizeof(contents), file) == sizeof(contents));
    CHECK(std::fclose(file) == 0);
    return 0;
  }
  parserTests(); runnerTests();
  CHECK(cardStatusReady(0x900));
  CHECK(!cardStatusReady(0x800)); // TRAN but not READY_FOR_DATA
  CHECK(!cardStatusReady(0xF00)); // programming state
  CHECK(!cardStatusReady(0x900 | (1u << 19)));
  CHECK(cardStatusError(1u << 26));
  CHECK(!cardStatusError(0x900));
  std::puts("PASS: parser, identity, no-overwrite, write/sync/close/read faults, gate closure, failure latch, read-only verification and R1 checks");
}
