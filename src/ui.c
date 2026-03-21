#define _GNU_SOURCE

#include "ui.h"

#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void ui_mpv_send_keys(mpv_handle *mpv, const char *buf, ssize_t n) {
    for (ssize_t i = 0; i < n; ) {
        unsigned char ch = (unsigned char)buf[i];
        if (ch == 0x1b) {
            if (i + 1 >= n) { const char *c[] = {"keypress", "ESC", NULL}; mpv_command_async(mpv, 0, c); i++; continue; }
            unsigned char n1 = (unsigned char)buf[i + 1];
            if (n1 == '[') {
                if (i + 2 < n) {
                    unsigned char n2 = (unsigned char)buf[i + 2];
                    const char *name = NULL;
                    if (n2 == 'A') name = "UP";
                    else if (n2 == 'B') name = "DOWN";
                    else if (n2 == 'C') name = "RIGHT";
                    else if (n2 == 'D') name = "LEFT";
                    if (name) { const char *c[] = {"keypress", name, NULL}; mpv_command_async(mpv, 0, c); i += 3; continue; }
                    int num = 0, j = (int)i + 2;
                    while (j < n && buf[j] >= '0' && buf[j] <= '9') { num = num * 10 + (buf[j] - '0'); j++; }
                    if (j < n && buf[j] == '~') {
                        const char *name2 = NULL;
                        if (num == 1) name2 = "HOME";
                        else if (num == 2) name2 = "INS";
                        else if (num == 3) name2 = "DEL";
                        else if (num == 4) name2 = "END";
                        else if (num == 5) name2 = "PGUP";
                        else if (num == 6) name2 = "PGDWN";
                        else if (num == 15) name2 = "F5";
                        else if (num == 17) name2 = "F6";
                        else if (num == 18) name2 = "F7";
                        else if (num == 19) name2 = "F8";
                        else if (num == 20) name2 = "F9";
                        else if (num == 21) name2 = "F10";
                        else if (num == 23) name2 = "F11";
                        else if (num == 24) name2 = "F12";
                        if (name2) { const char *c[] = {"keypress", name2, NULL}; mpv_command_async(mpv, 0, c); i = j + 1; continue; }
                    }
                }
                i++;
                continue;
            } else if (n1 == 'O') {
                if (i + 2 < n) {
                    unsigned char n2 = (unsigned char)buf[i + 2];
                    const char *name = NULL;
                    if (n2 == 'P') name = "F1";
                    else if (n2 == 'Q') name = "F2";
                    else if (n2 == 'R') name = "F3";
                    else if (n2 == 'S') name = "F4";
                    if (name) { const char *c[] = {"keypress", name, NULL}; mpv_command_async(mpv, 0, c); i += 3; continue; }
                }
                i++;
                continue;
            } else {
                const char *c[] = {"keypress", "ESC", NULL}; mpv_command_async(mpv, 0, c); i++; continue;
            }
        }
        if (ch >= 32 && ch <= 126) {
            char key[2] = {(char)ch, 0};
            const char *c[] = {"keypress", key, NULL};
            mpv_command_async(mpv, 0, c);
            if (ch == ' ') { const char *c2[] = {"cycle", "pause", NULL}; mpv_command_async(mpv, 0, c2); }
            else if (ch == 'n') { const char *c3[] = {"playlist-next", NULL}; mpv_command_async(mpv, 0, c3); }
            else if (ch == 'p') { const char *c4[] = {"playlist-prev", NULL}; mpv_command_async(mpv, 0, c4); }
            i++;
            continue;
        }
        if (ch == '\r' || ch == '\n') { const char *c[] = {"keypress", "ENTER", NULL}; mpv_command_async(mpv, 0, c); i++; continue; }
        if (ch == '\t') { const char *c[] = {"keypress", "TAB", NULL}; mpv_command_async(mpv, 0, c); i++; continue; }
        if (ch == 0x7f) { const char *c[] = {"keypress", "BS", NULL}; mpv_command_async(mpv, 0, c); i++; continue; }
        i++;
    }
}

static int ui_role_count(int pane_count) {
    return pane_count;
}

static int ui_focus_count(bool use_mpv, int pane_count) {
    (void)use_mpv;
    return pane_count;
}

static int ui_next_focus(int focus, bool use_mpv, int pane_count) {
    int count = ui_focus_count(use_mpv, pane_count);
    if (count <= 0) return -1;
    if (focus < 0 || focus >= count) return 0;
    return (focus + 1) % count;
}

static term_pane *ui_focus_term(term_pane *const *panes, int pane_count, int focus) {
    int pane_index = focus;
    if (pane_index < 0 || pane_index >= pane_count) return NULL;
    return panes[pane_index];
}

static void ui_roles_from_perm(options_t *opt, const ui_state *ui) {
    opt->roles_set = true;
    for (int i = 0; i < ui->role_count; ++i) opt->roles[i] = ui->perm[i];
}

static void ui_rotate_perm(ui_state *ui, int pane_count, int delta) {
    int role_count = ui_role_count(pane_count);
    int *rotated = calloc((size_t)role_count, sizeof(*rotated));
    if (!rotated) return;

    if (delta < 0) {
        delta = role_count - ((-delta) % role_count);
    }
    delta %= role_count;
    for (int i = 0; i < role_count; ++i) {
        rotated[i] = ui->perm[(i + delta) % role_count];
    }
    for (int i = 0; i < role_count; ++i) ui->perm[i] = rotated[i];
    free(rotated);
}

static bool ui_is_pane_focus(int focus, int pane_count) {
    return focus >= 0 && focus < pane_count;
}

static bool ui_overlay_roles_swapped(const options_t *opt) {
    return opt && opt->pane_count == 2 && opt->roles_set &&
           opt->roles[0] == KMS_MOSAIC_SLOT_PANE_B &&
           opt->roles[1] == KMS_MOSAIC_SLOT_PANE_A;
}

bool ui_state_init(ui_state *ui, const options_t *opt, bool use_mpv) {
    (void)use_mpv;
    memset(ui, 0, sizeof(*ui));
    ui->role_count = ui_role_count(opt->pane_count);
    ui->perm = calloc((size_t)ui->role_count, sizeof(*ui->perm));
    ui->last_perm = calloc((size_t)ui->role_count, sizeof(*ui->last_perm));
    if (!ui->perm || !ui->last_perm) {
        ui_state_destroy(ui);
        return false;
    }
    ui->focus = opt->pane_count > 0 ? 0 : -1;
    ui->show_osd = false;
    for (int i = 0; i < ui->role_count; ++i) {
        ui->perm[i] = i;
        ui->last_perm[i] = i;
    }
    ui->last_layout_mode = -1;
    ui->last_right_frac_pct = -1;
    ui->last_pane_split_pct = -1;
    if (opt->roles_set) {
        for (int i = 0; i < ui->role_count; ++i) ui->perm[i] = opt->roles[i];
        if (opt->layout_mode == 6) ui->overlay_swap = ui_overlay_roles_swapped(opt);
    }
    if (opt->layout_mode == 6) {
        if (!opt->roles_set) {
            for (int i = 0; i < ui->role_count; ++i) ui->perm[i] = i;
            ui->overlay_swap = false;
        }
        ui->last_overlay_swap = ui->overlay_swap;
    }
    return true;
}

void ui_state_destroy(ui_state *ui) {
    if (!ui) return;
    free(ui->perm);
    free(ui->last_perm);
    ui->perm = NULL;
    ui->last_perm = NULL;
    ui->role_count = 0;
}

void ui_update_fs_cycle(ui_state *ui, int pane_count, int fs_cycle_sec, double now_sec) {
    if (!ui->fs_cycle) return;
    if (ui->fs_next_switch == 0.0) ui->fs_next_switch = now_sec + (fs_cycle_sec > 0 ? fs_cycle_sec : 5);
    else if (now_sec >= ui->fs_next_switch) {
        ui->fs_pane = (ui->fs_pane + 1) % ui_role_count(pane_count);
        ui->fs_next_switch = now_sec + (fs_cycle_sec > 0 ? fs_cycle_sec : 5);
        ui->fullscreen = true;
    }
}

bool ui_handle_input(ui_state *ui, options_t *opt, const char *buf, ssize_t n,
                     bool use_mpv, term_pane *const *panes, mpv_handle *const *pane_mpv, int pane_count,
                     mpv_handle *mpv, bool *running, bool debug) {
    (void)mpv;
    for (ssize_t i = 0; i < n; i++) {
        unsigned char ch = (unsigned char)buf[i];
        if (ch == 0x05) ui->ui_control = !ui->ui_control;
        if (ch == 0x11) { *running = false; return true; }
    }

    bool consumed = false;
    if (ui->ui_control) {
        for (ssize_t i = 0; i < n; i++) if (buf[i] == '\t') {
            ui->focus = ui_next_focus(ui->focus, use_mpv, pane_count);
            consumed = true;
        }
    }
    for (ssize_t i = 0; i < n; i++) if (ui->ui_control) {
        if (buf[i] == 'l') { opt->layout_mode = (opt->layout_mode + 1) % 7; consumed = true; }
        else if (buf[i] == 'L') { opt->layout_mode = (opt->layout_mode + 6) % 7; consumed = true; }
        else if (buf[i] == 't') {
            if (opt->layout_mode == 6 && pane_count == 2) {
                if (ui_is_pane_focus(ui->focus, pane_count)) {
                    ui->overlay_swap = !ui->overlay_swap;
                    opt->roles_set = true;
                    opt->roles[0] = ui->overlay_swap ? KMS_MOSAIC_SLOT_PANE_B : KMS_MOSAIC_SLOT_PANE_A;
                    opt->roles[1] = ui->overlay_swap ? KMS_MOSAIC_SLOT_PANE_A : KMS_MOSAIC_SLOT_PANE_B;
                    ui->perm[0] = opt->roles[0];
                    ui->perm[1] = opt->roles[1];
                }
            } else {
                int next = ui_next_focus(ui->focus, use_mpv, pane_count);
                int tmp = ui->perm[ui->focus];
                ui->perm[ui->focus] = ui->perm[next];
                ui->perm[next] = tmp;
                ui_roles_from_perm(opt, ui);
            }
            consumed = true;
        } else if (buf[i] == 'r') {
            ui_rotate_perm(ui, pane_count, 1);
            ui_roles_from_perm(opt, ui);
            consumed = true;
        } else if (buf[i] == 'R') {
            ui_rotate_perm(ui, pane_count, -1);
            ui_roles_from_perm(opt, ui);
            consumed = true;
        } else if (buf[i] == 'z') {
            ui->fullscreen = !ui->fullscreen;
            if (ui->fullscreen) { ui->fs_pane = ui->focus; ui->fs_cycle = false; }
            consumed = true;
        } else if (buf[i] == 'n' && ui->fullscreen) {
            ui->fs_pane = (ui->fs_pane + 1) % ui_role_count(pane_count);
            ui->focus = ui->fs_pane;
            ui->fs_cycle = false;
            consumed = true;
        } else if (buf[i] == 'p' && ui->fullscreen) {
            ui->fs_pane = (ui->fs_pane + ui_role_count(pane_count) - 1) % ui_role_count(pane_count);
            ui->focus = ui->fs_pane;
            ui->fs_cycle = false;
            consumed = true;
        } else if (buf[i] == 'c') {
            ui->fs_cycle = !ui->fs_cycle;
            if (ui->fs_cycle) { ui->fullscreen = true; ui->fs_pane = ui->focus; ui->fs_next_switch = 0.0; }
            else ui->fullscreen = false;
            consumed = true;
        } else if (buf[i] == 'f') {
            for (int p = 0; p < pane_count; ++p) if (panes[p]) term_pane_force_rebuild(panes[p]);
            consumed = true;
        } else if (buf[i] == 's') {
            const char *p = opt->config_file ? opt->config_file : default_config_path();
            save_config(opt, p);
            fprintf(stderr, "Saved config to %s\n", p);
            consumed = true;
        } else if (buf[i] == 'o') {
            ui->show_osd = !ui->show_osd;
            consumed = true;
        }
    }

    if (ui->ui_control) {
        for (ssize_t i = 0; i + 2 < n; i++) {
            if ((unsigned char)buf[i] == 0x1b && buf[i + 1] == '[') {
                unsigned char k = (unsigned char)buf[i + 2];
                int step = 2;
                if (k == 'C' && opt->layout_mode >= 2 && opt->layout_mode <= 5) {
                    int rf = opt->right_frac_pct ? opt->right_frac_pct : 33;
                    rf += step; if (rf > 80) rf = 80; if (rf < 20) rf = 20; opt->right_frac_pct = rf; consumed = true;
                } else if (k == 'D' && opt->layout_mode >= 2 && opt->layout_mode <= 5) {
                    int rf = opt->right_frac_pct ? opt->right_frac_pct : 33;
                    rf -= step; if (rf < 20) rf = 20; if (rf > 80) rf = 80; opt->right_frac_pct = rf; consumed = true;
                } else if (k == 'A' && opt->layout_mode >= 2 && opt->layout_mode <= 6) {
                    int sp = opt->pane_split_pct ? opt->pane_split_pct : 50;
                    sp += step; if (sp > 90) sp = 90; if (sp < 10) sp = 10; opt->pane_split_pct = sp; consumed = true;
                } else if (k == 'B' && opt->layout_mode >= 2 && opt->layout_mode <= 6) {
                    int sp = opt->pane_split_pct ? opt->pane_split_pct : 50;
                    sp -= step; if (sp < 10) sp = 10; if (sp > 90) sp = 90; opt->pane_split_pct = sp; consumed = true;
                }
            }
        }
    }

    mpv_handle *focused_mpv = NULL;
    if (pane_mpv && ui->focus >= 0 && ui->focus < pane_count) focused_mpv = pane_mpv[ui->focus];

    if (focused_mpv) {
        for (ssize_t i = 0; i < n; i++) {
            if ((unsigned char)buf[i] == 0x10) {
                double cur = 0.0;
                mpv_get_property(focused_mpv, "panscan", MPV_FORMAT_DOUBLE, &cur);
                double target = cur > 0.0 ? 0.0 : (opt->panscan ? atof(opt->panscan) : 1.0);
                char cmd[64];
                snprintf(cmd, sizeof(cmd), "set panscan %f", target);
                mpv_command_string(focused_mpv, cmd);
                consumed = true;
                break;
            }
        }
    }

    if (!consumed && !ui->ui_control) {
        term_pane *focused = ui_focus_term(panes, pane_count, ui->focus);
        if (focused) term_pane_send_input(focused, buf, (size_t)n);
        else if (pane_mpv && ui->focus >= 0 && pane_mpv[ui->focus]) ui_mpv_send_keys(pane_mpv[ui->focus], buf, n);
    }

    if (debug) fprintf(stderr, "Input: focus=%d ui_control=%d consumed=%d bytes=%zd\n", ui->focus, ui->ui_control ? 1 : 0, consumed ? 1 : 0, n);
    return consumed;
}
