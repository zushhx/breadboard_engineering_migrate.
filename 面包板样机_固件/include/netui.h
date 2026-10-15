#pragma once
#include <Arduino.h>
#include <WiFi.h>
#include "config.h"
#include "disp.h"

// ============================================================
//  无线 UI 服务（WiFi AP + HTTP + WebSocket）
//
//  ● 为什么是 SoftAP 而不是连实验室路由器
//    车间/实验室的 WiFi 常常要认证、要 IT 审批，而且机床附近信号不稳。
//    设备自己发热点（VibMon-XXXX），手机/电脑直连即可，**不依赖任何现场网络**。
//    想改回连路由器只需调 setStaMode()。
//
//  ● 为什么自己写 HTTP/WebSocket 而不用第三方库
//    ① 少一个构建期依赖 = 少一处下载失败/版本不兼容的风险；
//    ② 本项目只需要「握手 + 收发小帧」，用不到完整协议栈；
//    ③ SHA-1 / base64 / 帧编解码都是自包含实现（见 netui.cpp），行为完全可控。
//
//  ● 线程模型
//    loop() 只在 **core 0 的 netTask** 里跑。
//    与 core 1 的采集线程之间**只通过 DispPipeline 的无锁队列通信**，
//    所以网络卡顿不会拖慢采集（这也是为什么环形缓冲 + 独立写卡任务要保留）。
//
//  ● ⚠ 功耗现实
//    WiFi 发射峰值约 355 mA，板载 ME6217 LDO 在 5V→3.3V 下热限流约 588 mA，
//    叠加 TF 卡写入（~100 mA）已接近上限。故 NET_TX_POWER 压到 11 dBm 换余量。
//    若采集期间出现大量丢帧，先试「关闭 WiFi」命令对比，再考虑降采样率。
// ============================================================

class NetUI {
 public:
  // 返回 false = WiFi 启动失败（此时仍可通过串口用全部原命令）
  bool begin(DispPipeline* disp);
  void loop();                       // netTask 里循环调用
  void stop();

  // ---- 由 main.cpp 注入的两个回调 ----
  // 构建状态 JSON（设备侧才知道 INA226 / SD / 采集状态）
  void setStatusFn(void (*fn)(String& out)) { statusFn_ = fn; }
  // 处理来自 UI 的命令 JSON，例如 {"c":"start","s":90}
  void setCommandFn(void (*fn)(const String& json)) { cmdFn_ = fn; }
  // 可选：从 SD 卡读 UI 页面（返回 false 则回落到内置 PROGMEM 版本）
  void setAssetFn(bool (*fn)(const char* path, String& out)) { assetFn_ = fn; }
  // 可选：返回 true = 设备忙（正在采集）。忙时**拒绝文件下载** ——
  // 因为下载要读 SD，而写卡任务在另一个核上同时写 SD，争用同一套 SDMMC 驱动会出事。
  void setBusyFn(bool (*fn)()) { busyFn_ = fn; }

  // ---- 供 main 主动下发 ----
  void broadcastText(const char* s);
  void sendTextTo(uint8_t idx, const char* s);

  // ---- 统计与查询 ----
  bool     wifiUp()   const { return up_; }
  bool     enabled()  const { return enabled_; }
  void     setEnabled(bool on);          // 采集保真优先时可临时关掉电台
  uint8_t  wsClients() const { return wsCount_; }
  uint32_t framesSent() const { return framesSent_; }
  uint32_t framesQueued() const { return framesQueued_; }
  uint32_t txBytes()  const { return txBytes_; }
  const char* ssid()  const { return ssid_; }
  IPAddress   ip()    const { return ip_; }

 private:
  struct Client {
    WiFiClient c;
    uint8_t    state = 0;      // 0=空闲  1=正在读 HTTP 请求头  2=WebSocket 已建立
    uint32_t   deadlineMs = 0; // 读 HTTP 头的超时点（浏览器会开"预连接"不发数据，必须超时清理）
    String     req;            // HTTP 请求头累积
    uint8_t    rx[512];        // WebSocket 收帧缓冲（命令都很小，512B 足够）
    size_t     rxLen = 0;
  };

  // ---- HTTP ----
  void serveAsset(int idx, const char* path);
  void serveStatus(int idx);
  // /dl?f=vib_0001.bin —— 把 TF 卡上的原始数据流给浏览器下载
  void serveDownload(int idx, const String& query);
  void writeHttp(int idx, int code, const char* ctype, const String& body);

  // ---- WebSocket ----
  bool doHandshake(int idx, const String& req);
  void parseFrames(int idx);
  void sendWsFrame(int idx, uint8_t opcode, const uint8_t* p, size_t n);
  void sendWsClose(int idx);

  Client   cl_[NET_MAX_CLIENTS];
  uint8_t  wsCount_ = 0;

  DispPipeline* disp_ = nullptr;
  WiFiServer    srv_{NET_HTTP_PORT};

  void (*statusFn_)(String&)             = nullptr;
  void (*cmdFn_)(const String&)          = nullptr;
  bool (*assetFn_)(const char*, String&) = nullptr;
  bool (*busyFn_)()                      = nullptr;

  bool      up_      = false;
  bool      enabled_ = true;
  char      ssid_[32] = {0};
  IPAddress ip_;

  uint32_t  lastStatusMs_ = 0;
  uint32_t  framesSent_   = 0;
  uint32_t  framesQueued_ = 0;
  uint32_t  txBytes_      = 0;

  uint8_t   txBuf_[640];
};
