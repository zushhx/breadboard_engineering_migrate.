#pragma once
#include "esp_http_server.h"
#include <atomic>
#include <cstdint>
#include <sys/socket.h>

namespace boundedhttp {
// Ordinary responses release their socket after the complete body. WebSocket
// upgrades and frames must not use these helpers.
inline esp_err_t prepare(httpd_req_t* request) {
  return httpd_resp_set_hdr(request,"Connection","close");
}
inline esp_err_t finish(httpd_req_t* request,esp_err_t sent) {
  if(sent!=ESP_OK) return sent; // Handler failure asks the SDK to close directly.
  // For a fully consumed valid request, shutdown preserves queued TCP data/FIN.
  // Unread pipelined/body data can instead cause lwIP to reset the connection.
  // The SDK owns close() and observes EOF on its next receive; no UDP control
  // work is queued per reply.
  return ::shutdown(httpd_req_to_sockfd(request),SHUT_RDWR)==0?ESP_OK:ESP_FAIL;
}
inline esp_err_t send(httpd_req_t* request,const char* body,ssize_t length) {
  esp_err_t ready=prepare(request);if(ready!=ESP_OK)return ready;
  return finish(request,httpd_resp_send(request,body,length));
}
inline esp_err_t error(httpd_req_t* request,httpd_err_code_t code,const char* message) {
  esp_err_t ready=prepare(request);if(ready!=ESP_OK)return ready;
  return finish(request,httpd_resp_send_err(request,code,message));
}
inline esp_err_t endChunks(httpd_req_t* request) {
  return finish(request,httpd_resp_send_chunk(request,nullptr,0));
}

// queue_work uses best-effort UDP: successful enqueue can still be dropped by
// lwIP. Expire a queued token after 12s, but never replace a running callback.
// Stale queued callbacks become no-ops, leaving at most one effective ping.
class PendingWork {
  static constexpr uint32_t running=0x80000000u;
  std::atomic<uint32_t> state{0};
  uint32_t next=0,queuedAt=0; // Only the heartbeat producer accesses these.
public:
  uint32_t reserve(uint32_t now) {
    uint32_t previous=state.load();
    if(previous&&(previous&running || uint32_t(now-queuedAt)<12000))return 0;
    next=(next+1)&~running;if(!next)++next;
    if(!state.compare_exchange_strong(previous,next))return 0;
    queuedAt=now;return next;
  }
  bool begin(uint32_t token) {
    return token&&state.compare_exchange_strong(token,token|running);
  }
  void complete(uint32_t token) {
    uint32_t expected=token|running;state.compare_exchange_strong(expected,0);
  }
  template<class Queue> bool schedule(uint32_t now,Queue queue) {
    uint32_t token=reserve(now);if(!token)return false;
    if(queue(token))return true;
    state.compare_exchange_strong(token,0);return false;
  }
};
}
