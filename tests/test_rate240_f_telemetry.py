from pathlib import Path
import json
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class UnifiedTelemetry(unittest.TestCase):
    def test_actual_renderers_no_heap_and_all_ranges(self):
        with tempfile.TemporaryDirectory() as directory:
            binary=Path(directory)/'telemetry.exe'
            source=(ROOT/'tests/telemetry_json_host.cpp').read_text(encoding='utf-8')
            # Preserve the baseline host harness and extend it using the same production renderers.
            marker='  printf("CHECKS=%u DENIED_ALLOCATION_ATTEMPTS=%u\\n",checks,deniedAllocations);'
            self.assertEqual(source.count(marker),1)
            extra=r'''
  const unsigned ranges[]={2,8,16};
  for(unsigned rangeG:ranges){
    s.rangeG=rangeG;denyAllocations=true;
    require(control::renderStatusJson(out,sizeof(out),key,s,owner,waiting,true)>0);
    require(!deniedAllocations);denyAllocations=false;printf("RANGE%u=%s\n",rangeG,out);
    denyAllocations=true;
    require(control::renderTrendJson(out,sizeof(out),key,s.run,points,control::kTrendPoints,rangeG)>0);
    require(!deniedAllocations);denyAllocations=false;printf("TREND%u=%s\n",rangeG,out);
  }
'''
            unit=Path(directory)/'telemetry.cpp'
            unit.write_text(source.replace(marker,extra+marker),encoding='utf-8')
            zig=ROOT/'local-data/tooling/ziglang-0.13.0/ziglang/zig.exe'
            compiled=subprocess.run([str(zig),'c++','-std=c++17','-Wall','-Wextra','-Werror',
                '-I'+str(ROOT/'面包板样机_固件/diagnostics/sensor_web_recorder/src'),
                str(unit),'-o',str(binary)],capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(compiled.returncode,0,compiled.stdout+compiled.stderr)
            run=subprocess.run([str(binary)],capture_output=True,text=True,encoding='utf-8',timeout=30)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)
            lines=dict(line.split('=',1) for line in run.stdout.splitlines())
            self.assertIn('DENIED_ALLOCATION_ATTEMPTS=0',run.stdout)
            for g,scale in ((2,61),(8,244),(16,488)):
                for prefix in ('RANGE','TREND'):
                    data=json.loads(lines[prefix+str(g)])
                    self.assertEqual((data['range_g'],data['scale_micro_g']),(g,scale))


if __name__=='__main__':unittest.main()
