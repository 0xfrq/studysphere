/*
 * ============================================================================
 *  StudySphere — Node Sensor + Kipas (Arduino UNO)
 * ============================================================================
 *  Peran:
 *    Node sensor sekunder + aktuator kipas. Membaca MQ-135 dan modul
 *    "MH Sensor Series", menampilkan status di LCD 16x2 I2C, menyalakan/mematikan
 *    kipas atas perintah ESP32, dan HANYA mengirim data ke ESP32 bila diminta
 *    (GET) atau bila status digital berubah (EVT). Tidak ada streaming periodik
 *    -> sesuai prinsip cost-aware semantic sensing.
 *
 *  Wiring UNO (tanpa komponen tambahan):
 *    MQ-135      : AO -> A0   | DO -> D2   | VCC -> 5V | GND -> GND
 *    MH sensor   : AO -> A3   | DO -> D11  | VCC -> 5V | GND -> GND
 *    LCD I2C 16x2: SDA -> A4  | SCL -> A5  | VCC -> 5V | GND -> GND
 *                  (boleh juga ke pin berlabel SDA/SCL dekat AREF; jalurnya sama)
 *    Kipas DC 5V : merah (+) -> D13 (disarankan juga dijumper ke D12) | hitam (-) -> GND
 *    Link ESP32 via TX/RX hardware (langsung, TANPA resistor):
 *      TX (D1) -> GPIO16 ESP32  (open-drain: UNO hanya menarik LOW,
 *                                HIGH berasal dari pull-up 3,3V di ESP32)
 *      RX (D0) <- GPIO17 ESP32  (3,3V dari ESP32 terbaca HIGH oleh UNO)
 *      GND     <-> GND ESP32    (WAJIB)
 *
 *    PENTING karena D0/D1 juga jalur USB:
 *      - CABUT kabel D0 (RX) saat upload sketch, pasang lagi setelah selesai.
 *      - Serial Monitor hanya untuk MEMBACA frame UNO -> ESP32 (19200 baud);
 *        jangan mengetik/mengirim apa pun dari Serial Monitor.
 *
 *  Protokol UART (teks per baris, 19200 8N1):
 *    Frame : $<BODY>*<CS>\n      CS = XOR semua karakter BODY (2 digit hex)
 *    ESP32 -> Arduino :
 *      PING,<seq>
 *      GET,<seq>,<AQ | MH | AQ;MH | ALL>
 *      FAN,<seq>,<0|1>
 *    Arduino -> ESP32 :
 *      PONG,<seq>,up=<detik>;fan=<0|1>
 *      DAT,<seq>,aqr=<0..1023>;aql=<0..3>;aqd=<0|1>;mhr=<0..1023>;mhd=<0|1>[;fan=<0|1>]
 *      ACK,<seq>,fan=<0|1>
 *      EVT,<n>,<aqd|mhd>=<0|1>        (hanya saat status digital berubah)
 *      ERR,<seq>,<alasan>
 *    aql: 0 = warmup, 1 = baik, 2 = sedang, 3 = buruk
 *
 *  Library: LiquidCrystal I2C (Frank de Brabander). Wire bawaan.
 * ============================================================================
 */

#if !defined(__AVR_ATmega328P__)
#error "Sketch ini untuk Arduino UNO (Tools > Board > Arduino Uno)"
#endif

#include <Arduino.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <util/delay.h>
#include <string.h>
#include <stdlib.h>

// ============================== KONFIGURASI =================================
#define LINK         Serial                   // UART hardware: RX = D0, TX = D1
#define LINK_BAUD    19200UL                  // turunkan ke 9600UL jika sering checksum salah
#define LINK_BIT_US  (1000000.0 / LINK_BAUD)

const uint8_t PIN_MQ_A     = A0;
const uint8_t PIN_MQ_D     = 2;
const uint8_t PIN_MH_A     = A3;
const uint8_t PIN_MH_D     = 11;
// D0 (PD0) = RX <- GPIO17 ESP32 (UART hardware)
// D1 (PD1) = TX open-drain -> GPIO16 ESP32 (dikendalikan langsung lewat register)

// Kipas langsung di pin digital (tanpa driver). D13 = PB5, D12 = PB4.
// Kedua pin selalu disetel BERSAMAAN dalam satu tulisan register, sehingga
// aman dijumper jadi satu untuk membagi arus kipas ke dua pin.
const bool    FAN_USE_D12 = true;
const uint8_t FAN_MASK    = _BV(PB5) | (FAN_USE_D12 ? _BV(PB4) : 0);

// Output digital modul LM393 umumnya aktif LOW (LED modul menyala = LOW).
const bool MQ_D_ACTIVE_LOW = true;
const bool MH_D_ACTIVE_LOW = true;

// MQ-135 butuh pemanasan. Selama warmup, level = 0 dan event alarm ditahan.
const unsigned long MQ_WARMUP_MS = 120000UL;

// Ambang level kualitas udara dari nilai ADC mentah MQ-135.
// MQ-135 BUKAN sensor CO2 sejati -> kalibrasi ambang ini di ruanganmu.
const int AQ_MODERATE = 300;
const int AQ_POOR     = 500;

const unsigned long SAMPLE_MS      = 100;    // sampling lokal (murah)
const unsigned long DEBOUNCE_MS    = 500;    // kestabilan pin digital
const unsigned long LCD_REFRESH_MS = 1000;
const unsigned long LINK_OK_MS     = 30000;  // ESP32 dianggap terhubung jika ada frame < 30 s

// ================================ STATE =====================================
LiquidCrystal_I2C* lcd = nullptr;

float aqEma = -1, mhEma = -1;            // nilai analog tersaring (EMA)
bool  aqTrig = false, mhTrig = false;    // status digital stabil (setelah debounce)
bool  aqRawTrig = false, mhRawTrig = false;
unsigned long aqChangedAt = 0, mhChangedAt = 0;
bool  fanOn = false;

unsigned long lastSampleMs = 0, lastLcdMs = 0, lastLinkMs = 0;
bool linkSeen = false;
uint16_t evtSeq = 0;

char rxBuf[72];
uint8_t rxLen = 0;
bool inFrame = false;

char lcdPrev0[17] = "", lcdPrev1[17] = "";

// ================================= KIPAS ====================================
void fanInit() {
  PORTB &= ~FAN_MASK;   // LOW dulu, baru jadi output -> tidak ada glitch
  DDRB  |=  FAN_MASK;
}

void setFan(bool on) {
  uint8_t sreg = SREG;
  cli();
  if (on) PORTB |= FAN_MASK; else PORTB &= ~FAN_MASK;   // D12 & D13 berubah serentak
  SREG = sreg;
  fanOn = on;
}

// ===================== TX OPEN-DRAIN KE ESP32 (D1 = PD1) ====================
// bit 0 -> pin OUTPUT LOW, bit 1 -> pin INPUT (hi-Z, ditarik 3,3V oleh ESP32).
// Jadi tegangan di GPIO16 ESP32 tidak pernah lebih dari 3,3V tanpa resistor.
// UART hardware hanya dipakai untuk MENERIMA (D0). Pemancar UART dimatikan agar
// D1 bisa dibuat open-drain (pemancar UART biasa mengeluarkan 5V).
void linkInit() {
  LINK.begin(LINK_BAUD);
  UCSR0B &= ~_BV(TXEN0);           // lepas D1 dari UART
  PORTD  &= ~_BV(PD1);             // tanpa pull-up internal ke 5V
  DDRD   &= ~_BV(PD1);             // idle = hi-Z (HIGH lewat pull-up ESP32)
  PORTD  &= ~_BV(PD0);             // RX (D0) tanpa pull-up internal
}

static void odWriteByte(uint8_t b) {
  uint8_t sreg = SREG;
  cli();
  DDRD |= _BV(PD1);                          // start bit (LOW)
  _delay_us(LINK_BIT_US - 0.5);
  for (uint8_t i = 0; i < 8; i++) {
    if (b & 0x01) DDRD &= ~_BV(PD1); else DDRD |= _BV(PD1);
    _delay_us(LINK_BIT_US - 0.5);
    b >>= 1;
  }
  DDRD &= ~_BV(PD1);                         // stop bit (HIGH)
  _delay_us(LINK_BIT_US);
  SREG = sreg;
}

// =============================== UTILITAS ===================================
uint8_t xorSum(const char* s) {
  uint8_t cs = 0;
  while (*s) cs ^= (uint8_t)*s++;
  return cs;
}

void sendFrame(const char* body) {
  char tail[6];
  snprintf(tail, sizeof(tail), "*%02X\n", xorSum(body));
  odWriteByte('$');
  for (const char* p = body; *p; ++p) odWriteByte((uint8_t)*p);
  for (const char* p = tail; *p; ++p) odWriteByte((uint8_t)*p);
}

bool aqReady() { return millis() >= MQ_WARMUP_MS; }

int aqLevel() {
  if (!aqReady() || aqEma < 0) return 0;
  int v = (int)aqEma;
  if (v < AQ_MODERATE) return 1;
  if (v < AQ_POOR) return 2;
  return 3;
}

const char* aqLevelText(int lvl) {
  switch (lvl) {
    case 1: return "BAIK";
    case 2: return "SEDANG";
    case 3: return "BURUK";
    default: return "WARMUP";
  }
}

bool readTrig(uint8_t pin, bool activeLow) {
  bool high = digitalRead(pin) == HIGH;
  return activeLow ? !high : high;
}

void sendEvent(const char* key, bool value) {
  char body[24];
  snprintf(body, sizeof(body), "EVT,%u,%s=%d", ++evtSeq, key, value ? 1 : 0);
  sendFrame(body);
}

// Debounce: status dianggap berubah jika stabil >= DEBOUNCE_MS.
void debounce(bool now, bool& lastRaw, bool& stable, unsigned long& changedAt,
              const char* key, bool allowEvent) {
  unsigned long t = millis();
  if (now != lastRaw) {
    lastRaw = now;
    changedAt = t;
  } else if (now != stable && t - changedAt >= DEBOUNCE_MS) {
    stable = now;
    if (allowEvent) sendEvent(key, stable);
  }
}

// ============================ SAMPLING LOKAL ================================
void sampleSensors() {
  int a = analogRead(PIN_MQ_A);
  aqEma = (aqEma < 0) ? a : aqEma + 0.2f * (a - aqEma);

  int m = analogRead(PIN_MH_A);
  mhEma = (mhEma < 0) ? m : mhEma + 0.3f * (m - mhEma);

  // Event alarm MQ-135 ditahan selama warmup (output digital belum valid).
  debounce(readTrig(PIN_MQ_D, MQ_D_ACTIVE_LOW), aqRawTrig, aqTrig, aqChangedAt,
           "aqd", aqReady());
  debounce(readTrig(PIN_MH_D, MH_D_ACTIVE_LOW), mhRawTrig, mhTrig, mhChangedAt,
           "mhd", true);
}

// ============================ HANDLER PROTOKOL ==============================
void appendKV(char* buf, size_t cap, const char* key, long val) {
  size_t n = strlen(buf);
  snprintf(buf + n, cap - n, "%s%s=%ld", n ? ";" : "", key, val);
}

void handleFrame(char* body) {
  char* star = strrchr(body, '*');
  if (!star) return;
  *star = '\0';
  uint8_t cs = (uint8_t)strtoul(star + 1, nullptr, 16);
  if (cs != xorSum(body)) return;   // checksum salah -> frame dibuang

  lastLinkMs = millis();
  linkSeen = true;

  char* save = nullptr;
  char* type = strtok_r(body, ",", &save);
  char* seqS = strtok_r(nullptr, ",", &save);
  char* args = strtok_r(nullptr, ",", &save);
  if (!type || !seqS) return;
  unsigned int seq = (unsigned int)atoi(seqS);

  char out[80];

  if (strcmp(type, "PING") == 0) {
    snprintf(out, sizeof(out), "PONG,%u,up=%lu;fan=%d", seq, millis() / 1000UL, fanOn ? 1 : 0);
    sendFrame(out);
    return;
  }

  if (strcmp(type, "FAN") == 0) {
    if (!args) {
      snprintf(out, sizeof(out), "ERR,%u,bad_args", seq);
    } else {
      setFan(atoi(args) != 0);
      snprintf(out, sizeof(out), "ACK,%u,fan=%d", seq, fanOn ? 1 : 0);
    }
    sendFrame(out);
    return;
  }

  if (strcmp(type, "GET") == 0) {
    bool all = (!args || strcmp(args, "ALL") == 0);
    bool wantAQ = all, wantMH = all;
    if (!all) {
      char* s2 = nullptr;
      for (char* f = strtok_r(args, ";", &s2); f; f = strtok_r(nullptr, ";", &s2)) {
        if (strcmp(f, "AQ") == 0) wantAQ = true;
        else if (strcmp(f, "MH") == 0) wantMH = true;
      }
    }
    if (!wantAQ && !wantMH) {
      snprintf(out, sizeof(out), "ERR,%u,bad_fields", seq);
      sendFrame(out);
      return;
    }

    char kv[60] = "";
    if (wantAQ) {
      appendKV(kv, sizeof(kv), "aqr", (long)(aqEma < 0 ? 0 : aqEma + 0.5f));
      appendKV(kv, sizeof(kv), "aql", aqLevel());
      appendKV(kv, sizeof(kv), "aqd", (aqReady() && aqTrig) ? 1 : 0);
    }
    if (wantMH) {
      appendKV(kv, sizeof(kv), "mhr", (long)(mhEma < 0 ? 0 : mhEma + 0.5f));
      appendKV(kv, sizeof(kv), "mhd", mhTrig ? 1 : 0);
    }
    if (all) appendKV(kv, sizeof(kv), "fan", fanOn ? 1 : 0);
    snprintf(out, sizeof(out), "DAT,%u,%s", seq, kv);
    sendFrame(out);
    return;
  }

  snprintf(out, sizeof(out), "ERR,%u,unknown_cmd", seq);
  sendFrame(out);
}

void pollLink() {
  while (LINK.available()) {
    char c = (char)LINK.read();
    if (c == '$') { rxLen = 0; inFrame = true; continue; }
    if (!inFrame) continue;
    if (c == '\n' || c == '\r') {
      if (rxLen) { rxBuf[rxLen] = '\0'; handleFrame(rxBuf); }
      inFrame = false;
      rxLen = 0;
    } else if (rxLen < sizeof(rxBuf) - 1) {
      rxBuf[rxLen++] = c;
    } else {
      inFrame = false;   // frame terlalu panjang -> buang
      rxLen = 0;
    }
  }
}

// ================================== LCD =====================================
uint8_t probeLcd() {
  const uint8_t addrs[] = {0x27, 0x3F};
  for (uint8_t i = 0; i < sizeof(addrs); i++) {
    Wire.beginTransmission(addrs[i]);
    if (Wire.endTransmission() == 0) return addrs[i];
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
  bool linkOk = linkSeen && (millis() - lastLinkMs < LINK_OK_MS);

  char l0[24], l1[24];
  snprintf(l0, sizeof(l0), "AQ%4d %-6s F%c", (int)(aqEma < 0 ? 0 : aqEma),
           aqLevelText(aqLevel()), fanOn ? '+' : '-');
  snprintf(l1, sizeof(l1), "MH%4d %c  LNK:%s", (int)(mhEma < 0 ? 0 : mhEma),
           mhTrig ? '!' : '-', linkOk ? "OK" : "--");
  lcdLine(0, l0, lcdPrev0);
  lcdLine(1, l1, lcdPrev1);
}

// ============================== SETUP / LOOP ================================
void setup() {
  fanInit();                       // kipas OFF secepat mungkin setelah reset
  linkInit();

  pinMode(PIN_MQ_D, INPUT);
  pinMode(PIN_MH_D, INPUT);

  Wire.begin();                    // A4 = SDA, A5 = SCL
  uint8_t addr = probeLcd();
  if (addr) {
    lcd = new LiquidCrystal_I2C(addr, 16, 2);
    lcd->init();
    lcd->backlight();
    lcd->setCursor(0, 0); lcd->print(F("StudySphere"));
    lcd->setCursor(0, 1); lcd->print(F("Node sensor..."));
  }
  // Tidak ada debug Serial: UART dipakai untuk link ESP32. Status lihat di LCD.
}

void loop() {
  pollLink();

  unsigned long now = millis();
  if (now - lastSampleMs >= SAMPLE_MS) {
    lastSampleMs = now;
    sampleSensors();
  }

  if (now - lastLcdMs >= LCD_REFRESH_MS) {
    lastLcdMs = now;
    updateLcd();
  }
}
