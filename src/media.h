#ifndef MEDIA_H
#define MEDIA_H

#include <stdbool.h>
#include <stdio.h>
#include <stdint.h>

#include <mpv/client.h>
#include <mpv/render_gl.h>

#include "options.h"

typedef struct {
    mpv_handle *mpv;
    mpv_render_context *mpv_gl;
    int wakeup_fd[2];
    FILE *mpv_out;
    int playlist_fifo_fd;
    const char *playlist_fifo_path;
    int64_t osd_playlist_pos;
    int64_t osd_playlist_count;
    int osd_paused;
    char *osd_title;
    uint64_t osd_revision;
    double last_progress_sec;
    double last_watchdog_check_sec;
    unsigned int restart_count;
    bool file_loaded;
    bool sync_released;
} media_ctx;

bool media_should_use(const options_t *opt);
bool media_should_use_pane(const pane_media_config *pane_media);
bool media_init(media_ctx *m, const options_t *opt, bool debug);
bool media_init_pane(media_ctx *m, const options_t *opt, const pane_media_config *pane_media, bool debug);
void media_handle_wakeup(media_ctx *m, bool debug, int *mpv_needs_render);
void media_handle_playlist_fifo(media_ctx *m, char *pfifo_buf, int *pfifo_len);
bool media_restart(media_ctx *m);
bool media_watchdog_poll(media_ctx *m, int timeout_sec);
bool media_set_paused(media_ctx *m, bool paused);
void media_shutdown(media_ctx *m);

#endif
