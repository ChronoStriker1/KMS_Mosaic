#ifndef LAYOUT_H
#define LAYOUT_H

#include <stdbool.h>

#include "options.h"
#include "term_pane.h"

typedef struct {
    pane_layout *role_layouts;
    int role_count;
} mosaic_layout;

bool mosaic_layout_init(mosaic_layout *layout, int role_count);
void mosaic_layout_destroy(mosaic_layout *layout);
void compute_mosaic_layout(int screen_w, int screen_h, int layout_mode,
                           int right_frac_pct, int pane_split_pct, int pane_count,
                           rotation_t rotation, const int *perm,
                           bool overlay_swap, bool fullscreen, int fs_pane,
                           mosaic_layout *out);

#endif
