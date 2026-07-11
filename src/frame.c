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
    (void)slot_layouts;
    bool has_pane_media = false;
    for (int i = 0; i < pane_count; ++i) {
        if (pane_media && pane_media[i].mpv_gl) {
            has_pane_media = true;
            break;
        }
    }
    if (snapshot_written) *snapshot_written = false;
    if (g->in_flight) return;

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
        if (opt->gl_finish) glFinish();
        render_gl_check(debug, "after eglSwapBuffers (direct test/baseline)");
        display_page_flip(d, g);
        rt->frame++;
        return;
    }

    glBindFramebuffer(GL_FRAMEBUFFER, rg->rt_fbo);
    glViewport(0, 0, logical_w, logical_h);
    render_gl_clear_color(0.0f, 0.0f, 0.0f, 1.0f);
    glBindFramebuffer(GL_FRAMEBUFFER, rg->rt_fbo);
    render_gl_reset_state_2d();
    glViewport(0, 0, logical_w, logical_h);

    if (!rt->direct_mode) {
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
            bool pane_hidden = options_pane_hidden(opt, i);
            bool pane_visible = !pane_hidden && (!ui->fullscreen || ui->fs_pane == i);
            media_ctx *pane_ctx = NULL;
            int *pane_needs_render = NULL;
            if (pane_media && pane_media[i].mpv_gl) {
                pane_ctx = &pane_media[i];
                pane_needs_render = rt->pane_mpv_needs_render ? &rt->pane_mpv_needs_render[i] : NULL;
            }
            if (pane_ctx && pane_ctx->mpv_gl) {
                if (pane_visible) {
                    int vw = pane_layouts[i].w;
                    int vh = pane_layouts[i].h;
                    if (vw < 1) vw = 1;
                    if (vh < 1) vh = 1;
                    bool pane_target_resized = render_gl_ensure_pane_video_rt(rg, i, vw, vh);
                    if (pane_target_resized && pane_needs_render) {
                        *pane_needs_render = 1;
                    }
                    GLuint pane_vid_fbo = render_gl_pane_video_fbo(rg, i);
                    GLuint pane_vid_tex = render_gl_pane_video_tex(rg, i);
                    if (!pane_needs_render || *pane_needs_render) {
                        glBindFramebuffer(GL_FRAMEBUFFER, pane_vid_fbo);
                        glDisable(GL_SCISSOR_TEST);
                        glDisable(GL_BLEND);
                        glDisable(GL_DITHER);
                        glDisable(GL_CULL_FACE);
                        glDisable(GL_DEPTH_TEST);
                        glViewport(0, 0, vw, vh);
                        render_gl_clear_color(0.0f, 0.0f, 0.0f, 1.0f);
                        int flip_y = 0;
                        mpv_opengl_fbo fbo = {.fbo = (int)pane_vid_fbo, .w = vw, .h = vh, .internal_format = 0};
                        mpv_render_param params[] = {
                            {MPV_RENDER_PARAM_OPENGL_FBO, &fbo},
                            {MPV_RENDER_PARAM_FLIP_Y, &flip_y},
                            {0}
                        };
                        mpv_render_context_render(pane_ctx->mpv_gl, params);
                        if (pane_needs_render) *pane_needs_render = 0;
                    }

                    glBindFramebuffer(GL_FRAMEBUFFER, rg->rt_fbo);
                    render_gl_reset_state_2d();
                    glViewport(0, 0, logical_w, logical_h);
                    render_gl_draw_tex_to_rt(rg, pane_vid_tex,
                                             pane_layouts[i].x, pane_layouts[i].y, vw, vh, logical_w, logical_h);
                }
                continue;
            }
            if (opt->no_panes) continue;
            term_pane *tp = panes_get_term(panes, i);
            if (!tp) continue;
            if (pane_ready[i]) (void)term_pane_poll(tp);
            if (pane_visible) {
                term_pane_render(tp, screen_w, screen_h);
                if (debug) {
                    fprintf(stderr, "Pane %d draw at %d,%d %dx%d\n", i + 1,
                            pane_layouts[i].x, pane_layouts[i].y, pane_layouts[i].w, pane_layouts[i].h);
                }
                render_gl_check(debug, "after term_pane_render");
            }
        }
    }

    if (!rt->direct_mode && opt->show_osd) {
        media_ctx *osd_media = NULL;
        if (ui->focus >= 0 && ui->focus < pane_count) {
            if (pane_media && pane_media[ui->focus].mpv_gl) {
                osd_media = &pane_media[ui->focus];
            }
        }
        if (!osd_media && pane_media) {
            for (int i = 0; i < pane_count; ++i) {
                if (pane_media[i].mpv_gl) {
                    osd_media = &pane_media[i];
                    break;
                }
            }
        }
        if (osd_media && osd_media->mpv) {
        static osd_ctx *osd = NULL;
        if (!osd) osd = osd_create(opt->font_px ? opt->font_px : 20);
        char line[512];
        snprintf(line, sizeof line, "%s %lld/%lld - %s",
                 osd_media->osd_paused ? "Paused" : "Playing",
                 (long long)(osd_media->osd_playlist_pos + 1),
                 (long long)osd_media->osd_playlist_count,
                 osd_media->osd_title ? osd_media->osd_title : "(no title)");
        osd_set_text(osd, line);
        glBindFramebuffer(GL_FRAMEBUFFER, rg->rt_fbo);
        render_gl_reset_state_2d();
        glViewport(0, 0, logical_w, logical_h);
        osd_draw(osd, 16, 16, logical_w, logical_h);
        }
    }

    if (snapshot_path && snapshot_written && !rt->direct_mode) {
        *snapshot_written = render_gl_write_preview_frame(rg, snapshot_path, rg->rt_tex,
                                                          logical_w, logical_h, 720);
    }

    glBindFramebuffer(GL_FRAMEBUFFER, 0);
    if (!rt->direct_mode) {
        glViewport(0, 0, fb_w, fb_h);
        render_gl_clear_color(0.f, 0.f, 0.f, 1.f);
        render_gl_blit_rt_to_screen_brightness(rg, opt->rotation, rt->transition_brightness);
    }
    if (snapshot_path && snapshot_written && rt->direct_mode) {
        *snapshot_written = render_gl_write_current_rgba_frame(rg, snapshot_path, fb_w, fb_h);
    }

    eglSwapBuffers(e->dpy, e->surf);
    if (opt->gl_finish) glFinish();
    render_gl_check(debug, "after eglSwapBuffers");
    display_page_flip(d, g);
    if (use_mpv && m->mpv_gl) {
        mpv_render_context_report_swap(m->mpv_gl);
    }
    if (pane_media && opt->pane_media) {
        for (int i = 0; i < pane_count; ++i) {
            if (opt->pane_media[i].enabled && pane_media[i].mpv_gl) {
                mpv_render_context_report_swap(pane_media[i].mpv_gl);
            }
        }
    }
    if (use_mpv) rt->mpv_needs_render = 0;
    rt->frame++;
}
