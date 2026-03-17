#ifndef OPTIONS_H
#define OPTIONS_H

#include <stdbool.h>

#include <mpv/client.h>

typedef enum { ROT_0 = 0, ROT_90 = 90, ROT_180 = 180, ROT_270 = 270 } rotation_t;

enum {
    KMS_MOSAIC_DEFAULT_PANE_COUNT = 2,
    KMS_MOSAIC_SLOT_VIDEO = 0,
    KMS_MOSAIC_SLOT_PANE_BASE = 1,
    KMS_MOSAIC_SLOT_PANE_A = KMS_MOSAIC_SLOT_PANE_BASE + 0,
    KMS_MOSAIC_SLOT_PANE_B = KMS_MOSAIC_SLOT_PANE_BASE + 1,
    KMS_MOSAIC_SLOT_PANE_C = KMS_MOSAIC_SLOT_PANE_BASE + 2,
    KMS_MOSAIC_SLOT_PANE_D = KMS_MOSAIC_SLOT_PANE_BASE + 3
};

typedef struct {
    const char *path;
    const char **opts;
    int nopts;
    int cap;
} video_item;

typedef struct {
    bool enabled;
    video_item *videos;
    int video_count;
    int video_cap;
    const char *playlist_path;
    const char *playlist_ext;
    const char **mpv_opts;
    int n_mpv_opts;
    int cap_mpv_opts;
} pane_media_config;

typedef struct {
    const char *video_path;
    video_item *videos;
    int video_count;
    int video_cap;
    const char *playlist_path;
    const char *playlist_ext;
    const char *connector_opt;
    int mode_w, mode_h;
    int mode_hz;
    rotation_t rotation;
    int font_px;
    int right_frac_pct;
    int pane_split_pct;
    int video_frac_pct;
    const char *split_tree_spec;
    const char **pane_cmds;
    pane_media_config *pane_media;
    int pane_cap;
    const char *pane_a_cmd;
    const char *pane_b_cmd;
    const char *pane_c_cmd;
    const char *pane_d_cmd;
    int pane_count;
    bool list_connectors;
    bool no_video;
    bool no_panes;
    bool gl_test;
    bool diag;
    bool loop_file;
    bool loop_playlist;
    bool shuffle;
    bool no_osd;
    bool loop_flag;
    int video_rotate;
    const char *panscan;
    bool no_config;
    bool smooth;
    bool atomic_nonblock;
    bool gl_finish;
    bool use_atomic;
    int layout_mode;
    int fs_cycle_sec;
    int *roles;
    int role_cap;
    bool roles_set;
    const char **mpv_opts;
    int n_mpv_opts;
    int cap_mpv_opts;
    const char *config_file;
    const char *save_config_file;
    bool save_config_default;
    const char *mpv_out_path;
    const char *playlist_fifo;
} options_t;

void parse_mode(const char *s, int *w, int *h, int *hz);
rotation_t parse_rot(const char *s);
int parse_layout_mode(const char *s);
const char *layout_mode_name(int mode);
bool parse_roles_string(const char *s, int *roles, int role_count);
void push_video(options_t *opt, const char *path);
void push_pane_video(pane_media_config *pane_media, const char *path);
void push_video_opt(video_item *vi, const char *kv);
void push_pane_mpv_opt(pane_media_config *pane_media, const char *kv);
const char *trim(char *s);
void parse_playlist_ext(options_t *opt, const char *file);
void mpv_append_line(mpv_handle *mpv, const char *line);
char **tokenize_file(const char *path, int *argc_out);
int options_parse_cli(options_t *opt, int argc, char **argv, int *debug);
void options_destroy(options_t *opt);
const char *default_config_path(void);
void save_config(const options_t *opt, const char *path);

#endif
