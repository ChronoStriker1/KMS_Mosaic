#define _GNU_SOURCE

#include "app.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <time.h>
#include <termios.h>
#include <unistd.h>

#include <EGL/egl.h>
#include <GLES2/gl2.h>

#include <linux/kd.h>
#include <linux/vt.h>

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
    bool *pane_ready;
    pane_layout *slot_layouts;
    pane_layout *pane_layouts;
} app_scene;

typedef struct {
    const char *path;
    struct timespec last_mtime;
    struct timespec candidate_mtime;
    off_t last_size;
    off_t candidate_size;
    double next_check_sec;
    bool enabled;
    bool exists;
    bool candidate_exists;
    bool candidate_pending;
} config_watch;

typedef struct {
    const char *request_path;
    const char *lease_path;
    const char *output_path;
    struct timespec request_last_mtime;
    off_t request_last_size;
    bool request_exists;
    bool request_pending;
    int stream_interval_ms;
    double stream_next_frame_sec;
    bool stream_active;
    double next_check_sec;
    struct timespec lease_last_mtime;
    off_t lease_last_size;
    bool lease_exists;
} snapshot_watch;

static bool app_scene_init(app_scene *scene, int pane_count) {
    memset(scene, 0, sizeof(*scene));
    scene->pane_count = pane_count;
    scene->pane_font_px = calloc((size_t)pane_count, sizeof(*scene->pane_font_px));
    scene->pane_ready = calloc((size_t)pane_count, sizeof(*scene->pane_ready));
    scene->slot_layouts = calloc((size_t)(KMS_MOSAIC_SLOT_PANE_BASE + pane_count), sizeof(*scene->slot_layouts));
    scene->pane_layouts = calloc((size_t)pane_count, sizeof(*scene->pane_layouts));
    return scene->pane_font_px && scene->pane_ready && scene->slot_layouts && scene->pane_layouts;
}

static void app_scene_destroy(app_scene *scene) {
    if (!scene) return;
    free(scene->pane_font_px);
    free(scene->pane_ready);
    free(scene->slot_layouts);
    free(scene->pane_layouts);
    scene->pane_font_px = NULL;
    scene->pane_ready = NULL;
    scene->slot_layouts = NULL;
    scene->pane_layouts = NULL;
    scene->pane_count = 0;
}

static void restore_tty(void) {
    if (g_have_oldt) tcsetattr(0, TCSANOW, &g_oldt);
}

static void app_write_text_file(const char *path, const char *value) {
    if (!path || !value) return;
    FILE *fp = fopen(path, "w");
    if (!fp) return;
    fputs(value, fp);
    fclose(fp);
}

static bool app_read_text_file(const char *path, char *buf, size_t buf_size) {
    if (!path || !buf || buf_size == 0) return false;
    FILE *fp = fopen(path, "r");
    if (!fp) return false;
    bool ok = fgets(buf, (int)buf_size, fp) != NULL;
    fclose(fp);
    return ok;
}

static void app_rebind_fbcon(void) {
    DIR *dir = opendir("/sys/class/vtconsole");
    if (!dir) return;
    struct dirent *entry;
    while ((entry = readdir(dir)) != NULL) {
        if (entry->d_name[0] == '.') continue;
        char name_path[256];
        char bind_path[256];
        snprintf(name_path, sizeof(name_path), "/sys/class/vtconsole/%s/name", entry->d_name);
        snprintf(bind_path, sizeof(bind_path), "/sys/class/vtconsole/%s/bind", entry->d_name);
        char name_buf[128] = {0};
        if (!app_read_text_file(name_path, name_buf, sizeof(name_buf))) continue;
        if (!strstr(name_buf, "frame buffer")) continue;
        app_write_text_file(bind_path, "0\n");
        usleep(100000);
        app_write_text_file(bind_path, "1\n");
    }
    closedir(dir);
}

static void app_unblank_framebuffers(void) {
    DIR *dir = opendir("/sys/class/graphics");
    if (!dir) return;
    struct dirent *entry;
    while ((entry = readdir(dir)) != NULL) {
        if (strncmp(entry->d_name, "fb", 2) != 0) continue;
        if (strcmp(entry->d_name, "fbcon") == 0) continue;
        char blank_path[256];
        snprintf(blank_path, sizeof(blank_path), "/sys/class/graphics/%s/blank", entry->d_name);
        app_write_text_file(blank_path, "0\n");
    }
    closedir(dir);
}

static void app_restore_linux_console(void) {
    app_rebind_fbcon();
    app_unblank_framebuffers();

    int vt_fd = open("/dev/tty0", O_RDWR | O_NOCTTY);
    if (vt_fd >= 0) {
        int vt = 1;
        ioctl(vt_fd, VT_ACTIVATE, vt);
        ioctl(vt_fd, VT_WAITACTIVE, vt);
        close(vt_fd);
    }

    int tty_fd = open("/dev/tty1", O_RDWR | O_NOCTTY);
    if (tty_fd >= 0) {
        ioctl(tty_fd, KDSETMODE, KD_TEXT);
        write(tty_fd, "\033c\033[2J\033[H", 10);
        close(tty_fd);
    }
}

static void app_die(const char *msg) {
    perror(msg);
    exit(1);
}

static double app_now_real_sec(void);

static double app_now_sec(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

static const char *app_control_request_path(void) {
    const char *path = getenv("KMS_MOSAIC_CONTROL_REQUEST");
    return path && *path ? path : "/tmp/kms_mosaic_control.request";
}

static const char *app_control_status_path(void) {
    const char *path = getenv("KMS_MOSAIC_CONTROL_STATUS");
    return path && *path ? path : "/tmp/kms_mosaic_control.status";
}

static void app_write_recovery_status(int pane_index, const char *kind, const char *source, bool ok,
                                      unsigned int restart_count) {
    char tmp_path[512];
    snprintf(tmp_path, sizeof(tmp_path), "%s.%ld", app_control_status_path(), (long)getpid());
    FILE *fp = fopen(tmp_path, "w");
    if (!fp) return;
    fprintf(fp,
            "{\"timestamp\":%.3f,\"pane\":%d,\"kind\":\"%s\",\"source\":\"%s\","
            "\"ok\":%s,\"restart_count\":%u}\n",
            app_now_real_sec(), pane_index, kind, source, ok ? "true" : "false", restart_count);
    if (fclose(fp) == 0) (void)rename(tmp_path, app_control_status_path());
    else (void)unlink(tmp_path);
}

static bool app_restart_pane(int pane_index, const char *source, const options_t *opt,
                             pane_runtime *panes, media_ctx *pane_media, runtime_state *rt) {
    if (!opt || pane_index < 0 || pane_index >= opt->pane_count) return false;
    if (pane_media && pane_media[pane_index].mpv) {
        bool ok = media_restart(&pane_media[pane_index]);
        app_write_recovery_status(pane_index, "media", source, ok, pane_media[pane_index].restart_count);
        if (ok && rt) rt->render_dirty = true;
        return ok;
    }
    term_pane *pane = panes_get_term(panes, pane_index);
    if (!pane) {
        app_write_recovery_status(pane_index, "terminal", source, false, 0);
        return false;
    }
    term_pane_respawn(pane);
    runtime_update_pane_fds(rt, opt, panes, pane_media);
    rt->render_dirty = true;
    app_write_recovery_status(pane_index, "terminal", source, true, 0);
    return true;
}

static void app_control_poll(const options_t *opt, pane_runtime *panes, media_ctx *pane_media,
                             runtime_state *rt) {
    FILE *fp = fopen(app_control_request_path(), "r");
    if (!fp) return;
    char line[128] = {0};
    bool read_ok = fgets(line, sizeof(line), fp) != NULL;
    fclose(fp);
    (void)unlink(app_control_request_path());
    int pane_index = -1;
    if (read_ok && sscanf(line, "restart-pane %d", &pane_index) == 1) {
        (void)app_restart_pane(pane_index, "manual", opt, panes, pane_media, rt);
    }
}

static void app_media_watchdogs_poll(const options_t *opt, media_ctx *pane_media, runtime_state *rt) {
    if (!opt || !pane_media) return;
    for (int i = 0; i < opt->pane_count; ++i) {
        int timeout_sec = opt->pane_media ? opt->pane_media[i].watchdog_sec : 0;
        if (timeout_sec > 0 && media_watchdog_poll(&pane_media[i], timeout_sec)) {
            fprintf(stderr, "Pane %d media watchdog restarted stalled playback.\n", i + 1);
            app_write_recovery_status(i, "media", "watchdog", true, pane_media[i].restart_count);
            rt->render_dirty = true;
        }
    }
}

static void app_media_sync_groups_poll(const options_t *opt, media_ctx *pane_media, runtime_state *rt) {
    if (!opt || !opt->pane_media || !pane_media) return;
    for (int i = 0; i < opt->pane_count; ++i) {
        const char *group = opt->pane_media[i].sync_group;
        if (!group || !*group || pane_media[i].sync_released) continue;
        bool all_loaded = true;
        int member_count = 0;
        for (int j = 0; j < opt->pane_count; ++j) {
            const char *candidate = opt->pane_media[j].sync_group;
            if (!candidate || strcmp(candidate, group) != 0) continue;
            member_count++;
            if (!pane_media[j].mpv || !pane_media[j].file_loaded) all_loaded = false;
        }
        if (!all_loaded || member_count == 0) continue;
        for (int j = 0; j < opt->pane_count; ++j) {
            const char *candidate = opt->pane_media[j].sync_group;
            if (!candidate || strcmp(candidate, group) != 0) continue;
            if (media_set_paused(&pane_media[j], false)) pane_media[j].sync_released = true;
        }
        fprintf(stderr, "Released %d loaded media panes in sync group '%s'.\n", member_count, group);
        rt->render_dirty = true;
    }
}

static const char *app_config_watch_path(const options_t *opt) {
    if (!opt || opt->no_config) return NULL;
    if (opt->config_file && *opt->config_file) return opt->config_file;
    return default_config_path();
}

static void app_config_watch_init(config_watch *watch, const options_t *opt) {
    memset(watch, 0, sizeof(*watch));
    watch->path = app_config_watch_path(opt);
    watch->enabled = watch->path && *watch->path;
    const char *disable_env = getenv("KMS_MOSAIC_DISABLE_CONFIG_WATCH");
    if (disable_env && *disable_env && strcmp(disable_env, "0") != 0) {
        watch->enabled = false;
    }
    watch->next_check_sec = app_now_sec() + 1.0;
    if (!watch->enabled) return;

    struct stat st;
    if (stat(watch->path, &st) == 0) {
        watch->exists = true;
        watch->last_mtime = st.st_mtim;
        watch->last_size = st.st_size;
    }
}

static void app_snapshot_watch_init(snapshot_watch *watch) {
    memset(watch, 0, sizeof(*watch));
    watch->request_path = "/tmp/kms_mosaic_snapshot.request";
    watch->lease_path = "/tmp/kms_mosaic_preview.active";
    watch->output_path = "/tmp/kms_mosaic_preview.rgba";
    watch->stream_interval_ms = 16;
    watch->next_check_sec = app_now_sec();
}

static double app_now_real_sec(void) {
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

static int app_snapshot_watch_interval_ms(const snapshot_watch *watch) {
    if (!watch) return 16;
    if (watch->stream_interval_ms < 1) return 1;
    if (watch->stream_interval_ms > 1000) return 1000;
    return watch->stream_interval_ms;
}

static void app_snapshot_watch_poll(snapshot_watch *watch) {
    if (!watch) return;

    double now_sec = app_now_sec();
    if (now_sec < watch->next_check_sec) return;
    watch->next_check_sec = now_sec + 0.25;

    if (watch->request_path) {
        struct stat st;
        bool exists = stat(watch->request_path, &st) == 0;
        if (!exists) {
            watch->request_exists = false;
            memset(&watch->request_last_mtime, 0, sizeof(watch->request_last_mtime));
            watch->request_last_size = 0;
        } else {
            bool changed = !watch->request_exists ||
                           watch->request_last_size != st.st_size ||
                           watch->request_last_mtime.tv_sec != st.st_mtim.tv_sec ||
                           watch->request_last_mtime.tv_nsec != st.st_mtim.tv_nsec;
            watch->request_exists = true;
            watch->request_last_mtime = st.st_mtim;
            watch->request_last_size = st.st_size;
            if (changed) watch->request_pending = true;
        }
    }

    watch->stream_active = false;
    if (!watch->lease_path) return;

    struct stat lease_st;
    if (stat(watch->lease_path, &lease_st) != 0) {
        watch->lease_exists = false;
        return;
    }

    double age_sec = app_now_real_sec() - (lease_st.st_mtim.tv_sec + lease_st.st_mtim.tv_nsec / 1e9);
    if (age_sec < 0.0) age_sec = 0.0;
    if (age_sec > 2.5) return;

    bool lease_changed = !watch->lease_exists ||
                         watch->lease_last_size != lease_st.st_size ||
                         watch->lease_last_mtime.tv_sec != lease_st.st_mtim.tv_sec ||
                         watch->lease_last_mtime.tv_nsec != lease_st.st_mtim.tv_nsec;
    watch->lease_exists = true;
    watch->lease_last_size = lease_st.st_size;
    watch->lease_last_mtime = lease_st.st_mtim;
    if (lease_changed) {
        FILE *fp = fopen(watch->lease_path, "r");
        if (fp) {
            char line[64] = {0};
            if (fgets(line, sizeof(line), fp)) {
                int parsed = atoi(line);
                if (parsed > 0) watch->stream_interval_ms = parsed;
            }
            fclose(fp);
        }
    }
    watch->stream_active = true;
}

static bool app_config_watch_poll(config_watch *watch) {
    if (!watch || !watch->enabled) return false;

    double now_sec = app_now_sec();
    if (now_sec < watch->next_check_sec) return false;
    watch->next_check_sec = now_sec + 1.0;

    struct stat st;
    bool exists = stat(watch->path, &st) == 0;
    bool changed = !watch->exists ||
                   !exists ||
                   watch->last_size != (exists ? st.st_size : 0) ||
                   watch->last_mtime.tv_sec != (exists ? st.st_mtim.tv_sec : 0) ||
                   watch->last_mtime.tv_nsec != (exists ? st.st_mtim.tv_nsec : 0);
    if (!changed) {
        watch->candidate_pending = false;
        return false;
    }

    struct timespec current_mtime = exists ? st.st_mtim : (struct timespec){0};
    off_t current_size = exists ? st.st_size : 0;
    bool same_candidate = watch->candidate_pending &&
                          watch->candidate_exists == exists &&
                          watch->candidate_size == current_size &&
                          watch->candidate_mtime.tv_sec == current_mtime.tv_sec &&
                          watch->candidate_mtime.tv_nsec == current_mtime.tv_nsec;
    if (!same_candidate) {
        watch->candidate_pending = true;
        watch->candidate_exists = exists;
        watch->candidate_size = current_size;
        watch->candidate_mtime = current_mtime;
        return false;
    }

    watch->candidate_pending = false;
    watch->exists = exists;
    watch->last_size = current_size;
    watch->last_mtime = current_mtime;
    return true;
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
        if (opt->gl_finish) glFinish();
        display_page_flip(d, g);
        if (display_wait_for_page_flip(d, g, 1000) != 0) {
            fprintf(stderr, "GL test: page flip timed out at frame %d.\n", f);
            return 1;
        }
    }
    fprintf(stderr, "GL test: rendered %d frames successfully.\n", frames);
    return 0;
}

static void app_prime_display(const options_t *opt, drm_ctx *d, gbm_ctx *g, egl_ctx *e,
                              render_gl_ctx *rg, app_scene *scene) {
    glViewport(0, 0, d->mode.hdisplay, d->mode.vdisplay);
    render_gl_clear_color(0.f, 0.f, 0.f, 1.f);
    eglSwapBuffers(e->dpy, e->surf);
    if (opt->gl_finish) glFinish();
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
                          opt->pane_split_pct, scene->pane_count, opt->split_tree_spec,
                          opt->rotation, ui->perm, opt->visibility_mode, opt->pane_media, ui->overlay_swap,
                          ui->fullscreen, ui->fs_pane, &initial_layout);
    for (int i = 0; i < KMS_MOSAIC_SLOT_PANE_BASE + scene->pane_count; ++i) scene->slot_layouts[i] = initial_layout.role_layouts[i];
    for (int i = 0; i < scene->pane_count; ++i) scene->pane_layouts[i] = scene->slot_layouts[KMS_MOSAIC_SLOT_PANE_BASE + i];
    mosaic_layout_destroy(&initial_layout);

    panes_compute_font_sizes(opt, scene->pane_layouts, scene->pane_count, scene->pane_font_px);
    if (!opt->no_panes) panes_create(panes, opt, scene->pane_layouts, debug);
}

static bool app_poll_runtime_with_media(runtime_state *rt, const options_t *opt,
                                        const pane_runtime *panes, const media_ctx *pane_media,
                                        int timeout_ms) {
    runtime_update_pane_fds(rt, opt, panes, pane_media);
    return poll(rt->pfds, rt->nfds, timeout_ms) >= 0 || errno == EINTR;
}

static int app_poll_timeout_ms(const config_watch *cfg_watch, const snapshot_watch *snap_watch,
                               const ui_state *ui, bool display_busy, bool render_dirty) {
    double now = app_now_sec();
    if (render_dirty && !display_busy) return 0;
    double deadline = now + 1.0;
    if (cfg_watch && cfg_watch->enabled && cfg_watch->next_check_sec < deadline) {
        deadline = cfg_watch->next_check_sec;
    }
    if (snap_watch) {
        if (snap_watch->request_pending && !display_busy) return 0;
        if (snap_watch->next_check_sec < deadline) deadline = snap_watch->next_check_sec;
        if (!display_busy && snap_watch->stream_active && snap_watch->stream_next_frame_sec < deadline) {
            deadline = snap_watch->stream_next_frame_sec;
        }
    }
    if (ui && ui->fs_cycle) {
        if (ui->fs_next_switch <= 0.0) return 0;
        if (ui->fs_next_switch < deadline) deadline = ui->fs_next_switch;
    }
    double remaining_ms = (deadline - now) * 1000.0;
    if (remaining_ms <= 0.0) return 0;
    if (remaining_ms >= 1000.0) return 1000;
    return (int)(remaining_ms + 0.999);
}

static bool app_handle_input_ready(runtime_state *rt, ui_state *ui, options_t *opt, bool use_mpv,
                                   pane_runtime *panes, media_ctx *m, media_ctx *pane_media, bool debug) {
    if (!(rt->pfds[RUNTIME_POLL_STDIN].revents & POLLIN)) return true;
    char buf[64];
    ssize_t n = read(0, buf, sizeof(buf));
    if (n > 0) {
        rt->render_dirty = true;
        term_pane **pane_terms = calloc((size_t)opt->pane_count, sizeof(*pane_terms));
        mpv_handle **pane_mpv = calloc((size_t)opt->pane_count, sizeof(*pane_mpv));
        if (!pane_terms || !pane_mpv) {
            free(pane_terms);
            free(pane_mpv);
            return rt->running;
        }
        for (int i = 0; i < opt->pane_count; ++i) pane_terms[i] = panes_get_term(panes, i);
        for (int i = 0; i < opt->pane_count; ++i) pane_mpv[i] = pane_media && pane_media[i].mpv ? pane_media[i].mpv : NULL;
        (void)ui_handle_input(ui, opt, buf, n, use_mpv,
                              pane_terms, pane_mpv, opt->pane_count,
                              m->mpv, &rt->running, debug);
        free(pane_terms);
        free(pane_mpv);
    }
    return rt->running;
}

static bool app_collect_pane_ready(const options_t *opt, const runtime_state *rt,
                                   bool *pane_ready) {
    bool any_ready = false;
    for (int i = 0; i < opt->pane_count; ++i) {
        pane_ready[i] = !opt->no_panes && runtime_pane_ready(rt, i);
        any_ready = any_ready || pane_ready[i];
    }
    return any_ready;
}

static void app_handle_runtime_events(runtime_state *rt, ui_state *ui, const options_t *opt, media_ctx *m,
                                      media_ctx *pane_media, drm_ctx *d, char *pfifo_buf, int *pfifo_len,
                                      char (*pane_pfifo_bufs)[1024], int *pane_pfifo_lens,
                                      bool use_mpv, bool debug) {
    struct timespec ts_now;
    clock_gettime(CLOCK_MONOTONIC, &ts_now);
    bool was_fullscreen = ui->fullscreen;
    int was_fs_pane = ui->fs_pane;
    ui_update_fs_cycle(ui, opt->pane_count, opt->fs_cycle_sec, ts_now.tv_sec + ts_now.tv_nsec / 1e9);
    if (was_fullscreen != ui->fullscreen || was_fs_pane != ui->fs_pane) rt->render_dirty = true;

    if (use_mpv && (rt->pfds[RUNTIME_POLL_MPV_WAKEUP].revents & POLLIN)) {
        int needs_render = 0;
        media_handle_wakeup(m, debug, &needs_render);
        if (needs_render) {
            rt->mpv_needs_render = 1;
            rt->render_dirty = true;
        }
    }
    for (int i = 0; i < opt->pane_count; ++i) {
        if (pane_media && pane_media[i].mpv && runtime_pane_media_ready(rt, opt, i)) {
            int pane_needs_render = 0;
            media_handle_wakeup(&pane_media[i], debug, &pane_needs_render);
            if (pane_needs_render && rt->pane_mpv_needs_render) {
                rt->pane_mpv_needs_render[i] = 1;
                rt->render_dirty = true;
            }
        }
    }
    if (m->playlist_fifo_fd >= 0 && (rt->pfds[RUNTIME_POLL_PLAYLIST_FIFO].revents & POLLIN)) {
        media_handle_playlist_fifo(m, pfifo_buf, pfifo_len);
        runtime_refresh_playlist_fd(rt, m);
    }
    for (int i = 0; i < opt->pane_count; ++i) {
        if (pane_media && pane_media[i].playlist_fifo_fd >= 0 &&
            runtime_pane_playlist_ready(rt, opt, i)) {
            media_handle_playlist_fifo(&pane_media[i], pane_pfifo_bufs[i], &pane_pfifo_lens[i]);
            runtime_refresh_pane_playlist_fd(rt, opt, pane_media);
        }
    }
    if (rt->pfds[RUNTIME_POLL_DRM].revents & POLLIN) {
        drmEventContext ev = {0};
        ev.version = 2;
        ev.page_flip_handler = display_on_page_flip;
        drmHandleEvent(d->fd, &ev);
    }
}

static bool app_update_layout(const options_t *opt, ui_state *ui, pane_runtime *panes, app_scene *scene, bool debug) {
    if (opt->layout_mode == 6) {
        if (ui->last_layout_mode != 6) {
            if (opt->roles_set) {
                for (int i = 0; i < KMS_MOSAIC_SLOT_PANE_BASE + opt->pane_count; ++i) ui->perm[i] = opt->roles[i];
                ui->overlay_swap = (opt->pane_count == 2 && opt->roles[0] == 1 && opt->roles[1] == 0);
            } else {
                for (int i = 0; i < KMS_MOSAIC_SLOT_PANE_BASE + opt->pane_count; ++i) ui->perm[i] = i;
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

    if (!layout_changed) return false;

    mosaic_layout active_layout = {
        .role_layouts = scene->slot_layouts,
        .role_count = KMS_MOSAIC_SLOT_PANE_BASE + scene->pane_count,
    };
    compute_mosaic_layout(scene->screen_w, scene->screen_h, opt->layout_mode, opt->right_frac_pct,
                          opt->pane_split_pct, scene->pane_count, opt->split_tree_spec,
                          opt->rotation, ui->perm, opt->visibility_mode, opt->pane_media, ui->overlay_swap,
                          ui->fullscreen, ui->fs_pane, &active_layout);
    for (int i = 0; i < scene->pane_count; ++i) scene->pane_layouts[i] = scene->slot_layouts[KMS_MOSAIC_SLOT_PANE_BASE + i];
    panes_apply_layout_mode_alpha(opt, panes);
    int default_frames = 3;
    const char *rf = getenv("KMS_MOSAIC_REINIT_FRAMES");
    if (rf) {
        int v = atoi(rf);
        if (v >= 0 && v <= 30) default_frames = v;
    }
    ui->layout_reinit_countdown = default_frames;
    if (debug) {
        int perm0 = opt->pane_count > 0 ? ui->perm[0] : -1;
        int perm1 = opt->pane_count > 1 ? ui->perm[1] : -1;
        int perm2 = opt->pane_count > 2 ? ui->perm[2] : -1;
        fprintf(stderr, "Layout changed -> reinit countdown %d (mode=%d, perm=%d/%d/%d, rot=%d)\n",
                ui->layout_reinit_countdown, opt->layout_mode,
                perm0, perm1, perm2, (int)opt->rotation);
    }

    panes_compute_font_sizes(opt, scene->pane_layouts, scene->pane_count, scene->pane_font_px);
    return true;
}

static void app_cleanup(const options_t *opt, media_ctx *m, media_ctx *pane_media, render_gl_ctx *rg, drm_ctx *d,
                        gbm_ctx *g, egl_ctx *e, pane_runtime *panes) {
    if (g->in_flight) (void)display_wait_for_page_flip(d, g, 1000);
    if (pane_media) {
        for (int i = 0; i < opt->pane_count; ++i) media_shutdown(&pane_media[i]);
        free(pane_media);
    }
    media_shutdown(m);
    render_gl_destroy(rg);
    if (d->orig_crtc) {
        drmModeSetCrtc(d->fd, d->orig_crtc->crtc_id, d->orig_crtc->buffer_id,
                       d->orig_crtc->x, d->orig_crtc->y, &d->conn_id, 1, &d->orig_crtc->mode);
        drmModeFreeCrtc(d->orig_crtc);
    }
    if (g->bo) {
        gbm_surface_release_buffer(g->surface, g->bo);
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
    app_restore_linux_console();
}

int app_run(int argc, char **argv, int *debug, volatile sig_atomic_t *stop_flag) {
    options_t opt = (options_t){0};
    opt.fs_cycle_sec = 5;

    pane_runtime panes = {0};
    media_ctx m = {0};
    media_ctx *pane_media = NULL;
    drm_ctx d = {0};
    gbm_ctx g = {0};
    egl_ctx e = {0};
    render_gl_ctx rg = {0};
    app_scene scene = {0};
    ui_state ui = {0};
    runtime_state rt = {0};
    config_watch cfg_watch = {0};
    snapshot_watch snap_watch = {0};
    char pfifo_buf[1024];
    int pfifo_len = 0;
    char (*pane_pfifo_bufs)[1024] = NULL;
    int *pane_pfifo_lens = NULL;
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
    pane_media = calloc((size_t)opt.pane_count, sizeof(*pane_media));
    if (!pane_media) app_die("calloc pane_media");
    pane_pfifo_bufs = calloc((size_t)opt.pane_count, sizeof(*pane_pfifo_bufs));
    pane_pfifo_lens = calloc((size_t)opt.pane_count, sizeof(*pane_pfifo_lens));
    if (!pane_pfifo_bufs || !pane_pfifo_lens) app_die("calloc pane playlist fifo buffers");
    for (int i = 0; i < opt.pane_count; ++i) {
        if (opt.pane_media && opt.pane_media[i].enabled) {
            (void)media_init_pane(&pane_media[i], &opt, &opt.pane_media[i], *debug);
        }
    }
    bool has_legacy_root_media =
        opt.video_path || opt.video_count > 0 || opt.playlist_path || opt.playlist_ext ||
        opt.playlist_fifo || opt.mpv_out_path || opt.n_mpv_opts > 0 ||
        (opt.panscan && *opt.panscan) || opt.video_rotate != 0;
    if (use_mpv && opt.pane_count > 0 && has_legacy_root_media) {
        if (!pane_media[0].mpv) {
            pane_media[0] = m;
        } else {
            media_shutdown(&m);
        }
        memset(&m, 0, sizeof(m));
        use_mpv = false;
    }
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
    fprintf(stderr, "Controls: Ctrl+E Control Mode; in Control Mode: Tab focus panes, Arrows resize, l/L layouts, r/R rotate roles, t swap focus/next, z fullscreen, n/p next/prev FS, c cycle FS, o OSD; Ctrl+P panscan; Ctrl+Q quit.\n");

    if (!runtime_init(&rt, &opt, use_mpv, &m, d.fd)) app_die("runtime_init");
    app_config_watch_init(&cfg_watch, &opt);
    app_snapshot_watch_init(&snap_watch);
    double transition_started_sec = app_now_sec();
    bool transition_fading_in = opt.transition_ms > 0;
    bool transition_fading_out = false;
    bool reload_pending = false;

    while (rt.running) {
        if (*stop_flag) {
            fprintf(stderr, "Exiting main loop: stop flag set\n");
            rt.running = false;
            break;
        }
        if (opt.transition_ms > 0 && (transition_fading_in || transition_fading_out)) {
            double progress = (app_now_sec() - transition_started_sec) * 1000.0 / opt.transition_ms;
            if (progress >= 1.0) {
                if (transition_fading_out) {
                    rt.transition_brightness = 0.0f;
                    rc = APP_RUN_RELOAD;
                    break;
                }
                transition_fading_in = false;
                rt.transition_brightness = 1.0f;
            } else {
                if (progress < 0.0) progress = 0.0;
                rt.transition_brightness = transition_fading_out ? (float)(1.0 - progress) : (float)progress;
                rt.render_dirty = true;
            }
        }
        if (*debug && rt.frame < 5) fprintf(stderr, "Loop frame %d start\n", rt.frame);
        app_snapshot_watch_poll(&snap_watch);
        int poll_timeout_ms = app_poll_timeout_ms(&cfg_watch, &snap_watch, &ui,
                                                  g.in_flight != 0, rt.render_dirty);
        if (!app_poll_runtime_with_media(&rt, &opt, &panes, pane_media, poll_timeout_ms)) app_die("poll");
        if (!app_handle_input_ready(&rt, &ui, &opt, use_mpv, &panes, &m, pane_media, *debug)) {
            fprintf(stderr, "Exiting main loop: input handler requested stop\n");
            break;
        }
        app_handle_runtime_events(&rt, &ui, &opt, &m, pane_media, &d,
                                  pfifo_buf, &pfifo_len, pane_pfifo_bufs, pane_pfifo_lens,
                                  use_mpv, *debug);
        app_control_poll(&opt, &panes, pane_media, &rt);
        app_media_sync_groups_poll(&opt, pane_media, &rt);
        app_media_watchdogs_poll(&opt, pane_media, &rt);
        if (!reload_pending && app_config_watch_poll(&cfg_watch)) {
            fprintf(stderr, "Config file changed: %s\n", cfg_watch.path);
            if (opt.transition_ms > 0) {
                reload_pending = true;
                transition_fading_in = false;
                transition_fading_out = true;
                transition_started_sec = app_now_sec();
                rt.transition_brightness = 1.0f;
                rt.render_dirty = true;
            } else {
                rc = APP_RUN_RELOAD;
                break;
            }
        }
        app_snapshot_watch_poll(&snap_watch);

        if (app_collect_pane_ready(&opt, &rt, scene.pane_ready)) rt.render_dirty = true;
        if (app_update_layout(&opt, &ui, &panes, &scene, *debug)) rt.render_dirty = true;
        bool snapshot_written = false;
        const char *snapshot_path = NULL;
        if (snap_watch.request_pending) {
            snapshot_path = snap_watch.output_path;
        } else if (snap_watch.stream_active && app_now_sec() >= snap_watch.stream_next_frame_sec) {
            snapshot_path = snap_watch.output_path;
        }
        if (snapshot_path) rt.render_dirty = true;
        if (rt.render_dirty && !g.in_flight) {
            if (!eglMakeCurrent(e.dpy, e.surf, e.surf, e.ctx)) app_die("eglMakeCurrent loop");
            frame_render(&opt, &rt, &rg, &m, pane_media, &d, &g, &e, &panes, &ui,
                         scene.slot_layouts, scene.pane_layouts, scene.pane_count, scene.logical_w, scene.logical_h,
                         scene.fb_w, scene.fb_h, scene.screen_w, scene.screen_h, scene.pane_font_px,
                         use_mpv, scene.pane_ready, *debug,
                         snapshot_path, &snapshot_written);
            rt.render_dirty = ui.layout_reinit_countdown > 0;
        }
        if (snapshot_written) {
            if (snap_watch.request_pending) snap_watch.request_pending = false;
            if (snap_watch.stream_active) {
                snap_watch.stream_next_frame_sec = app_now_sec() + app_snapshot_watch_interval_ms(&snap_watch) / 1000.0;
            }
        } else if (snapshot_path) {
            rt.render_dirty = true;
        }
    }

    fprintf(stderr, "Main loop exited: rc=%d running=%d stop_flag=%d\n", rc, rt.running ? 1 : 0, *stop_flag ? 1 : 0);

cleanup:
    ui_state_destroy(&ui);
    runtime_destroy(&rt);
    app_scene_destroy(&scene);
    free(pane_pfifo_bufs);
    free(pane_pfifo_lens);
    app_cleanup(&opt, &m, pane_media, &rg, &d, &g, &e, &panes);
    options_destroy(&opt);
    return rc;
}
