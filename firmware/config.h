// Network settings. The node talks to the MQTT broker inside the StudySphere desktop app
// (desktop/), which runs on the laptop. Only the WiFi and MQTT_HOST need changing for the real board.
#pragma once

// Uncomment for an ESP32 on the StudySphere PCB (hardware/): real ADC volts, the LDR on 3V3 and
// the MQ-135 output through a 10k/15k divider. Leave it off for the simulators.
// #define REAL_BOARD

// The simulator ignores these and uses the PC's network; the real board needs your WiFi.
#define WIFI_SSID "Wokwi-GUEST"
#define WIFI_PASS ""

#ifdef WIRELAB_SIM
// WireLab's local simulator runs the firmware on the laptop itself.
#define MQTT_HOST "127.0.0.1"
#else
// The laptop's IP on the same WiFi as the ESP32: the desktop app lists it.
#define MQTT_HOST "192.168.1.100"
#endif
#define MQTT_PORT 1883
#define MQTT_USER ""
#define MQTT_PASS ""

// Topics are TOPIC_ROOT/DEVICE_ID/...; the desktop app follows whichever DEVICE_ID it hears.
#define TOPIC_ROOT "studysphere"
#define DEVICE_ID "room-01"
