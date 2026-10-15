#include "power.h"
#include "config.h"

// ============================================================
//  单节锂电 开路电压(OCV) → 剩余电量(SOC) 对照表
//
//  来源：单节 Li-ion（4.2V 满充 / 3.0V 截止）典型放电曲线。
//  ⚠ 这是"典型值"不是"你这颗电池的实测值"。不同厂家、不同循环次数会差很多。
//    真要精确，做一次恒流放电标定（0.2C 放电，每 10 分钟记一个 OCV 点）。
//  平台区（3.72~3.87V）覆盖了约 30% 容量 —— 这正是电压法最不准的地方。
// ============================================================
struct SocPoint { float v; uint8_t pct; };
static const SocPoint kSocTable[] = {
  {4.20f, 100}, {4.10f, 91}, {4.00f, 83}, {3.93f, 75}, {3.87f, 66},
  {3.82f, 57},  {3.79f, 47}, {3.77f, 38}, {3.75f, 29}, {3.72f, 20},
  {3.68f, 11},  {3.55f,  5}, {3.30f,  0},
};
static const int kSocN = sizeof(kSocTable) / sizeof(kSocTable[0]);

uint8_t socFromVoltage(float v) {
  if (v >= kSocTable[0].v)            return 100;
  if (v <= kSocTable[kSocN - 1].v)    return 0;
  for (int i = 0; i + 1 < kSocN; i++) {
    const SocPoint& hi = kSocTable[i];
    const SocPoint& lo = kSocTable[i + 1];
    if (v <= hi.v && v >= lo.v) {
      float t = (v - lo.v) / (hi.v - lo.v);          // 0=lo 端, 1=hi 端
      float p = lo.pct + t * (float)(hi.pct - lo.pct);
      return (uint8_t)(p + 0.5f);
    }
  }
  return 0;
}

// ============================================================
void PowerMon::begin() {
  pinMode(PIN_VBAT, INPUT);
  pinMode(PIN_QIDET, INPUT);
  analogSetPinAttenuation(PIN_VBAT, ADC_ATTEN);
  analogSetPinAttenuation(PIN_QIDET, ADC_ATTEN);

  // 上电先读一轮，让电量一开界面就有数（否则头 500ms 显示 0）
  readChannel(PIN_VBAT, ADC_TRIM_VBAT);
  delay(2);
  vbat_ = readChannel(PIN_VBAT, ADC_TRIM_VBAT);
  if (vbat_ > VBAT_EMPTY * 0.5f) {
    soc_ = socFromVoltage(vbat_);
    socInit_ = true;
  }
  update();
}

float PowerMon::readChannel(uint8_t pin, float trim) {
  // analogReadMilliVolts() 用 eFuse 里的出厂校准值换算，比裸 analogRead() 线性好得多
  uint32_t sum = 0;
  for (int i = 0; i < ADC_AVG_N; i++) sum += analogReadMilliVolts(pin);
  float mv = (float)sum / ADC_AVG_N;
  return mv / 1000.0f * ADC_DIV_RATIO * trim;
}

void PowerMon::update() {
  const uint32_t now = millis();

  // 记录原始码值（排障：如果电压读数明显不对，先看这两个值是否在动）
  rawVbat_ = (uint16_t)analogReadMilliVolts(PIN_VBAT);
  rawQi_   = (uint16_t)analogReadMilliVolts(PIN_QIDET);

  vbat_ = readChannel(PIN_VBAT, ADC_TRIM_VBAT);
  qiV_  = readChannel(PIN_QIDET, ADC_TRIM_QI);

  // ---------- 无线充在场判定（带迟滞，防止临界抖动） ----------
  if (!qi_ && qiV_ > CHG_DET_V)  qi_ = true;
  if ( qi_ && qiV_ < CHG_LOST_V) qi_ = false;

  // ---------- 供电来源 ----------
  // 有 Qi → 无线；否则看 5V 轨（INA226 给的 busV_，没接就退回用 Qi 通道判断）
  if (qi_) {
    chg_ = CHG_WIRELESS;
  } else if (busV_ > USB_DET_V) {
    chg_ = CHG_USB;
  } else {
    chg_ = CHG_NONE;
  }

  // ---------- 电量 ----------
  // 只在"轻载 + 有电池电压"时才更新。重载时内阻压降会把 OCV 拉低，
  // 这时候更新 SOC 等于自己骗自己（大电流一开就掉一格）。
  const bool lightLoad = fabsf(loadMA_) < 300.0f;
  if (vbat_ > VBAT_EMPTY * 0.5f && lightLoad) {
    uint8_t tgt = socFromVoltage(vbat_);
    if (!socInit_) { soc_ = tgt; socInit_ = true; }
    else {
      // 缓变：一次最多动 1%，避免读数噪声让数字乱跳
      if (tgt > soc_)      { if (soc_ + 1 <= tgt) soc_ = (uint8_t)(soc_ + 1); else soc_ = tgt; }
      else if (tgt < soc_) { if (soc_ - 1 >= tgt) soc_ = (uint8_t)(soc_ - 1); else soc_ = tgt; }
    }
    socTarget_ = tgt;
  }

  // ---------- 趋势：60 秒窗口看电压在升还是降 ----------
  if (lastTrendMs_ == 0) {
    lastTrendMs_ = now;
    trendRefV_ = vbat_;
  } else if (now - lastTrendMs_ >= 60000UL) {
    float d = vbat_ - trendRefV_;
    // 单节锂电 60s 内变化 5mV 以上算有明显趋势（充电时典型 5~20mV/min）
    trend_ = (d > 0.005f) ? 1 : ((d < -0.005f) ? -1 : 0);
    trendRefV_  = vbat_;
    lastTrendMs_ = now;
  }

  lastUpdateMs_ = now;
}

const char* PowerMon::chgText() const {
  switch (chg_) {
    case CHG_WIRELESS: return qi_ ? "无线供电中" : "无线充已断开";
    case CHG_USB:      return "USB 供电";
    default:           return "未接外部电源";
  }
}
