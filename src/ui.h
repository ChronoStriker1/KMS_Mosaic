#ifndef UI_H
#define UI_H

#include <stdbool.h>
#include <sys/types.h>

#include <mpv/client.h>

#include "options.h"
#include "term_pane.h"

typedef struct {
    int focus;
    bool show_osd;
    bool ui_control;
    int perm[3];
    int last_perm[3];
    bool overlay_swap;
    bool last_overlay_swap;
    int last_layout_mode;
    int last_right_frac_pct;
    int last_pane_split_pct;
    int last_fullscreen;
    int last_fs_pane;
    int layout_reinit_countdown;
    bool fullscreen;
    int fs_pane;
    bool fs_cycle;
    double fs_next_switch;
} ui_state;

void ui_state_init(ui_state *ui, const options_t *opt, bool use_mpv);
void ui_update_fs_cycle(ui_state *ui, int fs_cycle_sec, double now_sec);
bool ui_handle_input(ui_state *ui, options_t *opt, const char *buf, ssize_t n,
                     bool use_mpv, term_pane *tp_a, term_pane *tp_b,
                     mpv_handle *mpv, bool *running, bool debug);

#endif
