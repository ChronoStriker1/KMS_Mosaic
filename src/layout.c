#include "layout.h"

#include <ctype.h>
#include <stdlib.h>
#include <string.h>

typedef struct split_tree_node {
    bool leaf;
    int role;
    bool split_rows;
    int pct;
    struct split_tree_node *first;
    struct split_tree_node *second;
} split_tree_node;

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

static void split_tree_destroy(split_tree_node *node) {
    if (!node) return;
    split_tree_destroy(node->first);
    split_tree_destroy(node->second);
    free(node);
}

static void split_tree_skip_ws(const char **sp) {
    while (sp && *sp && **sp && isspace((unsigned char)**sp)) (*sp)++;
}

static bool split_tree_parse_int(const char **sp, int *out) {
    char *end = NULL;
    long value;
    split_tree_skip_ws(sp);
    if (!sp || !*sp || !**sp) return false;
    value = strtol(*sp, &end, 10);
    if (end == *sp) return false;
    *sp = end;
    *out = (int)value;
    return true;
}

static split_tree_node *split_tree_parse_node(const char **sp) {
    split_tree_node *node = NULL;
    int value = 0;

    split_tree_skip_ws(sp);
    if (!sp || !*sp || !**sp) return NULL;

    if (isdigit((unsigned char)**sp)) {
        if (!split_tree_parse_int(sp, &value)) return NULL;
        node = calloc(1, sizeof(*node));
        if (!node) return NULL;
        node->leaf = true;
        node->role = value;
        return node;
    }

    if (!strncmp(*sp, "row", 3)) {
        *sp += 3;
        node = calloc(1, sizeof(*node));
        if (!node) return NULL;
        node->split_rows = true;
    } else if (!strncmp(*sp, "col", 3)) {
        *sp += 3;
        node = calloc(1, sizeof(*node));
        if (!node) return NULL;
        node->split_rows = false;
    } else {
        return NULL;
    }

    split_tree_skip_ws(sp);
    if (**sp != ':') goto fail;
    (*sp)++;
    if (!split_tree_parse_int(sp, &value)) goto fail;
    node->pct = clamp_split_pct(value);

    split_tree_skip_ws(sp);
    if (**sp != '(') goto fail;
    (*sp)++;
    node->first = split_tree_parse_node(sp);
    if (!node->first) goto fail;
    split_tree_skip_ws(sp);
    if (**sp != ',') goto fail;
    (*sp)++;
    node->second = split_tree_parse_node(sp);
    if (!node->second) goto fail;
    split_tree_skip_ws(sp);
    if (**sp != ')') goto fail;
    (*sp)++;
    return node;

fail:
    split_tree_destroy(node);
    return NULL;
}

static bool split_tree_parse(const char *spec, split_tree_node **out_node) {
    const char *p = spec;
    split_tree_node *node = NULL;
    if (!spec || !*spec || !out_node) return false;
    node = split_tree_parse_node(&p);
    if (!node) return false;
    split_tree_skip_ws(&p);
    if (*p != '\0') {
        split_tree_destroy(node);
        return false;
    }
    *out_node = node;
    return true;
}

static int split_tree_translate_legacy_role(int role, int pane_count) {
    if (role < 0 || role >= pane_count) return -1;
    return role;
}

static split_tree_node *split_tree_translate_legacy_roles(const split_tree_node *node, int pane_count) {
    split_tree_node *copy = NULL;
    if (!node) return NULL;
    copy = calloc(1, sizeof(*copy));
    if (!copy) return NULL;
    copy->leaf = node->leaf;
    copy->split_rows = node->split_rows;
    copy->pct = node->pct;
    if (node->leaf) {
        copy->role = split_tree_translate_legacy_role(node->role, pane_count);
        if (copy->role < 0) {
            free(copy);
            return NULL;
        }
        return copy;
    }
    copy->first = split_tree_translate_legacy_roles(node->first, pane_count);
    copy->second = split_tree_translate_legacy_roles(node->second, pane_count);
    if (!copy->first || !copy->second) {
        split_tree_destroy(copy);
        return NULL;
    }
    return copy;
}

static void split_tree_apply(const split_tree_node *node, pane_layout area,
                             pane_layout *role_layouts, int role_count) {
    if (!node) return;
    if (node->leaf) {
        if (node->role >= 0 && node->role < role_count) role_layouts[node->role] = area;
        return;
    }

    if (node->split_rows) {
        int first_h = area.h * clamp_split_pct(node->pct) / 100;
        pane_layout first = { .x = area.x, .y = area.y, .w = area.w, .h = first_h };
        pane_layout second = { .x = area.x, .y = area.y + first_h, .w = area.w, .h = area.h - first_h };
        split_tree_apply(node->first, first, role_layouts, role_count);
        split_tree_apply(node->second, second, role_layouts, role_count);
    } else {
        int first_w = area.w * clamp_split_pct(node->pct) / 100;
        pane_layout first = { .x = area.x, .y = area.y, .w = first_w, .h = area.h };
        pane_layout second = { .x = area.x + first_w, .y = area.y, .w = area.w - first_w, .h = area.h };
        split_tree_apply(node->first, first, role_layouts, role_count);
        split_tree_apply(node->second, second, role_layouts, role_count);
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
                           const char *split_tree_spec,
                           rotation_t rotation, const int *perm,
                           bool overlay_swap, bool fullscreen, int fs_pane,
                           mosaic_layout *out) {
    if (!out || !out->role_layouts) return;
    memset(out->role_layouts, 0, (size_t)out->role_count * sizeof(*out->role_layouts));

    int mode = layout_mode;
    int split_pct = clamp_split_pct(pane_split_pct);
    int col_pct = clamp_col_pct(right_frac_pct);
    int role_count = pane_count;
    pane_layout s0 = {0}, s1 = {0}, s2 = {0};

    if (split_tree_spec && *split_tree_spec) {
        split_tree_node *tree = NULL;
        if (split_tree_parse(split_tree_spec, &tree)) {
            split_tree_node *translated = split_tree_translate_legacy_roles(tree, role_count);
            if (translated) {
                split_tree_apply(translated,
                                 (pane_layout){ .x = 0, .y = 0, .w = screen_w, .h = screen_h },
                                 out->role_layouts, role_count);
                if (fullscreen) {
                    pane_layout full = { .x = 0, .y = 0, .w = screen_w, .h = screen_h };
                    out->role_layouts[fs_pane >= 0 && fs_pane < role_count ? fs_pane : KMS_MOSAIC_SLOT_PANE_A] = full;
                }
                split_tree_destroy(translated);
                split_tree_destroy(tree);
                return;
            }
            split_tree_destroy(tree);
        }
    }

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
            slots[KMS_MOSAIC_SLOT_PANE_A] =
                (pane_layout){ .x = wleft, .y = 0, .w = screen_w - wleft, .h = screen_h };
            pane_area = (pane_layout){ .x = 0, .y = 0, .w = wleft, .h = screen_h };
            tile_rects(pane_area, pane_count - 1, &slots[KMS_MOSAIC_SLOT_PANE_B]);
        } else if (mode == 3) {
            int wleft = screen_w * col_pct / 100;
            slots[KMS_MOSAIC_SLOT_PANE_A] = (pane_layout){ .x = 0, .y = 0, .w = wleft, .h = screen_h };
            pane_area = (pane_layout){ .x = wleft, .y = 0, .w = screen_w - wleft, .h = screen_h };
            tile_rects(pane_area, pane_count - 1, &slots[KMS_MOSAIC_SLOT_PANE_B]);
        } else if (mode == 4) {
            int htop = screen_h * split_pct / 100;
            pane_area = (pane_layout){ .x = 0, .y = screen_h - htop, .w = screen_w, .h = htop };
            slots[KMS_MOSAIC_SLOT_PANE_A] =
                (pane_layout){ .x = 0, .y = 0, .w = screen_w, .h = screen_h - htop };
            tile_rects(pane_area, pane_count - 1, &slots[KMS_MOSAIC_SLOT_PANE_B]);
        } else if (mode == 5) {
            int htop = screen_h * split_pct / 100;
            slots[KMS_MOSAIC_SLOT_PANE_A] =
                (pane_layout){ .x = 0, .y = screen_h - htop, .w = screen_w, .h = htop };
            pane_area = (pane_layout){ .x = 0, .y = 0, .w = screen_w, .h = screen_h - htop };
            tile_rects(pane_area, pane_count - 1, &slots[KMS_MOSAIC_SLOT_PANE_B]);
        } else {
            slots[KMS_MOSAIC_SLOT_PANE_A] = (pane_layout){ .x = 0, .y = 0, .w = screen_w, .h = screen_h };
            pane_area = (pane_layout){ .x = screen_w / 10, .y = screen_h / 10, .w = screen_w * 8 / 10, .h = screen_h * 8 / 10 };
            tile_rects(pane_area, pane_count - 1, &slots[KMS_MOSAIC_SLOT_PANE_B]);
        }

        for (int role = 0; role < role_count; ++role) out->role_layouts[role] = slots[perm[role]];
        if (fullscreen) {
            pane_layout full = { .x = 0, .y = 0, .w = screen_w, .h = screen_h };
            out->role_layouts[fs_pane >= 0 && fs_pane < role_count ? fs_pane : KMS_MOSAIC_SLOT_PANE_A] = full;
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
        out->role_layouts[KMS_MOSAIC_SLOT_PANE_A] = s0;
        if (role_count > 1) out->role_layouts[KMS_MOSAIC_SLOT_PANE_B] = overlay_swap ? s2 : s1;
    } else {
        pane_layout slots[3] = { s0, s1, s2 };
        for (int role = 0; role < role_count; ++role) {
            out->role_layouts[role] = slots[perm[role]];
        }
    }

    if (fullscreen) {
        pane_layout full = { .x = 0, .y = 0, .w = screen_w, .h = screen_h };
        out->role_layouts[fs_pane >= 0 && fs_pane < role_count ? fs_pane : KMS_MOSAIC_SLOT_PANE_A] = full;
    }
}
