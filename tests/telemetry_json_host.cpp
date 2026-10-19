#include "telemetry_json.h"
#include <cassert>
#include <cstdlib>
#include <cstring>
#include <new>
#include <limits>

static bool denyAllocations=false;
static unsigned deniedAllocations=0,checks=0;
void* operator new(std::size_t n) {
  if(denyAllocations){++deniedAllocations;throw std::bad_alloc();}
  if(auto p=std::malloc(n?n:1))return p;
  throw std::bad_alloc();
}
void* operator new[](std::size_t n){return ::operator new(n);}
void operator delete(void* p) noexcept {std::free(p);}
void operator delete[](void* p) noexcept {std::free(p);}
void operator delete(void* p,std::size_t) noexcept {std::free(p);}
void operator delete[](void* p,std::size_t) noexcept {std::free(p);}
static void require(bool value){++checks;assert(value);}
template<size_t N> void fill(char(&s)[N],char c){memset(s,c,N-1);s[N-1]=0;}
int main(){
  char out[control::kTelemetryJsonCapacity+1],key[33],owner[33],waiting[33];
  fill(key,'a');fill(owner,'b');fill(waiting,'c');
  control::Snapshot s;
  denyAllocations=true;
  auto empty=control::renderStatusJson(out,sizeof(out)-1,key,s,"","",false);
  require(empty>0&&strlen(out)==empty);require(!deniedAllocations);
  denyAllocations=false;printf("EMPTY=%s\n",out);
  s.phase=control::Phase::Recording;s.ready=s.connected=s.inaValid=s.spaceValid=true;
  s.serial=s.seconds=s.errors=s.queue=s.fifoHigh=s.queueHigh=s.maxWriteUs=s.maxSyncUs=s.freeHeap=s.previewSeq=UINT32_MAX;
  s.capacity=s.written=s.elapsedUs=s.inaUs=s.freeBytes=s.previewUs=UINT64_MAX;
  s.busMv=s.powerUw=INT32_MAX;s.currentUa=INT32_MIN;
  fill(s.ip,'1');fill(s.run,'A');fill(s.detail,'D');fill(s.sha,'f');fill(s.recordName,'N');fill(s.recordPath,'P');
  s.previewCount=control::kPreview;
  for(unsigned i=0;i<control::kPreview;++i){s.xyz[i][0]=-32768;s.xyz[i][1]=32767;s.xyz[i][2]=i%2?-1:0;}
  denyAllocations=true;
  out[sizeof(out)-1]='@';
  auto n=control::renderStatusJson(out,sizeof(out)-1,key,s,owner,waiting,true);
  require(n>0&&n<sizeof(out)-1);require(strlen(out)==n);require(out[sizeof(out)-1]=='@');
  require(!deniedAllocations);
  denyAllocations=false;printf("STATUS=%s\n",out);
  denyAllocations=true;
  require(control::renderStatusJson(out,n+1,key,s,owner,waiting,true)==n);
  out[n]='@';require(control::renderStatusJson(out,n,key,s,owner,waiting,true)==0);require(out[n]=='@');
  out[1]='@';require(control::renderStatusJson(out,1,key,s,owner,waiting,true)==0);require(out[1]=='@');
  require(control::renderStatusJson(nullptr,0,key,s,owner,waiting,true)==0);
  s.previewCount=control::kPreview+1;require(control::renderStatusJson(out,sizeof(out),key,s,owner,waiting,true)==0);s.previewCount=control::kPreview;
  control::TrendPoint points[control::kTrendPoints];
  for(auto& p:points){p.endSample=p.rmsUg[0]=p.rmsUg[1]=p.rmsUg[2]=p.nearFullScale=UINT32_MAX;}
  auto t=control::renderTrendJson(out,sizeof(out)-1,key,s.run,points,control::kTrendPoints);
  require(t>0&&t<sizeof(out)-1);require(strlen(out)==t);require(!deniedAllocations);
  denyAllocations=false;printf("TREND=%s\n",out);
  denyAllocations=true;
  require(control::renderTrendJson(out,t+1,key,s.run,points,control::kTrendPoints)==t);
  out[t]='@';require(control::renderTrendJson(out,t,key,s.run,points,control::kTrendPoints)==0);require(out[t]=='@');
  require(control::renderTrendJson(out,sizeof(out),key,s.run,points,control::kTrendPoints+1)==0);
  require(control::renderTrendJson(out,sizeof(out),key,s.run,nullptr,1)==0);
  require(control::renderTrendJson(nullptr,0,key,s.run,points,0)==0);
  require(control::renderTrendJson(out,sizeof(out),key,s.run,nullptr,0)>0);
  for(unsigned i=0;i<1000;++i){
    require(control::renderStatusJson(out,sizeof(out)-1,key,s,owner,waiting,true)==n);
    require(control::renderTrendJson(out,sizeof(out)-1,key,s.run,points,control::kTrendPoints)==t);
  }
  require(!deniedAllocations);denyAllocations=false;
  printf("CHECKS=%u DENIED_ALLOCATION_ATTEMPTS=%u\n",checks,deniedAllocations);
}
