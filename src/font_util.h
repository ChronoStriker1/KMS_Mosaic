#pragma once
#include <ft2build.h>
#include FT_FREETYPE_H

char *find_monospace_font(void);
int font_util_init(FT_Library *ftlib, FT_Face *face, int px_size);
void font_util_destroy(FT_Library ftlib, FT_Face face);
