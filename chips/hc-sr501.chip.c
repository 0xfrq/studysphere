// HC-SR501 PIR. Same behavior as Wokwi's built-in PIR, but the trigger is a
// control ("motion"), so automation (MCP, scenarios) can drive it.
// attrs: delayTime (s OUT stays high), inhibitTime (s blind after OUT drops),
// retrigger (1 = jumper H: motion keeps OUT high, 0 = jumper L).
#include "wokwi-api.h"
#include <stdlib.h>

#define TICK_US 50000

typedef struct {
  pin_t out;
  uint32_t motion_attr, delay_attr, inhibit_attr, retrigger_attr;
  float now, off_at, blind_until;
  bool high;
} chip_state_t;

static void tick(void *user_data) {
  chip_state_t *c = user_data;
  bool motion = attr_read_float(c->motion_attr) >= 0.5f;
  c->now += TICK_US / 1e6f;
  if (c->high) {
    if (motion && attr_read(c->retrigger_attr)) c->off_at = c->now + attr_read_float(c->delay_attr);
    if (c->now >= c->off_at) {
      c->high = false;
      c->blind_until = c->now + attr_read_float(c->inhibit_attr);
    }
  } else if (motion && c->now >= c->blind_until) {
    c->high = true;
    c->off_at = c->now + attr_read_float(c->delay_attr);
  }
  pin_write(c->out, c->high);
}

void chip_init(void) {
  chip_state_t *c = calloc(1, sizeof(chip_state_t));
  c->motion_attr = attr_init_float("motion", 0);
  c->delay_attr = attr_init_float("delayTime", 5);
  c->inhibit_attr = attr_init_float("inhibitTime", 1.2);
  c->retrigger_attr = attr_init("retrigger", 1);
  c->out = pin_init("OUT", OUTPUT_LOW);
  pin_init("VCC", INPUT);
  pin_init("GND", INPUT);
  const timer_config_t cfg = {.callback = tick, .user_data = c};
  timer_start(timer_init(&cfg), TICK_US, true);
}
