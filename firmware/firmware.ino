// StudySphere room node (UTS/UAS): a smart study room for students.
// Reads DHT11, LDR, MQ-135 and HC-SR501, runs a study lamp (LED) and a 16x2 I2C LCD, and talks to
// the desktop app over MQTT. Topics are TOPIC_ROOT/DEVICE_ID/... (see config.h and README.md):
//   telemetry  every 5 s and when occupancy or the lamp changes: every reading
//   request    a semantic selector: {"task":"comfort_check","required_information":["temperature"]}
//   response   its answer:          {"task":"comfort_check","data":{"temperature":25}}
//   command    {"lamp":"on"|"off"|"auto"} and/or {"lcd":"a message for the screen"}
//   status     "online", or "offline" (retained; "offline" is the MQTT last will)
// Requests and commands also work over Serial, one JSON line each.
#include <ArduinoJson.h>
#include <DHT.h>
#include <LiquidCrystal_I2C.h>
#include <PubSubClient.h>
#include <WiFi.h>
#include "config.h"

const int DHT_PIN = 4, PIR_PIN = 27, LDR_PIN = 34, MQ_PIN = 35, LAMP_PIN = 13;  // LCD: SDA 21, SCL 22
const unsigned long IDLE_MS = 60000;      // no motion for this long ends the study session
const unsigned long TELEMETRY_MS = 5000;
const unsigned long MESSAGE_MS = 30000;   // how long a message from the desktop stays on the LCD
const float DARK_LUX = 150;               // auto lamp: on while someone is there and it's darker than this
const float CO2_STALE = 1000;             // ppm; above this the room needs fresh air

// Calibration knobs. The defaults match the simulator; tune them against the real modules.
const float LDR_GAMMA = 0.7, LDR_RL10 = 50;                   // RL10: kOhm at 10 lux
const float MQ_VCC = 5.0, MQ_RLOAD = 10.0, MQ_RZERO = 76.63;  // kOhm; RZERO from clean-air calibration

DHT dht(DHT_PIN, DHT11);
LiquidCrystal_I2C lcd(0x27, 16, 2);
WiFiClient net;
PubSubClient mqtt(net);
const String base = String(TOPIC_ROOT) + "/" + DEVICE_ID + "/";
const char *const ALL[] = {"temperature", "humidity", "lighting", "co2", "air_quality", "occupancy", "session_duration", "lamp", "lamp_mode"};

float temperature = NAN, humidity = NAN, lighting = 0, co2 = 0;
bool present = false, lamp = false, wifiUp = false, mqttUp = false;
String lampMode = "auto", message, serialLine;
unsigned long sessionStart, lastMotion, lastRead, lastTelemetry, lastLcd, lastAttempt, messageUntil;

#ifdef REAL_BOARD
// The StudySphere PCB (hardware/): the LDR module runs on 3V3 and the MQ-135 output reaches IO35
// through a 10k/15k divider, so the pin sees 3/5 of it.
const float LDR_VCC = 3.3, MQ_DIVIDER = 25.0 / 15;
float volts(int pin) { return analogReadMilliVolts(pin) / 1000.0; }
#else
// Wokwi's ESP32 ADC maps 0-5 V to 0-4095, and the modules hang straight off the pins.
const float LDR_VCC = 5.0, MQ_DIVIDER = 1;
float volts(int pin) { return analogRead(pin) * 5.0 / 4095; }
#endif

void readSensors() {
  temperature = dht.readTemperature();  // NAN when the read fails
  humidity = dht.readHumidity();
  float v = volts(LDR_PIN);
  float r = 10000 * v / (LDR_VCC - v);  // the LDR is the low side of a 10k divider
  lighting = pow(LDR_RL10 * 1e3 * pow(10, LDR_GAMMA) / r, 1 / LDR_GAMMA);
  if (!isfinite(lighting)) lighting = 100000;
  v = volts(MQ_PIN) * MQ_DIVIDER;
  co2 = 116.6020682 * pow(MQ_RLOAD * (MQ_VCC - v) / v / MQ_RZERO, -2.769034857);
}

float round1(float x) { return round(x * 10) / 10; }

// One piece of information by its semantic name; false if the name is unknown.
bool describe(JsonObject data, const String &name) {
  if (name == "temperature") data[name] = round1(temperature);  // NAN becomes null
  else if (name == "humidity") data[name] = round1(humidity);
  else if (name == "lighting") data[name] = lround(lighting);
  else if (name == "co2") data[name] = lround(co2);
  else if (name == "air_quality") data[name] = co2 > CO2_STALE ? "stale" : "good";
  else if (name == "occupancy") data[name] = present;
  else if (name == "session_duration") data[name] = present ? (millis() - sessionStart) / 1000 : 0;
  else if (name == "lamp") data[name] = lamp ? "on" : "off";
  else if (name == "lamp_mode") data[name] = lampMode;
  else return false;
  return true;
}

void publish(const char *topic, const String &json) {
  Serial.println(json);
  if (mqtt.connected()) mqtt.publish((base + topic).c_str(), json.c_str());
}

void sendTelemetry() {
  JsonDocument doc;
  JsonObject data = doc.to<JsonObject>();
  for (const char *name : ALL) describe(data, name);
  String out;
  serializeJson(doc, out);
  publish("telemetry", out);
  lastTelemetry = millis();
}

bool updatePresence(unsigned long now) {
  bool was = present;
  if (digitalRead(PIR_PIN)) {
    if (!present) sessionStart = now;
    present = true;
    lastMotion = now;
  } else if (present && now - lastMotion > IDLE_MS) {
    present = false;
  }
  return present != was;
}

bool updateLamp() {
  bool on = lampMode == "on" || (lampMode == "auto" && present && lighting < DARK_LUX);
  if (on == lamp) return false;
  lamp = on;
  digitalWrite(LAMP_PIN, on);
  return true;
}

void showLcd() {
  char top[17], bottom[17];
  if (millis() < messageUntil) {  // a message from the desktop app, up to two lines
    snprintf(top, sizeof top, "%-16s", message.substring(0, 16).c_str());
    snprintf(bottom, sizeof bottom, "%-16s", message.substring(16, 32).c_str());
  } else {
    long lux = lround(min(lighting, 9999.0f));
    if (isnan(temperature)) snprintf(top, sizeof top, "--.-C --%% %4ldlx", lux);
    else snprintf(top, sizeof top, "%4.1fC %2.0f%% %4ldlx", temperature, humidity, lux);
    const char *link = mqtt.connected() ? "MQTT" : WiFi.status() == WL_CONNECTED ? "WIFI" : "----";
    snprintf(bottom, sizeof bottom, "CO2 %4ldppm %s", lround(min(co2, 9999.0f)), link);
  }
  lcd.setCursor(0, 0);
  lcd.print(top);
  lcd.setCursor(0, 1);
  lcd.print(bottom);
}

// The same JSON arrives over MQTT or Serial: a semantic request gets a response, a command
// changes the lamp or the LCD.
void handle(const char *json, size_t len) {
  JsonDocument in;
  if (deserializeJson(in, json, len)) return publish("response", "{\"error\":\"invalid json\"}");
  if (in["required_information"].is<JsonArray>()) {
    JsonDocument res;
    res["task"] = in["task"];
    JsonObject data = res["data"].to<JsonObject>();
    for (JsonVariant v : in["required_information"].as<JsonArray>()) {
      String name = v.as<String>();
      if (!describe(data, name)) res["unknown"].add(name);
    }
    String out;
    serializeJson(res, out);
    publish("response", out);
  }
  String mode = in["lamp"] | "";
  if (mode == "on" || mode == "off" || mode == "auto") {
    lampMode = mode;
    updateLamp();
    sendTelemetry();
  }
  if (in["lcd"].is<const char *>()) {
    message = in["lcd"].as<String>();
    messageUntil = millis() + MESSAGE_MS;
    showLcd();
  }
}

void onMessage(char *topic, byte *payload, unsigned int len) { handle((const char *)payload, len); }

// Non-blocking: the sensors, lamp and LCD keep working while WiFi or the broker is down.
void connectMqtt(unsigned long now) {
  if (mqttUp && !mqtt.connected()) Serial.printf("MQTT connection lost (state %d)\n", mqtt.state());
  mqttUp = mqtt.connected();
  if (WiFi.status() != WL_CONNECTED || mqttUp || now - lastAttempt < 5000) return;
  lastAttempt = now;
  String id = String(DEVICE_ID) + "-" + String((uint32_t)ESP.getEfuseMac(), HEX);  // unique per board
  const char *user = *MQTT_USER ? MQTT_USER : nullptr;
  if (mqtt.connect(id.c_str(), user, MQTT_PASS, (base + "status").c_str(), 1, true, "offline")) {
    mqtt.publish((base + "status").c_str(), "online", true);
    mqtt.subscribe((base + "request").c_str());
    mqtt.subscribe((base + "command").c_str());
    Serial.println("MQTT connected to " MQTT_HOST ", topics " + base + "...");
    mqttUp = true;
    sendTelemetry();
  } else {
    Serial.printf("MQTT connect failed (state %d), retrying\n", mqtt.state());
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(PIR_PIN, INPUT);
  pinMode(LAMP_PIN, OUTPUT);
  dht.begin();
  lcd.init();
  lcd.backlight();
  lcd.print("StudySphere");
  lcd.setCursor(0, 1);
  lcd.print("connecting...");
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS, strcmp(WIFI_SSID, "Wokwi-GUEST") ? 0 : 6);  // the simulator's AP is on channel 6
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onMessage);
  mqtt.setBufferSize(1024);
  mqtt.setSocketTimeout(5);
  mqtt.setKeepAlive(60);  // seconds; tolerant of a slow link (or a paused simulator)
  readSensors();
}

void loop() {
  unsigned long now = millis();
  bool changed = updatePresence(now);
  if (now - lastRead >= 2000) {  // the DHT11 allows about one read per second
    lastRead = now;
    readSensors();
  }
  changed |= updateLamp();

  if (WiFi.status() == WL_CONNECTED && !wifiUp) Serial.println("WiFi connected, IP " + WiFi.localIP().toString());
  wifiUp = WiFi.status() == WL_CONNECTED;
  connectMqtt(now);
  mqtt.loop();

  if (changed || now - lastTelemetry >= TELEMETRY_MS) sendTelemetry();
  if (now - lastLcd >= 1000) {
    lastLcd = now;
    showLcd();
  }
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      serialLine.trim();
      if (serialLine.length()) handle(serialLine.c_str(), serialLine.length());
      serialLine = "";
    } else if (serialLine.length() < 512) {
      serialLine += c;
    }
  }
}
