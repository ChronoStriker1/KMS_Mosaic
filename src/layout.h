#ifndef LAYOUT_H
#define LAYOUT_H

#include <stdbool.h>

#include "options.h"
#include "term_pane.h"

typedef struct {
    pane_layout video;
    pane_layout pane_a;
    pane_layout pane_b;
} mosaic_layout;

void compute_mosaic_layout(int screen_w, int screen_h, int layout_mode,
                           int right_frac_pct, int pane_split_pct,
                           rotation_t rotation, const int perm[3],
                           bool overlay_swap, bool fullscreen, int fs_pane,
                           mosaic_layout *out);

#endif
