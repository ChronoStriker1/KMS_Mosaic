#include "font_util.h"
#include <fontconfig/fontconfig.h>
#include <stdlib.h>
#include <string.h>

char *find_monospace_font(void) {
    FcInit();
    FcPattern *pat = FcNameParse((const FcChar8*)"monospace");
    FcConfigSubstitute(NULL, pat, FcMatchPattern);
    FcDefaultSubstitute(pat);
    FcResult res;
    FcPattern *match = FcFontMatch(NULL, pat, &res);
    FcPatternDestroy(pat);
    if (!match)
        return NULL;
    FcChar8 *file = NULL;
    char *out = NULL;
    if (FcPatternGetString(match, FC_FILE, 0, &file) == FcResultMatch)
        out = strdup((const char*)file);
    FcPatternDestroy(match);
    return out;
}

int font_util_init(FT_Library *ftlib, FT_Face *face, int px_size) {
    if (FT_Init_FreeType(ftlib))
        return -1;
    char *path = find_monospace_font();
    if (!path)
        return -1;
    int err = FT_New_Face(*ftlib, path, 0, face);
    free(path);
    if (err)
        return -1;
    FT_Set_Pixel_Sizes(*face, 0, px_size);
    return 0;
}

void font_util_destroy(FT_Library ftlib, FT_Face face) {
    if (face)
        FT_Done_Face(face);
    if (ftlib)
        FT_Done_FreeType(ftlib);
}
