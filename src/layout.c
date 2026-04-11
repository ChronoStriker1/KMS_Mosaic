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

static void split_tree_collect_roles(const split_tree_node *node, int role_count,
                                     int *out_roles, int *out_count, bool *seen) {
    if (!node || !out_roles || !out_count || !seen) return;
    if (node->leaf) {
        if (node->role >= 0 && node->role < role_count && !seen[node->role]) {
            out_roles[(*out_count)++] = node->role;
            seen[node->role] = true;
        }
        return;
    }
    split_tree_collect_roles(node->first, role_count, out_roles, out_count, seen);
    split_tree_collect_roles(node->second, role_count, out_roles, out_count, seen);
}

static void ordered_roles_from_perm(const int *perm, int role_count, int *ordered_roles) {
    int count = 0;
    for (int slot = 0; slot < role_count; ++slot) {
        for (int role = 0; role < role_count; ++role) {
            if (perm && perm[role] == slot) {
                ordered_roles[count++] = role;
                break;
            }
        }
    }
    while (count < role_count) {
        ordered_roles[count] = count;
        count++;
    }
}

static int visible_ordered_roles(const char *split_tree_spec, const int *perm,
                                 const bool *role_visible, int role_count,
                                 int *ordered_roles) {
    int ordered_count = 0;
    bool *seen = calloc((size_t)role_count, sizeof(*seen));
    if (!seen) return 0;

    if (split_tree_spec && *split_tree_spec) {
        split_tree_node *tree = NULL;
        if (split_tree_parse(split_tree_spec, &tree)) {
            split_tree_node *translated = split_tree_translate_legacy_roles(tree, role_count);
            if (translated) {
                split_tree_collect_roles(translated, role_count, ordered_roles, &ordered_count, seen);
                split_tree_destroy(translated);
            }
            split_tree_destroy(tree);
        }
    }

    if (ordered_count < role_count) {
        int *perm_roles = calloc((size_t)role_count, sizeof(*perm_roles));
        if (perm_roles) {
            ordered_roles_from_perm(perm, role_count, perm_roles);
            for (int i = 0; i < role_count; ++i) {
                int role = perm_roles[i];
                if (role >= 0 && role < role_count && !seen[role]) {
                    ordered_roles[ordered_count++] = role;
                    seen[role] = true;
                }
            }
            free(perm_roles);
        }
    }

    int visible_count = 0;
    for (int i = 0; i < ordered_count; ++i) {
        int role = ordered_roles[i];
        if (role_visible[role]) ordered_roles[visible_count++] = role;
    }
    free(seen);
    return visible_count;
}

static void build_visible_slots(int screen_w, int screen_h, int mode,
                                int split_pct, int col_pct, rotation_t rotation,
                                bool overlay_swap, int visible_count,
                                pane_layout *slots) {
    if (!slots || visible_count <= 0) return;

    pane_layout screen = { .x = 0, .y = 0, .w = screen_w, .h = screen_h };
    if (visible_count <= 1) {
        slots[0] = screen;
        return;
    }
    if (visible_count == 2) {
        if (mode == 1) split_horizontal(screen, 2, slots);
        else split_vertical(screen, 2, slots);
        return;
    }

    int pane_count = visible_count - 1;
    int role_count = visible_count;
    if (pane_count > 2) {
        pane_layout pane_area = {0};
        if (mode == 0) {
            split_vertical(screen, role_count, slots);
        } else if (mode == 1) {
            split_horizontal(screen, role_count, slots);
        } else if (mode == 2) {
            int wleft = screen_w * col_pct / 100;
            slots[0] = (pane_layout){ .x = wleft, .y = 0, .w = screen_w - wleft, .h = screen_h };
            pane_area = (pane_layout){ .x = 0, .y = 0, .w = wleft, .h = screen_h };
            tile_rects(pane_area, pane_count, &slots[1]);
        } else if (mode == 3) {
            int wleft = screen_w * col_pct / 100;
            slots[0] = (pane_layout){ .x = 0, .y = 0, .w = wleft, .h = screen_h };
            pane_area = (pane_layout){ .x = wleft, .y = 0, .w = screen_w - wleft, .h = screen_h };
            tile_rects(pane_area, pane_count, &slots[1]);
        } else if (mode == 4) {
            int htop = screen_h * split_pct / 100;
            pane_area = (pane_layout){ .x = 0, .y = screen_h - htop, .w = screen_w, .h = htop };
            slots[0] = (pane_layout){ .x = 0, .y = 0, .w = screen_w, .h = screen_h - htop };
            tile_rects(pane_area, pane_count, &slots[1]);
        } else if (mode == 5) {
            int htop = screen_h * split_pct / 100;
            slots[0] = (pane_layout){ .x = 0, .y = screen_h - htop, .w = screen_w, .h = htop };
            pane_area = (pane_layout){ .x = 0, .y = 0, .w = screen_w, .h = screen_h - htop };
            tile_rects(pane_area, pane_count, &slots[1]);
        } else {
            slots[0] = screen;
            pane_area = (pane_layout){ .x = screen_w / 10, .y = screen_h / 10, .w = screen_w * 8 / 10, .h = screen_h * 8 / 10 };
            tile_rects(pane_area, pane_count, &slots[1]);
        }
        return;
    }

    pane_layout s0 = {0}, s1 = {0}, s2 = {0};
    if (mode == 6) {
        s0 = screen;
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

    slots[0] = s0;
    slots[1] = (mode == 6 && overlay_swap) ? s2 : s1;
    slots[2] = (mode == 6 && overlay_swap) ? s1 : s2;
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
                           visibility_mode_t visibility_mode,
                           const pane_media_config *pane_media,
                           bool overlay_swap, bool fullscreen, int fs_pane,
                           mosaic_layout *out) {
    if (!out || !out->role_layouts) return;
    memset(out->role_layouts, 0, (size_t)out->role_count * sizeof(*out->role_layouts));

    int mode = layout_mode;
    int split_pct = clamp_split_pct(pane_split_pct);
    int col_pct = clamp_col_pct(right_frac_pct);
    int role_count = pane_count;
    bool *role_visible = calloc((size_t)role_count, sizeof(*role_visible));
    if (!role_visible) return;
    int visible_count = 0;
    int first_visible_role = -1;
    for (int role = 0; role < role_count; ++role) {
        bool is_media = pane_media && pane_media[role].enabled;
        role_visible[role] =
            visibility_mode == VISIBILITY_MODE_NO_VIDEO ? !is_media :
            visibility_mode == VISIBILITY_MODE_NO_TERMINAL ? is_media :
            true;
        if (role_visible[role]) {
            if (first_visible_role < 0) first_visible_role = role;
            visible_count++;
        }
    }

    if (visible_count == role_count && split_tree_spec && *split_tree_spec) {
        split_tree_node *tree = NULL;
        if (split_tree_parse(split_tree_spec, &tree)) {
            split_tree_node *translated = split_tree_translate_legacy_roles(tree, role_count);
            if (translated) {
                split_tree_apply(translated,
                                 (pane_layout){ .x = 0, .y = 0, .w = screen_w, .h = screen_h },
                                 out->role_layouts, role_count);
                if (fullscreen) {
                    pane_layout full = { .x = 0, .y = 0, .w = screen_w, .h = screen_h };
                    int target_role = fs_pane >= 0 && fs_pane < role_count ? fs_pane : KMS_MOSAIC_SLOT_PANE_A;
                    if (role_visible[target_role]) out->role_layouts[target_role] = full;
                }
                split_tree_destroy(translated);
                split_tree_destroy(tree);
                free(role_visible);
                return;
            }
            split_tree_destroy(tree);
        }
    }

    if (visible_count > 0) {
        int *ordered_roles = calloc((size_t)role_count, sizeof(*ordered_roles));
        pane_layout *slots = calloc((size_t)visible_count, sizeof(*slots));
        if (!ordered_roles || !slots) {
            free(ordered_roles);
            free(slots);
            free(role_visible);
            return;
        }
        int ordered_visible_count = visible_ordered_roles(split_tree_spec, perm, role_visible, role_count, ordered_roles);
        build_visible_slots(screen_w, screen_h, mode, split_pct, col_pct, rotation, overlay_swap, ordered_visible_count, slots);
        for (int index = 0; index < ordered_visible_count; ++index) {
            int role = ordered_roles[index];
            out->role_layouts[role] = slots[index];
        }
        free(ordered_roles);
        free(slots);
    }

    if (fullscreen) {
        pane_layout full = { .x = 0, .y = 0, .w = screen_w, .h = screen_h };
        int target_role = fs_pane >= 0 && fs_pane < role_count ? fs_pane : first_visible_role;
        if (target_role >= 0 && target_role < role_count && role_visible[target_role]) {
            out->role_layouts[target_role] = full;
        }
    }
    free(role_visible);
}
