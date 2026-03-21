#define _GNU_SOURCE
#include "options.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

void parse_mode(const char *s, int *w, int *h, int *hz) {
    if (!s) {
        *w = *h = *hz = 0;
        return;
    }
    int tw = 0, th = 0, thz = 0;
    if (sscanf(s, "%dx%d@%d", &tw, &th, &thz) >= 2) {
        *w = tw;
        *h = th;
        *hz = thz;
    }
}

rotation_t parse_rot(const char *s) {
    if (!s) return ROT_0;
    int v = atoi(s);
    switch (v) {
        case 0: return ROT_0;
        case 90: return ROT_90;
        case 180: return ROT_180;
        case 270: return ROT_270;
        default: return ROT_0;
    }
}

int parse_layout_mode(const char *s) {
    if (!s) return -1;
    if (!strcmp(s, "stack") || !strcmp(s, "stack3")) return 0;
    if (!strcmp(s, "row") || !strcmp(s, "row3")) return 1;
    if (!strcmp(s, "2x1")) return 2;
    if (!strcmp(s, "1x2")) return 3;
    if (!strcmp(s, "2over1")) return 4;
    if (!strcmp(s, "1over2")) return 5;
    if (!strcmp(s, "overlay")) return 6;
    return -1;
}

const char *layout_mode_name(int mode) {
    switch (mode) {
        case 0: return "stack";
        case 1: return "row";
        case 2: return "2x1";
        case 3: return "1x2";
        case 4: return "2over1";
        case 5: return "1over2";
        case 6: return "overlay";
        default: return "stack";
    }
}

static int options_role_from_char(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'A' && c <= 'Z') return c - 'A';
    if (c >= 'a' && c <= 'z') return c - 'a';
    return -1;
}

static int options_legacy_role_from_char(char c) {
    if (c == 'C' || c == 'c') return 0;
    if (c == 'A' || c == 'a' || c == '1') return 1;
    if (c == 'B' || c == 'b' || c == '2') return 2;
    if (c == 'D' || c == 'd' || c == '3') return 3;
    if (c == 'E' || c == 'e' || c == '4') return 4;
    if (c >= '0' && c <= '9') return c - '0';
    return -1;
}

bool parse_roles_string(const char *s, int *roles, int role_count) {
    if (!s || !roles) return false;
    int *parsed = calloc((size_t)role_count, sizeof(*parsed));
    bool *used = calloc((size_t)role_count, sizeof(*used));
    int slot = 0;
    if (!parsed || !used || role_count < 1) {
        free(parsed);
        free(used);
        return false;
    }
    for (int i = 0; i < role_count; ++i) parsed[i] = i;
    for (const char *p = s; *p && slot < role_count; ++p) {
        int role = options_role_from_char(*p);
        if (role < 0 || role >= role_count) continue;
        if (role >= 0 && !used[role]) {
            parsed[role] = slot++;
            used[role] = true;
        }
    }
    if (slot != role_count) {
        free(parsed);
        free(used);
        return false;
    }
    for (int i = 0; i < role_count; ++i) roles[i] = parsed[i];
    free(parsed);
    free(used);
    return true;
}

static bool options_parse_legacy_roles_string(const char *s, int *roles, int role_count) {
    if (!s || !roles) return false;
    int *parsed = calloc((size_t)role_count, sizeof(*parsed));
    bool *used = calloc((size_t)role_count, sizeof(*used));
    int slot = 0;
    if (!parsed || !used || role_count < 1) {
        free(parsed);
        free(used);
        return false;
    }
    for (int i = 0; i < role_count; ++i) parsed[i] = i;
    for (const char *p = s; *p && slot < role_count; ++p) {
        int role = options_legacy_role_from_char(*p);
        if (role < 0 || role >= role_count) continue;
        if (!used[role]) {
            parsed[role] = slot++;
            used[role] = true;
        }
    }
    if (slot != role_count) {
        free(parsed);
        free(used);
        return false;
    }
    for (int i = 0; i < role_count; ++i) roles[i] = parsed[i];
    free(parsed);
    free(used);
    return true;
}

static char options_role_char(int role, int role_count) {
    (void)role_count;
    if (role >= 0 && role < 26) return (char)('A' + role);
    if (role >= 0 && role <= 9) return (char)('0' + role);
    return '?';
}

static int options_role_count(const options_t *opt) {
    return opt->pane_count;
}

static bool options_ensure_role_capacity(options_t *opt, int role_count) {
    if (opt->role_cap >= role_count) return true;
    int old_cap = opt->role_cap;
    int *next = realloc(opt->roles, (size_t)role_count * sizeof(*next));
    if (!next) return false;
    opt->roles = next;
    opt->role_cap = role_count;
    for (int i = old_cap; i < role_count; ++i) opt->roles[i] = i;
    return true;
}

static bool options_ensure_pane_capacity(options_t *opt, int pane_count) {
    if (opt->pane_cap >= pane_count) return true;
    int old_cap = opt->pane_cap;
    const char **next = malloc((size_t)pane_count * sizeof(*next));
    if (!next) return false;
    pane_media_config *next_media = malloc((size_t)pane_count * sizeof(*next_media));
    if (!next_media) {
        free(next);
        return false;
    }
    if (opt->pane_cmds) memcpy(next, opt->pane_cmds, (size_t)old_cap * sizeof(*next));
    if (opt->pane_media) memcpy(next_media, opt->pane_media, (size_t)old_cap * sizeof(*next_media));
    free(opt->pane_cmds);
    free(opt->pane_media);
    opt->pane_cmds = next;
    opt->pane_media = next_media;
    opt->pane_cap = pane_count;
    for (int i = old_cap; i < pane_count; ++i) {
        opt->pane_cmds[i] = NULL;
        opt->pane_media[i] = (pane_media_config){ .video_rotate = -1 };
    }
    return true;
}

static int options_first_cmd_pane(const options_t *opt) {
    return (opt->unified_pane_model || opt->no_video) ? 0 : 1;
}

static void options_sync_pane_cmds(options_t *opt) {
    int first_cmd_pane = options_first_cmd_pane(opt);
    if (opt->pane_cap > first_cmd_pane + 0) opt->pane_cmds[first_cmd_pane + 0] = opt->pane_a_cmd;
    if (opt->pane_cap > first_cmd_pane + 1) opt->pane_cmds[first_cmd_pane + 1] = opt->pane_b_cmd;
    if (opt->pane_cap > first_cmd_pane + 2) opt->pane_cmds[first_cmd_pane + 2] = opt->pane_c_cmd;
    if (opt->pane_cap > first_cmd_pane + 3) opt->pane_cmds[first_cmd_pane + 3] = opt->pane_d_cmd;
}

static bool options_split_tree_roles_in_range(const char *spec, int role_count) {
    const char *p = spec;
    const char *prev = NULL;
    if (!spec || !*spec) return true;
    while (*p) {
        if (!isdigit((unsigned char)*p)) {
            if (!isspace((unsigned char)*p)) prev = p;
            ++p;
            continue;
        }
        char *end = NULL;
        long value = strtol(p, &end, 10);
        if (end == p) return false;
        if (!prev || *prev == '(' || *prev == ',') {
            if (value < 0 || value >= role_count) return false;
        }
        p = end;
    }
    return true;
}

static const char *options_translate_legacy_split_tree_spec(const char *spec, int role_count) {
    if (!spec || !*spec) return spec;
    return options_split_tree_roles_in_range(spec, role_count) ? spec : NULL;
}

static void options_promote_legacy_panes(options_t *opt) {
    if (!opt || opt->no_video || opt->pane_count < 1) return;
    int old_count = opt->pane_count;
    opt->pane_count = old_count + 1;
    if (!options_ensure_pane_capacity(opt, opt->pane_count) ||
        !options_ensure_role_capacity(opt, options_role_count(opt))) {
        return;
    }
    memmove(&opt->pane_cmds[1], &opt->pane_cmds[0], (size_t)old_count * sizeof(*opt->pane_cmds));
    memmove(&opt->pane_media[1], &opt->pane_media[0], (size_t)old_count * sizeof(*opt->pane_media));
    opt->pane_cmds[0] = NULL;
    opt->pane_media[0] = (pane_media_config){ .video_rotate = -1 };
}

static bool options_roles_string_has_legacy_hint(const char *roles) {
    if (!roles) return false;
    for (const char *p = roles; *p; ++p) {
        if (strchr("CcAaBbDdEe", *p)) return true;
    }
    return false;
}

static bool options_pane_media_has_payload(const pane_media_config *pane_media) {
    if (!pane_media) return false;
    return pane_media->enabled ||
           pane_media->video_count > 0 ||
           pane_media->playlist_path != NULL ||
           pane_media->playlist_ext != NULL ||
           pane_media->playlist_fifo != NULL ||
           pane_media->mpv_out_path != NULL ||
           pane_media->panscan != NULL ||
           pane_media->video_rotate >= 0 ||
           pane_media->n_mpv_opts > 0;
}

static bool options_root_media_present(const options_t *opt) {
    if (!opt) return false;
    return opt->video_count > 0 ||
           opt->playlist_path != NULL ||
           opt->playlist_ext != NULL ||
           opt->playlist_fifo != NULL ||
           opt->mpv_out_path != NULL ||
           opt->panscan != NULL ||
           opt->video_rotate != 0 ||
           opt->n_mpv_opts > 0;
}

static bool options_split_tree_has_legacy_shape(const char *spec, int pane_count) {
    bool *seen = NULL;
    int seen_count = 0;
    const char *p = spec;
    const char *prev = NULL;
    bool legacy_shape = false;
    if (!spec || !*spec || pane_count < 1) return false;
    seen = calloc((size_t)(pane_count + 1), sizeof(*seen));
    if (!seen) return false;
    while (*p) {
        if (!isdigit((unsigned char)*p)) {
            if (!isspace((unsigned char)*p)) prev = p;
            ++p;
            continue;
        }
        char *end = NULL;
        long value = strtol(p, &end, 10);
        if (end == p) break;
        if (!prev || *prev == '(' || *prev == ',') {
            if (value < 0 || value > pane_count) {
                free(seen);
                return false;
            }
            if (!seen[value]) {
                seen[value] = true;
                ++seen_count;
            }
        }
        p = end;
    }
    legacy_shape = seen_count == pane_count + 1;
    if (legacy_shape) {
        for (int i = 0; i <= pane_count; ++i) {
            if (!seen[i]) {
                legacy_shape = false;
                break;
            }
        }
    }
    free(seen);
    return legacy_shape;
}

static bool options_should_infer_legacy_pane_model(const options_t *opt, const char *roles_arg) {
    bool root_media_present = options_root_media_present(opt);
    bool legacy_hint = options_roles_string_has_legacy_hint(roles_arg);
    bool split_tree_legacy_hint =
        !root_media_present &&
        !legacy_hint &&
        options_split_tree_has_legacy_shape(opt ? opt->split_tree_spec : NULL, opt ? opt->pane_count : 0) &&
        !(opt && opt->pane_count > 0 && options_pane_media_has_payload(&opt->pane_media[0]));
    return root_media_present || legacy_hint || split_tree_legacy_hint;
}

void push_video(options_t *opt, const char *path) {
    if (opt->video_count == opt->video_cap) {
        int ncap = opt->video_cap ? opt->video_cap * 2 : 8;
        opt->videos = realloc(opt->videos, (size_t)ncap * sizeof(video_item));
        memset(opt->videos + opt->video_cap, 0, (size_t)(ncap - opt->video_cap) * sizeof(video_item));
        opt->video_cap = ncap;
    }
    video_item *vi = &opt->videos[opt->video_count++];
    memset(vi, 0, sizeof(*vi));
    vi->path = path;
    opt->video_path = path;
}

void push_pane_video(pane_media_config *pane_media, const char *path) {
    if (!pane_media) return;
    if (pane_media->video_count == pane_media->video_cap) {
        int ncap = pane_media->video_cap ? pane_media->video_cap * 2 : 8;
        video_item *next = realloc(pane_media->videos, (size_t)ncap * sizeof(video_item));
        if (!next) return;
        pane_media->videos = next;
        memset(pane_media->videos + pane_media->video_cap, 0,
               (size_t)(ncap - pane_media->video_cap) * sizeof(video_item));
        pane_media->video_cap = ncap;
    }
    video_item *vi = &pane_media->videos[pane_media->video_count++];
    memset(vi, 0, sizeof(*vi));
    vi->path = path;
}

void push_video_opt(video_item *vi, const char *kv) {
    if (!vi) return;
    if (vi->nopts == vi->cap) {
        int ncap = vi->cap ? vi->cap * 2 : 8;
        vi->opts = realloc(vi->opts, (size_t)ncap * sizeof(char *));
        vi->cap = ncap;
    }
    vi->opts[vi->nopts++] = kv;
}

void push_pane_mpv_opt(pane_media_config *pane_media, const char *kv) {
    if (!pane_media) return;
    if (pane_media->n_mpv_opts == pane_media->cap_mpv_opts) {
        int ncap = pane_media->cap_mpv_opts ? pane_media->cap_mpv_opts * 2 : 8;
        const char **next = realloc(pane_media->mpv_opts, (size_t)ncap * sizeof(*next));
        if (!next) return;
        pane_media->mpv_opts = next;
        pane_media->cap_mpv_opts = ncap;
    }
    pane_media->mpv_opts[pane_media->n_mpv_opts++] = kv;
}

const char *trim(char *s) {
    if (!s) return s;
    while (isspace((unsigned char)*s)) s++;
    char *e = s + strlen(s);
    while (e > s && isspace((unsigned char)e[-1])) *--e = '\0';
    return s;
}

void parse_playlist_ext(options_t *opt, const char *file) {
    FILE *f = fopen(file, "r");
    if (!f) {
        perror("playlist-ext open");
        return;
    }
    char *line = NULL;
    size_t cap = 0;
    ssize_t n;
    while ((n = getline(&line, &cap, f)) != -1) {
        while (n > 0 && (line[n - 1] == '\n' || line[n - 1] == '\r')) line[--n] = '\0';
        char *p = (char *)trim(line);
        if (*p == '#' || *p == '\0') continue;
        char *bar = strchr(p, '|');
        char *path = p;
        char *optstr = NULL;
        if (bar) {
            *bar = '\0';
            optstr = bar + 1;
        }
        push_video(opt, strdup(trim(path)));
        if (optstr) {
            video_item *vi = &opt->videos[opt->video_count - 1];
            char *opts_dup = strdup(optstr);
            char *tok = strtok(opts_dup, ",");
            while (tok) {
                push_video_opt(vi, strdup(trim(tok)));
                tok = strtok(NULL, ",");
            }
            free(opts_dup);
        }
    }
    free(line);
    fclose(f);
}

void mpv_append_line(mpv_handle *mpv, const char *line) {
    if (!mpv || !line) return;
    char *dup = strdup(line);
    if (!dup) return;
    char *p = (char *)trim(dup);
    if (*p == '#' || *p == '\0') {
        free(dup);
        return;
    }
    char *bar = strchr(p, '|');
    char *optstr = NULL;
    if (bar) {
        *bar = '\0';
        optstr = bar + 1;
    }
    if (!optstr) {
        const char *cmd[] = {"loadfile", p, "append", NULL};
        mpv_command_async(mpv, 0, cmd);
    } else {
        mpv_node root;
        memset(&root, 0, sizeof(root));
        root.format = MPV_FORMAT_NODE_ARRAY;
        root.u.list = malloc(sizeof(*root.u.list));
        root.u.list->num = 0;
        root.u.list->values = NULL;
        root.u.list->keys = NULL;
#define PUSH_STR_NODE(str) do { \
    root.u.list->values = realloc(root.u.list->values, sizeof(mpv_node) * (root.u.list->num + 1)); \
    root.u.list->values[root.u.list->num].format = MPV_FORMAT_STRING; \
    root.u.list->values[root.u.list->num].u.string = strdup(str); \
    root.u.list->num++; \
} while (0)
        PUSH_STR_NODE("loadfile");
        PUSH_STR_NODE(p);
        PUSH_STR_NODE("append");
#undef PUSH_STR_NODE
        mpv_node map;
        memset(&map, 0, sizeof(map));
        map.format = MPV_FORMAT_NODE_MAP;
        map.u.list = malloc(sizeof(*map.u.list));
        map.u.list->num = 0;
        map.u.list->values = NULL;
        map.u.list->keys = NULL;
        char *opts_dup = strdup(optstr);
        char *tok = strtok(opts_dup, ",");
        while (tok) {
            char *kv = (char *)trim(tok);
            char *eq = strchr(kv, '=');
            if (eq) {
                *eq = '\0';
                char *key = strdup(trim(kv));
                char *val = strdup(trim(eq + 1));
                map.u.list->keys = realloc(map.u.list->keys, sizeof(char *) * (map.u.list->num + 1));
                map.u.list->values = realloc(map.u.list->values, sizeof(mpv_node) * (map.u.list->num + 1));
                map.u.list->keys[map.u.list->num] = key;
                map.u.list->values[map.u.list->num].format = MPV_FORMAT_STRING;
                map.u.list->values[map.u.list->num].u.string = val;
                map.u.list->num++;
            }
            tok = strtok(NULL, ",");
        }
        free(opts_dup);
        root.u.list->values = realloc(root.u.list->values, sizeof(mpv_node) * (root.u.list->num + 1));
        root.u.list->keys = realloc(root.u.list->keys, sizeof(char *) * (root.u.list->num + 1));
        root.u.list->keys[root.u.list->num] = strdup("options");
        root.u.list->values[root.u.list->num] = map;
        root.u.list->num++;
        mpv_command_node_async(mpv, 0, &root);
        mpv_free_node_contents(&root);
    }
    free(dup);
}

char **tokenize_file(const char *path, int *argc_out) {
    FILE *f = fopen(path, "r");
    if (!f) return NULL;
    char **args = NULL;
    int n = 0, cap = 0, c;
    int state = 0;
    char buf[4096];
    int bi = 0;
    while ((c = fgetc(f)) != EOF) {
        if (state == 0) {
            if (c == '#') {
                while (c != EOF && c != '\n') c = fgetc(f);
                if (c == EOF) break;
                continue;
            }
            if (c == '\'') state = 2;
            else if (c == '"') state = 3;
            else if (c == '\n' || c == ' ' || c == '\t' || c == '\r') continue;
            else {
                if (bi < (int)sizeof(buf) - 1) buf[bi++] = (char)c;
                state = 1;
            }
        } else if (state == 1) {
            if (c == '\n' || c == ' ' || c == '\t' || c == '\r') {
                buf[bi] = '\0';
                if (n == cap) {
                    cap = cap ? cap * 2 : 16;
                    args = realloc(args, (size_t)cap * sizeof(char *));
                }
                args[n++] = strdup(buf);
                bi = 0;
                state = 0;
            } else if (c == '\'') state = 2;
            else if (c == '"') state = 3;
            else if (bi < (int)sizeof(buf) - 1) buf[bi++] = (char)c;
        } else if (state == 2) {
            if (c == '\'') state = 1;
            else if (bi < (int)sizeof(buf) - 1) buf[bi++] = (char)c;
        } else if (state == 3) {
            if (c == '"') state = 1;
            else if (bi < (int)sizeof(buf) - 1) buf[bi++] = (char)c;
        }
    }
    if (bi > 0) {
        buf[bi] = '\0';
        if (n == cap) {
            cap = cap ? cap * 2 : 16;
            args = realloc(args, (size_t)cap * sizeof(char *));
        }
        args[n++] = strdup(buf);
    }
    fclose(f);
    *argc_out = n;
    return args;
}

static void print_usage(const char *exe) {
    fprintf(stderr,
        "KMS Mosaic - tiled video + terminal panes (Linux KMS console)\n\n"
        "Usage:\n"
        "  %s [options] [video...]\n\n"
        "Core options:\n"
        "  --connector ID|NAME     Select DRM output (e.g. 42, HDMI-A-1, DP-1). Default: first connected.\n"
        "  --mode WxH[@Hz]         Mode like 1920x1080@60. Default: preferred.\n"
        "  --rotate 0|90|180|270   Presentation rotation (affects layout orientation).\n"
        "  --font-size PX          Terminal font pixel size (default 18).\n"
        "  --right-frac PCT        Right column width percentage (default 33).\n"
        "  --video-frac PCT        Override: video width percentage.\n"
        "  --pane-split PCT        Top row height percentage for split layouts (default 50).\n"
        "  --pane-count N          Number of terminal panes to create (default %d).\n"
        "  --pane-a \"CMD\"           Command for Pane A (default: btop).\n"
        "  --pane-b \"CMD\"           Command for Pane B (default: tail -F /var/log/syslog -n 500).\n"
        "  --pane-c \"CMD\"           Command for Pane C.\n"
        "  --pane-d \"CMD\"           Command for Pane D.\n"
        "  --pane N \"CMD\"           Command for pane index N (1-based).\n"
        "  --pane-media N           Make pane index N an mpv/media pane.\n"
        "  --pane-playlist N FILE   Playlist for media pane N.\n"
        "  --pane-playlist-extended N FILE\n"
        "                           Extended playlist for media pane N.\n"
        "  --pane-playlist-fifo N FILE\n"
        "                           FIFO to append playlist entries into media pane N.\n"
        "  --pane-video N PATH      Add a video to media pane N (repeatable).\n"
        "  --pane-mpv-opt N K=V     Per-pane mpv option for media pane N (repeatable).\n"
        "  --pane-mpv-out N FILE   Write pane-local mpv logs/events to FILE or FIFO.\n"
        "  --pane-video-rotate N D Per-pane pass-through to mpv video-rotate.\n"
        "  --pane-panscan N VAL    Per-pane pass-through to mpv panscan.\n"
        "  --pane-model MODEL      Pane indexing model: unified (default) or legacy.\n"
        "  --split-tree SPEC        Explicit split-tree layout override.\n"
        "  --layout M              stack | row | 2x1 | 1x2 | 2over1 | 1over2 | overlay\n"
        "  --roles ORDER           Pane order, using pane indices or letters like ABCD.\n"
        "  --fs-cycle-sec SEC      Fullscreen cycle interval for 'c' key.\n\n"
        "Display/KMS:\n"
        "  --atomic                Use DRM atomic modesetting (experimental; falls back on failure).\n"
        "  --atomic-nonblock       Use nonblocking atomic flips (event-driven).\n"
        "  --gl-finish             Call glFinish() before flips (serialize GPU).\n\n"
        "Video/playlist:\n"
        "  --video PATH            Add a video (repeatable). Bare args are treated as --video.\n"
        "  --video-opt K=V         Per-video options (repeatable, applies to the last --video).\n"
        "  --playlist FILE         Load playlist file.\n"
        "  --playlist-extended F   Extended playlist (path | k=v,k=v per line).\n"
        "  --playlist-fifo F       FIFO to append playlist entries from.\n"
        "  --loop-file             Loop current file indefinitely.\n"
        "  --loop                  Shorthand for --loop-file.\n"
        "  --loop-playlist         Loop the whole playlist.\n"
        "  --shuffle               Randomize playlist order.\n"
        "  --mpv-opt K=V           Global mpv option (repeatable).\n"
        "  --mpv-out FILE          Write mpv logs/events to FILE or FIFO.\n"
        "  --video-rotate DEG      Pass-through to mpv video-rotate.\n"
        "  --panscan VAL           Pass-through to mpv panscan.\n\n"
        "Config and misc:\n"
        "  --config FILE           Load options from file (supports quotes and # comments).\n"
        "  --save-config FILE      Save current options to file.\n"
        "  --save-config-default   Save to the default config path.\n"
        "  --no-config             Do not auto-load default config.\n"
        "  --list-connectors       Print connectors/modes and exit.\n"
        "  --no-video              Disable the video pane.\n"
        "  --no-panes              Disable terminal panes.\n"
        "  --smooth                Apply a sensible playback preset.\n"
        "  --gl-test               Render a diagnostic GL gradient and exit.\n"
        "  --diag                  Print GL/driver diagnostics and exit.\n"
        "  --debug                 Verbose logging.\n\n"
        "Defaults and notes:\n"
        "  - OSD is off by default (toggle in Control Mode with 'o').\n"
        "  - If a single video is provided (no playlist), --loop is assumed.\n"
        "  - Controls are gated behind Control Mode so panes and video receive keys normally.\n\n"
        "Controls (toggle Control Mode with Ctrl+E):\n"
        "  Tab           Cycle focus among the video and pane slots.\n"
        "  l / L         Cycle layouts forward/back.\n"
        "  r / R         Rotate slot roles (and reverse).\n"
        "  t             Swap the focused slot with the next slot.\n"
        "  z             Fullscreen focused pane.\n"
        "  c             Cycle fullscreen panes.\n"
        "  o             Toggle OSD visibility.\n"
        "  (Help shown automatically in Control Mode)\n"
        "  Ctrl+Q        Quit (only active in Control Mode).\n\n"
        "Always:\n"
        "  Ctrl+P        Toggle mpv panscan.\n\n",
        exe, KMS_MOSAIC_DEFAULT_PANE_COUNT);
}

int options_parse_cli(options_t *opt, int argc, char **argv, int *debug) {
    opt->pane_count = KMS_MOSAIC_DEFAULT_PANE_COUNT;
    const char *roles_arg = NULL;
    bool pane_model_explicit = false;
    bool used_legacy_pane_model = false;
    if (!options_ensure_pane_capacity(opt, opt->pane_count) ||
        !options_ensure_role_capacity(opt, options_role_count(opt))) {
        fprintf(stderr, "Failed to allocate option storage.\n");
        return 1;
    }
    const char *cfg = NULL;
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--config") && i + 1 < argc) {
            cfg = argv[i + 1];
            break;
        }
    }
    if (!cfg) {
        const char *def = default_config_path();
        if (!opt->no_config && access(def, R_OK) == 0) cfg = def;
    }
    opt->unified_pane_model = true;

    char **merged = NULL;
    int margc = 0;
    if (cfg) {
        int cargc = 0;
        char **cargv = tokenize_file(cfg, &cargc);
        merged = malloc(sizeof(char *) * (size_t)(1 + cargc + argc));
        merged[margc++] = argv[0];
        for (int i = 0; i < cargc; ++i) merged[margc++] = cargv[i];
        for (int i = 1; i < argc; ++i) {
            if (!strcmp(argv[i], "--config") && i + 1 < argc) {
                ++i;
                continue;
            }
            merged[margc++] = argv[i];
        }
        argv = merged;
        argc = margc;
    }

    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--video") && i + 1 < argc) {
            push_video(opt, argv[++i]);
        } else if (!strcmp(argv[i], "--video-opt") && i + 1 < argc) {
            if (opt->video_count > 0) push_video_opt(&opt->videos[opt->video_count - 1], argv[++i]);
            else {
                if (opt->n_mpv_opts == opt->cap_mpv_opts) {
                    int nc = opt->cap_mpv_opts ? opt->cap_mpv_opts * 2 : 8;
                    opt->mpv_opts = realloc(opt->mpv_opts, (size_t)nc * sizeof(char *));
                    opt->cap_mpv_opts = nc;
                }
                opt->mpv_opts[opt->n_mpv_opts++] = argv[++i];
            }
        } else if (!strcmp(argv[i], "--playlist") && i + 1 < argc) opt->playlist_path = argv[++i];
        else if (!strcmp(argv[i], "--config") && i + 1 < argc) opt->config_file = argv[++i];
        else if (!strcmp(argv[i], "--save-config") && i + 1 < argc) opt->save_config_file = argv[++i];
        else if (!strcmp(argv[i], "--playlist-extended") && i + 1 < argc) opt->playlist_ext = argv[++i];
        else if (!strcmp(argv[i], "--playlist-fifo") && i + 1 < argc) opt->playlist_fifo = argv[++i];
        else if (!strcmp(argv[i], "--mpv-out") && i + 1 < argc) opt->mpv_out_path = argv[++i];
        else if (!strcmp(argv[i], "--connector") && i + 1 < argc) opt->connector_opt = argv[++i];
        else if (!strcmp(argv[i], "--mode") && i + 1 < argc) parse_mode(argv[++i], &opt->mode_w, &opt->mode_h, &opt->mode_hz);
        else if (!strcmp(argv[i], "--rotate") && i + 1 < argc) opt->rotation = parse_rot(argv[++i]);
        else if (!strcmp(argv[i], "--font-size") && i + 1 < argc) opt->font_px = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--right-frac") && i + 1 < argc) opt->right_frac_pct = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--video-frac") && i + 1 < argc) opt->video_frac_pct = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--pane-split") && i + 1 < argc) opt->pane_split_pct = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--pane-a") && i + 1 < argc) {
            opt->pane_a_cmd = argv[++i];
        }
        else if (!strcmp(argv[i], "--pane-b") && i + 1 < argc) {
            opt->pane_b_cmd = argv[++i];
        }
        else if (!strcmp(argv[i], "--pane-c") && i + 1 < argc) {
            opt->pane_c_cmd = argv[++i];
        }
        else if (!strcmp(argv[i], "--pane-d") && i + 1 < argc) {
            opt->pane_d_cmd = argv[++i];
        }
        else if (!strcmp(argv[i], "--pane-count") && i + 1 < argc) {
            opt->pane_count = atoi(argv[++i]);
            if (opt->pane_count < 1) opt->pane_count = 1;
            if (!options_ensure_pane_capacity(opt, opt->pane_count) ||
                !options_ensure_role_capacity(opt, options_role_count(opt))) {
                fprintf(stderr, "Failed to allocate pane storage.\n");
                return 1;
            }
            options_sync_pane_cmds(opt);
        }
        else if (!strcmp(argv[i], "--pane") && i + 2 < argc) {
            int pane_index = atoi(argv[++i]) - 1;
            const char *pane_cmd = argv[++i];
            if (pane_index >= 0) {
                if (pane_index + 1 > opt->pane_count) opt->pane_count = pane_index + 1;
                if (!options_ensure_pane_capacity(opt, opt->pane_count) ||
                    !options_ensure_role_capacity(opt, options_role_count(opt))) {
                    fprintf(stderr, "Failed to allocate pane storage.\n");
                    return 1;
                }
                opt->pane_cmds[pane_index] = pane_cmd;
            }
        }
        else if (!strcmp(argv[i], "--pane-media") && i + 1 < argc) {
            int pane_index = atoi(argv[++i]) - 1;
            if (pane_index >= 0) {
                if (pane_index + 1 > opt->pane_count) opt->pane_count = pane_index + 1;
                if (!options_ensure_pane_capacity(opt, opt->pane_count) ||
                    !options_ensure_role_capacity(opt, options_role_count(opt))) {
                    fprintf(stderr, "Failed to allocate pane storage.\n");
                    return 1;
                }
                opt->pane_media[pane_index].enabled = true;
            }
        }
        else if (!strcmp(argv[i], "--pane-playlist") && i + 2 < argc) {
            int pane_index = atoi(argv[++i]) - 1;
            const char *playlist_path = argv[++i];
            if (pane_index >= 0) {
                if (pane_index + 1 > opt->pane_count) opt->pane_count = pane_index + 1;
                if (!options_ensure_pane_capacity(opt, opt->pane_count) ||
                    !options_ensure_role_capacity(opt, options_role_count(opt))) {
                    fprintf(stderr, "Failed to allocate pane storage.\n");
                    return 1;
                }
                opt->pane_media[pane_index].enabled = true;
                opt->pane_media[pane_index].playlist_path = playlist_path;
            }
        }
        else if (!strcmp(argv[i], "--pane-playlist-extended") && i + 2 < argc) {
            int pane_index = atoi(argv[++i]) - 1;
            const char *playlist_ext = argv[++i];
            if (pane_index >= 0) {
                if (pane_index + 1 > opt->pane_count) opt->pane_count = pane_index + 1;
                if (!options_ensure_pane_capacity(opt, opt->pane_count) ||
                    !options_ensure_role_capacity(opt, options_role_count(opt))) {
                    fprintf(stderr, "Failed to allocate pane storage.\n");
                    return 1;
                }
                opt->pane_media[pane_index].enabled = true;
                opt->pane_media[pane_index].playlist_ext = playlist_ext;
            }
        }
        else if (!strcmp(argv[i], "--pane-playlist-fifo") && i + 2 < argc) {
            int pane_index = atoi(argv[++i]) - 1;
            const char *playlist_fifo = argv[++i];
            if (pane_index >= 0) {
                if (pane_index + 1 > opt->pane_count) opt->pane_count = pane_index + 1;
                if (!options_ensure_pane_capacity(opt, opt->pane_count) ||
                    !options_ensure_role_capacity(opt, options_role_count(opt))) {
                    fprintf(stderr, "Failed to allocate pane storage.\n");
                    return 1;
                }
                opt->pane_media[pane_index].enabled = true;
                opt->pane_media[pane_index].playlist_fifo = playlist_fifo;
            }
        }
        else if (!strcmp(argv[i], "--pane-video") && i + 2 < argc) {
            int pane_index = atoi(argv[++i]) - 1;
            const char *video_path = argv[++i];
            if (pane_index >= 0) {
                if (pane_index + 1 > opt->pane_count) opt->pane_count = pane_index + 1;
                if (!options_ensure_pane_capacity(opt, opt->pane_count) ||
                    !options_ensure_role_capacity(opt, options_role_count(opt))) {
                    fprintf(stderr, "Failed to allocate pane storage.\n");
                    return 1;
                }
                opt->pane_media[pane_index].enabled = true;
                push_pane_video(&opt->pane_media[pane_index], video_path);
            }
        }
        else if (!strcmp(argv[i], "--pane-mpv-opt") && i + 2 < argc) {
            int pane_index = atoi(argv[++i]) - 1;
            const char *kv = argv[++i];
            if (pane_index >= 0) {
                if (pane_index + 1 > opt->pane_count) opt->pane_count = pane_index + 1;
                if (!options_ensure_pane_capacity(opt, opt->pane_count) ||
                    !options_ensure_role_capacity(opt, options_role_count(opt))) {
                    fprintf(stderr, "Failed to allocate pane storage.\n");
                    return 1;
                }
                opt->pane_media[pane_index].enabled = true;
                push_pane_mpv_opt(&opt->pane_media[pane_index], kv);
            }
        }
        else if (!strcmp(argv[i], "--pane-mpv-out") && i + 2 < argc) {
            int pane_index = atoi(argv[++i]) - 1;
            const char *mpv_out_path = argv[++i];
            if (pane_index >= 0) {
                if (pane_index + 1 > opt->pane_count) opt->pane_count = pane_index + 1;
                if (!options_ensure_pane_capacity(opt, opt->pane_count) ||
                    !options_ensure_role_capacity(opt, options_role_count(opt))) {
                    fprintf(stderr, "Failed to allocate pane storage.\n");
                    return 1;
                }
                opt->pane_media[pane_index].enabled = true;
                opt->pane_media[pane_index].mpv_out_path = mpv_out_path;
            }
        }
        else if (!strcmp(argv[i], "--pane-video-rotate") && i + 2 < argc) {
            int pane_index = atoi(argv[++i]) - 1;
            int video_rotate = atoi(argv[++i]);
            if (pane_index >= 0) {
                if (pane_index + 1 > opt->pane_count) opt->pane_count = pane_index + 1;
                if (!options_ensure_pane_capacity(opt, opt->pane_count) ||
                    !options_ensure_role_capacity(opt, options_role_count(opt))) {
                    fprintf(stderr, "Failed to allocate pane storage.\n");
                    return 1;
                }
                opt->pane_media[pane_index].enabled = true;
                opt->pane_media[pane_index].video_rotate = video_rotate;
            }
        }
        else if (!strcmp(argv[i], "--pane-panscan") && i + 2 < argc) {
            int pane_index = atoi(argv[++i]) - 1;
            const char *panscan = argv[++i];
            if (pane_index >= 0) {
                if (pane_index + 1 > opt->pane_count) opt->pane_count = pane_index + 1;
                if (!options_ensure_pane_capacity(opt, opt->pane_count) ||
                    !options_ensure_role_capacity(opt, options_role_count(opt))) {
                    fprintf(stderr, "Failed to allocate pane storage.\n");
                    return 1;
                }
                opt->pane_media[pane_index].enabled = true;
                opt->pane_media[pane_index].panscan = panscan;
            }
        }
        else if (!strcmp(argv[i], "--list-connectors")) opt->list_connectors = true;
        else if (!strcmp(argv[i], "--no-video")) opt->no_video = true;
        else if (!strcmp(argv[i], "--no-panes")) opt->no_panes = true;
        else if (!strcmp(argv[i], "--diag")) opt->diag = true;
        else if (!strcmp(argv[i], "--gl-test")) opt->gl_test = true;
        else if (!strcmp(argv[i], "--no-config")) opt->no_config = true;
        else if (!strcmp(argv[i], "--smooth")) opt->smooth = true;
        else if (!strcmp(argv[i], "--split-tree") && i + 1 < argc) opt->split_tree_spec = argv[++i];
        else if (!strcmp(argv[i], "--pane-model") && i + 1 < argc) {
            const char *pane_model = argv[++i];
            pane_model_explicit = true;
            if (!strcmp(pane_model, "unified")) opt->unified_pane_model = true;
            else if (!strcmp(pane_model, "legacy")) opt->unified_pane_model = false;
        }
        else if (!strcmp(argv[i], "--layout") && i + 1 < argc) {
            int mode = parse_layout_mode(argv[++i]);
            if (mode >= 0) opt->layout_mode = mode;
        } else if ((!strcmp(argv[i], "--landscape-layout") || !strcmp(argv[i], "--portrait-layout")) && i + 1 < argc) {
            int mode = parse_layout_mode(argv[++i]);
            if (mode >= 0) opt->layout_mode = mode;
        } else if (!strcmp(argv[i], "--fs-cycle-sec") && i + 1 < argc) opt->fs_cycle_sec = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--roles") && i + 1 < argc) roles_arg = argv[++i];
        else if (!strcmp(argv[i], "--loop-file")) opt->loop_file = true;
        else if (!strcmp(argv[i], "--loop")) opt->loop_flag = true;
        else if (!strcmp(argv[i], "--loop-playlist")) opt->loop_playlist = true;
        else if (!strcmp(argv[i], "--shuffle") || !strcmp(argv[i], "--randomize")) opt->shuffle = true;
        else if (!strcmp(argv[i], "--no-osd")) opt->no_osd = true;
        else if (!strcmp(argv[i], "--atomic")) opt->use_atomic = true;
        else if (!strcmp(argv[i], "--atomic-nonblock")) { opt->use_atomic = true; opt->atomic_nonblock = true; }
        else if (!strcmp(argv[i], "--gl-finish")) opt->gl_finish = true;
        else if (!strcmp(argv[i], "--mpv-opt") && i + 1 < argc) {
            if (opt->n_mpv_opts == opt->cap_mpv_opts) {
                int nc = opt->cap_mpv_opts ? opt->cap_mpv_opts * 2 : 8;
                opt->mpv_opts = realloc(opt->mpv_opts, (size_t)nc * sizeof(char *));
                opt->cap_mpv_opts = nc;
            }
            opt->mpv_opts[opt->n_mpv_opts++] = argv[++i];
        } else if (!strcmp(argv[i], "--save-config-default")) opt->save_config_default = true;
        else if (!strcmp(argv[i], "--debug")) *debug = 1;
        else if (!strcmp(argv[i], "--video-rotate") && i + 1 < argc) opt->video_rotate = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--panscan") && i + 1 < argc) opt->panscan = argv[++i];
        else if (!strcmp(argv[i], "-h") || !strcmp(argv[i], "--help")) {
            print_usage(argv[0]);
            return 1;
        } else if (argv[i][0] != '-') {
            push_video(opt, argv[i]);
        } else {
            fprintf(stderr, "Warning: unknown option '%s' (ignored). Use --help for usage.\n", argv[i]);
        }
    }

    if (opt->playlist_ext) parse_playlist_ext(opt, opt->playlist_ext);
    if (!opt->loop_playlist && (opt->playlist_path || opt->playlist_ext || opt->playlist_fifo)) opt->loop_playlist = true;
    if (!opt->playlist_path && !opt->playlist_ext && !opt->playlist_fifo) {
        if (opt->video_count == 1 && !opt->loop_file && !opt->loop_flag) opt->loop_flag = true;
    }
    if (!pane_model_explicit) {
        opt->unified_pane_model = !options_should_infer_legacy_pane_model(opt, roles_arg);
    }
    used_legacy_pane_model = !opt->unified_pane_model;
    if (!opt->unified_pane_model) options_promote_legacy_panes(opt);
    if (opt->pane_count < 1) opt->pane_count = 1;
    {
        int first_cmd_pane = options_first_cmd_pane(opt);
        if (opt->pane_a_cmd && opt->pane_count < first_cmd_pane + 1) opt->pane_count = first_cmd_pane + 1;
        if (opt->pane_b_cmd && opt->pane_count < first_cmd_pane + 2) opt->pane_count = first_cmd_pane + 2;
        if (opt->pane_c_cmd && opt->pane_count < first_cmd_pane + 3) opt->pane_count = first_cmd_pane + 3;
        if (opt->pane_d_cmd && opt->pane_count < first_cmd_pane + 4) opt->pane_count = first_cmd_pane + 4;
    }
    if (!options_ensure_pane_capacity(opt, opt->pane_count) ||
        !options_ensure_role_capacity(opt, options_role_count(opt))) {
        fprintf(stderr, "Failed to allocate pane storage.\n");
        return 1;
    }
    if (!opt->unified_pane_model) {
        opt->split_tree_spec =
            options_translate_legacy_split_tree_spec(opt->split_tree_spec, options_role_count(opt));
        if (roles_arg) {
            opt->roles_set =
                options_parse_legacy_roles_string(roles_arg, opt->roles, options_role_count(opt));
        }
    } else if (roles_arg) {
        opt->roles_set = parse_roles_string(roles_arg, opt->roles, options_role_count(opt));
    }
    options_sync_pane_cmds(opt);
    if (used_legacy_pane_model) opt->unified_pane_model = true;
    return 0;
}

const char *default_config_path(void) {
    static char buf[512];
    if (access("/boot/config", F_OK) == 0) {
        snprintf(buf, sizeof(buf), "/boot/config/kms_mosaic.conf");
        return buf;
    }
    const char *xdg = getenv("XDG_CONFIG_HOME");
    const char *home = getenv("HOME");
    if (xdg && *xdg) {
        snprintf(buf, sizeof(buf), "%s/kms_mosaic.conf", xdg);
        return buf;
    }
    if (home && *home) {
        snprintf(buf, sizeof(buf), "%s/.config/kms_mosaic.conf", home);
        return buf;
    }
    snprintf(buf, sizeof(buf), ".kms_mosaic.conf");
    return buf;
}

void save_config(const options_t *opt, const char *path) {
    FILE *f = fopen(path, "w");
    if (!f) {
        perror("save-config");
        return;
    }
    if (opt->connector_opt) fprintf(f, "--connector '%s'\n", opt->connector_opt);
    if (opt->mode_w || opt->mode_h) fprintf(f, "--mode %dx%d@%d\n", opt->mode_w, opt->mode_h, opt->mode_hz);
    if (opt->rotation) fprintf(f, "--rotate %d\n", (int)opt->rotation);
    if (opt->font_px) fprintf(f, "--font-size %d\n", opt->font_px);
    const char *lay_str = layout_mode_name(opt->layout_mode);
    fprintf(f, "--layout %s\n", lay_str);
    if (opt->split_tree_spec && *opt->split_tree_spec) fprintf(f, "--split-tree '%s'\n", opt->split_tree_spec);
    if (opt->video_frac_pct) fprintf(f, "--video-frac %d\n", opt->video_frac_pct);
    else if (opt->right_frac_pct) fprintf(f, "--right-frac %d\n", opt->right_frac_pct);
    if (opt->pane_split_pct) fprintf(f, "--pane-split %d\n", opt->pane_split_pct);
    if (opt->roles_set) {
        int role_count = options_role_count(opt);
        fprintf(f, "--roles ");
        for (int i = 0; i < role_count; ++i) fputc(options_role_char(opt->roles[i], role_count), f);
        fputc('\n', f);
    }
    if (opt->fs_cycle_sec) fprintf(f, "--fs-cycle-sec %d\n", opt->fs_cycle_sec);
    if (opt->pane_count != KMS_MOSAIC_DEFAULT_PANE_COUNT) fprintf(f, "--pane-count %d\n", opt->pane_count);
    for (int i = 0; i < opt->pane_count; ++i) {
        if (!opt->pane_cmds[i]) continue;
        fprintf(f, "--pane %d '%s'\n", i + 1, opt->pane_cmds[i]);
    }
    for (int i = 0; i < opt->pane_count; ++i) {
        const pane_media_config *pm = &opt->pane_media[i];
        if (!pm->enabled) continue;
        fprintf(f, "--pane-media %d\n", i + 1);
        if (pm->playlist_path) fprintf(f, "--pane-playlist %d '%s'\n", i + 1, pm->playlist_path);
        if (pm->playlist_ext) fprintf(f, "--pane-playlist-extended %d '%s'\n", i + 1, pm->playlist_ext);
        if (pm->playlist_fifo) fprintf(f, "--pane-playlist-fifo %d '%s'\n", i + 1, pm->playlist_fifo);
        if (pm->mpv_out_path) fprintf(f, "--pane-mpv-out %d '%s'\n", i + 1, pm->mpv_out_path);
        if (pm->video_rotate >= 0) fprintf(f, "--pane-video-rotate %d %d\n", i + 1, pm->video_rotate);
        if (pm->panscan) fprintf(f, "--pane-panscan %d '%s'\n", i + 1, pm->panscan);
        for (int vi = 0; vi < pm->video_count; ++vi) fprintf(f, "--pane-video %d '%s'\n", i + 1, pm->videos[vi].path);
        for (int oi = 0; oi < pm->n_mpv_opts; ++oi) fprintf(f, "--pane-mpv-opt %d '%s'\n", i + 1, pm->mpv_opts[oi]);
    }
    if (opt->no_video) fprintf(f, "--no-video\n");
    if (opt->shuffle) fprintf(f, "--shuffle\n");
    for (int i = 0; i < opt->n_mpv_opts; i++) fprintf(f, "--mpv-opt '%s'\n", opt->mpv_opts[i]);
    if (opt->playlist_path) fprintf(f, "--playlist '%s'\n", opt->playlist_path);
    if (opt->playlist_ext) fprintf(f, "--playlist-extended '%s'\n", opt->playlist_ext);
    if (opt->playlist_fifo) fprintf(f, "--playlist-fifo '%s'\n", opt->playlist_fifo);
    if (opt->mpv_out_path) fprintf(f, "--mpv-out '%s'\n", opt->mpv_out_path);
    for (int i = 0; i < opt->video_count; i++) {
        const video_item *vi = &opt->videos[i];
        fprintf(f, "--video '%s'\n", vi->path);
        for (int k = 0; k < vi->nopts; k++) fprintf(f, "--video-opt '%s'\n", vi->opts[k]);
    }
    fclose(f);
}

void options_destroy(options_t *opt) {
    if (!opt) return;
    free(opt->pane_cmds);
    if (opt->pane_media) {
        for (int i = 0; i < opt->pane_cap; ++i) {
            free(opt->pane_media[i].videos);
            free(opt->pane_media[i].mpv_opts);
        }
    }
    free(opt->pane_media);
    free(opt->roles);
    opt->pane_cmds = NULL;
    opt->pane_media = NULL;
    opt->roles = NULL;
    opt->pane_cap = 0;
    opt->role_cap = 0;
}
