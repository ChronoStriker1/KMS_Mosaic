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
#include <sys/stat.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <unistd.h>
#include <termios.h>
#include <ctype.h>
#include <signal.h>
#include <time.h>
#ifdef __linux__
#include <execinfo.h>
#endif
#include <dirent.h>

#include <drm.h>
#include <xf86drm.h>
#include <xf86drmMode.h>
#include <drm_fourcc.h>
#include <gbm.h>

#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>

#include <mpv/client.h>
#include <mpv/render_gl.h>

#include "term_pane.h"
#include "osd.h"

// Forward declaration for GL error checker used before its definition
static void gl_check(const char *stage);

typedef struct {
    int fd;
    drmModeRes *res;
    drmModeConnector *conn;
    drmModeCrtc *orig_crtc;
    drmModeModeInfo mode;
    uint32_t crtc_id;
    uint32_t conn_id;
    // Atomic modesetting context (optional)
    struct {
        int enabled; // 1 if client caps set and primary plane found
        uint32_t plane_id; // primary plane for selected CRTC
        int nonblock; // perform nonblocking atomic flips
        // Property IDs
        struct { uint32_t mode_id, active, out_fence_ptr; } crtc_props;
        struct { uint32_t crtc_id; } conn_props;
        struct { uint32_t fb_id, crtc_id, src_x, src_y, src_w, src_h, crtc_x, crtc_y, crtc_w, crtc_h, in_fence_fd; } plane_props;
    } atomic;
} drm_ctx;

typedef struct {
    struct gbm_device *dev;
    struct gbm_surface *surface;
    struct gbm_bo *bo, *next_bo;
    uint32_t fb_id;
    // Atomic async flip tracking
    struct gbm_bo *pending_bo;
    uint32_t pending_fb;
    int in_flight;
    int w, h; // cached dimensions
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
    bool no_panes;
    bool gl_test;
    bool diag;
    bool loop_file;
    bool loop_playlist;
    bool shuffle;
    bool no_osd;
    bool loop_flag;           // --loop shorthand for loop-file=inf
    int video_rotate;         // passthrough to mpv video-rotate (-1 unset)
    const char *panscan;      // passthrough to mpv panscan value
    bool no_config;           // skip loading default config file
    bool smooth;              // apply a sensible playback preset
    bool atomic_nonblock;     // use nonblocking atomic flips
    bool gl_finish;           // call glFinish() before flips (serialize GPU)
    bool use_atomic;          // try DRM atomic modesetting
    // Unified layout mode: stack3, row3, 2x1, 1x2, 2over1, 1over2
    int layout_mode;          // 0=stack3,1=row3,2=2x1,3=1x2,4=2over1,5=1over2
    int fs_cycle_sec;         // fullscreen cycle interval in seconds
    int roles[3]; bool roles_set; // initial slot roles: 0=video,1=paneA,2=paneB
    const char **mpv_opts; int n_mpv_opts; int cap_mpv_opts; // global mpv opts key=val
    const char *config_file; const char *save_config_file; bool save_config_default;
    const char *mpv_out_path;       // file or FIFO for mpv event/log output
    const char *playlist_fifo;      // FIFO to append playlist entries from
} options_t;

// TTY restore state
static struct termios g_oldt;
static int g_have_oldt = 0;
static void restore_tty(void){ if (g_have_oldt) tcsetattr(0, TCSANOW, &g_oldt); }

static int g_debug = 0;
static void dbg(const char *fmt, ...){
    if(!g_debug) return;
    va_list ap; va_start(ap, fmt); vfprintf(stderr, fmt, ap); va_end(ap);
}

static volatile sig_atomic_t g_stop = 0;
static void handle_stop(int sig){ (void)sig; g_stop = 1; }

static void dump_bt_and_exit(int sig){
    fprintf(stderr, "\nCaught signal %d. Dumping backtrace...\n", sig);
#ifdef __linux__
    void *buf[64]; int n = backtrace(buf, 64);
    backtrace_symbols_fd(buf, n, 2);
#endif
    _exit(128+sig);
}

static void install_signal_handlers(void){
    struct sigaction sa; memset(&sa, 0, sizeof sa);
    sa.sa_handler = dump_bt_and_exit; sa.sa_flags = SA_RESETHAND;
    sigaction(SIGSEGV, &sa, NULL);
    sigaction(SIGABRT, &sa, NULL);
    sigaction(SIGFPE,  &sa, NULL);
    sigaction(SIGILL,  &sa, NULL);

    // Graceful termination on SIGTERM (Ctrl+C not used to quit)
    struct sigaction sb; memset(&sb, 0, sizeof sb);
    sb.sa_handler = handle_stop; sb.sa_flags = 0;
    sigaction(SIGTERM, &sb, NULL);
}

static void die(const char *msg) {
    perror(msg);
    exit(1);
}

static void gl_reset_state_2d(void){
    glDisable(GL_SCISSOR_TEST);
    glDisable(GL_DEPTH_TEST);
    glDisable(GL_CULL_FACE);
    glDisable(GL_DITHER);
    glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE);
}

// Translate raw key bytes to mpv key names and send via "keypress".
// Also issues fallbacks for space/n/p where useful.
static void mpv_send_keys(mpv_handle *mpv, const char *buf, ssize_t n){
    for (ssize_t i=0; i<n; ){
        unsigned char ch = (unsigned char)buf[i];
        if (ch == 0x1b) { // ESC or CSI
            if (i+1 >= n) { const char *c[]={"keypress","ESC",NULL}; mpv_command_async(mpv,0,c); i++; continue; }
            unsigned char n1 = (unsigned char)buf[i+1];
            if (n1=='[') {
                // CSI sequences
                if (i+2 < n) {
                    unsigned char n2 = (unsigned char)buf[i+2];
                    const char *name=NULL;
                    if (n2=='A') name="UP";
                    else if (n2=='B') name="DOWN";
                    else if (n2=='C') name="RIGHT";
                    else if (n2=='D') name="LEFT";
                    if (name) { const char *c[]={"keypress",name,NULL}; mpv_command_async(mpv,0,c); i+=3; continue; }
                    // ESC [ <num> ~
                    int num=0; int j=i+2; while (j<n && buf[j]>='0'&&buf[j]<='9'){ num = num*10 + (buf[j]-'0'); j++; }
                    if (j<n && buf[j]=='~'){
                        const char *name2=NULL;
                        if (num==1) name2="HOME";
                        else if (num==2) name2="INS";
                        else if (num==3) name2="DEL";
                        else if (num==4) name2="END";
                        else if (num==5) name2="PGUP";
                        else if (num==6) name2="PGDWN";
                        else if (num==15) name2="F5";
                        else if (num==17) name2="F6";
                        else if (num==18) name2="F7";
                        else if (num==19) name2="F8";
                        else if (num==20) name2="F9";
                        else if (num==21) name2="F10";
                        else if (num==23) name2="F11";
                        else if (num==24) name2="F12";
                        if (name2){ const char *c[]={"keypress",name2,NULL}; mpv_command_async(mpv,0,c); i = j+1; continue; }
                    }
                }
                // Unhandled CSI; skip ESC
                i++;
                continue;
            } else if (n1=='O') {
                // ESC O P/Q/R/S => F1..F4
                if (i+2 < n){
                    unsigned char n2 = (unsigned char)buf[i+2];
                    const char *name=NULL;
                    if (n2=='P') name="F1"; else if (n2=='Q') name="F2"; else if (n2=='R') name="F3"; else if (n2=='S') name="F4";
                    if (name){ const char *c[]={"keypress",name,NULL}; mpv_command_async(mpv,0,c); i+=3; continue; }
                }
                i++;
                continue;
            } else {
                const char *c[]={"keypress","ESC",NULL}; mpv_command_async(mpv,0,c); i++; continue;
            }
        }
        // printable ASCII
        if (ch >= 32 && ch <= 126) {
            char key[2]={ (char)ch, 0};
            const char *c[]={"keypress", key, NULL}; mpv_command_async(mpv,0,c);
            if (ch==' ') { const char *c2[]={"cycle","pause",NULL}; mpv_command_async(mpv,0,c2); }
            else if (ch=='n') { const char *c3[]={"playlist-next",NULL}; mpv_command_async(mpv,0,c3); }
            else if (ch=='p') { const char *c4[]={"playlist-prev",NULL}; mpv_command_async(mpv,0,c4); }
            i++; continue;
        }
        if (ch=='\r' || ch=='\n') { const char *c[]={"keypress","ENTER",NULL}; mpv_command_async(mpv,0,c); i++; continue; }
        if (ch=='\t') { const char *c[]={"keypress","TAB",NULL}; mpv_command_async(mpv,0,c); i++; continue; }
        if (ch==0x7f) { const char *c[]={"keypress","BS",NULL}; mpv_command_async(mpv,0,c); i++; continue; }
        // Unknown control, skip
        i++;
    }
}

static void advise_no_drm(void) {
    fprintf(stderr,
        "No DRM device found (expected /dev/dri/card[0-2]).\n"
        "This program must run on a Linux console with KMS/DRM available.\n"
        "Tips:\n"
        "  - Ensure GPU drivers are loaded (e.g., i915/amdgpu/nouveau).\n"
        "  - On Unraid, enable the iGPU or pass the GPU through, and expose /dev/dri.\n"
        "  - If running in a container, pass --device=/dev/dri and required privileges.\n"
        "  - Run from a real TTY; mode setting requires DRM master (often root).\n");
}

static int open_drm_card(void) {
    const char *candidates[] = {
        "/dev/dri/card0", "/dev/dri/card1", "/dev/dri/card2"
    };
    for (size_t i = 0; i < sizeof(candidates)/sizeof(candidates[0]); i++) {
        int fd = open(candidates[i], O_RDWR | O_CLOEXEC);
        if (fd >= 0) return fd;
    }
    advise_no_drm();
    errno = ENODEV;
    die("open_drm_card");
    return -1;
}

static void advise_dri_drivers(void) {
    fprintf(stderr,
        "DRM device opened, but GBM/EGL failed to create a window surface.\n"
        "Likely missing Mesa GBM/EGL or DRI driver files for your GPU.\n"
        "Check these locations for DRI drivers (should contain e.g. iris_dri.so/radeonsi_dri.so):\n"
        "  - /usr/lib64/dri\n"
        "  - /usr/lib/x86_64-linux-gnu/dri\n"
        "On Unraid, install the GPU plugin or Mesa packages providing DRI.\n");
}

static void warn_if_missing_dri(void) {
    const char *paths[] = {
        "/usr/lib64/dri",
        "/usr/lib/x86_64-linux-gnu/dri",
        "/usr/lib/aarch64-linux-gnu/dri",
        NULL
    };
    int found = 0;
    for (int i=0; paths[i]; ++i) {
        if (access(paths[i], R_OK) == 0) { found = 1; break; }
    }
    if (!found) {
        fprintf(stderr,
            "Warning: No standard DRI driver directories found.\n"
            "EGL/GBM may fail to create a surface. Ensure Mesa DRI drivers are installed.\n");
    }
}

static void preflight_expect_dri_driver(void) {
    // Probe vendor id via sysfs
    unsigned vendor = 0;
    FILE *vf = fopen("/sys/class/drm/card0/device/vendor", "r");
    if (vf) { if (fscanf(vf, "%x", &vendor) != 1) vendor = 0; fclose(vf); }
    const char *vendor_name = "unknown";
    const char *expect_primary = NULL;
    const char *expect_alt = NULL;
    switch (vendor) {
        case 0x8086: vendor_name = "Intel"; expect_primary = "iris_dri.so"; expect_alt = "i965_dri.so"; break;
        case 0x1002: vendor_name = "AMD"; expect_primary = "radeonsi_dri.so"; expect_alt = "r600_dri.so"; break;
        case 0x10de: vendor_name = "NVIDIA"; expect_primary = "nouveau_dri.so"; expect_alt = NULL; break;
        default: vendor_name = "Unknown"; expect_primary = ""; expect_alt = NULL; break;
    }
    const char *paths[] = {
        "/usr/lib64/dri",
        "/usr/lib/x86_64-linux-gnu/dri",
        "/usr/lib/aarch64-linux-gnu/dri",
        NULL
    };
    int found = 0;
    for (int i=0; paths[i]; ++i) {
        if (!paths[i]) break;
        char buf1[512], buf2[512];
        if (expect_primary && *expect_primary) {
            snprintf(buf1, sizeof buf1, "%s/%s", paths[i], expect_primary);
            if (access(buf1, R_OK) == 0) { found = 1; break; }
        }
        if (expect_alt && *expect_alt) {
            snprintf(buf2, sizeof buf2, "%s/%s", paths[i], expect_alt);
            if (access(buf2, R_OK) == 0) { found = 1; break; }
        }
    }
    if (!found) {
        fprintf(stderr,
                "Preflight: Detected GPU vendor: %s (0x%04x).\n",
                vendor_name, vendor);
        if (expect_primary && *expect_primary) {
            fprintf(stderr,
                "Expected DRI driver file: %s%s%s\n",
                expect_primary,
                expect_alt?" (or ":"",
                expect_alt?expect_alt:"");
            if (expect_alt) fprintf(stderr, ")\n"); else fprintf(stderr, "\n");
        } else {
            fprintf(stderr,
                "Could not determine a specific DRI driver. Ensure Mesa DRI drivers are installed.\n");
        }
        fprintf(stderr,
            "Check directories: /usr/lib64/dri, /usr/lib/x86_64-linux-gnu/dri.\n"
            "On Unraid, install the GPU plugin or Mesa packages that provide these files.\n"
            "Note: NVIDIA proprietary driver is not supported by Mesa GBM; nouveau is required for GBM.\n");

        // Try software rasterizer fallback if no driver present
        // kms_swrast provides a GBM-compatible software path
        setenv("MESA_LOADER_DRIVER_OVERRIDE", "kms_swrast", 1);
        setenv("LIBGL_ALWAYS_SOFTWARE", "1", 1);
        fprintf(stderr,
            "Attempting software rasterizer fallback (MESA_LOADER_DRIVER_OVERRIDE=kms_swrast).\n");
    }
}

static void preflight_expect_dri_driver_diag(void) {
    unsigned vendor = 0;
    FILE *vf = fopen("/sys/class/drm/card0/device/vendor", "r");
    if (vf) { if (fscanf(vf, "%x", &vendor) != 1) vendor = 0; fclose(vf); }
    const char *vendor_name = "unknown";
    const char *expect_primary = NULL;
    const char *expect_alt = NULL;
    switch (vendor) {
        case 0x8086: vendor_name = "Intel"; expect_primary = "iris_dri.so"; expect_alt = "i965_dri.so"; break;
        case 0x1002: vendor_name = "AMD"; expect_primary = "radeonsi_dri.so"; expect_alt = "r600_dri.so"; break;
        case 0x10de: vendor_name = "NVIDIA"; expect_primary = "nouveau_dri.so"; expect_alt = NULL; break;
        default: vendor_name = "Unknown"; expect_primary = ""; expect_alt = NULL; break;
    }
    const char *paths[] = {
        "/usr/lib64/dri",
        "/usr/lib/x86_64-linux-gnu/dri",
        "/usr/lib/aarch64-linux-gnu/dri",
        NULL
    };
    int found = 0;
    for (int i=0; paths[i]; ++i) {
        if (!paths[i]) break;
        char buf1[512], buf2[512];
        if (expect_primary && *expect_primary) {
            snprintf(buf1, sizeof buf1, "%s/%s", paths[i], expect_primary);
            if (access(buf1, R_OK) == 0) { found = 1; break; }
        }
        if (expect_alt && *expect_alt) {
            snprintf(buf2, sizeof buf2, "%s/%s", paths[i], expect_alt);
            if (access(buf2, R_OK) == 0) { found = 1; break; }
        }
    }
    fprintf(stderr, "Diag: GPU vendor: %s (0x%04x)\n", vendor_name, vendor);
    if (expect_primary && *expect_primary) {
        fprintf(stderr, "Diag: Expected DRI: %s%s%s\n",
            expect_primary, expect_alt?" or ":"", expect_alt?expect_alt:"");
    }
    for (int i=0; paths[i]; ++i) {
        fprintf(stderr, "Diag: DRI dir %s: %s\n", paths[i], access(paths[i], R_OK)==0?"present":"missing");
    }
    fprintf(stderr, "Diag: DRI driver present: %s\n", found?"yes":"no");
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

static uint32_t get_prop_id(int fd, uint32_t obj_id, uint32_t obj_type, const char *name) {
    drmModeObjectProperties *props = drmModeObjectGetProperties(fd, obj_id, obj_type);
    if (!props) return 0;
    uint32_t id = 0;
    for (uint32_t i=0; i<props->count_props; ++i) {
        drmModePropertyRes *pr = drmModeGetProperty(fd, props->props[i]);
        if (pr) {
            if (strcmp(pr->name, name) == 0) { id = pr->prop_id; drmModeFreeProperty(pr); break; }
            drmModeFreeProperty(pr);
        }
    }
    drmModeFreeObjectProperties(props);
    return id;
}

static int plane_is_primary(int fd, uint32_t plane_id) {
    drmModeObjectProperties *props = drmModeObjectGetProperties(fd, plane_id, DRM_MODE_OBJECT_PLANE);
    if (!props) return 0;
    int is_primary = 0;
    for (uint32_t i=0; i<props->count_props; ++i) {
        drmModePropertyRes *pr = drmModeGetProperty(fd, props->props[i]);
        if (!pr) continue;
        if (strcmp(pr->name, "type") == 0 && (pr->flags & DRM_MODE_PROP_ENUM)) {
            for (int j=0; j<pr->count_enums; ++j) {
                if (strcmp(pr->enums[j].name, "Primary") == 0) {
                    uint64_t val = props->prop_values[i];
                    if (val == pr->enums[j].value) is_primary = 1;
                    break;
                }
            }
        }
        drmModeFreeProperty(pr);
        if (is_primary) break;
    }
    drmModeFreeObjectProperties(props);
    return is_primary;
}

static void try_init_atomic(drm_ctx *d) {
    memset(&d->atomic, 0, sizeof d->atomic);
    if (drmSetClientCap(d->fd, DRM_CLIENT_CAP_UNIVERSAL_PLANES, 1) != 0) return;
    if (drmSetClientCap(d->fd, DRM_CLIENT_CAP_ATOMIC, 1) != 0) return;

    // Find CRTC index for possible_crtcs bitmask
    int crtc_index = -1;
    for (int i=0; i<d->res->count_crtcs; ++i) if (d->res->crtcs[i] == d->crtc_id) { crtc_index = i; break; }
    if (crtc_index < 0) return;

    drmModePlaneRes *pres = drmModeGetPlaneResources(d->fd);
    if (!pres) return;
    uint32_t chosen_plane = 0;
    for (uint32_t i=0; i<pres->count_planes; ++i) {
        drmModePlane *pl = drmModeGetPlane(d->fd, pres->planes[i]);
        if (!pl) continue;
        if (pl->possible_crtcs & (1u << crtc_index)) {
            if (plane_is_primary(d->fd, pl->plane_id)) { chosen_plane = pl->plane_id; drmModeFreePlane(pl); break; }
        }
        drmModeFreePlane(pl);
    }
    drmModeFreePlaneResources(pres);
    if (!chosen_plane) return;

    // Fetch required property IDs
    uint32_t mode_id = get_prop_id(d->fd, d->crtc_id, DRM_MODE_OBJECT_CRTC, "MODE_ID");
    uint32_t active  = get_prop_id(d->fd, d->crtc_id, DRM_MODE_OBJECT_CRTC, "ACTIVE");
    uint32_t out_fence_ptr = get_prop_id(d->fd, d->crtc_id, DRM_MODE_OBJECT_CRTC, "OUT_FENCE_PTR");
    uint32_t conn_crtc = get_prop_id(d->fd, d->conn_id, DRM_MODE_OBJECT_CONNECTOR, "CRTC_ID");
    uint32_t fb_id = get_prop_id(d->fd, chosen_plane, DRM_MODE_OBJECT_PLANE, "FB_ID");
    uint32_t crtc_id = get_prop_id(d->fd, chosen_plane, DRM_MODE_OBJECT_PLANE, "CRTC_ID");
    uint32_t src_x = get_prop_id(d->fd, chosen_plane, DRM_MODE_OBJECT_PLANE, "SRC_X");
    uint32_t src_y = get_prop_id(d->fd, chosen_plane, DRM_MODE_OBJECT_PLANE, "SRC_Y");
    uint32_t src_w = get_prop_id(d->fd, chosen_plane, DRM_MODE_OBJECT_PLANE, "SRC_W");
    uint32_t src_h = get_prop_id(d->fd, chosen_plane, DRM_MODE_OBJECT_PLANE, "SRC_H");
    uint32_t crtc_x = get_prop_id(d->fd, chosen_plane, DRM_MODE_OBJECT_PLANE, "CRTC_X");
    uint32_t crtc_y = get_prop_id(d->fd, chosen_plane, DRM_MODE_OBJECT_PLANE, "CRTC_Y");
    uint32_t crtc_w = get_prop_id(d->fd, chosen_plane, DRM_MODE_OBJECT_PLANE, "CRTC_W");
    uint32_t crtc_h = get_prop_id(d->fd, chosen_plane, DRM_MODE_OBJECT_PLANE, "CRTC_H");
    uint32_t in_fence_fd = get_prop_id(d->fd, chosen_plane, DRM_MODE_OBJECT_PLANE, "IN_FENCE_FD");

    if (!mode_id || !active || !conn_crtc || !fb_id || !crtc_id || !src_x || !src_y || !src_w || !src_h || !crtc_x || !crtc_y || !crtc_w || !crtc_h) {
        return;
    }

    d->atomic.enabled = 1;
    d->atomic.plane_id = chosen_plane;
    d->atomic.crtc_props.mode_id = mode_id;
    d->atomic.crtc_props.active = active;
    d->atomic.conn_props.crtc_id = conn_crtc;
    d->atomic.plane_props.fb_id = fb_id;
    d->atomic.plane_props.crtc_id = crtc_id;
    d->atomic.crtc_props.out_fence_ptr = out_fence_ptr;
    d->atomic.plane_props.src_x = src_x;
    d->atomic.plane_props.src_y = src_y;
    d->atomic.plane_props.src_w = src_w;
    d->atomic.plane_props.src_h = src_h;
    d->atomic.plane_props.crtc_x = crtc_x;
    d->atomic.plane_props.crtc_y = crtc_y;
    d->atomic.plane_props.crtc_w = crtc_w;
    d->atomic.plane_props.crtc_h = crtc_h;
    d->atomic.plane_props.in_fence_fd = in_fence_fd;
}

static bool str_is_digits(const char *s) {
    if (!s || !*s) return false;
    for (const char *p = s; *p; ++p) {
        if (*p < '0' || *p > '9') return false;
    }
    return true;
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
    dbg("DRM: selected connector %u (%s-%u), mode %dx%d@?\n",
        d->conn_id, conn_type_str(best_conn->connector_type), best_conn->connector_type_id,
        best_mode.hdisplay, best_mode.vdisplay);

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
    // Initialize atomic plane/props if requested
    d->atomic.enabled = 0;
    if (opt->use_atomic) {
        try_init_atomic(d);
        if (!d->atomic.enabled) {
            fprintf(stderr, "Note: DRM atomic not available; using legacy KMS.\n");
        } else {
            fprintf(stderr, "Using DRM atomic modesetting (plane %u).\n", d->atomic.plane_id);
        }
        d->atomic.nonblock = opt->atomic_nonblock ? 1 : 0;
        if (g_debug && d->atomic.enabled) {
            fprintf(stderr, "Atomic props: CRTC MODE_ID=%u ACTIVE=%u OUT_FENCE_PTR=%u\n",
                    d->atomic.crtc_props.mode_id, d->atomic.crtc_props.active, d->atomic.crtc_props.out_fence_ptr);
            fprintf(stderr, "Atomic props: PLANE FB_ID=%u CRTC_ID=%u SRC_(x,y,w,h)=(%u,%u,%u,%u) CRTC_(x,y,w,h)=(%u,%u,%u,%u) IN_FENCE_FD=%u\n",
                    d->atomic.plane_props.fb_id, d->atomic.plane_props.crtc_id,
                    d->atomic.plane_props.src_x, d->atomic.plane_props.src_y, d->atomic.plane_props.src_w, d->atomic.plane_props.src_h,
                    d->atomic.plane_props.crtc_x, d->atomic.plane_props.crtc_y, d->atomic.plane_props.crtc_w, d->atomic.plane_props.crtc_h,
                    d->atomic.plane_props.in_fence_fd);
        }
    }
}

static void gbm_init(gbm_ctx *g, int drm_fd, int w, int h) {
    g->dev = gbm_create_device(drm_fd);
    if (!g->dev) die("gbm_create_device");
    g->surface = gbm_surface_create(g->dev, w, h, GBM_FORMAT_XRGB8888,
                                    GBM_BO_USE_SCANOUT | GBM_BO_USE_RENDERING);
    if (!g->surface) die("gbm_surface_create");
    g->w = w; g->h = h;
    dbg("GBM: device+surface created %dx%d, format=XRGB8888\n", w, h);
}

static EGLConfig find_config_for_format(EGLDisplay dpy, EGLint renderable_type, EGLBoolean want_alpha, uint32_t fourcc)
{
    EGLint num = 0;
    EGLint attrs[] = {
        EGL_SURFACE_TYPE, EGL_WINDOW_BIT,
        EGL_RED_SIZE, 8,
        EGL_GREEN_SIZE, 8,
        EGL_BLUE_SIZE, 8,
        EGL_ALPHA_SIZE, want_alpha ? 8 : 0,
        EGL_RENDERABLE_TYPE, renderable_type,
        EGL_NONE
    };
    eglChooseConfig(dpy, attrs, NULL, 0, &num);
    if (num <= 0) return NULL;
    EGLConfig *cfgs = calloc((size_t)num, sizeof *cfgs);
    if (!cfgs) return NULL;
    eglChooseConfig(dpy, attrs, cfgs, num, &num);
    EGLConfig match = NULL;
    for (EGLint i=0; i<num; i++) {
        EGLint id = 0; eglGetConfigAttrib(dpy, cfgs[i], EGL_NATIVE_VISUAL_ID, &id);
        if ((uint32_t)id == fourcc) { match = cfgs[i]; break; }
    }
    if (!match && num>0) match = cfgs[0];
    EGLConfig ret = match;
    free(cfgs);
    return ret;
}

static const char* egl_err_str(EGLint ecode){
    switch(ecode){
        case EGL_SUCCESS: return "EGL_SUCCESS";
        case EGL_NOT_INITIALIZED: return "EGL_NOT_INITIALIZED";
        case EGL_BAD_ACCESS: return "EGL_BAD_ACCESS";
        case EGL_BAD_ALLOC: return "EGL_BAD_ALLOC";
        case EGL_BAD_ATTRIBUTE: return "EGL_BAD_ATTRIBUTE";
        case EGL_BAD_CONTEXT: return "EGL_BAD_CONTEXT";
        case EGL_BAD_CONFIG: return "EGL_BAD_CONFIG";
        case EGL_BAD_CURRENT_SURFACE: return "EGL_BAD_CURRENT_SURFACE";
        case EGL_BAD_DISPLAY: return "EGL_BAD_DISPLAY";
        case EGL_BAD_SURFACE: return "EGL_BAD_SURFACE";
        case EGL_BAD_MATCH: return "EGL_BAD_MATCH";
        case EGL_BAD_PARAMETER: return "EGL_BAD_PARAMETER";
        case EGL_BAD_NATIVE_PIXMAP: return "EGL_BAD_NATIVE_PIXMAP";
        case EGL_BAD_NATIVE_WINDOW: return "EGL_BAD_NATIVE_WINDOW";
        default: return "EGL_ERROR";
    }
}

static void dbg_print_config(EGLDisplay dpy, EGLConfig cfg){
    if(!g_debug || !cfg) return;
    EGLint val=0;
    eglGetConfigAttrib(dpy, cfg, EGL_RENDERABLE_TYPE, &val);
    fprintf(stderr, "EGL cfg renderable: 0x%x\n", val);
    eglGetConfigAttrib(dpy, cfg, EGL_NATIVE_VISUAL_ID, &val);
    fprintf(stderr, "EGL cfg native_visual_id: 0x%x\n", val);
    eglGetConfigAttrib(dpy, cfg, EGL_BUFFER_SIZE, &val);
    fprintf(stderr, "EGL cfg buffer_size: %d\n", val);
}

static void egl_init(egl_ctx *e, gbm_ctx *g) {
    e->dpy = eglGetDisplay((EGLNativeDisplayType)g->dev);
    if (e->dpy == EGL_NO_DISPLAY) die("eglGetDisplay");
    if (!eglInitialize(e->dpy, NULL, NULL)) die("eglInitialize");
    if (g_debug) {
        const char *egl_ver = eglQueryString(e->dpy, EGL_VERSION);
        const char *egl_vendor = eglQueryString(e->dpy, EGL_VENDOR);
        fprintf(stderr, "EGL initialized: version=%s, vendor=%s\n",
                egl_ver?egl_ver:"?", egl_vendor?egl_vendor:"?");
    }
    if (g_debug) fprintf(stderr, "EGL: binding API EGL_OPENGL_ES_API\n");
    eglBindAPI(EGL_OPENGL_ES_API);

    // Try to match the GBM surface format first (XRGB8888), else fallback to ARGB8888 and recreate GBM surface
    EGLint renderable = EGL_OPENGL_ES2_BIT;
    if (g_debug) fprintf(stderr, "EGL: choosing config for XRGB8888...\n");
    EGLConfig cfg = find_config_for_format(e->dpy, renderable, EGL_FALSE, GBM_FORMAT_XRGB8888);
    if (!cfg) cfg = find_config_for_format(e->dpy, renderable, EGL_TRUE, GBM_FORMAT_ARGB8888);
    e->cfg = cfg;
    if (!e->cfg) die("eglChooseConfig");
    if (g_debug) fprintf(stderr, "EGL: got config %p\n", (void*)e->cfg);
    dbg_print_config(e->dpy, e->cfg);

    static const EGLint ctx_attribs[] = {EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE};
    if (g_debug) fprintf(stderr, "EGL: creating context...\n");
    e->ctx = eglCreateContext(e->dpy, e->cfg, EGL_NO_CONTEXT, ctx_attribs);
    if (e->ctx == EGL_NO_CONTEXT) die("eglCreateContext");
    if (g_debug) fprintf(stderr, "EGL: context %p created\n", (void*)e->ctx);

    if (g_debug) fprintf(stderr, "EGL: creating window surface...\n");
    e->surf = eglCreateWindowSurface(e->dpy, e->cfg, (EGLNativeWindowType)g->surface, NULL);
    if (e->surf == EGL_NO_SURFACE) {
        // Fallback: recreate GBM surface with ARGB8888 and try again
        EGLint err = eglGetError();
        fprintf(stderr, "eglCreateWindowSurface failed: %s. Retrying with ARGB8888...\n", egl_err_str(err));
        int w = g->w;
        int h = g->h;
        gbm_surface_destroy(g->surface);
        g->surface = gbm_surface_create(g->dev, (uint32_t)w, (uint32_t)h, GBM_FORMAT_ARGB8888,
                                        GBM_BO_USE_SCANOUT | GBM_BO_USE_RENDERING);
        if (!g->surface) die("gbm_surface_create ARGB8888");
        e->cfg = find_config_for_format(e->dpy, renderable, EGL_TRUE, GBM_FORMAT_ARGB8888);
        if (!e->cfg) die("eglChooseConfig ARGB8888");
        if (g_debug) fprintf(stderr, "EGL: retrying window surface with ARGB8888...\n");
        e->surf = eglCreateWindowSurface(e->dpy, e->cfg, (EGLNativeWindowType)g->surface, NULL);
        if (e->surf == EGL_NO_SURFACE) {
            EGLint err2 = eglGetError();
            fprintf(stderr, "eglCreateWindowSurface still failing: %s\n", egl_err_str(err2));
            advise_dri_drivers();
            die("eglCreateWindowSurface");
        }
    }
    if (g_debug) fprintf(stderr, "EGL: making context current...\n");
    if (!eglMakeCurrent(e->dpy, e->surf, e->surf, e->ctx)) die("eglMakeCurrent");
    if (g_debug) {
        EGLContext cur = eglGetCurrentContext();
        EGLSurface draw = eglGetCurrentSurface(EGL_DRAW);
        EGLDisplay dcur = eglGetCurrentDisplay();
        fprintf(stderr, "EGL current: ctx=%p draw=%p dpy=%p\n", (void*)cur, (void*)draw, (void*)dcur);
    }
    const char *renderer = (const char*)glGetString(GL_RENDERER);
    const char *vendor = (const char*)glGetString(GL_VENDOR);
    if (renderer && vendor)
        fprintf(stderr, "EGL/GL renderer: %s (%s)\n", renderer, vendor);
    gl_check("after eglMakeCurrent");
    if (!renderer || !vendor) {
        fprintf(stderr, "EGL: GL strings unavailable. Forcing software fallback (kms_swrast) and retrying...\n");
        setenv("MESA_LOADER_DRIVER_OVERRIDE", "kms_swrast", 1);
        setenv("LIBGL_ALWAYS_SOFTWARE", "1", 1);
        eglMakeCurrent(e->dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
        if (e->ctx) { eglDestroyContext(e->dpy, e->ctx); }
        e->ctx = EGL_NO_CONTEXT;
        if (e->surf) { eglDestroySurface(e->dpy, e->surf); }
        e->surf = EGL_NO_SURFACE;
        eglTerminate(e->dpy); e->dpy = EGL_NO_DISPLAY;
        e->dpy = eglGetDisplay((EGLNativeDisplayType)g->dev);
        if (e->dpy == EGL_NO_DISPLAY) die("eglGetDisplay-soft");
        if (!eglInitialize(e->dpy, NULL, NULL)) die("eglInitialize-soft");
        eglBindAPI(EGL_OPENGL_ES_API);
        EGLint renderable2 = EGL_OPENGL_ES2_BIT;
        e->cfg = find_config_for_format(e->dpy, renderable2, EGL_TRUE, GBM_FORMAT_ARGB8888);
        if (!e->cfg) die("eglChooseConfig-soft");
        static const EGLint ctx_attribs2[] = {EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE};
        e->ctx = eglCreateContext(e->dpy, e->cfg, EGL_NO_CONTEXT, ctx_attribs2);
        if (e->ctx == EGL_NO_CONTEXT) die("eglCreateContext-soft");
        e->surf = eglCreateWindowSurface(e->dpy, e->cfg, (EGLNativeWindowType)g->surface, NULL);
        if (e->surf == EGL_NO_SURFACE) die("eglCreateWindowSurface-soft");
        if (!eglMakeCurrent(e->dpy, e->surf, e->surf, e->ctx)) die("eglMakeCurrent-soft");
        renderer = (const char*)glGetString(GL_RENDERER);
        vendor = (const char*)glGetString(GL_VENDOR);
        if (renderer && vendor)
            fprintf(stderr, "EGL/GL renderer (soft): %s (%s)\n", renderer, vendor);
    }
    eglSwapInterval(e->dpy, 1);
}

static uint32_t drm_fb_for_bo(int drm_fd, struct gbm_bo *bo) {
    uint32_t fb_id = 0;
    uint32_t width = gbm_bo_get_width(bo);
    uint32_t height = gbm_bo_get_height(bo);
    uint32_t stride = gbm_bo_get_stride(bo);
    uint32_t handle = gbm_bo_get_handle(bo).u32;
    uint32_t format = gbm_bo_get_format(bo);
    uint32_t handles[4] = {handle,0,0,0};
    uint32_t strides[4] = {stride,0,0,0};
    uint32_t offsets[4] = {0,0,0,0};
    // Try AddFB2 with modifiers via raw ioctl if a modifier is present
    uint64_t modifier = 0;
    #ifdef DRM_FORMAT_MOD_INVALID
    modifier = gbm_bo_get_modifier(bo);
    if (modifier != DRM_FORMAT_MOD_INVALID) {
        struct drm_mode_fb_cmd2 cmd = {0};
        cmd.width = width; cmd.height = height; cmd.pixel_format = format ? format : DRM_FORMAT_XRGB8888;
        cmd.handles[0] = handles[0];
        cmd.pitches[0] = strides[0];
        cmd.offsets[0] = offsets[0];
        cmd.modifier[0] = modifier;
        cmd.flags = DRM_MODE_FB_MODIFIERS;
        if (drmIoctl(drm_fd, DRM_IOCTL_MODE_ADDFB2, &cmd) == 0) {
            return cmd.fb_id;
        }
    }
    #endif
    // Try AddFB2 first without modifiers
    if (drmModeAddFB2(drm_fd, width, height, format ? format : DRM_FORMAT_XRGB8888,
                      handles, strides, offsets, &fb_id, 0) == 0) {
        return fb_id;
    }
    // Fallback to legacy AddFB assuming XRGB8888
    int ret = drmModeAddFB(drm_fd, width, height, 24, 32, stride, handle, &fb_id);
    if (ret) die("drmModeAddFB");
    return fb_id;
}

static void drm_set_mode(drm_ctx *d, gbm_ctx *g) {
    // If atomic is enabled and ready, perform an atomic modeset
    if (d->atomic.enabled) {
        g->bo = gbm_surface_lock_front_buffer(g->surface);
        if (!g->bo) die("gbm_surface_lock_front_buffer");
        g->fb_id = drm_fb_for_bo(d->fd, g->bo);

        drmModeAtomicReq *req = drmModeAtomicAlloc();
        if (!req) die("drmModeAtomicAlloc");

        // MODE_ID blob
        uint32_t blob_id = 0;
        if (drmModeCreatePropertyBlob(d->fd, &d->mode, sizeof(d->mode), &blob_id) != 0) {
            drmModeAtomicFree(req);
            die("drmModeCreatePropertyBlob");
        }

        int r = 0;
        int out_fence = -1;
        r |= drmModeAtomicAddProperty(req, d->crtc_id, d->atomic.crtc_props.mode_id, blob_id) <= 0;
        r |= drmModeAtomicAddProperty(req, d->crtc_id, d->atomic.crtc_props.active, 1) <= 0;
        if (d->atomic.crtc_props.out_fence_ptr) {
            uint64_t ptr = (uint64_t)(uintptr_t)&out_fence;
            r |= drmModeAtomicAddProperty(req, d->crtc_id, d->atomic.crtc_props.out_fence_ptr, ptr) <= 0;
        }
        r |= drmModeAtomicAddProperty(req, d->conn_id, d->atomic.conn_props.crtc_id, d->crtc_id) <= 0;

        // Plane setup: full-screen
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.crtc_id, d->crtc_id) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.fb_id, g->fb_id) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.src_x, 0) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.src_y, 0) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.src_w, (uint64_t)d->mode.hdisplay << 16) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.src_h, (uint64_t)d->mode.vdisplay << 16) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.crtc_x, 0) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.crtc_y, 0) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.crtc_w, d->mode.hdisplay) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.crtc_h, d->mode.vdisplay) <= 0;

        if (r) {
            drmModeAtomicFree(req);
            drmModeDestroyPropertyBlob(d->fd, blob_id);
            die("drmModeAtomicAddProperty");
        }
        // For the initial modeset, use a blocking commit to simplify bootstrapping
        if (drmModeAtomicCommit(d->fd, req, DRM_MODE_ATOMIC_ALLOW_MODESET, g) != 0) {
            drmModeAtomicFree(req);
            drmModeDestroyPropertyBlob(d->fd, blob_id);
            die("drmModeAtomicCommit (modeset)");
        }
        drmModeAtomicFree(req);
        drmModeDestroyPropertyBlob(d->fd, blob_id);
        if (out_fence >= 0) close(out_fence);
        g->in_flight = 0;
        return;
    }

    // Legacy SetCrtc path
    g->bo = gbm_surface_lock_front_buffer(g->surface);
    if (!g->bo) die("gbm_surface_lock_front_buffer");
    g->fb_id = drm_fb_for_bo(d->fd, g->bo);
    int ret = drmModeSetCrtc(d->fd, d->crtc_id, g->fb_id, 0, 0, &d->conn_id, 1, &d->mode);
    if (ret) die("drmModeSetCrtc");
}

static void page_flip(drm_ctx *d, gbm_ctx *g) {
    g->next_bo = gbm_surface_lock_front_buffer(g->surface);
    uint32_t fb = drm_fb_for_bo(d->fd, g->next_bo);
    if (d->atomic.enabled) {
        drmModeAtomicReq *req = drmModeAtomicAlloc();
        if (!req) die("drmModeAtomicAlloc");
        int r = 0;
        int out_fence = -1;
        // Update plane fully for robustness across drivers
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.crtc_id, d->crtc_id) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.fb_id, fb) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.src_x, 0) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.src_y, 0) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.src_w, (uint64_t)d->mode.hdisplay << 16) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.src_h, (uint64_t)d->mode.vdisplay << 16) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.crtc_x, 0) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.crtc_y, 0) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.crtc_w, d->mode.hdisplay) <= 0;
        r |= drmModeAtomicAddProperty(req, d->atomic.plane_id, d->atomic.plane_props.crtc_h, d->mode.vdisplay) <= 0;
        if (d->atomic.crtc_props.out_fence_ptr) {
            uint64_t ptr = (uint64_t)(uintptr_t)&out_fence;
            r |= drmModeAtomicAddProperty(req, d->crtc_id, d->atomic.crtc_props.out_fence_ptr, ptr) <= 0;
        }
        if (r) { drmModeAtomicFree(req); die("drmModeAtomicAddProperty (flip)"); }
        // Choose blocking or nonblocking commit based on configuration
        unsigned int flags = d->atomic.nonblock ? DRM_MODE_ATOMIC_NONBLOCK : 0;
        if (drmModeAtomicCommit(d->fd, req, flags, d->atomic.nonblock ? g : NULL) != 0) {
            drmModeAtomicFree(req);
            fprintf(stderr, "drmModeAtomicCommit (flip) failed; falling back to legacy\n");
            d->atomic.enabled = 0; // disable atomic on failure
        } else {
            drmModeAtomicFree(req);
            if (out_fence >= 0) close(out_fence);
            if (d->atomic.nonblock) {
                // Defer swap/release until DRM event
                g->pending_bo = g->next_bo;
                g->pending_fb = fb;
                g->in_flight = 1;
                return;
            } else {
                // Blocking: advance immediately
                if (g->bo) {
                    uint32_t old_fb = g->fb_id;
                    gbm_surface_release_buffer(g->surface, g->bo);
                    drmModeRmFB(d->fd, old_fb);
                }
                g->bo = g->next_bo;
                g->fb_id = fb;
                g->in_flight = 0;
                return;
            }
        }
    }
    // Legacy blocking flip via SetCrtc for simplicity
    int ret = drmModeSetCrtc(d->fd, d->crtc_id, fb, 0, 0, &d->conn_id, 1, &d->mode);
    if (ret) fprintf(stderr, "drmModeSetCrtc (page_flip) failed: %d\n", ret);
    if (g->bo) {
        uint32_t old_fb = g->fb_id;
        gbm_surface_release_buffer(g->surface, g->bo);
        drmModeRmFB(d->fd, old_fb);
    }
    g->bo = g->next_bo;
    g->fb_id = fb;
}

// mpv render update wakeup via pipe, so we can poll
static void mpv_update_wakeup(void *ctx) {
    mpv_ctx *m = (mpv_ctx *)ctx;
    uint64_t one = 1;
    ssize_t n = write(m->wakeup_fd[1], &one, sizeof(one));
    if (n < 0) {
        // ignore EAGAIN for non-blocking pipe; best-effort wakeup
        (void)n;
    }
}

static void *get_proc_address(void *ctx, const char *name) {
    (void)ctx;
    return (void *)eglGetProcAddress(name);
}

static void gl_clear_color(float r, float g, float b, float a) {
    glClearColor(r, g, b, a);
    glClear(GL_COLOR_BUFFER_BIT);
}

static void gl_check(const char *stage){
    if(!g_debug) return;
    GLenum err; int cnt=0;
    while ((err = glGetError()) != GL_NO_ERROR) {
        fprintf(stderr, "GL error at %s: 0x%x\n", stage, (unsigned)err);
        if (++cnt > 8) break;
    }
}

// Draw a colored rectangular border into the currently bound framebuffer using scissor clears
static void draw_border_rect(int x, int y, int w, int h, int thickness, int fb_w, int fb_h,
                             float r, float g, float b, float a) {
    (void)fb_w;
    if (w <= 0 || h <= 0 || thickness <= 0) return;
    if (thickness > w/2) thickness = w/2;
    if (thickness > h/2) thickness = h/2;
    glEnable(GL_SCISSOR_TEST);
    glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE);
    glClearColor(r, g, b, a);
    // Convert to scissor coords (origin bottom-left). Our layout y is bottom-left already.
    int sx = x;
    int sy = fb_h - (y + h);
    if (sx < 0) sx = 0;
    if (sy < 0) sy = 0;
    // Top edge
    glScissor(sx, sy + h - thickness, w, thickness);
    glClear(GL_COLOR_BUFFER_BIT);
    // Bottom edge
    glScissor(sx, sy, w, thickness);
    glClear(GL_COLOR_BUFFER_BIT);
    // Left edge
    glScissor(sx, sy, thickness, h);
    glClear(GL_COLOR_BUFFER_BIT);
    // Right edge
    glScissor(sx + w - thickness, sy, thickness, h);
    glClear(GL_COLOR_BUFFER_BIT);
    glDisable(GL_SCISSOR_TEST);
}

// DRM event handling (atomic page flip completion)
static void on_page_flip(int fd, unsigned int sequence, unsigned int tv_sec, unsigned int tv_usec, void *user_data) {
    (void)fd; (void)sequence; (void)tv_sec; (void)tv_usec;
    gbm_ctx *g = (gbm_ctx*)user_data;
    if (!g) return;
    if (g->in_flight) {
        // Release the previous front buffer and adopt the pending as current
        if (g->bo) {
            // Remove FB and release the old BO
            drmModeRmFB(fd, g->fb_id);
            gbm_surface_release_buffer(g->surface, g->bo);
        }
        g->bo = g->pending_bo;
        g->fb_id = g->pending_fb;
        g->pending_bo = NULL;
        g->pending_fb = 0;
        g->in_flight = 0;
    }
}

// Offscreen render target for logical orientation
static GLuint rt_fbo=0, rt_tex=0; static int rt_w=0, rt_h=0;
static GLuint blit_prog=0, blit_vbo=0; static GLint blit_u_tex=-1;
static GLuint vid_fbo=0, vid_tex=0; static int vid_w=0, vid_h=0;

static GLuint compile_shader(GLenum type, const char *src) {
    GLuint s = glCreateShader(type);
    glShaderSource(s,1,&src,NULL);
    glCompileShader(s);
    GLint ok; glGetShaderiv(s,GL_COMPILE_STATUS,&ok);
    if(!ok){
        char log[1024]; GLsizei ln=0; log[0]='\0';
        glGetShaderInfoLog(s,(GLsizei)sizeof(log),&ln,log);
        fprintf(stderr,"shader compile failed (%s): %.*s\nSource:\n%.*s\n",
                type==GL_VERTEX_SHADER?"vertex":"fragment",
                ln, log,
                200, src);
        exit(1);
    }
    return s;
}

static void ensure_blit_prog(void){
    if(blit_prog) return;
    const char* vs =
        "#version 100\n"
        "#ifdef GL_ES\n"
        "precision mediump float;\n"
        "precision mediump int;\n"
        "#endif\n"
        "attribute vec2 a_pos;\n"
        "attribute vec2 a_uv;\n"
        "varying vec2 v_uv;\n"
        "void main(){ v_uv=a_uv; gl_Position=vec4(a_pos,0.0,1.0); }";
    const char* fs =
        "#version 100\n"
        "precision mediump float;\n"
        "varying vec2 v_uv;\n"
        "uniform sampler2D u_tex;\n"
        "void main(){ gl_FragColor = texture2D(u_tex, v_uv); }";
    GLuint v=compile_shader(GL_VERTEX_SHADER,vs), f=compile_shader(GL_FRAGMENT_SHADER,fs);
    blit_prog=glCreateProgram();
    glAttachShader(blit_prog,v); glAttachShader(blit_prog,f);
    glBindAttribLocation(blit_prog,0,"a_pos"); glBindAttribLocation(blit_prog,1,"a_uv");
    glLinkProgram(blit_prog); GLint ok; glGetProgramiv(blit_prog,GL_LINK_STATUS,&ok);
    if(!ok){fprintf(stderr,"link fail\n"); exit(1);} blit_u_tex=glGetUniformLocation(blit_prog,"u_tex");
    glGenBuffers(1,&blit_vbo);
}

static void ensure_rt(int w, int h){ if(rt_tex && (rt_w==w && rt_h==h)) return; if(rt_tex){ glDeleteTextures(1,&rt_tex); glDeleteFramebuffers(1,&rt_fbo);} rt_w=w; rt_h=h; glGenTextures(1,&rt_tex); glBindTexture(GL_TEXTURE_2D, rt_tex); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE); glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA, w,h,0,GL_RGBA,GL_UNSIGNED_BYTE,NULL); glGenFramebuffers(1,&rt_fbo); glBindFramebuffer(GL_FRAMEBUFFER, rt_fbo); glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, rt_tex, 0); GLenum stat=glCheckFramebufferStatus(GL_FRAMEBUFFER); if(stat!=GL_FRAMEBUFFER_COMPLETE){ fprintf(stderr,"FBO incomplete\n"); exit(1);} glBindFramebuffer(GL_FRAMEBUFFER, 0);} 

static void ensure_video_rt(int w, int h){ if(vid_tex && (vid_w==w && vid_h==h)) return; if(vid_tex){ glDeleteTextures(1,&vid_tex); glDeleteFramebuffers(1,&vid_fbo);} vid_w=w; vid_h=h; glGenTextures(1,&vid_tex); glBindTexture(GL_TEXTURE_2D, vid_tex); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE); glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA, w,h,0,GL_RGBA,GL_UNSIGNED_BYTE,NULL); glGenFramebuffers(1,&vid_fbo); glBindFramebuffer(GL_FRAMEBUFFER, vid_fbo); glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, vid_tex, 0); GLenum stat=glCheckFramebufferStatus(GL_FRAMEBUFFER); if(stat!=GL_FRAMEBUFFER_COMPLETE){ fprintf(stderr,"Video FBO incomplete\n"); exit(1);} glBindFramebuffer(GL_FRAMEBUFFER, 0);} 

static void blit_rt_to_screen(rotation_t rot){ ensure_blit_prog(); glUseProgram(blit_prog); glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D, rt_tex); glUniform1i(blit_u_tex,0); float L=-1.f,R=1.f,B=-1.f,T=1.f; float verts[24]; // 6 verts * (pos2+uv2)
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

static void draw_tex_fullscreen(GLuint tex){
    ensure_blit_prog();
    glUseProgram(blit_prog);
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, tex);
    glUniform1i(blit_u_tex, 0);
    float L=-1.f,R=1.f,B=-1.f,T=1.f;
    float verts[] = { L,B, 0,0,  R,B, 1,0,  R,T, 1,1,  L,B, 0,0,  R,T, 1,1,  L,T, 0,1 };
    glBindBuffer(GL_ARRAY_BUFFER, blit_vbo);
    glBufferData(GL_ARRAY_BUFFER, sizeof(verts), verts, GL_STREAM_DRAW);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0,2,GL_FLOAT,GL_FALSE,4*sizeof(float),(void*)0);
    glEnableVertexAttribArray(1);
    glVertexAttribPointer(1,2,GL_FLOAT,GL_FALSE,4*sizeof(float),(void*)(2*sizeof(float)));
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

// Append a playlist entry to an mpv instance, optionally with per-line options
static void mpv_append_line(mpv_handle *mpv, const char *line){
    if (!mpv || !line) return;
    char *dup = strdup(line);
    if (!dup) return;
    char *p = (char*)trim(dup);
    if (*p=='#' || *p=='\0') { free(dup); return; }
    char *bar = strchr(p, '|');
    char *optstr = NULL;
    if (bar){ *bar='\0'; optstr = bar+1; }
    if (!optstr) {
        const char *cmd[] = {"loadfile", p, "append", NULL};
        mpv_command_async(mpv, 0, cmd);
    } else {
        mpv_node root; memset(&root,0,sizeof(root));
        root.format = MPV_FORMAT_NODE_ARRAY;
        root.u.list = malloc(sizeof(*root.u.list));
        root.u.list->num = 0; root.u.list->values = NULL; root.u.list->keys = NULL;
        // Helper to push a string node
        #define PUSH_STR2(str) do{ root.u.list->values = realloc(root.u.list->values, sizeof(mpv_node)*(root.u.list->num+1)); \
            root.u.list->values[root.u.list->num].format = MPV_FORMAT_STRING; \
            root.u.list->values[root.u.list->num].u.string = strdup(str); \
            root.u.list->num++; }while(0)
        PUSH_STR2("loadfile"); PUSH_STR2(p); PUSH_STR2("append");
        mpv_node map; memset(&map,0,sizeof(map));
        map.format = MPV_FORMAT_NODE_MAP; map.u.list = malloc(sizeof(*map.u.list));
        map.u.list->num=0; map.u.list->values=NULL; map.u.list->keys=NULL;
        char *opts_dup = strdup(optstr);
        char *tok = strtok(opts_dup, ",");
        while (tok){
            char *kv = (char*)trim(tok);
            char *eq = strchr(kv,'=');
            if (eq){
                *eq='\0';
                char *key = strdup(trim(kv));
                char *val = strdup(trim(eq+1));
                map.u.list->keys = realloc(map.u.list->keys, sizeof(char*)*(map.u.list->num+1));
                map.u.list->values = realloc(map.u.list->values, sizeof(mpv_node)*(map.u.list->num+1));
                map.u.list->keys[map.u.list->num] = key;
                map.u.list->values[map.u.list->num].format = MPV_FORMAT_STRING;
                map.u.list->values[map.u.list->num].u.string = val;
                map.u.list->num++;
            }
            tok = strtok(NULL, ",");
        }
        free(opts_dup);
        root.u.list->values = realloc(root.u.list->values, sizeof(mpv_node)*(root.u.list->num+1));
        root.u.list->keys = realloc(root.u.list->keys, sizeof(char*)*(root.u.list->num+1));
        root.u.list->keys[root.u.list->num] = strdup("options");
        root.u.list->values[root.u.list->num] = map; root.u.list->num++;
        mpv_command_node_async(mpv, 0, &root);
        mpv_free_node_contents(&root);
    }
    free(dup);
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
    const char *lay_str = opt->layout_mode==0?"stack":
                         opt->layout_mode==1?"row":
                         opt->layout_mode==2?"2x1":
                         opt->layout_mode==3?"1x2":
                         opt->layout_mode==4?"2over1":"1over2";
    fprintf(f, "--layout %s\n", lay_str);
    if (opt->video_frac_pct) fprintf(f, "--video-frac %d\n", opt->video_frac_pct);
    else if (opt->right_frac_pct) fprintf(f, "--right-frac %d\n", opt->right_frac_pct);
    if (opt->pane_split_pct) fprintf(f, "--pane-split %d\n", opt->pane_split_pct);
    if (opt->roles_set) fprintf(f, "--roles %c%c%c\n", opt->roles[0]==0?'C':opt->roles[0]==1?'A':'B', opt->roles[1]==0?'C':opt->roles[1]==1?'A':'B', opt->roles[2]==0?'C':opt->roles[2]==1?'A':'B');
    if (opt->fs_cycle_sec) fprintf(f, "--fs-cycle-sec %d\n", opt->fs_cycle_sec);
    if (opt->pane_a_cmd) fprintf(f, "--pane-a '%s'\n", opt->pane_a_cmd);
    if (opt->pane_b_cmd) fprintf(f, "--pane-b '%s'\n", opt->pane_b_cmd);
    if (opt->no_video) fprintf(f, "--no-video\n");
    if (opt->loop_file) fprintf(f, "--loop-file\n");
    if (opt->loop_playlist) fprintf(f, "--loop-playlist\n");
    if (opt->shuffle) fprintf(f, "--shuffle\n");
    for (int i=0;i<opt->n_mpv_opts;i++) fprintf(f, "--mpv-opt '%s'\n", opt->mpv_opts[i]);
    if (opt->playlist_path) fprintf(f, "--playlist '%s'\n", opt->playlist_path);
    if (opt->playlist_ext) fprintf(f, "--playlist-extended '%s'\n", opt->playlist_ext);
    if (opt->playlist_fifo) fprintf(f, "--playlist-fifo '%s'\n", opt->playlist_fifo);
    if (opt->mpv_out_path) fprintf(f, "--mpv-out '%s'\n", opt->mpv_out_path);
    for (int i=0;i<opt->video_count;i++){ const video_item *vi=&opt->videos[i]; fprintf(f, "--video '%s'\n", vi->path); for (int k=0;k<vi->nopts;k++) fprintf(f, "--video-opt '%s'\n", vi->opts[k]); }
    fclose(f);
}

int main(int argc, char **argv) {
    install_signal_handlers();
    if (getenv("KMS_MPV_DEBUG")) g_debug = 1;
    // Try to avoid Mesa glthread confusion on some stacks when running headless/GBM
    setenv("mesa_glthread", "false", 0);
    setenv("MESA_GLTHREAD", "0", 0);
    options_t opt = (options_t){0};
    opt.fs_cycle_sec = 5;
    opt.roles[0]=0; opt.roles[1]=1; opt.roles[2]=2;
    // Always define pane pointers early so cleanup is safe on early exits (e.g., --diag)
    term_pane *tp_a = NULL;
    term_pane *tp_b = NULL;
    FILE *mpv_out = NULL;
    int playlist_fifo_fd = -1;
    char pfifo_buf[1024]; int pfifo_len = 0;
    // Preload config file if specified on CLI, else use default if present
    const char *cfg = NULL; for (int i=1;i<argc;i++){ if (!strcmp(argv[i],"--config") && i+1<argc){ cfg = argv[i+1]; break; } }
    if (!cfg) { const char *def = default_config_path(); if (!opt.no_config && access(def, R_OK)==0) cfg = def; }
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
        else if (!strcmp(argv[i], "--playlist-fifo") && i + 1 < argc) opt.playlist_fifo = argv[++i];
        else if (!strcmp(argv[i], "--mpv-out") && i + 1 < argc) opt.mpv_out_path = argv[++i];
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
        else if (!strcmp(argv[i], "--no-panes")) opt.no_panes = true;
        else if (!strcmp(argv[i], "--diag")) opt.diag = true;
        else if (!strcmp(argv[i], "--gl-test")) opt.gl_test = true;
        else if (!strcmp(argv[i], "--no-config")) opt.no_config = true;
        else if (!strcmp(argv[i], "--smooth")) opt.smooth = true;
        else if (!strcmp(argv[i], "--layout") && i + 1 < argc) {
            const char *v = argv[++i];
            if (!strcmp(v,"stack") || !strcmp(v,"stack3")) opt.layout_mode = 0;
            else if (!strcmp(v,"row") || !strcmp(v,"row3")) opt.layout_mode = 1;
            else if (!strcmp(v,"2x1")) opt.layout_mode = 2;
            else if (!strcmp(v,"1x2")) opt.layout_mode = 3;
            else if (!strcmp(v,"2over1")) opt.layout_mode = 4;
            else if (!strcmp(v,"1over2")) opt.layout_mode = 5;
        }
        else if (!strcmp(argv[i], "--landscape-layout") && i + 1 < argc) { // backward compat
            const char *v = argv[++i];
            if (!strcmp(v,"stack") || !strcmp(v,"stack3")) opt.layout_mode = 0;
            else if (!strcmp(v,"row") || !strcmp(v,"row3")) opt.layout_mode = 1;
            else if (!strcmp(v,"2x1")) opt.layout_mode = 2;
            else if (!strcmp(v,"1x2")) opt.layout_mode = 3;
            else if (!strcmp(v,"2over1")) opt.layout_mode = 4;
            else if (!strcmp(v,"1over2")) opt.layout_mode = 5;
        }
        else if (!strcmp(argv[i], "--portrait-layout") && i + 1 < argc) { // backward compat
            const char *v = argv[++i];
            if (!strcmp(v,"stack") || !strcmp(v,"stack3")) opt.layout_mode = 0;
            else if (!strcmp(v,"row") || !strcmp(v,"row3")) opt.layout_mode = 1;
            else if (!strcmp(v,"2x1")) opt.layout_mode = 2;
            else if (!strcmp(v,"1x2")) opt.layout_mode = 3;
            else if (!strcmp(v,"2over1")) opt.layout_mode = 4;
            else if (!strcmp(v,"1over2")) opt.layout_mode = 5;
        }
        else if (!strcmp(argv[i], "--fs-cycle-sec") && i + 1 < argc) { opt.fs_cycle_sec = atoi(argv[++i]); }
        else if (!strcmp(argv[i], "--roles") && i + 1 < argc) {
            const char *r = argv[++i]; int idx=0;
            for (const char *p=r; *p && idx<3; ++p){
                char c=*p;
                if (c=='C' || c=='c') opt.roles[idx++]=0;
                else if (c=='A' || c=='a') opt.roles[idx++]=1;
                else if (c=='B' || c=='b') opt.roles[idx++]=2;
            }
            if (idx==3) opt.roles_set=true;
        }
        else if (!strcmp(argv[i], "--loop-file")) opt.loop_file = true;
        else if (!strcmp(argv[i], "--loop")) opt.loop_flag = true;
        else if (!strcmp(argv[i], "--loop-playlist")) opt.loop_playlist = true;
        else if (!strcmp(argv[i], "--shuffle") || !strcmp(argv[i], "--randomize")) opt.shuffle = true;
        else if (!strcmp(argv[i], "--no-osd")) opt.no_osd = true;
        else if (!strcmp(argv[i], "--atomic")) opt.use_atomic = true;
        else if (!strcmp(argv[i], "--atomic-nonblock")) { opt.use_atomic = true; opt.atomic_nonblock = true; }
        else if (!strcmp(argv[i], "--gl-finish")) opt.gl_finish = true;
        else if (!strcmp(argv[i], "--mpv-opt") && i + 1 < argc) {
            if (opt.n_mpv_opts == opt.cap_mpv_opts){ int nc=opt.cap_mpv_opts?opt.cap_mpv_opts*2:8; opt.mpv_opts=realloc(opt.mpv_opts,(size_t)nc*sizeof(char*)); opt.cap_mpv_opts=nc; }
            opt.mpv_opts[opt.n_mpv_opts++] = argv[++i];
        }
        else if (!strcmp(argv[i], "--save-config-default")) opt.save_config_default = true;
        else if (!strcmp(argv[i], "--debug")) g_debug = 1;
        else if (!strcmp(argv[i], "--video-rotate") && i + 1 < argc) { opt.video_rotate = atoi(argv[++i]); }
        else if (!strcmp(argv[i], "--panscan") && i + 1 < argc) { opt.panscan = argv[++i]; }
        else if (!strcmp(argv[i], "-h") || !strcmp(argv[i], "--help")) {
            const char *exe = argv[0];
            fprintf(stderr,
                "KMS Mosaic — tiled video + terminal panes (Linux KMS console)\n\n"
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
                "  --pane-a \"CMD\"           Command for Pane A (default: btop).\n"
                "  --pane-b \"CMD\"           Command for Pane B (default: tail -f /var/log/syslog).\n"
                "  --layout M              stack | row | 2x1 | 1x2 | 2over1 | 1over2\n"
                "  --roles RRR            Slot roles order, e.g. CAB (default CAB).\n"
                "  --fs-cycle-sec SEC     Fullscreen cycle interval for 'c' key.\n\n"
                "Display/KMS:\n"
                "  --atomic                Use DRM atomic modesetting (experimental; falls back on failure).\n\n"
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
                "  Tab           Cycle focus C/A/B (video/paneA/paneB).\n"
                "  l / L         Cycle layouts forward/back.\n"
                "  r / R         Rotate roles among C/A/B (and reverse).\n"
                "  t             Swap panes A and B.\n"
                "  z             Fullscreen focused pane.\n"
                "  c             Cycle fullscreen panes.\n"
                "  o             Toggle OSD visibility.\n"
                "  ?             Help overlay.\n"
                "  Ctrl+Q        Quit (only active in Control Mode).\n\n",
                exe);
            return 0;
        }
        else {
            // Treat bare arguments that don't start with '-' as video paths for convenience
            if (argv[i][0] != '-') {
                push_video(&opt, argv[i]);
            } else {
                fprintf(stderr, "Warning: unknown option '%s' (ignored). Use --help for usage.\n", argv[i]);
            }
        }
    }

    if (opt.playlist_ext) parse_playlist_ext(&opt, opt.playlist_ext);

    // If exactly one video file is provided and no playlist,
    // assume --loop should be enabled unless user already set a loop.
    if (!opt.playlist_path && !opt.playlist_ext && !opt.playlist_fifo) {
        if (opt.video_count == 1 && !opt.loop_file && !opt.loop_flag) {
            opt.loop_flag = true;
        }
    }

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
    warn_if_missing_dri();
    if (opt.diag) preflight_expect_dri_driver_diag();
    else preflight_expect_dri_driver();
    gbm_init(&g, d.fd, d.mode.hdisplay, d.mode.vdisplay);
    egl_init(&e, &g);

    // mpv setup (skip if --no-video and no videos provided)
    mpv_ctx m = {0};
    bool use_mpv = !opt.no_video && (opt.video_count > 0 || opt.playlist_path || opt.playlist_ext);
    const char *disable_env = getenv("KMS_MPV_DISABLE");
    if (disable_env && (*disable_env=='1' || *disable_env=='y' || *disable_env=='Y')) {
        use_mpv = false; fprintf(stderr, "Debug: KMS_MPV_DISABLE set; skipping mpv setup.\n");
    }
    if (use_mpv) {
        if (pipe2(m.wakeup_fd, O_NONBLOCK | O_CLOEXEC) < 0) die("pipe2");
        m.mpv = mpv_create();
        if (!m.mpv) die("mpv_create");
        // Use libmpv render API; ensure mpv does NOT create its own window/VO.
        // vo=libmpv tells mpv to use the render API output only.
        mpv_set_option_string(m.mpv, "vo", "libmpv");
        mpv_set_option_string(m.mpv, "keep-open", "yes");
        // If this context is OpenGL ES, hint mpv accordingly.
        const char *glver = (const char*)glGetString(GL_VERSION);
        if (glver && strstr(glver, "OpenGL ES")) {
            mpv_set_option_string(m.mpv, "opengl-es", "yes");
        }
        // Global mpv opts
        bool user_set_hwdec = false;
        for (int i=0;i<opt.n_mpv_opts;i++) {
            const char *kv = opt.mpv_opts[i];
            const char *eq = strchr(kv, '=');
            if (eq) {
                char key[128]; size_t kl = (size_t)(eq-kv); if (kl>=sizeof key) kl=sizeof key-1; memcpy(key, kv, kl); key[kl]='\0';
                const char *val = eq+1;
                mpv_set_option_string(m.mpv, key, val);
                if (strcmp(key, "hwdec")==0) user_set_hwdec = true;
            }
        }
        if (!user_set_hwdec) mpv_set_option_string(m.mpv, "hwdec", "no");
        if (opt.loop_file || opt.loop_flag) mpv_set_option_string(m.mpv, "loop-file", "inf");
        if (opt.loop_playlist) mpv_set_option_string(m.mpv, "loop-playlist", "yes");
        if (opt.shuffle) mpv_set_option_string(m.mpv, "shuffle", "yes");
        // Reasonable defaults mirroring a typical KMS usage; can be overridden via --mpv-opt
        // Match user's working flags: video-sync=display-resample, keep aspect, rotate/panscan opts.
        // Only apply if not already set via --mpv-opt.
        bool user_set_vsync = false, user_set_keepaspect = false, user_set_rotate = false, user_set_panscan = false;
        bool user_set_interpolation = false, user_set_tscale = false, user_set_eflush = false, user_set_shader_cache = false;
        for (int i=0;i<opt.n_mpv_opts;i++) {
            const char *kv = opt.mpv_opts[i];
            const char *eq = strchr(kv, '=');
            if (!eq) continue;
            size_t kl = (size_t)(eq-kv);
            if (strncmp(kv, "video-sync", kl==strlen("video-sync")?kl:strlen("video-sync"))==0) user_set_vsync=true;
            else if (strncmp(kv, "keepaspect", kl==strlen("keepaspect")?kl:strlen("keepaspect"))==0) user_set_keepaspect=true;
            else if (strncmp(kv, "video-rotate", kl==strlen("video-rotate")?kl:strlen("video-rotate"))==0) user_set_rotate=true;
            else if (strncmp(kv, "panscan", kl==strlen("panscan")?kl:strlen("panscan"))==0) user_set_panscan=true;
            else if (strncmp(kv, "interpolation", kl==strlen("interpolation")?kl:strlen("interpolation"))==0) user_set_interpolation=true;
            else if (strncmp(kv, "tscale", kl==strlen("tscale")?kl:strlen("tscale"))==0) user_set_tscale=true;
            else if (strncmp(kv, "opengl-early-flush", kl==strlen("opengl-early-flush")?kl:strlen("opengl-early-flush"))==0) user_set_eflush=true;
            else if (strncmp(kv, "gpu-shader-cache", kl==strlen("gpu-shader-cache")?kl:strlen("gpu-shader-cache"))==0) user_set_shader_cache=true;
        }
        if (!user_set_vsync) mpv_set_option_string(m.mpv, "video-sync", "display-resample");
        if (!user_set_keepaspect) mpv_set_option_string(m.mpv, "keepaspect", "yes");
        if (opt.video_rotate >= 0 && !user_set_rotate) { char buf[16]; snprintf(buf,sizeof buf, "%d", opt.video_rotate); mpv_set_option_string(m.mpv, "video-rotate", buf); }
        if (opt.panscan && !user_set_panscan) { mpv_set_option_string(m.mpv, "panscan", opt.panscan); }
        if (opt.smooth) {
            if (!user_set_interpolation) mpv_set_option_string(m.mpv, "interpolation", "no");
            if (!user_set_tscale) mpv_set_option_string(m.mpv, "tscale", "linear");
            if (!user_set_eflush) mpv_set_option_string(m.mpv, "opengl-early-flush", "yes");
            if (!user_set_shader_cache) mpv_set_option_string(m.mpv, "gpu-shader-cache", "no");
        }
        if (g_debug) mpv_request_log_messages(m.mpv, "debug");
        if (mpv_initialize(m.mpv) < 0) die("mpv_initialize");

        int adv = 1;
        mpv_render_param params[] = {
            {MPV_RENDER_PARAM_API_TYPE, (void *)MPV_RENDER_API_TYPE_OPENGL},
            {MPV_RENDER_PARAM_OPENGL_INIT_PARAMS,
             &(mpv_opengl_init_params){.get_proc_address = get_proc_address, .get_proc_address_ctx = NULL}},
            {MPV_RENDER_PARAM_ADVANCED_CONTROL, &adv},
            {0}
        };
        if (mpv_render_context_create(&m.mpv_gl, m.mpv, params) < 0)
            die("mpv_render_context_create");
        mpv_render_context_set_update_callback(m.mpv_gl, mpv_update_wakeup, &m);
        mpv_set_wakeup_callback(m.mpv, mpv_update_wakeup, &m);

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

    if (use_mpv) {
        if (opt.mpv_out_path) {
            mpv_out = fopen(opt.mpv_out_path, "w");
            if (!mpv_out) perror("mpv-out");
        }
        if (opt.playlist_fifo) {
            mkfifo(opt.playlist_fifo, 0666);
            playlist_fifo_fd = open(opt.playlist_fifo, O_RDONLY | O_NONBLOCK);
            if (playlist_fifo_fd < 0) perror("playlist-fifo");
        }
    }

    if (opt.diag) {
        const char *gl_ver = (const char*)glGetString(GL_VERSION);
        const char *glsl = (const char*)glGetString(GL_SHADING_LANGUAGE_VERSION);
        const char *gl_vendor = (const char*)glGetString(GL_VENDOR);
        const char *gl_renderer = (const char*)glGetString(GL_RENDERER);
        fprintf(stderr, "Diag: GL_VERSION=%s\n", gl_ver?gl_ver:"?");
        fprintf(stderr, "Diag: GLSL_VERSION=%s\n", glsl?glsl:"?");
        fprintf(stderr, "Diag: GL_VENDOR=%s\n", gl_vendor?gl_vendor:"?");
        fprintf(stderr, "Diag: GL_RENDERER=%s\n", gl_renderer?gl_renderer:"?");
        const char *bundled = "/usr/local/lib/kms_mpv_compositor";
        fprintf(stderr, "Diag: Bundled lib dir %s: %s\n", bundled, access(bundled, R_OK)==0?"present":"missing");
        // Exit cleanly after diagnostics
        goto cleanup;
    }

    // First frame to program CRTC
    glViewport(0, 0, d.mode.hdisplay, d.mode.vdisplay);
    gl_clear_color(0.f, 0.f, 0.f, 1.f);
    eglSwapBuffers(e.dpy, e.surf);
    if (opt.use_atomic && opt.gl_finish) glFinish();
    drm_set_mode(&d, &g);

    // Logical render target (rotated presentation later)
    int fb_w = d.mode.hdisplay, fb_h = d.mode.vdisplay;
    int logical_w = (opt.rotation==ROT_90 || opt.rotation==ROT_270) ? fb_h : fb_w;
    int logical_h = (opt.rotation==ROT_90 || opt.rotation==ROT_270) ? fb_w : fb_h;
    ensure_rt(logical_w, logical_h);
    // Panes declared at function start; no redeclaration here

    if (opt.gl_test) {
        int frames = 120;
        for (int f=0; f<frames; ++f) {
            if (!eglMakeCurrent(e.dpy, e.surf, e.surf, e.ctx)) die("eglMakeCurrent loop");
            glBindFramebuffer(GL_FRAMEBUFFER, rt_fbo);
            glViewport(0, 0, logical_w, logical_h);
            float t = (float)f / (float)frames;
            gl_clear_color(0.1f + 0.7f*t, 0.1f + 0.5f*t, 0.2f, 1.0f);
            glBindFramebuffer(GL_FRAMEBUFFER, 0);
            glViewport(0, 0, fb_w, fb_h);
            gl_clear_color(0.f,0.f,0.f,1.f);
            blit_rt_to_screen(opt.rotation);
            eglSwapBuffers(e.dpy, e.surf);
            if (opt.use_atomic && opt.gl_finish) glFinish();
            page_flip(&d, &g);
        }
        fprintf(stderr, "GL test: rendered %d frames successfully.\n", frames);
        goto cleanup;
    }

    // Create terminal panes in logical space (initialized; will recompute each frame)
    int screen_w = logical_w, screen_h = logical_h;
    pane_layout lay_a = (pane_layout){0}, lay_b = (pane_layout){0};
    pane_layout lay_video = (pane_layout){ .x = 0, .y = 0, .w = logical_w, .h = logical_h };
    // Slot-based layout and permutation control: 0=video,1=paneA,2=paneB
    static int perm[3] = {0,1,2};
    static int last_perm[3] = {0,1,2};
    if (opt.roles_set) { perm[0]=opt.roles[0]; perm[1]=opt.roles[1]; perm[2]=opt.roles[2]; }
    static int last_font_px_a=-1, last_font_px_b=-1;
    static pane_layout prev_a={0}, prev_b={0};
    static int last_layout_mode=-1;
    static int last_right_frac_pct=-1;
    static int last_pane_split_pct=-1;
    static int last_fullscreen = 0;
    static int last_fs_pane = 0;
    static int layout_reinit_countdown = 0;

    bool fullscreen = false;
    int fs_pane = 0;
    bool fs_cycle = false;
    double fs_next_switch = 0.0;

    {
        int mode = opt.layout_mode; // 0=stack3,1=row3,2=2x1,3=1x2,4=2over1,5=1over2
        int split_pct = opt.pane_split_pct ? opt.pane_split_pct : 50; if (split_pct<10) split_pct=10; if (split_pct>90) split_pct=90;
        int col_pct = opt.right_frac_pct ? (100 - opt.right_frac_pct) : 50; if (col_pct<20) col_pct=20; if (col_pct>80) col_pct=80;
        pane_layout s0={0}, s1={0}, s2={0};
        if (mode == 0) {
            int h = screen_h/3; int h2 = h; int h3 = screen_h - h - h2;
            s0 = (pane_layout){ .x=0, .y=screen_h - h, .w=screen_w, .h=h };
            s1 = (pane_layout){ .x=0, .y=screen_h - h - h2, .w=screen_w, .h=h2 };
            s2 = (pane_layout){ .x=0, .y=0, .w=screen_w, .h=h3 };
        } else if (mode == 1) {
            int w = screen_w/3; int w2 = w; int w3 = screen_w - w - w2;
            s0 = (pane_layout){ .x=0, .y=0, .w=w, .h=screen_h };
            s1 = (pane_layout){ .x=w, .y=0, .w=w2, .h=screen_h };
            s2 = (pane_layout){ .x=w+w2, .y=0, .w=w3, .h=screen_h };
        } else if (mode == 2) {
            int wleft = screen_w * col_pct / 100; int wright = screen_w - wleft;
            int htop = screen_h * split_pct / 100; int hbot = screen_h - htop;
            s0 = (pane_layout){ .x=0, .y=screen_h - htop, .w=wleft, .h=htop };
            s1 = (pane_layout){ .x=0, .y=0, .w=wleft, .h=hbot };
            s2 = (pane_layout){ .x=wleft, .y=0, .w=wright, .h=screen_h };
        } else if (mode == 3) {
            int wleft = screen_w * col_pct / 100; int wright = screen_w - wleft;
            int htop = screen_h * split_pct / 100; int hbot = screen_h - htop;
            s0 = (pane_layout){ .x=0, .y=0, .w=wleft, .h=screen_h };
            s1 = (pane_layout){ .x=wleft, .y=screen_h - htop, .w=wright, .h=htop };
            s2 = (pane_layout){ .x=wleft, .y=0, .w=wright, .h=hbot };
        } else if (mode == 4) {
            int wleft = screen_w * col_pct / 100; int wright = screen_w - wleft;
            int htop = screen_h * split_pct / 100; int hbot = screen_h - htop;
            s0 = (pane_layout){ .x=0, .y=screen_h - htop, .w=wleft, .h=htop };
            s1 = (pane_layout){ .x=wleft, .y=screen_h - htop, .w=wright, .h=htop };
            s2 = (pane_layout){ .x=0, .y=0, .w=screen_w, .h=hbot };
        } else { // mode == 5
            int wleft = screen_w * col_pct / 100; int wright = screen_w - wleft;
            int htop = screen_h * split_pct / 100; int hbot = screen_h - htop;
            s0 = (pane_layout){ .x=0, .y=screen_h - htop, .w=screen_w, .h=htop };
            s1 = (pane_layout){ .x=0, .y=0, .w=wleft, .h=hbot };
            s2 = (pane_layout){ .x=wleft, .y=0, .w=wright, .h=hbot };
        }
        pane_layout slots[3] = { s0, s1, s2 };
        lay_video = slots[perm[0]];
        lay_a     = slots[perm[1]];
        lay_b     = slots[perm[2]];
        if (fullscreen) {
            pane_layout full = (pane_layout){ .x=0,.y=0,.w=screen_w,.h=screen_h };
            if (fs_pane==0) lay_video = full; else if (fs_pane==1) lay_a=full; else lay_b=full;
        }
    }

    // Enforce that pane A (default btop) is at least 80x24 characters by
    // adapting font size and minimally growing its rect if needed.
    int font_px_a = opt.font_px ? opt.font_px : 18;
    int cell_w_a=8, cell_h_a=16;
    term_measure_cell(font_px_a, &cell_w_a, &cell_h_a);
    for (int px=font_px_a; px>=10; --px) {
        int cw, ch; if (!term_measure_cell(px, &cw, &ch)) break;
        int cols_fit = lay_a.w / cw; int rows_fit = lay_a.h / ch;
        if (cols_fit >= 80 && rows_fit >= 24) { font_px_a = px; cell_w_a=cw; cell_h_a=ch; break; }
        if (px==10) { font_px_a = px; cell_w_a=cw; cell_h_a=ch; }
    }
    // Do not expand pane rects beyond computed layout slots; instead, adjust font size to fit.
    // This avoids stealing space from neighboring panes after rotation/layout changes.

    // Pane B font relative to its pane: aim for at least 60x20 if possible
    int font_px_b = opt.font_px ? opt.font_px : font_px_a;
    int cell_w_b=8, cell_h_b=16; term_measure_cell(font_px_b, &cell_w_b, &cell_h_b);
    for (int px=font_px_b; px>=10; --px) {
        int cw,ch; if (!term_measure_cell(px,&cw,&ch)) break;
        int cols_fit = lay_b.w / cw; int rows_fit = lay_b.h / ch;
        if (cols_fit >= 60 && rows_fit >= 20) { font_px_b=px; cell_w_b=cw; cell_h_b=ch; break; }
        if (px==10) { font_px_b=px; cell_w_b=cw; cell_h_b=ch; }
    }
    if (!opt.no_panes) {
        if (g_debug) fprintf(stderr, "Pane A min 80x24 -> using font_px=%d (cell=%dx%d), pane_px=%dx%d gives ~%dx%d chars\n",
                             font_px_a, cell_w_a, cell_h_a, lay_a.w, lay_a.h, lay_a.w/cell_w_a, lay_a.h/cell_h_a);
        if (opt.pane_a_cmd) tp_a = term_pane_create_cmd(&lay_a, font_px_a, opt.pane_a_cmd);
        else {
            char *argv_a[] = { "btop", NULL };
            tp_a = term_pane_create(&lay_a, font_px_a, "btop", argv_a);
        }
        if (opt.pane_b_cmd) tp_b = term_pane_create_cmd(&lay_b, font_px_b, opt.pane_b_cmd);
        else {
            char *argv_b[] = { "tail", "-f", "/var/log/syslog", NULL };
            tp_b = term_pane_create(&lay_b, font_px_b, "tail", argv_b);
        }
        last_font_px_a = font_px_a; last_font_px_b = font_px_b; prev_a = lay_a; prev_b = lay_b;
    }

    // Set TTY to raw mode for key forwarding
    struct termios rawt; if (tcgetattr(0, &g_oldt)==0) { g_have_oldt = 1; rawt = g_oldt; cfmakeraw(&rawt); tcsetattr(0, TCSANOW, &rawt); atexit(restore_tty); }
            fprintf(stderr, "Controls: Ctrl+E Control Mode; in Control Mode: Tab focus C/A/B, Arrows resize, l/L layouts, r/R rotate roles, t swap A/B, z fullscreen, c cycle FS, o OSD, ? help; Ctrl+Q quit.\n");
    int focus = use_mpv ? 0 : 1; // 0=video, 1=top pane, 2=bottom pane
    bool show_osd = false; // default OSD off
    if (getenv("KMS_MPV_NO_OSD")) show_osd = false;
    bool show_help = false; // OSD help overlay
    bool ui_control = false; // when true, keystrokes control mosaic instead of panes

    bool running = true;
    const char *direct_env_once = getenv("KMS_MPV_DIRECT");
    bool direct_mode = (direct_env_once && (*direct_env_once=='1' || *direct_env_once=='y' || *direct_env_once=='Y'));
    int mpv_flip_y_direct = 1; // default 1; can be overridden by env
    const char *flip_env = getenv("KMS_MPV_FLIPY");
    if (flip_env && (*flip_env=='0' || *flip_env=='n' || *flip_env=='N')) mpv_flip_y_direct = 0;
    bool direct_via_fbo = false; const char *dfbo_env = getenv("KMS_MPV_DIRECT_FBO");
    if (dfbo_env && (*dfbo_env=='1' || *dfbo_env=='y' || *dfbo_env=='Y')) direct_via_fbo = true;
    bool direct_test_only = false; const char *dtest_env = getenv("KMS_MPV_DIRECT_TEST");
    if (dtest_env && (*dtest_env=='1' || *dtest_env=='y' || *dtest_env=='Y')) direct_test_only = true;
    int frame = 0;
    int mpv_needs_render = 1; // request an initial render
    struct pollfd pfds[4];
    pfds[0].fd = 0; // stdin for keys
    pfds[0].events = POLLIN;
    pfds[1].fd = use_mpv ? m.wakeup_fd[0] : -1;
    pfds[1].events = POLLIN;
    pfds[2].fd = d.fd; // DRM events (atomic)
    pfds[2].events = POLLIN;
    pfds[3].fd = playlist_fifo_fd;
    pfds[3].events = POLLIN;
    int nfds = use_mpv ? 3 : 2;
    if (playlist_fifo_fd >= 0) nfds++;
    fcntl(0, F_SETFL, O_NONBLOCK);

    while (running) {
        if (g_stop) { running = false; break; }
        if (g_debug && frame < 5) fprintf(stderr, "Loop frame %d start\n", frame);
        // Small timeout to keep input responsive; rendering blocks to target time internally
        int timeout_ms = 10;
        int ret = poll(pfds, nfds, timeout_ms);
        if (ret < 0 && errno != EINTR) die("poll");

        if (pfds[0].revents & POLLIN) {
            char buf[64]; ssize_t n = read(0, buf, sizeof buf);
            if (n > 0) {
                // Toggle UI control mode (Ctrl+E)
                for (ssize_t i=0;i<n;i++) { unsigned char ch=(unsigned char)buf[i]; if (ch==0x05) { ui_control=!ui_control; }}
                // Ctrl+Q (DC1, 0x11) always quits
                for (ssize_t i=0;i<n;i++) { unsigned char ch=(unsigned char)buf[i]; if (ch==0x11) { running=false; break; } }
                if (!running) break;
                bool consumed=false;
                // Tab switches focus only in UI control mode
                if (ui_control) {
                    for (ssize_t i=0;i<n;i++) if (buf[i]=='\t') {
                        if (use_mpv) { focus = (focus+1)%3; }
                        else { focus = (focus==1?2:1); }
                        consumed=true;
                    }
                }
                // Layout/pane controls (only when in UI control mode)
                for (ssize_t i=0;i<n;i++) if (ui_control) {
                    if (buf[i]=='l') { opt.layout_mode = (opt.layout_mode+1)%6; consumed=true; }
                    else if (buf[i]=='L') { opt.layout_mode = (opt.layout_mode+5)%6; consumed=true; }
                    else if (buf[i]=='t') { int tmp = perm[1]; perm[1]=perm[2]; perm[2]=tmp; opt.roles_set=true; opt.roles[0]=perm[0]; opt.roles[1]=perm[1]; opt.roles[2]=perm[2]; consumed=true; }
                    else if (buf[i]=='r') { int p0=perm[0],p1=perm[1],p2=perm[2]; perm[0]=p1; perm[1]=p2; perm[2]=p0; opt.roles_set=true; opt.roles[0]=perm[0]; opt.roles[1]=perm[1]; opt.roles[2]=perm[2]; consumed=true; }
                    else if (buf[i]=='R') { int p0=perm[0],p1=perm[1],p2=perm[2]; perm[0]=p2; perm[1]=p0; perm[2]=p1; opt.roles_set=true; opt.roles[0]=perm[0]; opt.roles[1]=perm[1]; opt.roles[2]=perm[2]; consumed=true; }
                    else if (buf[i]=='z') { fullscreen = !fullscreen; if (fullscreen){ fs_pane=focus; fs_cycle=false; } consumed=true; }
                    else if (buf[i]=='c') { fs_cycle = !fs_cycle; if (fs_cycle){ fullscreen=true; fs_pane=focus; fs_next_switch=0.0; } else { fullscreen=false; } consumed=true; }
                    else if (buf[i]=='f') { term_pane_force_rebuild(tp_a); term_pane_force_rebuild(tp_b); consumed=true; }
                    else if (buf[i]=='?') { show_help = !show_help; consumed=true; }
                }
                // While in UI control mode, handle arrow keys for resizing splits
                if (ui_control) {
                    for (ssize_t i=0; i+2<n; i++) {
                        if ((unsigned char)buf[i] == 0x1b && buf[i+1] == '[') {
                            unsigned char k = (unsigned char)buf[i+2];
                            int step = 2; // percentage step per keypress
                            if (k=='C') { // Right: increase right column width
                                if (opt.layout_mode>=2 && opt.layout_mode<=5) {
                                    int rf = opt.right_frac_pct ? opt.right_frac_pct : 33;
                                    rf += step; if (rf > 80) rf = 80; if (rf < 20) rf = 20; opt.right_frac_pct = rf; consumed = true;
                                }
                                i += 2; continue;
                            } else if (k=='D') { // Left: decrease right column width
                                if (opt.layout_mode>=2 && opt.layout_mode<=5) {
                                    int rf = opt.right_frac_pct ? opt.right_frac_pct : 33;
                                    rf -= step; if (rf < 20) rf = 20; if (rf > 80) rf = 80; opt.right_frac_pct = rf; consumed = true;
                                }
                                i += 2; continue;
                            } else if (k=='A') { // Up: increase top pane height in split column
                                if (opt.layout_mode>=2 && opt.layout_mode<=5) {
                                    int sp = opt.pane_split_pct ? opt.pane_split_pct : 50;
                                    sp += step; if (sp > 90) sp = 90; if (sp < 10) sp = 10; opt.pane_split_pct = sp; consumed = true;
                                }
                                i += 2; continue;
                            } else if (k=='B') { // Down: decrease top pane height in split column
                                if (opt.layout_mode>=2 && opt.layout_mode<=5) {
                                    int sp = opt.pane_split_pct ? opt.pane_split_pct : 50;
                                    sp -= step; if (sp < 10) sp = 10; if (sp > 90) sp = 90; opt.pane_split_pct = sp; consumed = true;
                                }
                                i += 2; continue;
                            }
                        }
                    }
                }
                // OSD toggle only in UI control mode
                if (ui_control) { for (ssize_t i=0;i<n;i++) if (buf[i]=='o') { show_osd = !show_osd; consumed=true; } }
                if (!consumed && !ui_control) {
                    if (focus==1) term_pane_send_input(tp_a, buf, (size_t)n);
                    else if (focus==2) term_pane_send_input(tp_b, buf, (size_t)n);
                    else if (focus==0 && use_mpv) {
                        mpv_send_keys(m.mpv, buf, n);
                    }
                }
                if (g_debug) {
                    fprintf(stderr, "Input: focus=%d ui_control=%d consumed=%d bytes=%zd\n", focus, ui_control?1:0, consumed?1:0, n);
                }
            }
        }

        struct timespec ts_now; clock_gettime(CLOCK_MONOTONIC, &ts_now);
        double now_sec = ts_now.tv_sec + ts_now.tv_nsec/1e9;
        if (fs_cycle) {
            if (fs_next_switch == 0.0) fs_next_switch = now_sec + (opt.fs_cycle_sec>0?opt.fs_cycle_sec:5);
            else if (now_sec >= fs_next_switch) { fs_pane=(fs_pane+1)%3; fs_next_switch = now_sec + (opt.fs_cycle_sec>0?opt.fs_cycle_sec:5); fullscreen=true; }
        }
        if (use_mpv && (pfds[1].revents & POLLIN)) {
            uint64_t tmp;
            while (read(m.wakeup_fd[0], &tmp, sizeof(tmp)) > 0) {}
            // Drain mpv core events for visibility
            for (;;) {
                mpv_event *ev = mpv_wait_event(m.mpv, 0);
                if (!ev || ev->event_id == MPV_EVENT_NONE) break;
                if (ev->event_id == MPV_EVENT_LOG_MESSAGE) {
                    mpv_event_log_message *lm = ev->data;
                    fprintf(stderr, "mpv[%s]: %s", lm->prefix, lm->text);
                    if (mpv_out) { fprintf(mpv_out, "[%s] %s", lm->prefix, lm->text); fflush(mpv_out); }
                } else if (ev->event_id == MPV_EVENT_START_FILE) {
                    fprintf(stderr, "mpv: START_FILE\n");
                    if (mpv_out) { fprintf(mpv_out, "START_FILE\n"); fflush(mpv_out); }
                } else if (ev->event_id == MPV_EVENT_FILE_LOADED) {
                    fprintf(stderr, "mpv: FILE_LOADED\n");
                    if (mpv_out) { fprintf(mpv_out, "FILE_LOADED\n"); fflush(mpv_out); }
                    mpv_needs_render = 1;
                } else if (ev->event_id == MPV_EVENT_VIDEO_RECONFIG) {
                    fprintf(stderr, "mpv: VIDEO_RECONFIG\n");
                    if (mpv_out) { fprintf(mpv_out, "VIDEO_RECONFIG\n"); fflush(mpv_out); }
                    mpv_needs_render = 1;
                } else if (ev->event_id == MPV_EVENT_END_FILE) {
                    fprintf(stderr, "mpv: END_FILE\n");
                    if (mpv_out) { fprintf(mpv_out, "END_FILE\n"); fflush(mpv_out); }
                }
            }
            int flags = mpv_render_context_update(m.mpv_gl);
            if (flags & MPV_RENDER_UPDATE_FRAME) {
                mpv_needs_render = 1;
                if (g_debug) fprintf(stderr, "mpv: UPDATE_FRAME\n");
            }
        }

        if (playlist_fifo_fd >= 0 && (pfds[3].revents & POLLIN)) {
            ssize_t r = read(playlist_fifo_fd, pfifo_buf + pfifo_len, sizeof(pfifo_buf) - pfifo_len - 1);
            if (r > 0) {
                pfifo_len += (int)r;
                pfifo_buf[pfifo_len] = '\0';
                char *start = pfifo_buf;
                char *nl;
                while ((nl = strchr(start, '\n')) != NULL) {
                    *nl = '\0';
                    mpv_append_line(m.mpv, start);
                    start = nl + 1;
                }
                pfifo_len = (int)(pfifo_buf + pfifo_len - start);
                memmove(pfifo_buf, start, pfifo_len);
            } else if (r == 0) {
                close(playlist_fifo_fd);
                playlist_fifo_fd = open(opt.playlist_fifo, O_RDONLY | O_NONBLOCK);
                pfds[3].fd = playlist_fifo_fd;
            }
        }

        // Handle DRM events (atomic page flip completion)
        if (pfds[2].revents & POLLIN) {
            drmEventContext ev = {0};
            // Use version 2 for broad compat; page_flip_handler signature matches
            ev.version = 2;
            ev.page_flip_handler = on_page_flip;
            drmHandleEvent(d.fd, &ev);
        }

        // Recompute layout based on current rotation/layout/perm
        {
            int layout_changed = 0;
            if (last_layout_mode != opt.layout_mode) { layout_changed = 1; last_layout_mode = opt.layout_mode; }
            if (last_right_frac_pct != opt.right_frac_pct) { layout_changed = 1; last_right_frac_pct = opt.right_frac_pct; }
            if (last_pane_split_pct != opt.pane_split_pct) { layout_changed = 1; last_pane_split_pct = opt.pane_split_pct; }
            if (last_perm[0]!=perm[0] || last_perm[1]!=perm[1] || last_perm[2]!=perm[2]) { layout_changed = 1; last_perm[0]=perm[0]; last_perm[1]=perm[1]; last_perm[2]=perm[2]; }
            if (last_fullscreen != (fullscreen?1:0) || last_fs_pane != fs_pane) { layout_changed=1; last_fullscreen = fullscreen?1:0; last_fs_pane = fs_pane; }
            {
                int mode = opt.layout_mode; // 0=stack3,1=row3,2=2x1,3=1x2,4=2over1,5=1over2
                int split_pct = opt.pane_split_pct ? opt.pane_split_pct : 50; if (split_pct<10) split_pct=10; if (split_pct>90) split_pct=90;
                int col_pct = opt.right_frac_pct ? (100 - opt.right_frac_pct) : 50; if (col_pct<20) col_pct=20; if (col_pct>80) col_pct=80;
                pane_layout s0={0}, s1={0}, s2={0};
                if (mode == 0) {
                    // 3 rows stack
                    int h = screen_h/3; int h2=h; int h3=screen_h-h-h2;
                    s0=(pane_layout){.x=0,.y=screen_h-h,.w=screen_w,.h=h};
                    s1=(pane_layout){.x=0,.y=screen_h-h-h2,.w=screen_w,.h=h2};
                    s2=(pane_layout){.x=0,.y=0,.w=screen_w,.h=h3};
                } else if (mode == 1) {
                    // 3 columns row
                    int w=screen_w/3; int w2=w; int w3=screen_w-w-w2;
                    s0=(pane_layout){.x=0,.y=0,.w=w,.h=screen_h};
                    s1=(pane_layout){.x=w,.y=0,.w=w2,.h=screen_h};
                    s2=(pane_layout){.x=w+w2,.y=0,.w=w3,.h=screen_h};
                } else if (mode == 2) {
                    // Left column split rows, right full
                    int wleft=screen_w*col_pct/100; int wright=screen_w-wleft; int htop=screen_h*split_pct/100; int hbot=screen_h-htop;
                    s0=(pane_layout){.x=0,.y=screen_h-htop,.w=wleft,.h=htop};
                    s1=(pane_layout){.x=0,.y=0,.w=wleft,.h=hbot};
                    s2=(pane_layout){.x=wleft,.y=0,.w=wright,.h=screen_h};
                } else if (mode == 3) {
                    // Left full, right column split rows
                    int wleft=screen_w*col_pct/100; int wright=screen_w-wleft; int htop=screen_h*split_pct/100; int hbot=screen_h-htop;
                    s0=(pane_layout){.x=0,.y=0,.w=wleft,.h=screen_h};
                    s1=(pane_layout){.x=wleft,.y=screen_h-htop,.w=wright,.h=htop};
                    s2=(pane_layout){.x=wleft,.y=0,.w=wright,.h=hbot};
                } else if (mode == 4) {
                    // Top row split columns, bottom full
                    int wleft=screen_w*col_pct/100; int wright=screen_w-wleft; int htop=screen_h*split_pct/100; int hbot=screen_h-htop;
                    s0=(pane_layout){.x=0,.y=screen_h-htop,.w=wleft,.h=htop};
                    s1=(pane_layout){.x=wleft,.y=screen_h-htop,.w=wright,.h=htop};
                    s2=(pane_layout){.x=0,.y=0,.w=screen_w,.h=hbot};
                } else {
                    // Top full, bottom row split columns
                    int wleft=screen_w*col_pct/100; int wright=screen_w-wleft; int htop=screen_h*split_pct/100; int hbot=screen_h-htop;
                    s0=(pane_layout){.x=0,.y=screen_h-htop,.w=screen_w,.h=htop};
                    s1=(pane_layout){.x=0,.y=0,.w=wleft,.h=hbot};
                    s2=(pane_layout){.x=wleft,.y=0,.w=wright,.h=hbot};
                }
                pane_layout slots[3] = { s0, s1, s2 };
                lay_video = slots[perm[0]]; lay_a = slots[perm[1]]; lay_b = slots[perm[2]];
                if (fullscreen) {
                    pane_layout full = (pane_layout){ .x=0,.y=0,.w=screen_w,.h=screen_h };
                    if (fs_pane==0) lay_video=full; else if (fs_pane==1) lay_a=full; else lay_b=full;
                }
            }
            if (layout_changed) {
                // Force a few frames of reinit to mimic fresh start in this layout
                int default_frames = 3;
                const char *rf = getenv("KMS_MOSAIC_REINIT_FRAMES");
                if (rf) { int v = atoi(rf); if (v>=0 && v<=30) default_frames = v; }
                layout_reinit_countdown = default_frames;
                if (g_debug) fprintf(stderr, "Layout changed -> reinit countdown %d (mode=%d, perm=%d/%d/%d, rot=%d)\n",
                                     layout_reinit_countdown, opt.layout_mode, perm[0],perm[1],perm[2], (int)opt.rotation);
            }
        }

        // Recompute font sizes for panes based on current rects
        int font_px_a = opt.font_px ? opt.font_px : 18; int cell_w_a=8, cell_h_a=16; term_measure_cell(font_px_a,&cell_w_a,&cell_h_a);
        for (int px=font_px_a; px>=10; --px){ int cw,ch; if(!term_measure_cell(px,&cw,&ch))break; int cols=lay_a.w/cw; int rows=lay_a.h/ch; if(cols>=80 && rows>=24){ font_px_a=px; cell_w_a=cw; cell_h_a=ch; break;} if(px==10){ font_px_a=px; cell_w_a=cw; cell_h_a=ch; }}
        // Do not expand pane rects beyond computed layout slots; adjust only font size to fit.
        int font_px_b = opt.font_px ? opt.font_px : font_px_a; int cell_w_b=8, cell_h_b=16; term_measure_cell(font_px_b,&cell_w_b,&cell_h_b);
        for (int px=font_px_b; px>=10; --px){ int cw,ch; if(!term_measure_cell(px,&cw,&ch))break; int cols=lay_b.w/cw; int rows=lay_b.h/ch; if(cols>=60 && rows>=20){ font_px_b=px; cell_w_b=cw; cell_h_b=ch; break;} if(px==10){ font_px_b=px; cell_w_b=cw; cell_h_b=ch; }}

        // Render frame into offscreen logical FBO (unless in direct debug mode)
        if (!eglMakeCurrent(e.dpy, e.surf, e.surf, e.ctx)) die("eglMakeCurrent loop");

        // In direct test mode (or direct mode without mpv), show solid red so we know default FB is visible
        if (direct_mode && (direct_test_only || !use_mpv)) {
            glBindFramebuffer(GL_FRAMEBUFFER, 0);
            glDisable(GL_SCISSOR_TEST);
            glDisable(GL_BLEND);
            glDisable(GL_DITHER);
            glDisable(GL_CULL_FACE);
            glDisable(GL_DEPTH_TEST);
            glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE);
            glViewport(0, 0, fb_w, fb_h);
            gl_clear_color(1.0f, 0.0f, 0.0f, 1.0f);
            if (g_debug) {
                GLint vp[4] = {0}; glGetIntegerv(GL_VIEWPORT, vp);
                GLint cur_fbo = 0; glGetIntegerv(GL_FRAMEBUFFER_BINDING, &cur_fbo);
                fprintf(stderr, "Direct TEST/Baseline: viewport=%d,%d %dx%d fbo=%d\n", vp[0],vp[1],vp[2],vp[3], cur_fbo);
            }
            eglSwapBuffers(e.dpy, e.surf);
            gl_check("after eglSwapBuffers (direct test/baseline)");
            page_flip(&d, &g);
            frame++;
            continue;
        }

        glBindFramebuffer(GL_FRAMEBUFFER, rt_fbo);
        glViewport(0, 0, logical_w, logical_h);
        gl_clear_color(0.0f, 0.0f, 0.0f, 1.0f);

        // Draw mpv if enabled into its own FBO sized to the video region (left area)
        if (use_mpv && mpv_needs_render && (!fullscreen || fs_pane==0)) {
            int vw = lay_video.w;
            int vh = lay_video.h;
            if (vw < 1) vw = 1;
            if (vh < 1) vh = 1;
            if (direct_mode) {
                if (g_debug) fprintf(stderr, "Render: mpv direct to default FB...\n");
                if (!direct_via_fbo) {
                    glBindFramebuffer(GL_FRAMEBUFFER, 0);
                    // Ensure a clean, simple GL state baseline for mpv
                    glDisable(GL_SCISSOR_TEST);
                    glDisable(GL_BLEND);
                    glDisable(GL_DITHER);
                    glDisable(GL_CULL_FACE);
                    glDisable(GL_DEPTH_TEST);
                    glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE);
                    glViewport(0, 0, fb_w, fb_h);
                    // Clear to red to make default FB visibility obvious
                    gl_clear_color(1.0f, 0.0f, 0.0f, 1.0f);
                    if (g_debug) {
                        GLint vp[4] = {0}; glGetIntegerv(GL_VIEWPORT, vp);
                        GLint cur_fbo = 0; glGetIntegerv(GL_FRAMEBUFFER_BINDING, &cur_fbo);
                        fprintf(stderr, "Direct: viewport=%d,%d %dx%d fbo=%d\n", vp[0],vp[1],vp[2],vp[3], cur_fbo);
                    }
                    if (!direct_test_only) {
                    int flip_y = mpv_flip_y_direct;
                    mpv_opengl_fbo dfbo = {.fbo = 0, .w = fb_w, .h = fb_h, .internal_format = 0};
                    int block = 1;
                    mpv_render_param r_params2[] = {
                        {MPV_RENDER_PARAM_OPENGL_FBO, &dfbo},
                        {MPV_RENDER_PARAM_FLIP_Y, &flip_y},
                        {MPV_RENDER_PARAM_BLOCK_FOR_TARGET_TIME, &block},
                        {0}
                    };
                        if (g_debug) fprintf(stderr, "Render: calling mpv_render_context_render (direct)...\n");
                        mpv_render_context_render(m.mpv_gl, r_params2);
                        if (opt.use_atomic && opt.gl_finish) glFinish();
                        gl_check("after mpv_render_context_render (direct)");
                        mpv_needs_render = 0;
                    } else if (g_debug) {
                        fprintf(stderr, "Direct TEST: skipped mpv render (expect solid red)\n");
                    }
                } else {
                    // Render mpv into a texture FBO at full screen, then draw that texture to default FB
                    ensure_video_rt(fb_w, fb_h);
                    glBindFramebuffer(GL_FRAMEBUFFER, vid_fbo);
                    glDisable(GL_SCISSOR_TEST);
                    glDisable(GL_BLEND);
                    glDisable(GL_DITHER);
                    glDisable(GL_CULL_FACE);
                    glDisable(GL_DEPTH_TEST);
                    glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE);
                    glViewport(0, 0, fb_w, fb_h);
                    gl_clear_color(0.f, 0.f, 0.f, 1.0f);
                    if (!direct_test_only) {
                        int flip_y = 1;
                        mpv_opengl_fbo fbo = {.fbo = (int)vid_fbo, .w = fb_w, .h = fb_h, .internal_format = 0};
                        int block2 = 1;
                        mpv_render_param r_params[] = {
                            {MPV_RENDER_PARAM_OPENGL_FBO, &fbo},
                            {MPV_RENDER_PARAM_FLIP_Y, &flip_y},
                            {MPV_RENDER_PARAM_BLOCK_FOR_TARGET_TIME, &block2},
                            {0}
                        };
                        if (g_debug) fprintf(stderr, "Render: calling mpv_render_context_render (direct via FBO)...\n");
                        mpv_render_context_render(m.mpv_gl, r_params);
                        if (opt.use_atomic && opt.gl_finish) glFinish();
                        gl_check("after mpv_render_context_render (direct via FBO)");
                        mpv_needs_render = 0;
                    } else if (g_debug) {
                        fprintf(stderr, "Direct TEST: skipped mpv render into FBO\n");
                    }
                    // Now draw texture to the default FB
                    glBindFramebuffer(GL_FRAMEBUFFER, 0);
                    glViewport(0, 0, fb_w, fb_h);
                    // Clear to red to make default FB visibility obvious (direct via FBO)
                    gl_clear_color(1.0f, 0.0f, 0.0f, 1.0f);
                    if (!direct_test_only) draw_tex_fullscreen(vid_tex);
                    else if (g_debug) fprintf(stderr, "Direct TEST: drew red only (no texture blit)\n");
                }
            } else {
                if (g_debug) fprintf(stderr, "Render: preparing mpv FBO...\n");
                ensure_video_rt(vw, vh);
                glBindFramebuffer(GL_FRAMEBUFFER, vid_fbo);
                // Ensure a clean, simple GL state baseline for mpv
                glDisable(GL_SCISSOR_TEST);
                glDisable(GL_BLEND);
                glDisable(GL_DITHER);
                glDisable(GL_CULL_FACE);
                glDisable(GL_DEPTH_TEST);
                glViewport(0, 0, vw, vh);
                gl_clear_color(0.0f, 0.0f, 0.0f, 1.0f);
                int flip_y = 1;
                mpv_opengl_fbo fbo = {.fbo = (int)vid_fbo, .w = vw, .h = vh, .internal_format = 0};
                mpv_render_param r_params[] = {
                    {MPV_RENDER_PARAM_OPENGL_FBO, &fbo},
                    {MPV_RENDER_PARAM_FLIP_Y, &flip_y},
                    {0}
                };
                if (g_debug) fprintf(stderr, "Render: calling mpv_render_context_render...\n");
                mpv_render_context_render(m.mpv_gl, r_params);
                gl_check("after mpv_render_context_render");
                mpv_needs_render = 0;

                // Composite into logical RT: draw video into its layout rect
                glBindFramebuffer(GL_FRAMEBUFFER, rt_fbo);
                gl_reset_state_2d();
                glViewport(0, 0, logical_w, logical_h);
                draw_tex_to_rt(vid_tex, lay_video.x, lay_video.y, vw, vh, logical_w, logical_h);
            }
        } else {
            glBindFramebuffer(GL_FRAMEBUFFER, rt_fbo);
            gl_reset_state_2d();
            glViewport(0, 0, logical_w, logical_h);
        }
        
        // Draw terminal panes into rt
        // Before rendering, if layout changed, resize panes and adjust font sizes
        if (!direct_mode && !opt.no_panes) {
            if (g_debug) {
                // Tint pane backgrounds to visualize draw rects
                glBindFramebuffer(GL_FRAMEBUFFER, rt_fbo);
                gl_reset_state_2d();
                glEnable(GL_SCISSOR_TEST);
                // Pane A: bluish tint
                glScissor(lay_a.x, logical_h - (lay_a.y + lay_a.h), lay_a.w, lay_a.h);
                glClearColor(0.05f, 0.10f, 0.20f, 1.0f);
                glClear(GL_COLOR_BUFFER_BIT);
                // Pane B: greenish tint
                glScissor(lay_b.x, logical_h - (lay_b.y + lay_b.h), lay_b.w, lay_b.h);
                glClearColor(0.05f, 0.20f, 0.10f, 1.0f);
                glClear(GL_COLOR_BUFFER_BIT);
                glDisable(GL_SCISSOR_TEST);
            }
            // Adjust panes and fonts; if forcing reinit, reset and rebuild surfaces like fresh start
            if (last_font_px_a != font_px_a) { term_pane_set_font_px(tp_a, font_px_a); last_font_px_a = font_px_a; }
            if (prev_a.x!=lay_a.x || prev_a.y!=lay_a.y || prev_a.w!=lay_a.w || prev_a.h!=lay_a.h) { term_pane_resize(tp_a, &lay_a); prev_a=lay_a; }
            if (last_font_px_b != font_px_b) { term_pane_set_font_px(tp_b, font_px_b); last_font_px_b = font_px_b; }
            if (prev_b.x!=lay_b.x || prev_b.y!=lay_b.y || prev_b.w!=lay_b.w || prev_b.h!=lay_b.h) { term_pane_resize(tp_b, &lay_b); prev_b=lay_b; }
            if (layout_reinit_countdown > 0) {
                // Do NOT reset the vterm screens; that clears content.
                // Instead, poll aggressively for a few frames to let apps redraw after SIGWINCH.
                (void)term_pane_poll(tp_a);
                (void)term_pane_poll(tp_b);
                layout_reinit_countdown--;
            }
            // Poll PTYs and update textures only if changed
            (void)term_pane_poll(tp_a);
            (void)term_pane_poll(tp_b);
            if (!fullscreen || fs_pane==1) {
                term_pane_render(tp_a, screen_w, screen_h);
                if (g_debug) fprintf(stderr, "Pane A draw at %d,%d %dx%d\n", lay_a.x, lay_a.y, lay_a.w, lay_a.h);
                gl_check("after term_pane_render A");
            }
            if (!fullscreen || fs_pane==2) {
                term_pane_render(tp_b, screen_w, screen_h);
                if (g_debug) fprintf(stderr, "Pane B draw at %d,%d %dx%d\n", lay_b.x, lay_b.y, lay_b.w, lay_b.h);
                gl_check("after term_pane_render B");
            }
        }

        // OSD overlay (title, index/total, paused) and help
        if (!direct_mode && use_mpv && !opt.no_osd && (show_osd || show_help)) {
            static osd_ctx *osd = NULL; if (!osd) osd = osd_create(opt.font_px?opt.font_px:20);
            if (show_help) {
                const char *help =
                    "Control Mode\n"
                    "  Tab: focus cycle C/A/B\n"
                    "  o: toggle OSD\n"
                    "  l/L: cycle layouts\n"
                    "  r/R: rotate roles C/A/B\n"
                    "  t: swap panes A/B\n"
                    "  z: fullscreen focused pane\n"
                    "  c: cycle fullscreen panes\n"
                    "  Arrows: resize splits (2x1/1x2/2over1/1over2)\n"
                    "  f: force pane rebuild\n"
                    "Always: Ctrl+Q quit\n";
                osd_set_text(osd, help);
            } else {
                int64_t pos=0,count=0; int paused_flag=0; char *title=NULL;
                mpv_get_property(m.mpv, "playlist-pos", MPV_FORMAT_INT64, &pos);
                mpv_get_property(m.mpv, "playlist-count", MPV_FORMAT_INT64, &count);
                mpv_get_property(m.mpv, "pause", MPV_FORMAT_FLAG, &paused_flag);
                title = mpv_get_property_string(m.mpv, "media-title");
                const char *layout_name = (opt.layout_mode==0?"stack": opt.layout_mode==1?"row": opt.layout_mode==2?"2x1": opt.layout_mode==3?"1x2": opt.layout_mode==4?"2over1":"1over2");
                char line[512]; snprintf(line,sizeof line, "%s %lld/%lld - %s  |  layout: %s",
                                          paused_flag?"Paused":"Playing",
                                          (long long)(pos+1), (long long)count,
                                          title?title:"(no title)", layout_name);
                if (title) mpv_free(title);
                osd_set_text(osd, line);
            }
            // Draw into logical RT at 16px margin
            glBindFramebuffer(GL_FRAMEBUFFER, rt_fbo);
            gl_reset_state_2d();
            glViewport(0,0, logical_w, logical_h);
            osd_draw(osd, 16, 16, logical_w, logical_h);
        }
        // Control-mode indicator OSD (always visible when active)
        if (!direct_mode && ui_control) {
            static osd_ctx *osdcm = NULL; if (!osdcm) osdcm = osd_create(opt.font_px?opt.font_px:20);
            osd_set_text(osdcm, "Control Mode (Ctrl+E)  Tab focus  Arrows resize  l/L layouts  r/R rotate  t swap  z fullscreen  c cycle  o OSD  ? help");
            glBindFramebuffer(GL_FRAMEBUFFER, rt_fbo);
            gl_reset_state_2d();
            glViewport(0,0, logical_w, logical_h);
            osd_draw(osdcm, 16, 48, logical_w, logical_h);
            // Highlight focused pane with a border
            int bx=0, by=0, bw=0, bh=0; int thickness = 4;
            if (focus == 0) { bx = lay_video.x; by = lay_video.y; bw = lay_video.w; bh = lay_video.h; }
            else if (focus == 1) { bx = lay_a.x; by = lay_a.y; bw = lay_a.w; bh = lay_a.h; }
            else { bx = lay_b.x; by = lay_b.y; bw = lay_b.w; bh = lay_b.h; }
            // Draw a bright cyan border
            draw_border_rect(bx, by, bw, bh, thickness, logical_w, logical_h, 0.1f, 0.9f, 0.95f, 1.0f);
        }

        // Present
        glBindFramebuffer(GL_FRAMEBUFFER, 0);
        if (!direct_mode) {
            glViewport(0, 0, fb_w, fb_h);
            gl_clear_color(0.f,0.f,0.f,1.f);
            blit_rt_to_screen(opt.rotation);
        }

        eglSwapBuffers(e.dpy, e.surf);
        if (opt.use_atomic && opt.gl_finish) glFinish();
        gl_check("after eglSwapBuffers");
        page_flip(&d, &g);
        if (use_mpv && m.mpv_gl) {
            // Inform mpv that we swapped (advanced control)
            mpv_render_context_report_swap(m.mpv_gl);
        }
        // Ask for next render; mpv will block to target time internally
        if (use_mpv) mpv_needs_render = 1;
        frame++;
    }

cleanup:
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
    if (mpv_out) fclose(mpv_out);
    if (playlist_fifo_fd >= 0) close(playlist_fifo_fd);
    if (opt.save_config_file) save_config(&opt, opt.save_config_file);
    else if (opt.save_config_default) save_config(&opt, default_config_path());
    if (d.conn) drmModeFreeConnector(d.conn);
    if (d.res) drmModeFreeResources(d.res);
    if (d.fd >= 0) close(d.fd);
    return 0;
}
#include <stdarg.h>
// Forward decl for GL error checker used before its definition
static void gl_check(const char *stage);
