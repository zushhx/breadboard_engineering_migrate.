#include "netui.h"
#include "webui_asset.h"
#include <SD_MMC.h>
#include <string.h>

// ============================================================
//  NetUI 实现
//
//  这里包含三个"自己实现"的部件，都不依赖任何外部库：
//    ① SHA-1（WebSocket 握手要用）      —— 见下方 sha1* 系列
//    ② base64 编码（握手用的 Sec-WebSocket-Accept）
//    ③ WebSocket 帧的封装与解析
//
//  为什么不引库：本项目只需要 RFC6455 的最小子集（握手 + 收发小帧），
//  自己写可以完全掌控行为，也省掉一次构建期依赖下载。
// ============================================================

// ================= ① SHA-1（RFC 3174） =================
namespace {

inline uint32_t rol32(uint32_t v, int n) { return (v << n) | (v >> (32 - n)); }

struct Sha1State {
  uint32_t h[5];
  uint64_t total;
  uint8_t  buf[64];
  size_t   buflen;
};

void sha1Block(Sha1State& s, const uint8_t* p) {
  uint32_t w[80];
  for (int i = 0; i < 16; i++) {
    w[i] = ((uint32_t)p[i * 4] << 24) | ((uint32_t)p[i * 4 + 1] << 16) |
           ((uint32_t)p[i * 4 + 2] << 8) | (uint32_t)p[i * 4 + 3];
  }
  for (int i = 16; i < 80; i++) w[i] = rol32(w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16], 1);

  uint32_t a = s.h[0], b = s.h[1], c = s.h[2], d = s.h[3], e = s.h[4];
  for (int i = 0; i < 80; i++) {
    uint32_t f, k;
    if      (i < 20) { f = (b & c) | ((~b) & d);          k = 0x5A827999u; }
    else if (i < 40) { f = b ^ c ^ d;                     k = 0x6ED9EBA1u; }
    else if (i < 60) { f = (b & c) | (b & d) | (c & d);   k = 0x8F1BBCDCu; }
    else             { f = b ^ c ^ d;                     k = 0xCA62C1D6u; }
    const uint32_t t = rol32(a, 5) + f + e + k + w[i];
    e = d; d = c; c = rol32(b, 30); b = a; a = t;
  }
  s.h[0] += a; s.h[1] += b; s.h[2] += c; s.h[3] += d; s.h[4] += e;
}

void sha1Init(Sha1State& s) {
  s.h[0] = 0x67452301u; s.h[1] = 0xEFCDAB89u; s.h[2] = 0x98BADCFEu;
  s.h[3] = 0x10325476u; s.h[4] = 0xC3D2E1F0u;
  s.total = 0; s.buflen = 0;
}

void sha1Update(Sha1State& s, const uint8_t* p, size_t n) {
  s.total += n;
  while (n) {
    size_t take = 64 - s.buflen;
    if (take > n) take = n;
    memcpy(s.buf + s.buflen, p, take);
    s.buflen += take; p += take; n -= take;
    if (s.buflen == 64) { sha1Block(s, s.buf); s.buflen = 0; }
  }
}

void sha1Final(Sha1State& s, uint8_t out[20]) {
  const uint64_t bits = s.total * 8;      // 先记下来，下面补位会改 total
  const uint8_t pad = 0x80, zero = 0x00;
  sha1Update(s, &pad, 1);
  while (s.buflen != 56) sha1Update(s, &zero, 1);
  uint8_t lenb[8];
  for (int i = 0; i < 8; i++) lenb[i] = (uint8_t)(bits >> (56 - i * 8));
  sha1Update(s, lenb, 8);
  for (int i = 0; i < 5; i++) {
    out[i * 4]     = (uint8_t)(s.h[i] >> 24);
    out[i * 4 + 1] = (uint8_t)(s.h[i] >> 16);
    out[i * 4 + 2] = (uint8_t)(s.h[i] >> 8);
    out[i * 4 + 3] = (uint8_t)(s.h[i]);
  }
}

// ================= ② base64 =================
const char kB64[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

void b64encode(const uint8_t* in, size_t n, char* out) {
  size_t o = 0;
  for (size_t i = 0; i < n; i += 3) {
    uint32_t v = (uint32_t)in[i] << 16;
    if (i + 1 < n) v |= (uint32_t)in[i + 1] << 8;
    if (i + 2 < n) v |= (uint32_t)in[i + 2];
    out[o++] = kB64[(v >> 18) & 63];
    out[o++] = kB64[(v >> 12) & 63];
    out[o++] = (i + 1 < n) ? kB64[(v >> 6) & 63] : '=';
    out[o++] = (i + 2 < n) ? kB64[v & 63]        : '=';
  }
  out[o] = 0;
}

// ================= 小工具 =================
String toLower(String s) { s.toLowerCase(); return s; }

bool containsCI(const String& hay, const char* needle) {
  return toLower(hay).indexOf(toLower(String(needle))) >= 0;
}

// 从请求头里抽 Sec-WebSocket-Key 的值
String headerValue(const String& req, const char* name) {
  String lower = toLower(req);
  String key   = toLower(String(name)) + ":";
  int p = lower.indexOf(key);
  if (p < 0) return String();
  p += key.length();
  int e = req.indexOf("\r\n", p);
  if (e < 0) e = req.length();
  String v = req.substring(p, e);
  v.trim();
  return v;
}

}  // namespace

// ============================================================
bool NetUI::begin(DispPipeline* disp) {
  disp_ = disp;

  WiFi.persistent(false);
  WiFi.mode(WIFI_AP);
  WiFi.setSleep(false);

  // SSID 带本机 MAC 后两字节，多台设备同处一室时不会重名
  uint8_t mac[6] = {0};
  WiFi.macAddress(mac);
  snprintf(ssid_, sizeof(ssid_), "%s%02X%02X", NET_SSID_PREFIX, mac[4], mac[5]);

  if (!WiFi.softAP(ssid_, NET_PASS, NET_CHANNEL, 0, NET_MAX_CLIENTS)) {
    up_ = false;
    return false;
  }
  // ⚠ 必须在 softAP 之后调用（之前射频还没起来，设置会被忽略）
  WiFi.setTxPower(NET_TX_POWER);

  ip_ = WiFi.softAPIP();
  srv_.begin();
  srv_.setNoDelay(true);

  up_      = true;
  enabled_ = true;
  lastStatusMs_ = millis();
  return true;
}

void NetUI::stop() {
  for (int i = 0; i < NET_MAX_CLIENTS; i++) {
    if (cl_[i].state) { cl_[i].c.stop(); cl_[i].state = 0; cl_[i].rxLen = 0; cl_[i].req = ""; }
  }
  wsCount_ = 0;
  srv_.stop();
  WiFi.softAPdisconnect(true);
  WiFi.mode(WIFI_OFF);
  up_ = false;
}

void NetUI::setEnabled(bool on) {
  if (on == enabled_) return;
  enabled_ = on;
  if (!on) {
    for (int i = 0; i < NET_MAX_CLIENTS; i++) {
      if (cl_[i].state) sendWsClose(i);
    }
    // 关电台：省电、去干扰（保真采集时用）
    WiFi.softAPdisconnect(false);
    WiFi.mode(WIFI_OFF);
    up_ = false;
  } else {
    // 重新开电台
    WiFi.mode(WIFI_AP);
    WiFi.setSleep(false);
    if (WiFi.softAP(ssid_, NET_PASS, NET_CHANNEL, 0, NET_MAX_CLIENTS)) {
      WiFi.setTxPower(NET_TX_POWER);
      ip_ = WiFi.softAPIP();
      srv_.begin();
      srv_.setNoDelay(true);
      up_ = true;
    }
  }
}

// ============================================================
//  主循环（core 0）
// ============================================================
void NetUI::loop() {
  if (!enabled_ || !up_) return;

  // ---- 1. 接入新连接（不阻塞：只入槽，头部在下面慢慢读） ----
  WiFiClient nc = srv_.accept();
  if (nc) {
    nc.setNoDelay(true);          // ★ 关键：关 Nagle，否则小帧会被攒起来延迟发
    int slot = -1;
    for (int i = 0; i < NET_MAX_CLIENTS; i++) {
      if (cl_[i].state == 0) { slot = i; break; }
    }
    if (slot < 0) {
      nc.stop();
    } else {
      cl_[slot].c          = nc;
      cl_[slot].state      = 1;
      cl_[slot].deadlineMs = millis() + 2000;
      cl_[slot].rxLen      = 0;
      cl_[slot].req        = "";
      cl_[slot].req.reserve(1024);
    }
  }

  // ---- 2. 处理各连接 ----
  uint8_t wsAlive = 0;
  for (int i = 0; i < NET_MAX_CLIENTS; i++) {
    Client& c = cl_[i];
    if (c.state == 0) continue;

    if (!c.c.connected()) {
      c.state = 0; c.rxLen = 0; c.req = "";
      continue;
    }

    if (c.state == 1) {
      // 读 HTTP 请求头
      while (c.c.available() > 0) {
        int ch = c.c.read();
        if (ch < 0) break;
        c.req += (char)ch;
        if (c.req.length() >= 4 && c.req.endsWith("\r\n\r\n")) break;
        if (c.req.length() > 2048) break;
      }
      // 头部结束标记可能在本次读入的中间（后面还跟着别的字节），
      // 所以用 indexOf 定位而不是 endsWith —— 否则会一直读不到头。
      const int hend = c.req.indexOf("\r\n\r\n");
      if (hend >= 0 || c.req.length() > 2048) {
        const String head = (hend >= 0) ? c.req.substring(0, hend + 4) : c.req;
        const String rest = (hend >= 0) ? c.req.substring(hend + 4) : String();

        if (containsCI(head, "upgrade: websocket")) {
          if (!doHandshake(i, head)) { c.c.stop(); c.state = 0; c.req = ""; }
          else {
            c.state = 2; c.rxLen = 0; c.req = "";
            // 万一握手包里已经跟了帧数据，先搬进收帧缓冲，不能丢
            const size_t n = rest.length();
            if (n) {
              const size_t take = (n < sizeof(c.rx)) ? n : sizeof(c.rx);
              memcpy(c.rx, rest.c_str(), take);
              c.rxLen = take;
            }
            wsAlive++;
          }
        } else {
          // 普通 HTTP：第一行是 "GET <target> HTTP/1.1"
          String target = "/", query = "";
          int sp1 = head.indexOf(' ');
          int sp2 = (sp1 >= 0) ? head.indexOf(' ', sp1 + 1) : -1;
          if (sp1 >= 0 && sp2 > sp1) target = head.substring(sp1 + 1, sp2);
          int qm = target.indexOf('?');
          if (qm >= 0) { query = target.substring(qm + 1); target = target.substring(0, qm); }

          if (target == "/status")       serveStatus(i);
          else if (target == "/dl")      serveDownload(i, query);
          else if (target == "/ws")      writeHttp(i, 400, "text/plain", "expect websocket upgrade");
          else                           serveAsset(i, target.c_str());
          c.c.stop();
          c.state = 0; c.req = "";
        }
      } else if ((int32_t)(millis() - c.deadlineMs) >= 0) {
        // 浏览器常开"预连接"却迟迟不发请求 → 到点清掉，把槽位让出来
        c.c.stop(); c.state = 0; c.req = "";
      }
      continue;
    }

    // state == 2：WebSocket
    parseFrames(i);
    if (cl_[i].state == 2) wsAlive++;
  }
  wsCount_ = wsAlive;

  // ---- 3. 波形帧：优先发送，保证实时性 ----
  DispFrame f;
  if (wsCount_ == 0) {
    // 没有客户端：把队列抽干，避免下一个连上来的客户端被灌一堆过期帧
    while (disp_->pop(f)) { framesQueued_++; }
  } else {
    int budget = 6;                       // 单次最多补发 6 帧，避免长时间占住 loop
    while (budget-- > 0 && disp_->pop(f)) {
      framesQueued_++;
      // ---- 二进制帧序列化（小端）----
      txBuf_[0] = 0xAA;
      txBuf_[1] = 0x01;
      txBuf_[2] = (uint8_t)(f.seq & 0xFF);
      txBuf_[3] = (uint8_t)(f.seq >> 8);
      txBuf_[4] = (uint8_t)(f.tMs);
      txBuf_[5] = (uint8_t)(f.tMs >> 8);
      txBuf_[6] = (uint8_t)(f.tMs >> 16);
      txBuf_[7] = (uint8_t)(f.tMs >> 24);
      const uint16_t rate = (uint16_t)(SAMPLE_RATE_HZ / DISP_DECIM);
      txBuf_[8]  = (uint8_t)(rate & 0xFF);
      txBuf_[9]  = (uint8_t)(rate >> 8);
      txBuf_[10] = DISP_DECIM;
      txBuf_[11] = f.nAxes;
      txBuf_[12] = (uint8_t)(f.nPts & 0xFF);
      txBuf_[13] = (uint8_t)(f.nPts >> 8);

      size_t o = 14;
      // 顺序：逐点 → 逐轴 → (min, max)。UI 端按同样顺序解。
      for (uint16_t p = 0; p < f.nPts; p++) {
        for (uint8_t a = 0; a < f.nAxes; a++) {
          const int16_t mn = f.mn[a][p], mx = f.mx[a][p];
          txBuf_[o++] = (uint8_t)(mn & 0xFF); txBuf_[o++] = (uint8_t)(mn >> 8);
          txBuf_[o++] = (uint8_t)(mx & 0xFF); txBuf_[o++] = (uint8_t)(mx >> 8);
        }
      }

      for (int i = 0; i < NET_MAX_CLIENTS; i++) {
        if (cl_[i].state == 2) sendWsFrame(i, 0x02 /*binary*/, txBuf_, o);
      }
      framesSent_++;
    }
  }

  // ---- 4. 状态帧（2 Hz）----
  if (statusFn_ && (millis() - lastStatusMs_ >= STATUS_PERIOD_MS)) {
    lastStatusMs_ = millis();
    if (wsCount_ > 0) {
      String j;
      j.reserve(640);
      statusFn_(j);
      broadcastText(j.c_str());
    }
  }
}

// ============================================================
//  HTTP
// ============================================================
void NetUI::writeHttp(int idx, int code, const char* ctype, const String& body) {
  Client& c = cl_[idx];
  String h;
  h.reserve(256);
  h += "HTTP/1.1 ";
  h += code;
  h += (code == 200) ? " OK" : ((code == 404) ? " Not Found" : " Bad Request");
  h += "\r\nContent-Type: ";
  h += ctype;
  h += "\r\nContent-Length: ";
  h += (int)body.length();
  h += "\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n";
  c.c.write((const uint8_t*)h.c_str(), h.length());
  if (body.length()) c.c.write((const uint8_t*)body.c_str(), body.length());
  c.c.flush();
  txBytes_ += h.length() + body.length();
}

void NetUI::serveAsset(int idx, const char* path) {
  // ① 先看 SD 卡上有没有 /ui.html —— 有就优先用，改界面不用重编译
  String fromSd;
  if (assetFn_ && assetFn_(path, fromSd) && fromSd.length() > 0) {
    writeHttp(idx, 200, "text/html; charset=utf-8", fromSd);
    return;
  }
  // ② 回落到固化在 Flash 里的版本
  const bool isRoot = (strcmp(path, "/") == 0 || strcmp(path, "/index.html") == 0 ||
                       strcmp(path, "/ui.html") == 0);
  if (isRoot) {
    String body;
    body.reserve(WEBUI_HTML_LEN + 1);
    body = WEBUI_HTML;                       // const char[]，一次拷贝进 String
    writeHttp(idx, 200, "text/html; charset=utf-8", body);
  } else if (strcmp(path, "/favicon.ico") == 0) {
    writeHttp(idx, 404, "text/plain", "");
  } else {
    writeHttp(idx, 404, "text/plain", "not found");
  }
}

void NetUI::serveStatus(int idx) {
  String j;
  j.reserve(640);
  if (statusFn_) statusFn_(j);
  writeHttp(idx, 200, "application/json", j);
}

// ------------------------------------------------------------
//  /dl?f=vib_0001.bin —— 把 TF 卡里的原始数据直接流给浏览器下载
//
//  为什么需要它：一旦开启「录制时关 WiFi」（保真优先），录制期间 UI 是断的；
//  录完 WiFi 恢复，用户得能立刻把 .bin 取下来分析，而不是去拔 SD 卡。
//  这样「实时看波形」和「保真录原始数据」两个工况就都闭环了。
//
//  ★ 三条安全约束，缺一个都会出问题：
//    ① 采集中一律拒绝（409）：下载要读 SD，写卡任务在另一个核上同时写 SD，
//       两个任务并发碰同一套 SDMMC 驱动会出问题 → 直接让用户先停止采集。
//    ② 文件名白名单：只放行本固件自己写出来的 vib_*.bin，拒绝任何路径字符，
//       否则这就是个"整张卡暴露成公网文件服务"的洞。
//    ③ 循环里带 2 秒无进展保护 + vTaskDelay(1)：万一对端掉线，
//       不能把 netTask 永久卡死。
// ------------------------------------------------------------
void NetUI::serveDownload(int idx, const String& query) {
  Client& c = cl_[idx];

  if (busyFn_ && busyFn_()) {
    writeHttp(idx, 409, "text/plain; charset=utf-8",
              "设备正在采集，请先停止再下载（避免与写卡争用 SD 总线）");
    c.c.stop();
    return;
  }

  // ---- 解析 f= 参数 ----
  int p = query.indexOf("f=");
  if (p < 0) {
    writeHttp(idx, 400, "text/plain; charset=utf-8", "缺少 f= 参数");
    c.c.stop();
    return;
  }
  p += 2;
  int e = query.indexOf('&', p);
  String name = (e < 0) ? query.substring(p) : query.substring(p, e);

  // ---- 白名单校验（②） ----
  bool okName = (name.length() >= 9 && name.length() <= 40) &&
                name.startsWith("vib_") && name.endsWith(".bin");
  if (okName) {
    for (unsigned i = 0; i < name.length(); i++) {
      const char ch = name[i];
      const bool safe = (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') ||
                        (ch >= '0' && ch <= '9') || ch == '.' || ch == '_' || ch == '-';
      if (!safe) { okName = false; break; }
    }
  }
  if (!okName) {
    writeHttp(idx, 400, "text/plain; charset=utf-8", "文件名不合法（只允许 vib_*.bin）");
    c.c.stop();
    return;
  }

  String full = "/";
  full += name;
  File f = SD_MMC.open(full.c_str(), FILE_READ);
  if (!f) {
    writeHttp(idx, 404, "text/plain; charset=utf-8", "卡上没有这个文件");
    c.c.stop();
    return;
  }
  const size_t sz = f.size();

  // ---- 响应头：Content-Length 已知，浏览器能显示进度条 ----
  String h;
  h.reserve(224);
  h += "HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\n";
  h += "Content-Disposition: attachment; filename=\"";
  h += name;
  h += "\"\r\nContent-Length: ";
  h += (unsigned long)sz;
  h += "\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n";
  c.c.write((const uint8_t*)h.c_str(), h.length());
  txBytes_ += h.length();

  // ---- 流式发送（③） ----
  static uint8_t chunk[4096];
  size_t sent = 0;
  uint32_t lastProgress = millis();
  while (f.available()) {
    const size_t n = f.read(chunk, sizeof(chunk));
    if (!n) break;
    const size_t w = c.c.write(chunk, n);
    if (w == 0) break;                       // 对端断了
    sent += w;
    txBytes_ += w;
    if (millis() - lastProgress > 2000) break;   // 卡住就放弃，别把 netTask 占死
    lastProgress = millis();
    vTaskDelay(1);                            // 让出 CPU，别饿死 writerTask
  }
  f.close();
  c.c.flush();
  Serial.printf("[net] 下载 %s：已发送 %u / %u 字节\n",
                name.c_str(), (unsigned)sent, (unsigned)sz);
}

// ============================================================
//  WebSocket
// ============================================================
bool NetUI::doHandshake(int idx, const String& req) {
  String key = headerValue(req, "Sec-WebSocket-Key");
  if (key.length() == 0) return false;

  const char* GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";
  char joined[128];
  snprintf(joined, sizeof(joined), "%s%s", key.c_str(), GUID);

  Sha1State st;
  sha1Init(st);
  sha1Update(st, (const uint8_t*)joined, strlen(joined));
  uint8_t digest[20];
  sha1Final(st, digest);

  char accept[32];
  b64encode(digest, 20, accept);

  String resp;
  resp.reserve(192);
  resp += "HTTP/1.1 101 Switching Protocols\r\n";
  resp += "Upgrade: websocket\r\n";
  resp += "Connection: Upgrade\r\n";
  resp += "Sec-WebSocket-Accept: ";
  resp += accept;
  resp += "\r\n\r\n";

  Client& c = cl_[idx];
  c.c.write((const uint8_t*)resp.c_str(), resp.length());
  c.c.flush();
  txBytes_ += resp.length();
  return true;
}

void NetUI::sendWsFrame(int idx, uint8_t opcode, const uint8_t* p, size_t n) {
  Client& c = cl_[idx];
  if (c.state != 2) return;

  uint8_t hdr[10];
  hdr[0] = (uint8_t)(0x80 | (opcode & 0x0F));   // FIN=1；服务端发出的帧**不加掩码**
  size_t hlen;
  if (n < 126) {
    hdr[1] = (uint8_t)n;
    hlen = 2;
  } else if (n < 65536) {
    hdr[1] = 126;
    hdr[2] = (uint8_t)(n >> 8);
    hdr[3] = (uint8_t)n;
    hlen = 4;
  } else {
    hdr[1] = 127;
    for (int k = 0; k < 8; k++) hdr[2 + k] = (uint8_t)((uint64_t)n >> (56 - k * 8));
    hlen = 10;
  }
  c.c.write(hdr, hlen);
  if (n) c.c.write(p, n);
  txBytes_ += hlen + n;
}

void NetUI::sendWsClose(int idx) {
  if (cl_[idx].state != 2) return;
  const uint8_t payload[2] = {0x03, 0xE8};      // 1000 = 正常关闭
  sendWsFrame(idx, 0x08, payload, 2);
  cl_[idx].c.flush();
}

void NetUI::broadcastText(const char* s) {
  const size_t n = strlen(s);
  for (int i = 0; i < NET_MAX_CLIENTS; i++) {
    if (cl_[i].state == 2) sendWsFrame(i, 0x01, (const uint8_t*)s, n);
  }
}

void NetUI::sendTextTo(uint8_t idx, const char* s) {
  if (idx < NET_MAX_CLIENTS && cl_[idx].state == 2)
    sendWsFrame(idx, 0x01, (const uint8_t*)s, strlen(s));
}

// ------------------------------------------------------------
//  客户端帧解析（浏览器发来的帧**一定带掩码**，且载荷很小）
// ------------------------------------------------------------
void NetUI::parseFrames(int idx) {
  Client& c = cl_[idx];

  while (c.c.available() > 0 && c.rxLen < sizeof(c.rx)) {
    const int n = c.c.read(c.rx + c.rxLen, sizeof(c.rx) - c.rxLen);
    if (n <= 0) break;
    c.rxLen += n;
  }

  size_t off = 0;
  while (c.rxLen - off >= 2) {
    const uint8_t b0 = c.rx[off];
    const uint8_t b1 = c.rx[off + 1];
    const uint8_t opcode = b0 & 0x0F;
    const bool    masked = (b1 & 0x80) != 0;
    uint64_t      plen   = b1 & 0x7F;

    size_t hdrNeed = 2;
    if (plen == 126)      hdrNeed += 2;
    else if (plen == 127) hdrNeed += 8;
    if (masked) hdrNeed += 4;
    if (c.rxLen - off < hdrNeed) break;         // 头还没收全

    size_t p = off + 2;
    if (plen == 126) {
      plen = ((uint64_t)c.rx[p] << 8) | c.rx[p + 1]; p += 2;
    } else if (plen == 127) {
      plen = 0;
      for (int k = 0; k < 8; k++) plen = (plen << 8) | c.rx[p + k];
      p += 8;
    }
    uint8_t mask[4] = {0, 0, 0, 0};
    if (masked) { memcpy(mask, c.rx + p, 4); p += 4; }

    if (plen > sizeof(c.rx)) {
      // 命令不该这么大 → 视为协议异常，断开
      c.c.stop(); c.state = 0; c.rxLen = 0; c.req = "";
      return;
    }
    if (c.rxLen - off < hdrNeed + (size_t)plen) break;   // 载荷还没收全

    uint8_t payload[512];
    for (size_t k = 0; k < plen; k++)
      payload[k] = (uint8_t)(c.rx[p + k] ^ (masked ? mask[k & 3] : 0));
    payload[plen] = 0;
    off += hdrNeed + (size_t)plen;

    switch (opcode) {
      case 0x01: {                                  // 文本 → 命令 JSON
        if (cmdFn_) cmdFn_(String((const char*)payload));
        break;
      }
      case 0x08: {                                  // 客户端关闭
        sendWsFrame(idx, 0x08, payload, (size_t)plen);
        c.c.flush();
        c.c.stop(); c.state = 0; c.rxLen = 0; c.req = "";
        return;
      }
      case 0x09: {                                  // ping → 原样回 pong
        sendWsFrame(idx, 0x0A, payload, (size_t)plen);
        break;
      }
      default:                                       // 0x0A pong / 其他：忽略
        break;
    }
  }

  if (off) {
    memmove(c.rx, c.rx + off, c.rxLen - off);
    c.rxLen -= off;
  }
}
