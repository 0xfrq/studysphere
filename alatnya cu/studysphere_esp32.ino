/*
 * ============================================================================
 *  StudySphere — Gateway ESP32 (Smart Learning Station)
 * ============================================================================
 *  Peran:
 *    - Gateway Wi-Fi/MQTT ke EMQX (dashboard desktop + local LLM Gemma).
 *    - Sensor lokal : DHT22 (suhu, kelembapan), HC-SR04 (ada orang di depan meja).
 *    - Sensor remote: MQ-135 & sensor cahaya LDR ("MH sensor") via Arduino UNO (UART).
 *    - Aktuator     : servo SG90 (ventilasi/tirai) di ESP32,
 *                     kipas ON/OFF di Arduino UNO (diperintah lewat UART).
 *    - LCD 16x2 I2C : status lokal (hanya menampilkan cache, tidak memicu sensing).
 *
 *  Inti penelitian: COST-AWARE SEMANTIC SENSING
 *    LLM (Gemma 3 270M + LoRA) mengirim structured request:
 *      {"id":"q1","task":"study_environment_assessment",
 *       "required_information":["co2","temperature","lighting"]}
 *    ESP32 hanya mengambil informasi yang diminta, dengan urutan biaya termurah,
 *    memakai cache yang masih segar, dan menghormati budget. Informasi yang tidak
 *    sempat diambil dilaporkan di "missing" -> LLM bisa meminta tahap berikutnya
 *    (progressive retrieval).
 *
 *  Tiga mode untuk eksperimen (field "mode"):
 *    "raw"        -> baseline 1: semua sensor dibaca fresh, semua data dikirim
 *    "semantic"   -> baseline 2: hanya sensor yang relevan, selalu fresh
 *    "cost_aware" -> proposed  : relevan + cache + urutan biaya + budget
 *
 *  Wiring REVISI (tanpa komponen tambahan):
 *    SG90       : data -> GPIO32, merah -> 5V (VIN), coklat -> GND
 *    LCD 16x2   : VCC -> 5V (VIN), SDA -> GPIO25, SCL -> GPIO33  (bus Wire)
 *    DHT22      : VCC -> 3V3, DATA -> GPIO4, GND -> GND
 *    HC-SR04    : VCC -> 5V (VIN), TRIG -> GPIO22, ECHO -> GPIO21, GND -> GND
 *                 (ECHO keluar 5V; idealnya lewat pembagi 1k/2k ke GPIO21)
 *    Arduino UNO: GPIO27 (RX)  <- TX (D1) UNO (langsung; UNO open-drain, pull-up 3,3V di sini)
 *                 GPIO26 (TX)  -> RX (D0) UNO (langsung), GND bersama
 *    Kipas      : TIDAK di ESP32 -> di D13 (+D12) Arduino UNO
 *
 *  Library (Library Manager):
 *    PubSubClient (Nick O'Leary), ArduinoJson v7 (Benoit Blanchon),
 *    DHT sensor library + Adafruit Unified Sensor,
 *    LiquidCrystal I2C (Frank de Brabander), ESP32Servo (Kevin Harrington)
 * ============================================================================
 */

#include <WiFi.h>
#include <Wire.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include <LiquidCrystal_I2C.h>
#include <ESP32Servo.h>
#include <math.h>
#include "driver/gpio.h"

// ============================== KONFIGURASI =================================
const char* WIFI_SSID = "ISI_SSID_WIFI";
const char* WIFI_PASS = "ISI_PASSWORD_WIFI";

const char*    MQTT_HOST = "192.168.1.10";   // IP PC yang menjalankan EMQX
const uint16_t MQTT_PORT = 1883;
const char*    MQTT_USER = "";                // kosongkan jika EMQX tanpa auth
const char*    MQTT_PASS = "";
const char*    DEVICE_ID = "station01";

// IP statis, untuk router yang tidak memberi IP lewat DHCP (log berhenti di "[WiFi] status=0").
// 0.0.0.0 = pakai DHCP.
const IPAddress STATIC_IP(0, 0, 0, 0);
const IPAddress GATEWAY(192, 168, 1, 1);
const IPAddress SUBNET(255, 255, 255, 0);

// Refresh lokal DHT22 + cahaya (UNO) agar LCD selalu terisi.
// Set 0 saat EKSPERIMEN supaya cache hanya terisi oleh request (perbandingan adil).
const uint32_t LOCAL_REFRESH_MS = 2000;

// Pin
constexpr uint8_t PIN_SERVO     = 32;
constexpr uint8_t PIN_LCD_SDA   = 25;
constexpr uint8_t PIN_LCD_SCL   = 33;
constexpr uint8_t PIN_DHT       = 4;
constexpr uint8_t PIN_SONAR_TRIG = 22;
constexpr uint8_t PIN_SONAR_ECHO = 21;
// Ada orang jika benda terdekat di depan HC-SR04 sejauh ini atau lebih dekat. Kalibrasi:
// lihat distance_cm saat kursi kosong, lalu pasang di bawahnya.
constexpr int PRESENCE_CM = 60;
constexpr uint8_t PIN_LINK_RX   = 27;
constexpr uint8_t PIN_LINK_TX   = 26;
#define DHT_TYPE DHT11            // ganti DHT11 jika modul HW-036 kamu ternyata DHT11

// Link UART ke Arduino
constexpr uint32_t LINK_BAUD        = 19200;  // harus sama dengan Arduino
constexpr uint32_t LINK_TIMEOUT_MS  = 250;    // per percobaan, maks 2 percobaan
constexpr uint32_t PING_INTERVAL_MS = 10000;  // ping hanya jika link diam selama ini
constexpr uint32_t ARDUINO_ALIVE_MS = 25000;

// Aktuator
constexpr uint32_t SERVO_HOLD_MS  = 700;      // servo dilepas setelah bergerak (hemat energi, anti-jitter)

// ======================= REGISTRY INFORMASI & SUMBER ========================
enum InfoId : uint8_t { I_TEMP, I_HUM, I_LIGHT, I_AQ, I_MH, I_OCC, I_COUNT };
enum SourceId : uint8_t { S_DHT, S_SONAR, S_ARDUINO, S_COUNT };
enum Mode : uint8_t { MODE_RAW, MODE_SEMANTIC, MODE_COST_AWARE };

struct InfoDef   { const char* key; SourceId src; uint32_t maxAgeMs; };
struct SourceDef { const char* name; uint8_t cost; };   // cost: peringkat relatif (kecil = murah)

const InfoDef INFO[I_COUNT] = {
  {"temperature", S_DHT,     60000},   // suhu berubah lambat
  {"humidity",    S_DHT,     60000},
  {"lighting",    S_ARDUINO, 10000},   // LDR di UNO, sama dengan mh_sensor: terang/gelap
  {"air_quality", S_ARDUINO, 30000},   // MQ-135 (perkiraan, bukan ppm CO2)
  {"mh_sensor",   S_ARDUINO, 10000},
  {"occupancy",   S_SONAR,    3000},   // HC-SR04: orang bisa datang/pergi kapan saja
};

// HC-SR04: ping lokal, maks ~90 ms per ping. DHT22: protokol 1-wire ~5 ms + batas 2 s.
// Arduino: hop UART tambahan ke MCU lain -> paling mahal.
const SourceDef SRC[S_COUNT] = {
  {"dht22",        2},
  {"hcsr04",       1},
  {"arduino_uart", 3},
};

int resolveInfo(const char* k) {
  struct Alias { const char* name; uint8_t id; };
  static const Alias aliases[] = {
    {"temperature", I_TEMP}, {"temp", I_TEMP}, {"suhu", I_TEMP},
    {"humidity", I_HUM}, {"kelembapan", I_HUM},
    {"lighting", I_LIGHT}, {"light", I_LIGHT}, {"lux", I_LIGHT}, {"cahaya", I_LIGHT},
    {"air_quality", I_AQ}, {"co2", I_AQ}, {"udara", I_AQ},
    {"mh_sensor", I_MH}, {"mh", I_MH},
    {"occupancy", I_OCC}, {"presence", I_OCC}, {"kehadiran", I_OCC}, {"user", I_OCC},
  };
  for (const auto& a : aliases)
    if (strcasecmp(k, a.name) == 0) return a.id;
  return -1;
}

Mode parseMode(const char* s) {
  if (!strcasecmp(s, "raw") || !strcasecmp(s, "baseline1")) return MODE_RAW;
  if (!strcasecmp(s, "semantic") || !strcasecmp(s, "baseline2")) return MODE_SEMANTIC;
  return MODE_COST_AWARE;
}

const char* modeName(Mode m) {
  switch (m) {
    case MODE_RAW: return "raw";
    case MODE_SEMANTIC: return "semantic";
    default: return "cost_aware";
  }
}

const char* aqLevelName(int lvl) {
  switch (lvl) {
    case 1: return "good";
    case 2: return "moderate";
    case 3: return "poor";
    default: return "warmup";
  }
}

// ================================ OBJEK =====================================
WiFiClient       net;
PubSubClient     mqtt(net);
DHT              dht(PIN_DHT, DHT_TYPE);
LiquidCrystal_I2C* lcd = nullptr;
Servo            vent;
HardwareSerial&  LINK = Serial2;

// Topik MQTT
char T_REQ[64], T_RESP[64], T_CMD[64], T_EVENT[64], T_STATE[64], T_STATUS[64], T_STREAM[64];

// Cache nilai sensor + waktu baca per informasi
struct Cache {
  float temp = NAN, hum = NAN;
  int   aqRaw = -1, aqLevel = 0, mhRaw = -1;
  bool  aqAlarm = false, mhTrig = false;
  int   occCm = -1;             // -1 = tidak ada gema (tidak ada benda dalam ~5 m)
  bool  occPresent = false;
  bool  valid[I_COUNT] = {};
  uint32_t readAt[I_COUNT] = {};
} cache;

void stamp(uint8_t i) { cache.valid[i] = true; cache.readAt[i] = millis(); }

bool isFresh(uint8_t i, long maxAgeOverride) {
  if (!cache.valid[i]) return false;
  uint32_t limit = maxAgeOverride >= 0 ? (uint32_t)maxAgeOverride : INFO[i].maxAgeMs;
  return millis() - cache.readAt[i] <= limit;
}

// Request MQTT diantrikan, diproses di loop() (bukan di callback)
struct PendingReq {
  bool active = false;
  char payload[768];
  size_t len = 0;
  uint32_t rxMs = 0;
} pending;

// State aktuator
bool fanOn = false;          // status kipas (sumber kebenaran: Arduino)
int  ventAngle = 90;
uint32_t ventDetachAt = 0;
uint32_t streamMs = 0, lastStreamMs = 0;

// LCD
char lcdMsg0[17] = "", lcdMsg1[17] = "";
uint32_t lcdMsgUntil = 0, lastLcdMs = 0, lastLocalRefreshMs = 0;
char lcdPrev0[17] = "", lcdPrev1[17] = "";

// ============================== LINK ARDUINO ================================
uint16_t linkSeq = 0;
uint32_t lastArduinoMs = 0, lastPingMs = 0;

struct LinkWait {
  bool waiting = false, got = false;
  uint16_t seq = 0;
  char type[6] = "";
  char payload[96] = "";
} linkWait;

char linkBuf[128];
size_t linkLen = 0;
bool linkInFrame = false;

bool arduinoAlive() { return lastArduinoMs && (millis() - lastArduinoMs < ARDUINO_ALIVE_MS); }

uint8_t xorSum(const char* s) {
  uint8_t cs = 0;
  while (*s) cs ^= (uint8_t)*s++;
  return cs;
}

size_t linkSend(const char* body) {
  char frame[96];
  int n = snprintf(frame, sizeof(frame), "$%s*%02X\n", body, xorSum(body));
  LINK.write((const uint8_t*)frame, n);
  return n;
}

void publishOut(JsonDocument& out, const char* topic, bool retained = false);

// Parse "k=v;k=v" dari Arduino dan update cache.
void applyArduinoKV(char* kv, bool fromEvent) {
  char* save = nullptr;
  for (char* p = strtok_r(kv, ";", &save); p; p = strtok_r(nullptr, ";", &save)) {
    char* eq = strchr(p, '=');
    if (!eq) continue;
    *eq = '\0';
    int v = atoi(eq + 1);

    if (!strcmp(p, "aqr")) { cache.aqRaw = v; stamp(I_AQ); }
    else if (!strcmp(p, "aql")) cache.aqLevel = v;
    else if (!strcmp(p, "aqd")) cache.aqAlarm = v;
    else if (!strcmp(p, "mhr")) { cache.mhRaw = v; stamp(I_MH); stamp(I_LIGHT); }
    else if (!strcmp(p, "mhd")) cache.mhTrig = v;
    else if (!strcmp(p, "fan")) fanOn = v;

    // Event: status digital berubah -> diteruskan sekali ke MQTT (event-driven)
    if (fromEvent && (!strcmp(p, "aqd") || !strcmp(p, "mhd"))) {
      JsonDocument e;
      e["source"] = "arduino";
      e["event"] = !strcmp(p, "aqd") ? "air_quality_alarm" : "mh_sensor_triggered";
      e["value"] = (bool)v;
      e["uptime_s"] = millis() / 1000;
      publishOut(e, T_EVENT);
    }
  }
}

void handleLinkFrame(char* body) {
  char* star = strrchr(body, '*');
  if (!star) return;
  *star = '\0';
  if ((uint8_t)strtoul(star + 1, nullptr, 16) != xorSum(body)) {
    Serial.println("[LINK] checksum salah, frame dibuang");
    return;
  }
  lastArduinoMs = millis();

  // body = TYPE,SEQ[,ARGS]
  char* c1 = strchr(body, ',');
  if (!c1) return;
  *c1 = '\0';
  const char* type = body;
  char* seqS = c1 + 1;
  char* args = strchr(seqS, ',');
  if (args) *args++ = '\0';
  uint16_t seq = (uint16_t)atoi(seqS);

  if (!strcmp(type, "EVT")) {
    if (args) applyArduinoKV(args, true);
    return;
  }

  if (linkWait.waiting && seq == linkWait.seq) {
    strlcpy(linkWait.type, type, sizeof(linkWait.type));
    strlcpy(linkWait.payload, args ? args : "", sizeof(linkWait.payload));
    linkWait.got = true;
  }
}

size_t pollLink() {
  size_t bytes = 0;
  while (LINK.available()) {
    char c = (char)LINK.read();
    bytes++;
    if (c == '$') { linkLen = 0; linkInFrame = true; continue; }
    if (!linkInFrame) continue;
    if (c == '\n' || c == '\r') {
      if (linkLen) { linkBuf[linkLen] = '\0'; handleLinkFrame(linkBuf); }
      linkInFrame = false;
      linkLen = 0;
    } else if (linkLen < sizeof(linkBuf) - 1) {
      linkBuf[linkLen++] = c;
    } else {
      linkInFrame = false;
      linkLen = 0;
    }
  }
  return bytes;
}

// Kirim perintah ke Arduino dan tunggu balasan dengan seq yang sama.
bool linkRequest(const char* type, const char* args, char* out, size_t outLen, size_t& bytes) {
  for (int attempt = 0; attempt < 2; attempt++) {
    uint16_t seq = ++linkSeq;
    char body[64];
    if (args) snprintf(body, sizeof(body), "%s,%u,%s", type, seq, args);
    else      snprintf(body, sizeof(body), "%s,%u", type, seq);

    linkWait.waiting = true;
    linkWait.got = false;
    linkWait.seq = seq;
    bytes += linkSend(body);

    uint32_t t0 = millis();
    while (millis() - t0 < LINK_TIMEOUT_MS) {
      bytes += pollLink();
      if (linkWait.got) {
        linkWait.waiting = false;
        if (!strcmp(linkWait.type, "ERR")) return false;
        if (out) strlcpy(out, linkWait.payload, outLen);
        return true;
      }
      delay(1);
    }
  }
  linkWait.waiting = false;
  return false;
}

// ============================ PEMBACAAN SENSOR ==============================
// HC-SR04: median dari 3 ping, jadi satu gema nyasar (terlalu dekat) atau satu ping tanpa gema
// tidak menentukan hasil.
bool readSonar() {
  long us[3];
  for (int i = 0; i < 3; i++) {
    if (i) delay(60);   // gema ping sebelumnya harus reda dulu
    digitalWrite(PIN_SONAR_TRIG, HIGH);
    delayMicroseconds(10);
    digitalWrite(PIN_SONAR_TRIG, LOW);
    us[i] = pulseIn(PIN_SONAR_ECHO, HIGH, 30000);
    if (!us[i]) us[i] = LONG_MAX;   // tanpa gema = tidak ada benda dalam jangkauan
  }
  long med = std::max(std::min(us[0], us[1]), std::min(std::max(us[0], us[1]), us[2]));
  cache.occCm = med == LONG_MAX ? -1 : med / 58;
  cache.occPresent = cache.occCm >= 0 && cache.occCm <= PRESENCE_CM;
  stamp(I_OCC);
  return true;
}

bool readDHT() {
  for (int i = 0; i < 2; i++) {
    float h = dht.readHumidity();
    float t = dht.readTemperature();
    if (!isnan(h) && !isnan(t)) {
      cache.temp = t;
      cache.hum = h;
      stamp(I_TEMP);
      stamp(I_HUM);
      return true;
    }
    delay(50);
  }
  return false;
}

// Satu hop UART untuk semua field Arduino yang dibutuhkan (batching).
bool readArduino(bool wantAQ, bool wantMH, size_t& uartBytes) {
  if (!wantAQ && !wantMH) return true;
  const char* fields = (wantAQ && wantMH) ? "AQ;MH" : (wantAQ ? "AQ" : "MH");
  char reply[96];
  if (!linkRequest("GET", fields, reply, sizeof(reply), uartBytes)) return false;
  applyArduinoKV(reply, false);
  return true;
}

// ============================ INTI: REQUEST =================================
float round1(float x) { return roundf(x * 10.0f) / 10.0f; }

void addInfoValue(JsonObject data, uint8_t i, bool verbose) {
  switch (i) {
    case I_TEMP:  data["temperature"] = round1(cache.temp); break;
    case I_HUM:   data["humidity"] = round1(cache.hum); break;
    case I_LIGHT: {
      JsonObject o = data["lighting"].to<JsonObject>();
      o["level"] = cache.mhTrig ? "bright" : "dark";   // DO modul LDR (ambang = potensiometer modul)
      o["raw"] = cache.mhRaw;
      if (verbose) o["sensor"] = "ldr_uno";
      break;
    }
    case I_AQ: {
      JsonObject o = data["air_quality"].to<JsonObject>();
      o["raw"] = cache.aqRaw;
      o["level"] = aqLevelName(cache.aqLevel);
      o["alarm"] = cache.aqAlarm;
      if (verbose) o["sensor"] = "mq135_approx";
      break;
    }
    case I_OCC: {
      JsonObject o = data["occupancy"].to<JsonObject>();
      o["present"] = cache.occPresent;
      if (cache.occCm >= 0) o["distance_cm"] = cache.occCm;
      if (verbose) o["sensor"] = "hcsr04";
      break;
    }
    case I_MH: {
      JsonObject o = data["mh_sensor"].to<JsonObject>();
      o["raw"] = cache.mhRaw;
      o["triggered"] = cache.mhTrig;
      break;
    }
  }
}

void publishError(const char* id, const char* err, const char* topic) {
  JsonDocument out;
  if (id && id[0]) out["id"] = id;
  out["error"] = err;
  publishOut(out, topic);
}

void runRequest(JsonDocument& in, uint32_t t0, const char* topic) {
  const char* id   = in["id"] | "";
  const char* task = in["task"] | "";
  Mode mode        = parseMode(in["mode"] | "cost_aware");
  int  budget      = in["budget"] | -1;           // maks jumlah sumber yang dibaca fresh; -1 = bebas
  long maxAgeOv    = in["max_age_ms"] | -1L;      // override kesegaran cache

  JsonDocument out;
  out["id"] = id;
  if (task[0]) out["task"] = task;
  if (!in["stage"].isNull()) out["stage"] = in["stage"];
  out["mode"] = modeName(mode);

  // 1) Informasi apa yang dibutuhkan?
  bool need[I_COUNT] = {};
  if (mode == MODE_RAW) {
    for (uint8_t i = 0; i < I_COUNT; i++) need[i] = true;
  } else {
    JsonArray unknown;
    for (JsonVariantConst v : in["required_information"].as<JsonArrayConst>()) {
      const char* k = v.as<const char*>();
      if (!k) continue;
      int i = resolveInfo(k);
      if (i >= 0) {
        need[i] = true;
      } else {
        if (unknown.isNull()) unknown = out["unknown"].to<JsonArray>();
        unknown.add(k);
      }
    }
  }
  bool anyNeed = false;
  for (uint8_t i = 0; i < I_COUNT; i++) anyNeed |= need[i];
  if (!anyNeed) {
    out["error"] = "no_known_required_information";
    publishOut(out, topic);
    return;
  }

  // 2) Mana yang harus dibaca fresh? (cost_aware memakai cache yang masih segar)
  bool fresh[I_COUNT] = {};
  for (uint8_t i = 0; i < I_COUNT; i++) {
    if (!need[i]) continue;
    fresh[i] = !(mode == MODE_COST_AWARE && isFresh(i, maxAgeOv));
  }

  // 3) Urutkan sumber dari biaya termurah, baca sesuai budget
  uint8_t order[S_COUNT] = {0, 1, 2};
  for (uint8_t a = 0; a < S_COUNT; a++)
    for (uint8_t b = a + 1; b < S_COUNT; b++)
      if (SRC[order[b]].cost < SRC[order[a]].cost) { uint8_t t = order[a]; order[a] = order[b]; order[b] = t; }

  bool usedBudget = (mode == MODE_COST_AWARE && budget >= 0);
  const char* missReason[I_COUNT] = {};
  int reads = 0;
  size_t uartBytes = 0;
  uint32_t readMs = 0;
  bool sourceRead[S_COUNT] = {};

  for (uint8_t k = 0; k < S_COUNT; k++) {
    uint8_t s = order[k];
    bool needS = false;
    for (uint8_t i = 0; i < I_COUNT; i++) if (fresh[i] && INFO[i].src == s) needS = true;
    if (!needS) continue;

    if (usedBudget && reads >= budget) {
      for (uint8_t i = 0; i < I_COUNT; i++)
        if (fresh[i] && INFO[i].src == s) missReason[i] = "budget";
      continue;
    }

    uint32_t r0 = millis();
    bool ok = false;
    switch (s) {
      case S_DHT:     ok = readDHT(); break;
      case S_SONAR:   ok = readSonar(); break;
      case S_ARDUINO: ok = readArduino(fresh[I_AQ], fresh[I_MH] || fresh[I_LIGHT], uartBytes); break;
    }
    readMs += millis() - r0;
    reads++;
    sourceRead[s] = true;

    if (!ok) {
      const char* reason = (s == S_ARDUINO && !arduinoAlive()) ? "arduino_offline" : "sensor_error";
      for (uint8_t i = 0; i < I_COUNT; i++)
        if (fresh[i] && INFO[i].src == s) missReason[i] = reason;
    }
  }

  // 4) Susun respons: hanya informasi yang diminta
  JsonObject data = out["data"].to<JsonObject>();
  JsonObject missing, cachedAge;
  uint32_t now = millis();
  for (uint8_t i = 0; i < I_COUNT; i++) {
    if (!need[i]) continue;
    if (missReason[i] || !cache.valid[i]) {
      if (missing.isNull()) missing = out["missing"].to<JsonObject>();
      missing[INFO[i].key] = missReason[i] ? missReason[i] : "sensor_error";
      continue;
    }
    addInfoValue(data, i, mode == MODE_RAW);
    if (!fresh[i]) {
      if (cachedAge.isNull()) cachedAge = out["cached_age_ms"].to<JsonObject>();
      cachedAge[INFO[i].key] = now - cache.readAt[i];
    }
  }

  if (mode == MODE_RAW) {
    JsonObject dev = data["device"].to<JsonObject>();
    dev["rssi"] = WiFi.RSSI();
    dev["uptime_s"] = millis() / 1000;
    dev["arduino_alive"] = arduinoAlive();
    dev["fan"] = fanOn;
    dev["vent"] = ventAngle;
  }

  // 5) Metrik eksperimen
  JsonObject meta = out["meta"].to<JsonObject>();
  meta["sensor_reads"] = reads;
  JsonArray srcArr = meta["sources"].to<JsonArray>();
  for (uint8_t s = 0; s < S_COUNT; s++) if (sourceRead[s]) srcArr.add(SRC[s].name);
  meta["uart_bytes"] = uartBytes;
  meta["read_ms"] = readMs;
  meta["latency_ms"] = millis() - t0;
  meta["payload_bytes"] = 0;   // diisi di publishOut()

  publishOut(out, topic);
}

void processPending() {
  JsonDocument in;
  DeserializationError err = deserializeJson(in, pending.payload, pending.len);
  uint32_t t0 = pending.rxMs;
  pending.active = false;
  if (err) {
    publishError("", "bad_json", T_RESP);
    return;
  }
  runRequest(in, t0, T_RESP);
}

// ================================ MQTT ======================================
void publishOut(JsonDocument& out, const char* topic, bool retained) {
  static char buf[1024];

  // payload_bytes harus memuat dirinya sendiri -> iterasi sampai stabil
  if (out["meta"].is<JsonObject>()) {
    for (int k = 0; k < 3; k++) out["meta"]["payload_bytes"] = measureJson(out);
  }

  size_t n = serializeJson(out, buf, sizeof(buf));
  if (n == 0 || n >= sizeof(buf) - 1) {
    Serial.println("[MQTT] payload terlalu besar");
    return;
  }
  if (mqtt.connected()) mqtt.publish(topic, (const uint8_t*)buf, n, retained);
  Serial.printf("[PUB] %s (%u B): %s\n", topic, (unsigned)n, buf);
}

void publishState() {
  JsonDocument s;
  s["fan"] = fanOn;
  s["vent"] = ventAngle;
  s["arduino_alive"] = arduinoAlive();
  s["stream_ms"] = streamMs;
  publishOut(s, T_STATE, true);
}

// ============================== AKTUATOR ====================================
// Kipas ada di Arduino UNO -> kirim perintah FAN lewat UART, tunggu ACK.
bool setFan(bool on) {
  char reply[48];
  size_t bytes = 0;
  if (!linkRequest("FAN", on ? "1" : "0", reply, sizeof(reply), bytes)) return false;
  applyArduinoKV(reply, false);
  return true;
}

void setVent(int angle) {
  ventAngle = constrain(angle, 0, 180);
  if (!vent.attached()) {
    int ch = vent.attach(PIN_SERVO, 500, 2400);   // ESP32Servo: 0 = gagal mendapat kanal PWM
    Serial.printf("[VENT] attach GPIO%d -> %d\n", PIN_SERVO, ch);
  }
  vent.write(ventAngle);
  Serial.printf("[VENT] %d derajat, pulsa %d us\n", ventAngle, vent.readMicroseconds());
  ventDetachAt = millis() + SERVO_HOLD_MS;
}

void handleCommand(const byte* p, unsigned int len) {
  JsonDocument d;
  if (deserializeJson(d, p, len)) return;

  // "fan": true/false atau 1/0 (angka > 0 = ON). Kipas hanya ON/OFF.
  const char* fanError = nullptr;
  if (!d["fan"].isNull()) {
    bool on = d["fan"].is<bool>() ? d["fan"].as<bool>() : d["fan"].as<int>() > 0;
    if (!setFan(on)) fanError = arduinoAlive() ? "fan_no_ack" : "arduino_offline";
  }
  if (d["vent"].is<int>()) setVent(d["vent"].as<int>());

  if (d["lcd"].is<JsonArrayConst>()) {
    JsonArrayConst lines = d["lcd"].as<JsonArrayConst>();
    strlcpy(lcdMsg0, lines[0] | "", sizeof(lcdMsg0));
    strlcpy(lcdMsg1, lines[1] | "", sizeof(lcdMsg1));
    lcdMsgUntil = millis() + (d["lcd_ms"] | 10000UL);
  }

  // Baseline IoT konvensional: streaming semua data tiap N ms (0 = mati)
  if (d["stream_ms"].is<long>()) {
    long v = d["stream_ms"].as<long>();
    streamMs = v <= 0 ? 0 : (uint32_t)max(2000L, v);
  }

  publishState();
  if (fanError) publishError("", fanError, T_STATE);
}

void onMqtt(char* topic, byte* payload, unsigned int len) {
  if (!strcmp(topic, T_REQ)) {
    if (pending.active) {
      publishError("", "busy", T_RESP);
      return;
    }
    len = min((size_t)len, sizeof(pending.payload) - 1);
    memcpy(pending.payload, payload, len);
    pending.payload[len] = '\0';
    pending.len = len;
    pending.rxMs = millis();
    pending.active = true;
  } else if (!strcmp(topic, T_CMD)) {
    handleCommand(payload, len);
  }
}

uint32_t lastMqttTry = 0;

void ensureMqtt() {
  if (WiFi.status() != WL_CONNECTED || mqtt.connected()) return;
  if (millis() - lastMqttTry < 5000) return;
  lastMqttTry = millis();

  char cid[48];
  snprintf(cid, sizeof(cid), "studysphere-%s-%04X", DEVICE_ID, (uint16_t)(ESP.getEfuseMac() & 0xFFFF));
  bool ok = (MQTT_USER[0])
    ? mqtt.connect(cid, MQTT_USER, MQTT_PASS, T_STATUS, 1, true, "offline")
    : mqtt.connect(cid, nullptr, nullptr, T_STATUS, 1, true, "offline");

  if (ok) {
    mqtt.publish(T_STATUS, "online", true);
    mqtt.subscribe(T_REQ, 1);
    mqtt.subscribe(T_CMD, 1);
    publishState();
    Serial.println("[MQTT] terhubung ke EMQX");
  } else {
    Serial.printf("[MQTT] gagal, state=%d\n", mqtt.state());
  }
}

// ================================== LCD =====================================
uint8_t probeI2C(TwoWire& w, const uint8_t* addrs, size_t n) {
  for (size_t i = 0; i < n; i++) {
    w.beginTransmission(addrs[i]);
    if (w.endTransmission() == 0) return addrs[i];
  }
  return 0;
}

void lcdLine(uint8_t row, const char* text, char* prev) {
  char line[17];
  snprintf(line, sizeof(line), "%-16s", text);
  if (strcmp(line, prev) == 0) return;   // tulis hanya jika berubah
  lcd->setCursor(0, row);
  lcd->print(line);
  strcpy(prev, line);
}

void updateLcd() {
  if (!lcd) return;
  char l0[24], l1[24];

  if (millis() < lcdMsgUntil) {
    lcdLine(0, lcdMsg0, lcdPrev0);
    lcdLine(1, lcdMsg1, lcdPrev1);
    return;
  }

  // Baris 0: nilai dari CACHE (LCD tidak memicu sensing baru)
  char t[6] = "--.-", h[4] = "--", l[7] = "----";
  if (cache.valid[I_TEMP])  snprintf(t, sizeof(t), "%.1f", cache.temp);
  if (cache.valid[I_HUM])   snprintf(h, sizeof(h), "%.0f", cache.hum);
  if (cache.valid[I_LIGHT]) strlcpy(l, cache.mhTrig ? "bright" : "dark", sizeof(l));
  snprintf(l0, sizeof(l0), "%sC %s%% %s", t, h, l);

  // Baris 1: status koneksi
  snprintf(l1, sizeof(l1), "WiFi%c MQTT%c ARD%c",
           WiFi.status() == WL_CONNECTED ? '+' : '-',
           mqtt.connected() ? '+' : '-',
           arduinoAlive() ? '+' : '-');

  lcdLine(0, l0, lcdPrev0);
  lcdLine(1, l1, lcdPrev1);
}

// ============================== SETUP / LOOP ================================
void setup() {
  Serial.begin(115200);
  LINK.begin(LINK_BAUD, SERIAL_8N1, PIN_LINK_RX, PIN_LINK_TX);
  // Pull-up 3,3V di RX: UNO mengirim secara open-drain (hanya menarik LOW),
  // jadi level HIGH berasal dari sini dan tidak pernah 5V.
  gpio_pullup_en((gpio_num_t)PIN_LINK_RX);

  // Bus I2C #0: LCD
  Wire.begin(PIN_LCD_SDA, PIN_LCD_SCL, 100000);
  const uint8_t lcdAddrs[] = {0x27, 0x3F};
  uint8_t la = probeI2C(Wire, lcdAddrs, sizeof(lcdAddrs));
  if (la) {
    lcd = new LiquidCrystal_I2C(la, 16, 2);
    lcd->init();
    lcd->backlight();
    lcd->setCursor(0, 0); lcd->print("StudySphere");
    lcd->setCursor(0, 1); lcd->print("Booting...");
    Serial.printf("[LCD] OK di 0x%02X\n", la);
  } else {
    Serial.println("[LCD] tidak terdeteksi");
  }

  dht.begin();
  pinMode(PIN_SONAR_TRIG, OUTPUT);
  digitalWrite(PIN_SONAR_TRIG, LOW);
  pinMode(PIN_SONAR_ECHO, INPUT);

  // Timer LEDC untuk servo (ESP32Servo)
  ESP32PWM::allocateTimer(0);
  vent.setPeriodHertz(50);

  // Topik: studysphere/<device>/<...>
  snprintf(T_REQ,    sizeof(T_REQ),    "studysphere/%s/req",    DEVICE_ID);
  snprintf(T_RESP,   sizeof(T_RESP),   "studysphere/%s/resp",   DEVICE_ID);
  snprintf(T_CMD,    sizeof(T_CMD),    "studysphere/%s/cmd",    DEVICE_ID);
  snprintf(T_EVENT,  sizeof(T_EVENT),  "studysphere/%s/event",  DEVICE_ID);
  snprintf(T_STATE,  sizeof(T_STATE),  "studysphere/%s/state",  DEVICE_ID);
  snprintf(T_STATUS, sizeof(T_STATUS), "studysphere/%s/status", DEVICE_ID);
  snprintf(T_STREAM, sizeof(T_STREAM), "studysphere/%s/stream", DEVICE_ID);

  WiFi.mode(WIFI_STA);
  // Alasan putus dari driver: 201 = SSID tidak ketemu, 202/15 = password/handshake, 2/4 = AP menolak
  WiFi.onEvent([](WiFiEvent_t, WiFiEventInfo_t info) {
    Serial.printf("[WiFi] putus, alasan %u\n", info.wifi_sta_disconnected.reason);
  }, ARDUINO_EVENT_WIFI_STA_DISCONNECTED);
  WiFi.setAutoReconnect(true);
  if (STATIC_IP[0]) WiFi.config(STATIC_IP, GATEWAY, SUBNET, GATEWAY);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onMqtt);
  mqtt.setBufferSize(1280);
  mqtt.setKeepAlive(30);

  Serial.println("[BOOT] Gateway ESP32 siap");
}

void loop() {
  // Log setiap perubahan status WiFi (3 = terhubung, 1 = SSID tidak terlihat, 4 = gagal, 6 = putus)
  static int lastWifi = -1;
  int ws = WiFi.status();
  if (ws != lastWifi) {
    lastWifi = ws;
    if (ws == WL_CONNECTED) Serial.printf("[WiFi] terhubung, IP %s, RSSI %d\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
    else Serial.printf("[WiFi] status=%d\n", ws);
  }

  ensureMqtt();
  mqtt.loop();
  pollLink();   // tangkap EVT dari Arduino

  if (pending.active) processPending();

  uint32_t now = millis();

  // Ping Arduino hanya jika link diam (hemat komunikasi)
  if (now - lastArduinoMs > PING_INTERVAL_MS && now - lastPingMs > PING_INTERVAL_MS) {
    lastPingMs = now;
    size_t dummy = 0;
    char pong[48];
    if (linkRequest("PING", nullptr, pong, sizeof(pong), dummy)) applyArduinoKV(pong, false);
  }

  // Lepas servo setelah selesai bergerak
  if (ventDetachAt && (int32_t)(millis() - ventDetachAt) >= 0) {
    vent.detach();
    ventDetachAt = 0;
  }

  // Baseline streaming (hanya jika diaktifkan lewat cmd stream_ms)
  if (streamMs && millis() - lastStreamMs >= streamMs && !pending.active) {
    lastStreamMs = millis();
    JsonDocument in;
    in["id"] = "stream";
    in["mode"] = "raw";
    runRequest(in, millis(), T_STREAM);
  }

  // Refresh lokal opsional (untuk tampilan LCD)
  if (LOCAL_REFRESH_MS && millis() - lastLocalRefreshMs >= LOCAL_REFRESH_MS) {
    lastLocalRefreshMs = millis();
    readDHT();
    // Cahaya dari UNO; hanya saat link hidup, supaya loop tidak tertahan timeout UART.
    size_t uartBytes = 0;
    if (arduinoAlive()) readArduino(false, true, uartBytes);
  }

  if (millis() - lastLcdMs >= 1000) {
    lastLcdMs = millis();
    updateLcd();
  }
}
