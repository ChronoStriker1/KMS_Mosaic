#ifndef TERM_FONT_H
#define TERM_FONT_H

#include <stdbool.h>
#include <stdint.h>

#include <ft2build.h>
#include FT_FREETYPE_H

typedef struct {
    FT_Library lib;
    FT_Face face;
    int scale;
    int cell_w, cell_h, baseline;
} term_font;

typedef struct {
    int x, y, w, h;
    unsigned char *alpha;
} term_glyph;

bool term_font_open(term_font *f, const char *path);
bool term_font_set_px(term_font *f, int font_px);
bool term_font_render(term_font *f, uint32_t cp, term_glyph *out);
void term_font_close(term_font *f);

#endif
