#pragma once
#include <Arduino.h>
#include <FS.h>
#include <SD_MMC.h>

// ============================================================
//  TF 卡记录器（SDMMC 4-bit，沿用原设计的六脚）
//
//  文件格式（小端）：
//    偏移 0   4B  magic "VIBB"
//    偏移 4   2B  version = 1
//    偏移 6   4B  sampleRateHz
//    偏移 10  4B  startMillis（设备上电以来的 ms）
//    偏移 14  2B  bytesPerSample（6 = 三轴 int16）
//    偏移 16 16B  保留区 ★ 段结束时回填，见 RecTail
//    偏移 32 ...  原始样本：X_L X_H Y_L Y_H Z_L Z_H（小端 int16 交错）
//
//  ★ 保留区回填（2026-09-18 新增）
//    段文件关闭前，把"这段数据可不可信"写回文件头。这样事后分析
//    不必依赖串口日志就能判断某段有没有丢过样本。
//    版本号仍为 1 —— 头长度没变，旧工具按固定偏移 32 读样本不受影响。
//
//  设计要点：
//   · 批量写（BUF_SAMPLES 一次），绝不逐样本写卡。
//   · **分段**：每 MAX_SEGMENT_SEC 换一个文件，掉电最多丢当前这一段。
//   · **编号不重头**：上电扫描卡上已有的 vib_*.bin，从最大编号 +1 继续。
//     （此前 fileIndex_ 初值 0，重启一次就会覆盖掉上一段实验数据 —— 已修）
//
//  吞吐账：26.667kHz × 6B ≈ 160 KB/s —— 容量不是瓶颈，SPI/SDMMC 写速才是。
// ============================================================

struct RecHeader {
  char     magic[4];
  uint16_t version;
  uint32_t sampleRateHz;
  uint32_t startMillis;
  uint16_t bytesPerSample;
  uint8_t  reserved[16];
} __attribute__((packed));

// RecHeader.reserved 的布局（段结束时回填，正好占满 16 字节）
struct RecTail {
  uint32_t dropSamples;     // 本段录制期间，环形缓冲满而丢掉的样本数
  uint32_t writtenSamples;  // 本段实际落盘的样本数
  uint32_t writeFails;      // 本段写文件失败的次数（卡满 / 掉卡 / 坏块）
  uint32_t lostByWrite;     // 本段因写失败而丢掉的样本数
} __attribute__((packed));

class Recorder {
 public:
  // freqKhz：本版IDF使用标准20000（20MHz）档，10000会停在400kHz
  bool begin(uint32_t freqKhz, bool mode1bit = false);
  static void printPinDiagnostics(const char* stage);
  uint32_t frequencyKhz() const { return freqKhz_; }
  bool mode1bit() const { return mode1bit_; }

  bool     mounted() const { return mounted_; }
  uint32_t cardSizeMB();
  uint32_t cardFreeMB();
  uint8_t  cardType();

  bool startSegment();                                  // 开新段文件
  size_t writeSamples(const int16_t* xyz, size_t n);    // n = 样本数
  bool endSegment();                                    // 回填头 + 落盘 + 关闭

  // 关闭当前段之前调用：把该段录制期间"环形缓冲丢掉的样本数"交给本类回填
  void setClosingDrop(uint32_t n) { closeDrop_ = n; }

  const char* currentFileName() const { return curName_; }
  uint64_t totalSamples() const { return totalSamples_; }
  uint32_t nextIndex()   const { return fileIndex_; }   // 下一个要用的编号 = 已有段数
  uint32_t writeFails()  const { return writeFails_; }  // 累计写失败次数
  uint32_t lostSamples() const { return lostSamples_; } // 累计因写失败丢的样本

  // 写入并完整读回自测；direct用于POSIX对照。返回写速（样本/秒），0表示失败
  double selfTestThroughput(size_t nSamples, bool direct = false);

  // 按采样数据率持续模拟写卡并完整读回；1..600秒，串口x取消。非真实传感器验收。
  bool selfTestSustained(uint32_t seconds);

 private:
  void scanNextIndex();   // 上电扫描已有 vib_*.bin，避免编号重头导致覆盖

  bool     mounted_ = false;
  uint32_t freqKhz_ = 20000;
  bool mode1bit_ = false;
  File     file_;
  bool     segOpen_ = false;
  char     curName_[64] = {0};
  uint64_t totalSamples_ = 0;
  uint32_t fileIndex_ = 0;

  RecHeader hdr_{};          // 开段时写下的头；关段时回填 reserved 后重写
  uint32_t  segSamples_    = 0;
  uint32_t  segWriteFails_ = 0;
  uint32_t  segLost_       = 0;
  uint32_t  closeDrop_     = 0;

  uint32_t  writeFails_  = 0;
  uint32_t  lostSamples_ = 0;
};
