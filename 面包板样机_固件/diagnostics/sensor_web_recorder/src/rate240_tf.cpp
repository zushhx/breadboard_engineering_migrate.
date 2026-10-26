#include "rate240_tf.h"
#include "rate240_core.h"
#include "rate190_stream.h"
#include "record_request.h"
#include "serial_compat.h"
#include "tf_test_core.h"
#include "web_control.h"
#include "device_v2.h"
#include "esp_heap_caps.h"
#include "storage_space.h"
#include "ff.h"
#include "esp_timer.h"
#include "mbedtls/sha256.h"
#include <cstring>

namespace rate240tf {
struct Workspace { FIL file; uint8_t data[rate240core::blockBytes], readBuffer[4096]; };
struct StorageClaim {
  bool held=control::reserveFiles();
  ~StorageClaim(){if(held)control::releaseFiles();}
};
struct BufferClaim {
  Workspace* p=nullptr;
  BufferClaim(){if(heap_caps_get_free_size(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT)>sizeof(Workspace)+16384)
    p=static_cast<Workspace*>(heap_caps_calloc(1,sizeof(Workspace),MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT));}
  ~BufferClaim(){heap_caps_free(p);}
};
static uint64_t sessionWritten;
static uint64_t now() { return esp_timer_get_time(); }
static void hex(const uint8_t* bytes, char* text) {
  for (unsigned i=0;i<32;++i) snprintf(text+i*2,3,"%02x",bytes[i]);
}
void command(const char* line, const recorder::Store& store) {
  // Reuse the strict identity/run/duration grammar; never accept an arbitrary path.
  constexpr const char* prefix="RATE240_TF ";
  if (strncmp(line,prefix,11)) return;
  char requestLine[100];
  int n=snprintf(requestLine,sizeof(requestLine),"REC_START %s",line+11);
  recorder::Request req={};
  if(n<0 || n>=int(sizeof(requestLine)) || !recorder::parseRequest(requestLine,req) ||
      !req.write || req.serial!=store.serial || req.capacity!=store.capacity) {
    Serial.println("[rate240tf] rejected identity/range/grammar; no writes"); return;
  }
  StorageClaim claim;
  if(!claim.held){Serial.println("[rate240tf] rejected storage/session busy; no writes");return;}
  BufferClaim memory;
  if(!memory.p){Serial.println("[rate240tf] rejected heap headroom; no writes");return;}
  FIL& file=memory.p->file; auto& data=memory.p->data; auto& readBuffer=memory.p->readBuffer;
  if(!control::begin(req.run,req.seconds)) { Serial.println("[rate240tf] rejected busy/locked"); return; }
  rate190::Recording mirror(req.run,req.seconds,"pattern");
  control::phase(control::Phase::Preparing,"240 kB/s 合成写卡测试，非真实振动采样");
  char directory[40],path[56];
  snprintf(directory,sizeof(directory),"%s/RATE_%s",store.drive,req.run);
  snprintf(path,sizeof(path),"%s/DATA.BIN",directory);
  FILINFO info; DWORD clusters=0; FATFS* fs=nullptr; uint64_t freeBytes=0;
  const uint64_t total=uint64_t(req.seconds)*rate240core::rate;
  bool space=f_getfree(store.drive,&clusters,&fs)==FR_OK && fs &&
      control::freeBytesFromClusters(clusters,fs->csize,store.capacity,freeBytes);
  if(f_stat(directory,&info)!=FR_NO_FILE || !space || freeBytes<total+536870912ull ||
      sessionWritten+total>1073741824ull || control::stopping()) {
    control::result(false,false,"目录、容量或取消门槛未通过；未写卡"); return;
  }
  mbedtls_sha256_context hash; mbedtls_sha256_init(&hash);
  bool ok=mbedtls_sha256_starts_ret(&hash,0)==0;
  // The only write gate is below an explicit identity-bound RATE240_TF command.
  bool opened=false;
  if(ok) {
    store.gate(true);
    ok=f_mkdir(directory)==FR_OK;
    if(ok) { opened=f_open(&file,path,FA_WRITE|FA_CREATE_NEW)==FR_OK; ok=opened; }
  }
  const uint64_t begin=now(); mirror.start(begin); uint64_t written=0,backlogHigh=0;
  uint64_t nextSync=begin+1000000; uint32_t maxWrite=0,maxSync=0;
  tftest::LineInput input;
  control::phase(control::Phase::Recording,"240 kB/s 合成测试中；网络仅复制已写入字节");
  while(ok && written<total) {
    while(Serial.available()) {
      int event=input.push(Serial.read());
      if(event>0 && !strcmp(input.line(),"RATE240_STOP")) ok=false;
    }
    if(control::stopping()) ok=false;
    const uint64_t elapsed=now()-begin;
    const uint64_t produced=rate240core::due(elapsed,total);
    const uint64_t backlog=produced>written?produced-written:0;
    if(backlog>backlogHigh)backlogHigh=backlog;
    if(backlog>rate240core::backlogLimit || elapsed>uint64_t(req.seconds)*1000000+500000)ok=false;
    if(!ok)break;
    if(produced-written<sizeof(data)) { delay(1); continue; }
    for(unsigned i=0;i<sizeof(data);++i)data[i]=rate240core::pattern(written+i);
    UINT count=0;uint64_t before=now();
    ok=f_write(&file,data,sizeof(data),&count)==FR_OK && count==sizeof(data);
    uint32_t took=now()-before;if(took>maxWrite)maxWrite=took;
    if(ok)ok=mbedtls_sha256_update_ret(&hash,data,sizeof(data))==0;
    if(ok){written+=count;sessionWritten+=count;rate190::tee(data,count);}
    if(ok && now()>=nextSync){before=now();ok=f_sync(&file)==FR_OK;
      took=now()-before;if(took>maxSync)maxSync=took;nextSync=now()+1000000;}
    delay(1); // Service the idle task and HTTP stop control during catch-up.
  }
  control::phase(control::Phase::Closing,"同步关闭合成文件；尚未验收");
  if(opened){uint64_t before=now();bool synced=f_sync(&file)==FR_OK;
    uint32_t took=now()-before;if(took>maxSync)maxSync=took;
    bool closed=f_close(&file)==FR_OK;ok=ok&&synced&&closed;}
  store.gate(false);
  const uint64_t elapsed=now()-begin;
  ok=ok && rate240core::cadenceAccepted(written,req.seconds,elapsed);
  mirror.stop();
  uint8_t digest[32]={};char expected[65]={},actual[65]={};
  ok=mbedtls_sha256_finish_ret(&hash,digest)==0&&ok;hex(digest,expected);mbedtls_sha256_free(&hash);
  control::phase(control::Phase::Verifying,"完整回读合成文件并核对模式及SHA；不修复文件");
  bool readOk=opened && f_open(&file,path,FA_READ|FA_OPEN_EXISTING)==FR_OK;
  const bool reading=readOk;uint64_t checked=0;
  mbedtls_sha256_init(&hash);
  if(mbedtls_sha256_starts_ret(&hash,0)!=0)readOk=false;
  while(readOk){UINT got=0;if(f_read(&file,readBuffer,sizeof(readBuffer),&got)!=FR_OK){readOk=false;break;}
    if(!got)break;
    if(checked+got>total){readOk=false;break;}
    for(unsigned i=0;i<got;++i)if(readBuffer[i]!=rate240core::pattern(checked+i)){readOk=false;break;}
    if(mbedtls_sha256_update_ret(&hash,readBuffer,got)!=0)readOk=false;
    checked+=got;delay(1);}
  if(reading)readOk=f_close(&file)==FR_OK&&readOk;
  if(mbedtls_sha256_finish_ret(&hash,digest)!=0)readOk=false;
  hex(digest,actual);mbedtls_sha256_free(&hash);
  ok=ok&&readOk&&checked==total&&!strcmp(expected,actual);
  mirror.finish(ok,actual);
  control::result(ok,!ok,ok?"240 kB/s合成TF写入、同步关闭及完整回读通过；WiFi以电脑结果为准":"合成测试失败；保留现场，不自动重试",actual);
  Serial.printf("[rate240tf] %s run=%s bytes=%llu elapsed_us=%llu backlog_high_bytes=%llu write_us=%u sync_us=%u sha256=%s synthetic=1 write_gate=0\n",
    ok?"PASS":"FAIL",req.run,(unsigned long long)written,(unsigned long long)elapsed,
    (unsigned long long)backlogHigh,(unsigned)maxWrite,(unsigned)maxSync,actual);
}
}
