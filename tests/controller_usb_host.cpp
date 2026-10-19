// Compile the real USB writer against a driver that refuses items larger than its TX ring.
#include "device_v2.h"
#include <vector>
#include <cstdint>
#include <cassert>
#include <cstring>
#include <cstdio>
#include <cstdlib>
#include <new>
#include "driver/usb_serial_jtag.h"
static std::vector<uint8_t> wire;
static size_t maxWrite=0;
static bool denyNew=false,formatFailure=false;
static size_t failAt=SIZE_MAX;
static int64_t advancePerWrite=0;
static size_t acceptedPerWrite=SIZE_MAX;
extern int64_t hostTime;
void* operator new(size_t n){if(denyNew)throw std::bad_alloc();if(auto p=malloc(n))return p;throw std::bad_alloc();}
void* operator new[](size_t n){return ::operator new(n);}
void operator delete(void* p)noexcept{free(p);}
void operator delete[](void* p)noexcept{free(p);}
void operator delete(void* p,size_t)noexcept{free(p);}
void operator delete[](void* p,size_t)noexcept{free(p);}
int usb_serial_jtag_write_bytes(const void* bytes,size_t count,int){
 hostTime+=advancePerWrite;
 if(count>maxWrite)maxWrite=count;
 if(count>512)return 0;
 if(wire.size()>=failAt)return 0;
 if(count>acceptedPerWrite)count=acceptedPerWrite;
 auto data=static_cast<const uint8_t*>(bytes);wire.insert(wire.end(),data,data+count);return count;
}
int usb_serial_jtag_read_bytes(void*,size_t,int){return 0;}
int usb_serial_jtag_driver_install(const usb_serial_jtag_driver_config_t*){return 0;}
int usb_serial_jtag_driver_uninstall(){return 0;}
#include "../面包板样机_固件/diagnostics/sensor_web_recorder/src/usb_v2.cpp"
namespace control {
const char* sessionKey(){return "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";}
std::string statusJson(){assert(false&&"USB status must bypass dynamic dispatch");return {};}
std::string trendJson(){assert(false&&"USB trend must bypass dynamic dispatch");return {};}
bool streamTelemetry(bool trend,TelemetrySink sink,void* context){
 static char response[9000];memset(response,trend?'t':'s',sizeof(response));
 static constexpr char error[]="{\"error\":\"telemetry bound exceeded\"}";
 denyNew=true;
 bool result=formatFailure?sink(500,error,sizeof(error)-1,context):sink(200,response,trend?8192:sizeof(response),context);
 denyNew=false;return result;
}
devicev2::Reply sessionRequest(const char*,cJSON*){return {};}
}
namespace devicev2 {
void initFiles(){}
Reply filesRequest(const char*,const char*,cJSON*){return {};}
Reply wifiRequest(const char*,const char*,cJSON*){return {};}
}
int64_t hostTime=0;
static void check(unsigned id,size_t expected,int code=200){
 std::string body;bool ended=false,meta=false;size_t at=0;
 while(at<wire.size()){
  assert(at+16<=wire.size());auto header=wire.data()+at;
  size_t n=header[6]|(size_t(header[7])<<8);
  assert(at+16+n<=wire.size()&&devicev2::validFrame(header,header+16));
  assert(devicev2::get32(header+8)==id);
  assert(!ended);
  if(header[5]==2){
   assert(!meta&&at==0);meta=true;
   auto json=cJSON_ParseWithLength((char*)header+16,n);assert(json);
   assert(cJSON_GetObjectItem(json,"code")->valueint==code);
   assert(cJSON_GetObjectItem(json,"length")->valuedouble==expected);
   assert(cJSON_IsString(cJSON_GetObjectItem(json,"type")));cJSON_Delete(json);
  }
  if(header[5]==3)assert(meta&&n<=2048);
  if(header[5]==3)body.append((char*)header+16,n);
  if(header[5]==4){assert(n==0&&meta);ended=true;}
  at+=16+n;
 }
 assert(meta&&ended&&body.size()==expected&&maxWrite<=128);
 if(code==200&&id!=1)for(char c:body)assert(c==(id==3?'t':'s'));
}
int main(){
 wire.reserve(20000);
 assert(devicev2::serve(1,"{\"method\":\"GET\",\"path\":\"/\"}"));
 check(1,sizeof(webPage)-1-7+32);
 wire.clear();assert(devicev2::serve(2,"{\"method\":\"GET\",\"path\":\"/api/status\"}"));check(2,9000);
 wire.clear();assert(devicev2::serve(3,"{\"method\":\"GET\",\"path\":\"/api/trend\"}"));check(3,8192);
 formatFailure=true;wire.clear();assert(devicev2::serve(4,"{\"method\":\"GET\",\"path\":\"/api/status\"}"));
 check(4,strlen("{\"error\":\"telemetry bound exceeded\"}"),500);formatFailure=false;
 for(size_t limit:{size_t(0),size_t(200),size_t(4200),size_t(9120)}){
  wire.clear();failAt=limit;assert(!devicev2::serve(5,"{\"method\":\"GET\",\"path\":\"/api/status\"}"));
  size_t at=0;while(at+16<=wire.size()){
   auto header=wire.data()+at;size_t n=header[6]|(size_t(header[7])<<8);assert(header[5]!=4);at+=16+n;
  }
 }
 failAt=SIZE_MAX;
 // Every write makes progress: per-call or per-chunk stall timeouts would
 // never trip. The single response deadline must still release the buffer.
 wire.clear();hostTime=0;advancePerWrite=20000;acceptedPerWrite=1;
 assert(!devicev2::serve(7,"{\"method\":\"GET\",\"path\":\"/api/status\"}"));
 assert(hostTime==1000000&&wire.size()==50);
 advancePerWrite=0;acceptedPerWrite=SIZE_MAX;wire.clear();
 assert(devicev2::serve(2,"{\"method\":\"GET\",\"path\":\"/api/status\"}"));check(2,9000);
 // Also cross a protocol-chunk boundary with steady progress before expiring.
 wire.clear();hostTime=0;advancePerWrite=20000;
 assert(!devicev2::serve(8,"{\"method\":\"GET\",\"path\":\"/api/trend\"}"));
 assert(hostTime==1000000&&wire.size()>2048&&wire.size()<8192);
 advancePerWrite=0;wire.clear();
 assert(devicev2::serve(3,"{\"method\":\"GET\",\"path\":\"/api/trend\"}"));check(3,8192);
 assert(!devicev2::serve(6,"invalid"));
 puts("PASS: real USB writer streams 9k status/8k trend without C++ allocation; metadata/CRC/id/end/chunks, failure propagation and whole-response deadline/recovery");
}
