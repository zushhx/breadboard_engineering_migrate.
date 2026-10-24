#include "web_control.h"
#include "session_model.h"
#include "telemetry_json.h"
#include "device_v2.h"
#include <unistd.h>
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "vibration_trend.h"
#include "wifi_credentials.h"
#include "web_asset.h"
#include "serial_compat.h"
#include "module_values.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_http_server.h"
#include "http_response_policy.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "cJSON.h"
#include <cmath>
#include <cerrno>
#include <cstdlib>
#include <inttypes.h>
#if defined(CONFIG_ESP_PHY_CALIBRATION_AND_DATA_STORAGE) || defined(CONFIG_ESP32_PHY_CALIBRATION_AND_DATA_STORAGE)
#error "Web recorder must not persist RF calibration to NVS"
#endif
#if defined(CONFIG_ESP_WIFI_STA_DISCONNECTED_PM_ENABLE)
#error "Controlled WiFi diagnostic requires disconnected power management disabled"
#endif

namespace devicev2 {esp_err_t downloadHandler(httpd_req_t*);}
namespace control {
static Model model;
static Sessions sessions;
static SemaphoreHandle_t serialization;
static portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
struct Guard { Guard() { portENTER_CRITICAL(&mux); } ~Guard() { portEXIT_CRITICAL(&mux); } };
static httpd_handle_t server;
static char key[33];
static uint64_t lastPreview;
static TrendAccumulator trendAccumulator;
static TrendHistory trendHistory;

static char trendRun[9];
static unsigned trendRangeG = 2;
static bool addressLogPending;
static esp_err_t connectLogPending = ESP_OK;
static const char* const pageStart = webPage;
static const char* const pageEnd = webPage + sizeof(webPage);
void initialize(bool ready, uint32_t serial, uint64_t capacity) {
  static StaticSemaphore_t storage; serialization=xSemaphoreCreateMutexStatic(&storage);
  Guard g; model.s.ready = ready; model.s.serial = serial; model.s.capacity = capacity;
  if (!ready) { model.s.phase = Phase::Failed; snprintf(model.s.detail, sizeof(model.s.detail), "TF或采集池不可用；禁止录制"); }
}
void storageSpace(bool valid, uint64_t bytes) { Guard g; model.storageSpace(valid, bytes); }
bool take(char* run, unsigned& seconds, unsigned* rangeG) { Guard g; sessions.tick(model,esp_timer_get_time()); return model.take(run, seconds, rangeG); }
bool begin(const char* run, unsigned seconds, unsigned rangeG) {
  Guard g;
  if (!model.begin(run, seconds, rangeG)) return false;
  lastPreview = 0; trendAccumulator.reset(sensor_range::scaleMicroG(rangeG)); trendHistory.reset(); trendRangeG = rangeG;
  snprintf(trendRun, sizeof(trendRun), "%s", run);
  return true;
}
bool recordNaming(const char* run,char* local,uint64_t& elapsedUs) {
  Guard g;
  if(strcmp(model.s.run,run)||!model.s.startLocal[0]) {local[0]=0;elapsedUs=0;return false;}
  memcpy(local,model.s.startLocal,18);uint64_t current=esp_timer_get_time();
  elapsedUs=current>=model.s.namedAtUs?current-model.s.namedAtUs:0;return true;
}
void recordLocation(const char* run,const char* name,const char* path) {
  Guard g;if(strcmp(model.s.run,run))return;
  snprintf(model.s.recordName,sizeof(model.s.recordName),"%s",name);
  snprintf(model.s.recordPath,sizeof(model.s.recordPath),"%s",path);
}
void phase(Phase value, const char* detail) { Guard g; model.s.phase = value; snprintf(model.s.detail, sizeof(model.s.detail), "%s", detail); }
bool stopping() { Guard g; sessions.tick(model,esp_timer_get_time()); return model.s.stop; }
void result(bool passed, bool locked, const char* detail, const char* sha) {
  Guard g; model.s.phase = passed ? Phase::Passed : (locked ? Phase::Failed : Phase::Rejected);
  snprintf(model.s.detail, sizeof(model.s.detail), "%s", detail); snprintf(model.s.sha, sizeof(model.s.sha), "%s", sha);
  sessions.finish(model,passed,esp_timer_get_time());
}
void progress(uint64_t written, uint64_t elapsed, uint32_t errors, uint32_t queue, uint32_t writeUs, uint32_t syncUs) {
  uint32_t freeHeap = heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  // A heap walk is deliberately omitted from the recording path.
  Guard g; auto& s = model.s; s.written = written; s.elapsedUs = elapsed; s.errors = errors; s.queue = queue;
  s.maxWriteUs = writeUs; s.maxSyncUs = syncUs; s.freeHeap = freeHeap;
}
void preview(const uint8_t* frames, unsigned count, uint64_t firstUs) {
  // This function already receives every dequeued block on the recording owner.
  // Accumulate before limiting the separate continuous raw snapshot to 5Hz/272 points.
  for (unsigned i = 0; i < count; ++i) {
    int16_t sample[3];
    for (unsigned a = 0; a < 3; ++a) sample[a] = modules::leSigned16(frames + i * 7 + 1 + a * 2);
    TrendPoint point;
    if (trendAccumulator.add(sample, point)) { Guard g; trendHistory.push(point); }
  }
  uint64_t stamp = esp_timer_get_time();
  if (lastPreview && stamp - lastPreview < 200000) return;
  lastPreview = stamp; count = count < kPreview ? count : kPreview;
  static int16_t xyz[kPreview][3]; // Recording owner only; do not grow its task stack.
  for (unsigned i = 0; i < count; ++i)
    for (unsigned a = 0; a < 3; ++a) xyz[i][a] = modules::leSigned16(frames + i * 7 + 1 + a * 2);
  Guard g; memcpy(model.s.xyz, xyz, count * sizeof(xyz[0])); model.s.previewCount = count;
  model.s.previewUs = firstUs; ++model.s.previewSeq;
}
void ina(uint64_t timeUs, bool valid, int32_t busMv, int32_t currentUa, int32_t powerUw) {
  Guard g; model.s.inaUs = timeUs; model.s.inaValid = valid; model.s.busMv = busMv;
  model.s.currentUa = currentUa; model.s.powerUw = powerUw;
}
void finalMetrics(uint32_t fifoHigh, uint32_t queueHigh) { Guard g; model.s.fifoHigh = fifoHigh; model.s.queueHigh = queueHigh; }
static esp_err_t headers(httpd_req_t* r, const char* type) {
  if(boundedhttp::prepare(r)!=ESP_OK)return ESP_FAIL;
  httpd_resp_set_type(r, type);
  httpd_resp_set_hdr(r, "Cache-Control", "no-store");
  httpd_resp_set_hdr(r, "X-Content-Type-Options", "nosniff");
  httpd_resp_set_hdr(r, "Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; form-action 'none'");
  return ESP_OK;
}
static esp_err_t page(httpd_req_t* r) {
  if(headers(r, "text/html; charset=utf-8")!=ESP_OK)return ESP_FAIL;
  const char* marker = strstr(pageStart, "{{KEY}}");
  if (!marker) return boundedhttp::error(r, HTTPD_500_INTERNAL_SERVER_ERROR, "asset invalid");
  if (httpd_resp_send_chunk(r, pageStart, marker - pageStart) != ESP_OK ||
      httpd_resp_send_chunk(r, key, strlen(key)) != ESP_OK ||
      httpd_resp_send_chunk(r, marker + 7, pageEnd - (marker + 7) - 1) != ESP_OK) return ESP_FAIL;
  return boundedhttp::endChunks(r);
}
// Only network/USB response tasks take this mutex. Never hold Guard across I/O.
static char telemetryJson[kTelemetryJsonCapacity];
struct TelemetryLock {
  TelemetryLock() {xSemaphoreTake(serialization,portMAX_DELAY);}
  ~TelemetryLock() {xSemaphoreGive(serialization);}
};
static size_t buildStatusJson() {
  Snapshot s; char owner[33],waiting[33]; bool files;
  {Guard g;sessions.tick(model,esp_timer_get_time());s=model.s;
    snprintf(owner,sizeof(owner),"%s",sessions.owner);snprintf(waiting,sizeof(waiting),"%s",sessions.pendingOwner);files=sessions.files;}
  return renderStatusJson(telemetryJson,sizeof(telemetryJson),key,s,owner,waiting,files);
}
static size_t buildTrendJson() {
  static TrendPoint points[kTrendPoints]; char run[9]; unsigned count, rangeG;
  {Guard g;count=trendHistory.copy(points);memcpy(run,trendRun,sizeof(run));rangeG=trendRangeG;}
  return renderTrendJson(telemetryJson,sizeof(telemetryJson),key,run,points,count,rangeG);
}
bool streamTelemetry(bool trend,TelemetrySink sink,void* context) {
  if(!sink) return false;
  TelemetryLock lock;
  size_t size=trend?buildTrendJson():buildStatusJson();
  static constexpr char error[]="{\"error\":\"telemetry bound exceeded\"}";
  return size?sink(200,telemetryJson,size,context):sink(500,error,sizeof(error)-1,context);
}
std::string statusJson() {
  TelemetryLock lock;size_t size=buildStatusJson();
  // Compatibility for generic dispatch callers; the USB transport streams directly.
  return size?std::string(telemetryJson,size):std::string("{}");
}
std::string trendJson() {
  TelemetryLock lock;size_t size=buildTrendJson();
  return size?std::string(telemetryJson,size):std::string("{}");
}
static esp_err_t telemetry(httpd_req_t* r,bool trend) {
  const int64_t entered=esp_timer_get_time();
  headers(r,"application/json");
  const int64_t waiting=esp_timer_get_time();
  int64_t locked=0,rendered=0,finished=0;size_t size=0;esp_err_t sent;int sendError=0;
  {
    TelemetryLock lock;locked=esp_timer_get_time();
    size=trend?buildTrendJson():buildStatusJson();rendered=esp_timer_get_time();
    // Buffer stays stable for synchronous send; diagnostics run after unlock.
    errno=0;
    sent=size?boundedhttp::send(r,telemetryJson,size):
      boundedhttp::error(r,HTTPD_500_INTERNAL_SERVER_ERROR,"telemetry bound exceeded");
    sendError=sent==ESP_OK?0:errno;finished=esp_timer_get_time();
  }
  if(sent!=ESP_OK||finished-entered>100000) {
    Serial.printf("[http] telemetry=%s bytes=%u lock_ms=%" PRIi64 " render_ms=%" PRIi64 " send_close_ms=%" PRIi64 " total_ms=%" PRIi64 " result=%d errno=%d\n",
      trend?"trend":"status",(unsigned)size,(locked-waiting)/1000,(rendered-locked)/1000,
      (finished-rendered)/1000,(finished-entered)/1000,(int)sent,sendError);
  }
  return sent;
}
static esp_err_t status(httpd_req_t* r) {return telemetry(r,false);}
static esp_err_t trend(httpd_req_t* r) {return telemetry(r,true);}
static bool authorized(httpd_req_t* r) {
  char received[33] = {};
  return httpd_req_get_hdr_value_len(r, "X-Record-Key") == 32 &&
      httpd_req_get_hdr_value_str(r, "X-Record-Key", received, sizeof(received)) == ESP_OK && !strcmp(received, key);
}
static esp_err_t stop(httpd_req_t* r) {
  headers(r, "application/json");
  if (!authorized(r)) return boundedhttp::error(r, HTTPD_403_FORBIDDEN, "stale session or missing token; reload page");
  if (r->content_len != 0) return boundedhttp::error(r, HTTPD_400_BAD_REQUEST, "empty body required");
  { Guard g; model.requestStop(); }
  return boundedhttp::send(r, "{\"accepted\":true}",HTTPD_RESP_USE_STRLEN);
}
static esp_err_t start(httpd_req_t* r) {
  httpd_resp_set_status(r,"426 Upgrade Required"); headers(r,"application/json");
  return boundedhttp::send(r,"{\"error\":\"v2 control session required\"}",HTTPD_RESP_USE_STRLEN);
}
void pollNetworkLog() {
  char ip[16] = {}; bool addressPending; esp_err_t error;
  { Guard g; addressPending = addressLogPending; addressLogPending = false;
    memcpy(ip, model.s.ip, sizeof(ip)); error = connectLogPending; connectLogPending = ESP_OK; }
  if (addressPending && ip[0]) Serial.printf("[web] address=http://%s/ hotspot_client_isolation=UNTESTED\n", ip);
  if (error != ESP_OK) Serial.printf("[web] connect request=%s\n", esp_err_to_name(error));
}
const char* sessionKey() { return key; }
bool busyNow() { Guard g; sessions.tick(model,esp_timer_get_time()); return busy(model.s.phase)||sessions.pendingOwner[0]||sessions.files; }
bool reserveFiles() { Guard g; if(busy(model.s.phase)||sessions.pendingOwner[0]||sessions.files) return false; sessions.files=true; return true; }
void releaseFiles() { Guard g; sessions.files=false; }
void networkAddress(bool connected,const char* ip) { Guard g; model.s.connected=connected; snprintf(model.s.ip,sizeof(model.s.ip),"%s",ip); }
devicev2::Reply sessionRequest(const char* action,cJSON* a) {
  const char* id=devicev2::text(a,"client"); uint64_t now=esp_timer_get_time();
  if(!strcmp(action,"state")) {
    char current[33],waiting[33];
    {Guard guard;sessions.tick(model,now);snprintf(current,sizeof(current),"%s",sessions.owner);snprintf(waiting,sizeof(waiting),"%s",sessions.pendingOwner);}
    auto out=cJSON_CreateObject();cJSON_AddStringToObject(out,"owner",current);cJSON_AddStringToObject(out,"pending_owner",waiting);
    devicev2::Reply r;r.body=devicev2::json(out);return r;
  }
  if(!Sessions::valid(id)) return devicev2::error(400,"控制会话格式无效");
  bool ok=false;
  if(!strcmp(action,"open")||!strcmp(action,"touch")) {
    {Guard guard;sessions.tick(model,now);ok=sessions.touch(id,now);}
    if(!ok) return devicev2::error(429,"已达到四个检测会话上限");
  } else if(!strcmp(action,"release")) {Guard guard;sessions.release(model,id);}
  else if(!strcmp(action,"stop")) {Guard guard;model.requestStop();}
  else {
    int rangeG=2; auto rangeValue=cJSON_GetObjectItemCaseSensitive(a,"range_g");
    if(rangeValue&&(!devicev2::integer(a,"range_g",2,16,rangeG)||!sensor_range::valid(rangeG)))return devicev2::error(400,"invalid range");
    int seconds=0;const char* run=devicev2::text(a,"run");const char* serial=devicev2::text(a,"card_serial");const char* capacity=devicev2::text(a,"card_bytes");
    auto stampValue=cJSON_GetObjectItem(a,"local_time");
    if(stampValue&&(!cJSON_IsString(stampValue)||!recorder::localStamp(stampValue->valuestring)))return devicev2::error(400,"采集本地时间无效，请校准电脑或手机时间并刷新页面");
    const char* local=stampValue?stampValue->valuestring:"";
    if(!devicev2::integer(a,"seconds",1,300,seconds)||!recorder::runId(serial)||!recorder::runId(run)||!capacity[0]) return devicev2::error(400,"采集参数无效");
    for(const char* digit=capacity;*digit;digit++) if(*digit<'0'||*digit>'9') return devicev2::error(400,"卡容量无效");
    char* end=nullptr;uint64_t bytes=strtoull(capacity,&end,10);if(!end||*end) return devicev2::error(400,"卡容量无效");
    {Guard guard;
      if(!strcmp(action,"start")) ok=sessions.start(model,id,now,strtoul(serial,nullptr,16),bytes,run,seconds,local,rangeG);
      else if(!strcmp(action,"takeover")) ok=sessions.takeover(model,id,now,devicev2::text(a,"expected_run"),strtoul(serial,nullptr,16),bytes,run,seconds,local,rangeG);
    }
    if(!ok) return devicev2::error(409,"状态已变化、其他页面正在采集或文件任务未完成；请刷新状态");
  }
  devicev2::Reply r;r.code=202;r.body="{\"accepted\":true}";return r;
}
static esp_err_t v2Handler(httpd_req_t* r) {
  if(r->content_len>4096) return boundedhttp::error(r,HTTPD_400_BAD_REQUEST,"request too large");
  std::string body; body.resize(r->content_len); size_t used=0;
  while(used<body.size()) { int n=httpd_req_recv(r,&body[used],body.size()-used); if(n<=0) return ESP_FAIL; used+=n; }
  char token[33]={}; httpd_req_get_hdr_value_str(r,"X-Record-Key",token,sizeof(token));
  auto reply=devicev2::dispatch(r->method==HTTP_GET?"GET":"POST",r->uri,body.c_str(),token);
  char statusText[40]; snprintf(statusText,sizeof(statusText),"%d %s",reply.code,reply.code<400?"OK":"Request Rejected");
  httpd_resp_set_status(r,statusText); headers(r,reply.type); return boundedhttp::send(r,reply.body.data(),reply.body.size());
}

struct LiveClient {int fd=-1;char id[33]={};};
static LiveClient live[4];
static void socketClosed(httpd_handle_t,int fd) {
  for(auto& client:live) if(client.fd==fd) {
    {Guard guard;sessions.release(model,client.id);}
    client=LiveClient{};
  }
  ::close(fd);
}
static esp_err_t liveHandler(httpd_req_t* r) {
  int fd=httpd_req_to_sockfd(r);
  if(r->method==HTTP_GET) {
    char query[160]={},id[33]={},boot[33]={};
    if(httpd_req_get_url_query_str(r,query,sizeof(query))!=ESP_OK||
       httpd_query_key_value(query,"client",id,sizeof(id))!=ESP_OK||
       httpd_query_key_value(query,"boot",boot,sizeof(boot))!=ESP_OK||
       strcmp(boot,key)||!Sessions::valid(id)) return boundedhttp::error(r,HTTPD_403_FORBIDDEN,"invalid session");
    LiveClient* slot=nullptr;
    for(auto& client:live) if(client.fd==fd||client.fd<0) {slot=&client;break;}
    if(!slot) {httpd_resp_set_status(r,"429 Too Many Requests");return boundedhttp::send(r,"client limit",HTTPD_RESP_USE_STRLEN);}
    bool accepted=false;{Guard guard;accepted=sessions.touch(id,esp_timer_get_time());}
    if(!accepted) {httpd_resp_set_status(r,"429 Too Many Requests");return boundedhttp::send(r,"client limit",HTTPD_RESP_USE_STRLEN);}
    slot->fd=fd;snprintf(slot->id,sizeof(slot->id),"%s",id);return ESP_OK;
  }
  httpd_ws_frame_t frame={};uint8_t payload[160];frame.payload=payload;
  if(httpd_ws_recv_frame(r,&frame,0)!=ESP_OK||frame.len>sizeof(payload)) return ESP_FAIL;
  if(frame.len&&httpd_ws_recv_frame(r,&frame,sizeof(payload))!=ESP_OK) return ESP_FAIL;
  for(auto& client:live) if(client.fd==fd) {
    if(frame.type==HTTPD_WS_TYPE_PONG) {Guard guard;sessions.touch(client.id,esp_timer_get_time());}
    if(frame.type==HTTPD_WS_TYPE_CLOSE) {{Guard guard;sessions.release(model,client.id);}frame.type=HTTPD_WS_TYPE_CLOSE;return httpd_ws_send_frame(r,&frame);}
  }
  if(frame.type==HTTPD_WS_TYPE_PING) {frame.type=HTTPD_WS_TYPE_PONG;return httpd_ws_send_frame(r,&frame);}
  return ESP_OK;
}
static boundedhttp::PendingWork heartbeatPending;
static void pingClients(void* context) {
  uint32_t token=static_cast<uint32_t>(reinterpret_cast<uintptr_t>(context));
  if(!heartbeatPending.begin(token))return;
  for(auto& client:live) if(client.fd>=0) {
    httpd_ws_frame_t frame={};frame.type=HTTPD_WS_TYPE_PING;
    if(httpd_ws_send_frame_async(server,client.fd,&frame)!=ESP_OK) httpd_sess_trigger_close(server,client.fd);
  }
  heartbeatPending.complete(token);
}
static void heartbeatTask(void*) {
  for(;;) {
    vTaskDelay(pdMS_TO_TICKS(2000));
    if(server) heartbeatPending.schedule(static_cast<uint32_t>(esp_timer_get_time()/1000),[](uint32_t token) {
      return httpd_queue_work(server,pingClients,reinterpret_cast<void*>(static_cast<uintptr_t>(token)))==ESP_OK;
    });
  }
}
void startNetwork() {
  for(unsigned i=0;i<4;i++) snprintf(key+i*8,9,"%08" PRIx32,esp_random());
  devicev2::initialize(); devicev2::startWifi();
  httpd_config_t config=HTTPD_DEFAULT_CONFIG(); config.stack_size=12288; config.task_priority=2; config.core_id=0;
  config.max_open_sockets=9; config.lru_purge_enable=false; config.close_fn=socketClosed; config.max_uri_handlers=12;
  config.uri_match_fn=httpd_uri_match_wildcard; config.recv_wait_timeout=2; config.send_wait_timeout=2;
  esp_err_t error=httpd_start(&server,&config);
  if(error==ESP_OK) {
    httpd_uri_t download={};download.uri="/api/v2/download";download.method=HTTP_GET;download.handler=devicev2::downloadHandler;httpd_register_uri_handler(server,&download);
    httpd_uri_t ws={};ws.uri="/api/v2/live";ws.method=HTTP_GET;ws.handler=liveHandler;ws.is_websocket=true;ws.handle_ws_control_frames=true;
    httpd_register_uri_handler(server,&ws);
    xTaskCreatePinnedToCore(heartbeatTask,"control_lease",2048,nullptr,1,nullptr,0);
    const char* paths[]={"/","/api/status","/api/trend","/api/start","/api/stop","/api/v2/*"};
    esp_err_t (*handlers[])(httpd_req_t*)={page,status,trend,start,stop,v2Handler};
    for(unsigned i=0;i<6;i++) { httpd_uri_t u={}; u.uri=paths[i]; u.method=(i==3||i==4)?HTTP_POST:HTTP_GET; u.handler=handlers[i]; httpd_register_uri_handler(server,&u); }
    httpd_uri_t post={}; post.uri="/api/v2/*"; post.method=HTTP_POST; post.handler=v2Handler; httpd_register_uri_handler(server,&post);
  }
  devicev2::startUsb();
  Serial.printf("[web] HTTP v2 startup=%s; USB and WiFi configuration enabled\n",esp_err_to_name(error));
}
}
