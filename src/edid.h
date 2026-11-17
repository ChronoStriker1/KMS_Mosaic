#ifndef EDID_H
#define EDID_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define EDID_MONITOR_NAME_MAX 14

bool edid_monitor_name(const uint8_t *edid, size_t len, char out[EDID_MONITOR_NAME_MAX]);

#endif
