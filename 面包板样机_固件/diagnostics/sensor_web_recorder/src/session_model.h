#pragma once
#include <cstdint>
#include <cstring>
#include <cstdio>
#include "web_model.h"
namespace control {
// Portable state machine. All accesses share the Model guard.
struct Sessions {
  static constexpr uint64_t timeoutUs = 10000000;
  struct Client { char id[33] = {}; uint64_t seen = 0; } clients[4];
  char owner[33] = {}, pendingOwner[33] = {}, pendingRun[9] = {};
  unsigned pendingSeconds = 0, pendingRangeG = 2;
  char pendingLocal[18] = {}; uint64_t pendingNamedAt = 0;
  uint32_t pendingSerial = 0; uint64_t pendingCapacity = 0;
  bool files = false;
  static bool valid(const char* id) {
    if (!id || strlen(id) != 32) return false;
    for (unsigned i=0;i<32;i++) if (!((id[i]>='0'&&id[i]<='9')||(id[i]>='a'&&id[i]<='f')||(id[i]>='A'&&id[i]<='F'))) return false;
    return true;
  }
  bool alive(const char* id, uint64_t now) const {
    if (!id || !id[0]) return false;
    for (auto& c:clients) if (!strcmp(c.id,id)) return now<c.seen || now-c.seen < timeoutUs;
    return false;
  }
  bool touch(const char* id, uint64_t now) {
    if (!valid(id)) return false;
    for(auto& c:clients) if(!strcmp(c.id,id)) { if(now>c.seen)c.seen=now; return true; }
    for(auto& c:clients) if(!c.id[0] || (now>=c.seen && now-c.seen>=timeoutUs && strcmp(c.id,owner) && strcmp(c.id,pendingOwner))) {
      snprintf(c.id,sizeof(c.id),"%s",id); c.seen=now; return true;
    }
    return false;
  }
  void cancelPending() { pendingOwner[0]=pendingRun[0]=pendingLocal[0]=0; pendingSeconds=0; pendingRangeG=2; pendingNamedAt=0; }
  void release(Model& m,const char* id) {
    if(!valid(id)) return;
    if(!strcmp(owner,id)) { m.requestStop(); snprintf(m.s.detail,sizeof(m.s.detail),"控制页面已退出；正在安全停止并保存"); }
    if(!strcmp(pendingOwner,id)) cancelPending();
    for(auto& c:clients) if(!strcmp(c.id,id)) c={};
  }
  void tick(Model& m,uint64_t now) {
    if(pendingOwner[0]&&!alive(pendingOwner,now)) cancelPending();
    if(owner[0]&&busy(m.s.phase)&&!alive(owner,now)) {
      m.requestStop(); snprintf(m.s.detail,sizeof(m.s.detail),"控制端失联；正在安全停止并保存");
    }
  }
  bool start(Model& m,const char* id,uint64_t now,uint32_t serial,uint64_t capacity,const char* run,unsigned seconds,const char* local="",unsigned rangeG=2) {
    tick(m,now);
    if(files||pendingOwner[0]||!alive(id,now)||!m.submit(serial,capacity,run,seconds,local,now,rangeG)) return false;
    snprintf(owner,sizeof(owner),"%s",id); return true;
  }
  bool takeover(Model& m,const char* id,uint64_t now,const char* expected,uint32_t serial,uint64_t capacity,const char* run,unsigned seconds,const char* local="",unsigned rangeG=2) {
    tick(m,now);
    if(files||pendingOwner[0]||!alive(id,now)||!busy(m.s.phase)||m.s.phase==Phase::Closing||m.s.phase==Phase::Verifying||
       strcmp(m.s.run,expected)||!strcmp(owner,id)||serial!=m.s.serial||capacity!=m.s.capacity||
       !sensor_range::valid(rangeG)||!recorder::runId(run)||!strcmp(run,m.s.run)||seconds<1||seconds>300||!local||(local[0]&&!recorder::localStamp(local))) return false;
    snprintf(pendingOwner,sizeof(pendingOwner),"%s",id); snprintf(pendingRun,sizeof(pendingRun),"%s",run);
    pendingSerial=serial; pendingCapacity=capacity; pendingSeconds=seconds; pendingRangeG=rangeG;
    snprintf(pendingLocal,sizeof(pendingLocal),"%s",local);pendingNamedAt=now;
    if(m.s.phase==Phase::Queued) { m.requestStop(); finish(m,true,now); return true; }
    m.requestStop(); snprintf(m.s.detail,sizeof(m.s.detail),"其他页面请求接管；正在停止并保存当前记录"); return true;
  }
  void finish(Model& m,bool passed,uint64_t now) {
    owner[0]=0;
    if(pendingOwner[0] && passed && alive(pendingOwner,now)) {
      char id[33],run[9],local[18]; snprintf(id,sizeof(id),"%s",pendingOwner); snprintf(run,sizeof(run),"%s",pendingRun);snprintf(local,sizeof(local),"%s",pendingLocal);
      auto serial=pendingSerial; auto capacity=pendingCapacity; auto seconds=pendingSeconds;
      auto requestedAt=pendingNamedAt; auto rangeG=pendingRangeG;
      cancelPending();
      if(m.submit(serial,capacity,run,seconds,local,requestedAt,rangeG)) snprintf(owner,sizeof(owner),"%s",id);
    } else cancelPending();
  }
};
}
