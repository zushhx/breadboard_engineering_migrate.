#include "rate190_stream.h"
#include "rate240_stream_core.h"
#include "device_v2.h"
#include "serial_compat.h"
#include "build_identity.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/sockets.h"
#include <cstring>
#include <cstdio>
#include <cstdint>
#include <cerrno>

namespace rate190 {
constexpr unsigned fifoBytes=25600, payload=3200;
constexpr uint64_t byteLimit=80000000;
struct Item { uint32_t size; uint8_t data[payload]; };
static uint8_t* ring;
static rate240stream::ByteFifo fifo;
static portMUX_TYPE mux=portMUX_INITIALIZER_UNLOCKED;
struct Guard { Guard(){portENTER_CRITICAL(&mux);} ~Guard(){portEXIT_CRITICAL(&mux);} };
static char secret[33], runName[9], sha[65], sourceFormat[8];
static char expectedRun[9], expectedFormat[8];
static bool armed, active, finished, sourceOk;
static bool serviceStarted, serviceReady, serviceCancelled;
static unsigned serviceGeneration;
static unsigned high, requested, epoch;
static uint32_t errors;
static uint64_t input, dropped, started, stopped;
// Error bits: 1 transport,2 queue,4 bounds,8 timeout,16 source/early-return.
static uint64_t now(){return esp_timer_get_time();}
Recording::Recording(const char* run,unsigned seconds,const char* format){
  Guard g;
  if(!armed) return;
  armed=false;
  if(seconds!=requested || strcmp(run,expectedRun) || strcmp(format,expectedFormat)){
    errors|=4;finished=true;return;
  }
  snprintf(sourceFormat,sizeof(sourceFormat),"%s",format);
  active=true; generation=epoch; started=now(); snprintf(runName,sizeof(runName),"%s",run);
}
void Recording::start(uint64_t timeUs){Guard g;if(generation&&generation==epoch&&active)started=timeUs;}
void Recording::stop(){Guard g;if(generation&&generation==epoch)stopped=now();}
void Recording::finish(bool ok,const char* digest){
  Guard g;
  if(!generation||generation!=epoch||!active) return;
  active=false; finished=true; if(!stopped)stopped=now(); sourceOk=ok;
  if(!ok)errors|=16;
  snprintf(sha,sizeof(sha),"%s",digest);
}
Recording::~Recording(){finish(false,"");}
void tee(const void* data,size_t bytes){
  Guard g;
  if(!active) return;
  input+=bytes;
  if(bytes>payload || input>byteLimit){errors|=4;dropped+=bytes;return;}
  if(!fifo.push(data,bytes)){errors|=2;dropped+=bytes;return;}
  if(fifo.size()>high)high=fifo.size();
}
struct SendFailure {
  struct Sample {
    uint64_t beginUs, endUs, sampleUs;
    uint32_t type, sequence;
    const char* part;
    size_t requested, freeBefore, largestBefore, freeAfter, largestAfter;
    int result, error;
  };
  static constexpr unsigned traceCapacity=8;
  Sample trace[traceCapacity]={};
  unsigned traceCount=0, traceNext=0, traceTotal=0;
  void retain(const Sample& sample){
    trace[traceNext]=sample;traceNext=(traceNext+1)%traceCapacity;
    if(traceCount<traceCapacity)++traceCount;
    ++traceTotal;
  }
  bool captured=false;
  const char* reason=nullptr;
  const char* part=nullptr;
  int error=0, result=0;
  uint32_t type=0, sequence=0;
  size_t requested=0, accepted=0, attemptRequested=0, queued=0;
  unsigned queueHigh=0, calls=0;
  uint64_t lastReturnUs=0,lastSendUs=0,maxSendUs=0,maxGapUs=0;
  int lastSendResult=0;size_t lastSendRequested=0;
  uint64_t stamp=0, attemptUs=0, totalUs=0, sourceInput=0, payloadSent=0, sourceDropped=0;
};
static void reportSendSamples(const SendFailure& failure){
  for(unsigned i=0;i<failure.traceCount;++i){
    const auto& s=failure.trace[(failure.traceNext+SendFailure::traceCapacity-failure.traceCount+i)%SendFailure::traceCapacity];
    Serial.printf("[rate240] SEND_SAMPLE total=%u retained=%u index=%u begin_us=%llu end_us=%llu sample_us=%llu type=%u seq=%u part=%s requested=%u result=%d errno=%d free_before=%u largest_before=%u free_after=%u largest_after=%u\n",
      failure.traceTotal,failure.traceCount,i,(unsigned long long)s.beginUs,(unsigned long long)s.endUs,
      (unsigned long long)s.sampleUs,s.type,s.sequence,s.part,unsigned(s.requested),s.result,s.error,
      unsigned(s.freeBefore),unsigned(s.largestBefore),unsigned(s.freeAfter),unsigned(s.largestAfter));
  }
}
static void captureSendFailure(SendFailure& failure,const char* reason,int error,int result,
                              uint32_t type,uint32_t sequence,const char* part,size_t requested,
                              size_t accepted,size_t attemptRequested,uint64_t attemptStart,
                              uint64_t begin,uint64_t payloadSent){
  if(failure.captured)return;
  const uint64_t stamp=now();
  failure.captured=true;failure.reason=reason;failure.error=error;failure.result=result;
  failure.type=type;failure.sequence=sequence;failure.part=part;
  failure.requested=requested;failure.accepted=accepted;failure.attemptRequested=attemptRequested;
  failure.stamp=stamp;failure.attemptUs=!strcmp(reason,"deadline")?0:stamp-attemptStart;
  failure.totalUs=stamp-begin;
  failure.payloadSent=payloadSent;
  {Guard g;failure.sourceInput=input;failure.sourceDropped=dropped;
    failure.queued=fifo.size();failure.queueHigh=high;}
}
static void reportSendFailure(const SendFailure& failure){
  if(!failure.captured)return;
  // Called after the failed connection is closed, never while holding the FIFO lock.
  // errno is meaningful only for a negative send result; zero/deadline record zero.
  Serial.printf("[rate240] TX_FAIL us=%llu reason=%s errno=%d result=%d type=%u seq=%u part=%s requested=%u accepted=%u attempt_requested=%u attempt_us=%llu total_us=%llu input=%llu sent=%llu dropped=%llu queue=%u queue_high=%u calls=%u last_send_us=%llu max_send_us=%llu max_gap_us=%llu last_result=%d last_requested=%u\n",
    (unsigned long long)failure.stamp,failure.reason,failure.error,failure.result,
    (unsigned)failure.type,(unsigned)failure.sequence,failure.part,
    (unsigned)failure.requested,(unsigned)failure.accepted,(unsigned)failure.attemptRequested,
    (unsigned long long)failure.attemptUs,(unsigned long long)failure.totalUs,
    (unsigned long long)failure.sourceInput,(unsigned long long)failure.payloadSent,
    (unsigned long long)failure.sourceDropped,(unsigned)failure.queued,failure.queueHigh,
    failure.calls,(unsigned long long)failure.lastSendUs,(unsigned long long)failure.maxSendUs,
    (unsigned long long)failure.maxGapUs,failure.lastSendResult,(unsigned)failure.lastSendRequested);
}
static bool sendAll(int fd,const void* data,size_t n,SendFailure& failure,uint32_t type,
                    uint32_t sequence,const char* part,uint64_t payloadSent){
  const auto* p=static_cast<const uint8_t*>(data);
  const size_t requested=n;
  const uint64_t begin=now(),deadline=begin+250000;
  while(n){
    const uint64_t attemptStart=now();
    if(failure.lastReturnUs && attemptStart-failure.lastReturnUs>failure.maxGapUs)
      failure.maxGapUs=attemptStart-failure.lastReturnUs;
    if(attemptStart>deadline){
      captureSendFailure(failure,"deadline",0,0,type,sequence,part,requested,requested-n,n,
                         attemptStart,begin,payloadSent);return false;
    }
    // Heap walks add observation overhead. Each free/largest pair is sequential,
    // not an atomic snapshot; retain sampling cost separately from send duration.
    constexpr unsigned caps=MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT;
    const uint64_t sampleStart=now();
    const size_t freeBefore=heap_caps_get_free_size(caps);
    const size_t largestBefore=heap_caps_get_largest_free_block(caps);
    const uint64_t sendStart=now();
    int sent=send(fd,p,n,0);
    const int error=sent<0?errno:0; // Capture before any timing/logging/API call.
    const uint64_t returned=now();
    const size_t freeAfter=heap_caps_get_free_size(caps);
    const size_t largestAfter=heap_caps_get_largest_free_block(caps);
    const uint64_t sampleEnd=now();
    if(sent<=0 || returned-sendStart>=20000)
      failure.retain({sendStart,returned,(sendStart-sampleStart)+(sampleEnd-returned),
        type,sequence,part,n,freeBefore,largestBefore,freeAfter,largestAfter,sent,error});
    ++failure.calls;failure.lastReturnUs=returned;failure.lastSendUs=returned-sendStart;
    if(failure.lastSendUs>failure.maxSendUs)failure.maxSendUs=failure.lastSendUs;
    failure.lastSendResult=sent;failure.lastSendRequested=n;
    if(sent<=0){
      captureSendFailure(failure,sent<0?"negative":"zero",error,sent,type,sequence,part,
                         requested,requested-n,n,attemptStart,begin,payloadSent);
      // Keep call duration separate from pre/post heap observation overhead.
      failure.attemptUs=returned-sendStart;return false;
    }
    p+=sent;n-=sent;
  }
  return true;
}
static bool frame(int fd,uint32_t type,uint32_t& sequence,const void* data,uint32_t n,
                  SendFailure& failure,uint64_t payloadSent){
  uint32_t header[4]={0x30343252u,type,sequence++,n};
  return sendAll(fd,header,sizeof(header),failure,type,header[2],"header",payloadSent)&&
         sendAll(fd,data,n,failure,type,header[2],"payload",payloadSent);
}
struct SessionMemory {
  ~SessionMemory(){
    uint8_t* allocation;
    {Guard g; armed=false;active=false;allocation=ring;ring=nullptr;fifo.reset(nullptr,0);}
    heap_caps_free(allocation);
  }
};
struct Admission {
  bool held=control::reserveFiles();
  void release(){if(held){control::releaseFiles();held=false;}}
  ~Admission(){release();}
};
static void session(int fd,SendFailure& failure){
  SessionMemory memory;
  char line[100]={}, token[33]={}, mode[8]={}, extra;unsigned seconds=0,used=0;
  bool newline=false;uint64_t deadline=now()+3000000;
  while(used<sizeof(line)-1 && now()<deadline){
    char ch;int got=recv(fd,&ch,1,0);if(got<=0)return;
    if(ch=='\n'){newline=true;break;}
    if(ch=='\r'||ch==0)return;
    line[used++]=ch;
  }
  if(!newline)return;
  bool bound=sscanf(line,"BENCH2 %32s %7s %u %8s %7s %c",token,mode,&seconds,expectedRun,expectedFormat,&extra)==5;
  if(!bound && sscanf(line,"BENCH1 %32s %7s %u %c",token,mode,&seconds,&extra)!=3)return;
  if(strcmp(token,secret)||seconds<1||seconds>300)return;
  bool synth=!strcmp(mode,"SYNTH");if(!synth&&strcmp(mode,"MIRROR"))return;
  // Recheck admission after the bounded accept/auth wait, not only at serial ARM.
  Admission admission;
  if(!admission.held)return;
  if(!synth){
    if(!bound || strlen(expectedRun)!=8 || strspn(expectedRun,"0123456789ABCDEF")!=8 ||
       (strcmp(expectedFormat,"pattern")&&strcmp(expectedFormat,"record")))return;
    if(heap_caps_get_free_size(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT)<fifoBytes+16384)return;
    ring=static_cast<uint8_t*>(heap_caps_malloc(fifoBytes,MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT));if(!ring)return;
  }
  {Guard g;armed=!synth;active=false;finished=false;sourceOk=false;fifo.reset(ring,ring?fifoBytes:0);high=0;
    ++epoch;requested=seconds;errors=0;input=dropped=started=stopped=0;sha[0]=runName[0]=sourceFormat[0]=0;}
  uint32_t sequence=0;uint64_t sent=0;bool transport=true;
  char ready[384];
  snprintf(ready,sizeof(ready),"{\"mode\":\"%s\",\"armed\":true,\"rate_bps\":240000,\"seconds\":%u,\"format\":\"%s\",\"source\":\"%s\",\"inputs\":\"%s\"}",mode,seconds,synth?"pattern":"pending",TF_SOURCE_COMMIT,TF_INPUT_SHA256);
  // MIRROR observes the next bound source; its original v2 start must acquire admission itself.
  // SYNTH retains admission, excluding files/network/recording until the RAM test returns.
  if(!synth)admission.release();
  if(!frame(fd,3,sequence,ready,strlen(ready),failure,sent)){Guard g;armed=false;active=false;return;}
  uint64_t begin=now();
  // Off acquisition core; all transport buffers belong solely to this task.
  Item item={};
  if(synth){
    started=begin;snprintf(sourceFormat,sizeof(sourceFormat),"pattern");
    const uint64_t total=uint64_t(seconds)*240000;
    const uint64_t plannedUs=uint64_t(seconds)*1000000;
    const uint64_t end=begin+plannedUs,deadline=end+plannedUs/100;
    while(sent<total){
      uint64_t target=begin+(sent/2400)*10000;
      while(now()<target)vTaskDelay(1);
      // SYNTH measures full-run average throughput, not instantaneous packet jitter.
      // Retain absolute targets: never send ahead; catch up only within the 1% run deadline.
      if(now()>deadline){errors|=8;break;}
      item.size=2400;for(unsigned i=0;i<item.size;++i)item.data[i]=uint8_t((sent+i)*31+17);
      input+=item.size;
      if(!frame(fd,1,sequence,item.data,item.size,failure,sent)){errors|=1;transport=false;break;}
      sent+=item.size;
      if(now()>deadline){errors|=8;break;}
    }
    // Include the final payload's 10 ms interval in measured source duration.
    while(!errors&&now()<end)vTaskDelay(1);
    stopped=now();
    const uint64_t elapsed=stopped-started;
    if(stopped>deadline||elapsed<plannedUs||
       sent*1000000<237600*elapsed)errors|=8;
    sourceOk=!errors&&sent==total;
  }else{
    uint64_t drainDeadline=0;
    while(true){
      bool done, running;unsigned n;
      {Guard g;done=finished;running=started!=0;item.size=fifo.pop(item.data,payload);n=item.size;}
      if(n){if(!frame(fd,1,sequence,item.data,item.size,failure,sent)){Guard g;errors|=1;transport=false;break;}sent+=item.size;}
      else if(done)break;
      else vTaskDelay(1);
      uint64_t stamp=now();
      if(done&&!drainDeadline)drainDeadline=stamp+10000000;
      if((!running&&!done&&stamp-begin>30000000)||stamp-begin>uint64_t(seconds+120)*1000000||
          (drainDeadline&&stamp>drainDeadline)){Guard g;errors|=8;break;}
    }
  }
  char out[640];
  {Guard g;armed=false;active=false;
    if(!sourceOk)errors|=16;
    int n=snprintf(out,sizeof(out),"{\"mode\":\"%s\",\"source_ok\":%s,\"errors\":%u,\"input_bytes\":%llu,\"sent_bytes\":%llu,\"dropped_bytes\":%llu,\"queue_high\":%u,\"queue_high_bytes\":%u,\"start_us\":%llu,\"stop_us\":%llu,\"elapsed_us\":%llu,\"sha256\":\"%s\",\"run\":\"%s\",\"format\":\"%s\",\"target_bps\":240000,\"planned_us\":%llu,\"rate_bps\":%llu}",
      mode,sourceOk&&!errors?"true":"false",(unsigned)errors,(unsigned long long)input,(unsigned long long)sent,
      (unsigned long long)dropped,high,high,(unsigned long long)started,(unsigned long long)stopped,
      (unsigned long long)(stopped>started?stopped-started:0),sha,runName,sourceFormat,
      (unsigned long long)(uint64_t(seconds)*1000000),
      (unsigned long long)(stopped>started?input*1000000/(stopped-started):0));
    if(n<0||n>=int(sizeof(out)))transport=false;
  }
  if(transport)frame(fd,2,sequence,out,strlen(out),failure,sent);
}
struct ClientOptions {
  bool ready=false;
  const char* stage="nodelay_set";
  int error=0, nodelay=-1;
  socklen_t length=sizeof(int);
};
static ClientOptions configureClient(int fd){
  ClientOptions options;
  const int enabled=1;
  if(setsockopt(fd,IPPROTO_TCP,TCP_NODELAY,&enabled,sizeof(enabled))!=0){
    options.error=errno;return options;
  }
  options.stage="nodelay_get";
  if(getsockopt(fd,IPPROTO_TCP,TCP_NODELAY,&options.nodelay,&options.length)!=0){
    options.error=errno;return options;
  }
  options.stage="nodelay_value";
  if(options.length!=sizeof(options.nodelay)||options.nodelay!=1)return options;
  options.stage="ready";options.ready=true;return options;
}
static void reportClientState(const char* phase,const ClientOptions& options){
  constexpr unsigned caps=MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT;
  Serial.printf("[rate240] SOCKET phase=%s config=%s nodelay=%d option_bytes=%u errno=%d heap_free=%u heap_min_since_boot=%u heap_largest=%u\n",
    phase,options.stage,options.nodelay,unsigned(options.length),options.error,
    unsigned(heap_caps_get_free_size(caps)),unsigned(heap_caps_get_minimum_free_size(caps)),
    unsigned(heap_caps_get_largest_free_block(caps)));
}
static void serviceStopped(unsigned generation){
  Guard g;
  if(generation==serviceGeneration){serviceStarted=false;serviceReady=false;serviceCancelled=false;}
}
static void worker(void* argument){
  const unsigned generation=static_cast<unsigned>(reinterpret_cast<uintptr_t>(argument));
  bool cancelled;
  {Guard g;cancelled=generation!=serviceGeneration||serviceCancelled;}
  // Keep ownership until this worker has exited, including a late task after ARM timeout.
  if(cancelled){serviceStopped(generation);vTaskDelete(nullptr);return;}
  int server=socket(AF_INET,SOCK_STREAM,IPPROTO_TCP);
  if(server<0){const int error=errno;
    Serial.printf("[rate240] LISTEN_FAIL stage=socket errno=%d\n",error);
    serviceStopped(generation);vTaskDelete(nullptr);return;}
  const int reuse=1;
  if(setsockopt(server,SOL_SOCKET,SO_REUSEADDR,&reuse,sizeof(reuse))!=0){
    const int error=errno;close(server);
    Serial.printf("[rate240] LISTEN_FAIL stage=reuse errno=%d\n",error);
    serviceStopped(generation);vTaskDelete(nullptr);return;
  }
  sockaddr_in addr={};addr.sin_family=AF_INET;addr.sin_port=htons(19000);addr.sin_addr.s_addr=htonl(INADDR_ANY);
  const int bound=bind(server,reinterpret_cast<sockaddr*>(&addr),sizeof(addr));
  const int bindError=bound?errno:0;
  const int listening=bound?-1:listen(server,1);
  const int listenError=(!bound && listening)?errno:0;
  if(bound||listening){
    close(server);
    Serial.printf("[rate240] LISTEN_FAIL stage=%s errno=%d\n",bound?"bind":"listen",bound?bindError:listenError);
    serviceStopped(generation);vTaskDelete(nullptr);return;
  }
  {Guard g;cancelled=generation!=serviceGeneration||serviceCancelled;
    if(!cancelled)serviceReady=true;}
  if(cancelled){close(server);serviceStopped(generation);vTaskDelete(nullptr);return;}
  fd_set waiting;FD_ZERO(&waiting);FD_SET(server,&waiting);timeval armTimeout={30,0};
  if(select(server+1,&waiting,nullptr,nullptr,&armTimeout)>0){int fd=accept(server,nullptr,nullptr);if(fd>=0){
    timeval timeout={0,250000};setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof(timeout));setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&timeout,sizeof(timeout));
    const ClientOptions options=configureClient(fd);
    SendFailure failure;
    if(options.ready){reportClientState("session_start",options);session(fd,failure);}
    shutdown(fd,SHUT_RDWR);close(fd);
    Serial.printf("[rate240] WORKER worker_stack_min_bytes=%u\n",
      unsigned(uxTaskGetStackHighWaterMark(nullptr)));
    reportClientState("closed",options);
    reportSendSamples(failure);
    reportSendFailure(failure);
  }}
  close(server);
  serviceStopped(generation);
  vTaskDelete(nullptr);
}
bool start(const char* key){
  unsigned generation;
  {Guard g;if(serviceStarted)return false;
    serviceStarted=true;serviceReady=false;serviceCancelled=false;generation=++serviceGeneration;}
  snprintf(secret,sizeof(secret),"%s",key);
  const uint64_t deadline=now()+1000000;
  if(xTaskCreatePinnedToCore(worker,"rate240",8192,
      reinterpret_cast<void*>(static_cast<uintptr_t>(generation)),1,nullptr,0)!=pdPASS){
    serviceStopped(generation);return false;
  }
  while(true){
    {Guard g;
      if(generation!=serviceGeneration||!serviceStarted)return false;
      if(serviceReady)return true;
      if(now()>=deadline){serviceCancelled=true;return false;}}
    vTaskDelay(1);
  }
}
}
