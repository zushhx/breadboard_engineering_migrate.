#include "network_sdk_stubs.h"
#include "device_v2.h"
#include <vector>
#include <iostream>
#include <stdexcept>
#include <cassert>
#include <cstdio>
static int64_t clockUs=100;
static bool busy=false,reserved=false,failCommit=false,failSet=false,failApply=false,failNvs=false;
static unsigned writes=0,commits=0,checks=0,applies=0;
static std::vector<uint8_t> saved,pending;
static wifi_config_t station;
static wifi_mode_t mode=WIFI_MODE_NULL;
static esp_netif_t staNetHost,apNetHost;
int64_t esp_timer_get_time(){return clockUs;}
uint32_t esp_random(){return 1234;}
esp_err_t esp_efuse_mac_get_default(uint8_t* p){const uint8_t mac[]={0x44,0x1b,0xf6,0x81,0x91,0x10};memcpy(p,mac,6);return ESP_OK;}
esp_err_t nvs_flash_init(){return failNvs?ESP_FAIL:ESP_OK;}
esp_err_t nvs_open(const char* space,int,nvs_handle_t* h){assert(!strcmp(space,"controller_v2"));*h=1;return ESP_OK;}
esp_err_t nvs_get_blob(nvs_handle_t,const char*,void* out,size_t* n){if(saved.empty()||*n<saved.size())return ESP_FAIL;memcpy(out,saved.data(),saved.size());*n=saved.size();return ESP_OK;}
esp_err_t nvs_set_blob(nvs_handle_t,const char*,const void* p,size_t n){++writes;if(failSet)return ESP_FAIL;pending.assign((const uint8_t*)p,(const uint8_t*)p+n);return ESP_OK;}
esp_err_t nvs_commit(nvs_handle_t){++commits;if(failCommit)return ESP_FAIL;saved=pending;return ESP_OK;}
void nvs_close(nvs_handle_t){}
esp_err_t esp_netif_init(){return ESP_OK;}
esp_err_t esp_event_loop_create_default(){return ESP_OK;}
esp_netif_t* esp_netif_create_default_wifi_sta(){return &staNetHost;}
esp_netif_t* esp_netif_create_default_wifi_ap(){return &apNetHost;}
esp_err_t esp_wifi_init(const wifi_init_config_t* c){assert(c->nvs_enable==0);return ESP_OK;}
esp_err_t esp_wifi_set_storage(int s){assert(s==WIFI_STORAGE_RAM);return ESP_OK;}
esp_err_t esp_event_handler_register(esp_event_base_t,int,void(*)(void*,esp_event_base_t,int32_t,void*),void*){return ESP_OK;}
esp_err_t esp_wifi_connect(){return ESP_OK;}
esp_err_t esp_wifi_stop(){return ESP_OK;}
esp_err_t esp_wifi_start(){++applies;return failApply?ESP_FAIL:ESP_OK;}
esp_err_t esp_wifi_set_mode(wifi_mode_t m){mode=m;return ESP_OK;}
esp_err_t esp_wifi_get_mode(wifi_mode_t* p){*p=mode;return ESP_OK;}
esp_err_t esp_wifi_set_config(int which,const wifi_config_t* p){if(which==WIFI_IF_STA)station=*p;return ESP_OK;}
esp_err_t esp_wifi_scan_start(const wifi_scan_config_t*,bool){return ESP_OK;}
esp_err_t esp_wifi_scan_get_ap_records(uint16_t* n,wifi_ap_record_t* p){*n=1;strcpy((char*)p[0].ssid,"TestNet");return ESP_OK;}
esp_err_t esp_netif_get_ip_info(esp_netif_t* n,esp_netif_ip_info_t* p){*p=n->info;return ESP_OK;}
esp_err_t esp_netif_set_ip_info(esp_netif_t* n,const esp_netif_ip_info_t* p){n->info=*p;return ESP_OK;}
esp_err_t esp_netif_dhcps_stop(esp_netif_t*){return ESP_OK;}
esp_err_t esp_netif_dhcps_start(esp_netif_t*){return ESP_OK;}
const char* esp_ip4addr_ntoa(const ip4_addr_t*,char* p,int n){snprintf(p,n,"192.168.8.2");return p;}
esp_err_t esp_netif_str_to_ip4(const char*,ip4_addr_t* p){p->addr=1;return ESP_OK;}
namespace control {
bool busyNow(){return busy||reserved;}
bool reserveFiles(){if(busyNow())return false;reserved=true;return true;}
void releaseFiles(){assert(reserved);reserved=false;}
void networkAddress(bool,const char*){}
}
namespace devicev2 {
std::string json(cJSON* j){char* p=cJSON_PrintUnformatted(j);std::string s=p?p:"null";cJSON_free(p);cJSON_Delete(j);return s;}
Reply error(int code,const char* msg){Reply r;r.code=code;auto j=cJSON_CreateObject();cJSON_AddStringToObject(j,"error",msg);r.body=json(j);return r;}
const char* text(cJSON* j,const char* k,const char* fallback){auto v=cJSON_GetObjectItem(j,k);return cJSON_IsString(v)?v->valuestring:fallback;}
}
// Include the production implementation, not a reimplementation of its validation/state logic.
#include "network_v2.cpp"
static void check(bool ok,const char* name){++checks;if(!ok)throw std::runtime_error(name);std::cout<<"PASS "<<name<<"\n";}
static devicev2::Reply save(const char* ssid,const char* password){
  auto j=cJSON_CreateObject();cJSON_AddBoolToObject(j,"sta_enabled",true);cJSON_AddBoolToObject(j,"ap_enabled",true);
  cJSON_AddStringToObject(j,"ssid",ssid);cJSON_AddStringToObject(j,"password",password);cJSON_AddStringToObject(j,"ap_ssid","TestAP");cJSON_AddStringToObject(j,"ap_password","test-ap-password");
  auto r=devicev2::wifiRequest("POST","save",j);cJSON_Delete(j);return r;
}
static void apply(){clockUs+=600000;devicev2::pollWifi();check(!reserved,"network apply releases acquisition exclusion");}
static bool stateFlag(const char* key){auto r=devicev2::wifiRequest("GET","state",nullptr);auto j=cJSON_Parse(r.body.c_str());bool yes=cJSON_IsTrue(cJSON_GetObjectItem(j,key));cJSON_Delete(j);return yes;}
int main(){try{
  devicev2::startWifi();check(mode==WIFI_MODE_AP,"empty credentials bootstrap recovery AP");
  check(save("TestNet","original-password").code==202,"new credentials accepted");apply();
  check(!strcmp((char*)station.sta.password,"original-password"),"new credentials reach WiFi driver");
  check(save("TestNet","").code==202,"same SSID blank password accepted");apply();
  check(!strcmp((char*)station.sta.password,"original-password"),"same SSID blank password preserves WiFi password");
  auto persisted=saved;devicev2::NetworkConfig blob;assert(persisted.size()==sizeof(blob));memcpy(&blob,persisted.data(),sizeof(blob));
  check(!strcmp(blob.password,"original-password"),"preserved password committed unchanged to NVS");
  check(save("TestNet","replacement-password").code==202,"replacement password accepted");apply();
  check(!strcmp((char*)station.sta.password,"replacement-password"),"replacement password reaches driver");
  auto oldWrites=writes;check(save("OtherNet","").code==400&&writes==oldWrites&&!reserved,"different SSID blank password rejected without persistence");
  busy=true;check(save("TestNet","").code==409&&writes==oldWrites,"busy acquisition rejects network save");
  check(devicev2::wifiRequest("POST","scan",nullptr).code==409&&!reserved,"busy acquisition rejects scan");busy=false;
  auto before=saved;failSet=true;check(save("TestNet","must-not-commit").code==500&&!reserved&&saved==before,"NVS set failure preserves current configuration");failSet=false;
  failCommit=true;auto oldCommits=commits;check(save("TestNet","must-not-commit").code==500&&!reserved&&saved==before&&commits==oldCommits+1,"NVS commit failure does not schedule reconfiguration");failCommit=false;
  check(save("TestNet","").code==202,"blank password remains usable after failed saves");apply();
  check(!strcmp((char*)station.sta.password,"replacement-password"),"failed save did not change in-memory password");
  failApply=true;check(save("OtherNet","valid-password").code==202,"persistence accepted separately from network start");apply();
  check(!stateFlag("apply_ok")&&!stateFlag("connected"),"driver failure is visible and not reported as connected");failApply=false;
  check(save("TestNet","replacement-password").code==202,"USB request can recover failed network configuration");apply();
  check(stateFlag("apply_ok"),"recovered driver start reported successful");
  auto state=devicev2::wifiRequest("GET","state",nullptr);check(state.body.find("replacement-password")==std::string::npos,"STA secret not exposed in network state");
  auto beforeApplies=applies;devicev2::startWifi();check(applies==beforeApplies+1&&mode==WIFI_MODE_APSTA&&!strcmp((char*)station.sta.ssid,"TestNet")&&!strcmp((char*)station.sta.password,"replacement-password"),"restart reloads persisted SSID, password and APSTA mode");
  devicev2::nvsOk=false;oldWrites=writes;check(save("TestNet","").code==503&&writes==oldWrites,"unavailable NVS rejects save without erase");
  std::cout<<"RESULT "<<checks<<" production network checks; host SDK/NVS adapters, not radio or real NVS\n";return 0;
}catch(const std::exception& e){std::cerr<<"FAIL "<<e.what()<<"\n";return 1;}}
