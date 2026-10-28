"""Exercise actual production send/frame functions with injected transport results."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from rate_test_compiler import binary_path, compiler_command

ROOT=Path(__file__).resolve().parents[1]


class SendTraceTests(unittest.TestCase):
    def test_partial_zero_errno_and_deadline(self):
        source=(ROOT/'面包板样机_固件/diagnostics/sensor_web_recorder/src/rate190_stream.cpp').read_text(encoding='utf-8')
        actual=source[source.index('struct SendFailure {'):source.index('struct SessionMemory {')]
        stub=r'''
#include <cassert>
#include <cerrno>
#include <cstdint>
#include <cstddef>
#include <cstring>
#include <vector>
static std::vector<int> replies;
static unsigned calls=0, prints=0;
static uint64_t tick=0, step=10, sendDelay=0;
static uint64_t freeQueryUs=3,largestQueryUs=4;
static uint64_t now(){tick+=step;return tick;}
constexpr unsigned MALLOC_CAP_INTERNAL=1,MALLOC_CAP_8BIT=2;
static size_t heap_caps_get_free_size(unsigned caps){assert(caps==3);tick+=freeQueryUs;errno=EINVAL;return 12345;}
static size_t heap_caps_get_largest_free_block(unsigned caps){assert(caps==3);tick+=largestQueryUs;errno=EINVAL;return 6789;}
static uint64_t input=123,dropped=4;
static unsigned high=8;
struct Guard {};
struct Fifo {size_t size(){return 7;}} fifo;
struct Printer {template<class... T> void printf(const char*,T...){++prints;}} Serial;
static int send(int,const void*,size_t n,int){
 ++calls;tick+=sendDelay;
 int result=replies.empty()?int(n):replies.front();
 if(!replies.empty())replies.erase(replies.begin());
 if(result<0)errno=EAGAIN;
 return result;
}
'''
        cases=r'''
int main(){
 const char data[4]={};
 SendFailure ok;replies={2,2};
 assert(sendAll(0,data,4,ok,1,4,"payload",100));
 assert(!ok.captured&&prints==0);
 SendFailure negative;replies={2,-1};
 assert(!sendAll(0,data,4,negative,1,5,"payload",100));
 assert(negative.captured&&negative.error==EAGAIN&&negative.accepted==2);
 assert(negative.attemptRequested==2&&negative.sourceInput==123&&negative.sourceDropped==4);
 assert(negative.queued==7&&negative.queueHigh==8&&negative.payloadSent==100);
 assert(!strcmp(negative.reason,"negative")&&negative.attemptUs>0);
 errno=EINVAL;SendFailure zero;replies={0};
 assert(!sendAll(0,data,4,zero,1,6,"payload",0));
 assert(zero.error==0&&!strcmp(zero.reason,"zero"));
 SendFailure deadline;step=250001;unsigned before=calls;
 assert(!sendAll(0,data,4,deadline,1,7,"payload",0));
 assert(calls==before&&deadline.error==0&&deadline.attemptUs==0);
 assert(!strcmp(deadline.reason,"deadline"));step=10;
 SendFailure slow;sendDelay=300000;replies={2,2};unsigned slowBefore=calls;
 assert(!sendAll(0,data,4,slow,1,8,"payload",0));
 assert(calls==slowBefore+1&&slow.calls==1&&slow.lastSendResult==2);
 assert(slow.lastSendRequested==4&&slow.maxSendUs>=300000);
 assert(slow.accepted==2&&!strcmp(slow.reason,"deadline"));sendDelay=0;replies.clear();
 SendFailure gap;
 assert(sendAll(0,data,4,gap,1,8,"payload",0));
 tick+=800000;
 assert(sendAll(0,data,4,gap,1,8,"payload",0));
 assert(gap.maxGapUs>=800000&&gap.maxSendUs<1000&&gap.calls==2);
 SendFailure header;replies={-1};uint32_t seq=9;
 assert(!frame(0,1,seq,data,4,header,0));
 assert(header.sequence==9&&seq==10&&!strcmp(header.part,"header"));
 assert(prints==0);reportSendFailure(header);assert(prints==1);
 assert(header.traceCount==1&&header.trace[0].error==EAGAIN);
 assert(header.trace[0].freeBefore==12345&&header.trace[0].largestAfter==6789);
 assert(header.trace[0].sampleUs>0);
 SendFailure ring;sendDelay=20000;
 for(unsigned i=0;i<12;++i)assert(sendAll(0,data,4,ring,1,i,"payload",0));
 assert(ring.traceCount==8&&ring.traceTotal==12&&ring.traceNext==4);
 for(unsigned i=0;i<8;++i){
   const auto& s=ring.trace[(ring.traceNext+i)%8];
   assert(s.sequence==i+4&&s.endUs-s.beginUs>=20000);
 }
 assert(prints==1);reportSendSamples(ring);assert(prints==9);
 sendDelay=0;SendFailure fast;
 assert(sendAll(0,data,4,fast,1,0,"payload",0)&&fast.traceCount==0);
 freeQueryUs=700;largestQueryUs=1300;sendDelay=25000;
 SendFailure separated;replies={-1};
 assert(!sendAll(0,data,4,separated,1,0,"payload",0));
 assert(separated.lastSendUs==25000+step&&separated.maxSendUs==25000+step);
 assert(separated.attemptUs==25000+step&&separated.error==EAGAIN);
 assert(separated.trace[0].sampleUs==4000+2*step);
 assert(separated.totalUs>separated.attemptUs+separated.trace[0].sampleUs);
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);unit=folder/'send.cpp';binary=binary_path(folder,'send')
            unit.write_text(stub+actual+cases,encoding='utf-8')
            compiler=compiler_command(ROOT)
            built=subprocess.run(compiler+['-std=c++17',str(unit),'-o',str(binary)],capture_output=True,text=True,timeout=180)
            self.assertEqual(built.returncode,0,built.stdout+built.stderr)
            result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__=='__main__':unittest.main()
