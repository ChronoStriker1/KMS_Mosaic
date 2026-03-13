#include "layout.h"

#include <stdlib.h>
#include <string.h>

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

static void tile_rects(pane_layout area, int count, pane_layout *out) {
    if (count <= 0) return;
    int cols = 1;
    while (cols * cols < count) cols++;
    int rows = (count + cols - 1) / cols;
    int y = area.y;
    for (int r = 0, idx = 0; r < rows; ++r) {
        int cells_left = count - idx;
        int row_cols = cells_left < cols ? cells_left : cols;
        int row_h = (r == rows - 1) ? (area.y + area.h - y) : area.h / rows;
        int x = area.x;
        for (int c = 0; c < row_cols; ++c, ++idx) {
            int cell_w = (c == row_cols - 1) ? (area.x + area.w - x) : area.w / row_cols;
            out[idx] = (pane_layout){ .x = x, .y = y, .w = cell_w, .h = row_h };
            x += cell_w;
        }
        y += row_h;
    }
}

static void split_vertical(pane_layout area, int count, pane_layout *out) {
    int y = area.y;
    for (int i = 0; i < count; ++i) {
        int h = (i == count - 1) ? (area.y + area.h - y) : area.h / count;
        out[i] = (pane_layout){ .x = area.x, .y = y, .w = area.w, .h = h };
        y += h;
    }
}

static void split_horizontal(pane_layout area, int count, pane_layout *out) {
    int x = area.x;
    for (int i = 0; i < count; ++i) {
        int w = (i == count - 1) ? (area.x + area.w - x) : area.w / count;
        out[i] = (pane_layout){ .x = x, .y = area.y, .w = w, .h = area.h };
        x += w;
    }
}

bool mosaic_layout_init(mosaic_layout *layout, int role_count) {
    memset(layout, 0, sizeof(*layout));
    layout->role_layouts = calloc((size_t)role_count, sizeof(*layout->role_layouts));
    layout->role_count = layout->role_layouts ? role_count : 0;
    return layout->role_layouts != NULL;
}

void mosaic_layout_destroy(mosaic_layout *layout) {
    if (!layout) return;
    free(layout->role_layouts);
    layout->role_layouts = NULL;
    layout->role_count = 0;
}

void compute_mosaic_layout(int screen_w, int screen_h, int layout_mode,
                           int right_frac_pct, int pane_split_pct, int pane_count,
                           rotation_t rotation, const int *perm,
                           bool overlay_swap, bool fullscreen, int fs_pane,
                           mosaic_layout *out) {
    if (!out || !out->role_layouts) return;
    memset(out->role_layouts, 0, (size_t)out->role_count * sizeof(*out->role_layouts));

    int mode = layout_mode;
    int split_pct = clamp_split_pct(pane_split_pct);
    int col_pct = clamp_col_pct(right_frac_pct);
    int role_count = KMS_MOSAIC_SLOT_PANE_BASE + pane_count;
    pane_layout s0 = {0}, s1 = {0}, s2 = {0};

    if (pane_count > 2) {
        pane_layout *slots = calloc((size_t)role_count, sizeof(*slots));
        pane_layout pane_area = {0};
        if (!slots) return;

        if (mode == 0) {
            split_vertical((pane_layout){ .x = 0, .y = 0, .w = screen_w, .h = screen_h }, role_count, slots);
        } else if (mode == 1) {
            split_horizontal((pane_layout){ .x = 0, .y = 0, .w = screen_w, .h = screen_h }, role_count, slots);
        } else if (mode == 2) {
            int wleft = screen_w * col_pct / 100;
            slots[KMS_MOSAIC_SLOT_VIDEO] = (pane_layout){ .x = wleft, .y = 0, .w = screen_w - wleft, .h = screen_h };
            pane_area = (pane_layout){ .x = 0, .y = 0, .w = wleft, .h = screen_h };
            tile_rects(pane_area, pane_count, &slots[KMS_MOSAIC_SLOT_PANE_BASE]);
        } else if (mode == 3) {
            int wleft = screen_w * col_pct / 100;
            slots[KMS_MOSAIC_SLOT_VIDEO] = (pane_layout){ .x = 0, .y = 0, .w = wleft, .h = screen_h };
            pane_area = (pane_layout){ .x = wleft, .y = 0, .w = screen_w - wleft, .h = screen_h };
            tile_rects(pane_area, pane_count, &slots[KMS_MOSAIC_SLOT_PANE_BASE]);
        } else if (mode == 4) {
            int htop = screen_h * split_pct / 100;
            pane_area = (pane_layout){ .x = 0, .y = screen_h - htop, .w = screen_w, .h = htop };
            slots[KMS_MOSAIC_SLOT_VIDEO] = (pane_layout){ .x = 0, .y = 0, .w = screen_w, .h = screen_h - htop };
            tile_rects(pane_area, pane_count, &slots[KMS_MOSAIC_SLOT_PANE_BASE]);
        } else if (mode == 5) {
            int htop = screen_h * split_pct / 100;
            slots[KMS_MOSAIC_SLOT_VIDEO] = (pane_layout){ .x = 0, .y = screen_h - htop, .w = screen_w, .h = htop };
            pane_area = (pane_layout){ .x = 0, .y = 0, .w = screen_w, .h = screen_h - htop };
            tile_rects(pane_area, pane_count, &slots[KMS_MOSAIC_SLOT_PANE_BASE]);
        } else {
            slots[KMS_MOSAIC_SLOT_VIDEO] = (pane_layout){ .x = 0, .y = 0, .w = screen_w, .h = screen_h };
            pane_area = (pane_layout){ .x = screen_w / 10, .y = screen_h / 10, .w = screen_w * 8 / 10, .h = screen_h * 8 / 10 };
            tile_rects(pane_area, pane_count, &slots[KMS_MOSAIC_SLOT_PANE_BASE]);
        }

        for (int role = 0; role < role_count; ++role) out->role_layouts[role] = slots[perm[role]];
        if (fullscreen) {
            pane_layout full = { .x = 0, .y = 0, .w = screen_w, .h = screen_h };
            out->role_layouts[fs_pane >= 0 && fs_pane < role_count ? fs_pane : KMS_MOSAIC_SLOT_VIDEO] = full;
        }
        free(slots);
        return;
    }

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
        out->role_layouts[KMS_MOSAIC_SLOT_VIDEO] = s0;
        out->role_layouts[KMS_MOSAIC_SLOT_PANE_A] = overlay_swap ? s2 : s1;
        out->role_layouts[KMS_MOSAIC_SLOT_PANE_B] = overlay_swap ? s1 : s2;
    } else {
        pane_layout slots[3] = { s0, s1, s2 };
        for (int role = 0; role < role_count; ++role) {
            out->role_layouts[role] = slots[perm[role]];
        }
    }

    if (fullscreen) {
        pane_layout full = { .x = 0, .y = 0, .w = screen_w, .h = screen_h };
        out->role_layouts[fs_pane >= 0 && fs_pane < role_count ? fs_pane : KMS_MOSAIC_SLOT_VIDEO] = full;
    }
}
