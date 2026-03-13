#ifndef FONT_UTIL_H
#define FONT_UTIL_H

// Helper to locate a monospace font via fontconfig.
// Returns a malloc'd path string that the caller must free, or NULL on failure.
char *kms_font_find_monospace(void);

#endif // FONT_UTIL_H
