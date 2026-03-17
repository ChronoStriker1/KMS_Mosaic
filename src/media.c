#define _GNU_SOURCE

#include "media.h"

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include <EGL/egl.h>
#include <GLES2/gl2.h>

static void media_update_wakeup(void *ctx) {
    media_ctx *m = (media_ctx *)ctx;
    if (!m) return;
    char one = 'x';
    if (m->wakeup_fd[1] >= 0) {
        ssize_t n = write(m->wakeup_fd[1], &one, 1);
        if (n < 0 && errno != EAGAIN && errno != EWOULDBLOCK) {
            perror("media wakeup write");
        }
    }
}

static void *media_get_proc_address(void *ctx, const char *name) {
    (void)ctx;
    return (void *)eglGetProcAddress(name);
}

static int media_key_matches(const char *kv, const char *key) {
    const char *eq = strchr(kv, '=');
    if (!eq) return 0;
    size_t kl = (size_t)(eq - kv);
    return kl == strlen(key) && strncmp(kv, key, kl) == 0;
}

static void media_apply_option_list(media_ctx *m, const char *const *opts, int count,
                                    bool *user_set_hwdec, bool *user_set_vsync,
                                    bool *user_set_keepaspect, bool *user_set_rotate,
                                    bool *user_set_panscan, bool *user_set_interpolation,
                                    bool *user_set_tscale, bool *user_set_eflush,
                                    bool *user_set_shader_cache) {
    for (int i = 0; i < count; i++) {
        const char *kv = opts[i];
        const char *eq = strchr(kv, '=');
        if (!eq) continue;
        char key[128];
        size_t kl = (size_t)(eq - kv);
        if (kl >= sizeof(key)) kl = sizeof(key) - 1;
        memcpy(key, kv, kl);
        key[kl] = '\0';
        mpv_set_option_string(m->mpv, key, eq + 1);
        if (strcmp(key, "hwdec") == 0) *user_set_hwdec = true;
        if (media_key_matches(kv, "video-sync")) *user_set_vsync = true;
        else if (media_key_matches(kv, "keepaspect")) *user_set_keepaspect = true;
        else if (media_key_matches(kv, "video-rotate")) *user_set_rotate = true;
        else if (media_key_matches(kv, "panscan")) *user_set_panscan = true;
        else if (media_key_matches(kv, "interpolation")) *user_set_interpolation = true;
        else if (media_key_matches(kv, "tscale")) *user_set_tscale = true;
        else if (media_key_matches(kv, "opengl-early-flush")) *user_set_eflush = true;
        else if (media_key_matches(kv, "gpu-shader-cache")) *user_set_shader_cache = true;
    }
}

static void media_apply_options(media_ctx *m, const options_t *opt, const pane_media_config *pane_media) {
    bool user_set_hwdec = false;
    bool user_set_vsync = false;
    bool user_set_keepaspect = false;
    bool user_set_rotate = false;
    bool user_set_panscan = false;
    bool user_set_interpolation = false;
    bool user_set_tscale = false;
    bool user_set_eflush = false;
    bool user_set_shader_cache = false;

    media_apply_option_list(m, opt->mpv_opts, opt->n_mpv_opts,
                            &user_set_hwdec, &user_set_vsync, &user_set_keepaspect,
                            &user_set_rotate, &user_set_panscan, &user_set_interpolation,
                            &user_set_tscale, &user_set_eflush, &user_set_shader_cache);
    if (pane_media && pane_media->n_mpv_opts > 0) {
        media_apply_option_list(m, pane_media->mpv_opts, pane_media->n_mpv_opts,
                                &user_set_hwdec, &user_set_vsync, &user_set_keepaspect,
                                &user_set_rotate, &user_set_panscan, &user_set_interpolation,
                                &user_set_tscale, &user_set_eflush, &user_set_shader_cache);
    }

    if (!user_set_hwdec) mpv_set_option_string(m->mpv, "hwdec", "no");
    if (opt->loop_file || opt->loop_flag) mpv_set_option_string(m->mpv, "loop-file", "inf");
    if (opt->loop_playlist) mpv_set_option_string(m->mpv, "loop-playlist", "yes");
    if (opt->shuffle) mpv_set_option_string(m->mpv, "shuffle", "yes");
    if (!user_set_vsync) mpv_set_option_string(m->mpv, "video-sync", "display-resample");
    if (!user_set_keepaspect) mpv_set_option_string(m->mpv, "keepaspect", "yes");
    if (opt->video_rotate >= 0 && !user_set_rotate) {
        char buf[16];
        snprintf(buf, sizeof(buf), "%d", opt->video_rotate);
        mpv_set_option_string(m->mpv, "video-rotate", buf);
    }
    if (opt->panscan && !user_set_panscan) mpv_set_option_string(m->mpv, "panscan", opt->panscan);
    if (opt->smooth) {
        if (!user_set_interpolation) mpv_set_option_string(m->mpv, "interpolation", "no");
        if (!user_set_tscale) mpv_set_option_string(m->mpv, "tscale", "linear");
        if (!user_set_eflush) mpv_set_option_string(m->mpv, "opengl-early-flush", "yes");
        if (!user_set_shader_cache) mpv_set_option_string(m->mpv, "gpu-shader-cache", "no");
    }
}

static void media_load_inputs_source(media_ctx *m, const options_t *opt, const pane_media_config *pane_media) {
    const char *playlist_path = pane_media ? pane_media->playlist_path : opt->playlist_path;
    const char *playlist_ext = pane_media ? pane_media->playlist_ext : opt->playlist_ext;
    video_item *videos = pane_media ? pane_media->videos : opt->videos;
    int video_count = pane_media ? pane_media->video_count : opt->video_count;

    if (playlist_path) {
        const char *cmd[] = {"loadlist", playlist_path, "replace", NULL};
        mpv_command_async(m->mpv, 0, cmd);
    } else if (playlist_ext) {
        int loaded = 0;
        FILE *f = fopen(playlist_ext, "r");
        if (f) {
            char *line = NULL;
            size_t cap = 0;
            ssize_t n;
            while ((n = getline(&line, &cap, f)) != -1) {
                while (n > 0 && (line[n - 1] == '\n' || line[n - 1] == '\r')) line[--n] = '\0';
                mpv_append_line(m->mpv, line);
                loaded++;
            }
            free(line);
            fclose(f);
        }
        if (loaded == 0) {
            fprintf(stderr, "warning: playlist-ext empty or unreadable: %s\n", playlist_ext);
        }
    } else if (video_count > 0) {
        for (int vi = 0; vi < video_count; ++vi) {
            const video_item *item = &videos[vi];
            if (item->nopts == 0) {
                const char *mode = (vi == 0 ? "replace" : "append");
                const char *cmd[] = {"loadfile", item->path, mode, NULL};
                mpv_command_async(m->mpv, 0, cmd);
                continue;
            }

            mpv_node root;
            memset(&root, 0, sizeof(root));
            root.format = MPV_FORMAT_NODE_ARRAY;
            root.u.list = malloc(sizeof(*root.u.list));
            root.u.list->num = 0;
            root.u.list->values = NULL;
            root.u.list->keys = NULL;
#define MEDIA_PUSH_STR(str) do { \
    root.u.list->values = realloc(root.u.list->values, sizeof(mpv_node) * (root.u.list->num + 1)); \
    root.u.list->values[root.u.list->num].format = MPV_FORMAT_STRING; \
    root.u.list->values[root.u.list->num].u.string = (char *)(str); \
    root.u.list->num++; \
} while (0)
            MEDIA_PUSH_STR("loadfile");
            MEDIA_PUSH_STR(item->path);
            MEDIA_PUSH_STR(vi == 0 ? "replace" : "append");
#undef MEDIA_PUSH_STR

            mpv_node map;
            memset(&map, 0, sizeof(map));
            map.format = MPV_FORMAT_NODE_MAP;
            map.u.list = malloc(sizeof(*map.u.list));
            map.u.list->num = 0;
            map.u.list->values = NULL;
            map.u.list->keys = NULL;
            for (int oi = 0; oi < item->nopts; ++oi) {
                const char *kv = item->opts[oi];
                const char *eq = strchr(kv, '=');
                if (!eq) continue;
                size_t kl = (size_t)(eq - kv);
                char *key = strndup(kv, kl);
                const char *val = eq + 1;
                map.u.list->values = realloc(map.u.list->values, sizeof(mpv_node) * (map.u.list->num + 1));
                map.u.list->keys = realloc(map.u.list->keys, sizeof(char *) * (map.u.list->num + 1));
                map.u.list->keys[map.u.list->num] = key;
                map.u.list->values[map.u.list->num].format = MPV_FORMAT_STRING;
                map.u.list->values[map.u.list->num].u.string = (char *)val;
                map.u.list->num++;
            }
            root.u.list->values = realloc(root.u.list->values, sizeof(mpv_node) * (root.u.list->num + 1));
            root.u.list->values[root.u.list->num] = map;
            root.u.list->num++;
            mpv_command_node_async(m->mpv, 0, &root);
        }
    }
    if (opt->shuffle) {
        const char *cmd[] = {"playlist-shuffle", NULL};
        mpv_command_async(m->mpv, 0, cmd);
    }
}

void media_handle_wakeup(media_ctx *m, bool debug, int *mpv_needs_render) {
    uint64_t tmp;
    while (read(m->wakeup_fd[0], &tmp, sizeof(tmp)) > 0) {}
    for (;;) {
        mpv_event *ev = mpv_wait_event(m->mpv, 0);
        if (!ev || ev->event_id == MPV_EVENT_NONE) break;
        if (ev->event_id == MPV_EVENT_LOG_MESSAGE) {
            mpv_event_log_message *lm = ev->data;
            if (debug) fprintf(stderr, "mpv[%s]: %s", lm->prefix, lm->text);
            if (m->mpv_out) {
                fprintf(m->mpv_out, "[%s] %s", lm->prefix, lm->text);
                fflush(m->mpv_out);
            }
        } else if (ev->event_id == MPV_EVENT_START_FILE) {
            if (debug) fprintf(stderr, "mpv: START_FILE\n");
            if (m->mpv_out) {
                fprintf(m->mpv_out, "START_FILE\n");
                fflush(m->mpv_out);
            }
        } else if (ev->event_id == MPV_EVENT_FILE_LOADED) {
            if (debug) fprintf(stderr, "mpv: FILE_LOADED\n");
            if (m->mpv_out) {
                fprintf(m->mpv_out, "FILE_LOADED\n");
                fflush(m->mpv_out);
            }
            *mpv_needs_render = 1;
        } else if (ev->event_id == MPV_EVENT_VIDEO_RECONFIG) {
            if (debug) fprintf(stderr, "mpv: VIDEO_RECONFIG\n");
            if (m->mpv_out) {
                fprintf(m->mpv_out, "VIDEO_RECONFIG\n");
                fflush(m->mpv_out);
            }
            *mpv_needs_render = 1;
        } else if (ev->event_id == MPV_EVENT_END_FILE) {
            if (debug) fprintf(stderr, "mpv: END_FILE\n");
            if (m->mpv_out) {
                fprintf(m->mpv_out, "END_FILE\n");
                fflush(m->mpv_out);
            }
        }
    }
    int flags = mpv_render_context_update(m->mpv_gl);
    if (flags & MPV_RENDER_UPDATE_FRAME) {
        *mpv_needs_render = 1;
        if (debug) fprintf(stderr, "mpv: UPDATE_FRAME\n");
    }
}

void media_handle_playlist_fifo(media_ctx *m, char *pfifo_buf, int *pfifo_len) {
    ssize_t r = read(m->playlist_fifo_fd, pfifo_buf + *pfifo_len, 1024 - *pfifo_len - 1);
    if (r > 0) {
        *pfifo_len += (int)r;
        pfifo_buf[*pfifo_len] = '\0';
        char *start = pfifo_buf;
        char *nl;
        while ((nl = strchr(start, '\n')) != NULL) {
            *nl = '\0';
            mpv_append_line(m->mpv, start);
            start = nl + 1;
        }
        *pfifo_len = (int)(pfifo_buf + *pfifo_len - start);
        memmove(pfifo_buf, start, (size_t)*pfifo_len);
    } else if (r == 0) {
        close(m->playlist_fifo_fd);
        if (m->playlist_fifo_path) {
            m->playlist_fifo_fd = open(m->playlist_fifo_path, O_RDONLY | O_NONBLOCK);
        } else {
            m->playlist_fifo_fd = -1;
        }
    }
}

bool media_should_use(const options_t *opt) {
    if (!opt || opt->no_video) return false;
    if (opt->video_count > 0 || opt->playlist_path || opt->playlist_ext) return true;
    return false;
}

bool media_should_use_pane(const pane_media_config *pane_media) {
    if (!pane_media || !pane_media->enabled) return false;
    if (pane_media->video_count > 0 || pane_media->playlist_path || pane_media->playlist_ext) return true;
    return false;
}

static bool media_init_source(media_ctx *m, const options_t *opt, const pane_media_config *pane_media, bool debug) {
    if (!m || !opt) return false;
    memset(m, 0, sizeof(*m));
    m->wakeup_fd[0] = -1;
    m->wakeup_fd[1] = -1;
    m->playlist_fifo_fd = -1;
    m->playlist_fifo_path = pane_media ? pane_media->playlist_fifo : opt->playlist_fifo;

    if (pane_media) {
        if (!media_should_use_pane(pane_media)) return false;
    } else if (!media_should_use(opt)) {
        return false;
    }
    const char *disable_env = getenv("KMS_MPV_DISABLE");
    if (disable_env && (*disable_env == '1' || *disable_env == 'y' || *disable_env == 'Y')) {
        fprintf(stderr, "Debug: KMS_MPV_DISABLE set; skipping mpv setup.\n");
        return false;
    }

    if (pipe2(m->wakeup_fd, O_NONBLOCK | O_CLOEXEC) < 0) {
        perror("pipe2");
        exit(1);
    }
    m->mpv = mpv_create();
    if (!m->mpv) {
        fprintf(stderr, "mpv_create failed\n");
        exit(1);
    }

    mpv_set_option_string(m->mpv, "vo", "libmpv");
    mpv_set_option_string(m->mpv, "keep-open", "yes");
    const char *glver = (const char *)glGetString(GL_VERSION);
    if (glver && strstr(glver, "OpenGL ES")) mpv_set_option_string(m->mpv, "opengl-es", "yes");
    media_apply_options(m, opt, pane_media);
    if (debug) mpv_request_log_messages(m->mpv, "debug");
    if (mpv_initialize(m->mpv) < 0) {
        fprintf(stderr, "mpv_initialize failed\n");
        exit(1);
    }

    int adv = 1;
    mpv_render_param params[] = {
        { MPV_RENDER_PARAM_API_TYPE, (void *)MPV_RENDER_API_TYPE_OPENGL },
        { MPV_RENDER_PARAM_OPENGL_INIT_PARAMS,
          &(mpv_opengl_init_params){ .get_proc_address = media_get_proc_address, .get_proc_address_ctx = NULL } },
        { MPV_RENDER_PARAM_ADVANCED_CONTROL, &adv },
        { 0 }
    };
    if (mpv_render_context_create(&m->mpv_gl, m->mpv, params) < 0) {
        fprintf(stderr, "mpv_render_context_create failed\n");
        exit(1);
    }
    mpv_render_context_set_update_callback(m->mpv_gl, media_update_wakeup, m);
    mpv_set_wakeup_callback(m->mpv, media_update_wakeup, m);

    media_load_inputs_source(m, opt, pane_media);

    if (!pane_media && opt->mpv_out_path) {
        m->mpv_out = fopen(opt->mpv_out_path, "w");
        if (!m->mpv_out) perror("mpv-out");
    }
    if (m->playlist_fifo_path) {
        mkfifo(m->playlist_fifo_path, 0666);
        m->playlist_fifo_fd = open(m->playlist_fifo_path, O_RDONLY | O_NONBLOCK);
        if (m->playlist_fifo_fd < 0) perror("playlist-fifo");
    }
    return true;
}

bool media_init(media_ctx *m, const options_t *opt, bool debug) {
    return media_init_source(m, opt, NULL, debug);
}

bool media_init_pane(media_ctx *m, const options_t *opt, const pane_media_config *pane_media, bool debug) {
    return media_init_source(m, opt, pane_media, debug);
}

void media_shutdown(media_ctx *m) {
    if (!m) return;
    if (m->mpv_gl) mpv_render_context_free(m->mpv_gl);
    m->mpv_gl = NULL;
    if (m->mpv) mpv_terminate_destroy(m->mpv);
    m->mpv = NULL;
    if (m->mpv_out) fclose(m->mpv_out);
    m->mpv_out = NULL;
    if (m->playlist_fifo_fd >= 0) close(m->playlist_fifo_fd);
    m->playlist_fifo_fd = -1;
    m->playlist_fifo_path = NULL;
    if (m->wakeup_fd[0] >= 0) close(m->wakeup_fd[0]);
    if (m->wakeup_fd[1] >= 0) close(m->wakeup_fd[1]);
    m->wakeup_fd[0] = -1;
    m->wakeup_fd[1] = -1;
}
