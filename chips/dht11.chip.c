// DHT11 temperature & humidity module (3 pins, 10k pull-up on DATA).
// The MCU pulls DATA low for >= 18 ms and releases it. The sensor answers 80 us low + 80 us high,
// then 40 bits, each 50 us low followed by ~27 us (0) or 70 us (1) high: humidity, humidity
// decimal, temperature, temperature decimal, checksum. Works with the Adafruit DHT and DHTesp
// libraries. Range like the real part: 0-50 C, 20-90 %RH.
#include "wokwi-api.h"
#include <stdlib.h>

#define SEGMENTS (2 + 80 + 1)

typedef struct {
  pin_t data;
  uint32_t temp_attr, hum_attr;
  timer_t timer;
  uint64_t low_since;
  uint32_t durations[SEGMENTS];  // us, alternating low/high, starting low
  int step;
  bool sending;
} chip_state_t;

static void next(void *user_data) {
  chip_state_t *c = user_data;
  if (++c->step == SEGMENTS) {
    c->sending = false;
    pin_mode(c->data, INPUT_PULLUP);
    return;
  }
  pin_mode(c->data, c->step % 2 ? OUTPUT_HIGH : OUTPUT_LOW);
  timer_start(c->timer, c->durations[c->step], false);
}

static void answer(chip_state_t *c) {
  float t = attr_read_float(c->temp_attr);
  float h = attr_read_float(c->hum_attr);
  int t10 = (int)(t * 10 + 0.5f);
  int h10 = (int)(h * 10 + 0.5f);
  uint8_t b[5] = {h10 / 10, h10 % 10, t10 / 10, t10 % 10, 0};
  b[4] = b[0] + b[1] + b[2] + b[3];
  c->durations[0] = 80;
  c->durations[1] = 80;
  for (int i = 0; i < 40; i++) {
    int bit = (b[i / 8] >> (7 - i % 8)) & 1;
    c->durations[2 + i * 2] = 50;
    c->durations[3 + i * 2] = bit ? 70 : 27;
  }
  c->durations[SEGMENTS - 1] = 50;
  c->sending = true;
  c->step = -1;
  timer_start(c->timer, 30, false);  // the sensor waits 20-40 us before answering
}

static void on_change(void *user_data, pin_t pin, uint32_t value) {
  chip_state_t *c = user_data;
  if (c->sending) return;  // our own edges
  if (!value) {
    c->low_since = get_sim_nanos();
  } else if (get_sim_nanos() - c->low_since >= 10000000ULL) {  // >= 10 ms low: start signal
    answer(c);
  }
}

void chip_init(void) {
  chip_state_t *c = calloc(1, sizeof(chip_state_t));
  c->temp_attr = attr_init_float("temperature", 25);
  c->hum_attr = attr_init_float("humidity", 50);
  c->data = pin_init("DATA", INPUT_PULLUP);
  pin_init("VCC", INPUT);
  pin_init("GND", INPUT);
  const timer_config_t timer = {.callback = next, .user_data = c};
  c->timer = timer_init(&timer);
  const pin_watch_config_t watch = {.edge = BOTH, .pin_change = on_change, .user_data = c};
  pin_watch(c->data, &watch);
}
