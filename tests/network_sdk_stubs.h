#pragma once
// Host-only SDK boundaries. network_v2.cpp itself is compiled unchanged by this adapter.
#include <cstdint>
#include <cstddef>
#include <mutex>
#include <cstring>
using esp_err_t=int;
constexpr int ESP_OK=0, ESP_FAIL=-1, ESP_EVENT_ANY_ID=-1, portMAX_DELAY=0;
using SemaphoreHandle_t=std::mutex*;
inline auto xSemaphoreCreateMutex(){return new std::mutex;}
inline void xSemaphoreTake(SemaphoreHandle_t p,int){p->lock();}
inline void xSemaphoreGive(SemaphoreHandle_t p){p->unlock();}
using esp_event_base_t=const char*;
inline constexpr char WIFI_EVENT[]="wifi",IP_EVENT[]="ip";
constexpr int WIFI_EVENT_STA_START=1,WIFI_EVENT_STA_DISCONNECTED=2,IP_EVENT_STA_GOT_IP=3;
enum wifi_mode_t {WIFI_MODE_NULL,WIFI_MODE_STA,WIFI_MODE_AP,WIFI_MODE_APSTA};
constexpr int WIFI_AUTH_WPA2_PSK=3,WIFI_IF_STA=0,WIFI_IF_AP=1,WIFI_STORAGE_RAM=0;
struct ip4_addr_t {uint32_t addr=0;};
struct esp_netif_ip_info_t {ip4_addr_t ip,netmask,gw;};
struct esp_netif_t {esp_netif_ip_info_t info;};
struct ip_event_got_ip_t {esp_netif_ip_info_t ip_info;};
struct wifi_config_t {
  struct {uint8_t ssid[32]={},password[64]={};struct {int authmode=0;} threshold;} sta;
  struct {uint8_t ssid[32]={},password[64]={};unsigned ssid_len=0,channel=0,max_connection=0;int authmode=0;} ap;
};
struct wifi_init_config_t {int nvs_enable=1;};
#define WIFI_INIT_CONFIG_DEFAULT() wifi_init_config_t{}
struct wifi_scan_config_t {};
struct wifi_ap_record_t {uint8_t ssid[33]={};int rssi=-40;};
using nvs_handle_t=int;
constexpr int NVS_READONLY=0,NVS_READWRITE=1;
int64_t esp_timer_get_time();
uint32_t esp_random();
esp_err_t esp_efuse_mac_get_default(uint8_t*);
esp_err_t nvs_flash_init();
esp_err_t nvs_open(const char*,int,nvs_handle_t*);
esp_err_t nvs_get_blob(nvs_handle_t,const char*,void*,size_t*);
esp_err_t nvs_set_blob(nvs_handle_t,const char*,const void*,size_t);
esp_err_t nvs_commit(nvs_handle_t);
void nvs_close(nvs_handle_t);
esp_err_t esp_netif_init();
esp_err_t esp_event_loop_create_default();
esp_netif_t* esp_netif_create_default_wifi_sta();
esp_netif_t* esp_netif_create_default_wifi_ap();
esp_err_t esp_wifi_init(const wifi_init_config_t*);
esp_err_t esp_wifi_set_storage(int);
esp_err_t esp_event_handler_register(esp_event_base_t,int,void(*)(void*,esp_event_base_t,int32_t,void*),void*);
esp_err_t esp_wifi_connect();
esp_err_t esp_wifi_stop();
esp_err_t esp_wifi_start();
esp_err_t esp_wifi_set_mode(wifi_mode_t);
esp_err_t esp_wifi_get_mode(wifi_mode_t*);
esp_err_t esp_wifi_set_config(int,const wifi_config_t*);
esp_err_t esp_wifi_scan_start(const wifi_scan_config_t*,bool);
esp_err_t esp_wifi_scan_get_ap_records(uint16_t*,wifi_ap_record_t*);
esp_err_t esp_netif_get_ip_info(esp_netif_t*,esp_netif_ip_info_t*);
esp_err_t esp_netif_set_ip_info(esp_netif_t*,const esp_netif_ip_info_t*);
esp_err_t esp_netif_dhcps_stop(esp_netif_t*);
esp_err_t esp_netif_dhcps_start(esp_netif_t*);
const char* esp_ip4addr_ntoa(const ip4_addr_t*,char*,int);
esp_err_t esp_netif_str_to_ip4(const char*,ip4_addr_t*);
