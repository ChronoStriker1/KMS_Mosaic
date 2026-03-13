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
                  const pane_layout *slot_layouts,
                  const pane_layout *pane_layouts, int pane_count,
                  int logical_w,
                  int logical_h, int fb_w, int fb_h, int screen_w, int screen_h,
                  const int *pane_font_px, bool use_mpv,
                  const bool *pane_ready, bool debug);

#endif
