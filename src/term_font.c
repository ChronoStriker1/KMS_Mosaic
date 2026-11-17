#include "term_font.h"
#include "font_util.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static bool term_font_load_face(term_font *f, const char *path) {
    return path && *path && FT_New_Face(f->lib, path, 0, &f->face) == 0;
}

bool term_font_open(term_font *f, const char *path) {
    memset(f, 0, sizeof(*f));
    if (FT_Init_FreeType(&f->lib)) return false;
    if (term_font_load_face(f, path)) return true;
    if (path && *path) fprintf(stderr, "term_font: cannot load %s, using fontconfig monospace\n", path);
    char *fallback = kms_font_find_monospace();
    bool ok = term_font_load_face(f, fallback);
    free(fallback);
    if (!ok) {
        FT_Done_FreeType(f->lib);
        f->lib = NULL;
    }
    return ok;
}

void term_font_close(term_font *f) {
    if (f->face) FT_Done_Face(f->face);
    if (f->lib) FT_Done_FreeType(f->lib);
    memset(f, 0, sizeof(*f));
}

static bool term_font_is_bitmap(const term_font *f) {
    return !FT_IS_SCALABLE(f->face) && f->face->num_fixed_sizes > 0;
}

static int term_font_pick_strike(const FT_Face face, int font_px) {
    int fitting = -1;
    int smallest = 0;
    for (int i = 0; i < face->num_fixed_sizes; ++i) {
        int h = face->available_sizes[i].height;
        if (h < face->available_sizes[smallest].height) smallest = i;
        if (h <= font_px && (fitting < 0 || h > face->available_sizes[fitting].height)) fitting = i;
    }
    return fitting >= 0 ? fitting : smallest;
}

bool term_font_set_px(term_font *f, int font_px) {
    if (font_px <= 0) font_px = 18;
    if (term_font_is_bitmap(f)) {
        int strike = term_font_pick_strike(f->face, font_px);
        if (FT_Select_Size(f->face, strike) || FT_Load_Char(f->face, 'M', FT_LOAD_DEFAULT)) return false;
        int native_h = f->face->available_sizes[strike].height;
        f->scale = font_px / native_h > 1 ? font_px / native_h : 1;
        f->cell_w = (int)(f->face->glyph->advance.x >> 6) * f->scale;
        f->cell_h = native_h * f->scale;
        f->baseline = (int)(f->face->size->metrics.ascender >> 6) * f->scale;
        return f->cell_w > 0;
    }
    if (FT_Set_Pixel_Sizes(f->face, 0, (FT_UInt)font_px) || FT_Load_Char(f->face, 'M', FT_LOAD_DEFAULT)) return false;
    f->scale = 0;
    f->cell_w = (int)((f->face->glyph->advance.x + 31) / 64);
    f->cell_h = font_px + 2;
    f->baseline = (int)((f->face->size->metrics.ascender + 31) / 64);
    return f->cell_w > 0;
}

static unsigned char term_font_bitmap_alpha(const FT_Bitmap *bm, int x, int y) {
    const unsigned char *row = bm->buffer + (ptrdiff_t)y * bm->pitch;
    if (bm->pixel_mode == FT_PIXEL_MODE_MONO) return (row[x >> 3] & (0x80 >> (x & 7))) ? 255 : 0;
    return row[x];
}

bool term_font_render(term_font *f, uint32_t cp, term_glyph *out) {
    memset(out, 0, sizeof(*out));
    int s = f->scale > 0 ? f->scale : 1;
    FT_Int32 flags = f->scale > 0 ? (FT_LOAD_RENDER | FT_LOAD_TARGET_MONO) : FT_LOAD_RENDER;
    if (FT_Load_Char(f->face, cp, flags)) return false;
    FT_GlyphSlot slot = f->face->glyph;
    const FT_Bitmap *bm = &slot->bitmap;
    if (bm->pixel_mode != FT_PIXEL_MODE_MONO && bm->pixel_mode != FT_PIXEL_MODE_GRAY) return false;
    out->w = (int)bm->width * s;
    out->h = (int)bm->rows * s;
    out->x = f->scale > 0 ? slot->bitmap_left * s : (f->cell_w - out->w) / 2 + slot->bitmap_left;
    out->y = f->baseline - slot->bitmap_top * s;
    if (out->w == 0 || out->h == 0) return true;
    out->alpha = malloc((size_t)out->w * (size_t)out->h);
    if (!out->alpha) return false;
    for (int y = 0; y < out->h; ++y) {
        for (int x = 0; x < out->w; ++x) {
            out->alpha[(size_t)y * out->w + x] = term_font_bitmap_alpha(bm, x / s, y / s);
        }
    }
    return true;
}
