#include "device_v2.h"
#include "usb_protocol.h"
#include "web_asset.h"
#include "driver/usb_serial_jtag.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <cstring>
namespace devicev2 {
static uint8_t incoming[16+frameLimit];
static bool writeAll(const uint8_t* data,size_t size) {
  unsigned stalled=0;
  while(size) {
    // IDF enqueues the entire argument as one ring-buffer item; it cannot split
    // a 2048-byte protocol payload into the configured 512-byte TX ring.
    size_t chunk=size>128?128:size;
    int n=usb_serial_jtag_write_bytes(data,chunk,0);
    if(n>0) {data+=n;size-=n;stalled=0;}
    else {if(++stalled>2000) return false;vTaskDelay(1);}
  }
  return true;
}
static bool send(uint8_t kind,uint32_t id,const uint8_t* data,size_t length) {
  uint8_t header[16]; frameHeader(header,kind,id,data,length);
  return writeAll(header,sizeof(header))&&writeAll(data,length);
}
static bool body(uint32_t id,const char* data,size_t length) {
  while(length) {size_t n=length>2048?2048:length; if(!send(3,id,(const uint8_t*)data,n)) return false; data+=n;length-=n;}
  return true;
}
static bool meta(uint32_t id,int code,const char* type,size_t length) {
  char header[192]; int n=snprintf(header,sizeof(header),"{\"code\":%d,\"type\":\"%s\",\"length\":%u}",code,type,(unsigned)length);
  return send(2,id,(const uint8_t*)header,n);
}
static void serve(uint32_t id,const char* request) {
  auto args=cJSON_Parse(request);
  if(!cJSON_IsObject(args)) {cJSON_Delete(args);return;}
  const char* method=text(args,"method"),*path=text(args,"path");
  if(!strcmp(method,"GET")&&!strcmp(path,"/")) {
    const char* marker=strstr(webPage,"{{KEY}}"); size_t first=marker?marker-webPage:0;
    if(marker&&meta(id,200,"text/html; charset=utf-8",sizeof(webPage)-1-7+32)) {
      if(body(id,webPage,first)&&body(id,control::sessionKey(),32)&&body(id,marker+7,sizeof(webPage)-1-first-7)) send(4,id,nullptr,0);
    }
  } else {
    Reply reply=dispatch(method,path,text(args,"body"),text(args,"key"));
    if(meta(id,reply.code,reply.type,reply.body.size())&&body(id,reply.body.data(),reply.body.size())) send(4,id,nullptr,0);
  }
  cJSON_Delete(args);
}
static void receiveTask(void*) {
  size_t used=0;
  while(true) {
    int count=usb_serial_jtag_read_bytes(incoming+used,sizeof(incoming)-used,pdMS_TO_TICKS(100));
    if(count<=0) continue;
    used+=count;
    while(used>=16) {
      size_t length=incoming[6]|(size_t(incoming[7])<<8);
      if(memcmp(incoming,"BRC2",4)||incoming[4]!=2||length>frameLimit) {memmove(incoming,incoming+1,--used);continue;}
      if(used<16+length) break;
      if(validFrame(incoming,incoming+16)&&incoming[5]==1&&length&&memchr(incoming+16,0,length)==nullptr) {
        // A separate bounded string terminates JSON without modifying the next frame.
        std::string request((char*)incoming+16,length);
        serve(get32(incoming+8),request.c_str());
      }
      used-=16+length; if(used) memmove(incoming,incoming+16+length,used);
    }
  }
}
void startUsb() {
  usb_serial_jtag_driver_config_t config={}; config.rx_buffer_size=512;config.tx_buffer_size=512;
  if(usb_serial_jtag_driver_install(&config)!=ESP_OK) return;
  if(xTaskCreatePinnedToCore(receiveTask,"usb_control",8192,nullptr,3,nullptr,0)!=pdPASS) usb_serial_jtag_driver_uninstall();
}
}
