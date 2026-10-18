#include "device_v2.h"
#include "mutex_lock.h"
#include "wifi_credentials.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_mac.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include <cstring>
namespace devicev2 {
struct NetworkConfig { uint32_t version=2; bool sta=true,ap=false; char ssid[33]={},password[65]={},apSsid[33]={},apPassword[64]={}; };
static NetworkConfig config;
static SemaphoreHandle_t gate;
static bool started=false,nvsOk=false,change=false,recovery=false,linked=false,initialized=false,applyOk=false,subnetPending=false;
static int64_t applyAt=0,startAt=0;
static char staIp[16]={},apIp[16]="192.168.4.1";
static esp_netif_t* staNet;
static esp_netif_t* apNet;

static void events(void*,esp_event_base_t base,int32_t id,void* data) {
  bool reconnect=false;
  if(base==WIFI_EVENT&&(id==WIFI_EVENT_STA_START||id==WIFI_EVENT_STA_DISCONNECTED)) {
    {MutexLock lock(gate); linked=false; staIp[0]=0; reconnect=config.sta&&config.ssid[0];}
    control::networkAddress(false,"");
    if(reconnect) esp_wifi_connect();
  } else if(base==IP_EVENT&&id==IP_EVENT_STA_GOT_IP) {
    auto e=static_cast<ip_event_got_ip_t*>(data); char ip[16]; esp_ip4addr_ntoa(&e->ip_info.ip,ip,sizeof(ip));
    {MutexLock lock(gate); linked=true; subnetPending=true; snprintf(staIp,sizeof(staIp),"%s",ip);}
    control::networkAddress(true,ip);
  }
}
static bool apply() {
  NetworkConfig copy; {MutexLock lock(gate); copy=config; linked=false; staIp[0]=0;}
  if(started) esp_wifi_stop();
  control::networkAddress(false,"");
  bool ap=copy.ap||recovery;
  if(!copy.sta&&!ap) {MutexLock lock(gate);started=false;applyOk=true; control::networkAddress(false,""); return true;}
  wifi_mode_t mode=ap?(copy.sta&&copy.ssid[0]?WIFI_MODE_APSTA:WIFI_MODE_AP):WIFI_MODE_STA;
  esp_err_t result=esp_wifi_set_mode(mode);
  if(result==ESP_OK&&copy.sta&&copy.ssid[0]) {
    wifi_config_t c={}; memcpy(c.sta.ssid,copy.ssid,strlen(copy.ssid)); memcpy(c.sta.password,copy.password,strlen(copy.password));
    c.sta.threshold.authmode=WIFI_AUTH_WPA2_PSK; result=esp_wifi_set_config(WIFI_IF_STA,&c);
  }
  if(result==ESP_OK&&ap) {
    wifi_config_t c={}; memcpy(c.ap.ssid,copy.apSsid,strlen(copy.apSsid)); c.ap.ssid_len=strlen(copy.apSsid);
    memcpy(c.ap.password,copy.apPassword,strlen(copy.apPassword)); c.ap.channel=1; c.ap.max_connection=4; c.ap.authmode=WIFI_AUTH_WPA2_PSK;
    result=esp_wifi_set_config(WIFI_IF_AP,&c);
  }
  if(result==ESP_OK) result=esp_wifi_start();
  {MutexLock lock(gate); started=result==ESP_OK; startAt=esp_timer_get_time();}
  {MutexLock lock(gate); applyOk=result==ESP_OK;}
  return result==ESP_OK;
}
void startWifi() {
  gate=xSemaphoreCreateMutex(); if(!gate) return;
  snprintf(config.ssid,sizeof(config.ssid),"%s",WIFI_SSID); snprintf(config.password,sizeof(config.password),"%s",WIFI_PASSWORD);
  config.ap=!config.ssid[0];
  uint8_t mac[6]={}; esp_efuse_mac_get_default(mac);
  snprintf(config.apSsid,sizeof(config.apSsid),"Breadboard-%02X%02X%02X",mac[3],mac[4],mac[5]);
  snprintf(config.apPassword,sizeof(config.apPassword),"%08lx%04lx",(unsigned long)esp_random(),(unsigned long)(esp_random()&0xffff));
  if(nvs_flash_init()==ESP_OK) {
    nvsOk=true; nvs_handle_t handle; if(nvs_open("controller_v2",NVS_READONLY,&handle)==ESP_OK) {
      NetworkConfig saved; size_t length=sizeof(saved);
      if(nvs_get_blob(handle,"network",&saved,&length)==ESP_OK&&length==sizeof(saved)&&saved.version==2&&
         memchr(saved.ssid,0,sizeof(saved.ssid))&&memchr(saved.password,0,sizeof(saved.password))&&
         memchr(saved.apSsid,0,sizeof(saved.apSsid))&&memchr(saved.apPassword,0,sizeof(saved.apPassword))&&strlen(saved.apPassword)>=8) config=saved;
      nvs_close(handle);
    }
  } // Never erase NVS automatically on initialization errors.
  if(esp_netif_init()!=ESP_OK||esp_event_loop_create_default()!=ESP_OK) return;
  staNet=esp_netif_create_default_wifi_sta(); apNet=esp_netif_create_default_wifi_ap(); if(!staNet||!apNet) return;
  wifi_init_config_t init=WIFI_INIT_CONFIG_DEFAULT(); init.nvs_enable=0;
  if(esp_wifi_init(&init)!=ESP_OK) return;
  esp_wifi_set_storage(WIFI_STORAGE_RAM);
  esp_event_handler_register(WIFI_EVENT,ESP_EVENT_ANY_ID,events,nullptr);
  esp_event_handler_register(IP_EVENT,IP_EVENT_STA_GOT_IP,events,nullptr);
  initialized=true; apply();
}
void pollWifi() {
  if(!gate||!initialized) return;
  bool readdress=false;{MutexLock lock(gate);readdress=subnetPending;subnetPending=false;}
  if(readdress) {
    esp_netif_ip_info_t station={},access={};
    if(esp_netif_get_ip_info(staNet,&station)==ESP_OK&&esp_netif_get_ip_info(apNet,&access)==ESP_OK&&
       (station.ip.addr&station.netmask.addr)==(access.ip.addr&station.netmask.addr)) {
      if(!control::reserveFiles()) {MutexLock lock(gate);subnetPending=true;}
      else {
        // Select a private AP /24 outside the station's current network.
        const char* options[]={"192.168.4.1","192.168.50.1","172.31.240.1","10.77.0.1"};
        for(auto candidate:options) {
          esp_netif_str_to_ip4(candidate,&access.ip);
          if((station.ip.addr&station.netmask.addr)!=(access.ip.addr&station.netmask.addr)) {
            access.gw=access.ip;esp_netif_str_to_ip4("255.255.255.0",&access.netmask);
            esp_netif_dhcps_stop(apNet);
            if(esp_netif_set_ip_info(apNet,&access)==ESP_OK) {MutexLock lock(gate);snprintf(apIp,sizeof(apIp),"%s",candidate);}
            esp_netif_dhcps_start(apNet);break;
          }
        }
        control::releaseFiles();
      }
    }
  }
  bool doApply=false;
  {MutexLock lock(gate);
    if(change&&esp_timer_get_time()>=applyAt) {change=false; recovery=false; doApply=true;}
    else if(started&&config.sta&&config.ssid[0]&&!linked&&!config.ap&&!recovery&&esp_timer_get_time()-startAt>30000000&&!control::busyNow()) {
      if(control::reserveFiles()) {recovery=true; doApply=true;}
    }
  }
  if(doApply) {apply(); control::releaseFiles();}
}
Reply wifiRequest(const char* method,const char* action,cJSON* a) {
  if(!gate||!initialized) return error(503,"网络管理未初始化，USB仍可使用");
  if(!strcmp(action,"state")&&!strcmp(method,"GET")) {
    MutexLock lock(gate); auto j=cJSON_CreateObject(); cJSON_AddBoolToObject(j,"connected",linked);
    cJSON_AddBoolToObject(j,"sta_enabled",config.sta); cJSON_AddBoolToObject(j,"ap_enabled",config.ap||recovery);
    cJSON_AddBoolToObject(j,"running",started); cJSON_AddBoolToObject(j,"apply_ok",applyOk); cJSON_AddBoolToObject(j,"persistent_available",nvsOk); cJSON_AddBoolToObject(j,"applying",change);
    cJSON_AddStringToObject(j,"ssid",config.ssid); cJSON_AddStringToObject(j,"ap_ssid",config.apSsid);
    cJSON_AddStringToObject(j,"ap_password",config.apPassword); cJSON_AddStringToObject(j,"sta_ip",staIp);
    cJSON_AddStringToObject(j,"ap_ip",apIp); Reply r; r.body=json(j); return r;
  }
  if(strcmp(method,"POST")) return error(405,"网络操作需POST");
  if(!strcmp(action,"scan")) {
    if(!control::reserveFiles()) return error(409,"采集或文件任务中不能扫描网络");
    wifi_mode_t previous=WIFI_MODE_NULL;esp_wifi_get_mode(&previous);
    bool wasStarted=started;
    esp_err_t result=esp_wifi_set_mode(previous==WIFI_MODE_AP?WIFI_MODE_APSTA:previous==WIFI_MODE_NULL?WIFI_MODE_STA:previous);
    if(result==ESP_OK&&!wasStarted) result=esp_wifi_start();
    wifi_scan_config_t scan={}; if(result==ESP_OK)result=esp_wifi_scan_start(&scan,true);
    auto list=cJSON_CreateArray(); if(result==ESP_OK) {
      uint16_t count=16; wifi_ap_record_t points[16]={}; result=esp_wifi_scan_get_ap_records(&count,points);
      for(unsigned i=0;i<count&&result==ESP_OK;i++) {auto p=cJSON_CreateObject(); cJSON_AddStringToObject(p,"ssid",(char*)points[i].ssid); cJSON_AddNumberToObject(p,"rssi",points[i].rssi); cJSON_AddItemToArray(list,p);}
    }
    if(!wasStarted) esp_wifi_stop();
    esp_wifi_set_mode(previous);
    control::releaseFiles(); if(result!=ESP_OK) {cJSON_Delete(list); return error(409,"扫描失败，请检查主控Wi-Fi是否启用");}
    Reply r; r.body=json(list); return r;
  }
  if(strcmp(action,"save")) return error(404,"网络操作不存在");
  if(!nvsOk) return error(503,"NVS不可用；未自动擦除，请保留现场");
  NetworkConfig next; {MutexLock lock(gate); if(change) return error(409,"网络配置正在应用"); next=config;}
  const char* ssid=text(a,"ssid"),*password=text(a,"password"),*apSsid=text(a,"ap_ssid"),*apPass=text(a,"ap_password");
  auto sta=cJSON_GetObjectItem(a,"sta_enabled"),ap=cJSON_GetObjectItem(a,"ap_enabled");
  if(!cJSON_IsBool(sta)||!cJSON_IsBool(ap)||strlen(ssid)>32||strlen(password)>63||!strlen(apSsid)||strlen(apSsid)>32||strlen(apPass)<8||strlen(apPass)>63)
    return error(400,"检查网络名称、密码长度和开关");
  const bool keepPassword=password[0]==0&&!strcmp(ssid,next.ssid);
  const char* effectivePassword=keepPassword?next.password:password;
  if(cJSON_IsTrue(sta)&&(!ssid[0]||strlen(effectivePassword)<8)) return error(400,"Wi-Fi名称不能为空，密码需8至63字节");
  if(!control::reserveFiles()) return error(409,"采集或文件任务中不能修改网络");
  next.sta=cJSON_IsTrue(sta); next.ap=cJSON_IsTrue(ap); snprintf(next.ssid,sizeof(next.ssid),"%s",ssid);
  // next already owns the previous password; copying it onto itself is undefined.
  if(!keepPassword) snprintf(next.password,sizeof(next.password),"%s",password);
  snprintf(next.apSsid,sizeof(next.apSsid),"%s",apSsid); snprintf(next.apPassword,sizeof(next.apPassword),"%s",apPass);
  nvs_handle_t handle; esp_err_t saved=nvs_open("controller_v2",NVS_READWRITE,&handle);
  if(saved==ESP_OK) {saved=nvs_set_blob(handle,"network",&next,sizeof(next)); if(saved==ESP_OK) saved=nvs_commit(handle); nvs_close(handle);}
  if(saved!=ESP_OK) {control::releaseFiles(); return error(500,"配置保存失败，未切换网络");}
  {MutexLock lock(gate); config=next; change=true; applyAt=esp_timer_get_time()+500000;}
  Reply r; r.code=202; r.body="{\"accepted\":true}"; return r;
}
}
