#include "ui.h"

#include <stdlib.h>
#include <string.h>

bool ui_state_init(ui_state *ui, const options_t *opt, bool use_mpv) {
    (void)use_mpv;
    memset(ui, 0, sizeof(*ui));
    ui->role_count = opt->pane_count;
    ui->perm = calloc((size_t)ui->role_count, sizeof(*ui->perm));
    ui->last_perm = calloc((size_t)ui->role_count, sizeof(*ui->last_perm));
    if (!ui->perm || !ui->last_perm) {
        ui_state_destroy(ui);
        return false;
    }

    ui->focus = opt->osd_pane >= 0 && opt->osd_pane < opt->pane_count ? opt->osd_pane : 0;
    ui->fullscreen = opt->fullscreen_pane >= 0 && opt->fullscreen_pane < opt->pane_count;
    ui->fs_pane = ui->fullscreen ? opt->fullscreen_pane : 0;
    ui->fs_cycle = opt->fullscreen_cycle;
    if (ui->fs_cycle) ui->fullscreen = true;

    for (int i = 0; i < ui->role_count; ++i) {
        ui->perm[i] = opt->roles_set ? opt->roles[i] : i;
        ui->last_perm[i] = i;
    }
    ui->last_layout_mode = -1;
    ui->last_right_frac_pct = -1;
    ui->last_pane_split_pct = -1;
    if (opt->layout_mode == 6) {
        ui->overlay_swap = opt->roles_set && opt->pane_count == 2 &&
                           opt->roles[0] == KMS_MOSAIC_SLOT_PANE_B &&
                           opt->roles[1] == KMS_MOSAIC_SLOT_PANE_A;
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
    if (!ui->fs_cycle || pane_count <= 0) return;
    if (ui->fs_next_switch == 0.0) {
        ui->fs_next_switch = now_sec + (fs_cycle_sec > 0 ? fs_cycle_sec : 5);
    } else if (now_sec >= ui->fs_next_switch) {
        ui->fs_pane = (ui->fs_pane + 1) % pane_count;
        ui->fs_next_switch = now_sec + (fs_cycle_sec > 0 ? fs_cycle_sec : 5);
        ui->fullscreen = true;
    }
}
