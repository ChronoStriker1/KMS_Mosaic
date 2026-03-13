#include "panes.h"

#include <stdio.h>
#include <string.h>
#include <unistd.h>

void panes_init_runtime(pane_runtime *panes) {
    memset(panes, 0, sizeof(*panes));
    panes->last_font_px_a = -1;
    panes->last_font_px_b = -1;
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

void panes_compute_font_sizes(const options_t *opt, const pane_layout *lay_a, const pane_layout *lay_b,
                              int *font_px_a, int *font_px_b) {
    int cell_w = 0;
    int cell_h = 0;
    *font_px_a = panes_fit_font_px(opt->font_px ? opt->font_px : 18, lay_a, 80, 24, &cell_w, &cell_h);
    *font_px_b = panes_fit_font_px(opt->font_px ? opt->font_px : *font_px_a, lay_b, 60, 20, &cell_w, &cell_h);
}

void panes_create(pane_runtime *panes, const options_t *opt, const pane_layout *lay_a, const pane_layout *lay_b, bool debug) {
    int font_px_a = 0;
    int font_px_b = 0;
    int cell_w_a = 8;
    int cell_h_a = 16;
    panes_compute_font_sizes(opt, lay_a, lay_b, &font_px_a, &font_px_b);
    term_measure_cell(font_px_a, &cell_w_a, &cell_h_a);
    if (debug) {
        fprintf(stderr, "Pane A min 80x24 -> using font_px=%d (cell=%dx%d), pane_px=%dx%d gives ~%dx%d chars\n",
                font_px_a, cell_w_a, cell_h_a, lay_a->w, lay_a->h, lay_a->w / cell_w_a, lay_a->h / cell_h_a);
    }
    if (opt->pane_a_cmd) panes->tp_a = term_pane_create_cmd(lay_a, font_px_a, opt->pane_a_cmd);
    else {
        char *argv_a[] = { "btop", "--utf-force", NULL };
        panes->tp_a = term_pane_create(lay_a, font_px_a, "btop", argv_a);
    }
    if (opt->pane_b_cmd) panes->tp_b = term_pane_create_cmd(lay_b, font_px_b, opt->pane_b_cmd);
    else {
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
        panes->tp_b = term_pane_create(lay_b, font_px_b, cmd_b, argv_b);
    }
    panes->last_font_px_a = font_px_a;
    panes->last_font_px_b = font_px_b;
    panes->prev_a = *lay_a;
    panes->prev_b = *lay_b;
    panes_apply_layout_mode_alpha(opt, panes);
}

void panes_apply_layout_mode_alpha(const options_t *opt, pane_runtime *panes) {
    if (!panes->tp_a || !panes->tp_b) return;
    if (opt->layout_mode == 6) {
        term_pane_set_alpha(panes->tp_a, 192);
        term_pane_set_alpha(panes->tp_b, 192);
    } else {
        term_pane_set_alpha(panes->tp_a, 255);
        term_pane_set_alpha(panes->tp_b, 255);
    }
}

void panes_sync_layout(pane_runtime *panes, const pane_layout *lay_a, const pane_layout *lay_b,
                       int font_px_a, int font_px_b) {
    if (panes->last_font_px_a != font_px_a) {
        term_pane_set_font_px(panes->tp_a, font_px_a);
        panes->last_font_px_a = font_px_a;
    }
    if (panes->prev_a.x != lay_a->x || panes->prev_a.y != lay_a->y ||
        panes->prev_a.w != lay_a->w || panes->prev_a.h != lay_a->h) {
        term_pane_resize(panes->tp_a, lay_a);
        panes->prev_a = *lay_a;
    }
    if (panes->last_font_px_b != font_px_b) {
        term_pane_set_font_px(panes->tp_b, font_px_b);
        panes->last_font_px_b = font_px_b;
    }
    if (panes->prev_b.x != lay_b->x || panes->prev_b.y != lay_b->y ||
        panes->prev_b.w != lay_b->w || panes->prev_b.h != lay_b->h) {
        term_pane_resize(panes->tp_b, lay_b);
        panes->prev_b = *lay_b;
    }
}

void panes_destroy(pane_runtime *panes) {
    if (!panes) return;
    term_pane_destroy(panes->tp_a);
    term_pane_destroy(panes->tp_b);
    panes->tp_a = NULL;
    panes->tp_b = NULL;
}
