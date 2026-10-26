"""Offline clock/send injection into the production SYNTH loop, not hardware evidence.

The test extracts the actual loop and its full-run rate/duration checks.
Only now(), vTaskDelay(), and successful frame transport are simulated. Catch-up
within the run deadline proves average throughput, not uniform packet spacing.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SynthScheduleTests(unittest.TestCase):
    def test_complete_source_and_successful_but_slow_send(self):
        source = (ROOT/'面包板样机_固件/diagnostics/sensor_web_recorder/src/rate190_stream.cpp').read_text(encoding='utf-8')
        start = source.index('  if(synth){') + len('  if(synth){')
        end = source.index('  }else{', start)
        actual_loop = source[start:end]
        stub = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
static uint64_t clockUs=0, injectedDelayUs=0;
static unsigned frames=0, delayedFrame=0;
static bool delayEveryFrame=false;
static uint64_t now(){return clockUs;}
static void vTaskDelay(unsigned ticks){clockUs+=uint64_t(ticks)*1000;}
struct SendFailure {};
static bool frame(int,uint32_t type,uint32_t& sequence,const void* bytes,uint32_t n,
                  SendFailure&,uint64_t payloadSent){
  assert(type==1&&n==2400);
  // Absolute targets still prohibit sending future packets ahead of schedule.
  assert(clockUs>=1000+(payloadSent/2400)*10000);
  assert(clockUs<=1000+30300000);
  assert(sequence==frames&&payloadSent==uint64_t(frames)*2400);
  const auto* data=static_cast<const uint8_t*>(bytes);
  for(unsigned i=0;i<n;++i)assert(data[i]==uint8_t((payloadSent+i)*31+17));
  ++sequence;++frames;
  if(delayEveryFrame||frames==delayedFrame)clockUs+=injectedDelayUs;
  return true; // The modeled transport succeeds, including the injected slow send.
}
struct Result {
  uint32_t errors; bool transport,sourceOk; uint64_t sent,input,elapsed; unsigned frames;
};
static Result simulate(unsigned delayAt,uint64_t delayUs,bool everyFrame=false){
  clockUs=1000;injectedDelayUs=delayUs;delayedFrame=delayAt;frames=0;
  delayEveryFrame=everyFrame;
  int fd=0;unsigned seconds=30;uint32_t sequence=0,errors=0;
  uint64_t input=0,sent=0,started=0,stopped=0,begin=now();
  bool transport=true,sourceOk=false;
  char sourceFormat[8]={};SendFailure failure;
  struct {uint32_t size;uint8_t data[3200];} item={};
'''
        cases = r'''
  return {errors,transport,sourceOk,sent,input,stopped-started,frames};
}
int main(){
  const auto normal=simulate(0,0);
  assert(normal.errors==0&&normal.transport&&normal.sourceOk);
  assert(normal.sent==7200000&&normal.input==7200000&&normal.frames==3000);
  assert(normal.elapsed==30000000);
  const auto slow=simulate(171,25001);
  assert(slow.transport&&slow.sourceOk&&slow.errors==0);
  assert(slow.sent==7200000&&slow.input==7200000&&slow.frames==3000);
  assert(slow.elapsed>=30000000&&slow.elapsed<=30001000); // Simulated 1 ms wait granularity.
  // A successful but very long send must not permit an unbounded catch-up.
  const auto longDelay=simulate(171,50000000);
  assert(longDelay.transport&&!longDelay.sourceOk&&longDelay.errors==8);
  assert(longDelay.sent==410400&&longDelay.elapsed>30300000);
  // Sustained transport capacity below the target still fails with incomplete data.
  const auto sustained=simulate(0,20000,true);
  assert(sustained.transport&&!sustained.sourceOk&&sustained.errors==8);
  assert(sustained.sent<7200000&&sustained.elapsed>30300000);
  // Even all bytes accepted are a failure when the final send exceeds the 1% deadline.
  const auto lateComplete=simulate(3000,310001);
  assert(lateComplete.sent==7200000&&lateComplete.transport&&!lateComplete.sourceOk);
  assert(lateComplete.errors==8&&lateComplete.elapsed==30300001);
  const auto boundary=simulate(3000,310000);
  assert(boundary.errors==0&&boundary.transport&&boundary.sourceOk);
  assert(boundary.sent==7200000&&boundary.elapsed==30300000);
  std::puts("SYNTH_SIMULATION normal=complete30s/7200000B short_delay=complete long_or_sustained_delay=failed late_complete=failed deadline_boundary=passed");
  return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            unit, binary = folder/'schedule.cpp', folder/'schedule.exe'
            unit.write_text(stub+actual_loop+cases, encoding='utf-8')
            compiler = ROOT/'local-data/tooling/ziglang-0.13.0/ziglang/zig.exe'
            built = subprocess.run([str(compiler),'c++','-std=c++17',str(unit),'-o',str(binary)],
                                   capture_output=True,text=True,timeout=180)
            self.assertEqual(built.returncode,0,built.stdout+built.stderr)
            result = subprocess.run([str(binary)],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('short_delay=complete long_or_sustained_delay=failed',result.stdout)


if __name__=='__main__':
    unittest.main()
