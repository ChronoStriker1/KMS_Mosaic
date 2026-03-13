#define _POSIX_C_SOURCE 200809L
#include "font_util.h"

#include <stdlib.h>
#include <string.h>

#include <fontconfig/fontconfig.h>

char *kms_font_find_monospace(void) {
    if (!FcInit()) {
        return NULL;
    }

    FcPattern *pat = FcNameParse((const FcChar8 *)"monospace");
    if (!pat) {
        return NULL;
    }

    FcConfigSubstitute(NULL, pat, FcMatchPattern);
    FcDefaultSubstitute(pat);

    FcResult res;
    FcPattern *match = FcFontMatch(NULL, pat, &res);
    FcPatternDestroy(pat);
    if (!match) {
        return NULL;
    }

    FcChar8 *file = NULL;
    char *out = NULL;
    if (FcPatternGetString(match, FC_FILE, 0, &file) == FcResultMatch && file) {
        out = strdup((const char *)file);
    }

    FcPatternDestroy(match);
    return out;
}
