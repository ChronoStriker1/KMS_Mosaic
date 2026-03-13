#include "layout.h"

static int clamp_split_pct(int split_pct) {
    if (split_pct <= 0) split_pct = 50;
    if (split_pct < 10) split_pct = 10;
    if (split_pct > 90) split_pct = 90;
    return split_pct;
}

static int clamp_col_pct(int right_frac_pct) {
    int col_pct = right_frac_pct ? (100 - right_frac_pct) : 50;
    if (col_pct < 20) col_pct = 20;
    if (col_pct > 80) col_pct = 80;
    return col_pct;
}

void compute_mosaic_layout(int screen_w, int screen_h, int layout_mode,
                           int right_frac_pct, int pane_split_pct,
                           rotation_t rotation, const int perm[3],
                           bool overlay_swap, bool fullscreen, int fs_pane,
                           mosaic_layout *out) {
    if (!out) return;

    int mode = layout_mode;
    int split_pct = clamp_split_pct(pane_split_pct);
    int col_pct = clamp_col_pct(right_frac_pct);
    pane_layout s0 = {0}, s1 = {0}, s2 = {0};

    if (mode == 6) {
        s0 = (pane_layout){ .x = 0, .y = 0, .w = screen_w, .h = screen_h };
        if (rotation == ROT_0 || rotation == ROT_180) {
            int wleft = screen_w * split_pct / 100;
            int wright = screen_w - wleft;
            s1 = (pane_layout){ .x = 0, .y = 0, .w = wleft, .h = screen_h };
            s2 = (pane_layout){ .x = wleft, .y = 0, .w = wright, .h = screen_h };
        } else {
            int htop = screen_h * split_pct / 100;
            int hbot = screen_h - htop;
            s1 = (pane_layout){ .x = 0, .y = screen_h - htop, .w = screen_w, .h = htop };
            s2 = (pane_layout){ .x = 0, .y = 0, .w = screen_w, .h = hbot };
        }
    } else if (mode == 0) {
        int h = screen_h / 3;
        int h2 = h;
        int h3 = screen_h - h - h2;
        s0 = (pane_layout){ .x = 0, .y = screen_h - h, .w = screen_w, .h = h };
        s1 = (pane_layout){ .x = 0, .y = screen_h - h - h2, .w = screen_w, .h = h2 };
        s2 = (pane_layout){ .x = 0, .y = 0, .w = screen_w, .h = h3 };
    } else if (mode == 1) {
        int w = screen_w / 3;
        int w2 = w;
        int w3 = screen_w - w - w2;
        s0 = (pane_layout){ .x = 0, .y = 0, .w = w, .h = screen_h };
        s1 = (pane_layout){ .x = w, .y = 0, .w = w2, .h = screen_h };
        s2 = (pane_layout){ .x = w + w2, .y = 0, .w = w3, .h = screen_h };
    } else if (mode == 2) {
        int wleft = screen_w * col_pct / 100;
        int wright = screen_w - wleft;
        int htop = screen_h * split_pct / 100;
        int hbot = screen_h - htop;
        s0 = (pane_layout){ .x = 0, .y = screen_h - htop, .w = wleft, .h = htop };
        s1 = (pane_layout){ .x = 0, .y = 0, .w = wleft, .h = hbot };
        s2 = (pane_layout){ .x = wleft, .y = 0, .w = wright, .h = screen_h };
    } else if (mode == 3) {
        int wleft = screen_w * col_pct / 100;
        int wright = screen_w - wleft;
        int htop = screen_h * split_pct / 100;
        int hbot = screen_h - htop;
        s0 = (pane_layout){ .x = 0, .y = 0, .w = wleft, .h = screen_h };
        s1 = (pane_layout){ .x = wleft, .y = screen_h - htop, .w = wright, .h = htop };
        s2 = (pane_layout){ .x = wleft, .y = 0, .w = wright, .h = hbot };
    } else if (mode == 4) {
        int wleft = screen_w * col_pct / 100;
        int wright = screen_w - wleft;
        int htop = screen_h * split_pct / 100;
        int hbot = screen_h - htop;
        s0 = (pane_layout){ .x = 0, .y = screen_h - htop, .w = wleft, .h = htop };
        s1 = (pane_layout){ .x = wleft, .y = screen_h - htop, .w = wright, .h = htop };
        s2 = (pane_layout){ .x = 0, .y = 0, .w = screen_w, .h = hbot };
    } else {
        int wleft = screen_w * col_pct / 100;
        int wright = screen_w - wleft;
        int htop = screen_h * split_pct / 100;
        int hbot = screen_h - htop;
        s0 = (pane_layout){ .x = 0, .y = screen_h - htop, .w = screen_w, .h = htop };
        s1 = (pane_layout){ .x = 0, .y = 0, .w = wleft, .h = hbot };
        s2 = (pane_layout){ .x = wleft, .y = 0, .w = wright, .h = hbot };
    }

    if (mode == 6) {
        out->video = s0;
        out->pane_a = overlay_swap ? s2 : s1;
        out->pane_b = overlay_swap ? s1 : s2;
    } else {
        pane_layout slots[3] = { s0, s1, s2 };
        out->video = slots[perm[0]];
        out->pane_a = slots[perm[1]];
        out->pane_b = slots[perm[2]];
    }

    if (fullscreen) {
        pane_layout full = { .x = 0, .y = 0, .w = screen_w, .h = screen_h };
        if (fs_pane == 0) out->video = full;
        else if (fs_pane == 1) out->pane_a = full;
        else out->pane_b = full;
    }
}
