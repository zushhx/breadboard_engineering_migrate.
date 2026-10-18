#include "device_v2.h"
#include "esp_timer.h"
#include <cmath>
#include <cstring>
namespace devicev2 {
std::string json(cJSON* value) {
  if(!value) return "{}";
  char* data=cJSON_PrintUnformatted(value); std::string result=data?data:"{}"; cJSON_free(data); cJSON_Delete(value); return result;
}
Reply error(int code,const char* message) { Reply r; r.code=code; auto j=cJSON_CreateObject(); cJSON_AddStringToObject(j,"error",message); r.body=json(j); return r; }
const char* text(cJSON* obj,const char* key,const char* fallback) { auto v=cJSON_GetObjectItemCaseSensitive(obj,key); return cJSON_IsString(v)?v->valuestring:fallback; }
bool integer(cJSON* obj,const char* key,int low,int high,int& value) {
  auto v=cJSON_GetObjectItemCaseSensitive(obj,key);
  if(!cJSON_IsNumber(v)||!std::isfinite(v->valuedouble)||v->valuedouble<low||v->valuedouble>high||v->valuedouble!=v->valueint) return false;
  value=v->valueint; return true;
}
void initFiles();
void initialize() {initFiles();}
Reply dispatch(const char* method,const char* path,const char* body,const char* key) {
  if(strlen(path)>128||strchr(path,'?')) return error(400,"接口路径无效");
  bool get=!strcmp(method,"GET"); if(!get&&strcmp(method,"POST")) return error(405,"方法不支持");
  if(get&&!strcmp(path,"/api/status")) { Reply r; r.body=control::statusJson(); return r; }
  if(get&&!strcmp(path,"/api/trend")) { Reply r; r.body=control::trendJson(); return r; }
  if(get&&!strcmp(path,"/api/v2/capabilities")) { Reply r; r.body="{\"version\":2,\"usb\":true,\"wifi_config\":true,\"files\":true,\"record_time_names\":true,\"owner_timeout_ms\":10000,\"max_clients\":4}"; return r; }
  if(strlen(body)>4096) return error(413,"请求过长");
  cJSON* args=body[0]?cJSON_Parse(body):cJSON_CreateObject();
  if(!cJSON_IsObject(args)) { cJSON_Delete(args); return error(400,"JSON对象无效"); }
  const char* token=(key&&key[0])?key:text(args,"boot");
  if(!get&&strcmp(token,control::sessionKey())) { cJSON_Delete(args); return error(403,"设备会话已变化，请刷新页面"); }
  Reply reply;
  if(!strncmp(path,"/api/v2/session/",16)) {
    if(get&&strcmp(path+16,"state")) reply=error(405,"会话操作需POST");
    else reply=control::sessionRequest(path+16,args);
  } else if(!strncmp(path,"/api/v2/network/",16)) reply=wifiRequest(method,path+16,args);
  else if(!strncmp(path,"/api/v2/files/",14)) reply=filesRequest(method,path+14,args);
  else reply=error(404,"接口不存在或需要v2客户端");
  cJSON_Delete(args); return reply;
}
}
