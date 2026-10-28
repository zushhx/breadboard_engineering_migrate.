"""Exercise the production bounded TF trace without hardware."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from rate_test_compiler import binary_path, compiler_command

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / '面包板样机_固件/diagnostics/sensor_web_recorder/src/rate240_tf.cpp'


class TfTraceTests(unittest.TestCase):
    def test_production_ring_threshold_and_chronology(self):
        code = SOURCE.read_text(encoding='utf-8')
        structure = code[code.index('struct SlowIoTrace {'):code.index('struct Workspace {')]
        harness = '''
#include <cstdint>
#include <cassert>
''' + structure + '''
int main(){
 SlowIoTrace t;
 t.add('W',1,20000,0,0); t.add('W',2,1,0,0);
 assert(t.count==0);
 t.add('S',1,20001,42,43);
 assert(t.count==1 && t.at(0).kind=='S' && t.at(0).offset==42);
 for(unsigned i=0;i<10;++i)t.add('W',100000+i,120000+i,i,i+3);
 assert(t.count==8 && t.overwritten==3);
 for(unsigned i=0;i<8;++i){const auto& e=t.at(i);
  assert(e.offset==i+2 && e.backlog==i+5 && e.end-e.start==20000);
 }
}
'''
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'trace.cpp'
            source.write_text(harness, encoding='utf-8')
            binary = binary_path(directory, 'trace')
            result = subprocess.run(compiler_command(ROOT)+['-std=c++17','-Wall','-Wextra','-Werror',str(source),'-o',str(binary)],capture_output=True,text=True,timeout=180)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            result = subprocess.run([str(binary)],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_output_after_write_gate_and_mirror_finish(self):
        code = SOURCE.read_text(encoding='utf-8')
        self.assertLess(code.index('store.gate(false)'),code.index('mirror.finish(ok,actual)'))
        self.assertLess(code.index('mirror.finish(ok,actual)'),code.index('Serial.printf("[rate240tf] IO_TRACE'))
        self.assertIn('SlowIoTrace trace;',code)
        self.assertNotIn('SlowIoTrace trace;',code[code.index('void command('):])


if __name__ == '__main__':
    unittest.main()
