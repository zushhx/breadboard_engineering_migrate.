// Compile the real USB writer against a driver that refuses items larger than its TX ring.
#include "device_v2.h"
#include <vector>
#include <cassert>
#include <cstring>
#include <cstdio>
#include "driver/usb_serial_jtag.h"
static std::vector<uint8_t> wire;
static size_t maxWrite=0;
int usb_serial_jtag_write_bytes(const void* bytes,size_t count,int){
 if(count>maxWrite)maxWrite=count;
 if(count>512)return 0;
 auto data=static_cast<const uint8_t*>(bytes);wire.insert(wire.end(),data,data+count);return count;
}
int usb_serial_jtag_read_bytes(void*,size_t,int){return 0;}
int usb_serial_jtag_driver_install(const usb_serial_jtag_driver_config_t*){return 0;}
int usb_serial_jtag_driver_uninstall(){return 0;}
#include "../面包板样机_固件/diagnostics/sensor_web_recorder/src/usb_v2.cpp"
namespace control {
const char* sessionKey(){return "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";}
std::string statusJson(){return std::string(5000,'x');}std::string trendJson(){return "{}";}
devicev2::Reply sessionRequest(const char*,cJSON*){return {};}
}
namespace devicev2 {
void initFiles(){}
Reply filesRequest(const char*,const char*,cJSON*){return {};}
Reply wifiRequest(const char*,const char*,cJSON*){return {};}
}
int64_t hostTime=0;
static void check(unsigned id,size_t expected){
 std::string body;bool ended=false;size_t at=0;
 while(at<wire.size()){
  assert(at+16<=wire.size());auto header=wire.data()+at;
  size_t n=header[6]|(size_t(header[7])<<8);
  assert(at+16+n<=wire.size()&&devicev2::validFrame(header,header+16));
  assert(devicev2::get32(header+8)==id);
  if(header[5]==3)body.append((char*)header+16,n);
  if(header[5]==4)ended=true;
  at+=16+n;
 }
 assert(ended&&body.size()==expected&&maxWrite<=128);
}
int main(){
 devicev2::serve(1,"{\"method\":\"GET\",\"path\":\"/\"}");
 check(1,sizeof(webPage)-1-7+32);
 wire.clear();devicev2::serve(2,"{\"method\":\"GET\",\"path\":\"/api/status\"}");
 check(2,5000);
 puts("PASS: real USB writer streams page and >512-byte status through 512-byte ring with valid CRC/end frames");
}
