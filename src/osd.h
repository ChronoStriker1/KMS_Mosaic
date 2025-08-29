#pragma once
#include <stdbool.h>

typedef struct osd_ctx osd_ctx;

// Create OSD with given pixel font size
osd_ctx* osd_create(int font_px);
void osd_destroy(osd_ctx* o);

// Set text content to display (UTF-8)
void osd_set_text(osd_ctx* o, const char *text);

// Draw at pixel position (x,y) in the logical render target of size (fb_w, fb_h)
void osd_draw(osd_ctx* o, int x, int y, int fb_w, int fb_h);

