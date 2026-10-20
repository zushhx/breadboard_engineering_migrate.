#include "device_v2.h"
#include "file_policy.h"
#include "esp_http_server.h"
#include "http_response_policy.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "mbedtls/base64.h"
#include <cstring>
#include <cctype>
namespace devicev2 {
static Reply waitFile(const char* action,cJSON* args) {
  char job[33];for(unsigned i=0;i<4;i++)snprintf(job+i*8,9,"%08lx",(unsigned long)esp_random());
  cJSON_AddStringToObject(args,"job",job);Reply accepted=filesRequest("POST",action,args);
  if(accepted.code!=202)return accepted;
  int64_t deadline=esp_timer_get_time()+6000000;
  while(esp_timer_get_time()<deadline) {
    vTaskDelay(pdMS_TO_TICKS(5));auto state=filesRequest("GET","state",nullptr);auto parsed=cJSON_Parse(state.body.c_str());
    if(strcmp(text(parsed,"job"),job)) {cJSON_Delete(parsed);return error(409,"文件任务已变化，未自动重发");}
    auto running=cJSON_GetObjectItem(parsed,"running");
    if(cJSON_IsFalse(running)) {
      int code=500;integer(parsed,"code",100,599,code);Reply result;result.code=code;
      result.body=json(cJSON_Duplicate(cJSON_GetObjectItem(parsed,"result"),true));cJSON_Delete(parsed);return result;
    }
    cJSON_Delete(parsed);
  }
  return error(504,"文件任务超时，状态未确认");
}
static std::string encode(const std::string& value) {
  std::string result;char part[4];
  for(unsigned char c:value) {if((c>='a'&&c<='z')||(c>='A'&&c<='Z')||(c>='0'&&c<='9')||c=='.'||c=='-'||c=='_')result+=c;else{snprintf(part,sizeof(part),"%%%02X",c);result+=part;}}
  return result;
}
esp_err_t downloadHandler(httpd_req_t* r) {
  if(boundedhttp::prepare(r)!=ESP_OK) return ESP_FAIL;
  char query[1024]={},encoded[768]={};
  if(httpd_req_get_url_query_str(r,query,sizeof(query))!=ESP_OK||httpd_query_key_value(query,"path",encoded,sizeof(encoded))!=ESP_OK) return boundedhttp::error(r,HTTPD_400_BAD_REQUEST,"path required");
  std::string path;
  for(size_t i=0;encoded[i];i++) {
    if(encoded[i]=='%') {unsigned byte=0;if(!encoded[i+1]||!encoded[i+2]||!isxdigit((unsigned char)encoded[i+1])||!isxdigit((unsigned char)encoded[i+2])||sscanf(encoded+i+1,"%2x",&byte)!=1||!byte)return boundedhttp::error(r,HTTPD_400_BAD_REQUEST,"invalid encoding");path+=char(byte);i+=2;}
    else path+=encoded[i]=='+'?' ':encoded[i];
  }
  if(!validPath(path))return boundedhttp::error(r,HTTPD_400_BAD_REQUEST,"invalid path");
  auto args=cJSON_CreateObject();cJSON_AddStringToObject(args,"path",path.c_str());auto begin=waitFile("begin_read",args);cJSON_Delete(args);
  if(begin.code!=200) {char code[32];snprintf(code,sizeof(code),"%d File Rejected",begin.code);httpd_resp_set_status(r,code);httpd_resp_set_type(r,"application/json");return boundedhttp::send(r,begin.body.data(),begin.body.size());}
  auto meta=cJSON_Parse(begin.body.c_str());std::string transfer=text(meta,"transfer");cJSON_Delete(meta);
  std::string disposition="attachment; filename*=UTF-8''"+encode(path.substr(path.find_last_of('/')+1));
  httpd_resp_set_type(r,"application/octet-stream");httpd_resp_set_hdr(r,"Content-Disposition",disposition.c_str());httpd_resp_set_hdr(r,"Cache-Control","no-store");
  bool ok=true;
  for(;;) {
    args=cJSON_CreateObject();cJSON_AddStringToObject(args,"transfer",transfer.c_str());auto chunk=waitFile("read",args);cJSON_Delete(args);
    if(chunk.code!=200){ok=false;break;}
    auto data=cJSON_Parse(chunk.body.c_str());bool end=cJSON_IsTrue(cJSON_GetObjectItem(data,"done"));
    if(end){cJSON_Delete(data);break;}
    uint8_t bytes[2048];size_t length=0;const char* base64=text(data,"data");
    ok=mbedtls_base64_decode(bytes,sizeof(bytes),&length,(const uint8_t*)base64,strlen(base64))==0;
    if(ok)ok=httpd_resp_send_chunk(r,(char*)bytes,length)==ESP_OK;
    cJSON_Delete(data);if(!ok)break;
  }
  if(!ok) {args=cJSON_CreateObject();cJSON_AddStringToObject(args,"transfer",transfer.c_str());waitFile("cancel",args);cJSON_Delete(args);return ESP_FAIL;}
  return boundedhttp::endChunks(r);
}
}
