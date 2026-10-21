// MQ-135 module (5 V). AO follows the common MQ135 CO2 curve
// ppm = 116.602 * (Rs/R0)^-2.769 through a 10k load resistor.
// DO is the LM393 output: LOW when AO exceeds attrs.threshold (volts),
// standing in for the module's trim pot.
#include "wokwi-api.h"
#include <math.h>
#include <stdlib.h>

#define VCC 5.0f
#define RLOAD 10.0f  // kOhm
#define RZERO 76.63f // kOhm, sensor resistance in clean air

typedef struct {
  pin_t ao, do_;
  uint32_t ppm_attr, threshold_attr;
} chip_state_t;

static void update(void *user_data) {
  chip_state_t *c = user_data;
  float ppm = attr_read_float(c->ppm_attr);
  float rs = RZERO * powf(ppm / 116.6020682f, -1.0f / 2.769034857f);
  float v = VCC * RLOAD / (RLOAD + rs);
  pin_dac_write(c->ao, v);
  pin_write(c->do_, v < attr_read_float(c->threshold_attr));
}

void chip_init(void) {
  chip_state_t *c = malloc(sizeof(chip_state_t));
  c->ppm_attr = attr_init_float("ppm", 400);
  c->threshold_attr = attr_init_float("threshold", 1.0);
  c->ao = pin_init("AO", ANALOG);
  c->do_ = pin_init("DO", OUTPUT_HIGH);
  pin_init("VCC", INPUT);
  pin_init("GND", INPUT);
  const timer_config_t cfg = {.callback = update, .user_data = c};
  timer_start(timer_init(&cfg), 50000, true);  // re-read the slider every 50 ms
  update(c);
}
