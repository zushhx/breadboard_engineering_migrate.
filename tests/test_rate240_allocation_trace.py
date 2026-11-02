"""Exercise the production bounded failed-allocation recorder on the host."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from rate_test_compiler import binary_path, compiler_command

ROOT = Path(__file__).resolve().parents[1]


class AllocationTraceTests(unittest.TestCase):
    def test_window_wrap_saturation_and_registration(self):
        source = (ROOT/'面包板样机_固件/diagnostics/sensor_web_recorder/src/rate190_stream.cpp').read_text(encoding='utf-8')
        actual = source[source.index('struct AllocationTrace {'):source.index('struct SendFailure {')]
        stub = r'''
#include <cassert>
#include <cstdint>
#include <cstddef>
#define DRAM_ATTR
#define IRAM_ATTR
using portMUX_TYPE=int;
#define portMUX_INITIALIZER_UNLOCKED 0
static int locked=0, registrations=0, prints=0, registrationResult=1;
static uint64_t tick=0;
static void portENTER_CRITICAL_SAFE(int*){assert(!locked);++locked;}
static void portEXIT_CRITICAL_SAFE(int*){assert(locked==1);--locked;}
static uint64_t esp_timer_get_time(){assert(locked);return ++tick;}
static constexpr int ESP_OK=0;
static int heap_caps_register_failed_alloc_callback(void(*)(size_t,uint32_t,const char*)){
  ++registrations;return registrationResult;
}
struct Printer {template<class... T> void printf(const char*,T...){assert(!locked);++prints;}} Serial;
'''
        cases = r'''
int main(){
 assert(!registerAllocationTrace());assert(!allocationHookRegistered&&registrations==1);
 registrationResult=0;assert(registerAllocationTrace());assert(registerAllocationTrace());assert(registrations==2);
 auto invalidName=reinterpret_cast<const char*>(uintptr_t(1));
 allocationFailed(123,7,invalidName);assert(allocationTrace.total==0&&tick==0);
 beginAllocationTrace();assert(allocationTrace.beginUs==1&&allocationTrace.active);
 for(unsigned i=0;i<11;++i)allocationFailed(100+i,7,invalidName);
 assert(allocationTrace.total==11&&allocationTrace.count==8&&allocationTrace.next==3);
 for(unsigned i=0;i<8;++i){auto& e=allocationTrace.events[(allocationTrace.next+i)&7];
   assert(e.bytes==103+i&&e.us==5+i&&e.caps==7&&e.functionAddress==1);}
 assert(prints==0);endAllocationTrace();assert(allocationTrace.endUs==13);
 allocationFailed(999,0,invalidName);assert(allocationTrace.total==11&&tick==13);
 reportAllocationTrace();assert(prints==9);
 beginAllocationTrace();assert(allocationTrace.total==0&&allocationTrace.count==0&&allocationTrace.next==0&&allocationTrace.endUs==0);
 allocationTrace.total=UINT32_MAX-1;
 allocationFailed(1,2,invalidName);allocationFailed(3,4,invalidName);
 assert(allocationTrace.total==UINT32_MAX&&allocationTrace.count==2);
 endAllocationTrace();reportAllocationTrace();assert(prints==12);
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            unit, binary = folder/'allocation.cpp', binary_path(folder, 'allocation')
            unit.write_text(stub+actual+cases, encoding='utf-8')
            built = subprocess.run(compiler_command(ROOT)+['-std=c++17', str(unit), '-o', str(binary)], capture_output=True, text=True, timeout=180)
            self.assertEqual(built.returncode, 0, built.stdout+built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(ran.returncode, 0, ran.stdout+ran.stderr)


if __name__ == '__main__':
    unittest.main()
