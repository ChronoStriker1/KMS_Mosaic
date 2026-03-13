#define _GNU_SOURCE

#include "app.h"

#include <errno.h>
#include <poll.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <termios.h>
#include <unistd.h>

#include <EGL/egl.h>
#include <GLES2/gl2.h>

#include <mpv/client.h>

#include "display.h"
#include "frame.h"
#include "layout.h"
#include "media.h"
#include "options.h"
#include "panes.h"
#include "render_gl.h"
#include "runtime.h"
#include "ui.h"

static struct termios g_oldt;
static int g_have_oldt = 0;

typedef struct {
    int fb_w;
    int fb_h;
    int logical_w;
    int logical_h;
    int screen_w;
    int screen_h;
    int pane_count;
    int *pane_font_px;
    pane_layout *slot_layouts;
    pane_layout *pane_layouts;
} app_scene;

static bool app_scene_init(app_scene *scene, int pane_count) {
    memset(scene, 0, sizeof(*scene));
    scene->pane_count = pane_count;
    scene->pane_font_px = calloc((size_t)pane_count, sizeof(*scene->pane_font_px));
    scene->slot_layouts = calloc((size_t)(KMS_MOSAIC_SLOT_PANE_BASE + pane_count), sizeof(*scene->slot_layouts));
    scene->pane_layouts = calloc((size_t)pane_count, sizeof(*scene->pane_layouts));
    return scene->pane_font_px && scene->slot_layouts && scene->pane_layouts;
}

static void app_scene_destroy(app_scene *scene) {
    if (!scene) return;
    free(scene->pane_font_px);
    free(scene->slot_layouts);
    free(scene->pane_layouts);
    scene->pane_font_px = NULL;
    scene->slot_layouts = NULL;
    scene->pane_layouts = NULL;
    scene->pane_count = 0;
}

static void restore_tty(void) {
    if (g_have_oldt) tcsetattr(0, TCSANOW, &g_oldt);
}

static void app_die(const char *msg) {
    perror(msg);
    exit(1);
}

static int app_list_connectors(const drm_ctx *d) {
    fprintf(stderr, "Connectors:\n");
    for (int i = 0; i < d->res->count_connectors; i++) {
        drmModeConnector *c = drmModeGetConnector(d->fd, d->res->connectors[i]);
        if (!c) continue;
        fprintf(stderr, "  %u: %s-%u (%s) modes:%d %s\n", c->connector_id,
                display_conn_type_str(c->connector_type), c->connector_type_id,
                c->connection == DRM_MODE_CONNECTED ? "connected" : "disconnected",
                c->count_modes, (c->count_modes > 0 ? "[use --mode WxH@Hz]" : ""));
        for (int mi = 0; mi < c->count_modes && mi < 8; mi++) {
            drmModeModeInfo *m = &c->modes[mi];
            int hz = (int)((m->clock * 1000LL) / (m->htotal * m->vtotal));
            fprintf(stderr, "      %dx%d@%d %s\n", m->hdisplay, m->vdisplay, hz,
                    (m->type & DRM_MODE_TYPE_PREFERRED) ? "(preferred)" : "");
        }
        drmModeFreeConnector(c);
    }
    return 0;
}

static int app_print_diag(void) {
    const char *gl_ver = (const char *)glGetString(GL_VERSION);
    const char *glsl = (const char *)glGetString(GL_SHADING_LANGUAGE_VERSION);
    const char *gl_vendor = (const char *)glGetString(GL_VENDOR);
    const char *gl_renderer = (const char *)glGetString(GL_RENDERER);
    fprintf(stderr, "Diag: GL_VERSION=%s\n", gl_ver ? gl_ver : "?");
    fprintf(stderr, "Diag: GLSL_VERSION=%s\n", glsl ? glsl : "?");
    fprintf(stderr, "Diag: GL_VENDOR=%s\n", gl_vendor ? gl_vendor : "?");
    fprintf(stderr, "Diag: GL_RENDERER=%s\n", gl_renderer ? gl_renderer : "?");
    fprintf(stderr, "Diag: Bundled lib dir /usr/local/lib/kms_mosaic: %s\n",
            access("/usr/local/lib/kms_mosaic", R_OK) == 0 ? "present" : "missing");
    return 0;
}

static int app_run_gl_test(const options_t *opt, render_gl_ctx *rg, drm_ctx *d, gbm_ctx *g, egl_ctx *e,
                           int logical_w, int logical_h, int fb_w, int fb_h) {
    int frames = 120;
    for (int f = 0; f < frames; ++f) {
        if (!eglMakeCurrent(e->dpy, e->surf, e->surf, e->ctx)) app_die("eglMakeCurrent loop");
        glBindFramebuffer(GL_FRAMEBUFFER, rg->rt_fbo);
        glViewport(0, 0, logical_w, logical_h);
        float t = (float)f / (float)frames;
        render_gl_clear_color(0.1f + 0.7f * t, 0.1f + 0.5f * t, 0.2f, 1.0f);
        glBindFramebuffer(GL_FRAMEBUFFER, 0);
        glViewport(0, 0, fb_w, fb_h);
        render_gl_clear_color(0.f, 0.f, 0.f, 1.f);
        render_gl_blit_rt_to_screen(rg, opt->rotation);
        eglSwapBuffers(e->dpy, e->surf);
        if (opt->use_atomic && opt->gl_finish) glFinish();
        display_page_flip(d, g);
    }
    fprintf(stderr, "GL test: rendered %d frames successfully.\n", frames);
    return 0;
}

static void app_prime_display(const options_t *opt, drm_ctx *d, gbm_ctx *g, egl_ctx *e,
                              render_gl_ctx *rg, app_scene *scene) {
    glViewport(0, 0, d->mode.hdisplay, d->mode.vdisplay);
    render_gl_clear_color(0.f, 0.f, 0.f, 1.f);
    eglSwapBuffers(e->dpy, e->surf);
    if (opt->use_atomic && opt->gl_finish) glFinish();
    display_drm_set_mode(d, g);

    scene->fb_w = d->mode.hdisplay;
    scene->fb_h = d->mode.vdisplay;
    scene->logical_w = (opt->rotation == ROT_90 || opt->rotation == ROT_270) ? scene->fb_h : scene->fb_w;
    scene->logical_h = (opt->rotation == ROT_90 || opt->rotation == ROT_270) ? scene->fb_w : scene->fb_h;
    scene->screen_w = scene->logical_w;
    scene->screen_h = scene->logical_h;
    render_gl_ensure_rt(rg, scene->logical_w, scene->logical_h);
}

static void app_init_scene(const options_t *opt, bool use_mpv, pane_runtime *panes, ui_state *ui, app_scene *scene,
                           bool debug) {
    for (int i = 0; i < KMS_MOSAIC_SLOT_PANE_BASE + scene->pane_count; ++i) scene->slot_layouts[i] = (pane_layout){0};
    for (int i = 0; i < scene->pane_count; ++i) scene->pane_layouts[i] = (pane_layout){0};
    scene->slot_layouts[KMS_MOSAIC_SLOT_VIDEO] = (pane_layout){.x = 0, .y = 0, .w = scene->logical_w, .h = scene->logical_h};
    if (!ui_state_init(ui, opt, use_mpv)) app_die("ui_state_init");

    mosaic_layout initial_layout = {0};
    if (!mosaic_layout_init(&initial_layout, KMS_MOSAIC_SLOT_PANE_BASE + scene->pane_count)) app_die("mosaic_layout_init");
    compute_mosaic_layout(scene->screen_w, scene->screen_h, opt->layout_mode, opt->right_frac_pct,
                          opt->pane_split_pct, scene->pane_count, opt->rotation, ui->perm, ui->overlay_swap,
                          ui->fullscreen, ui->fs_pane, &initial_layout);
    for (int i = 0; i < KMS_MOSAIC_SLOT_PANE_BASE + scene->pane_count; ++i) scene->slot_layouts[i] = initial_layout.role_layouts[i];
    for (int i = 0; i < scene->pane_count; ++i) scene->pane_layouts[i] = scene->slot_layouts[KMS_MOSAIC_SLOT_PANE_BASE + i];
    mosaic_layout_destroy(&initial_layout);

    panes_compute_font_sizes(opt, scene->pane_layouts, scene->pane_count, scene->pane_font_px);
    if (!opt->no_panes) panes_create(panes, opt, scene->pane_layouts, debug);
}

static bool app_poll_runtime(runtime_state *rt, const options_t *opt, const pane_runtime *panes) {
    runtime_update_pane_fds(rt, opt, panes);
    return poll(rt->pfds, rt->nfds, 10) >= 0 || errno == EINTR;
}

static bool app_handle_input_ready(runtime_state *rt, ui_state *ui, options_t *opt, bool use_mpv,
                                   pane_runtime *panes, media_ctx *m, bool debug) {
    if (!(rt->pfds[RUNTIME_POLL_STDIN].revents & POLLIN)) return true;
    char buf[64];
    ssize_t n = read(0, buf, sizeof(buf));
    if (n > 0) {
        term_pane **pane_terms = calloc((size_t)opt->pane_count, sizeof(*pane_terms));
        if (!pane_terms) return rt->running;
        for (int i = 0; i < opt->pane_count; ++i) pane_terms[i] = panes_get_term(panes, i);
        (void)ui_handle_input(ui, opt, buf, n, use_mpv,
                              pane_terms, opt->pane_count,
                              m->mpv, &rt->running, debug);
        free(pane_terms);
    }
    return rt->running;
}

static void app_collect_pane_ready(const options_t *opt, const runtime_state *rt,
                                   bool *pane_ready) {
    for (int i = 0; i < opt->pane_count; ++i) {
        int poll_index = RUNTIME_POLL_PANE_BASE + i;
        pane_ready[i] = !opt->no_panes &&
                        (rt->pfds[poll_index].revents & (POLLIN | POLLERR | POLLHUP));
    }
}

static void app_handle_runtime_events(runtime_state *rt, ui_state *ui, const options_t *opt, media_ctx *m,
                                      drm_ctx *d, char *pfifo_buf, int *pfifo_len, bool use_mpv, bool debug) {
    struct timespec ts_now;
    clock_gettime(CLOCK_MONOTONIC, &ts_now);
    ui_update_fs_cycle(ui, opt->pane_count, opt->fs_cycle_sec, ts_now.tv_sec + ts_now.tv_nsec / 1e9);

    if (use_mpv && (rt->pfds[RUNTIME_POLL_MPV_WAKEUP].revents & POLLIN)) {
        media_handle_wakeup(m, debug, &rt->mpv_needs_render);
    }
    if (m->playlist_fifo_fd >= 0 && (rt->pfds[RUNTIME_POLL_PLAYLIST_FIFO].revents & POLLIN)) {
        media_handle_playlist_fifo(m, opt, pfifo_buf, pfifo_len);
        runtime_refresh_playlist_fd(rt, m);
    }
    if (rt->pfds[RUNTIME_POLL_DRM].revents & POLLIN) {
        drmEventContext ev = {0};
        ev.version = 2;
        ev.page_flip_handler = display_on_page_flip;
        drmHandleEvent(d->fd, &ev);
    }
}

static void app_update_layout(const options_t *opt, ui_state *ui, pane_runtime *panes, app_scene *scene, bool debug) {
    if (opt->layout_mode == 6) {
        ui->perm[KMS_MOSAIC_SLOT_VIDEO] = KMS_MOSAIC_SLOT_VIDEO;
        if (ui->last_layout_mode != 6) {
            if (opt->roles_set) {
                for (int i = 1; i < KMS_MOSAIC_SLOT_PANE_BASE + opt->pane_count; ++i) ui->perm[i] = opt->roles[i];
                ui->overlay_swap = (opt->pane_count == 2 && opt->roles[1] == 2 && opt->roles[2] == 1);
            } else {
                for (int i = 1; i < KMS_MOSAIC_SLOT_PANE_BASE + opt->pane_count; ++i) ui->perm[i] = i;
                ui->overlay_swap = false;
            }
            ui->last_overlay_swap = ui->overlay_swap;
        }
    }

    int layout_changed = 0;
    if (ui->last_layout_mode != opt->layout_mode) { layout_changed = 1; ui->last_layout_mode = opt->layout_mode; }
    if (ui->last_right_frac_pct != opt->right_frac_pct) { layout_changed = 1; ui->last_right_frac_pct = opt->right_frac_pct; }
    if (ui->last_pane_split_pct != opt->pane_split_pct) { layout_changed = 1; ui->last_pane_split_pct = opt->pane_split_pct; }
    for (int i = 0; i < KMS_MOSAIC_SLOT_PANE_BASE + opt->pane_count; ++i) {
        if (ui->last_perm[i] != ui->perm[i]) {
            layout_changed = 1;
            break;
        }
    }
    if (layout_changed) {
        for (int i = 0; i < KMS_MOSAIC_SLOT_PANE_BASE + opt->pane_count; ++i) ui->last_perm[i] = ui->perm[i];
    }
    if (ui->last_overlay_swap != ui->overlay_swap) { layout_changed = 1; ui->last_overlay_swap = ui->overlay_swap; }
    if (ui->last_fullscreen != (ui->fullscreen ? 1 : 0) || ui->last_fs_pane != ui->fs_pane) {
        layout_changed = 1;
        ui->last_fullscreen = ui->fullscreen ? 1 : 0;
        ui->last_fs_pane = ui->fs_pane;
    }

    mosaic_layout active_layout = {0};
    if (!mosaic_layout_init(&active_layout, KMS_MOSAIC_SLOT_PANE_BASE + scene->pane_count)) app_die("mosaic_layout_init");
    compute_mosaic_layout(scene->screen_w, scene->screen_h, opt->layout_mode, opt->right_frac_pct,
                          opt->pane_split_pct, scene->pane_count, opt->rotation, ui->perm, ui->overlay_swap,
                          ui->fullscreen, ui->fs_pane, &active_layout);
    for (int i = 0; i < KMS_MOSAIC_SLOT_PANE_BASE + scene->pane_count; ++i) scene->slot_layouts[i] = active_layout.role_layouts[i];
    for (int i = 0; i < scene->pane_count; ++i) scene->pane_layouts[i] = scene->slot_layouts[KMS_MOSAIC_SLOT_PANE_BASE + i];
    mosaic_layout_destroy(&active_layout);
    if (layout_changed) {
        panes_apply_layout_mode_alpha(opt, panes);
        int default_frames = 3;
        const char *rf = getenv("KMS_MOSAIC_REINIT_FRAMES");
        if (rf) {
            int v = atoi(rf);
            if (v >= 0 && v <= 30) default_frames = v;
        }
        ui->layout_reinit_countdown = default_frames;
        if (debug) {
            fprintf(stderr, "Layout changed -> reinit countdown %d (mode=%d, perm=%d/%d/%d, rot=%d)\n",
                    ui->layout_reinit_countdown, opt->layout_mode,
                    ui->perm[0], ui->perm[1], ui->perm[2], (int)opt->rotation);
        }
    }

    panes_compute_font_sizes(opt, scene->pane_layouts, scene->pane_count, scene->pane_font_px);
}

static void app_cleanup(const options_t *opt, media_ctx *m, render_gl_ctx *rg, drm_ctx *d,
                        gbm_ctx *g, egl_ctx *e, pane_runtime *panes) {
    media_shutdown(m);
    render_gl_destroy(rg);
    if (d->orig_crtc) {
        drmModeSetCrtc(d->fd, d->orig_crtc->crtc_id, d->orig_crtc->buffer_id,
                       d->orig_crtc->x, d->orig_crtc->y, &d->conn_id, 1, &d->orig_crtc->mode);
        drmModeFreeCrtc(d->orig_crtc);
    }
    if (g->bo) {
        gbm_surface_release_buffer(g->surface, g->bo);
        drmModeRmFB(d->fd, g->fb_id);
    }
    if (e->dpy != EGL_NO_DISPLAY) {
        eglMakeCurrent(e->dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
        if (e->ctx) eglDestroyContext(e->dpy, e->ctx);
        if (e->surf) eglDestroySurface(e->dpy, e->surf);
        eglTerminate(e->dpy);
    }
    if (g->surface) gbm_surface_destroy(g->surface);
    if (g->dev) gbm_device_destroy(g->dev);
    panes_destroy(panes);
    if (opt->save_config_file) save_config(opt, opt->save_config_file);
    else if (opt->save_config_default) {
        const char *p = opt->config_file ? opt->config_file : default_config_path();
        save_config(opt, p);
    }
    if (d->conn) drmModeFreeConnector(d->conn);
    if (d->res) drmModeFreeResources(d->res);
    if (d->fd >= 0) close(d->fd);
}

int app_run(int argc, char **argv, int *debug, volatile sig_atomic_t *stop_flag) {
    options_t opt = (options_t){0};
    opt.fs_cycle_sec = 5;

    pane_runtime panes = {0};
    media_ctx m = {0};
    drm_ctx d = {0};
    gbm_ctx g = {0};
    egl_ctx e = {0};
    render_gl_ctx rg = {0};
    app_scene scene = {0};
    ui_state ui = {0};
    runtime_state rt = {0};
    char pfifo_buf[1024];
    int pfifo_len = 0;
    int rc = 0;

    if (options_parse_cli(&opt, argc, argv, debug)) return 0;
    if (!panes_init_runtime(&panes, opt.pane_count)) app_die("panes_init_runtime");

    d.fd = display_open_drm_card();
    display_pick_connector_mode(&d, &opt, *debug);
    if (opt.list_connectors) {
        rc = app_list_connectors(&d);
        goto cleanup;
    }

    display_warn_if_missing_dri();
    if (opt.diag) display_preflight_expect_dri_driver_diag();
    else display_preflight_expect_dri_driver();
    display_gbm_init(&g, d.fd, d.mode.hdisplay, d.mode.vdisplay, *debug);
    display_egl_init(&e, &g, *debug);

    bool use_mpv = media_init(&m, &opt, *debug);
    if (opt.diag) {
        rc = app_print_diag();
        goto cleanup;
    }

    if (!app_scene_init(&scene, opt.pane_count)) app_die("app_scene_init");
    app_prime_display(&opt, &d, &g, &e, &rg, &scene);

    if (opt.gl_test) {
        rc = app_run_gl_test(&opt, &rg, &d, &g, &e, scene.logical_w, scene.logical_h, scene.fb_w, scene.fb_h);
        goto cleanup;
    }

    app_init_scene(&opt, use_mpv, &panes, &ui, &scene, *debug);

    struct termios rawt;
    if (tcgetattr(0, &g_oldt) == 0) {
        g_have_oldt = 1;
        rawt = g_oldt;
        cfmakeraw(&rawt);
        tcsetattr(0, TCSANOW, &rawt);
        atexit(restore_tty);
    }
    fprintf(stderr, "Controls: Ctrl+E Control Mode; in Control Mode: Tab focus video/panes, Arrows resize, l/L layouts, r/R rotate roles, t swap focus/next, z fullscreen, n/p next/prev FS, c cycle FS, o OSD; Ctrl+P panscan; Ctrl+Q quit.\n");

    if (!runtime_init(&rt, &opt, use_mpv, &m, d.fd)) app_die("runtime_init");

    while (rt.running) {
        if (*stop_flag) {
            rt.running = false;
            break;
        }
        if (*debug && rt.frame < 5) fprintf(stderr, "Loop frame %d start\n", rt.frame);
        if (!app_poll_runtime(&rt, &opt, &panes)) app_die("poll");
        if (!app_handle_input_ready(&rt, &ui, &opt, use_mpv, &panes, &m, *debug)) break;
        app_handle_runtime_events(&rt, &ui, &opt, &m, &d, pfifo_buf, &pfifo_len, use_mpv, *debug);

        bool *pane_ready = calloc((size_t)scene.pane_count, sizeof(*pane_ready));
        if (!pane_ready) app_die("calloc pane_ready");
        app_collect_pane_ready(&opt, &rt, pane_ready);
        app_update_layout(&opt, &ui, &panes, &scene, *debug);
        if (!eglMakeCurrent(e.dpy, e.surf, e.surf, e.ctx)) app_die("eglMakeCurrent loop");
        frame_render(&opt, &rt, &rg, &m, &d, &g, &e, &panes, &ui,
                     scene.slot_layouts, scene.pane_layouts, scene.pane_count, scene.logical_w, scene.logical_h,
                     scene.fb_w, scene.fb_h, scene.screen_w, scene.screen_h, scene.pane_font_px,
                     use_mpv, pane_ready, *debug);
        free(pane_ready);
    }

cleanup:
    ui_state_destroy(&ui);
    runtime_destroy(&rt);
    app_scene_destroy(&scene);
    app_cleanup(&opt, &m, &rg, &d, &g, &e, &panes);
    options_destroy(&opt);
    return rc;
}
