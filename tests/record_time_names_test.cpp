#include "record_name.h"
#include "session_model.h"
#include <cassert>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>
using recorder::NameSlot;
int main(int argc,char** argv) {
  assert(argc==2);char name[recorder::kRecordNameSize];
  for(auto stamp:{"26-10-18-15.46.00","00-02-29-23.59.59","24-02-29-00.00.00","99-12-31-23.59.59"})assert(recorder::localStamp(stamp));
  for(auto stamp:{"","26-10-18-15.46","26-02-29-15.46.00","26-04-31-15.46.00","26-00-18-15.46.00","26-10-00-15.46.00","26-10-18-24.46.00","26-10-18-15.60.00","26-10-18-15.46.60","26/10/18-15.46.00","../../bad","26-10-18-15.46.00 extra"})assert(!recorder::localStamp(stamp));
  assert(recorder::minuteLabel("26-10-18-15.46.30",0,name)&&!strcmp(name,"26-10-18-15.46"));
  assert(recorder::minuteLabel("26-10-18-15.46.59",2000000,name)&&!strcmp(name,"26-10-18-15.47"));
  assert(recorder::minuteLabel("26-12-31-23.59.59",2000000,name)&&!strcmp(name,"27-01-01-00.00"));
  assert(recorder::minuteLabel("24-02-28-23.59.59",1000000,name)&&!strcmp(name,"24-02-29-00.00"));
  assert(recorder::minuteLabel("26-02-28-23.59.59",1000000,name)&&!strcmp(name,"26-03-01-00.00"));
  assert(!recorder::minuteLabel("99-12-31-23.59.59",1000000,name));
  assert(!recorder::minuteLabel("26-10-18-15.46.00",86400000001ULL,name));
  for(auto label:{"26-10-18-15.46","26-10-18-15.46-02","26-10-18-15.46-99","26-10-18-15.46-100","26-10-18-15.46-9999"})assert(recorder::recordingName(label));
  for(auto label:{"26-10-18-15.46-01","26-10-18-15.46-2","26-10-18-15.46-002","26-10-18-15.46-10000","26-10-18-15.46.BIN","../26-10-18-15.46","26-02-29-15.46"})assert(!recorder::recordingName(label));
  std::filesystem::path root(argv[1]);std::filesystem::create_directories(root);
  auto inspect=[&](const char* candidate) {return std::filesystem::exists(root/candidate)?NameSlot::Occupied:NameSlot::Free;};
  for(auto expected:{"26-10-18-15.46","26-10-18-15.46-02","26-10-18-15.46-03"}) {
    assert(recorder::chooseRecordingName("26-10-18-15.46.00",0,inspect,name)&&!strcmp(name,expected));
    char directory[512],path[1024];assert(recorder::recordingPaths(argv[1],name,true,directory,path));
    assert(std::filesystem::path(path).filename()==std::string(expected)+".BIN");
    std::filesystem::create_directory(directory);std::ofstream(path)<<expected;
  }
  std::string first;std::ifstream(root/"26-10-18-15.46"/"26-10-18-15.46.BIN")>>first;assert(first=="26-10-18-15.46");
  unsigned calls=0;
  assert(!recorder::chooseRecordingName("26-10-18-15.46.00",0,[&](const char*){calls++;return NameSlot::Error;},name)&&!name[0]&&calls==1);
  calls=0;assert(!recorder::chooseRecordingName("26-10-18-15.46.00",0,[&](const char*){calls++;return NameSlot::Occupied;},name)&&calls==9999&&!name[0]);
  char directory[64],path[96];assert(recorder::recordingPaths("0:","REC_00000001",false,directory,path)&&!strcmp(path,"0:/REC_00000001/DATA.BIN"));
  char tiny[4],other[96];assert(!recorder::recordingPaths("0:","26-10-18-15.46",true,tiny,other));
  control::Model m;m.s.ready=true;m.s.serial=7;m.s.capacity=8000000000ULL;control::Sessions s;
  const char* a="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";const char* b="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
  assert(s.touch(a,1)&&s.touch(b,1));
  assert(!s.start(m,a,1,7,m.s.capacity,"00000001",30,"26-02-29-15.46.00"));
  assert(s.start(m,a,1,7,m.s.capacity,"00000001",30,"26-10-18-15.46.59"));
  char run[9];unsigned seconds;assert(m.take(run,seconds)&&m.begin(run,seconds));
  assert(!strcmp(m.s.startLocal,"26-10-18-15.46.59")&&m.s.namedAtUs==1);
  m.s.phase=control::Phase::Recording;
  assert(s.takeover(m,b,2000000,"00000001",7,m.s.capacity,"00000002",30,"26-10-18-15.47.59"));
  m.s.phase=control::Phase::Passed;s.finish(m,true,4000000);
  assert(m.s.phase==control::Phase::Queued&&!strcmp(m.s.startLocal,"26-10-18-15.47.59")&&m.s.namedAtUs==2000000);
  assert(recorder::minuteLabel(m.s.startLocal,4000000-m.s.namedAtUs,name)&&!strcmp(name,"26-10-18-15.48"));
  assert(m.take(run,seconds)&&m.begin(run,seconds)&&!strcmp(m.s.startLocal,"26-10-18-15.47.59"));
  assert(!s.pendingLocal[0]);
  puts("PASS: actual naming/path helpers: calendar/rollover, collision sequence, no overwrite, I/O error, bounds, legacy paths, queued and takeover local-time propagation");
}
