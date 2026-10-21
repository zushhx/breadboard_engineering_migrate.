#pragma once
#include <sys/types.h>
using esp_err_t=int;
constexpr esp_err_t ESP_OK=0,ESP_FAIL=-1;
using httpd_handle_t=void*;
enum httpd_err_code_t {HTTPD_400_BAD_REQUEST=400};
struct httpd_req_t {httpd_handle_t handle;int fd;};
esp_err_t httpd_resp_set_hdr(httpd_req_t*,const char*,const char*);
esp_err_t httpd_resp_send(httpd_req_t*,const char*,ssize_t);
esp_err_t httpd_resp_send_err(httpd_req_t*,httpd_err_code_t,const char*);
esp_err_t httpd_resp_send_chunk(httpd_req_t*,const char*,ssize_t);
esp_err_t httpd_sess_trigger_close(httpd_handle_t,int);
int httpd_req_to_sockfd(httpd_req_t*);
