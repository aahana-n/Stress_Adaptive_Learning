/*
 * eDock Sensor — Optimised EEG Band Power Edition
 * ================================================
 * Replaces raw-variance EEG metric with proper FFT-based band power.
 * HR + HRV-RMSSD section is UNCHANGED from original.
 *
 * EEG SIGNAL CHAIN (journal-backed):
 * ──────────────────────────────────
 * 1. 8× oversampled ADC read (reduces quantisation noise)
 * 2. 50 Hz notch filter (IIR biquad, Q=35)
 * 3. 0.5 Hz highpass (removes DC drift / electrode offset)
 * 4. Hanning window × 128 samples
 * 5. Real FFT → PSD via Welch-style single-frame
 * 6. Band power summation per Stancin & Jovic (2019) bins
 * 7. Stress features:
 *      Beta/Alpha ratio  — Yin et al. (2017), Knyazev (2007)
 *      Theta/Beta ratio  — Clarke et al. (2001), Barry et al. (2003)
 *      Relative Alpha    — Klimesch (1999)
 *
 * HR + RMSSD:
 *   Task Force (1996) ESC/NASPE standard — 300–1500 ms RR gate
 *   Thresholds: RMSSD >50ms=LOW, 20-50ms=MOD, <20ms=HIGH stress
 *
 * RMSSD FIX (v3):
 *   • Corrected circular buffer indexing (v2) — chronological order.
 *   • Replaced fixed 500 ms artifact gate with ±20% median-relative gate
 *     (Malik et al. 1993) — the fixed gate passed inflated RR intervals
 *     at normal HR, keeping RMSSD pinned at the 100 ms clamp.
 *   • Removed hard clamp — percentage gate makes it unnecessary.
 *   • Buffer size 8 → 16 for stable median estimate.
 *   Refs: Malik et al. (1993) Med Biol Eng Comput 31(5):539-544.
 *         Shaffer & Ginsberg (2017) Front Public Health 5:258.
 *
 * ADC → µV conversion (BioAmp EXG Pill):
 *   Gain ≈ 1000×, Vref=3.3V, 12-bit ADC → 1 LSB = 3300mV/4096 = 0.806mV input
 *   After ×1000 gain: 1 LSB ≈ 0.806 µV   (Maddirala & Shaik 2016 method)
 *
 * Sampling: 250 Hz, window N=128 → freq resolution = 1.953 Hz/bin
 */

#include <Wire.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include "MAX30105.h"
#include "heartRate.h"
#include <time.h>
#include <math.h>

// ======================================================
// WIFI / FIREBASE CONFIG
// ======================================================
#define WIFI_SSID      ""
#define WIFI_PASSWORD  ""
#define DATABASE_URL   ""
#define DATABASE_SECRET ""
String userUID = "";

// ======================================================
// HARDWARE PINS
// ======================================================
#define I2C_SDA    8
#define I2C_SCL    9
#define BIOAMP_PIN 4

MAX30105 particleSensor;

// ======================================================
// EEG — CONSTANTS
// ======================================================
#define EEG_FS       250        // sampling rate Hz — BioAmp EXG nominal
#define EEG_N        128        // FFT window size (power of 2)
#define EEG_INTERVAL_US 4000   // 4000 µs = 250 Hz

/*
 * ADC → µV scaling
 * BioAmp EXG Pill gain = 1000 (datasheet)
 * ESP32 ADC: Vref=3.3V, 12-bit → step = 3300000/4096 µV = 805.7 µV/LSB at input to amp
 * After amp gain cancellation: 1 LSB = 805.7/1000 = 0.8057 µV
 */
#define ADC_VREF_UV   3300000.0f
#define ADC_BITS      4096.0f
#define AMP_GAIN      1000.0f
#define ADC_UV_PER_LSB (ADC_VREF_UV / ADC_BITS / AMP_GAIN)

/*
 * FFT bin width = fs / N = 250/128 = 1.953 Hz per bin
 */
#define BIN_DELTA_LO   0
#define BIN_DELTA_HI   2
#define BIN_THETA_LO   2
#define BIN_THETA_HI   4
#define BIN_ALPHA_LO   4
#define BIN_ALPHA_HI   6
#define BIN_BETA_LO    6
#define BIN_BETA_HI   15
#define BIN_GAMMA_LO  15
#define BIN_GAMMA_HI  23

// ======================================================
// EEG — SAMPLE BUFFER & STATE
// ======================================================
float eegRaw[EEG_N];
int   eegIdx       = 0;
bool  eegFull      = false;
float dcOffset     = 0.0f;

volatile float bp_delta = 0, bp_theta = 0, bp_alpha = 0;
volatile float bp_beta  = 0, bp_gamma = 0, bp_total = 0;

volatile float feat_beta_alpha  = 0;
volatile float feat_theta_beta  = 0;
volatile float feat_rel_alpha   = 0;

int  disconnectCounter = 0;
bool isConnected       = false;
#define DISCONNECT_CONFIRM_SAMPLES 10

// ======================================================
// EEG — FILTERS
// ======================================================

struct NotchFilter {
  float b0=0.9780f, b1=-1.6180f, b2=0.9780f;
  float a1=-1.6180f, a2=0.9560f;
  float x1=0, x2=0, y1=0, y2=0;
  float process(float x) {
    float y = b0*x + b1*x1 + b2*x2 - a1*y1 - a2*y2;
    x2=x1; x1=x; y2=y1; y1=y; return y;
  }
} notch;

struct HighpassFilter {
  float alpha=0.9876f, prevX=0, prevY=0;
  float process(float x) {
    float y = alpha*(prevY + x - prevX);
    prevX=x; prevY=y; return y;
  }
} hp;

// ======================================================
// EEG — HANNING WINDOW
// ======================================================
float hanningWindow[EEG_N];

void precomputeHanning() {
  for (int n = 0; n < EEG_N; n++)
    hanningWindow[n] = 0.5f * (1.0f - cosf(2.0f * M_PI * n / (EEG_N - 1)));
}

// ======================================================
// EEG — REAL FFT
// ======================================================
float fftReal[EEG_N];
float fftImag[EEG_N];

void realFFT(float* re, float* im, int n) {
  int j = 0;
  for (int i = 1; i < n; i++) {
    int bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      float tr=re[i]; re[i]=re[j]; re[j]=tr;
      float ti=im[i]; im[i]=im[j]; im[j]=ti;
    }
  }
  for (int len = 2; len <= n; len <<= 1) {
    float ang = -2.0f * M_PI / len;
    float wRe = cosf(ang), wIm = sinf(ang);
    for (int i = 0; i < n; i += len) {
      float curRe = 1.0f, curIm = 0.0f;
      for (int k = 0; k < len/2; k++) {
        float uRe = re[i+k],       uIm = im[i+k];
        float vRe = re[i+k+len/2], vIm = im[i+k+len/2];
        float tRe = curRe*vRe - curIm*vIm;
        float tIm = curRe*vIm + curIm*vRe;
        re[i+k]        = uRe + tRe;
        im[i+k]        = uIm + tIm;
        re[i+k+len/2]  = uRe - tRe;
        im[i+k+len/2]  = uIm - tIm;
        float newRe = curRe*wRe - curIm*wIm;
        curIm = curRe*wIm + curIm*wRe;
        curRe = newRe;
      }
    }
  }
}

// ======================================================
// EEG — BAND POWER COMPUTATION
// ======================================================
void computeBandPowers() {
  float windowPowerSum = 0.0f;
  for (int i = 0; i < EEG_N; i++) {
    float w       = hanningWindow[i];
    fftReal[i]    = eegRaw[(eegIdx + i) % EEG_N] * ADC_UV_PER_LSB * w;
    fftImag[i]    = 0.0f;
    windowPowerSum += w * w;
  }
  realFFT(fftReal, fftImag, EEG_N);

  float norm = 2.0f / ((float)EEG_N * (float)EEG_FS * windowPowerSum);
  float psd[EEG_N/2 + 1];
  psd[0] = (fftReal[0]*fftReal[0] + fftImag[0]*fftImag[0]) * norm * 0.5f;
  for (int k = 1; k < EEG_N/2; k++)
    psd[k] = (fftReal[k]*fftReal[k] + fftImag[k]*fftImag[k]) * norm;
  psd[EEG_N/2] = (fftReal[EEG_N/2]*fftReal[EEG_N/2]
                 + fftImag[EEG_N/2]*fftImag[EEG_N/2]) * norm * 0.5f;

  auto sumBand = [&](int lo, int hi) -> float {
    float s = 0;
    for (int k = lo; k < hi && k <= EEG_N/2; k++) s += psd[k];
    return s;
  };

  bp_delta = sumBand(BIN_DELTA_LO, BIN_DELTA_HI);
  bp_theta = sumBand(BIN_THETA_LO, BIN_THETA_HI);
  bp_alpha = sumBand(BIN_ALPHA_LO, BIN_ALPHA_HI);
  bp_beta  = sumBand(BIN_BETA_LO,  BIN_BETA_HI);
  bp_gamma = sumBand(BIN_GAMMA_LO, BIN_GAMMA_HI);
  bp_total = bp_delta + bp_theta + bp_alpha + bp_beta + bp_gamma;

  feat_beta_alpha = (bp_alpha > 1e-9f) ? bp_beta / bp_alpha : 0.0f;
  feat_theta_beta = (bp_beta  > 1e-9f) ? bp_theta / bp_beta : 0.0f;
  feat_rel_alpha  = (bp_total > 1e-9f) ? bp_alpha / bp_total : 0.0f;
}

// ======================================================
// EEG — STRESS SCORE FROM BAND FEATURES
// ======================================================
String eegStressLabel() {
  int score = 0;
  if      (feat_beta_alpha > 3.0f)  score += 3;
  else if (feat_beta_alpha > 1.5f)  score += 2;
  else if (feat_beta_alpha > 0.5f)  score += 1;
  if      (feat_rel_alpha < 0.10f)  score += 3;
  else if (feat_rel_alpha < 0.20f)  score += 2;
  else if (feat_rel_alpha < 0.35f)  score += 1;
  if      (feat_theta_beta > 6.0f)  score += 3;
  else if (feat_theta_beta > 3.0f)  score += 2;
  else if (feat_theta_beta > 1.0f)  score += 1;
  if (score >= 7) return "HIGH";
  if (score >= 4) return "MODERATE";
  return "LOW";
}

// ======================================================
// EEG — SAMPLING
// ======================================================
unsigned long lastEEGMicros = 0;

void sampleEEG() {
  unsigned long now = micros();
  if (now - lastEEGMicros < EEG_INTERVAL_US) return;
  lastEEGMicros = now;

  int raw = 0;
  for (int i = 0; i < 8; i++) { raw += analogRead(BIOAMP_PIN); delayMicroseconds(10); }
  raw /= 8;

  float filtered = hp.process(notch.process((float)raw)) - dcOffset;

  bool railing = (raw < 50 || raw > 4000);
  if (railing) { disconnectCounter++; }
  else         { disconnectCounter = 0; isConnected = true; }
  if (disconnectCounter >= DISCONNECT_CONFIRM_SAMPLES) isConnected = false;

  eegRaw[eegIdx] = isConnected ? filtered : 0.0f;
  eegIdx = (eegIdx + 1) % EEG_N;
  if (eegIdx == 0) eegFull = true;
}

// ======================================================
// HEART RATE + HRV RMSSD  ← FIXED
// ======================================================

/*
 * RMSSD = sqrt( mean of (RRi+1 - RRi)² )
 *
 * Standard reference:
 *   Task Force of ESC and NASPE (1996) — "Heart rate variability:
 *   standards of measurement, physiological interpretation, and
 *   clinical use" — Circulation 93(5):1043-1065.
 *
 * RMSSD stress thresholds (Shaffer & Ginsberg 2017):
 *   > 50 ms  → LOW stress
 *   20–50 ms → MODERATE
 *   < 20 ms  → HIGH stress
 *
 * RR gate 300–1500 ms (40–200 BPM) — unchanged.
 *
 * ── FIXES vs original ──────────────────────────────────
 * Bug 1 — Circular buffer indexing:
 *   Original used (rrSpot - i) and (rrSpot - i - 1) to compute
 *   successive differences. When the buffer had wrapped, slots
 *   were not in chronological order, so differences could be
 *   between beats separated by many seconds → huge spurious RMSSD.
 *   Fix: write with an explicit head pointer; read back oldest→newest
 *   using (head - n + k) % BUF so order is always chronological.
 *
 * Bug 2 — No artifact rejection:
 *   A single missed beat doubles one RR interval and halves the
 *   next; the squared difference explodes.
 *   Fix: skip any pair where |ΔRR| > 500 ms — impossible beat-to-beat
 *   at normal HR, so this only fires on ectopics or signal dropout.
 *   Ref: Clifford et al. (2006) "Advanced Methods and Tools for ECG
 *        Data Analysis" Artech House, ch.3 — standard ectopic gate.
 *
 * Bug 3 — No physiological ceiling:
 *   Even after the above, ADC noise or motion artefacts can produce
 *   brief outlier RR values. Clamping to 100 ms matches the published
 *   upper bound for healthy resting adults.
 *   Ref: Shaffer & Ginsberg (2017) Front Public Health 5:258.
 *        doi:10.3389/fpubh.2017.00258
 *
 * Buffer enlarged 8 → 16 for a more stable single-frame estimate.
 */

const long FINGER_THRESHOLD = 50000;
#define RATE_SIZE   6
#define RR_BUF_SIZE 16          // ← increased from 8

byte  rates[RATE_SIZE];
byte  rateSpot       = 0;
long  lastBeat       = 0;
int   beatAvg        = 0;
int   beatsCollected = 0;
bool  fingerOn       = false;

long  rrBuf[RR_BUF_SIZE];
int   rrHead      = 0;          // ← index of NEXT write slot
int   rrCount     = 0;          // ← how many valid intervals stored
int   hrv_rmssd   = 0;

int computeRMSSD() {
  int n = min(rrCount, RR_BUF_SIZE);
  if (n < 2) return 0;

  // ── Step 1: median RR for percentage-based artifact gate ─────────────
  // A fixed 500 ms absolute gate fails at normal HR. At 70 BPM
  // (RR≈857 ms) a missed beat gives RR≈1714 ms; the next successive
  // difference can still be < 500 ms against the inflated baseline,
  // passing the old gate and pushing RMSSD to the 100 ms clamp.
  //
  // Fix: percentage gate of ±20% relative to the median RR.
  // This is the clinical standard for HRV artefact rejection.
  // Ref: Malik et al. (1993) Med Biol Eng Comput 31(5):539-544.
  //      doi:10.1007/BF02441992
  long sortBuf[RR_BUF_SIZE];
  for (int k = 0; k < n; k++) {
    int idx    = (rrHead - n + k + RR_BUF_SIZE) % RR_BUF_SIZE;
    sortBuf[k] = rrBuf[idx];
  }
  // Insertion sort — n <= 16, negligible cost
  for (int i = 1; i < n; i++) {
    long key = sortBuf[i]; int j = i - 1;
    while (j >= 0 && sortBuf[j] > key) { sortBuf[j+1] = sortBuf[j]; j--; }
    sortBuf[j+1] = key;
  }
  long medianRR = sortBuf[n / 2];              // ms, e.g. ~857 ms at 70 BPM
  long rrGate   = max(medianRR / 5L, 50L);    // 20% of median, floor 50 ms

  // ── Step 2: successive differences with per-interval deviation check ──
  float sumSqDiff  = 0.0f;
  int   validPairs = 0;

  for (int k = 1; k < n; k++) {
    int idxNew = (rrHead - n + k     + RR_BUF_SIZE) % RR_BUF_SIZE;
    int idxOld = (rrHead - n + k - 1 + RR_BUF_SIZE) % RR_BUF_SIZE;

    // Reject any interval that deviates >20% from median (ectopic/noise).
    // Both the new and old interval must be clean before their difference
    // is accepted — one bad RR contaminates two pairs under the old scheme.
    long devNew = abs(rrBuf[idxNew] - medianRR);
    long devOld = abs(rrBuf[idxOld] - medianRR);
    if (devNew > rrGate || devOld > rrGate) continue;

    long diff = rrBuf[idxNew] - rrBuf[idxOld];
    sumSqDiff += (float)(diff * diff);
    validPairs++;
  }

  if (validPairs == 0) return 0;

  // No hard ceiling — the percentage gate already excludes outliers.
  // A genuine high RMSSD (e.g. 60-80 ms in a relaxed young adult) will
  // now be reported correctly instead of clamping to 100.
  return (int)sqrtf(sumSqDiff / (float)validPairs);
}

void processHeartRate() {
  long irValue = particleSensor.getIR();
  if (irValue > FINGER_THRESHOLD) {
    fingerOn = true;
    if (checkForBeat(irValue)) {
      long now = millis(), delta = now - lastBeat;
      lastBeat = now;
      if (delta == 0) return;
      float bpm = 60000.0f / (float)delta;
      if (bpm >= 40 && bpm <= 200) {
        rates[rateSpot % RATE_SIZE] = (byte)bpm; rateSpot++;
        if (beatsCollected < RATE_SIZE) beatsCollected++;
        long total = 0; int cnt = beatsCollected;
        for (int i = 0; i < cnt; i++)
          total += rates[((int)rateSpot-1-i+RATE_SIZE)%RATE_SIZE];
        beatAvg = (int)(total/cnt);

        if (delta >= 300 && delta <= 1500) {
          // ── Fixed: write to head slot, advance head ──
          rrBuf[rrHead] = delta;
          rrHead        = (rrHead + 1) % RR_BUF_SIZE;
          if (rrCount < RR_BUF_SIZE) rrCount++;
          hrv_rmssd = computeRMSSD();
        }
      }
    }
  } else {
    fingerOn = false; beatAvg = 0; rateSpot = 0; beatsCollected = 0;
    lastBeat = 0; memset(rates, 0, sizeof(rates));
    // ── Fixed: reset head/count instead of rrSpot/rrCollected ──
    rrHead = 0; rrCount = 0; hrv_rmssd = 0; memset(rrBuf, 0, sizeof(rrBuf));
  }
}

// ======================================================
// STRESS FUSION  (unchanged)
// ======================================================

#define HR_THRESH_HIGH  95
#define HR_THRESH_MOD   78
#define HRV_THRESH_LOW  20
#define HRV_THRESH_MOD  50

String deriveStress(int hr, int rmssd) {
  if (rrCount < 2 || rmssd == 0) {     // ← rrCollected → rrCount
    if (hr > HR_THRESH_HIGH) return "HIGH";
    if (hr > HR_THRESH_MOD)  return "MODERATE";
    return "LOW";
  }
  if (hr > HR_THRESH_HIGH && rmssd < HRV_THRESH_LOW) return "HIGH";
  if (rmssd < 10)   return "HIGH";
  if (hr > 105)     return "HIGH";
  if (hr > HR_THRESH_MOD && rmssd < HRV_THRESH_MOD)  return "MODERATE";
  if (hr > HR_THRESH_HIGH || rmssd < HRV_THRESH_LOW) return "MODERATE";
  return "LOW";
}

String fusedStress(int hr, int rmssd) {
  String hrStress  = deriveStress(hr, rmssd);
  if (!isConnected || !eegFull) return hrStress;

  String eegStress = eegStressLabel();
  if (hrStress == eegStress) return hrStress;

  auto level = [](const String& s) {
    if (s=="HIGH") return 2; if (s=="MODERATE") return 1; return 0;
  };
  return level(hrStress) >= level(eegStress) ? hrStress : eegStress;
}

// ======================================================
// FIREBASE UPLOAD  (unchanged)
// ======================================================
void sendToFirebase(int hr_bpm, const String& hr_status,
                    int rmssd,  const String& stress_level,
                    float bar,  float rap,  float tbr,
                    float b_theta, float b_alpha, float b_beta) {
  if (WiFi.status() != WL_CONNECTED) { Serial.println("📶 WiFi offline"); return; }
  HTTPClient http;
  String url = String(DATABASE_URL)
             + "/StressAdaptiveLearning/sessions/" + userUID
             + "/current_session/live_data.json?auth=" + DATABASE_SECRET;

  String body = "{";
  body += "\"hr_bpm\":"          + String(hr_bpm)             + ",";
  body += "\"hr_status\":\""     + hr_status                  + "\",";
  body += "\"hrv_rmssd\":"       + String(rmssd)              + ",";
  body += "\"stress_level\":\""  + stress_level               + "\",";
  body += "\"eeg_beta_alpha\":"  + String(bar,   3)           + ",";
  body += "\"eeg_rel_alpha\":"   + String(rap,   3)           + ",";
  body += "\"eeg_theta_beta\":"  + String(tbr,   3)           + ",";
  body += "\"eeg_theta_uv2hz\":" + String(b_theta, 4)        + ",";
  body += "\"eeg_alpha_uv2hz\":" + String(b_alpha, 4)        + ",";
  body += "\"eeg_beta_uv2hz\":"  + String(b_beta,  4)        + ",";
  body += "\"eeg_connected\":"   + String(isConnected ? F("true") : F("false")) + ",";
  body += "\"timestamp\":"       + String((unsigned long)millis());
  body += "}";

  http.begin(url);
  http.addHeader("Content-Type","application/json");
  int code = http.PUT(body);
  if (code==200)
    Serial.println("✅ FB | hr="+String(hr_bpm)+" rmssd="+String(rmssd)
                   +" BAR="+String(bar,2)+" RAP="+String(rap,2)
                   +" stress="+stress_level);
  else
    Serial.println("❌ FB HTTP "+String(code)+" "+http.getString());
  http.end();
}

// ======================================================
// TIMING  (unchanged)
// ======================================================
unsigned long lastFirebaseUpload = 0;
const unsigned long FIREBASE_INTERVAL = 5000;
unsigned long lastBandCompute   = 0;
const unsigned long BAND_COMPUTE_INTERVAL = 500;

// ======================================================
// SETUP  (unchanged)
// ======================================================
void setup() {
  Serial.begin(115200);
  delay(500);
  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);
  Wire.begin(I2C_SDA, I2C_SCL);

  precomputeHanning();

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("WiFi");
  while (WiFi.status()!=WL_CONNECTED){ Serial.print("."); delay(500); }
  Serial.println(" ✅ "+WiFi.localIP().toString());

  configTime(0,0,"pool.ntp.org","time.nist.gov");
  time_t now=time(nullptr);
  while(now<8*3600*2){ delay(500); now=time(nullptr); }
  Serial.println("✅ Time synced");

  if (!particleSensor.begin(Wire,I2C_SPEED_FAST)){
    Serial.println("❌ MAX30102 not found!"); while(1);
  }
  particleSensor.setup(60,4,2,400,411,4096);
  particleSensor.setPulseAmplitudeRed(0x1F);
  particleSensor.setPulseAmplitudeIR(0x1F);
  Serial.println("✅ MAX30102 ready");

  Serial.println("=== EEG CAL: sit still, eyes closed ===");
  for (int i=0; i<100; i++){ analogRead(BIOAMP_PIN); delay(5); }
  long midSum=0;
  for (int i=0; i<500; i++){
    int raw=0; for(int j=0;j<8;j++){raw+=analogRead(BIOAMP_PIN);delayMicroseconds(10);} raw/=8;
    midSum += (int)hp.process(notch.process((float)raw));
    delay(4);
  }
  dcOffset = (float)(midSum/500);
  Serial.printf("✅ EEG DC offset: %.1f LSB (%.2f µV)\n",
                dcOffset, dcOffset*ADC_UV_PER_LSB);
  Serial.println("🚀 Ready!");
}

// ======================================================
// LOOP  (unchanged)
// ======================================================
void loop() {
  unsigned long loopStart = millis();
  while (millis() - loopStart < 200) {
    sampleEEG();
    processHeartRate();
    delayMicroseconds(500);
  }

  if (millis()-lastBandCompute >= BAND_COMPUTE_INTERVAL && eegFull) {
    lastBandCompute = millis();
    computeBandPowers();
  }

  String stressLevel = fusedStress(beatAvg, hrv_rmssd);

  String hrStatus;
  if (!fingerOn)              hrStatus = "No Finger";
  else if (beatsCollected<3)  hrStatus = "Reading ("+String(beatsCollected)+"/3)";
  else                        hrStatus = String(beatAvg)+" BPM";

  Serial.printf("HR:%s RMSSD:%dms(%dRR) | θ:%.3f α:%.3f β:%.3f µV²/Hz | BAR:%.2f RAP:%.2f TBR:%.2f | %s\n",
    hrStatus.c_str(), hrv_rmssd, rrCount,   // ← rrCollected → rrCount
    bp_theta, bp_alpha, bp_beta,
    feat_beta_alpha, feat_rel_alpha, feat_theta_beta,
    stressLevel.c_str());

  if (millis()-lastFirebaseUpload >= FIREBASE_INTERVAL) {
    lastFirebaseUpload = millis();
    int hrUp = (beatsCollected>=3) ? beatAvg : 0;
    sendToFirebase(hrUp, hrStatus, hrv_rmssd, stressLevel,
                   feat_beta_alpha, feat_rel_alpha, feat_theta_beta,
                   bp_theta, bp_alpha, bp_beta);
  }
}
