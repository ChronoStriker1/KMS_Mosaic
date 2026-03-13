#define _GNU_SOURCE

#include "display.h"

#include <errno.h>
#include <fcntl.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <drm_fourcc.h>
#include <GLES2/gl2.h>

static void display_die(const char *msg) {
    perror(msg);
    exit(1);
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

static void advise_dri_drivers(void) {
    fprintf(stderr,
        "DRM device opened, but GBM/EGL failed to create a window surface.\n"
        "Likely missing Mesa GBM/EGL or DRI driver files for your GPU.\n"
        "Check these locations for DRI drivers (should contain e.g. iris_dri.so/radeonsi_dri.so):\n"
        "  - /usr/lib64/dri\n"
        "  - /usr/lib/x86_64-linux-gnu/dri\n"
        "On Unraid, install the GPU plugin or Mesa packages providing DRI.\n");
}

static bool mode_matches(const drmModeModeInfo *m, int w, int h, int hz) {
    if (w && m->hdisplay != (uint32_t)w) return false;
    if (h && m->vdisplay != (uint32_t)h) return false;
    if (hz) {
        int calc_hz = (int)((m->clock * 1000LL) / (m->htotal * m->vtotal));
        if (calc_hz < hz - 1 || calc_hz > hz + 1) return false;
    }
    return true;
}

static uint32_t get_prop_id(int fd, uint32_t obj_id, uint32_t obj_type, const char *name) {
    drmModeObjectProperties *props = drmModeObjectGetProperties(fd, obj_id, obj_type);
    if (!props) return 0;
    uint32_t id = 0;
    for (uint32_t i = 0; i < props->count_props; ++i) {
        drmModePropertyRes *pr = drmModeGetProperty(fd, props->props[i]);
        if (pr) {
            if (strcmp(pr->name, name) == 0) {
                id = pr->prop_id;
                drmModeFreeProperty(pr);
                break;
            }
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
    for (uint32_t i = 0; i < props->count_props; ++i) {
        drmModePropertyRes *pr = drmModeGetProperty(fd, props->props[i]);
        if (!pr) continue;
        if (strcmp(pr->name, "type") == 0 && (pr->flags & DRM_MODE_PROP_ENUM)) {
            for (int j = 0; j < pr->count_enums; ++j) {
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

static void try_init_atomic(drm_ctx *d, bool debug) {
    memset(&d->atomic, 0, sizeof(d->atomic));
    if (drmSetClientCap(d->fd, DRM_CLIENT_CAP_UNIVERSAL_PLANES, 1) != 0) return;
    if (drmSetClientCap(d->fd, DRM_CLIENT_CAP_ATOMIC, 1) != 0) return;
    int crtc_index = -1;
    for (int i = 0; i < d->res->count_crtcs; ++i) {
        if (d->res->crtcs[i] == d->crtc_id) { crtc_index = i; break; }
    }
    if (crtc_index < 0) return;
    drmModePlaneRes *pres = drmModeGetPlaneResources(d->fd);
    if (!pres) return;
    uint32_t chosen_plane = 0;
    for (uint32_t i = 0; i < pres->count_planes; ++i) {
        drmModePlane *pl = drmModeGetPlane(d->fd, pres->planes[i]);
        if (!pl) continue;
        if ((pl->possible_crtcs & (1u << crtc_index)) && plane_is_primary(d->fd, pl->plane_id)) {
            chosen_plane = pl->plane_id;
            drmModeFreePlane(pl);
            break;
        }
        drmModeFreePlane(pl);
    }
    drmModeFreePlaneResources(pres);
    if (!chosen_plane) return;
    uint32_t mode_id = get_prop_id(d->fd, d->crtc_id, DRM_MODE_OBJECT_CRTC, "MODE_ID");
    uint32_t active = get_prop_id(d->fd, d->crtc_id, DRM_MODE_OBJECT_CRTC, "ACTIVE");
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
    if (!mode_id || !active || !conn_crtc || !fb_id || !crtc_id || !src_x || !src_y || !src_w || !src_h || !crtc_x || !crtc_y || !crtc_w || !crtc_h) return;
    d->atomic.enabled = 1;
    d->atomic.plane_id = chosen_plane;
    d->atomic.crtc_props.mode_id = mode_id;
    d->atomic.crtc_props.active = active;
    d->atomic.crtc_props.out_fence_ptr = out_fence_ptr;
    d->atomic.conn_props.crtc_id = conn_crtc;
    d->atomic.plane_props.fb_id = fb_id;
    d->atomic.plane_props.crtc_id = crtc_id;
    d->atomic.plane_props.src_x = src_x;
    d->atomic.plane_props.src_y = src_y;
    d->atomic.plane_props.src_w = src_w;
    d->atomic.plane_props.src_h = src_h;
    d->atomic.plane_props.crtc_x = crtc_x;
    d->atomic.plane_props.crtc_y = crtc_y;
    d->atomic.plane_props.crtc_w = crtc_w;
    d->atomic.plane_props.crtc_h = crtc_h;
    d->atomic.plane_props.in_fence_fd = in_fence_fd;
    if (debug) {
        fprintf(stderr, "Atomic props: CRTC MODE_ID=%u ACTIVE=%u OUT_FENCE_PTR=%u\n",
                d->atomic.crtc_props.mode_id, d->atomic.crtc_props.active, d->atomic.crtc_props.out_fence_ptr);
    }
}

static bool str_is_digits(const char *s) {
    if (!s || !*s) return false;
    for (const char *p = s; *p; ++p) if (*p < '0' || *p > '9') return false;
    return true;
}

static EGLConfig find_config_for_format(EGLDisplay dpy, EGLint renderable_type, EGLBoolean want_alpha, uint32_t fourcc) {
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
    EGLConfig *cfgs = calloc((size_t)num, sizeof(*cfgs));
    if (!cfgs) return NULL;
    eglChooseConfig(dpy, attrs, cfgs, num, &num);
    EGLConfig match = NULL;
    for (EGLint i = 0; i < num; i++) {
        EGLint id = 0;
        eglGetConfigAttrib(dpy, cfgs[i], EGL_NATIVE_VISUAL_ID, &id);
        if ((uint32_t)id == fourcc) { match = cfgs[i]; break; }
    }
    if (!match && num > 0) match = cfgs[0];
    free(cfgs);
    return match;
}

static const char *egl_err_str(EGLint ecode) {
    switch (ecode) {
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

static uint32_t drm_fb_for_bo(int drm_fd, struct gbm_bo *bo) {
    uint32_t fb_id = 0;
    uint32_t width = gbm_bo_get_width(bo);
    uint32_t height = gbm_bo_get_height(bo);
    uint32_t stride = gbm_bo_get_stride(bo);
    uint32_t handle = gbm_bo_get_handle(bo).u32;
    uint32_t format = gbm_bo_get_format(bo);
    uint32_t handles[4] = {handle, 0, 0, 0};
    uint32_t strides[4] = {stride, 0, 0, 0};
    uint32_t offsets[4] = {0, 0, 0, 0};
#ifdef DRM_FORMAT_MOD_INVALID
    uint64_t modifier = gbm_bo_get_modifier(bo);
    if (modifier != DRM_FORMAT_MOD_INVALID) {
        struct drm_mode_fb_cmd2 cmd = {0};
        cmd.width = width; cmd.height = height; cmd.pixel_format = format ? format : DRM_FORMAT_XRGB8888;
        cmd.handles[0] = handles[0]; cmd.pitches[0] = strides[0]; cmd.offsets[0] = offsets[0]; cmd.modifier[0] = modifier;
        cmd.flags = DRM_MODE_FB_MODIFIERS;
        if (drmIoctl(drm_fd, DRM_IOCTL_MODE_ADDFB2, &cmd) == 0) return cmd.fb_id;
    }
#endif
    if (drmModeAddFB2(drm_fd, width, height, format ? format : DRM_FORMAT_XRGB8888, handles, strides, offsets, &fb_id, 0) == 0) return fb_id;
    if (drmModeAddFB(drm_fd, width, height, 24, 32, stride, handle, &fb_id) != 0) display_die("drmModeAddFB");
    return fb_id;
}

int display_open_drm_card(void) {
    const char *candidates[] = {"/dev/dri/card0", "/dev/dri/card1", "/dev/dri/card2"};
    for (size_t i = 0; i < sizeof(candidates) / sizeof(candidates[0]); i++) {
        int fd = open(candidates[i], O_RDWR | O_CLOEXEC);
        if (fd >= 0) return fd;
    }
    advise_no_drm();
    errno = ENODEV;
    display_die("open_drm_card");
    return -1;
}

void display_warn_if_missing_dri(void) {
    const char *paths[] = {"/usr/lib64/dri", "/usr/lib/x86_64-linux-gnu/dri", "/usr/lib/aarch64-linux-gnu/dri", NULL};
    int found = 0;
    for (int i = 0; paths[i]; ++i) if (access(paths[i], R_OK) == 0) { found = 1; break; }
    if (!found) fprintf(stderr, "Warning: No standard DRI driver directories found.\nEGL/GBM may fail to create a surface. Ensure Mesa DRI drivers are installed.\n");
}

void display_preflight_expect_dri_driver(void) {
    unsigned vendor = 0;
    FILE *vf = fopen("/sys/class/drm/card0/device/vendor", "r");
    if (vf) { if (fscanf(vf, "%x", &vendor) != 1) vendor = 0; fclose(vf); }
    const char *vendor_name = "unknown", *expect_primary = NULL, *expect_alt = NULL;
    switch (vendor) {
        case 0x8086: vendor_name = "Intel"; expect_primary = "iris_dri.so"; expect_alt = "i965_dri.so"; break;
        case 0x1002: vendor_name = "AMD"; expect_primary = "radeonsi_dri.so"; expect_alt = "r600_dri.so"; break;
        case 0x10de: vendor_name = "NVIDIA"; expect_primary = "nouveau_dri.so"; break;
        default: vendor_name = "Unknown"; expect_primary = ""; break;
    }
    const char *paths[] = {"/usr/lib64/dri", "/usr/lib/x86_64-linux-gnu/dri", "/usr/lib/aarch64-linux-gnu/dri", NULL};
    int found = 0;
    for (int i = 0; paths[i]; ++i) {
        char buf1[512], buf2[512];
        if (expect_primary && *expect_primary) { snprintf(buf1, sizeof(buf1), "%s/%s", paths[i], expect_primary); if (access(buf1, R_OK) == 0) { found = 1; break; } }
        if (expect_alt && *expect_alt) { snprintf(buf2, sizeof(buf2), "%s/%s", paths[i], expect_alt); if (access(buf2, R_OK) == 0) { found = 1; break; } }
    }
    if (!found) {
        fprintf(stderr, "Preflight: Detected GPU vendor: %s (0x%04x).\n", vendor_name, vendor);
        setenv("MESA_LOADER_DRIVER_OVERRIDE", "kms_swrast", 1);
        setenv("LIBGL_ALWAYS_SOFTWARE", "1", 1);
        fprintf(stderr, "Attempting software rasterizer fallback (MESA_LOADER_DRIVER_OVERRIDE=kms_swrast).\n");
    }
}

void display_preflight_expect_dri_driver_diag(void) {
    unsigned vendor = 0;
    FILE *vf = fopen("/sys/class/drm/card0/device/vendor", "r");
    if (vf) { if (fscanf(vf, "%x", &vendor) != 1) vendor = 0; fclose(vf); }
    fprintf(stderr, "Diag: GPU vendor: 0x%04x\n", vendor);
}

const char *display_conn_type_str(uint32_t type) {
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

void display_pick_connector_mode(drm_ctx *d, const options_t *opt, bool debug) {
    d->res = drmModeGetResources(d->fd);
    if (!d->res) display_die("drmModeGetResources");
    drmModeConnector *best_conn = NULL;
    drmModeModeInfo best_mode = {0};
    for (int i = 0; i < d->res->count_connectors; i++) {
        drmModeConnector *conn = drmModeGetConnector(d->fd, d->res->connectors[i]);
        if (!conn) continue;
        if (conn->connection != DRM_MODE_CONNECTED || conn->count_modes == 0) { drmModeFreeConnector(conn); continue; }
        bool chosen = false;
        if (opt->connector_opt) {
            if (str_is_digits(opt->connector_opt)) chosen = conn->connector_id == (uint32_t)atoi(opt->connector_opt);
            else {
                char namebuf[32];
                snprintf(namebuf, sizeof(namebuf), "%s-%u", display_conn_type_str(conn->connector_type), conn->connector_type_id);
                chosen = strcmp(namebuf, opt->connector_opt) == 0;
            }
        } else chosen = true;
        if (!chosen) { drmModeFreeConnector(conn); continue; }
        drmModeModeInfo chosen_mode = conn->modes[0];
        if (opt->mode_w || opt->mode_h || opt->mode_hz) {
            bool found = false;
            for (int mi = 0; mi < conn->count_modes; mi++) if (mode_matches(&conn->modes[mi], opt->mode_w, opt->mode_h, opt->mode_hz)) { chosen_mode = conn->modes[mi]; found = true; break; }
            if (!found) { drmModeFreeConnector(conn); continue; }
        }
        best_conn = conn; best_mode = chosen_mode; break;
    }
    if (!best_conn) display_die("no suitable connector/mode");
    d->conn = best_conn;
    d->conn_id = best_conn->connector_id;
    drmModeEncoder *enc = best_conn->encoder_id ? drmModeGetEncoder(d->fd, best_conn->encoder_id) : NULL;
    if (!enc) for (int i = 0; i < best_conn->count_encoders; i++) { enc = drmModeGetEncoder(d->fd, best_conn->encoders[i]); if (enc) break; }
    if (!enc) display_die("no encoder");
    uint32_t crtc_id = enc->crtc_id;
    if (!crtc_id) for (int i = 0; i < d->res->count_crtcs; i++) if (enc->possible_crtcs & (1 << i)) { crtc_id = d->res->crtcs[i]; break; }
    drmModeFreeEncoder(enc);
    if (!crtc_id) display_die("no crtc");
    d->crtc_id = crtc_id;
    d->orig_crtc = drmModeGetCrtc(d->fd, crtc_id);
    d->mode = best_mode;
    d->atomic.enabled = 0;
    if (opt->use_atomic) {
        try_init_atomic(d, debug);
        if (!d->atomic.enabled) fprintf(stderr, "Note: DRM atomic not available; using legacy KMS.\n");
        else fprintf(stderr, "Using DRM atomic modesetting (plane %u).\n", d->atomic.plane_id);
        d->atomic.nonblock = opt->atomic_nonblock ? 1 : 0;
    }
}

void display_gbm_init(gbm_ctx *g, int drm_fd, int w, int h, bool debug) {
    g->dev = gbm_create_device(drm_fd);
    if (!g->dev) display_die("gbm_create_device");
    g->surface = gbm_surface_create(g->dev, w, h, GBM_FORMAT_XRGB8888, GBM_BO_USE_SCANOUT | GBM_BO_USE_RENDERING);
    if (!g->surface) display_die("gbm_surface_create");
    g->w = w; g->h = h;
    if (debug) fprintf(stderr, "GBM: device+surface created %dx%d, format=XRGB8888\n", w, h);
}

void display_egl_init(egl_ctx *e, gbm_ctx *g, bool debug) {
    e->dpy = eglGetDisplay((EGLNativeDisplayType)g->dev);
    if (e->dpy == EGL_NO_DISPLAY) display_die("eglGetDisplay");
    if (!eglInitialize(e->dpy, NULL, NULL)) display_die("eglInitialize");
    eglBindAPI(EGL_OPENGL_ES_API);
    EGLint renderable = EGL_OPENGL_ES2_BIT;
    e->cfg = find_config_for_format(e->dpy, renderable, EGL_FALSE, GBM_FORMAT_XRGB8888);
    if (!e->cfg) e->cfg = find_config_for_format(e->dpy, renderable, EGL_TRUE, GBM_FORMAT_ARGB8888);
    if (!e->cfg) display_die("eglChooseConfig");
    static const EGLint ctx_attribs[] = {EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE};
    e->ctx = eglCreateContext(e->dpy, e->cfg, EGL_NO_CONTEXT, ctx_attribs);
    if (e->ctx == EGL_NO_CONTEXT) display_die("eglCreateContext");
    e->surf = eglCreateWindowSurface(e->dpy, e->cfg, (EGLNativeWindowType)g->surface, NULL);
    if (e->surf == EGL_NO_SURFACE) {
        EGLint err = eglGetError();
        fprintf(stderr, "eglCreateWindowSurface failed: %s. Retrying with ARGB8888...\n", egl_err_str(err));
        int w = g->w, h = g->h;
        gbm_surface_destroy(g->surface);
        g->surface = gbm_surface_create(g->dev, (uint32_t)w, (uint32_t)h, GBM_FORMAT_ARGB8888, GBM_BO_USE_SCANOUT | GBM_BO_USE_RENDERING);
        if (!g->surface) display_die("gbm_surface_create ARGB8888");
        e->cfg = find_config_for_format(e->dpy, renderable, EGL_TRUE, GBM_FORMAT_ARGB8888);
        if (!e->cfg) display_die("eglChooseConfig ARGB8888");
        e->surf = eglCreateWindowSurface(e->dpy, e->cfg, (EGLNativeWindowType)g->surface, NULL);
        if (e->surf == EGL_NO_SURFACE) {
            fprintf(stderr, "eglCreateWindowSurface still failing: %s\n", egl_err_str(eglGetError()));
            advise_dri_drivers();
            display_die("eglCreateWindowSurface");
        }
    }
    if (!eglMakeCurrent(e->dpy, e->surf, e->surf, e->ctx)) display_die("eglMakeCurrent");
    const char *renderer = (const char *)glGetString(GL_RENDERER);
    const char *vendor = (const char *)glGetString(GL_VENDOR);
    if (renderer && vendor) fprintf(stderr, "EGL/GL renderer: %s (%s)\n", renderer, vendor);
    if (!renderer || !vendor) {
        setenv("MESA_LOADER_DRIVER_OVERRIDE", "kms_swrast", 1);
        setenv("LIBGL_ALWAYS_SOFTWARE", "1", 1);
    }
    if (debug) {
        const char *egl_ver = eglQueryString(e->dpy, EGL_VERSION);
        const char *egl_vendor = eglQueryString(e->dpy, EGL_VENDOR);
        fprintf(stderr, "EGL initialized: version=%s, vendor=%s\n", egl_ver ? egl_ver : "?", egl_vendor ? egl_vendor : "?");
    }
    eglSwapInterval(e->dpy, 1);
}

void display_drm_set_mode(drm_ctx *d, gbm_ctx *g) {
    if (d->atomic.enabled) {
        g->bo = gbm_surface_lock_front_buffer(g->surface);
        if (!g->bo) display_die("gbm_surface_lock_front_buffer");
        g->fb_id = drm_fb_for_bo(d->fd, g->bo);
        drmModeAtomicReq *req = drmModeAtomicAlloc();
        if (!req) display_die("drmModeAtomicAlloc");
        uint32_t blob_id = 0;
        if (drmModeCreatePropertyBlob(d->fd, &d->mode, sizeof(d->mode), &blob_id) != 0) display_die("drmModeCreatePropertyBlob");
        int out_fence = -1, r = 0;
        r |= drmModeAtomicAddProperty(req, d->crtc_id, d->atomic.crtc_props.mode_id, blob_id) <= 0;
        r |= drmModeAtomicAddProperty(req, d->crtc_id, d->atomic.crtc_props.active, 1) <= 0;
        if (d->atomic.crtc_props.out_fence_ptr) {
            uint64_t ptr = (uint64_t)(uintptr_t)&out_fence;
            r |= drmModeAtomicAddProperty(req, d->crtc_id, d->atomic.crtc_props.out_fence_ptr, ptr) <= 0;
        }
        r |= drmModeAtomicAddProperty(req, d->conn_id, d->atomic.conn_props.crtc_id, d->crtc_id) <= 0;
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
        if (r) display_die("drmModeAtomicAddProperty");
        if (drmModeAtomicCommit(d->fd, req, DRM_MODE_ATOMIC_ALLOW_MODESET, g) != 0) display_die("drmModeAtomicCommit (modeset)");
        drmModeAtomicFree(req);
        drmModeDestroyPropertyBlob(d->fd, blob_id);
        if (out_fence >= 0) close(out_fence);
        g->in_flight = 0;
        return;
    }
    g->bo = gbm_surface_lock_front_buffer(g->surface);
    if (!g->bo) display_die("gbm_surface_lock_front_buffer");
    g->fb_id = drm_fb_for_bo(d->fd, g->bo);
    if (drmModeSetCrtc(d->fd, d->crtc_id, g->fb_id, 0, 0, &d->conn_id, 1, &d->mode) != 0) display_die("drmModeSetCrtc");
}

void display_page_flip(drm_ctx *d, gbm_ctx *g) {
    g->next_bo = gbm_surface_lock_front_buffer(g->surface);
    uint32_t fb = drm_fb_for_bo(d->fd, g->next_bo);
    if (d->atomic.enabled) {
        drmModeAtomicReq *req = drmModeAtomicAlloc();
        if (!req) display_die("drmModeAtomicAlloc");
        int r = 0, out_fence = -1;
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
        if (r) display_die("drmModeAtomicAddProperty (flip)");
        unsigned int flags = d->atomic.nonblock ? DRM_MODE_ATOMIC_NONBLOCK : 0;
        if (drmModeAtomicCommit(d->fd, req, flags, d->atomic.nonblock ? g : NULL) == 0) {
            drmModeAtomicFree(req);
            if (out_fence >= 0) close(out_fence);
            if (d->atomic.nonblock) {
                g->pending_bo = g->next_bo; g->pending_fb = fb; g->in_flight = 1; return;
            }
            if (g->bo) { uint32_t old_fb = g->fb_id; gbm_surface_release_buffer(g->surface, g->bo); drmModeRmFB(d->fd, old_fb); }
            g->bo = g->next_bo; g->fb_id = fb; g->in_flight = 0; return;
        }
        drmModeAtomicFree(req);
        fprintf(stderr, "drmModeAtomicCommit (flip) failed; falling back to legacy\n");
        d->atomic.enabled = 0;
    }
    int ret = drmModeSetCrtc(d->fd, d->crtc_id, fb, 0, 0, &d->conn_id, 1, &d->mode);
    if (ret) fprintf(stderr, "drmModeSetCrtc (page_flip) failed: %d\n", ret);
    if (g->bo) { uint32_t old_fb = g->fb_id; gbm_surface_release_buffer(g->surface, g->bo); drmModeRmFB(d->fd, old_fb); }
    g->bo = g->next_bo; g->fb_id = fb;
}

void display_on_page_flip(int fd, unsigned int sequence, unsigned int tv_sec, unsigned int tv_usec, void *user_data) {
    (void)fd; (void)sequence; (void)tv_sec; (void)tv_usec;
    gbm_ctx *g = (gbm_ctx *)user_data;
    if (!g || !g->in_flight) return;
    if (g->bo) {
        drmModeRmFB(fd, g->fb_id);
        gbm_surface_release_buffer(g->surface, g->bo);
    }
    g->bo = g->pending_bo;
    g->fb_id = g->pending_fb;
    g->pending_bo = NULL;
    g->pending_fb = 0;
    g->in_flight = 0;
}
