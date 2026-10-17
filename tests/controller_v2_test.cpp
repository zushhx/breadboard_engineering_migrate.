#include "session_model.h"
#include "file_policy.h"
#include "usb_protocol.h"
#include <cassert>
#include <cstdio>
#include <string>
using control::Phase;
const char* A="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const char* B="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const char* C="cccccccccccccccccccccccccccccccc";
const char* D="dddddddddddddddddddddddddddddddd";
control::Model model(){control::Model m;m.s.ready=true;m.s.serial=7;m.s.capacity=8000000000ULL;return m;}
bool start(control::Sessions& s,control::Model& m,const char* id,const char* run="00000001",uint64_t now=1){return s.start(m,id,now,7,8000000000ULL,run,90);}
bool takeover(control::Sessions& s,control::Model& m,const char* id,uint64_t now=2,const char* expected="00000001"){return s.takeover(m,id,now,expected,7,8000000000ULL,"00000002",60);}
int main(){
  auto m=model();control::Sessions s;
  assert(!start(s,m,A));assert(!s.touch("bad",1));
  for(auto id:{A,B,C,D})assert(s.touch(id,1));
  assert(!s.touch("eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",1));
  assert(start(s,m,A));assert(!start(s,m,B));
  s.release(m,B);assert(!m.s.stop&&m.s.phase==Phase::Queued);
  assert(s.touch(B,2));assert(!takeover(s,m,B,2,"00000000"));
  assert(!takeover(s,m,A));m.s.phase=Phase::Recording;
  assert(takeover(s,m,B));assert(m.s.stop&&!strcmp(s.owner,A)&&!strcmp(s.pendingOwner,B));
  assert(!takeover(s,m,C));assert(!start(s,m,C));
  m.s.phase=Phase::Passed;s.finish(m,true,3);
  assert(m.s.phase==Phase::Queued&&!strcmp(s.owner,B)&&!strcmp(m.s.run,"00000002")&&m.s.seconds==60&&!m.s.stop);
  m.s.phase=Phase::Recording;s.release(m,A);assert(!m.s.stop);
  s.release(m,B);assert(m.s.stop);
  // Old record failure and pending-page loss must never start the new record.
  for(int failure=0;failure<2;failure++){
    m=model();s={};s.touch(A,1);s.touch(B,1);assert(start(s,m,A));m.s.phase=Phase::Recording;assert(takeover(s,m,B));
    if(failure==1)s.release(m,B);
    m.s.phase=failure==0?Phase::Failed:Phase::Passed;s.finish(m,failure!=0,3);
    assert(!s.owner[0]&&!s.pendingOwner[0]&&m.s.phase!=Phase::Queued);
  }
  m=model();s={};s.touch(A,1);assert(start(s,m,A));m.s.phase=Phase::Recording;
  s.tick(m,10000000);assert(!m.s.stop);s.tick(m,10000001);assert(m.s.stop);
  m=model();s={};s.touch(A,1);s.touch(B,1);assert(start(s,m,A));m.s.phase=Phase::Recording;assert(takeover(s,m,B));
  s.touch(A,10000000);s.tick(m,10000002);m.s.phase=Phase::Passed;s.finish(m,true,10000002);assert(!s.owner[0]);
  m=model();s={};s.touch(A,1);s.files=true;assert(!start(s,m,A));s.files=false;assert(start(s,m,A));
  // A queued start is cancelled before storage, so queued takeover may transfer immediately.
  s.touch(B,2);assert(takeover(s,m,B));assert(m.s.phase==Phase::Queued&&!strcmp(s.owner,B));
  for(auto phase:{Phase::Closing,Phase::Verifying}){m.s.phase=phase;assert(!takeover(s,m,C));}
  assert(devicev2::validPath("/",true));assert(!devicev2::validPath("/"));
  assert(devicev2::validPath("/测试/波形.bin"));
  for(auto path:{"","relative","//bad","/../x","/a/./b","/a\\b","/a:stream","/trailing.","/trailing ","/a//b","/a/"})assert(!devicev2::validPath(path));
  assert(devicev2::nestedCopy("/a","/a/b"));assert(devicev2::nestedCopy("/a","/a"));assert(!devicev2::nestedCopy("/a","/ab"));
  // Fixed standard CRC-32 vector ensures framing is not merely self-consistent.
  const uint8_t data[]={'1','2','3','4','5','6','7','8','9'};
  assert((~devicev2::crc32(data,9,0xffffffff))==0xcbf43926);
  uint8_t header[16];devicev2::frameHeader(header,1,123,data,9);assert(devicev2::validFrame(header,data));header[8]^=1;assert(!devicev2::validFrame(header,data));
  puts("PASS: four-client ownership, takeover races/failure/loss, 10s watchdog, storage exclusion, TF paths, CRC standard vector");
}
