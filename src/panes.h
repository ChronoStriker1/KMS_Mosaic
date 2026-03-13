#ifndef PANES_H
#define PANES_H

#include <stdbool.h>

#include "options.h"
#include "term_pane.h"

typedef struct {
    term_pane *tp_a;
    term_pane *tp_b;
    int last_font_px_a;
    int last_font_px_b;
    pane_layout prev_a;
    pane_layout prev_b;
} pane_runtime;

void panes_init_runtime(pane_runtime *panes);
void panes_compute_font_sizes(const options_t *opt, const pane_layout *lay_a, const pane_layout *lay_b,
                              int *font_px_a, int *font_px_b);
void panes_create(pane_runtime *panes, const options_t *opt, const pane_layout *lay_a, const pane_layout *lay_b, bool debug);
void panes_apply_layout_mode_alpha(const options_t *opt, pane_runtime *panes);
void panes_sync_layout(pane_runtime *panes, const pane_layout *lay_a, const pane_layout *lay_b,
                       int font_px_a, int font_px_b);
void panes_destroy(pane_runtime *panes);

#endif
