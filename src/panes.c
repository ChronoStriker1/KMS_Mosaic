#include "panes.h"

#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

term_pane *panes_get_term(const pane_runtime *panes, int slot) {
    if (!panes || slot < 0 || slot >= panes->count) return NULL;
    return panes->tp[slot];
}

bool panes_init_runtime(pane_runtime *panes, int pane_count) {
    memset(panes, 0, sizeof(*panes));
    panes->count = pane_count;
    panes->tp = calloc((size_t)pane_count, sizeof(*panes->tp));
    panes->last_font_px = malloc((size_t)pane_count * sizeof(*panes->last_font_px));
    panes->prev = calloc((size_t)pane_count, sizeof(*panes->prev));
    if (!panes->tp || !panes->last_font_px || !panes->prev) {
        panes_destroy(panes);
        return false;
    }
    for (int i = 0; i < pane_count; ++i) panes->last_font_px[i] = -1;
    return true;
}

static const char *panes_slot_name(int slot) {
    switch (slot) {
        case PANE_SLOT_A: return "A";
        case PANE_SLOT_B: return "B";
        case PANE_SLOT_C: return "C";
        case PANE_SLOT_D: return "D";
        default: return "?";
    }
}

static int panes_fit_font_px(int requested, const pane_layout *lay, int min_cols, int min_rows,
                             int *cell_w, int *cell_h) {
    int font_px = requested;
    *cell_w = 8;
    *cell_h = 16;
    term_measure_cell(font_px, cell_w, cell_h);
    for (int px = font_px; px >= 10; --px) {
        int cw, ch;
        if (!term_measure_cell(px, &cw, &ch)) break;
        int cols_fit = lay->w / cw;
        int rows_fit = lay->h / ch;
        if (cols_fit >= min_cols && rows_fit >= min_rows) {
            font_px = px;
            *cell_w = cw;
            *cell_h = ch;
            break;
        }
        if (px == 10) {
            font_px = px;
            *cell_w = cw;
            *cell_h = ch;
        }
    }
    return font_px;
}

void panes_compute_font_sizes(const options_t *opt, const pane_layout *layouts,
                              int pane_count, int *font_sizes) {
    int base_font = opt->font_px ? opt->font_px : 18;

    for (int i = 0; i < pane_count; ++i) {
        int cell_w = 0;
        int cell_h = 0;
        int requested = (i == 0) ? base_font : font_sizes[i - 1];
        int min_cols = (i == 0) ? 80 : (i == 1 ? 60 : 50);
        int min_rows = (i == 0) ? 24 : (i == 1 ? 20 : 16);
        font_sizes[i] = panes_fit_font_px(requested, &layouts[i], min_cols, min_rows, &cell_w, &cell_h);
    }
}

void panes_create(pane_runtime *panes, const options_t *opt, const pane_layout *layouts, bool debug) {
    int *font_sizes = calloc((size_t)panes->count, sizeof(*font_sizes));
    if (!font_sizes) return;
    panes->count = opt->pane_count;

    panes_compute_font_sizes(opt, layouts, panes->count, font_sizes);
    for (int i = 0; i < panes->count; ++i) {
        if (opt->pane_media && opt->pane_media[i].enabled) {
            panes->last_font_px[i] = font_sizes[i];
            panes->prev[i] = layouts[i];
            continue;
        }
        int cell_w = 8;
        int cell_h = 16;
        term_measure_cell(font_sizes[i], &cell_w, &cell_h);
        if (debug) {
            fprintf(stderr, "Pane %s using font_px=%d (cell=%dx%d), pane_px=%dx%d gives ~%dx%d chars\n",
                    panes_slot_name(i), font_sizes[i], cell_w, cell_h,
                    layouts[i].w, layouts[i].h, layouts[i].w / cell_w, layouts[i].h / cell_h);
        }

        if (opt->pane_cmds[i]) {
            panes->tp[i] = term_pane_create_cmd(&layouts[i], font_sizes[i], opt->pane_cmds[i]);
        } else if (i == PANE_SLOT_A) {
            char *argv_a[] = { "btop", "--utf-force", NULL };
            panes->tp[i] = term_pane_create(&layouts[i], font_sizes[i], "btop", argv_a);
        } else if (i == PANE_SLOT_B) {
            char *argv_b[6];
            const char *cmd_b = NULL;
            if (access("/var/log/syslog", R_OK) == 0) {
                cmd_b = "tail";
                argv_b[0] = "tail"; argv_b[1] = "-F"; argv_b[2] = "/var/log/syslog"; argv_b[3] = "-n"; argv_b[4] = "500"; argv_b[5] = NULL;
            } else if (access("/usr/bin/journalctl", X_OK) == 0) {
                cmd_b = "journalctl";
                argv_b[0] = "journalctl"; argv_b[1] = "-f"; argv_b[2] = NULL; argv_b[3] = NULL; argv_b[4] = NULL; argv_b[5] = NULL;
            } else {
                cmd_b = "tail";
                argv_b[0] = "tail"; argv_b[1] = "-F"; argv_b[2] = "/var/log/messages"; argv_b[3] = "-n"; argv_b[4] = "500"; argv_b[5] = NULL;
            }
            panes->tp[i] = term_pane_create(&layouts[i], font_sizes[i], cmd_b, argv_b);
        } else {
            char *argv_top[] = { "btop", "--utf-force", NULL };
            panes->tp[i] = term_pane_create(&layouts[i], font_sizes[i], "btop", argv_top);
        }

        panes->last_font_px[i] = font_sizes[i];
        panes->prev[i] = layouts[i];
    }
    free(font_sizes);
    panes_apply_layout_mode_alpha(opt, panes);
}

void panes_apply_layout_mode_alpha(const options_t *opt, pane_runtime *panes) {
    uint8_t alpha = (opt->layout_mode == 6) ? 192 : 255;
    for (int i = 0; i < panes->count; ++i) {
        if (panes->tp[i]) term_pane_set_alpha(panes->tp[i], alpha);
    }
}

void panes_sync_layout(pane_runtime *panes, const pane_layout *layouts,
                       int pane_count, const int *font_sizes) {
    panes->count = pane_count;
    for (int i = 0; i < pane_count; ++i) {
        if (!panes->tp[i]) continue;
        if (panes->last_font_px[i] != font_sizes[i]) {
            term_pane_set_font_px(panes->tp[i], font_sizes[i]);
            panes->last_font_px[i] = font_sizes[i];
        }
        if (panes->prev[i].x != layouts[i].x || panes->prev[i].y != layouts[i].y ||
            panes->prev[i].w != layouts[i].w || panes->prev[i].h != layouts[i].h) {
            term_pane_resize(panes->tp[i], &layouts[i]);
            panes->prev[i] = layouts[i];
        }
    }
}

void panes_destroy(pane_runtime *panes) {
    if (!panes) return;
    for (int i = 0; i < panes->count; ++i) {
        term_pane_destroy(panes->tp[i]);
    }
    free(panes->tp);
    free(panes->last_font_px);
    free(panes->prev);
    panes->tp = NULL;
    panes->last_font_px = NULL;
    panes->prev = NULL;
    panes->count = 0;
}
