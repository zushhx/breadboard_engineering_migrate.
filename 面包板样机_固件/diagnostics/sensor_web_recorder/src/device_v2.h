#pragma once
#include <string>
#include <cstddef>
#include "cJSON.h"
namespace devicev2 {
struct Reply { int code=200; std::string body; const char* type="application/json; charset=utf-8"; };
Reply dispatch(const char* method,const char* path,const char* body,const char* key);
void initialize();
void pollStorage(const char* drive);
void startUsb();
void startWifi();
void pollWifi();
Reply wifiRequest(const char* method,const char* action,cJSON* args);
Reply filesRequest(const char* method,const char* action,cJSON* args);
std::string json(cJSON* value);
Reply error(int code,const char* message);
const char* text(cJSON* obj,const char* key,const char* fallback="");
bool integer(cJSON* obj,const char* key,int low,int high,int& value);
}
namespace control {
const char* sessionKey();
// The sink must consume the bounded buffer synchronously; it cannot retain it.
using TelemetrySink = bool (*)(int code,const char* data,size_t size,void* context);
bool streamTelemetry(bool trend,TelemetrySink sink,void* context);
std::string statusJson();
std::string trendJson();
devicev2::Reply sessionRequest(const char* action,cJSON* args);
bool busyNow();
bool reserveFiles();
void releaseFiles();
void networkAddress(bool connected,const char* ip);
}
