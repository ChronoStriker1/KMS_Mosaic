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

void ui_state_init(ui_state *ui, const options_t *opt, bool use_mpv) {
    memset(ui, 0, sizeof(*ui));
    ui->focus = use_mpv ? 0 : 1;
    ui->show_osd = false;
    ui->perm[0] = 0; ui->perm[1] = 1; ui->perm[2] = 2;
    ui->last_perm[0] = 0; ui->last_perm[1] = 1; ui->last_perm[2] = 2;
    ui->last_layout_mode = -1;
    ui->last_right_frac_pct = -1;
    ui->last_pane_split_pct = -1;
    if (opt->roles_set) {
        ui->perm[0] = opt->roles[0];
        ui->perm[1] = opt->roles[1];
        ui->perm[2] = opt->roles[2];
        if (opt->layout_mode == 6) ui->overlay_swap = (opt->roles[1] == 2 && opt->roles[2] == 1);
    }
    if (opt->layout_mode == 6) {
        ui->perm[0] = 0;
        if (!opt->roles_set) { ui->perm[1] = 1; ui->perm[2] = 2; ui->overlay_swap = false; }
        ui->last_overlay_swap = ui->overlay_swap;
    }
}

void ui_update_fs_cycle(ui_state *ui, int fs_cycle_sec, double now_sec) {
    if (!ui->fs_cycle) return;
    if (ui->fs_next_switch == 0.0) ui->fs_next_switch = now_sec + (fs_cycle_sec > 0 ? fs_cycle_sec : 5);
    else if (now_sec >= ui->fs_next_switch) {
        ui->fs_pane = (ui->fs_pane + 1) % 3;
        ui->fs_next_switch = now_sec + (fs_cycle_sec > 0 ? fs_cycle_sec : 5);
        ui->fullscreen = true;
    }
}

bool ui_handle_input(ui_state *ui, options_t *opt, const char *buf, ssize_t n,
                     bool use_mpv, term_pane *tp_a, term_pane *tp_b,
                     mpv_handle *mpv, bool *running, bool debug) {
    for (ssize_t i = 0; i < n; i++) {
        unsigned char ch = (unsigned char)buf[i];
        if (ch == 0x05) ui->ui_control = !ui->ui_control;
        if (ch == 0x11) { *running = false; return true; }
    }

    bool consumed = false;
    if (ui->ui_control) {
        for (ssize_t i = 0; i < n; i++) if (buf[i] == '\t') {
            ui->focus = use_mpv ? (ui->focus + 1) % 3 : (ui->focus == 1 ? 2 : 1);
            consumed = true;
        }
    }
    for (ssize_t i = 0; i < n; i++) if (ui->ui_control) {
        if (buf[i] == 'l') { opt->layout_mode = (opt->layout_mode + 1) % 7; consumed = true; }
        else if (buf[i] == 'L') { opt->layout_mode = (opt->layout_mode + 6) % 7; consumed = true; }
        else if (buf[i] == 't') {
            if (opt->layout_mode == 6) {
                if (ui->focus == 1 || ui->focus == 2) {
                    ui->overlay_swap = !ui->overlay_swap;
                    opt->roles_set = true;
                    opt->roles[0] = 0;
                    opt->roles[1] = ui->overlay_swap ? 2 : 1;
                    opt->roles[2] = ui->overlay_swap ? 1 : 2;
                }
            } else {
                int next = use_mpv ? (ui->focus + 1) % 3 : (ui->focus == 1 ? 2 : 1);
                int tmp = ui->perm[ui->focus];
                ui->perm[ui->focus] = ui->perm[next];
                ui->perm[next] = tmp;
                opt->roles_set = true;
                opt->roles[0] = ui->perm[0];
                opt->roles[1] = ui->perm[1];
                opt->roles[2] = ui->perm[2];
            }
            consumed = true;
        } else if (buf[i] == 'r') {
            int p0 = ui->perm[0], p1 = ui->perm[1], p2 = ui->perm[2];
            ui->perm[0] = p1; ui->perm[1] = p2; ui->perm[2] = p0;
            opt->roles_set = true; opt->roles[0] = ui->perm[0]; opt->roles[1] = ui->perm[1]; opt->roles[2] = ui->perm[2];
            consumed = true;
        } else if (buf[i] == 'R') {
            int p0 = ui->perm[0], p1 = ui->perm[1], p2 = ui->perm[2];
            ui->perm[0] = p2; ui->perm[1] = p0; ui->perm[2] = p1;
            opt->roles_set = true; opt->roles[0] = ui->perm[0]; opt->roles[1] = ui->perm[1]; opt->roles[2] = ui->perm[2];
            consumed = true;
        } else if (buf[i] == 'z') {
            ui->fullscreen = !ui->fullscreen;
            if (ui->fullscreen) { ui->fs_pane = ui->focus; ui->fs_cycle = false; }
            consumed = true;
        } else if (buf[i] == 'n' && ui->fullscreen) {
            ui->fs_pane = (ui->fs_pane + 1) % 3; ui->focus = ui->fs_pane; ui->fs_cycle = false; consumed = true;
        } else if (buf[i] == 'p' && ui->fullscreen) {
            ui->fs_pane = (ui->fs_pane + 2) % 3; ui->focus = ui->fs_pane; ui->fs_cycle = false; consumed = true;
        } else if (buf[i] == 'c') {
            ui->fs_cycle = !ui->fs_cycle;
            if (ui->fs_cycle) { ui->fullscreen = true; ui->fs_pane = ui->focus; ui->fs_next_switch = 0.0; }
            else ui->fullscreen = false;
            consumed = true;
        } else if (buf[i] == 'f') {
            if (tp_a) term_pane_force_rebuild(tp_a);
            if (tp_b) term_pane_force_rebuild(tp_b);
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

    if (mpv) {
        for (ssize_t i = 0; i < n; i++) {
            if ((unsigned char)buf[i] == 0x10) {
                double cur = 0.0;
                mpv_get_property(mpv, "panscan", MPV_FORMAT_DOUBLE, &cur);
                double target = cur > 0.0 ? 0.0 : (opt->panscan ? atof(opt->panscan) : 1.0);
                char cmd[64];
                snprintf(cmd, sizeof(cmd), "set panscan %f", target);
                mpv_command_string(mpv, cmd);
                consumed = true;
                break;
            }
        }
    }

    if (!consumed && !ui->ui_control) {
        if (ui->focus == 1 && tp_a) term_pane_send_input(tp_a, buf, (size_t)n);
        else if (ui->focus == 2 && tp_b) term_pane_send_input(tp_b, buf, (size_t)n);
        else if (ui->focus == 0 && mpv) ui_mpv_send_keys(mpv, buf, n);
    }

    if (debug) fprintf(stderr, "Input: focus=%d ui_control=%d consumed=%d bytes=%zd\n", ui->focus, ui->ui_control ? 1 : 0, consumed ? 1 : 0, n);
    return consumed;
}
