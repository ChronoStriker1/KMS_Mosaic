#ifndef FRAME_H
#define FRAME_H

#include <stdbool.h>

#include "display.h"
#include "media.h"
#include "options.h"
#include "panes.h"
#include "render_gl.h"
#include "runtime.h"
#include "ui.h"

void frame_render(const options_t *opt, runtime_state *rt, render_gl_ctx *rg, media_ctx *m,
                  drm_ctx *d, gbm_ctx *g, egl_ctx *e, pane_runtime *panes, ui_state *ui,
                  const pane_layout *lay_video,
                  const pane_layout *lay_a, const pane_layout *lay_b, int logical_w,
                  int logical_h, int fb_w, int fb_h, int screen_w, int screen_h,
                  int font_px_a, int font_px_b, bool use_mpv, bool pane_a_ready,
                  bool pane_b_ready, bool debug);

#endif
