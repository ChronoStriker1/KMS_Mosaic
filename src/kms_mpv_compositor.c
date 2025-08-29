// Minimal DRM/GBM/EGL compositor embedding libmpv (OpenGL-cb)
// - Sets KMS mode, creates GBM/EGL surface, renders mpv into a region
// - Stubs two text panes as solid rectangles (PTY/text TODO)

#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <termios.h>
#include <ctype.h>

#include <drm.h>
#include <xf86drm.h>
#include <xf86drmMode.h>
#include <gbm.h>

#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>

#include <mpv/client.h>
#include <mpv/render_gl.h>

#include "term_pane.h"
#include "osd.h"

typedef struct {
    int fd;
    drmModeRes *res;
    drmModeConnector *conn;
    drmModeCrtc *orig_crtc;
    drmModeModeInfo mode;
    uint32_t crtc_id;
    uint32_t conn_id;
} drm_ctx;

typedef struct {
    struct gbm_device *dev;
    struct gbm_surface *surface;
    struct gbm_bo *bo, *next_bo;
    uint32_t fb_id;
} gbm_ctx;

typedef struct {
    EGLDisplay dpy;
    EGLConfig cfg;
    EGLContext ctx;
    EGLSurface surf;
} egl_ctx;

typedef struct {
    mpv_handle *mpv;
    mpv_render_context *mpv_gl;
    int wakeup_fd[2];
} mpv_ctx;

typedef enum { ROT_0=0, ROT_90=90, ROT_180=180, ROT_270=270 } rotation_t;

typedef struct {
    const char *path;
    const char **opts; int nopts; int cap;
} video_item;

typedef struct {
    // Videos / playlist
    const char *video_path; // legacy single (also used if only one --video)
    video_item *videos; int video_count; int video_cap;
    const char *playlist_path;
    const char *playlist_ext; // our extended playlist with per-line options
    const char *connector_opt; // id or name like HDMI-A-1
    int mode_w, mode_h;        // 0=auto
    int mode_hz;               // 0=auto
    rotation_t rotation;
    int font_px;               // font size in pixels
    int right_frac_pct;        // percent of screen for right column (default 33)
    int pane_split_pct;        // percent of right column height for top pane (default 50)
    int video_frac_pct;        // percent width for video (overrides right-frac)
    const char *pane_a_cmd;    // shell command for top-right
    const char *pane_b_cmd;    // shell command for bottom-right
    bool list_connectors;
    bool no_video;
    bool loop_file;
    bool loop_playlist;
    bool shuffle;
    const char **mpv_opts; int n_mpv_opts; int cap_mpv_opts; // global mpv opts key=val
    const char *config_file; const char *save_config_file; bool save_config_default;
} options_t;

// TTY restore state
static struct termios g_oldt;
static int g_have_oldt = 0;
static void restore_tty(void){ if (g_have_oldt) tcsetattr(0, TCSANOW, &g_oldt); }

static void die(const char *msg) {
    perror(msg);
    exit(1);
}

static int open_drm_card(void) {
    const char *candidates[] = {
        "/dev/dri/card0", "/dev/dri/card1", "/dev/dri/card2"
    };
    for (size_t i = 0; i < sizeof(candidates)/sizeof(candidates[0]); i++) {
        int fd = open(candidates[i], O_RDWR | O_CLOEXEC);
        if (fd >= 0) return fd;
    }
    die("open_drm_card");
    return -1;
}

static const char* conn_type_str(uint32_t type) {
    switch (type) {
        case DRM_MODE_CONNECTOR_Unknown: return "UNKNOWN";
        case DRM_MODE_CONNECTOR_VGA: return "VGA";
        case DRM_MODE_CONNECTOR_DVII: return "DVI-I";
        case DRM_MODE_CONNECTOR_DVID: return "DVI-D";
        case DRM_MODE_CONNECTOR_DVIA: return "DVI-A";
        case DRM_MODE_CONNECTOR_Composite: return "Composite";
        case DRM_MODE_CONNECTOR_SVIDEO: return "SVIDEO";
        case DRM_MODE_CONNECTOR_LVDS: return "LVDS";
        case DRM_MODE_CONNECTOR_Component: return "Component";
        case DRM_MODE_CONNECTOR_9PinDIN: return "DIN";
        case DRM_MODE_CONNECTOR_DisplayPort: return "DP";
        case DRM_MODE_CONNECTOR_HDMIA: return "HDMI-A";
        case DRM_MODE_CONNECTOR_HDMIB: return "HDMI-B";
        case DRM_MODE_CONNECTOR_TV: return "TV";
        case DRM_MODE_CONNECTOR_eDP: return "eDP";
        default: return "CONN";
    }
}

static bool mode_matches(const drmModeModeInfo *m, int w, int h, int hz) {
    if (w && m->hdisplay != (uint32_t)w) return false;
    if (h && m->vdisplay != (uint32_t)h) return false;
    if (hz) {
        int calc_hz = (int)((m->clock * 1000LL) / (m->htotal * m->vtotal));
        if (calc_hz < hz-1 || calc_hz > hz+1) return false;
    }
    return true;
}

static bool str_is_digits(const char *s) {
    if (!s||!*s) return false; for (const char *p=s; *p; ++p) if (*p<'0'||*p>'9') return false; return true;
}

static void pick_connector_mode(drm_ctx *d, const options_t *opt) {
    d->res = drmModeGetResources(d->fd);
    if (!d->res) die("drmModeGetResources");

    drmModeConnector *best_conn = NULL;
    drmModeModeInfo best_mode = {0};
    for (int i = 0; i < d->res->count_connectors; i++) {
        drmModeConnector *conn = drmModeGetConnector(d->fd, d->res->connectors[i]);
        if (!conn) continue;
        if (conn->connection != DRM_MODE_CONNECTED || conn->count_modes == 0) {
            drmModeFreeConnector(conn); continue;
        }
        bool chosen = false;
        if (opt->connector_opt) {
            // match by id or name
            if (str_is_digits(opt->connector_opt)) {
                if (conn->connector_id == (uint32_t)atoi(opt->connector_opt)) chosen = true;
            } else {
                char namebuf[32];
                snprintf(namebuf, sizeof namebuf, "%s-%u", conn_type_str(conn->connector_type), conn->connector_type_id);
                if (strcmp(namebuf, opt->connector_opt)==0) chosen = true;
            }
        } else {
            chosen = true; // first connected
        }
        if (!chosen) { drmModeFreeConnector(conn); continue; }

        // Pick mode: if specified search for match; else prefer first preferred
        drmModeModeInfo chosen_mode = conn->modes[0];
        if (opt->mode_w || opt->mode_h || opt->mode_hz) {
            bool found=false;
            for (int mi=0; mi<conn->count_modes; mi++) {
                if (mode_matches(&conn->modes[mi], opt->mode_w, opt->mode_h, opt->mode_hz)) {
                    chosen_mode = conn->modes[mi];
                    found = true; break;
                }
            }
            if (!found) { drmModeFreeConnector(conn); continue; }
        }
        best_conn = conn; best_mode = chosen_mode; break;
    }
    if (!best_conn) die("no suitable connector/mode");
    d->conn = best_conn;
    d->conn_id = best_conn->connector_id;

    // Find an encoder + CRTC
    drmModeEncoder *enc = NULL;
    if (best_conn->encoder_id)
        enc = drmModeGetEncoder(d->fd, best_conn->encoder_id);
    if (!enc) {
        // try first encoder
        for (int i = 0; i < best_conn->count_encoders; i++) {
            enc = drmModeGetEncoder(d->fd, best_conn->encoders[i]);
            if (enc) break;
        }
    }
    if (!enc) die("no encoder");

    uint32_t crtc_id = enc->crtc_id;
    if (!crtc_id) {
        // find a CRTC compatible with encoder
        for (int i = 0; i < d->res->count_crtcs; i++) {
            if (enc->possible_crtcs & (1 << i)) {
                crtc_id = d->res->crtcs[i];
                break;
            }
        }
    }
    drmModeFreeEncoder(enc);
    if (!crtc_id) die("no crtc");

    d->crtc_id = crtc_id;
    d->orig_crtc = drmModeGetCrtc(d->fd, crtc_id);
    d->mode = best_mode;
}

static void gbm_init(gbm_ctx *g, int drm_fd, int w, int h) {
    g->dev = gbm_create_device(drm_fd);
    if (!g->dev) die("gbm_create_device");
    g->surface = gbm_surface_create(g->dev, w, h, GBM_FORMAT_XRGB8888,
                                    GBM_BO_USE_SCANOUT | GBM_BO_USE_RENDERING);
    if (!g->surface) die("gbm_surface_create");
}

static void egl_init(egl_ctx *e, gbm_ctx *g) {
    e->dpy = eglGetDisplay((EGLNativeDisplayType)g->dev);
    if (e->dpy == EGL_NO_DISPLAY) die("eglGetDisplay");
    if (!eglInitialize(e->dpy, NULL, NULL)) die("eglInitialize");
    static const EGLint cfg_attribs[] = {
        EGL_SURFACE_TYPE, EGL_WINDOW_BIT,
        EGL_RED_SIZE, 8,
        EGL_GREEN_SIZE, 8,
        EGL_BLUE_SIZE, 8,
        EGL_ALPHA_SIZE, 0,
        EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT,
        EGL_NONE
    };
    EGLint ncfg;
    if (!eglChooseConfig(e->dpy, cfg_attribs, &e->cfg, 1, &ncfg) || ncfg != 1)
        die("eglChooseConfig");
    static const EGLint ctx_attribs[] = {EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE};
    e->ctx = eglCreateContext(e->dpy, e->cfg, EGL_NO_CONTEXT, ctx_attribs);
    if (e->ctx == EGL_NO_CONTEXT) die("eglCreateContext");
    e->surf = eglCreateWindowSurface(e->dpy, e->cfg, (EGLNativeWindowType)g->surface, NULL);
    if (e->surf == EGL_NO_SURFACE) die("eglCreateWindowSurface");
    if (!eglMakeCurrent(e->dpy, e->surf, e->surf, e->ctx)) die("eglMakeCurrent");
    eglSwapInterval(e->dpy, 1);
}

static uint32_t drm_fb_for_bo(int drm_fd, struct gbm_bo *bo) {
    uint32_t fb_id;
    uint32_t width = gbm_bo_get_width(bo);
    uint32_t height = gbm_bo_get_height(bo);
    uint32_t stride = gbm_bo_get_stride(bo);
    uint32_t handle = gbm_bo_get_handle(bo).u32;
    int ret = drmModeAddFB(drm_fd, width, height, 24, 32, stride, handle, &fb_id);
    if (ret) die("drmModeAddFB");
    return fb_id;
}

static void drm_set_mode(drm_ctx *d, gbm_ctx *g) {
    g->bo = gbm_surface_lock_front_buffer(g->surface);
    if (!g->bo) die("gbm_surface_lock_front_buffer");
    g->fb_id = drm_fb_for_bo(d->fd, g->bo);
    int ret = drmModeSetCrtc(d->fd, d->crtc_id, g->fb_id, 0, 0, &d->conn_id, 1, &d->mode);
    if (ret) die("drmModeSetCrtc");
}

static void page_flip(drm_ctx *d, gbm_ctx *g) {
    g->next_bo = gbm_surface_lock_front_buffer(g->surface);
    uint32_t fb = drm_fb_for_bo(d->fd, g->next_bo);
    // Legacy blocking flip via SetCrtc for simplicity
    drmModeSetCrtc(d->fd, d->crtc_id, fb, 0, 0, &d->conn_id, 1, &d->mode);
    if (g->bo) {
        uint32_t old_fb = g->fb_id;
        gbm_surface_release_buffer(g->surface, g->bo);
        drmModeRmFB(d->fd, old_fb);
    }
    g->bo = g->next_bo;
    g->fb_id = fb;
}

// mpv wakeup via pipe, so we can poll
static void mpv_wakeup(void *ctx) {
    mpv_ctx *m = (mpv_ctx *)ctx;
    uint64_t one = 1;
    (void)write(m->wakeup_fd[1], &one, sizeof(one));
}

static void *get_proc_address(void *ctx, const char *name) {
    (void)ctx;
    return (void *)eglGetProcAddress(name);
}

static void gl_clear_color(float r, float g, float b, float a) {
    glClearColor(r, g, b, a);
    glClear(GL_COLOR_BUFFER_BIT);
}

// Offscreen render target for logical orientation
static GLuint rt_fbo=0, rt_tex=0; static int rt_w=0, rt_h=0;
static GLuint blit_prog=0, blit_vbo=0; static GLint blit_u_tex=-1;
static GLuint vid_fbo=0, vid_tex=0; static int vid_w=0, vid_h=0;

static GLuint compile_shader(GLenum type, const char *src) {
    GLuint s = glCreateShader(type); glShaderSource(s,1,&src,NULL); glCompileShader(s); GLint ok; glGetShaderiv(s,GL_COMPILE_STATUS,&ok); if(!ok){char log[512]; glGetShaderInfoLog(s,512,NULL,log); fprintf(stderr,"shader compile: %s\n",log); exit(1);} return s;
}

static void ensure_blit_prog(void){ if(blit_prog) return; const char* vs="attribute vec2 a_pos; attribute vec2 a_uv; varying vec2 v_uv; void main(){v_uv=a_uv; gl_Position=vec4(a_pos,0.0,1.0);}"; const char* fs="precision mediump float; varying vec2 v_uv; uniform sampler2D u_tex; void main(){ gl_FragColor = texture2D(u_tex, v_uv);}"; GLuint v=compile_shader(GL_VERTEX_SHADER,vs), f=compile_shader(GL_FRAGMENT_SHADER,fs); blit_prog=glCreateProgram(); glAttachShader(blit_prog,v); glAttachShader(blit_prog,f); glBindAttribLocation(blit_prog,0,"a_pos"); glBindAttribLocation(blit_prog,1,"a_uv"); glLinkProgram(blit_prog); GLint ok; glGetProgramiv(blit_prog,GL_LINK_STATUS,&ok); if(!ok){fprintf(stderr,"link fail\n"); exit(1);} blit_u_tex=glGetUniformLocation(blit_prog,"u_tex"); glGenBuffers(1,&blit_vbo);} 

static void ensure_rt(int w, int h){ if(rt_tex && (rt_w==w && rt_h==h)) return; if(rt_tex){ glDeleteTextures(1,&rt_tex); glDeleteFramebuffers(1,&rt_fbo);} rt_w=w; rt_h=h; glGenTextures(1,&rt_tex); glBindTexture(GL_TEXTURE_2D, rt_tex); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR); glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA, w,h,0,GL_RGBA,GL_UNSIGNED_BYTE,NULL); glGenFramebuffers(1,&rt_fbo); glBindFramebuffer(GL_FRAMEBUFFER, rt_fbo); glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, rt_tex, 0); GLenum stat=glCheckFramebufferStatus(GL_FRAMEBUFFER); if(stat!=GL_FRAMEBUFFER_COMPLETE){ fprintf(stderr,"FBO incomplete\n"); exit(1);} glBindFramebuffer(GL_FRAMEBUFFER, 0);} 

static void ensure_video_rt(int w, int h){ if(vid_tex && (vid_w==w && vid_h==h)) return; if(vid_tex){ glDeleteTextures(1,&vid_tex); glDeleteFramebuffers(1,&vid_fbo);} vid_w=w; vid_h=h; glGenTextures(1,&vid_tex); glBindTexture(GL_TEXTURE_2D, vid_tex); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR); glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA, w,h,0,GL_RGBA,GL_UNSIGNED_BYTE,NULL); glGenFramebuffers(1,&vid_fbo); glBindFramebuffer(GL_FRAMEBUFFER, vid_fbo); glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, vid_tex, 0); GLenum stat=glCheckFramebufferStatus(GL_FRAMEBUFFER); if(stat!=GL_FRAMEBUFFER_COMPLETE){ fprintf(stderr,"Video FBO incomplete\n"); exit(1);} glBindFramebuffer(GL_FRAMEBUFFER, 0);} 

static void blit_rt_to_screen(rotation_t rot, int fb_w, int fb_h){ ensure_blit_prog(); glUseProgram(blit_prog); glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D, rt_tex); glUniform1i(blit_u_tex,0); float L=-1.f,R=1.f,B=-1.f,T=1.f; float verts[24]; // 6 verts * (pos2+uv2)
    // Set UVs based on rotation
    float u0=0.f,v0=0.f,u1=1.f,v1=1.f;
    float quad[] = { L,B, u0,v1,  R,B, u1,v1,  R,T, u1,v0,  L,B, u0,v1,  R,T, u1,v0,  L,T, u0,v0 };
    float quad90[] = { L,B, u1,v1, R,B, u1,v0, R,T, u0,v0, L,B, u1,v1, R,T, u0,v0, L,T, u0,v1 };
    float quad180[] = { L,B, u1,v0, R,B, u0,v0, R,T, u0,v1, L,B, u1,v0, R,T, u0,v1, L,T, u1,v1 };
    float quad270[] = { L,B, u0,v0, R,B, u0,v1, R,T, u1,v1, L,B, u0,v0, R,T, u1,v1, L,T, u1,v0 };
    const float *src = quad; if(rot==ROT_90) src=quad90; else if(rot==ROT_180) src=quad180; else if(rot==ROT_270) src=quad270;
    memcpy(verts, src, sizeof(verts));
    glBindBuffer(GL_ARRAY_BUFFER, blit_vbo); glBufferData(GL_ARRAY_BUFFER, sizeof(verts), verts, GL_STREAM_DRAW);
    glEnableVertexAttribArray(0); glVertexAttribPointer(0,2,GL_FLOAT,GL_FALSE,4*sizeof(float),(void*)0);
    glEnableVertexAttribArray(1); glVertexAttribPointer(1,2,GL_FLOAT,GL_FALSE,4*sizeof(float),(void*)(2*sizeof(float)));
    glDrawArrays(GL_TRIANGLES, 0, 6);
}

static void draw_tex_to_rt(GLuint tex, int x, int y, int w, int h, int rt_w_, int rt_h_){ ensure_blit_prog(); glUseProgram(blit_prog); glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D, tex); glUniform1i(blit_u_tex, 0); float L = (2.0f * x / rt_w_) - 1.0f; float R = (2.0f * (x + w) / rt_w_) - 1.0f; float T = 1.0f - (2.0f * y / rt_h_); float B = 1.0f - (2.0f * (y + h) / rt_h_); float verts[] = { L,B, 0,0,  R,B, 1,0,  R,T, 1,1,  L,B, 0,0,  R,T, 1,1,  L,T, 0,1 }; glBindBuffer(GL_ARRAY_BUFFER, blit_vbo); glBufferData(GL_ARRAY_BUFFER, sizeof(verts), verts, GL_STREAM_DRAW); glEnableVertexAttribArray(0); glVertexAttribPointer(0,2,GL_FLOAT,GL_FALSE,4*sizeof(float),(void*)0); glEnableVertexAttribArray(1); glVertexAttribPointer(1,2,GL_FLOAT,GL_FALSE,4*sizeof(float),(void*)(2*sizeof(float))); glDrawArrays(GL_TRIANGLES,0,6);} 

static void parse_mode(const char *s, int *w, int *h, int *hz){ if(!s){*w=*h=*hz=0; return;} int tw=0, th=0, thz=0; if (sscanf(s, "%dx%d@%d", &tw,&th,&thz)>=2){ *w=tw; *h=th; *hz=thz; } }

static rotation_t parse_rot(const char *s){ if(!s) return ROT_0; int v=atoi(s); switch(v){case 0:return ROT_0; case 90:return ROT_90; case 180:return ROT_180; case 270:return ROT_270; default:return ROT_0;} }

static void push_video(options_t *opt, const char *path){
    if (opt->video_count == opt->video_cap) {
        int ncap = opt->video_cap ? opt->video_cap*2 : 8;
        opt->videos = realloc(opt->videos, (size_t)ncap * sizeof(video_item));
        memset(opt->videos + opt->video_cap, 0, (size_t)(ncap - opt->video_cap) * sizeof(video_item));
        opt->video_cap = ncap;
    }
    video_item *vi = &opt->videos[opt->video_count++];
    memset(vi, 0, sizeof *vi);
    vi->path = path;
    opt->video_path = path;
}

static void push_video_opt(video_item *vi, const char *kv){
    if (!vi) return;
    if (vi->nopts == vi->cap) {
        int ncap = vi->cap ? vi->cap*2 : 8;
        vi->opts = realloc(vi->opts, (size_t)ncap * sizeof(char*));
        vi->cap = ncap;
    }
    vi->opts[vi->nopts++] = kv;
}

static const char* trim(char *s){ if(!s) return s; while(isspace((unsigned char)*s)) s++; char *e=s+strlen(s); while(e>s && isspace((unsigned char)e[-1])) *--e='\0'; return s; }

static void parse_playlist_ext(options_t *opt, const char *file){
    FILE *f = fopen(file, "r"); if (!f) { perror("playlist-ext open"); return; }
    char *line=NULL; size_t cap=0; ssize_t n;
    while ((n=getline(&line,&cap,f))!=-1){
        if (n>0 && (line[n-1]=='\n' || line[n-1]=='\r')) line[n-1]='\0';
        char *p = (char*)trim(line); if (*p=='#' || *p=='\0') continue;
        char *bar = strchr(p, '|');
        char *path = p; char *optstr = NULL;
        if (bar){ *bar='\0'; optstr = bar+1; }
        push_video(opt, strdup(trim(path)));
        if (optstr){
            video_item *vi = &opt->videos[opt->video_count-1];
            char *opts_dup = strdup(optstr);
            char *tok = strtok(opts_dup, ",");
            while (tok){ push_video_opt(vi, strdup(trim(tok))); tok = strtok(NULL, ","); }
            free(opts_dup);
        }
    }
    free(line); fclose(f);
}

// Tokenize a config file with simple quoting and comments (#)
static char** tokenize_file(const char *path, int *argc_out){
    FILE *f = fopen(path, "r"); if(!f) return NULL; char **args=NULL; int n=0, cap=0; int c; int state=0; // 0=ws,1=token,2=single,3=double
    char buf[4096]; int bi=0;
    while ((c=fgetc(f))!=EOF){
        if (state==0){ if (c=='#'){ while(c!=EOF && c!='\n') c=fgetc(f); if(c==EOF) break; else continue; } if (c=='\'' ){ state=2; }
            else if (c=='"'){ state=3; } else if (c=='\n' || c==' ' || c=='\t' || c=='\r'){ continue; } else { if (bi<(int)sizeof(buf)-1) buf[bi++]=c; state=1; } }
        else if (state==1){ if (c=='\n' || c==' ' || c=='\t' || c=='\r'){ buf[bi]='\0'; if(n==cap){cap=cap?cap*2:16; args=realloc(args,(size_t)cap*sizeof(char*));} args[n++]=strdup(buf); bi=0; state=0; }
            else if (c=='\'' ){ state=2; } else if (c=='"'){ state=3; } else { if (bi<(int)sizeof(buf)-1) buf[bi++]=c; } }
        else if (state==2){ if (c=='\''){ state=1; } else { if (bi<(int)sizeof(buf)-1) buf[bi++]=c; } }
        else if (state==3){ if (c=='"'){ state=1; } else { if (bi<(int)sizeof(buf)-1) buf[bi++]=c; } }
    }
    if (bi>0){ buf[bi]='\0'; if(n==cap){cap=cap?cap*2:16; args=realloc(args,(size_t)cap*sizeof(char*));} args[n++]=strdup(buf); }
    fclose(f); *argc_out=n; return args;
}

static const char* default_config_path(void){
    static char buf[512];
    // Prefer Unraid boot config if available
    if (access("/boot/config", F_OK) == 0) {
        snprintf(buf, sizeof buf, "/boot/config/kms_mpv_compositor.conf");
        return buf;
    }
    const char *xdg = getenv("XDG_CONFIG_HOME");
    const char *home = getenv("HOME");
    if (xdg && *xdg) { snprintf(buf, sizeof buf, "%s/kms_mpv_compositor.conf", xdg); return buf; }
    if (home && *home) { snprintf(buf, sizeof buf, "%s/.config/kms_mpv_compositor.conf", home); return buf; }
    snprintf(buf, sizeof buf, ".kms_mpv_compositor.conf");
    return buf;
}

static void save_config(const options_t *opt, const char *path){ FILE *f=fopen(path,"w"); if(!f){perror("save-config"); return;} 
    if (opt->connector_opt) fprintf(f, "--connector '%s'\n", opt->connector_opt);
    if (opt->mode_w||opt->mode_h) fprintf(f, "--mode %dx%d@%d\n", opt->mode_w,opt->mode_h,opt->mode_hz);
    if (opt->rotation) fprintf(f, "--rotate %d\n", (int)opt->rotation);
    if (opt->font_px) fprintf(f, "--font-size %d\n", opt->font_px);
    if (opt->video_frac_pct) fprintf(f, "--video-frac %d\n", opt->video_frac_pct);
    else if (opt->right_frac_pct) fprintf(f, "--right-frac %d\n", opt->right_frac_pct);
    if (opt->pane_split_pct) fprintf(f, "--pane-split %d\n", opt->pane_split_pct);
    if (opt->pane_a_cmd) fprintf(f, "--pane-a '%s'\n", opt->pane_a_cmd);
    if (opt->pane_b_cmd) fprintf(f, "--pane-b '%s'\n", opt->pane_b_cmd);
    if (opt->no_video) fprintf(f, "--no-video\n");
    if (opt->loop_file) fprintf(f, "--loop-file\n");
    if (opt->loop_playlist) fprintf(f, "--loop-playlist\n");
    if (opt->shuffle) fprintf(f, "--shuffle\n");
    for (int i=0;i<opt->n_mpv_opts;i++) fprintf(f, "--mpv-opt '%s'\n", opt->mpv_opts[i]);
    if (opt->playlist_path) fprintf(f, "--playlist '%s'\n", opt->playlist_path);
    if (opt->playlist_ext) fprintf(f, "--playlist-extended '%s'\n", opt->playlist_ext);
    for (int i=0;i<opt->video_count;i++){ const video_item *vi=&opt->videos[i]; fprintf(f, "--video '%s'\n", vi->path); for (int k=0;k<vi->nopts;k++) fprintf(f, "--video-opt '%s'\n", vi->opts[k]); }
    fclose(f);
}

int main(int argc, char **argv) {
    options_t opt = (options_t){0};
    // Preload config file if specified on CLI, else use default if present
    const char *cfg = NULL; for (int i=1;i<argc;i++){ if (!strcmp(argv[i],"--config") && i+1<argc){ cfg = argv[i+1]; break; } }
    if (!cfg) { const char *def = default_config_path(); if (access(def, R_OK)==0) cfg = def; }
    char **merged = NULL; int margc = 0;
    if (cfg) {
        int cargc=0; char **cargv = tokenize_file(cfg, &cargc);
        // Build merged argv: argv[0], config tokens, then original args excluding --config and its value
        merged = malloc(sizeof(char*) * (size_t)(1 + cargc + argc));
        merged[margc++] = argv[0];
        for (int i=0;i<cargc;i++) merged[margc++] = cargv[i];
        for (int i=1;i<argc;i++) { if (!strcmp(argv[i],"--config") && i+1<argc){ i++; continue; } merged[margc++] = argv[i]; }
        argv = merged; argc = margc;
    }
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--video") && i + 1 < argc) { push_video(&opt, argv[++i]); }
        else if (!strcmp(argv[i], "--video-opt") && i + 1 < argc) {
            if (opt.video_count>0) push_video_opt(&opt.videos[opt.video_count-1], argv[++i]);
            else { // treat as global if no videos yet
                if (opt.n_mpv_opts == opt.cap_mpv_opts){ int nc=opt.cap_mpv_opts?opt.cap_mpv_opts*2:8; opt.mpv_opts=realloc(opt.mpv_opts,(size_t)nc*sizeof(char*)); opt.cap_mpv_opts=nc; }
                opt.mpv_opts[opt.n_mpv_opts++] = argv[++i];
            }
        }
        else if (!strcmp(argv[i], "--playlist") && i + 1 < argc) opt.playlist_path = argv[++i];
        else if (!strcmp(argv[i], "--config") && i + 1 < argc) opt.config_file = argv[++i];
        else if (!strcmp(argv[i], "--save-config") && i + 1 < argc) opt.save_config_file = argv[++i];
        else if (!strcmp(argv[i], "--playlist-extended") && i + 1 < argc) opt.playlist_ext = argv[++i];
        else if (!strcmp(argv[i], "--connector") && i + 1 < argc) opt.connector_opt = argv[++i];
        else if (!strcmp(argv[i], "--mode") && i + 1 < argc) parse_mode(argv[++i], &opt.mode_w, &opt.mode_h, &opt.mode_hz);
        else if (!strcmp(argv[i], "--rotate") && i + 1 < argc) opt.rotation = parse_rot(argv[++i]);
        else if (!strcmp(argv[i], "--font-size") && i + 1 < argc) opt.font_px = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--right-frac") && i + 1 < argc) opt.right_frac_pct = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--video-frac") && i + 1 < argc) opt.video_frac_pct = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--pane-split") && i + 1 < argc) opt.pane_split_pct = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--pane-a") && i + 1 < argc) opt.pane_a_cmd = argv[++i];
        else if (!strcmp(argv[i], "--pane-b") && i + 1 < argc) opt.pane_b_cmd = argv[++i];
        else if (!strcmp(argv[i], "--list-connectors")) opt.list_connectors = true;
        else if (!strcmp(argv[i], "--no-video")) opt.no_video = true;
        else if (!strcmp(argv[i], "--loop-file")) opt.loop_file = true;
        else if (!strcmp(argv[i], "--loop-playlist")) opt.loop_playlist = true;
        else if (!strcmp(argv[i], "--shuffle") || !strcmp(argv[i], "--randomize")) opt.shuffle = true;
        else if (!strcmp(argv[i], "--mpv-opt") && i + 1 < argc) {
            if (opt.n_mpv_opts == opt.cap_mpv_opts){ int nc=opt.cap_mpv_opts?opt.cap_mpv_opts*2:8; opt.mpv_opts=realloc(opt.mpv_opts,(size_t)nc*sizeof(char*)); opt.cap_mpv_opts=nc; }
            opt.mpv_opts[opt.n_mpv_opts++] = argv[++i];
        }
        else if (!strcmp(argv[i], "--save-config-default")) opt.save_config_default = true;
        else if (!strcmp(argv[i], "-h") || !strcmp(argv[i], "--help")) {
            fprintf(stderr, "Usage: %s [--video PATH] [--connector ID|NAME] [--mode WxH[@Hz]] [--rotate 0|90|180|270]\n"
                            "            [--font-size PX] [--right-frac PCT] [--pane-split PCT]\n"
                            "            [--pane-a \"CMD\"] [--pane-b \"CMD\"] [--list-connectors]\n"
                            "            [--no-video] [--playlist FILE] [--playlist-extended FILE]\n"
                            "            [--loop-file] [--loop-playlist] [--shuffle] [--mpv-opt K=V]\n"
                            "            [--video-opt K=V] [--video-frac PCT] [--config FILE] [--save-config FILE] [--save-config-default]\n",
                    argv[0]);
            return 0;
        }
    }

    if (opt.playlist_ext) parse_playlist_ext(&opt, opt.playlist_ext);

    drm_ctx d = {0};
    gbm_ctx g = {0};
    egl_ctx e = {0};
    d.fd = open_drm_card();
    pick_connector_mode(&d, &opt);
    if (opt.list_connectors) {
        fprintf(stderr, "Connectors:\n");
        for (int i = 0; i < d.res->count_connectors; i++) {
            drmModeConnector *c = drmModeGetConnector(d.fd, d.res->connectors[i]);
            if (!c) continue;
            fprintf(stderr, "  %u: %s-%u (%s) modes:%d %s\n", c->connector_id, conn_type_str(c->connector_type), c->connector_type_id,
                    c->connection==DRM_MODE_CONNECTED?"connected":"disconnected", c->count_modes,
                    (c->count_modes>0?"[use --mode WxH@Hz]":""));
            for (int mi=0; mi<c->count_modes && mi<8; mi++) {
                drmModeModeInfo *m = &c->modes[mi];
                int hz = (int)((m->clock * 1000LL) / (m->htotal * m->vtotal));
                fprintf(stderr, "      %dx%d@%d %s\n", m->hdisplay, m->vdisplay, hz, (m->type & DRM_MODE_TYPE_PREFERRED)?"(preferred)":"");
            }
            drmModeFreeConnector(c);
        }
        return 0;
    }
    gbm_init(&g, d.fd, d.mode.hdisplay, d.mode.vdisplay);
    egl_init(&e, &g);

    // mpv setup (skip if --no-video and no videos provided)
    mpv_ctx m = {0};
    bool use_mpv = !opt.no_video && (opt.video_count > 0 || opt.playlist_path || opt.playlist_ext);
    if (use_mpv) {
        if (pipe2(m.wakeup_fd, O_NONBLOCK | O_CLOEXEC) < 0) die("pipe2");
        m.mpv = mpv_create();
        if (!m.mpv) die("mpv_create");
        mpv_set_option_string(m.mpv, "vo", "gpu");
        mpv_set_option_string(m.mpv, "gpu-api", "opengl");
        mpv_set_option_string(m.mpv, "keep-open", "yes");
        // Global mpv opts
        for (int i=0;i<opt.n_mpv_opts;i++) {
            const char *kv = opt.mpv_opts[i];
            const char *eq = strchr(kv, '=');
            if (eq) {
                char key[128]; size_t kl = (size_t)(eq-kv); if (kl>=sizeof key) kl=sizeof key-1; memcpy(key, kv, kl); key[kl]='\0';
                const char *val = eq+1;
                mpv_set_option_string(m.mpv, key, val);
            }
        }
        if (opt.loop_file) mpv_set_option_string(m.mpv, "loop-file", "inf");
        if (opt.loop_playlist) mpv_set_option_string(m.mpv, "loop-playlist", "yes");
        if (opt.shuffle) mpv_set_option_string(m.mpv, "shuffle", "yes");
        if (mpv_initialize(m.mpv) < 0) die("mpv_initialize");

        mpv_render_param params[] = {
            {MPV_RENDER_PARAM_API_TYPE, (void *)MPV_RENDER_API_TYPE_OPENGL},
            {MPV_RENDER_PARAM_OPENGL_INIT_PARAMS,
             &(mpv_opengl_init_params){.get_proc_address = get_proc_address, .get_proc_address_ctx = NULL}},
            {0}
        };
        if (mpv_render_context_create(&m.mpv_gl, m.mpv, params) < 0)
            die("mpv_render_context_create");
        mpv_render_context_set_update_callback(m.mpv_gl, mpv_wakeup, &m);

        // Load playlist or multiple videos
        if (opt.playlist_path) {
            const char *cmd[] = {"loadlist", opt.playlist_path, "replace", NULL};
            mpv_command_async(m.mpv, 0, cmd);
        } else if (opt.video_count > 0) {
            for (int vi = 0; vi < opt.video_count; ++vi) {
                const video_item *item = &opt.videos[vi];
                if (item->nopts == 0) {
                    const char *mode = (vi==0?"replace":"append");
                    const char *cmd[] = {"loadfile", item->path, mode, NULL};
                    mpv_command_async(m.mpv, 0, cmd);
                } else {
                    // Use node API to pass options per-file
                    mpv_node args[4]; memset(args,0,sizeof args);
                    mpv_node root; root.format = MPV_FORMAT_NODE_ARRAY; root.u.list = malloc(sizeof(*root.u.list));
                    root.u.list->num = 0; root.u.list->values = NULL; root.u.list->keys = NULL;
                    // Helper to push a string node
                    #define PUSH_STR(str) do{ root.u.list->values = realloc(root.u.list->values, sizeof(mpv_node)*(root.u.list->num+1)); root.u.list->values[root.u.list->num].format = MPV_FORMAT_STRING; root.u.list->values[root.u.list->num].u.string = (char*)(str); root.u.list->num++; }while(0)
                    PUSH_STR("loadfile"); PUSH_STR(item->path); PUSH_STR(vi==0?"replace":"append");
                    // options map
                    mpv_node map; map.format = MPV_FORMAT_NODE_MAP; map.u.list = malloc(sizeof(*map.u.list)); map.u.list->num=0; map.u.list->values=NULL; map.u.list->keys=NULL;
                    for (int oi=0; oi<item->nopts; ++oi){
                        const char *kv = item->opts[oi]; const char *eq = strchr(kv,'='); if(!eq) continue;
                        size_t kl = (size_t)(eq-kv); char *key = strndup(kv, kl); const char *val = eq+1;
                        map.u.list->values = realloc(map.u.list->values, sizeof(mpv_node)*(map.u.list->num+1));
                        map.u.list->keys = realloc(map.u.list->keys, sizeof(char*)*(map.u.list->num+1));
                        map.u.list->keys[map.u.list->num] = key;
                        map.u.list->values[map.u.list->num].format = MPV_FORMAT_STRING;
                        map.u.list->values[map.u.list->num].u.string = (char*)val;
                        map.u.list->num++;
                    }
                    root.u.list->values = realloc(root.u.list->values, sizeof(mpv_node)*(root.u.list->num+1));
                    root.u.list->values[root.u.list->num] = map; root.u.list->num++;
                    mpv_command_node_async(m.mpv, 0, &root);
                    // We intentionally leak small allocations here for simplicity; process lifetime is fine.
                }
            }
        }
        if (opt.shuffle) {
            const char *cmd2[] = {"playlist-shuffle", NULL};
            mpv_command_async(m.mpv, 0, cmd2);
        }
    }

    // First frame to program CRTC
    glViewport(0, 0, d.mode.hdisplay, d.mode.vdisplay);
    gl_clear_color(0.f, 0.f, 0.f, 1.f);
    eglSwapBuffers(e.dpy, e.surf);
    drm_set_mode(&d, &g);

    // Logical render target (rotated presentation later)
    int fb_w = d.mode.hdisplay, fb_h = d.mode.vdisplay;
    int logical_w = (opt.rotation==ROT_90 || opt.rotation==ROT_270) ? fb_h : fb_w;
    int logical_h = (opt.rotation==ROT_90 || opt.rotation==ROT_270) ? fb_w : fb_h;
    ensure_rt(logical_w, logical_h);

    // Create terminal panes in logical space
    int screen_w = logical_w, screen_h = logical_h;
    int right_pct = opt.right_frac_pct ? opt.right_frac_pct : 33; if (right_pct<10) right_pct=10; if (right_pct>80) right_pct=80;
    if (opt.video_frac_pct>0 && opt.video_frac_pct<100) right_pct = 100 - opt.video_frac_pct;
    int right_w = opt.no_video ? screen_w : (screen_w * right_pct / 100);
    int right_x = screen_w - right_w;
    int split_pct = opt.pane_split_pct ? opt.pane_split_pct : 50; if (split_pct<10) split_pct=10; if (split_pct>90) split_pct=90;
    int top_h = screen_h * split_pct / 100;
    // Top-right pane cmd: btop
    pane_layout lay_a = { .x = right_x, .y = screen_h - top_h, .w = right_w, .h = top_h };
    term_pane *tp_a = NULL;
    if (opt.pane_a_cmd) tp_a = term_pane_create_cmd(&lay_a, opt.font_px, opt.pane_a_cmd);
    else {
        char *argv_a[] = { "btop", NULL };
        tp_a = term_pane_create(&lay_a, opt.font_px, "btop", argv_a);
    }
    // Bottom-right pane cmd: tail -f
    pane_layout lay_b = { .x = right_x, .y = 0, .w = right_w, .h = screen_h - top_h };
    term_pane *tp_b = NULL;
    if (opt.pane_b_cmd) tp_b = term_pane_create_cmd(&lay_b, opt.font_px, opt.pane_b_cmd);
    else {
        char *argv_b[] = { "tail", "-f", "/var/log/syslog", NULL };
        tp_b = term_pane_create(&lay_b, opt.font_px, "tail", argv_b);
    }

    // Set TTY to raw mode for key forwarding
    struct termios rawt; if (tcgetattr(0, &g_oldt)==0) { g_have_oldt = 1; rawt = g_oldt; cfmakeraw(&rawt); tcsetattr(0, TCSANOW, &rawt); atexit(restore_tty); }
    fprintf(stderr, "Controls: Tab focus A/B, Ctrl+Q quit, n/p next/prev, space pause, o OSD toggle.\n");
    int focus = 1; // 1=top pane, 2=bottom pane
    bool show_osd = true;

    bool running = true;
    struct pollfd pfds[2];
    pfds[0].fd = 0; // stdin for keys
    pfds[0].events = POLLIN;
    pfds[1].fd = use_mpv ? m.wakeup_fd[0] : -1;
    pfds[1].events = POLLIN;
    int nfds = use_mpv ? 2 : 1;
    fcntl(0, F_SETFL, O_NONBLOCK);

    while (running) {
        int ret = poll(pfds, nfds, 16); // ~60fps budget, render if needed
        if (ret < 0 && errno != EINTR) die("poll");

        if (pfds[0].revents & POLLIN) {
            char buf[64]; ssize_t n = read(0, buf, sizeof buf);
            if (n > 0) {
                // Ctrl+Q to quit
                for (ssize_t i=0;i<n;i++) if ((unsigned char)buf[i] == 0x11) { running=false; break; }
                if (!running) break;
                // Tab switches focus
                bool consumed=false;
                for (ssize_t i=0;i<n;i++) if (buf[i]=='\t') { focus = (focus==1?2:1); consumed=true; }
                // Playback keys sent to mpv if present
                if (use_mpv) {
                    for (ssize_t i=0;i<n;i++) {
                        if (buf[i]=='n') { const char *c[]={"playlist-next",NULL}; mpv_command_async(m.mpv,0,c); consumed=true; }
                        else if (buf[i]=='p') { const char *c[]={"playlist-prev",NULL}; mpv_command_async(m.mpv,0,c); consumed=true; }
                        else if (buf[i]==' ') { const char *c[]={"cycle","pause",NULL}; mpv_command_async(m.mpv,0,c); consumed=true; }
                    }
                }
                for (ssize_t i=0;i<n;i++) if (buf[i]=='o') { show_osd = !show_osd; consumed=true; }
                if (!consumed) {
                    if (focus==1) term_pane_send_input(tp_a, buf, (size_t)n);
                    else if (focus==2) term_pane_send_input(tp_b, buf, (size_t)n);
                }
            }
        }
        if (use_mpv && (pfds[1].revents & POLLIN)) {
            uint64_t tmp;
            while (read(m.wakeup_fd[0], &tmp, sizeof(tmp)) > 0) {}
        }

        // Render frame into offscreen logical FBO
        if (!eglMakeCurrent(e.dpy, e.surf, e.surf, e.ctx)) die("eglMakeCurrent loop");

        glBindFramebuffer(GL_FRAMEBUFFER, rt_fbo);
        glViewport(0, 0, logical_w, logical_h);
        gl_clear_color(0.0f, 0.0f, 0.0f, 1.0f);

        // Draw mpv if enabled into its own FBO sized to the video region (left area)
        if (use_mpv) {
            int vw = logical_w - right_w;
            int vh = logical_h;
            if (vw < 0) vw = 0;
            ensure_video_rt(vw, vh);
            glBindFramebuffer(GL_FRAMEBUFFER, vid_fbo);
            glViewport(0, 0, vw, vh);
            gl_clear_color(0.0f, 0.0f, 0.0f, 1.0f);
            int flip_y = 1;
            mpv_opengl_fbo fbo = {.fbo = (int)vid_fbo, .w = vw, .h = vh, .internal_format = 0};
            mpv_render_param r_params[] = {
                {MPV_RENDER_PARAM_OPENGL_FBO, &fbo},
                {MPV_RENDER_PARAM_FLIP_Y, &flip_y},
                {0}
            };
            mpv_render_context_render(m.mpv_gl, r_params);

            // Composite into logical RT: first the video on the left region
            glBindFramebuffer(GL_FRAMEBUFFER, rt_fbo);
            glViewport(0, 0, logical_w, logical_h);
            draw_tex_to_rt(vid_tex, 0, 0, vw, vh, logical_w, logical_h);
        } else {
            glBindFramebuffer(GL_FRAMEBUFFER, rt_fbo);
            glViewport(0, 0, logical_w, logical_h);
        }
        
        // Draw terminal panes into rt
        // Poll PTYs and update textures only if changed
        (void)term_pane_poll(tp_a);
        (void)term_pane_poll(tp_b);
        term_pane_render(tp_a, screen_w, screen_h);
        term_pane_render(tp_b, screen_w, screen_h);

        // OSD overlay (title, index/total, paused)
        if (use_mpv && show_osd) {
            static osd_ctx *osd = NULL; if (!osd) osd = osd_create(opt.font_px?opt.font_px:20);
            int64_t pos=0,count=0; int paused_flag=0; char *title=NULL;
            mpv_get_property(m.mpv, "playlist-pos", MPV_FORMAT_INT64, &pos);
            mpv_get_property(m.mpv, "playlist-count", MPV_FORMAT_INT64, &count);
            mpv_get_property(m.mpv, "pause", MPV_FORMAT_FLAG, &paused_flag);
            title = mpv_get_property_string(m.mpv, "media-title");
            char line[512]; snprintf(line,sizeof line, "%s %lld/%lld - %s", paused_flag?"Paused":"Playing", (long long)(pos+1), (long long)count, title?title:"(no title)");
            if (title) mpv_free(title);
            osd_set_text(osd, line);
            // Draw into logical RT at 16px margin
            glBindFramebuffer(GL_FRAMEBUFFER, rt_fbo);
            glViewport(0,0, logical_w, logical_h);
            osd_draw(osd, 16, 16, logical_w, logical_h);
        }

        // Present: bind default framebuffer and blit rotated
        glBindFramebuffer(GL_FRAMEBUFFER, 0);
        glViewport(0, 0, fb_w, fb_h);
        gl_clear_color(0.f,0.f,0.f,1.f);
        blit_rt_to_screen(opt.rotation, fb_w, fb_h);

        eglSwapBuffers(e.dpy, e.surf);
        page_flip(&d, &g);
    }

    // Cleanup
    if (m.mpv_gl) mpv_render_context_free(m.mpv_gl);
    if (m.mpv) mpv_terminate_destroy(m.mpv);
    if (d.orig_crtc) {
        drmModeSetCrtc(d.fd, d.orig_crtc->crtc_id, d.orig_crtc->buffer_id,
                       d.orig_crtc->x, d.orig_crtc->y, &d.conn_id, 1, &d.orig_crtc->mode);
        drmModeFreeCrtc(d.orig_crtc);
    }
    if (g.bo) {
        gbm_surface_release_buffer(g.surface, g.bo);
        drmModeRmFB(d.fd, g.fb_id);
    }
    if (e.dpy != EGL_NO_DISPLAY) {
        eglMakeCurrent(e.dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
        if (e.ctx) eglDestroyContext(e.dpy, e.ctx);
        if (e.surf) eglDestroySurface(e.dpy, e.surf);
        eglTerminate(e.dpy);
    }
    if (g.surface) gbm_surface_destroy(g.surface);
    if (g.dev) gbm_device_destroy(g.dev);
    term_pane_destroy(tp_a);
    term_pane_destroy(tp_b);
    if (opt.save_config_file) save_config(&opt, opt.save_config_file);
    else if (opt.save_config_default) save_config(&opt, default_config_path());
    if (d.conn) drmModeFreeConnector(d.conn);
    if (d.res) drmModeFreeResources(d.res);
    if (d.fd >= 0) close(d.fd);
    return 0;
}
