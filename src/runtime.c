#include "runtime.h"

#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "term_pane.h"

static int runtime_pane_poll_index(int pane_index) {
    return RUNTIME_POLL_PANE_BASE + pane_index;
}

bool runtime_init(runtime_state *rt, const options_t *opt, bool use_mpv, const media_ctx *m, int drm_fd) {
    memset(rt, 0, sizeof(*rt));
    rt->nfds = opt->no_panes ? 4 : (RUNTIME_POLL_PANE_BASE + opt->pane_count);
    rt->pfds = calloc((size_t)rt->nfds, sizeof(*rt->pfds));
    if (!rt->pfds) return false;
    rt->running = true;
    const char *direct_env = getenv("KMS_MPV_DIRECT");
    rt->direct_mode = (direct_env && (*direct_env == '1' || *direct_env == 'y' || *direct_env == 'Y'));
    const char *flip_env = getenv("KMS_MPV_FLIPY");
    if (flip_env) {
        if (*flip_env == '1' || *flip_env == 'y' || *flip_env == 'Y') rt->mpv_flip_y_direct = 1;
        else if (*flip_env == '0' || *flip_env == 'n' || *flip_env == 'N') rt->mpv_flip_y_direct = 0;
    }
    const char *dfbo_env = getenv("KMS_MPV_DIRECT_FBO");
    rt->direct_via_fbo = (dfbo_env && (*dfbo_env == '1' || *dfbo_env == 'y' || *dfbo_env == 'Y'));
    const char *dtest_env = getenv("KMS_MPV_DIRECT_TEST");
    rt->direct_test_only = (dtest_env && (*dtest_env == '1' || *dtest_env == 'y' || *dtest_env == 'Y'));
    rt->mpv_needs_render = 1;

    rt->pfds[RUNTIME_POLL_STDIN].fd = 0;
    rt->pfds[RUNTIME_POLL_STDIN].events = POLLIN;
    rt->pfds[RUNTIME_POLL_MPV_WAKEUP].fd = use_mpv ? m->wakeup_fd[0] : -1;
    rt->pfds[RUNTIME_POLL_MPV_WAKEUP].events = POLLIN;
    rt->pfds[RUNTIME_POLL_DRM].fd = drm_fd;
    rt->pfds[RUNTIME_POLL_DRM].events = POLLIN;
    rt->pfds[RUNTIME_POLL_PLAYLIST_FIFO].fd = m->playlist_fifo_fd;
    rt->pfds[RUNTIME_POLL_PLAYLIST_FIFO].events = POLLIN;
    for (int i = 0; i < opt->pane_count; ++i) {
        rt->pfds[runtime_pane_poll_index(i)].events = POLLIN | POLLERR | POLLHUP;
    }
    fcntl(0, F_SETFL, O_NONBLOCK);
    return true;
}

void runtime_update_pane_fds(runtime_state *rt, const options_t *opt, const pane_runtime *panes) {
    if (opt->no_panes) return;
    for (int i = 0; i < panes->count; ++i) {
        rt->pfds[runtime_pane_poll_index(i)].fd = term_pane_get_fd(panes_get_term(panes, i));
    }
}

void runtime_refresh_playlist_fd(runtime_state *rt, const media_ctx *m) {
    rt->pfds[RUNTIME_POLL_PLAYLIST_FIFO].fd = m->playlist_fifo_fd;
}

void runtime_destroy(runtime_state *rt) {
    if (!rt) return;
    free(rt->pfds);
    rt->pfds = NULL;
    rt->nfds = 0;
}
