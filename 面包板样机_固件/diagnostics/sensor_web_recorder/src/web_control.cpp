#include "web_control.h"
#include "wifi_credentials.h"
#include "web_asset.h"
#include "serial_compat.h"
#include "module_values.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_http_server.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "cJSON.h"
#include <cmath>
#include <cstdlib>
#include <inttypes.h>
#if defined(CONFIG_ESP_PHY_CALIBRATION_AND_DATA_STORAGE) || defined(CONFIG_ESP32_PHY_CALIBRATION_AND_DATA_STORAGE)
#error "Web recorder must not persist RF calibration to NVS"
#endif

namespace control {
static Model model;
static portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
struct Guard { Guard() { portENTER_CRITICAL(&mux); } ~Guard() { portEXIT_CRITICAL(&mux); } };
static httpd_handle_t server;
static char key[33];
static uint64_t lastPreview;
static const char* const pageStart = webPage;
static const char* const pageEnd = webPage + sizeof(webPage);
void initialize(bool ready, uint32_t serial, uint64_t capacity) {
  Guard g; model.s.ready = ready; model.s.serial = serial; model.s.capacity = capacity;
  if (!ready) { model.s.phase = Phase::Failed; snprintf(model.s.detail, sizeof(model.s.detail), "TF或采集池不可用；禁止录制"); }
}
bool take(char* run, unsigned& seconds) { Guard g; return model.take(run, seconds); }
bool begin(const char* run, unsigned seconds) { Guard g; lastPreview = 0; return model.begin(run, seconds); }
void phase(Phase value, const char* detail) { Guard g; model.s.phase = value; snprintf(model.s.detail, sizeof(model.s.detail), "%s", detail); }
bool stopping() { Guard g; return model.s.stop; }
void result(bool passed, bool locked, const char* detail, const char* sha) {
  Guard g; model.s.phase = passed ? Phase::Passed : (locked ? Phase::Failed : Phase::Rejected);
  snprintf(model.s.detail, sizeof(model.s.detail), "%s", detail); snprintf(model.s.sha, sizeof(model.s.sha), "%s", sha);
}
void progress(uint64_t written, uint64_t elapsed, uint32_t errors, uint32_t queue, uint32_t writeUs, uint32_t syncUs) {
  uint32_t freeHeap = heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  // A heap walk is deliberately omitted from the recording path.
  Guard g; auto& s = model.s; s.written = written; s.elapsedUs = elapsed; s.errors = errors; s.queue = queue;
  s.maxWriteUs = writeUs; s.maxSyncUs = syncUs; s.freeHeap = freeHeap;
}
void preview(const uint8_t* frames, unsigned count, uint64_t firstUs) {
  uint64_t stamp = esp_timer_get_time();
  if (lastPreview && stamp - lastPreview < 200000) return;
  lastPreview = stamp; count = count < kPreview ? count : kPreview;
  int16_t xyz[kPreview][3];
  for (unsigned i = 0; i < count; ++i)
    for (unsigned a = 0; a < 3; ++a) xyz[i][a] = modules::leSigned16(frames + i * 7 + 1 + a * 2);
  Guard g; memcpy(model.s.xyz, xyz, count * sizeof(xyz[0])); model.s.previewCount = count;
  model.s.previewUs = firstUs; ++model.s.previewSeq;
}
void ina(uint64_t timeUs, bool valid, int32_t busMv, int32_t currentUa, int32_t powerUw) {
  Guard g; model.s.inaUs = timeUs; model.s.inaValid = valid; model.s.busMv = busMv;
  model.s.currentUa = currentUa; model.s.powerUw = powerUw;
}
void finalMetrics(uint32_t fifoHigh, uint32_t queueHigh) { Guard g; model.s.fifoHigh = fifoHigh; model.s.queueHigh = queueHigh; }
static esp_err_t headers(httpd_req_t* r, const char* type) {
  httpd_resp_set_type(r, type);
  httpd_resp_set_hdr(r, "Cache-Control", "no-store");
  httpd_resp_set_hdr(r, "X-Content-Type-Options", "nosniff");
  httpd_resp_set_hdr(r, "Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; form-action 'none'");
  return ESP_OK;
}
static esp_err_t page(httpd_req_t* r) {
  headers(r, "text/html; charset=utf-8");
  const char* marker = strstr(pageStart, "{{KEY}}");
  if (!marker) return httpd_resp_send_err(r, HTTPD_500_INTERNAL_SERVER_ERROR, "asset invalid");
  if (httpd_resp_send_chunk(r, pageStart, marker - pageStart) != ESP_OK ||
      httpd_resp_send_chunk(r, key, strlen(key)) != ESP_OK ||
      httpd_resp_send_chunk(r, marker + 7, pageEnd - (marker + 7) - 1) != ESP_OK) return ESP_FAIL;
  return httpd_resp_send_chunk(r, nullptr, 0);
}
static esp_err_t status(httpd_req_t* r) {
  Snapshot s; { Guard g; s = model.s; }
  headers(r, "application/json");
  // Bound output to < 6 KiB; values inserted into strings are firmware-owned enums/hex/IP/messages.
  static char out[6144]; // HTTP handlers run on one server task, never on acquisition tasks.
  int n = snprintf(out, sizeof(out),
      "{\"boot\":\"%s\",\"phase\":\"%s\",\"ready\":%s,\"connected\":%s,\"ip\":\"%s\","
      "\"card_serial\":\"%08" PRIX32 "\",\"card_bytes\":\"%" PRIu64 "\",\"run\":\"%s\",\"seconds\":%u,"
      "\"written\":%" PRIu64 ",\"elapsed_us\":%" PRIu64 ",\"errors\":%u,\"queue\":%u,"
      "\"fifo_high\":%u,\"queue_high\":%u,\"write_us\":%u,\"sync_us\":%u,\"free_heap\":%u,"
      "\"ina_valid\":%s,\"ina_us\":%" PRIu64 ",\"bus_mv\":%" PRId32 ",\"current_ua\":%" PRId32 ",\"power_uw\":%" PRId32 ","
      "\"detail\":\"%s\",\"sha256\":\"%s\",\"preview_seq\":%u,\"preview_us\":%" PRIu64 ",\"preview\":[",
      key, phaseName(s.phase), s.ready ? "true" : "false", s.connected ? "true" : "false", s.ip,
      s.serial, s.capacity, s.run, (unsigned)s.seconds, s.written, s.elapsedUs, (unsigned)s.errors, (unsigned)s.queue,
      (unsigned)s.fifoHigh, (unsigned)s.queueHigh, (unsigned)s.maxWriteUs, (unsigned)s.maxSyncUs, (unsigned)s.freeHeap,
      s.inaValid ? "true" : "false", s.inaUs, s.busMv, s.currentUa, s.powerUw, s.detail, s.sha, (unsigned)s.previewSeq, s.previewUs);
  for (unsigned i = 0; i < s.previewCount && n > 0 && n < static_cast<int>(sizeof(out)); ++i)
    n += snprintf(out + n, sizeof(out) - n, "%s[%d,%d,%d]", i ? "," : "", s.xyz[i][0], s.xyz[i][1], s.xyz[i][2]);
  if (n <= 0 || n + 3 >= static_cast<int>(sizeof(out))) return httpd_resp_send_err(r, HTTPD_500_INTERNAL_SERVER_ERROR, "status bounds");
  memcpy(out + n, "]}", 3);
  return httpd_resp_send(r, out, n + 2);
}
static bool authorized(httpd_req_t* r) {
  char received[33] = {};
  return httpd_req_get_hdr_value_len(r, "X-Record-Key") == 32 &&
      httpd_req_get_hdr_value_str(r, "X-Record-Key", received, sizeof(received)) == ESP_OK && !strcmp(received, key);
}
static esp_err_t stop(httpd_req_t* r) {
  headers(r, "application/json");
  if (!authorized(r)) return httpd_resp_send_err(r, HTTPD_403_FORBIDDEN, "stale session or missing token; reload page");
  if (r->content_len != 0) return httpd_resp_send_err(r, HTTPD_400_BAD_REQUEST, "empty body required");
  { Guard g; model.requestStop(); }
  return httpd_resp_sendstr(r, "{\"accepted\":true}");
}
static esp_err_t start(httpd_req_t* r) {
  headers(r, "application/json");
  if (!authorized(r)) return httpd_resp_send_err(r, HTTPD_403_FORBIDDEN, "stale session or missing token; reload page");
  if (r->content_len < 1 || r->content_len > 160) return httpd_resp_send_err(r, HTTPD_400_BAD_REQUEST, "body length");
  char data[161]; unsigned used = 0;
  while (used < r->content_len) {
    int got = httpd_req_recv(r, data + used, r->content_len - used);
    if (got <= 0) return ESP_FAIL;
    used += got;
  }
  data[used] = 0;
  if (memchr(data, 0, used)) return httpd_resp_send_err(r, HTTPD_400_BAD_REQUEST, "embedded NUL");
  cJSON* json = cJSON_ParseWithOpts(data, nullptr, true);
  auto* run = cJSON_GetObjectItemCaseSensitive(json, "run");
  auto* seconds = cJSON_GetObjectItemCaseSensitive(json, "seconds");
  auto* serial = cJSON_GetObjectItemCaseSensitive(json, "card_serial");
  auto* capacity = cJSON_GetObjectItemCaseSensitive(json, "card_bytes");
  bool accepted = false;
  if (cJSON_IsObject(json) && cJSON_GetArraySize(json) == 4 && cJSON_IsString(run) && cJSON_IsNumber(seconds) &&
      std::isfinite(seconds->valuedouble) && seconds->valuedouble == seconds->valueint &&
      cJSON_IsString(serial) && cJSON_IsString(capacity)) {
    char line[100];
    int n = snprintf(line, sizeof(line), "REC_START %s %s %s %d", serial->valuestring, capacity->valuestring, run->valuestring, seconds->valueint);
    recorder::Request req = {};
    if (n > 0 && n < static_cast<int>(sizeof(line)) && recorder::parseRequest(line, req)) {
      Guard g; accepted = model.submit(req.serial, req.capacity, req.run, req.seconds);
    }
  }
  cJSON_Delete(json);
  if (!accepted) { httpd_resp_set_status(r, "409 Conflict"); return httpd_resp_sendstr(r, "{\"accepted\":false,\"reason\":\"busy, locked, invalid request or card identity\"}"); }
  httpd_resp_set_status(r, "202 Accepted");
  return httpd_resp_sendstr(r, "{\"accepted\":true}");
}
static void networkEvent(void*, esp_event_base_t base, int32_t id, void* data) {
  if (base == WIFI_EVENT && (id == WIFI_EVENT_STA_START || id == WIFI_EVENT_STA_DISCONNECTED)) {
    { Guard g; model.s.connected = false; model.s.ip[0] = 0; }
    // Retries are paced by the driver's scan/connect cycle, never a recording restart.
    esp_err_t error = esp_wifi_connect();
    if (error != ESP_OK) Serial.printf("[web] connect request=%s\n", esp_err_to_name(error));
  } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
    auto* event = static_cast<ip_event_got_ip_t*>(data);
    char ip[16]; snprintf(ip, sizeof(ip), IPSTR, IP2STR(&event->ip_info.ip));
    { Guard g; model.s.connected = true; snprintf(model.s.ip, sizeof(model.s.ip), "%s", ip); }
    Serial.printf("[web] address=http://%s/ hotspot_client_isolation=UNTESTED\n", ip);
  }
}
void startNetwork() {
  for (unsigned i = 0; i < 4; ++i) snprintf(key + i * 8, 9, "%08" PRIx32, esp_random());
  if (!WIFI_SSID[0]) { Serial.println("[web] credentials absent; network disabled (CI/software build)"); return; }
  esp_err_t error = esp_netif_init();
  if (error == ESP_OK) error = esp_event_loop_create_default();
  if (error != ESP_OK || !esp_netif_create_default_wifi_sta()) { Serial.println("[web] netif initialization failed; serial remains available"); return; }
  wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT(); init.nvs_enable = 0;
  error = esp_wifi_init(&init);
  if (error == ESP_OK) error = esp_wifi_set_storage(WIFI_STORAGE_RAM);
  if (error == ESP_OK) error = esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, networkEvent, nullptr);
  if (error == ESP_OK) error = esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, networkEvent, nullptr);
  wifi_config_t wifi = {};
  static_assert(sizeof(WIFI_SSID) - 1 <= sizeof(wifi.sta.ssid), "SSID too long");
  static_assert(sizeof(WIFI_PASSWORD) - 1 < sizeof(wifi.sta.password), "password too long");
  memcpy(wifi.sta.ssid, WIFI_SSID, sizeof(WIFI_SSID) - 1);
  memcpy(wifi.sta.password, WIFI_PASSWORD, sizeof(WIFI_PASSWORD) - 1);
  wifi.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
  if (error == ESP_OK) error = esp_wifi_set_mode(WIFI_MODE_STA);
  if (error == ESP_OK) error = esp_wifi_set_config(WIFI_IF_STA, &wifi);
  if (error == ESP_OK) error = esp_wifi_start();
  if (error != ESP_OK) { Serial.printf("[web] WiFi startup=%s; serial remains available\n", esp_err_to_name(error)); return; }
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.stack_size = 10240; config.task_priority = 2; config.core_id = 0;
  config.max_open_sockets = 3; config.lru_purge_enable = true;
  config.recv_wait_timeout = 1; config.send_wait_timeout = 1;
  error = httpd_start(&server, &config);
  if (error == ESP_OK) {
    httpd_uri_t root = {}; root.uri = "/"; root.method = HTTP_GET; root.handler = page;
    httpd_uri_t stats = {}; stats.uri = "/api/status"; stats.method = HTTP_GET; stats.handler = status;
    httpd_uri_t begin = {}; begin.uri = "/api/start"; begin.method = HTTP_POST; begin.handler = start;
    httpd_uri_t end = {}; end.uri = "/api/stop"; end.method = HTTP_POST; end.handler = stop;
    error = httpd_register_uri_handler(server, &root);
    if (error == ESP_OK) error = httpd_register_uri_handler(server, &stats);
    if (error == ESP_OK) error = httpd_register_uri_handler(server, &begin);
    if (error == ESP_OK) error = httpd_register_uri_handler(server, &end);
  }
  Serial.printf("[web] HTTP startup=%s STA_only=1 NVS_write=0\n", esp_err_to_name(error));
}
}
