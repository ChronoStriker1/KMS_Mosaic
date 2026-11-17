#include "edid.h"

#include <string.h>

static const uint8_t EDID_HEADER[8] = {0x00, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0x00};

bool edid_monitor_name(const uint8_t *edid, size_t len, char out[EDID_MONITOR_NAME_MAX]) {
    out[0] = '\0';
    if (!edid || len < 128 || memcmp(edid, EDID_HEADER, sizeof(EDID_HEADER)) != 0) return false;
    for (size_t off = 54; off <= 108; off += 18) {
        const uint8_t *d = edid + off;
        if (d[0] != 0 || d[1] != 0 || d[2] != 0 || d[3] != 0xfc) continue;
        size_t n = 0;
        for (size_t i = 5; i < 18 && d[i] != 0x0a; ++i) {
            uint8_t ch = d[i];
            out[n++] = (ch >= 0x20 && ch < 0x7f && ch != '"') ? (char)ch : '?';
        }
        while (n > 0 && out[n - 1] == ' ') --n;
        out[n] = '\0';
        return n > 0;
    }
    return false;
}
