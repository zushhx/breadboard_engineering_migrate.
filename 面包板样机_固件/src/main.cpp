// ============================================================
//  面包板样机 · 主固件（C 混合路线）
//  目标板：立创·ESP32S3R8N8 (C20626143)
//
//  三条数据通路，各走各的，互不拖累：
//
//   ┌─ 原始全速信号 ────────────────────────────────────────┐
//   │ core 1（Arduino loop）：轮询 DRDY → 读 6 字节 → 环形缓冲 │
//   │ core 0（writerTask）  ：批量取出 → 写 TF 卡              │
//   │ 方向：SD 卡   速率：160 KB/s   内容：未处理的 int16      │
//   └──────────────────────────────────────────────────────┘
//
//   ┌─ 实时显示信号（"被处理过、不大"）──────────────────────┐
//   │ core 1：同一个样本顺手喂给 DispPipeline                 │
//   │         → 去直流 + min/max 包络抽帧（1026 Hz）           │
//   │ core 0（netTask）：WebSocket 二进制帧 → 浏览器 UI        │
//   │ 方向：WiFi    速率：12.7 KB/s   内容：已处理的包络       │
//   └──────────────────────────────────────────────────────┘
//
//   ┌─ 状态量（电量 / 无线充 / 电流 / SD 进度）──────────────┐
//   │ core 0：2 Hz 组 JSON 广播                               │
//   └──────────────────────────────────────────────────────┘
//
//  为什么必须解耦：26.667kHz 下样本间隔仅 37.5µs，而一次批量写卡
//  会阻塞数毫秒。单线程下这段时间必然丢样本。环形缓冲（192KB PSRAM）
//  用来吸收抖动 —— 所以 WiFi 就算卡顿，丢的也只是显示帧，SD 卡不受影响。
// ============================================================

#include <Arduino.h>
#include <SPI.h>
#include <Wire.h>
#include <SD_MMC.h>
#include <stdarg.h>
#include <string.h>
#include "esp_heap_caps.h"
#include "esp_system.h"

#include "config.h"
#include "iis3dwb.h"
#include "ina226.h"
#include "recorder.h"
#include "disp.h"
#include "power.h"
#include "netui.h"

// ---------------- 对象 ----------------
static IIS3DWB  sensor;
// 注：变量名不能叫 ina —— 会与 ina226.h 里的 namespace ina 撞名（首次编译实测）
static INA226   powerMon;
static Recorder rec;
static DispPipeline disp;
static PowerMon     power;
static NetUI        net;

// ---------------- 环形缓冲（样本槽，容量为 2 的幂） ----------------
static const uint32_t RING_SAMPLES = 32768;          // 32768 × 6B = 192KB（放 PSRAM）
static const uint32_t RING_MASK    = RING_SAMPLES - 1;
static int16_t*        ring        = nullptr;        // 长度 RING_SAMPLES × 3
static volatile uint32_t rHead = 0;                  // 生产者（采集）写指针
static volatile uint32_t rTail = 0;                  // 消费者（写卡）读指针
static volatile uint32_t dropCount = 0;              // 因缓冲满而丢弃的样本数

// ---------------- 运行状态 ----------------
static volatile bool     recording   = false;
static volatile bool     sdTestBusy  = false;
static volatile bool     segPending  = false;
static volatile bool     stopPending = false;
static volatile uint64_t capturedSamples = 0;
static uint32_t          recordStartMs = 0;
static uint32_t          recordTargetSec = RECORD_SECONDS;

// ---- 分段落盘（MAX_SEGMENT_SEC 的真正落地） ----
//  ★ 此前 MAX_SEGMENT_SEC 只在 config.h 里定义了、代码里从没用过，
//    于是 90 秒采集只开一个文件，掉电会丢**全部 90 秒**；
//    而文档承诺"掉电最多丢当前这一段（10 秒）"。现已实现真正的分段。
static uint32_t segStartMs     = 0;    // 本段起始时刻
static uint32_t dropAtSegStart = 0;    // 本段开始时的 dropCount（用于回填文件头）

static bool sensorOk = false;
static bool inaOk    = false;
static bool sdOk     = false;

// 上一次错误快照（自检用）
static uint8_t sensorWhoAmI = 0;

static uint32_t lastPowerMs = 0;

// ---- 录制保真模式：录制期间关掉 WiFi ----
//  recWifiOff_     ：用户设定的策略（UI 勾选 / 串口 m 切换）
//  wifiOffPending_ ：已安排关闭，等延迟到点
//  wifiOffByRec_   ：确认是"因为我们自己关的"，录完才由我们恢复
//                    （用户手动用 n 关的，不能被录制逻辑擅自打开）
static bool     recWifiOff_     = (REC_WIFI_OFF_DEFAULT != 0);
static bool     wifiOffPending_ = false;
static uint32_t wifiOffAtMs_    = 0;
static bool     wifiOffByRec_   = false;

// ---- 前置声明（handleUiCommand 要调这两个） ----
static void startRecording(uint32_t seconds);
static void stopRecording();

// 字符串化宏（自检文本里要把引脚号嵌进字符串）
#define STR_HELPER(x) #x
#define STR(x) STR_HELPER(x)

// ============================================================
//  工具
// ============================================================
static inline void ledOn()  { digitalWrite(PIN_LED, HIGH); }
static inline void ledOff() { digitalWrite(PIN_LED, LOW); }

// 往 String 里追加格式化文本（String 没有 printf，自备一个）
static void appf(String& s, const char* fmt, ...) {
  char buf[224];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(buf, sizeof(buf), fmt, ap);
  va_end(ap);
  s += buf;
}

static void banner() {
  Serial.println();
  Serial.println(F("=============================================="));
  Serial.println(F(" 面包板样机 · 振动监测固件 (C 路线)"));
  Serial.println(F(" 主控：立创 ESP32S3R8N8 (C20626143)"));
  Serial.println(F(" 新增：浏览器 UI（形态=手机/电脑，走 WiFi）"));
  Serial.println(F("=============================================="));
}

static void help() {
  Serial.println(F("命令："));
  Serial.println(F("  i  自检报告"));
  Serial.println(F("  w  I2C 地址扫描"));
  Serial.println(F("  v  振动实时读数（约 2 秒）"));
  Serial.println(F("  j  传感器身份及配置寄存器实时回读"));
  Serial.println(F("  k  静置统计（单姿态不判灵敏度、不改系数）"));
  Serial.println(F("  c  电流/电压实时读数（约 2 秒）"));
  Serial.println(F("  u  INA226 身份、配置与校准寄存器实时回读"));
  Serial.println(F("  p  TF 卡写入吞吐自测"));
  Serial.println(F("  r  TF卡挂载失败时：400kHz四位/一位初始化诊断（不格式化）"));
  Serial.println(F("  p soak 600  模拟满速数据持续写卡600秒并完整读回；x可取消"));
  Serial.println(F("  l  列出 TF 卡文件"));
  Serial.println(F("  s  开始采集（默认 " "90" " 秒；'s 30' 可指定秒数）"));
  Serial.println(F("  x  停止采集"));
  Serial.println(F("  n  ★ 开关设备 WiFi（UI 依赖它；关掉可去掉射频干扰）"));
  Serial.println(F("  m  ★ 切换「录制时关 WiFi」保真模式（默认 "
                   "关" "；开启后录完自动恢复并可下载原始文件）"));
  Serial.println(F("  h  本帮助"));
  Serial.println(F(""));
  Serial.println(F("UI 用法：手机/电脑连 WiFi →"));
  Serial.printf ("  热点 %s  密码 %s  浏览器打开 http://%s/\n",
                 net.ssid(), NET_PASS, net.ip().toString().c_str());
  Serial.printf ("原始数据下载：http://%s/dl?f=vib_00000.bin（采集中会拒绝）\n",
                 net.ip().toString().c_str());
}

// ============================================================
//  自检
// ============================================================
static const char* b2s(bool b) { return b ? "OK" : "FAIL"; }

static void buildSelfText(String& out) {
  out.reserve(1400);
  out += "---------- 自检报告 ----------\n";

  appf(out, "传感器 IIS3DWB : %s   WHO_AM_I=0x%02X (应为 0x7B)\n",
       b2s(sensorOk), sensorWhoAmI);
  if (!sensorOk) out += "   → 查 SPI Mode 3 / CS 极性 / 电源与地；电源没接错时 SPI 接错不会烧件\n";

  appf(out, "电流 INA226    : %s   ", b2s(inaOk));
  if (inaOk) {
    uint16_t liveCal = powerMon.calibrationRegisterValue();
    appf(out, "MANUF=0x%04X DIE=0x%04X CAL=%u (设定 %u) Current_LSB=%.3f mA\n",
         powerMon.manufacturerId(), powerMon.dieId(),
         liveCal, powerMon.calibrationValue(), powerMon.currentLsb_A() * 1000.0f);
    if (liveCal != powerMon.calibrationValue()) {
      out += "   → 校准寄存器与设定不一致，电流/功率读数暂不可用；检查供电并重新上电\n";
    }
    appf(out, "   Rshunt=%.4f Ω → 满量程 ±%.4f A（模块自带分流，未更换）\n",
         SHUNT_OHM, INA_MAX_CURRENT);
  } else {
    appf(out, "ADDR=0x%02X MANUF=0x%04X DIE=0x%04X（身份校验或配置失败）\n",
         INA_ADDR, powerMon.manufacturerId(), powerMon.dieId());
    out += "   → 核对模块型号、地址拨位和 I2C 接线\n";
  }

  appf(out, "TF 卡 (SDMMC)  : %s   ", b2s(sdOk));
  if (sdOk) {
    appf(out, "容量 %u MB，剩余 %u MB，卡类型 %u，配置频率 %u kHz，%u位\n",
         rec.cardSizeMB(), rec.cardFreeMB(), rec.cardType(), rec.frequencyKhz(), rec.mode1bit() ? 1u : 4u);
    appf(out, "  下一个文件名 %s（已存在 %u 段，编号不会重头覆盖）\n",
         rec.currentFileName(), (unsigned)rec.nextIndex());
    appf(out, "  累计写失败 %u 次，因写失败丢 %u 样本\n",
         (unsigned)rec.writeFails(), (unsigned)rec.lostSamples());
    if (rec.lostSamples()) out += "  ★ 卡上已有不完整数据 —— 段文件的文件头保留区会标注是哪一段\n";
  } else {
    out += "挂载失败\n";
    out += "   → 查卡座供电/共地与CLK/CMD/D0..D3；输入r做400kHz初始化诊断\n";
  }

  appf(out, "采集缓冲       : %s  (%lu 样本 / %lu KB PSRAM)\n",
       b2s(ring != nullptr), (unsigned long)RING_SAMPLES,
       (unsigned long)(RING_SAMPLES * 6 / 1024));

  // ---- 电源 / 电量 ----
  appf(out, "电池           : %.3f V  ≈ %u%%  趋势 %s   (ADC 原始 %u mV 脚上)\n",
       power.vbat(), power.socPct(),
       power.socTrend() > 0 ? "升" : (power.socTrend() < 0 ? "降" : "平"),
       power.rawVbat());
  appf(out, "无线充         : %s   Qi 输出 %.3f V   (ADC 原始 %u mV 脚上)\n",
       power.chgText(), power.qiVolt(), power.rawQi());
  if (!power.qiPresent() && power.qiVolt() < 0.2f)
    out += "   → Qi 未放置，或分压网络还没焊（Pin " STR(PIN_QIDET) "，10k/10k）\n";

  // ---- 网络 ----
  appf(out, "WiFi           : %s   SSID %s   密码 %s   IP %s\n",
       net.wifiUp() ? "开启" : "关闭", net.ssid(), NET_PASS,
       net.ip().toString().c_str());
  appf(out, "  WebSocket 客户端 %u 个，已上行 %lu 帧 / %lu KB，队列丢帧 %lu\n",
       net.wsClients(), (unsigned long)net.framesSent(),
       (unsigned long)(net.txBytes() / 1024), (unsigned long)disp.frameDropCount());
  appf(out, "录制时关 WiFi  : %s%s\n",
       recWifiOff_ ? "开（保真优先）" : "关（保持实时推流）",
       wifiOffByRec_ ? "  ← 当前正因录制而关闭" : "");
  out += "  ⚠ 功耗提示：电台保持关联约 +100 mA（RX 95mA 为主），TX 峰值 355mA；\n";
  out += "     而显示流仅 12.7 KB/s，在 54Mbps 下只占空口 0.5% —— 耗电大头是电台开着，不是传数据。\n";

  // ---- 显示链路 ----
  const DispStats& st = disp.stats();
  appf(out, "显示抽帧       : 1:%u  → %u Hz（约 %.1f fps 上行）\n",
       DISP_DECIM, st.rateHz, (double)st.rateHz / DISP_PTS);
  appf(out, "直流(被减掉的) : X %+.1f  Y %+.1f  Z %+.1f  mg\n",
       st.dcMg[0], st.dcMg[1], st.dcMg[2]);

  appf(out, "内存           : 空闲堆 %u B，PSRAM 空闲 %u B\n",
       ESP.getFreeHeap(), ESP.getFreePsram());
  out += "-------------------------------\n";
  out += "提示：UI 里切到「直流(含重力)」可把上面这组直流加回去看原始量。\n";
}

static void selfReport() {
  String s;
  buildSelfText(s);
  Serial.print(s);
}

// ============================================================
//  环形缓冲
// ============================================================
static inline uint32_t ringCount() {
  return (rHead - rTail) & RING_MASK;
}

static inline bool ringPush(int16_t x, int16_t y, int16_t z) {
  uint32_t next = (rHead + 1) & RING_MASK;
  if (next == rTail) { dropCount++; return false; }   // 满
  int16_t* p = ring + (size_t)rHead * 3;
  p[0] = x; p[1] = y; p[2] = z;
  rHead = next;
  return true;
}

static size_t ringPop(int16_t* dst, size_t maxSamples) {
  size_t n = 0;
  while (n < maxSamples && rTail != rHead) {
    const int16_t* p = ring + (size_t)rTail * 3;
    dst[n * 3 + 0] = p[0];
    dst[n * 3 + 1] = p[1];
    dst[n * 3 + 2] = p[2];
    rTail = (rTail + 1) & RING_MASK;
    n++;
  }
  return n;
}

// ============================================================
//  写卡任务（core 0）
// ============================================================
static void writerTask(void* arg) {
  (void)arg;
  int16_t* buf = (int16_t*)heap_caps_malloc(BUF_SAMPLES * 3 * sizeof(int16_t),
      MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA);
  if (!buf) buf = (int16_t*)malloc(BUF_SAMPLES * 3 * sizeof(int16_t));
  if (!buf) {
    Serial.println(F("[writer] 缓冲分配失败，写卡任务退出"));
    vTaskDelete(nullptr);
    return;
  }

  for (;;) {
    // 段的开合由本任务独占执行，避免与写操作竞争 File 句柄
    if (segPending) {
      segPending = false;
      // 关旧段前把"这一段的缓冲丢样本数"交给 recorder 回填进文件头
      rec.setClosingDrop(dropCount - dropAtSegStart);
      if (rec.startSegment()) {
        dropAtSegStart = dropCount;
        Serial.printf("[writer] 新段 %s\n", rec.currentFileName());
      } else {
        Serial.println(F("[writer] 开段失败（卡满或掉卡？）"));
        recording = false;
      }
    }

    size_t n = 0;
    if (recording || ringCount() > 0) {
      n = ringPop(buf, BUF_SAMPLES);
      if (n) rec.writeSamples(buf, n);
    }

    if (stopPending && ringCount() == 0) {
      stopPending = false;
      rec.setClosingDrop(dropCount - dropAtSegStart);
      rec.endSegment();
      Serial.println(F("[writer] 已落盘并关闭文件"));
      recording = false;
    }

    if (n == 0) vTaskDelay(1);
    else taskYIELD();
  }
}

// ============================================================
//  状态 JSON（UI 的"仪表盘"数据源，2 Hz）
// ============================================================
static void appendArr3(String& j, const char* name, const float* v, int dec) {
  j += ",\"";
  j += name;
  j += "\":[";
  for (int i = 0; i < 3; i++) {
    if (i) j += ',';
    j += String(v[i], dec);
  }
  j += ']';
}

static void buildStatusJson(String& j) {
  static uint32_t cachedFreeMB = 0;
  if (!sdTestBusy) cachedFreeMB = rec.cardFreeMB();
  static uint32_t lastFrames = 0;
  static uint32_t lastFpsMs  = 0;

  const uint32_t now = millis();
  const uint32_t fr  = net.framesSent();
  float fps = 0.0f;
  if (lastFpsMs == 0) { lastFpsMs = now; lastFrames = fr; }
  else if (now - lastFpsMs >= STATUS_PERIOD_MS) {
    fps = (float)(fr - lastFrames) * 1000.0f / (float)(now - lastFpsMs);
    lastFrames = fr;
    lastFpsMs  = now;
  } else {
    fps = (float)(fr - lastFrames) * 1000.0f / (float)(now - lastFpsMs + 1);
  }

  const DispStats& st = disp.stats();

  j = "{\"ty\":\"st\"";
  appf(j, ",\"tm\":%lu", (unsigned long)now);

  // ---- 电池 ----
  appf(j, ",\"bat_v\":%.3f,\"bat_pct\":%u,\"bat_tr\":%d",
       power.vbat(), (unsigned)power.socPct(), (int)power.socTrend());

  // ---- 无线充 / 供电 ----
  appf(j, ",\"qi\":%u,\"qi_v\":%.3f", power.qiPresent() ? 1u : 0u, power.qiVolt());
  appf(j, ",\"bus_v\":%.3f,\"bus_ma\":%.1f,\"bus_mw\":%.1f",
       inaOk ? powerMon.busVoltage_V() : 0.0f,
       inaOk ? powerMon.current_mA()    : 0.0f,
       inaOk ? powerMon.power_mW()      : 0.0f);

  // ---- SD / 采集 ----
  appf(j, ",\"sdp\":%u,\"sensor\":%u,\"ina\":%u",
       sdOk ? 1u : 0u, sensorOk ? 1u : 0u, inaOk ? 1u : 0u);
  j += ",\"sdf\":\"";
  j += (rec.currentFileName() ? rec.currentFileName() : "");
  j += "\"";
  appf(j, ",\"sd_mb\":%.3f,\"cap\":%llu,\"drop\":%lu",
       (double)rec.totalSamples() * 6.0 / 1048576.0,
       (unsigned long long)rec.totalSamples(),
       (unsigned long)dropCount);
  // sd_fail = 因写文件失败而丢的样本数（卡满/掉卡/坏块）；sd_drop = 环形缓冲满丢的
  // 两者语义不同，必须分开报 —— 否则用户看到"丢 0"会误以为数据是全的
  appf(j, ",\"sd_fail\":%lu,\"sd_drops\":%lu,\"sd_free\":%lu,\"seg\":%u",
       (unsigned long)rec.lostSamples(),
       (unsigned long)rec.writeFails(),
       (unsigned long)cachedFreeMB,
       (unsigned)MAX_SEGMENT_SEC);
  appf(j, ",\"rec\":%u,\"sec\":%lu,\"tgt\":%lu",
       recording ? 1u : 0u,
       recording ? (unsigned long)((now - recordStartMs) / 1000) : 0UL,
       (unsigned long)recordTargetSec);

  // ---- 显示抽帧 ----
  appf(j, ",\"fs\":%u,\"decim\":%u", st.rateHz, (unsigned)DISP_DECIM);
  appendArr3(j, "dc",  st.dcMg,  2);
  appendArr3(j, "rms", st.rmsMg, 2);
  appendArr3(j, "pk",  st.pkMg,  2);

  // ---- 系统 ----
  appf(j, ",\"fps\":%.1f,\"cl\":%u,\"qdrop\":%lu,\"heap\":%u,\"psram\":%u",
       fps, (unsigned)net.wsClients(),
       (unsigned long)disp.frameDropCount(),
       ESP.getFreeHeap(), ESP.getFreePsram());
  appf(j, ",\"wifi\":%u", net.wifiUp() ? 1u : 0u);
  appf(j, ",\"rw\":%u", recWifiOff_ ? 1u : 0u);   // 录制时关 WiFi（保真优先）
  j += "}";
}

// ============================================================
//  UI 命令处理（来自 WebSocket 文本帧）
//
//  刻意不做完整 JSON 解析：命令就 7 种，把空格去掉后按关键字匹配
//  足够可靠，还省掉一个 JSON 库。前端一律用 JSON.stringify 发，
//  格式固定为 {"c":"xxx","k":v}。
// ============================================================
static void handleUiCommand(const String& raw) {
  if (sdTestBusy) {
    net.broadcastText("{\"ty\":\"ack\",\"text\":\"TF卡正在测试，请等待完成\"}");
    return;
  }
  String s = raw;
  s.replace(" ", "");
  s.replace("\n", "");
  s.replace("\r", "");

  if (s.indexOf("\"c\":\"start\"") >= 0) {
    if (!sdOk) {
      net.broadcastText("{\"ty\":\"ack\",\"text\":\"TF卡未挂载，无法采集\"}");
      return;
    }
    if (!sensorOk) {
      net.broadcastText("{\"ty\":\"ack\",\"text\":\"振动传感器未就绪，无法采集\"}");
      return;
    }
    if (stopPending) {
      net.broadcastText("{\"ty\":\"ack\",\"text\":\"正在落盘，请等待完成\"}");
      return;
    }
    if (recording) {
      net.broadcastText("{\"ty\":\"ack\",\"text\":\"已在采集中\"}");
      return;
    }
    int p = s.indexOf("\"s\":");
    uint32_t sec = 0;
    if (p >= 0) sec = (uint32_t)s.substring(p + 4).toInt();
    startRecording(sec);
    net.broadcastText("{\"ty\":\"ack\",\"text\":\"已开始采集\"}");
    return;
  }
  if (s.indexOf("\"c\":\"stop\"") >= 0) {
    stopRecording();
    net.broadcastText("{\"ty\":\"ack\",\"text\":\"已停止，正在落盘\"}");
    return;
  }
  if (s.indexOf("\"c\":\"zero\"") >= 0) {
    disp.requestZero();
    net.broadcastText("{\"ty\":\"ack\",\"text\":\"已清零\"}");
    return;
  }
  if (s.indexOf("\"c\":\"self\"") >= 0) {
    String t;
    buildSelfText(t);
    t.replace("\r", "");
    t.replace("\n", "\\n");
    String out = "{\"ty\":\"self\",\"text\":\"";
    out += t;
    out += "\"}";
    net.broadcastText(out.c_str());
    return;
  }
  if (s.indexOf("\"c\":\"list\"") >= 0) {
    String out = "{\"ty\":\"files\",\"items\":[";
    if (sdOk) {
      File root = SD_MMC.open("/");
      if (root) {
        File f = root.openNextFile();
        bool first = true;
        while (f) {
          if (!f.isDirectory()) {
            if (!first) out += ',';
            first = false;
            out += "{\"n\":\"";
            out += f.name();
            appf(out, "\",\"b\":%lu}", (unsigned long)f.size());
          }
          f = root.openNextFile();
        }
      }
    }
    out += "]}";
    net.broadcastText(out.c_str());
    return;
  }
  if (s.indexOf("\"c\":\"wifi\"") >= 0) {
    const bool on = s.indexOf("\"on\":0") < 0;
    if (on) {
      net.setEnabled(true);
      net.broadcastText("{\"ty\":\"ack\",\"text\":\"WiFi 已开启\"}");
    } else {
      net.broadcastText("{\"ty\":\"ack\",\"text\":\"WiFi 即将关闭，断电重启可恢复\"}");
      delay(160);
      net.setEnabled(false);
    }
    return;
  }
  if (s.indexOf("\"c\":\"recwifi\"") >= 0) {
    // 录制保真模式开关：录制时把电台彻底断电
    recWifiOff_ = (s.indexOf("\"on\":0") < 0);
    String r = "{\"ty\":\"ack\",\"text\":\"录制时关 WiFi：";
    r += recWifiOff_ ? "开（保真优先）\"}" : "关（保持实时推流）\"}";
    net.broadcastText(r.c_str());
    return;
  }
  // 未知命令：不回话（避免 UI 被无意义提示刷屏）
}

// ============================================================
//  UI 页面优先从 TF 卡读取（改界面不必重编译固件）
// ============================================================
static bool assetFromSd(const char* path, String& out) {
  if (!sdOk || sdTestBusy) return false;
  // 只认根路径与 /ui.html，避免把整张卡暴露成文件服务
  if (!(strcmp(path, "/") == 0 || strcmp(path, "/ui.html") == 0)) return false;

  File f = SD_MMC.open("/ui.html", FILE_READ);
  if (!f) return false;
  const size_t n = f.size();
  if (n == 0 || n > 300 * 1024) { f.close(); return false; }

  out.reserve(n + 1);
  uint8_t chunk[512];
  while (f.available()) {
    const size_t got = f.read(chunk, sizeof(chunk));
    if (!got) break;
    for (size_t i = 0; i < got; i++) out += (char)chunk[i];
  }
  f.close();
  return out.length() > 0;
}

// ============================================================
//  网络任务（core 0）
// ============================================================
static void netTask(void* arg) {
  (void)arg;
  for (;;) {
    // 电源采样放这里：不在采集核心路径上，2 秒一次足够
    if (millis() - lastPowerMs >= 2000) {
      lastPowerMs = millis();
      if (inaOk) {
        power.setBusVolt_V(powerMon.busVoltage_V());
        power.setLoadMA(powerMon.current_mA());
      }
      power.update();
    }
    net.loop();
    vTaskDelay(1);
  }
}

// ============================================================
//  命令实现
// ============================================================
static void cmdScanI2C() {
  uint8_t found[16];
  int n = INA226::scanBus(&Wire, found, 16);
  Serial.printf("I2C 扫描：发现 %d 个设备 → ", n);
  for (int i = 0; i < n && i < 16; i++) Serial.printf("0x%02X ", found[i]);
  Serial.println();
  if (n == 0) Serial.println(F("  一个都没有 → 查上拉电阻与接线"));
}

static void cmdVibRead() {
  if (!sensorOk) { Serial.println(F("传感器未就绪")); return; }
  Serial.println(F("X(g)\tY(g)\tZ(g)\t合模量(g)"));
  uint32_t t0 = millis();
  while (millis() - t0 < 2000) {
    if (sensor.dataReady()) {
      int16_t x, y, z;
      sensor.readRaw(x, y, z);
      float fx = sensor.rawToG(x), fy = sensor.rawToG(y), fz = sensor.rawToG(z);
      Serial.printf("%.3f\t%.3f\t%.3f\t%.3f\n",
                    fx, fy, fz, sqrtf(fx * fx + fy * fy + fz * fz));
      delay(50);   // 只打点，不追高速
    }
  }
  Serial.println(F("※ 静置数据包含重力和零偏；记录板的朝向，不能只凭一次读数判精度。"));
}

static void cmdSensorRegisters() {
  const auto regs = sensor.registerSnapshot();
  const uint8_t c3Expected = iis3::CTRL3_BDU | iis3::CTRL3_IFINC;
  const bool identityOk = regs.identity == iis3::WHO_AM_I_VALUE;
  const bool configOk = regs.ctrl1 == iis3::CTRL1_XL_26K7_2G &&
                       (regs.ctrl3 & c3Expected) == c3Expected &&
                       (regs.ctrl6 & 0x03) == iis3::CTRL6_C_3AXIS;
  Serial.printf("[iischeck] WHO=%02X CTRL1=%02X CTRL3=%02X CTRL6=%02X STATUS=%02X identity=%s config=%s\n",
                (unsigned)regs.identity, (unsigned)regs.ctrl1, (unsigned)regs.ctrl3,
                (unsigned)regs.ctrl6, (unsigned)regs.status,
                identityOk ? "OK" : "FAIL", configOk ? "OK" : "FAIL");
}

static void cmdInaRegisters() {
  const uint16_t manufacturer = powerMon.manufacturerId();
  const uint16_t die = powerMon.dieId();
  const uint16_t config = powerMon.configurationRegisterValue();
  const uint16_t calibration = powerMon.calibrationRegisterValue();
  const bool identityOk = manufacturer == ina::MANUF_ID_EXPECT && die == ina::DIE_ID_EXPECT;
  Serial.printf("[inacheck] MANUF=%04X DIE=%04X CONFIG=%04X CAL=%u identity=%s config=%s calibration=%s\n",
                (unsigned)manufacturer, (unsigned)die, (unsigned)config, (unsigned)calibration,
                identityOk ? "OK" : "FAIL", config == ina::CONFIG_EXPECT ? "OK" : "FAIL",
                calibration == powerMon.calibrationValue() && calibration != 0 ? "OK" : "FAIL");
}

// 单姿态静置统计。合模量偏差同时受零偏、标度、环境与读数条件影响，
// 不能直接拿灵敏度的 ±2% 公差作合格判据，也不能据此建议修改换算系数。
static void cmdCalibCheck() {
  if (!sensorOk) { Serial.println(F("传感器未就绪")); return; }

  const int N = 400;                  // 固定样本数；本命令不验收噪声或频率响应
  double sx = 0, sy = 0, sz = 0;
  int got = 0;
  uint32_t t0 = millis();

  Serial.println(F("静置统计中，请保持传感器完全静止……"));
  while (got < N && millis() - t0 < 5000) {
    if (sensor.dataReady()) {
      int16_t x, y, z;
      sensor.readRaw(x, y, z);
      sx += sensor.rawToG(x);
      sy += sensor.rawToG(y);
      sz += sensor.rawToG(z);
      got++;
    }
  }
  if (got < 20) {
    Serial.printf("样本太少（%d 个），先检查传感器通信\n", got);
    return;
  }

  float mx = sx / got, my = sy / got, mz = sz / got;
  float mag    = sqrtf(mx * mx + my * my + mz * mz);
  float errPct = (mag - 1.0f) * 100.0f;

  Serial.printf("有效样本 %d 个\n", got);
  Serial.printf("平均加速度   X=%+.4f  Y=%+.4f  Z=%+.4f  (g)\n", mx, my, mz);
  Serial.printf("合模量 |a|   %.4f g\n", mag);
  Serial.printf("相对 1g 的合模量差 %+.2f %%（不等同于灵敏度误差）\n", errPct);
  Serial.println(F("本次仅记录静置基线，不判定灵敏度合格，不修改 SENS_MG_PER_LSB。"));
  Serial.println(F("下一步：同一轴沿重力方向正、反放置，分别静置读 a+、a−。"));
  Serial.println(F("零偏估计=(a+ + a−)/2；按 1g 换算的标度比=|a+ − a−|/2。"));
}

static void cmdCurRead() {
  if (!inaOk) { Serial.println(F("INA226 未就绪")); return; }
  Serial.println(F("Vbus(V)\tVshunt(mV)\tI(mA)\tP(mW)"));
  uint32_t t0 = millis();
  while (millis() - t0 < 2000) {
    const uint16_t liveCal = powerMon.calibrationRegisterValue();
    if (liveCal == 0 || liveCal != powerMon.calibrationValue()) {
      Serial.printf("[ina] CAL=%u（设定 %u），电流/功率无效；停止数值输出\n",
                    (unsigned)liveCal, (unsigned)powerMon.calibrationValue());
      Serial.printf("[ina] 独立电压寄存器：Vbus=%.3f V Vshunt=%.3f mV\n",
                    powerMon.busVoltage_V(), powerMon.shuntVoltage_mV());
      return;
    }
    Serial.printf("%.3f\t%.3f\t%.2f\t%.2f\n",
                  powerMon.busVoltage_V(), powerMon.shuntVoltage_mV(),
                  powerMon.current_mA(), powerMon.power_mW());
    delay(200);
  }
}

static void cmdThroughput(bool direct = false) {
  if (recording || stopPending) { Serial.println(F("请先停止录制并等待落盘")); return; }
  if (!sdOk) { Serial.println(F("TF 卡未挂载")); return; }
  Serial.println(F("正在自测写入吞吐（100 万样本，6 MB，随后完整读回）..."));
  sdTestBusy = true;
  double sps = rec.selfTestThroughput(1000000, direct);
  sdTestBusy = false;
  if (sps <= 0) { Serial.println(F("自测失败")); return; }
  double kbps = sps * 6.0 / 1024.0;
  Serial.printf("实测 %.0f 样本/秒 = %.1f KiB/s = %.3f MB/s\n", sps, kbps, sps * 6.0 / 1000000.0);
  Serial.printf("需求：%.1f KiB/s（满速 %u Hz × 6B）→ %s\n",
                (double)SAMPLE_RATE_HZ * 6.0 / 1024.0, SAMPLE_RATE_HZ,
                (kbps > (double)SAMPLE_RATE_HZ * 6.0 / 1024.0 * 1.3) ? "短测吞吐余量充足，持续录制仍待验证" : "★ 余量不足，需优化写入或降低采样率");
}

static void cmdSdProbe() {
  if (recording || stopPending || sdTestBusy || ringCount() > 0) {
    Serial.println("[sdprobe] busy; no changes made");
    return;
  }
  if (sdOk) {
    Serial.println("[sdprobe] already mounted; no reinitialization performed");
    return;
  }
  sdTestBusy = true;
  for (unsigned mode = 0; mode < 2 && !sdOk; ++mode) {
    SD_MMC.end();
    delay(100);
    Serial.printf("[sdprobe] trying 400 kHz, %u-bit\n", mode ? 1u : 4u);
    sdOk = rec.begin(400, mode != 0);
    Serial.printf("[sdprobe] result=%s\n", sdOk ? "MOUNTED" : "FAILED");
    Recorder::printPinDiagnostics("after-mount-return");
  }
  sdTestBusy = false;
  Serial.println("[sdprobe] done");
}

static void cmdList() {
  if (!sdOk) { Serial.println(F("TF 卡未挂载")); return; }
  File root = SD_MMC.open("/");
  if (!root) { Serial.println(F("无法打开根目录")); return; }
  File f = root.openNextFile();
  while (f) {
    Serial.printf("%-24s %9u B\n", f.name(), (unsigned)f.size());
    f = root.openNextFile();
  }
}

static void startRecording(uint32_t seconds) {
  if (sdTestBusy) { Serial.println(F("TF卡正在测试，请等待完成")); return; }
  if (stopPending) { Serial.println(F("正在落盘，请等待完成")); return; }
  if (!sdOk)     { Serial.println(F("TF 卡未挂载，无法采集")); return; }
  if (!sensorOk) { Serial.println(F("传感器未就绪，无法采集")); return; }
  if (recording) { Serial.println(F("已在采集中")); return; }
  recordTargetSec = seconds ? seconds : RECORD_SECONDS;
  rHead = rTail = 0;
  dropCount = 0;
  dropAtSegStart = 0;
  capturedSamples = 0;
  recordStartMs = millis();
  segStartMs    = recordStartMs;
  segPending = true;
  recording = true;
  Serial.printf("开始采集：%u Hz，%u 秒，预计 %.1f MB（每 %u 秒分一段，掉电最多丢当前段）\n",
                (unsigned)SAMPLE_RATE_HZ, (unsigned)recordTargetSec,
                (double)SAMPLE_RATE_HZ * 6.0 * recordTargetSec / 1048576.0,
                (unsigned)MAX_SEGMENT_SEC);
  Serial.printf("  卡剩余 %u MB\n", rec.cardFreeMB());

  // ---- 保真模式：稍后关掉电台 ----
  // 延迟一点，先把 ack 和状态帧发出去，浏览器才不会只看到"连接断开"而不知道发生了什么
  if (recWifiOff_ && net.enabled()) {
    wifiOffPending_ = true;
    wifiOffAtMs_    = millis() + REC_WIFI_OFF_DELAY_MS;
    Serial.println(F("（保真模式：录制期间将关闭 WiFi，录完自动恢复）"));
  }
}

static void stopRecording() {
  if (!recording) return;
  stopPending = true;
  Serial.println(F("停止采集，等待缓冲落盘..."));
}

// ============================================================
//  setup / loop
// ============================================================
void setup() {
  Serial.begin(115200);
  delay(300);
  banner();

  pinMode(PIN_LED, OUTPUT);
  ledOff();

  // ---- 采集缓冲（PSRAM） ----
  ring = (int16_t*)heap_caps_malloc((size_t)RING_SAMPLES * 3 * sizeof(int16_t),
                                    MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (!ring) ring = (int16_t*)malloc((size_t)RING_SAMPLES * 3 * sizeof(int16_t));
  if (!ring) Serial.println(F("★ 环形缓冲分配失败，请确认已开 PSRAM"));

  // ---- 显示抽帧流水线 ----
  disp.begin();

  // ---- 传感器 SPI ----
  // 注意：开发板把 IO11/12/13 用作默认 SPI，这里显式指定引脚
  SPI.begin(PIN_SENS_SCK, PIN_SENS_MISO, PIN_SENS_MOSI, PIN_SENS_CS);
  sensorOk = sensor.begin(&SPI, PIN_SENS_CS, SENS_SPI_HZ);
  sensorWhoAmI = sensor.whoAmI();
  if (sensorOk) {
    if (!sensor.configure()) {
      sensorOk = false;
      Serial.printf("传感器寄存器配置回读失败（错误码 0x%02X）\n", sensor.lastError());
    }
  }

  // ---- INA226 I2C ----
  Wire.begin(PIN_INA_SDA, PIN_INA_SCL, I2C_HZ);
  inaOk = powerMon.begin(&Wire, INA_ADDR, SHUNT_OHM, INA_MAX_CURRENT);

  // ---- TF 卡 ----
  sdOk = rec.begin(SD_FREQ_KHZ);

  // ---- 电池 / 无线充采样 ----
  power.begin();

  // ---- 写卡任务钉在 core 0 ----
  xTaskCreatePinnedToCore(writerTask, "writer", 8192, nullptr, 1, nullptr, 0);

  // ---- 网络任务（UI 服务）钉在 core 0 ----
  //  与 writerTask 同优先级：两者都是 I/O 型，靠 taskYIELD/vTaskDelay 让出
  net.setStatusFn(buildStatusJson);
  net.setCommandFn(handleUiCommand);
  net.setAssetFn(assetFromSd);
  net.setBusyFn([]() -> bool { return recording || stopPending || sdTestBusy; });
  const bool netOk = net.begin(&disp);
  if (netOk) {
    xTaskCreatePinnedToCore(netTask, "netui", 8192, nullptr, 1, nullptr, 0);
  } else {
    Serial.println(F("★ WiFi 启动失败：UI 不可用，但串口与采集功能完全正常"));
  }

  selfReport();
  help();
  ledOn();
}

void loop() {
  // ---- 命令解析 ----
  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line.length() == 0) { help(); }
    else {
      char c = line.charAt(0);
      switch (c) {
        case 'i': selfReport(); break;
        case 'w': cmdScanI2C(); break;
        case 'v': cmdVibRead(); break;
        case 'j': cmdSensorRegisters(); break;
        case 'u': cmdInaRegisters(); break;
        case 'k': cmdCalibCheck(); break;
        case 'c': cmdCurRead(); break;
        case 'r': cmdSdProbe(); break;
        case 'p':
          if (line == "p soak" || line.startsWith("p soak ")) {
            if (recording || stopPending || ringCount() > 0) {
              Serial.println("[sdsoak] FAIL: wait for recording to stop and drain");
            } else {
              const long seconds = line == "p soak" ? 600 : line.substring(7).toInt();
              if (seconds < 1 || seconds > 600)
                Serial.println("[sdsoak] FAIL: duration must be 1..600 seconds");
              else {
                sdTestBusy = true;
                rec.selfTestSustained((uint32_t)seconds);
                sdTestBusy = false;
              }
            }
          } else cmdThroughput(line == "p direct");
          break;
        case 'l': cmdList(); break;
        case 's': startRecording(line.length() > 2 ? line.substring(2).toInt() : 0); break;
        case 'x': stopRecording(); break;
        case 'n': {
          const bool on = !net.enabled();
          net.setEnabled(on);
          Serial.printf("WiFi 已%s\n", on ? "开启" : "关闭（去掉射频干扰，UI 暂时不可用）");
          if (on) Serial.printf("  热点 %s  密码 %s  http://%s/\n",
                                net.ssid(), NET_PASS, net.ip().toString().c_str());
          break;
        }
        case 'h': help(); break;
        case 'm': {
          recWifiOff_ = !recWifiOff_;
          Serial.printf("录制时关 WiFi：%s\n", recWifiOff_ ? "开（保真优先）" : "关（保持实时推流）");
          if (recWifiOff_) {
            Serial.println(F("  效果：录制期间电台断电 —— 去掉射频干扰与 TX 355mA 峰值电流"));
            Serial.println(F("  代价：录制期间 UI 断线，录完自动恢复；数据可事后从网页下载"));
          }
          break;
        }
        default: help(); break;
      }
    }
  }

  // ---- 采集核心路径：读样本 → 入环（SD）+ 入抽帧流水线（UI） ----
  //  注意：即使没在采集，也照样读传感器 —— 这样 UI 一打开就能看到实时波形，
  //        只是这段时间不写 TF 卡（避免随手看一眼就在卡上堆 14MB 垃圾）。
  if (sensorOk) {
    if (sensor.dataReady()) {
      int16_t x, y, z;
      sensor.readRaw(x, y, z);

      disp.push(x, y, z);        // ★ 显示链路：去直流 + 包络抽帧

      if (recording) {           // ★ 存储链路：原始值原样落盘
        ringPush(x, y, z);
        capturedSamples++;
      }
    }

    if (recording) {
      // ---- 到点自动停 ----
      uint32_t elapsed = millis() - recordStartMs;
      if (elapsed >= recordTargetSec * 1000UL) stopRecording();

      // ---- 分段落盘：每 MAX_SEGMENT_SEC 换一个文件 ----
      //  目的：掉电最多丢当前这一段（默认 10 秒 = 1.6 MB），而不是整场 90 秒。
      //  只有上一段已开好（!segPending）才提新请求，否则会把请求吞掉。
      if (!segPending && !stopPending &&
          (millis() - segStartMs) >= (uint32_t)MAX_SEGMENT_SEC * 1000UL) {
        segPending = true;
        segStartMs = millis();
      }
    }
  }

  // ---- 显示流水线的例行工作（发布 2Hz 统计、处理清零请求） ----
  disp.service();

  // ---- 保真录制模式：电台的关与恢复 ----
  //  关：延迟到点再关，让 ack/状态帧先出去
  if (wifiOffPending_) {
    if (!recording) {
      wifiOffPending_ = false;              // 还没关就停了采集 → 撤销
    } else if ((int32_t)(millis() - wifiOffAtMs_) >= 0) {
      wifiOffPending_ = false;
      if (net.enabled()) {
        wifiOffByRec_ = true;
        net.setEnabled(false);
        Serial.println(F("[rec] 已关闭 WiFi（保真录制模式，录完自动恢复）"));
      }
    }
  }
  //  恢复：不管是到点自动停还是手动停，只要 recording 由真变假就走这里
  {
    static bool wasRecording = false;
    if (wasRecording && !recording && wifiOffByRec_) {
      wifiOffByRec_ = false;
      net.setEnabled(true);
      Serial.printf("[rec] 录制结束，WiFi 已恢复：热点 %s  http://%s/\n",
                    net.ssid(), net.ip().toString().c_str());
    }
    wasRecording = recording;
  }

  // ---- 每秒打印一次进度 ----
  static uint32_t lastTick = 0;
  if (recording && millis() - lastTick >= 1000) {
    lastTick = millis();
    uint32_t sec = (millis() - recordStartMs) / 1000;
    Serial.printf("[%4us/%us] %s  采集 %llu 样本，缓冲 %lu，缓冲丢 %lu，写失败 %lu\n",
                  sec, (unsigned)recordTargetSec, rec.currentFileName(),
                  (unsigned long long)capturedSamples,
                  (unsigned long)ringCount(),
                  (unsigned long)dropCount,
                  (unsigned long)rec.writeFails());
  }

  // ---- 采集时 LED 常亮，空闲时慢闪 ----
  if (!recording) {
    static uint32_t lastBlink = 0;
    if (millis() - lastBlink >= 1000) {
      lastBlink = millis();
      digitalWrite(PIN_LED, !digitalRead(PIN_LED));
    }
  } else {
    ledOn();
  }
}
