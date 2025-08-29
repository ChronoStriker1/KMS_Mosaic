// xterm 256-color palette mapping to RGB
#pragma once
#include <stdint.h>

typedef struct { uint8_t r, g, b; } rgb8;

static inline rgb8 color_from_index(int idx) {
    // Standard 0-15 ANSI
    static const rgb8 ansi16[16] = {
        {0,0,0},{128,0,0},{0,128,0},{128,128,0},{0,0,128},{128,0,128},{0,128,128},{192,192,192},
        {128,128,128},{255,0,0},{0,255,0},{255,255,0},{0,0,255},{255,0,255},{0,255,255},{255,255,255}
    };
    if (idx < 0) idx = 7;
    if (idx < 16) return ansi16[idx];
    if (idx >= 16 && idx < 232) {
        int c = idx - 16;
        int r = c / 36; c %= 36;
        int g = c / 6;  c %= 6;
        int b = c;
        rgb8 out = {
            .r = (uint8_t)(r ? 55 + r * 40 : 0),
            .g = (uint8_t)(g ? 55 + g * 40 : 0),
            .b = (uint8_t)(b ? 55 + b * 40 : 0)
        };
        return out;
    }
    if (idx >= 232 && idx <= 255) {
        uint8_t v = (uint8_t)(8 + (idx - 232) * 10);
        return (rgb8){v,v,v};
    }
    return ansi16[7];
}

