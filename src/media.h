#ifndef MEDIA_H
#define MEDIA_H

#include <stdbool.h>
#include <stdio.h>

#include <mpv/client.h>
#include <mpv/render_gl.h>

#include "options.h"

typedef struct {
    mpv_handle *mpv;
    mpv_render_context *mpv_gl;
    int wakeup_fd[2];
    FILE *mpv_out;
    int playlist_fifo_fd;
} media_ctx;

bool media_should_use(const options_t *opt);
bool media_init(media_ctx *m, const options_t *opt, bool debug);
void media_handle_wakeup(media_ctx *m, bool debug, int *mpv_needs_render);
void media_handle_playlist_fifo(media_ctx *m, const options_t *opt, char *pfifo_buf, int *pfifo_len);
void media_shutdown(media_ctx *m);

#endif
