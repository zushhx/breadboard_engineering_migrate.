#include "disp.h"

// ============================================================
//  DispPipeline 实现
//
//  ★ 性能约束（务必守住）：
//    push() 每个原始样本都会被调一次，样本间隔只有 37.5 µs。
//    这里全部用定长循环 + float 标量运算，**没有分配、没有锁、没有日志**。
//    实测开销约 40~60 个 CPU 周期（240MHz 下 ≈ 0.25 µs），占总预算 0.7%。
// ============================================================

static inline int16_t clampI16(float v) {
  if (v >  32767.0f) return  32767;
  if (v < -32768.0f) return -32768;
  return (int16_t)v;
}

void DispPipeline::begin() {
  // 一阶低通的系数：α = 1/(τ·fs)，τ = 3s，fs = 26667Hz → 1.25e-5
  dcAlpha_ = 1.0f / (DISP_DC_TAU_S * (float)SAMPLE_RATE_HZ);

  cur_.nAxes = DISP_AXES;
  cur_.nPts  = 0;
  curStartMs_ = millis();
  lastPubMs_  = millis();

  stats_.rateHz = (uint16_t)(SAMPLE_RATE_HZ / DISP_DECIM);
  for (int i = 0; i < DISP_AXES; i++) {
    wMin_[i] =  100000.0f;
    wMax_[i] = -100000.0f;
  }
}

void DispPipeline::push(int16_t x, int16_t y, int16_t z) {
  const float raw[DISP_AXES] = {(float)x, (float)y, (float)z};

  // ---------- ① 去直流 ----------
  if (!dcInit_) {
    for (int i = 0; i < DISP_AXES; i++) dc_[i] = raw[i];
    dcInit_ = true;
  }
  float ac[DISP_AXES];
  for (int i = 0; i < DISP_AXES; i++) {
    ac[i] = raw[i] - dc_[i];
    dc_[i] += dcAlpha_ * ac[i];      // dc 缓慢跟随 → 相当于高通（截止 ≈0.05 Hz）
    lastRaw_[i] = (int16_t)raw[i];
  }

  // ---------- ② 包络抽帧 ----------
  for (int i = 0; i < DISP_AXES; i++) {
    if (ac[i] < wMin_[i]) wMin_[i] = ac[i];
    if (ac[i] > wMax_[i]) wMax_[i] = ac[i];
  }

  // ---------- ③ 统计量（在**全速原始样本**上算，不是抽帧后的） ----------
  for (int i = 0; i < DISP_AXES; i++) {
    ss_[i] += (double)ac[i] * (double)ac[i];
    float a = fabsf(ac[i]);
    if (a > pkAcc_[i]) pkAcc_[i] = a;
  }
  ssN_++;

  if (++decCnt_ >= DISP_DECIM) {
    decCnt_ = 0;
    emitPoint();
  }
}

void DispPipeline::emitPoint() {
  if (cur_.nPts == 0) curStartMs_ = millis();

  const uint16_t p = cur_.nPts;
  for (int i = 0; i < DISP_AXES; i++) {
    cur_.mn[i][p] = clampI16(wMin_[i]);
    cur_.mx[i][p] = clampI16(wMax_[i]);
    wMin_[i] =  100000.0f;
    wMax_[i] = -100000.0f;
  }

  if (++cur_.nPts >= DISP_PTS) {
    // ---------- 发布整帧（release 语义：确保数据写完再更新 head） ----------
    cur_.seq = ++seq_;
    cur_.tMs = curStartMs_;
    cur_.nPts = DISP_PTS;
    cur_.nAxes = DISP_AXES;

    const uint32_t head = qHead_.load(std::memory_order_relaxed);
    const uint32_t tail = qTail_.load(std::memory_order_acquire);
    if (((head + 1) & (DISP_QUEUE - 1)) == (tail & (DISP_QUEUE - 1))) {
      // 队列满：consumer（netTask）跟不上。丢这一帧，UI 会看到 seq 跳号。
      frameDrop_.fetch_add(1, std::memory_order_relaxed);
    } else {
      q_[head & (DISP_QUEUE - 1)] = cur_;
      qHead_.store(head + 1, std::memory_order_release);
    }
    cur_.nPts = 0;
  }
}

bool DispPipeline::pop(DispFrame& out) {
  const uint32_t tail = qTail_.load(std::memory_order_relaxed);
  if (tail == qHead_.load(std::memory_order_acquire)) return false;
  out = q_[tail & (DISP_QUEUE - 1)];
  qTail_.store(tail + 1, std::memory_order_release);
  return true;
}

uint32_t DispPipeline::queueDepth() const {
  return (qHead_.load(std::memory_order_acquire) - qTail_.load(std::memory_order_acquire))
         & 0xFFFFFFFFu;
}

void DispPipeline::service() {
  const uint32_t now = millis();

  // 清零请求：把当前原始值直接当作直流基准（比 3 秒慢跟随干脆）
  if (zeroReq_) {
    zeroReq_ = false;
    for (int i = 0; i < DISP_AXES; i++) dc_[i] = (float)lastRaw_[i];
  }

  if (now - lastPubMs_ >= STATUS_PERIOD_MS) {
    lastPubMs_ = now;
    publishStats();
  }
}

void DispPipeline::publishStats() {
  const float mgPerLsb = SENS_MG_PER_LSB;

  if (ssN_ > 0) {
    for (int i = 0; i < DISP_AXES; i++) {
      const float intervalRms  = sqrtf((float)(ss_[i] / (double)ssN_)) * mgPerLsb;
      const float intervalPeak = pkAcc_[i] * mgPerLsb;

      // RMS：轻度平滑，读数不跳
      rmsSm_[i] = (rmsSm_[i] == 0.0f) ? intervalRms
                                      : (0.5f * rmsSm_[i] + 0.5f * intervalRms);
      // 峰值保持 + 0.75 衰减：最近的冲击能看见，约 2 秒后消失
      const float decayed = pkSm_[i] * 0.75f;
      pkSm_[i] = (intervalPeak > decayed) ? intervalPeak : decayed;

      stats_.rmsMg[i] = rmsSm_[i];
      stats_.pkMg[i]  = pkSm_[i];
    }
  }
  for (int i = 0; i < DISP_AXES; i++) stats_.dcMg[i] = dc_[i] * mgPerLsb;
  stats_.lastSeq      = seq_;
  stats_.rateHz       = (uint16_t)(SAMPLE_RATE_HZ / DISP_DECIM);

  // 复位区间累加器
  for (int i = 0; i < DISP_AXES; i++) { ss_[i] = 0.0; pkAcc_[i] = 0.0f; }
  ssN_ = 0;
}
