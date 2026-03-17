#include "frame.h"

#include <stdint.h>
#include <stdio.h>

#include <GLES2/gl2.h>

#include <mpv/client.h>
#include <mpv/render_gl.h>

#include "osd.h"
#include "term_pane.h"

void frame_render(const options_t *opt, runtime_state *rt, render_gl_ctx *rg, media_ctx *m,
                  media_ctx *pane_media,
                  drm_ctx *d, gbm_ctx *g, egl_ctx *e, pane_runtime *panes, ui_state *ui,
                  const pane_layout *slot_layouts,
                  const pane_layout *pane_layouts, int pane_count,
                  int logical_w,
                  int logical_h, int fb_w, int fb_h, int screen_w, int screen_h,
                  const int *pane_font_px, bool use_mpv,
                  const bool *pane_ready, bool debug,
                  const char *snapshot_path, bool *snapshot_written) {
    const pane_layout *lay_video = &slot_layouts[KMS_MOSAIC_SLOT_VIDEO];
    bool has_pane_media = false;
    for (int i = 0; i < pane_count; ++i) {
        if (pane_media && opt->pane_media && opt->pane_media[i].enabled && pane_media[i].mpv_gl) {
            has_pane_media = true;
            break;
        }
    }
    if (snapshot_written) *snapshot_written = false;

    if (!has_pane_media && rt->direct_mode && (rt->direct_test_only || !use_mpv)) {
        glBindFramebuffer(GL_FRAMEBUFFER, 0);
        glDisable(GL_SCISSOR_TEST);
        glDisable(GL_BLEND);
        glDisable(GL_DITHER);
        glDisable(GL_CULL_FACE);
        glDisable(GL_DEPTH_TEST);
        glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE);
        glViewport(0, 0, fb_w, fb_h);
        render_gl_clear_color(1.0f, 0.0f, 0.0f, 1.0f);
        if (debug) {
            GLint vp[4] = {0};
            glGetIntegerv(GL_VIEWPORT, vp);
            GLint cur_fbo = 0;
            glGetIntegerv(GL_FRAMEBUFFER_BINDING, &cur_fbo);
            fprintf(stderr, "Direct TEST/Baseline: viewport=%d,%d %dx%d fbo=%d\n",
                    vp[0], vp[1], vp[2], vp[3], cur_fbo);
        }
        eglSwapBuffers(e->dpy, e->surf);
        render_gl_check(debug, "after eglSwapBuffers (direct test/baseline)");
        display_page_flip(d, g);
        rt->frame++;
        return;
    }

    glBindFramebuffer(GL_FRAMEBUFFER, rg->rt_fbo);
    glViewport(0, 0, logical_w, logical_h);
    render_gl_clear_color(0.0f, 0.0f, 0.0f, 1.0f);

    if (use_mpv && (!ui->fullscreen || ui->fs_pane == 0)) {
        int vw = lay_video->w;
        int vh = lay_video->h;
        if (vw < 1) vw = 1;
        if (vh < 1) vh = 1;
        if (!has_pane_media && rt->direct_mode) {
            if (debug) fprintf(stderr, "Render: mpv direct to default FB...\n");
            if (!rt->direct_via_fbo) {
                glBindFramebuffer(GL_FRAMEBUFFER, 0);
                glDisable(GL_SCISSOR_TEST);
                glDisable(GL_BLEND);
                glDisable(GL_DITHER);
                glDisable(GL_CULL_FACE);
                glDisable(GL_DEPTH_TEST);
                glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE);
                glViewport(0, 0, fb_w, fb_h);
                render_gl_clear_color(1.0f, 0.0f, 0.0f, 1.0f);
                if (debug) {
                    GLint vp[4] = {0};
                    glGetIntegerv(GL_VIEWPORT, vp);
                    GLint cur_fbo = 0;
                    glGetIntegerv(GL_FRAMEBUFFER_BINDING, &cur_fbo);
                    fprintf(stderr, "Direct: viewport=%d,%d %dx%d fbo=%d\n",
                            vp[0], vp[1], vp[2], vp[3], cur_fbo);
                }
                if (!rt->direct_test_only) {
                    int flip_y = rt->mpv_flip_y_direct;
                    mpv_opengl_fbo dfbo = {.fbo = 0, .w = fb_w, .h = fb_h, .internal_format = 0};
                    int block = 1;
                    mpv_render_param params[] = {
                        {MPV_RENDER_PARAM_OPENGL_FBO, &dfbo},
                        {MPV_RENDER_PARAM_FLIP_Y, &flip_y},
                        {MPV_RENDER_PARAM_BLOCK_FOR_TARGET_TIME, &block},
                        {0}
                    };
                    if (debug) fprintf(stderr, "Render: calling mpv_render_context_render (direct)...\n");
                    mpv_render_context_render(m->mpv_gl, params);
                    if (opt->use_atomic && opt->gl_finish) glFinish();
                    render_gl_check(debug, "after mpv_render_context_render (direct)");
                    rt->mpv_needs_render = 0;
                } else if (debug) {
                    fprintf(stderr, "Direct TEST: skipped mpv render (expect solid red)\n");
                }
            } else {
                render_gl_ensure_video_rt(rg, fb_w, fb_h);
                glBindFramebuffer(GL_FRAMEBUFFER, rg->vid_fbo);
                glDisable(GL_SCISSOR_TEST);
                glDisable(GL_BLEND);
                glDisable(GL_DITHER);
                glDisable(GL_CULL_FACE);
                glDisable(GL_DEPTH_TEST);
                glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE);
                glViewport(0, 0, fb_w, fb_h);
                render_gl_clear_color(0.f, 0.f, 0.f, 1.0f);
                if (!rt->direct_test_only) {
                    int flip_y = 0;
                    mpv_opengl_fbo fbo = {.fbo = (int)rg->vid_fbo, .w = fb_w, .h = fb_h, .internal_format = 0};
                    int block2 = 1;
                    mpv_render_param params[] = {
                        {MPV_RENDER_PARAM_OPENGL_FBO, &fbo},
                        {MPV_RENDER_PARAM_FLIP_Y, &flip_y},
                        {MPV_RENDER_PARAM_BLOCK_FOR_TARGET_TIME, &block2},
                        {0}
                    };
                    if (debug) fprintf(stderr, "Render: calling mpv_render_context_render (direct via FBO)...\n");
                    mpv_render_context_render(m->mpv_gl, params);
                    if (opt->use_atomic && opt->gl_finish) glFinish();
                    render_gl_check(debug, "after mpv_render_context_render (direct via FBO)");
                    rt->mpv_needs_render = 0;
                } else if (debug) {
                    fprintf(stderr, "Direct TEST: skipped mpv render into FBO\n");
                }
                glBindFramebuffer(GL_FRAMEBUFFER, 0);
                glViewport(0, 0, fb_w, fb_h);
                render_gl_clear_color(1.0f, 0.0f, 0.0f, 1.0f);
                if (!rt->direct_test_only) render_gl_draw_tex_fullscreen(rg, rg->vid_tex);
                else if (debug) fprintf(stderr, "Direct TEST: drew red only (no texture blit)\n");
            }
        } else {
            if (debug) fprintf(stderr, "Render: preparing mpv FBO...\n");
            render_gl_ensure_video_rt(rg, vw, vh);
            glBindFramebuffer(GL_FRAMEBUFFER, rg->vid_fbo);
            glDisable(GL_SCISSOR_TEST);
            glDisable(GL_BLEND);
            glDisable(GL_DITHER);
            glDisable(GL_CULL_FACE);
            glDisable(GL_DEPTH_TEST);
            glViewport(0, 0, vw, vh);
            render_gl_clear_color(0.0f, 0.0f, 0.0f, 1.0f);
            int flip_y = 0;
            mpv_opengl_fbo fbo = {.fbo = (int)rg->vid_fbo, .w = vw, .h = vh, .internal_format = 0};
            mpv_render_param params[] = {
                {MPV_RENDER_PARAM_OPENGL_FBO, &fbo},
                {MPV_RENDER_PARAM_FLIP_Y, &flip_y},
                {0}
            };
            if (debug) fprintf(stderr, "Render: calling mpv_render_context_render...\n");
            mpv_render_context_render(m->mpv_gl, params);
            render_gl_check(debug, "after mpv_render_context_render");
            rt->mpv_needs_render = 0;

            glBindFramebuffer(GL_FRAMEBUFFER, rg->rt_fbo);
            render_gl_reset_state_2d();
            glViewport(0, 0, logical_w, logical_h);
            render_gl_draw_tex_to_rt(rg, rg->vid_tex, lay_video->x, lay_video->y, vw, vh, logical_w, logical_h);
        }
    } else {
        glBindFramebuffer(GL_FRAMEBUFFER, rg->rt_fbo);
        render_gl_reset_state_2d();
        glViewport(0, 0, logical_w, logical_h);
    }

    if (!rt->direct_mode && !opt->no_panes) {
        if (debug) {
            glBindFramebuffer(GL_FRAMEBUFFER, rg->rt_fbo);
            render_gl_reset_state_2d();
            glEnable(GL_SCISSOR_TEST);
            for (int i = 0; i < pane_count; ++i) {
                float shade = 0.08f + 0.06f * (float)(i % 4);
                glScissor(pane_layouts[i].x,
                          logical_h - (pane_layouts[i].y + pane_layouts[i].h),
                          pane_layouts[i].w, pane_layouts[i].h);
                glClearColor(0.05f, 0.08f + shade, 0.12f + shade, 1.0f);
                glClear(GL_COLOR_BUFFER_BIT);
            }
            glDisable(GL_SCISSOR_TEST);
        }
        panes_sync_layout(panes, pane_layouts, pane_count, pane_font_px);
        if (ui->layout_reinit_countdown > 0) ui->layout_reinit_countdown--;
        for (int i = 0; i < pane_count; ++i) {
            if (pane_media && opt->pane_media && opt->pane_media[i].enabled && pane_media[i].mpv_gl) {
                if (!ui->fullscreen || ui->fs_pane == i + 1) {
                    int vw = pane_layouts[i].w;
                    int vh = pane_layouts[i].h;
                    if (vw < 1) vw = 1;
                    if (vh < 1) vh = 1;
                    render_gl_ensure_video_rt(rg, vw, vh);
                    glBindFramebuffer(GL_FRAMEBUFFER, rg->vid_fbo);
                    glDisable(GL_SCISSOR_TEST);
                    glDisable(GL_BLEND);
                    glDisable(GL_DITHER);
                    glDisable(GL_CULL_FACE);
                    glDisable(GL_DEPTH_TEST);
                    glViewport(0, 0, vw, vh);
                    render_gl_clear_color(0.0f, 0.0f, 0.0f, 1.0f);
                    int flip_y = 0;
                    mpv_opengl_fbo fbo = {.fbo = (int)rg->vid_fbo, .w = vw, .h = vh, .internal_format = 0};
                    mpv_render_param params[] = {
                        {MPV_RENDER_PARAM_OPENGL_FBO, &fbo},
                        {MPV_RENDER_PARAM_FLIP_Y, &flip_y},
                        {0}
                    };
                    mpv_render_context_render(pane_media[i].mpv_gl, params);

                    glBindFramebuffer(GL_FRAMEBUFFER, rg->rt_fbo);
                    render_gl_reset_state_2d();
                    glViewport(0, 0, logical_w, logical_h);
                    render_gl_draw_tex_to_rt(rg, rg->vid_tex,
                                             pane_layouts[i].x, pane_layouts[i].y, vw, vh, logical_w, logical_h);
                }
                continue;
            }
            term_pane *tp = panes_get_term(panes, i);
            if (!tp) continue;
            if (pane_ready[i]) (void)term_pane_poll(tp);
            if (!ui->fullscreen || ui->fs_pane == i + 1) {
                term_pane_render(tp, screen_w, screen_h);
                if (debug) {
                    fprintf(stderr, "Pane %d draw at %d,%d %dx%d\n", i + 1,
                            pane_layouts[i].x, pane_layouts[i].y, pane_layouts[i].w, pane_layouts[i].h);
                }
                render_gl_check(debug, "after term_pane_render");
            }
        }
    }

    if (!rt->direct_mode && use_mpv && !opt->no_osd && ui->show_osd) {
        static osd_ctx *osd = NULL;
        if (!osd) osd = osd_create(opt->font_px ? opt->font_px : 20);
        int64_t pos = 0, count = 0;
        int paused_flag = 0;
        char *title = NULL;
        mpv_get_property(m->mpv, "playlist-pos", MPV_FORMAT_INT64, &pos);
        mpv_get_property(m->mpv, "playlist-count", MPV_FORMAT_INT64, &count);
        mpv_get_property(m->mpv, "pause", MPV_FORMAT_FLAG, &paused_flag);
        title = mpv_get_property_string(m->mpv, "media-title");
        char line[512];
        snprintf(line, sizeof line, "%s %lld/%lld - %s",
                 paused_flag ? "Paused" : "Playing",
                 (long long)(pos + 1), (long long)count,
                 title ? title : "(no title)");
        if (title) mpv_free(title);
        osd_set_text(osd, line);
        glBindFramebuffer(GL_FRAMEBUFFER, rg->rt_fbo);
        render_gl_reset_state_2d();
        glViewport(0, 0, logical_w, logical_h);
        osd_draw(osd, 16, 16, logical_w, logical_h);
    }

    if (!rt->direct_mode && ui->ui_control) {
        static osd_ctx *osdcm = NULL;
        if (!osdcm) osdcm = osd_create(opt->font_px ? opt->font_px : 20);
        const char *layout_name = layout_mode_name(opt->layout_mode);
        const char *help =
            "Tab: focus cycle video/panes\n"
            "o: toggle OSD\n"
            "l/L: cycle layouts\n"
            "r/R: rotate roles\n"
            "t: swap focused pane with next\n"
            "z: fullscreen focused pane\n"
            "n: next fullscreen pane\n"
            "p: previous fullscreen pane\n"
            "c: cycle fullscreen panes\n"
            "Arrows: resize splits (2x1/1x2/2over1/1over2)\n"
            "f: force pane rebuild\n"
            "Always: Ctrl+Q quit";
        char cm_text[1024];
        snprintf(cm_text, sizeof cm_text, "Control Mode (Ctrl+E)  Layout: %s\n%s", layout_name, help);
        osd_set_text(osdcm, cm_text);
        glBindFramebuffer(GL_FRAMEBUFFER, rg->rt_fbo);
        render_gl_reset_state_2d();
        glViewport(0, 0, logical_w, logical_h);
        osd_draw(osdcm, 16, 48, logical_w, logical_h);
        int bx = 0, by = 0, bw = 0, bh = 0;
        int thickness = 4;
        const pane_layout **focus_layouts = calloc((size_t)(KMS_MOSAIC_SLOT_PANE_BASE + pane_count), sizeof(*focus_layouts));
        if (!focus_layouts) return;
        focus_layouts[KMS_MOSAIC_SLOT_VIDEO] = &slot_layouts[KMS_MOSAIC_SLOT_VIDEO];
        for (int i = 0; i < pane_count; ++i) focus_layouts[KMS_MOSAIC_SLOT_PANE_BASE + i] = &pane_layouts[i];
        int focus_slot = ui->focus;
        if (focus_slot < 0 || focus_slot >= KMS_MOSAIC_SLOT_PANE_BASE + pane_count || !focus_layouts[focus_slot]) {
            focus_slot = KMS_MOSAIC_SLOT_VIDEO;
        }
        bx = focus_layouts[focus_slot]->x;
        by = focus_layouts[focus_slot]->y;
        bw = focus_layouts[focus_slot]->w;
        bh = focus_layouts[focus_slot]->h;
        free(focus_layouts);
        render_gl_draw_border_rect(bx, by, bw, bh, thickness, logical_w, logical_h, 0.1f, 0.9f, 0.95f, 1.0f);
    }

    glBindFramebuffer(GL_FRAMEBUFFER, 0);
    if (!rt->direct_mode) {
        glViewport(0, 0, fb_w, fb_h);
        render_gl_clear_color(0.f, 0.f, 0.f, 1.f);
        render_gl_blit_rt_to_screen(rg, opt->rotation);
    }
    if (snapshot_path && snapshot_written) {
        *snapshot_written = render_gl_write_current_bmp(snapshot_path, fb_w, fb_h);
    }

    eglSwapBuffers(e->dpy, e->surf);
    if (opt->use_atomic && opt->gl_finish) glFinish();
    render_gl_check(debug, "after eglSwapBuffers");
    display_page_flip(d, g);
    if (use_mpv && m->mpv_gl) {
        mpv_render_context_report_swap(m->mpv_gl);
    }
    if (use_mpv) rt->mpv_needs_render = 1;
    rt->frame++;
}
