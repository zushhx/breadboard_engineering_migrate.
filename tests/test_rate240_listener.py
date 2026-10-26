"""Exercise production listener/start lifecycle with offline socket and task stubs."""
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ListenerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (ROOT/'面包板样机_固件/diagnostics/sensor_web_recorder/src/rate190_stream.cpp').read_text(encoding='utf-8')
        state_start = source.index('static bool serviceStarted')
        state_end = source.index('static unsigned high', state_start)
        actual = source[state_start:state_end] + source[source.index('struct ClientOptions {'):source.rindex('\n}')]
        stub = r'''
#include <algorithm>
#include <atomic>
#include <cassert>
#include <cerrno>
#include <condition_variable>
#include <cstdarg>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>
#include <vector>
#undef FD_ZERO
#undef FD_SET
#define FD_ZERO(p) ((p)->fd=0)
#define FD_SET(n,p) ((p)->fd=(n))
namespace fixture {
static std::recursive_mutex stateMutex;
static thread_local unsigned criticalDepth=0;
struct Guard {
  Guard(){stateMutex.lock();++criticalDepth;}
  ~Guard(){--criticalDepth;stateMutex.unlock();}
};
static void outsideCritical(){assert(criticalDepth==0);}
static std::mutex logMutex, selectMutex;
static std::condition_variable selectChanged;
static std::vector<std::string> events;
static std::string failureStage;
static std::atomic<uint64_t> clockUs{0};
static std::atomic<unsigned> sessions{0},deletes{0},creates{0};
static std::atomic<bool> reuseSet{false};
static bool allowSelectReturn=false,acceptClient=false,holdTask=false;
static void (*heldFunction)(void*)=nullptr;
static void* heldArgument=nullptr;
static std::thread task;
static char secret[33];
static uint64_t now(){return clockUs.load();}
static void event(const char* name){outsideCritical();std::lock_guard<std::mutex> lock(logMutex);events.emplace_back(name);}
static std::vector<std::string> observed(){std::lock_guard<std::mutex> lock(logMutex);return events;}
struct Printer {
  void printf(const char* format,...){
    outsideCritical();char text[256];va_list args;va_start(args,format);
    vsnprintf(text,sizeof(text),format,args);va_end(args);event(text);
  }
} Serial;
static constexpr int pdPASS=1;
static void vTaskDelay(unsigned ticks){
  outsideCritical();assert(ticks==1);clockUs+=1000;
  if(!holdTask)std::this_thread::sleep_for(std::chrono::microseconds(100));
}
static void vTaskDelete(void*){outsideCritical();++deletes;}
static int xTaskCreatePinnedToCore(void (*function)(void*),const char* name,unsigned stack,
                                 void* argument,unsigned priority,void*,unsigned core){
  outsideCritical();++creates;assert(!strcmp(name,"rate240")&&stack==8192&&priority==1&&core==0);
  if(failureStage=="task")return 0;
  if(holdTask){heldFunction=function;heldArgument=argument;return pdPASS;}
  assert(!task.joinable());task=std::thread(function,argument);return pdPASS;
}
static constexpr int AF_INET=2,SOCK_STREAM=1,IPPROTO_TCP=6,SOL_SOCKET=1;
static constexpr int SO_REUSEADDR=2,SO_RCVTIMEO=3,SO_SNDTIMEO=4,SHUT_RDWR=2;
static constexpr int TCP_NODELAY=1,MALLOC_CAP_INTERNAL=1,MALLOC_CAP_8BIT=2;
using socklen_t=unsigned;
static bool nodelaySet=false,nodelayVerified=false;
static unsigned heapQueries=0;
static size_t heap_caps_get_free_size(unsigned caps){assert(caps==3);++heapQueries;return 40000;}
static size_t heap_caps_get_minimum_free_size(unsigned caps){assert(caps==3);++heapQueries;return 30000;}
static size_t heap_caps_get_largest_free_block(unsigned caps){assert(caps==3);++heapQueries;return 20000;}
static constexpr uint32_t INADDR_ANY=0;
struct sockaddr {};
struct sockaddr_in {int sin_family;uint16_t sin_port;struct {uint32_t s_addr;} sin_addr;};
struct timeval {long tv_sec,tv_usec;};
struct fd_set {int fd;};
static uint16_t htons(uint16_t value){return value;}
static uint32_t htonl(uint32_t value){return value;}
static int socket(int family,int type,int protocol){
  event("socket");assert(family==AF_INET&&type==SOCK_STREAM&&protocol==IPPROTO_TCP);
  if(failureStage=="socket"){errno=EMFILE;return -1;}return 42;
}
static int setsockopt(int fd,int level,int option,const void* value,size_t size){
  outsideCritical();
  if(level==IPPROTO_TCP){
    event("nodelay-set");assert(fd==43&&option==TCP_NODELAY&&size==sizeof(int)&&*static_cast<const int*>(value)==1);
    if(failureStage=="nodelay_set"){errno=ENOPROTOOPT;return -1;}
    nodelaySet=true;return 0;
  }
  assert(level==SOL_SOCKET);
  if(option==SO_REUSEADDR){
    event("reuse");assert(fd==42&&size==sizeof(int)&&*static_cast<const int*>(value)==1);
    if(failureStage=="reuse"){errno=ENOPROTOOPT;return -1;}
    reuseSet=true;
  }else{
    assert(fd==43&&(option==SO_RCVTIMEO||option==SO_SNDTIMEO)&&size==sizeof(timeval));
    const auto* timeout=static_cast<const timeval*>(value);
    assert(timeout->tv_sec==0&&timeout->tv_usec==250000);event("client-timeout");
  }
  return 0;
}
static int getsockopt(int fd,int level,int option,void* value,socklen_t* size){
  event("nodelay-get");assert(fd==43&&level==IPPROTO_TCP&&option==TCP_NODELAY&&nodelaySet&&*size==sizeof(int));
  if(failureStage=="nodelay_get"){errno=EIO;return -1;}
  *static_cast<int*>(value)=failureStage=="nodelay_value"?0:1;
  *size=failureStage=="nodelay_length"?0:sizeof(int);
  nodelayVerified=failureStage.empty();return 0;
}
static int bind(int fd,const sockaddr* address,size_t size){
  event("bind");assert(fd==42&&reuseSet&&size==sizeof(sockaddr_in));
  const auto* actual=reinterpret_cast<const sockaddr_in*>(address);
  assert(actual->sin_port==19000&&actual->sin_family==AF_INET);
  if(failureStage=="bind"){errno=EADDRINUSE;return -1;}return 0;
}
static int listen(int fd,int backlog){
  event("listen");assert(fd==42&&backlog==1);
  if(failureStage=="listen"){errno=EIO;return -1;}return 0;
}
static int select(int count,fd_set* read,void*,void*,timeval* timeout){
  event("select");assert(count==43&&read->fd==42&&timeout->tv_sec==30&&timeout->tv_usec==0);
  std::unique_lock<std::mutex> lock(selectMutex);
  selectChanged.wait(lock,[]{return allowSelectReturn;});return acceptClient?1:0;
}
static int accept(int fd,void*,void*){event("accept");assert(fd==42);nodelaySet=nodelayVerified=false;return 43;}
static int shutdown(int fd,int how){event("shutdown");assert(fd==43&&how==SHUT_RDWR);return 0;}
static int close(int fd){event(fd==42?"close-server":"close-client");assert(fd==42||fd==43);return 0;}
struct SendFailure {};
static void session(int fd,SendFailure&){event("session");assert(fd==43&&nodelaySet&&nodelayVerified);++sessions;}
static void reportSendFailure(const SendFailure&){event("report");}
'''
        cases = r'''
static void finishTask(){
  {std::lock_guard<std::mutex> lock(selectMutex);allowSelectReturn=true;}
  selectChanged.notify_all();if(task.joinable())task.join();
}
static bool has(const char* text){const auto seen=observed();return std::any_of(seen.begin(),seen.end(),
  [text](const std::string& item){return item.find(text)!=std::string::npos;});}
static void clean(){
  assert(!serviceStarted&&!serviceReady&&!serviceCancelled&&!task.joinable());
}
static void successAndBusy(){
  acceptClient=true;assert(start("original"));
  assert(serviceStarted&&serviceReady&&!serviceCancelled);
  const unsigned generation=serviceGeneration,created=creates;
  assert(!start("replacement"));assert(creates==created);
  assert(serviceGeneration==generation&&serviceReady&&!strcmp(secret,"original"));
  // A stale completion/worker has no ownership of the active listener.
  serviceStopped(generation-1);worker(reinterpret_cast<void*>(static_cast<uintptr_t>(generation-1)));
  assert(serviceStarted&&serviceReady&&serviceGeneration==generation);
  const auto seen=observed();
  assert(std::find(seen.begin(),seen.end(),"reuse")<std::find(seen.begin(),seen.end(),"bind"));
  assert(std::find(seen.begin(),seen.end(),"bind")<std::find(seen.begin(),seen.end(),"listen"));
  finishTask();clean();assert(sessions==1);
  assert(heapQueries==6&&has("phase=session_start config=ready nodelay=1")&&has("heap_min_since_boot=30000"));
  {std::lock_guard<std::mutex> lock(selectMutex);allowSelectReturn=false;}
  assert(start("next"));assert(serviceGeneration==generation+1&&!strcmp(secret,"next"));
  finishTask();clean();assert(sessions==2);
  assert(heapQueries==12);
}
static void clientFailure(const std::string& stage){
  failureStage=stage;acceptClient=true;assert(start("client-failure"));
  finishTask();clean();assert(sessions==0&&heapQueries==3);
  assert(has("close-client")&&!has("phase=session_start")&&has("phase=closed"));
  const auto seen=observed();
  auto closed=std::find(seen.begin(),seen.end(),"close-client");
  auto reported=std::find_if(seen.begin(),seen.end(),[](const std::string& text){return text.find("phase=closed")!=std::string::npos;});
  assert(closed<reported);
  if(stage=="nodelay_set")assert(!has("nodelay-get")&&has("config=nodelay_set"));
  else if(stage=="nodelay_get")assert(has("config=nodelay_get"));
  else assert(has("config=nodelay_value"));
  failureStage.clear();reuseSet=false;
  {std::lock_guard<std::mutex> lock(selectMutex);allowSelectReturn=false;}
  assert(start("after-client-failure"));finishTask();clean();assert(sessions==1);
}
static void startupFailure(const std::string& stage){
  failureStage=stage;assert(!start("failed"));finishTask();clean();
  assert(sessions==0);
  if(stage!="task")assert(has(("LISTEN_FAIL stage="+stage).c_str()));
  if(stage=="reuse")assert(!has("bind")&&!has("listen"));
  if(stage=="bind")assert(!has("listen"));
  failureStage.clear();reuseSet=false;acceptClient=false;
  {std::lock_guard<std::mutex> lock(selectMutex);allowSelectReturn=false;}
  assert(start("retry"));finishTask();clean();assert(sessions==0);
}
static void lateWorker(){
  holdTask=true;assert(!start("late"));
  assert(clockUs>=1000000&&clockUs<=1001000&&serviceStarted&&serviceCancelled&&!serviceReady);
  const unsigned generation=serviceGeneration,created=creates;
  assert(!start("busy"));assert(creates==created&&serviceGeneration==generation&&!strcmp(secret,"late"));
  assert(heldFunction);holdTask=false;heldFunction(heldArgument);clean();
  assert(!has("socket")&&!has("session"));
  assert(start("after-timeout"));finishTask();clean();
}
} // namespace fixture
int main(int argc,char** argv){
  assert(argc==2);const std::string scenario=argv[1];
  if(scenario=="success")fixture::successAndBusy();
  else if(scenario=="late")fixture::lateWorker();
  else if(scenario.find("nodelay_")==0)fixture::clientFailure(scenario);
  else fixture::startupFailure(scenario);
  std::puts(("LISTENER_SIMULATION passed="+scenario).c_str());
}
'''
        cls.temporary = tempfile.TemporaryDirectory()
        folder = Path(cls.temporary.name)
        unit = folder/'listener.cpp'
        cls.binary = folder/'listener.exe'
        unit.write_text(stub+actual+cases, encoding='utf-8')
        compiler = ROOT/'local-data/tooling/ziglang-0.13.0/ziglang/zig.exe'
        built = subprocess.run([str(compiler), 'c++', '-std=c++17', str(unit), '-o', str(cls.binary)],
                               capture_output=True, text=True, timeout=180)
        if built.returncode:
            cls.temporary.cleanup()
            raise AssertionError(built.stdout+built.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_scenario(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)

    def test_listener_ready_busy_cleanup_and_restart(self):
        self.run_scenario('success')

    def test_socket_failure_returns_zero(self):
        self.run_scenario('socket')

    def test_reuse_failure_prevents_bind(self):
        self.run_scenario('reuse')

    def test_bind_failure_returns_zero(self):
        self.run_scenario('bind')

    def test_listen_failure_returns_zero(self):
        self.run_scenario('listen')

    def test_task_creation_failure_returns_zero(self):
        self.run_scenario('task')

    def test_startup_timeout_cancels_late_worker(self):
        self.run_scenario('late')

    def test_nodelay_set_failure_closes_without_session(self):
        self.run_scenario('nodelay_set')

    def test_nodelay_readback_failure_closes_without_session(self):
        self.run_scenario('nodelay_get')

    def test_nodelay_readback_zero_closes_without_session(self):
        self.run_scenario('nodelay_value')

    def test_nodelay_readback_wrong_length_closes_without_session(self):
        self.run_scenario('nodelay_length')


if __name__ == '__main__':
    unittest.main()
