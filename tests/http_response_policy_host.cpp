#include "http_response_policy.h"
#include <cassert>
#include <cstring>
#include <iostream>
#include <thread>
#include <vector>

static std::vector<int> events;
static int headerResult=ESP_OK,sendResult=ESP_OK,closeResult=ESP_OK;
esp_err_t httpd_resp_set_hdr(httpd_req_t*,const char* name,const char* value) {
  assert(!strcmp(name,"Connection")&&!strcmp(value,"close"));events.push_back(1);return headerResult;
}
esp_err_t httpd_resp_send(httpd_req_t*,const char*,ssize_t) {events.push_back(2);return sendResult;}
esp_err_t httpd_resp_send_err(httpd_req_t*,httpd_err_code_t,const char*) {events.push_back(3);return sendResult;}
esp_err_t httpd_resp_send_chunk(httpd_req_t*,const char* body,ssize_t length) {
  assert(!body&&!length);events.push_back(4);return sendResult;
}
int shutdown(int fd,int how) noexcept {
  assert(fd==7&&how==SHUT_RDWR);events.push_back(5);return closeResult;
}
int httpd_req_to_sockfd(httpd_req_t* r) {return r->fd;}
int main() {
  httpd_req_t request{reinterpret_cast<void*>(1),7};
  assert(boundedhttp::send(&request,"body",4)==ESP_OK);
  assert((events==std::vector<int>{1,2,5}));events.clear();
  sendResult=-9;
  assert(boundedhttp::send(&request,"body",4)==-9);
  assert((events==std::vector<int>{1,2}));events.clear();
  sendResult=ESP_OK;closeResult=-10;
  assert(boundedhttp::send(&request,"body",4)==ESP_FAIL);
  assert((events==std::vector<int>{1,2,5}));events.clear();
  closeResult=ESP_OK;headerResult=-11;
  assert(boundedhttp::send(&request,"body",4)==-11);
  assert((events==std::vector<int>{1}));events.clear();headerResult=ESP_OK;
  assert(boundedhttp::error(&request,HTTPD_400_BAD_REQUEST,"bad")==ESP_OK);
  assert((events==std::vector<int>{1,3,5}));events.clear();
  assert(boundedhttp::prepare(&request)==ESP_OK);
  // An arbitrary number of body chunks may be sent before the final empty one.
  assert(boundedhttp::endChunks(&request)==ESP_OK);
  assert((events==std::vector<int>{1,4,5}));events.clear();
  sendResult=-9;assert(boundedhttp::endChunks(&request)==-9);
  assert((events==std::vector<int>{4}));events.clear();sendResult=ESP_OK;
  boundedhttp::PendingWork pending;int queued=0;uint32_t first=0,second=0;
  assert(pending.schedule(100,[&](uint32_t token){++queued;first=token;return true;}));
  for(int i=0;i<10000;++i)assert(!pending.schedule(101,[&](uint32_t){++queued;return true;}));
  assert(queued==1);
  assert(!pending.schedule(12099,[](uint32_t){return true;}));
  // A lost UDP work item expires; an old callback must never execute or clear
  // its replacement, even if delivered after the lease has expired.
  assert(pending.schedule(12100,[&](uint32_t token){second=token;return true;}));
  assert(first!=second&&!pending.begin(first));pending.complete(first);
  assert(pending.begin(second));
  assert(!pending.schedule(50000,[](uint32_t){return true;})); // In flight never expires.
  assert(!pending.begin(second));pending.complete(second);
  assert(!pending.schedule(50001,[&](uint32_t){++queued;return false;}));
  assert(pending.schedule(50002,[&](uint32_t token){++queued;second=token;return true;}));assert(queued==3);
  // Competing duplicate callbacks for one token elect just one effective ping.
  std::atomic<int> winners{0};std::vector<std::thread> threads;
  for(int i=0;i<16;++i)threads.emplace_back([&]{if(pending.begin(second))++winners;});
  for(auto& thread:threads) {thread.join();}
  assert(winners==1);pending.complete(second);assert(pending.reserve(50003));
  boundedhttp::PendingWork wrapped;
  auto beforeWrap=wrapped.reserve(0xfffffff0u);assert(beforeWrap);
  assert(!wrapped.reserve(0x10u));assert(wrapped.reserve(12000u));
  std::cout<<"Production HTTP policy: send/shutdown ordering, failures, chunk termination, heartbeat leases/stale/duplicate/inflight/wraparound passed\n";
}
