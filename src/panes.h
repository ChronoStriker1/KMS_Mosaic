#ifndef PANES_H
#define PANES_H

#include <stdbool.h>

#include "options.h"
#include "term_pane.h"

typedef enum {
    PANE_SLOT_A = 0,
    PANE_SLOT_B = 1,
    PANE_SLOT_C = 2,
    PANE_SLOT_D = 3,
    PANE_SLOT_COUNT = 4
} pane_slot;

typedef struct {
    term_pane **tp;
    int *last_font_px;
    pane_layout *prev;
    int count;
} pane_runtime;

term_pane *panes_get_term(const pane_runtime *panes, int slot);
bool panes_init_runtime(pane_runtime *panes, int pane_count);
void panes_compute_font_sizes(const options_t *opt, const pane_layout *layouts,
                              int pane_count, int *font_sizes);
void panes_create(pane_runtime *panes, const options_t *opt, const pane_layout *layouts, bool debug);
void panes_apply_layout_mode_alpha(const options_t *opt, pane_runtime *panes);
void panes_sync_layout(pane_runtime *panes, const pane_layout *layouts,
                       int pane_count, const int *font_sizes);
void panes_destroy(pane_runtime *panes);

#endif
