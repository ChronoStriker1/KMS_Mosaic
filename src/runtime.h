#ifndef RUNTIME_H
#define RUNTIME_H

#include <poll.h>
#include <stdbool.h>

#include "media.h"
#include "options.h"
#include "panes.h"
#include "ui.h"

enum {
    RUNTIME_POLL_STDIN = 0,
    RUNTIME_POLL_MPV_WAKEUP,
    RUNTIME_POLL_DRM,
    RUNTIME_POLL_PLAYLIST_FIFO,
    RUNTIME_POLL_PANE_BASE,
    RUNTIME_POLL_COUNT = RUNTIME_POLL_PANE_BASE + PANE_SLOT_COUNT
};

typedef struct {
    bool running;
    bool direct_mode;
    int mpv_flip_y_direct;
    bool direct_via_fbo;
    bool direct_test_only;
    int frame;
    int mpv_needs_render;
    struct pollfd *pfds;
    int nfds;
} runtime_state;

bool runtime_init(runtime_state *rt, const options_t *opt, bool use_mpv, const media_ctx *m, int drm_fd);
void runtime_update_pane_fds(runtime_state *rt, const options_t *opt, const pane_runtime *panes);
void runtime_refresh_playlist_fd(runtime_state *rt, const media_ctx *m);
void runtime_destroy(runtime_state *rt);

#endif
